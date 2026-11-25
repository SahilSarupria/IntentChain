"""app/services/guard_hook.py -- glue that puts ICS and ITCG on the live /build-tx path (Option A)."""
import json, os
from app.services import tx_guard
from app.services.chain_selector import select_chain, ChainCandidate

# measured median block times (s) from the live collection; blocks-to-inclusion per tier
BLOCK_TIME_S = {"ethereum": 12.0, "sepolia": 12.0, "polygon": 1.5, "arbitrum": 0.27, "optimism": 2.0, "bsc": 0.45}
TIER_BLOCKS = {"cheapest": 3, "standard": 2, "fastest": 1}
CONTACTS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "contacts.json")   # optional: {"alice": "0x..", ...}


def _address_book(wallet: str | None):
    try:
        data = json.load(open(CONTACTS_PATH))
        return [v for v in (data.get(wallet.lower(), data) if wallet else data).values() if isinstance(v, str) and v.startswith("0x")]
    except Exception:
        return []


def guard_transaction(prompt: str, intent: dict, tx_params: dict, from_address: str | None = None) -> dict:
    """Run ITCG on the unsigned tx. Never raises: on internal error returns CONFIRM (fail-safe)."""
    from app.blockchain import erc20
    from app.config.networks import NETWORKS, normalize_network
    try:
        net = normalize_network(intent.get("network"))
        known = erc20.known_tokens_for_network(net)
        chain_ids = {k: v["chain_id"] for k, v in NETWORKS.items()}
        res = tx_guard.verify(prompt or "", intent, tx_params, known_tokens=known, chain_ids=chain_ids, address_book=_address_book(from_address))
        by_chain = {v["chain_id"]: k for k, v in NETWORKS.items()}
        by_addr = {m["address"].lower(): (s, m["decimals"]) for s, m in known.items()}
        return {"decision": res.decision, "summary": tx_guard.describe(tx_params, by_chain, by_addr),
                "findings": [{"check": f.check, "code": f.code, "severity": f.severity, "message": f.message} for f in res.findings]}
    except Exception as exc:     # fail-safe, but visible
        return {"decision": "CONFIRM", "summary": "", "findings": [{"check": "GUARD", "code": "GUARD_ERROR", "severity": "CONFIRM", "message": f"Guard could not run: {exc}"}]}


def _balance_ok(n: str, intent: dict, from_address: str, is_token: bool, amount: float) -> bool:
    from app.blockchain.rpc import get_w3
    from app.blockchain import erc20
    try:
        w3 = get_w3(n)
        if is_token:
            addr, dec = erc20.resolve_token_address(n, intent.get("token"))
            return addr is not None and float(erc20.get_token_balance(w3, addr, from_address, dec)["balance"]) >= amount
        return float(erc20.get_native_balance(w3, from_address)) >= amount
    except Exception:
        return False


def pick_network(intent: dict, from_address: str, networks=("ethereum", "arbitrum", "optimism", "polygon", "bsc")) -> dict:
    """ICS-M selection used when the user did not name a network. Returns {"network", "explanation", "excluded"}."""
    from app.blockchain.gas_oracle import resolve_tier
    from app.services.chain_selector import candidates_from_live
    tier = resolve_tier(intent.get("priority"), intent.get("gas_mode"))
    is_token = str(intent.get("action", "")).lower() in ("transfer_token", "send_token", "approve_token")
    amount = float(intent.get("amount") or 0)
    cands = candidates_from_live(intent, from_address, list(networks))
    for c in cands:                                   # replace the constant 60 s and fill the balance flag
        c.conf_time_s = TIER_BLOCKS[tier] * BLOCK_TIME_S.get(c.network, 12.0)
        c.balance_ok = c.healthy and c.asset_supported and _balance_ok(c.network, intent, from_address, is_token, amount)
    usd_amount = float(intent.get("amount_usd") or amount)      # for USD-valued assets pass amount_usd if known
    sel = select_chain(cands, tier, usd_amount, normalization="materiality")
    return {"network": sel.network, "explanation": sel.explanation, "excluded": sel.excluded}
