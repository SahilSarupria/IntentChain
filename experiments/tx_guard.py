"""
IntentChain -- Intent-Transaction Consistency Guard (ITCG)

An independent, deterministic verifier that runs between tx_builder and the
wallet. It never trusts the LLM's output: every security-relevant field is
re-derived from (a) the user's literal prompt and (b) the bytes of the built
unsigned transaction, and the three are cross-checked.

   prompt  --(grounding)-->  intent  --(round-trip decode)-->  unsigned tx

Checks (codes in Finding.code):
  G1 RECIPIENT_UNGROUNDED     recipient/spender hex string is not in the prompt
  G2 AMOUNT_UNGROUNDED        intent amount is not a number written in the prompt
     AMOUNT_UNPARSEABLE       prompt has number words we cannot parse (soft)
  G3 NETWORK_UNGROUNDED       prompt names a network, intent chose another
  G4 TOKEN_MISMATCH           prompt names token X, intent/tx uses token Y
  G5 ACTION_MISMATCH          prompt verb class (send vs approve) != intent action
  G6 TX_INTENT_MISMATCH       decoded tx (to / selector / args / value) != intent
     CHAIN_MISMATCH           tx.chainId != chain of intent.network
  G7 UNLIMITED_APPROVAL       approve amount >= 2**255
  G8 ADDR_LOOKALIKE           recipient mimics (first4/last4) a known counterparty
     MULTI_ADDRESS_PROMPT     prompt contains extra addresses beyond the target
     TOKEN_NOT_IN_REGISTRY    raw token address not in tokens.json (soft)

Decision: any BLOCK-severity finding -> BLOCK; else any CONFIRM -> CONFIRM
(step-up confirmation in the UI); else ALLOW. Pure stdlib.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kKmM])?(?![\w])")
NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred|thousand|half|quarter|tenth|couple|dozen)\b", re.I)

SEL_TRANSFER, SEL_APPROVE = "0xa9059cbb", "0x095ea8ba"
MAX_UINT_HALF = 2 ** 255

NETWORK_WORDS = {
    "ethereum": ["ethereum mainnet", "ethereum", "mainnet"],
    "sepolia": ["sepolia"],
    "polygon": ["polygon", "matic network", "polygon pos"],
    "arbitrum": ["arbitrum"],
    "optimism": ["optimism", "op mainnet"],
    "bsc": ["bsc", "bnb chain", "bnb smart chain", "binance smart chain"],
}
TRANSFER_VERBS = re.compile(r"\b(send|transfer|pay|give|move|remit)\b", re.I)
APPROVE_VERBS = re.compile(r"\b(approve|allow|authori[sz]e|permit|allowance)\b", re.I)
UNLIMITED_WORDS = re.compile(r"\b(unlimited|infinite|max(imum)?|no limit)\b", re.I)

BLOCK, CONFIRM, ALLOW = "BLOCK", "CONFIRM", "ALLOW"
ALL_CHECKS = {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"}


@dataclass
class Finding:
    check: str
    code: str
    severity: str
    message: str


@dataclass
class GuardResult:
    decision: str
    findings: list = field(default_factory=list)
    summary: str = ""


def _low(a) -> str:
    return str(a or "").lower()


def prompt_numbers(prompt: str) -> set[Decimal]:
    text = ADDR_RE.sub(" ", prompt)
    vals = set()
    for m in NUM_RE.finditer(text):
        whole = m.group(1).replace(",", "")
        try:
            v = Decimal(whole + (m.group(2) or ""))
        except InvalidOperation:
            continue
        suf = (m.group(3) or "").lower()
        if suf == "k":
            v *= 1000
        elif suf == "m":
            v *= 1_000_000
        vals.add(v)
    return vals


def prompt_networks(prompt: str) -> set[str]:
    p = prompt.lower()
    found = set()
    for net, words in NETWORK_WORDS.items():
        for w in words:
            if re.search(r"\b" + re.escape(w) + r"\b", p):
                found.add(net)
                break
    # "ethereum" appearing alongside a more specific L2 name is not a claim
    return found


def prompt_symbols(prompt: str, symbols: set[str]) -> set[str]:
    p = prompt.upper()
    return {s for s in symbols if re.search(r"\b" + re.escape(s.upper()) + r"\b", p)}


def decode_tx(tx: dict) -> dict:
    """Decode an unsigned tx the way a wallet would: native vs ERC-20 call."""
    data = _low(tx.get("data") or "0x")
    to = _low(tx.get("to"))
    value = int(str(tx.get("value") or "0x0"), 16) if str(tx.get("value", "0")).startswith("0x") else int(tx.get("value") or 0)
    chain_id = tx.get("chainId")
    if isinstance(chain_id, str):
        chain_id = int(chain_id, 16) if chain_id.startswith("0x") else int(chain_id)
    if data in ("0x", ""):
        return {"kind": "native", "to": to, "value": value, "chain_id": chain_id}
    sel = data[:10]
    words = [data[10 + 64 * i: 10 + 64 * (i + 1)] for i in range((len(data) - 10) // 64)]
    if sel in (SEL_TRANSFER, SEL_APPROVE) and len(words) == 2:
        return {"kind": "transfer" if sel == SEL_TRANSFER else "approve", "contract": to,
                "target": "0x" + words[0][-40:], "raw_amount": int(words[1], 16),
                "value": value, "chain_id": chain_id}
    return {"kind": "unknown", "to": to, "selector": sel, "value": value, "chain_id": chain_id}


def describe(tx: dict, networks_by_chain: dict, tokens_by_addr: dict) -> str:
    """Plain-language 'what you are actually signing', derived from tx bytes only."""
    d = decode_tx(tx)
    net = networks_by_chain.get(d.get("chain_id"), f"chainId {d.get('chain_id')}")
    if d["kind"] == "native":
        return f"Send {Decimal(d['value']) / Decimal(10**18):f} native coin to {d['to']} on {net}"
    if d["kind"] in ("transfer", "approve"):
        sym, dec = tokens_by_addr.get(d["contract"], ("UNKNOWN TOKEN " + d["contract"], 18))
        amt = Decimal(d["raw_amount"]) / (Decimal(10) ** dec)
        verb = "Send" if d["kind"] == "transfer" else "Allow spending of"
        tail = "to" if d["kind"] == "transfer" else "by spender"
        big = " (UNLIMITED)" if d["raw_amount"] >= MAX_UINT_HALF else ""
        return f"{verb} {amt:f}{big} {sym} {tail} {d['target']} on {net}"
    return f"Unrecognised contract call to {d.get('to')} (selector {d.get('selector')}) on {net}"


def verify(prompt: str, intent: dict, tx: dict, *, known_tokens: dict, chain_ids: dict,
           address_book: list[str] | None = None, checks: set[str] | None = None) -> GuardResult:
    """known_tokens: {SYMBOL: {"address":..., "decimals":...}} for intent.network
    chain_ids: {network: int}. checks: subset of ALL_CHECKS (for ablations)."""
    on = checks if checks is not None else ALL_CHECKS
    F: list[Finding] = []
    action = _low(intent.get("action"))
    is_approve = action == "approve_token"
    is_token = action in ("transfer_token", "send_token", "approve_token")
    network = _low(intent.get("network"))
    target = _low(intent.get("spender") if is_approve else intent.get("recipient"))
    amount = Decimal(str(intent.get("amount") or 0))
    addrs_in_prompt = {a.lower() for a in ADDR_RE.findall(prompt)}
    d = decode_tx(tx)

    # G1: the target address must be literally present in what the user typed
    if "G1" in on and target not in addrs_in_prompt:
        F.append(Finding("G1", "RECIPIENT_UNGROUNDED", BLOCK,
                         "Recipient/spender in the transaction does not appear in your message."))

    # G2: amount must be a number the user wrote
    if "G2" in on:
        nums = prompt_numbers(prompt)
        if amount not in nums:
            if NUMBER_WORDS.search(prompt) and not nums:
                F.append(Finding("G2", "AMOUNT_UNPARSEABLE", CONFIRM,
                                 "Amount written in words; please confirm the exact number."))
            elif is_approve and UNLIMITED_WORDS.search(prompt):
                pass
            else:
                F.append(Finding("G2", "AMOUNT_UNGROUNDED", BLOCK,
                                 f"Amount {amount} does not appear in your message."))

    # G3: network named in prompt must match intent
    if "G3" in on:
        named = prompt_networks(prompt)
        if named and network not in named:
            F.append(Finding("G3", "NETWORK_UNGROUNDED", BLOCK,
                             f"Message names {sorted(named)} but transaction targets {network}."))

    # G4: token named in prompt must match the token actually used
    token_sym = None
    token_addr = None
    if is_token:
        ref = str(intent.get("token") or "")
        if ADDR_RE.fullmatch(ref):
            token_addr = ref.lower()
            token_sym = next((s for s, m in known_tokens.items() if _low(m["address"]) == token_addr), None)
            if token_sym is None and "G8" in on:
                F.append(Finding("G8", "TOKEN_NOT_IN_REGISTRY", CONFIRM,
                                 "Token contract is not in the known-token list."))
            if "G4" in on and token_addr not in addrs_in_prompt:
                F.append(Finding("G4", "TOKEN_MISMATCH", BLOCK, "Token address not present in your message."))
        else:
            token_sym = ref.upper()
            meta = known_tokens.get(token_sym)
            token_addr = _low(meta["address"]) if meta else None
        if "G4" in on and token_sym:
            all_syms = set(known_tokens)  # symbols on this chain
            mentioned = prompt_symbols(prompt, all_syms | {token_sym})
            if mentioned and token_sym not in mentioned:
                F.append(Finding("G4", "TOKEN_MISMATCH", BLOCK,
                                 f"Message mentions {sorted(mentioned)} but transaction uses {token_sym}."))
    else:
        # native transfer: if the prompt names a registry token, native is wrong
        if "G4" in on:
            syms = prompt_symbols(prompt, set(known_tokens))
            if syms:
                F.append(Finding("G4", "TOKEN_MISMATCH", BLOCK,
                                 f"Message mentions {sorted(syms)} but a native-coin transfer was built."))

    # G5: verb class must agree with action
    if "G5" in on:
        says_approve, says_transfer = bool(APPROVE_VERBS.search(prompt)), bool(TRANSFER_VERBS.search(prompt))
        if is_approve and not says_approve:
            F.append(Finding("G5", "ACTION_MISMATCH", BLOCK, "Approval built but your message did not ask for one."))
        if (not is_approve) and says_approve and not says_transfer:
            F.append(Finding("G5", "ACTION_MISMATCH", BLOCK, "Message asked for an approval; a transfer was built."))
        if (not is_approve) and not says_transfer:
            F.append(Finding("G5", "ACTION_MISMATCH", CONFIRM, "No clear send/transfer verb in your message."))

    # G6: round-trip decode of the actual bytes vs intent
    if "G6" in on:
        exp_chain = chain_ids.get(network)
        if d.get("chain_id") != exp_chain:
            F.append(Finding("G6", "CHAIN_MISMATCH", BLOCK, "Transaction chainId differs from the selected network."))
        ok = True
        if not is_token:
            ok = (d["kind"] == "native" and d["to"] == target
                  and d["value"] == int(amount * Decimal(10) ** 18))
        else:
            meta = known_tokens.get(token_sym) if token_sym else None
            dec = meta["decimals"] if meta else None
            ok = (d["kind"] == ("approve" if is_approve else "transfer")
                  and d["target"] == target and d["value"] == 0
                  and (token_addr is None or d["contract"] == token_addr))
            if ok and dec is not None:
                ok = d["raw_amount"] == int(amount * (Decimal(10) ** dec))
            elif ok and dec is None:
                F.append(Finding("G6", "DECIMALS_UNKNOWN", CONFIRM, "Token decimals unknown; amount unverified."))
        if not ok:
            F.append(Finding("G6", "TX_INTENT_MISMATCH", BLOCK,
                             "Built transaction bytes do not match the parsed intent."))

    # G7: unlimited approvals
    if "G7" in on and d["kind"] == "approve" and d["raw_amount"] >= MAX_UINT_HALF:
        if UNLIMITED_WORDS.search(prompt):
            F.append(Finding("G7", "UNLIMITED_APPROVAL", CONFIRM, "Unlimited token approval requested; confirm."))
        else:
            F.append(Finding("G7", "UNLIMITED_APPROVAL", BLOCK, "Unlimited approval you did not ask for."))

    # G8: address poisoning / injected extra addresses
    if "G8" in on:
        for known in (address_book or []):
            k = known.lower()
            if k != target and k[2:6] == target[2:6] and k[-4:] == target[-4:]:
                F.append(Finding("G8", "ADDR_LOOKALIKE", CONFIRM,
                                 f"Recipient resembles known contact {known[:8]}…{known[-4:]} but is a different address."))
                break
        extras = addrs_in_prompt - {target} - ({token_addr} if token_addr else set())
        if extras:
            F.append(Finding("G8", "MULTI_ADDRESS_PROMPT", CONFIRM,
                             "Message contains more than one address; confirm which is the recipient."))

    decision = BLOCK if any(f.severity == BLOCK for f in F) else CONFIRM if F else ALLOW
    return GuardResult(decision, F)
