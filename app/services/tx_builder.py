"""
IntentChain — Unified Transaction Builder

Single dispatch point that turns (intent, strategy, from_address) into an
unsigned tx ready for `eth_sendTransaction` in MetaMask. Every branch reuses
the same fee_fields computed once by the strategy engine, so "fastest" vs
"cheapest" behaves identically whether you're sending ETH, an ERC-20, or
calling the supply-chain contract.

Never signs anything — the private key never enters this process. This is
the same non-custodial design the original executor.py established; this
module just extends it to cover more than plain ETH transfers.

Two internal-only keys get attached to the built tx and popped off by
intent_engine.build_tx_for_wallet before the dict is sent to the caller as
`tx_params` (i.e. they never reach `eth_sendTransaction`, only the frontend's
own display logic):
  - "_meta": extra context (e.g. product_id_hex, resolved contract address)
  - "_simulation_warning": result of a pre-flight `eth_call` dry run, so a
    tx that would revert (wrong network, wrong contract, insufficient
    funds, bad args...) gets flagged *before* the user opens their wallet,
    not just after MetaMask's own (optional, wallet-dependent) simulation.
"""
from __future__ import annotations

from web3 import Web3

from app.blockchain.rpc import get_w3
from app.blockchain.gas_oracle import estimate_gas
from app.blockchain import erc20
from app.services import supply_chain_service
from app.config.networks import normalize_network, get_chain_id

SUPPORTED_ACTIONS = {
    "transfer", "send",            # native coin transfer
    "bridge",                      # treated as a native transfer to a bridge contract/address for now
    "transfer_token", "send_token",# ERC-20 transfer
    "approve_token",               # ERC-20 approve
    "register_product",            # supply-chain: register a new batch/SKU
    "log_checkpoint",              # supply-chain: log a custody/condition event
}

NOT_YET_IMPLEMENTED = {
    "swap": "Token swaps require routing through a DEX aggregator (0x/1inch) which isn't wired up yet. "
            "Roadmap item — for now, IntentChain supports native transfers, ERC-20 transfers/approvals, "
            "and supply-chain contract calls.",
}

DEFAULT_GAS = {
    "native": 21_000,
    "erc20_transfer": 65_000,
    "erc20_approve": 55_000,
    "contract_write": 150_000,
}


def _apply_fee_and_chain(tx: dict, strategy: dict, network: str) -> dict:
    tx = {**tx, **strategy["fee_fields"]}
    # Including chainId (in addition to relying on MetaMask's active network)
    # protects the user from accidentally signing on the wrong chain if
    # their wallet is pointed somewhere unexpected — MetaMask will surface a
    # mismatch warning instead of silently broadcasting. The frontend also
    # explicitly requests a chain switch before sending (see
    # ensureCorrectChain in frontend/index.html) since not every wallet
    # enforces this field on its own.
    tx["chainId"] = hex(get_chain_id(network))
    return tx


def _simulate(w3: Web3, tx: dict) -> str | None:
    """Best-effort pre-flight check via `eth_call` so we can warn *before*
    the user opens their wallet, instead of only finding out after they've
    signed. Never raises — a failed simulation attempt itself (RPC hiccup,
    node that doesn't support the call, etc.) just means we skip the
    warning, not block the flow."""
    call_tx = {k: v for k, v in tx.items()
               if k in ("from", "to", "data", "value", "gas",
                        "maxFeePerGas", "maxPriorityFeePerGas", "gasPrice")}
    try:
        w3.eth.call(call_tx)
        return None
    except Exception as exc:
        msg = str(exc)
        return msg[:300]


def build_unsigned_tx(intent: dict, strategy: dict, from_address: str) -> dict:
    network = normalize_network(intent.get("network") or strategy.get("network"))
    w3 = get_w3(network)
    action = str(intent.get("action") or "transfer").lower().strip()

    if action in NOT_YET_IMPLEMENTED:
        raise ValueError(NOT_YET_IMPLEMENTED[action])

    if action not in SUPPORTED_ACTIONS:
        raise ValueError(
            f"Unsupported action '{action}'. Supported actions: {', '.join(sorted(SUPPORTED_ACTIONS))}."
        )

    meta = None

    if action in ("transfer", "send", "bridge"):
        tx = _build_native_transfer(w3, intent, from_address)
        tx["gas"] = hex(estimate_gas(w3, {k: v for k, v in tx.items() if k in ("from", "to", "value")},
                                      strategy.get("gas_estimate") or DEFAULT_GAS["native"]))

    elif action in ("transfer_token", "send_token"):
        tx = _build_token_transfer(w3, network, intent, from_address)
        tx["gas"] = hex(estimate_gas(w3, tx, DEFAULT_GAS["erc20_transfer"]))

    elif action == "approve_token":
        tx = _build_token_approve(w3, network, intent, from_address)
        tx["gas"] = hex(estimate_gas(w3, tx, DEFAULT_GAS["erc20_approve"]))

    elif action == "register_product":
        tx = supply_chain_service.build_register_product_tx(
            w3, network, from_address,
            batch_id=str(intent.get("product_id") or intent.get("batch_id") or intent.get("recipient") or ""),
            name=str(intent.get("name") or intent.get("token") or "Unnamed product"),
            origin=str(intent.get("origin") or "Unspecified"),
            contract_address=intent.get("contract_address") or None,
        )
        meta = tx.pop("meta", None)
        tx["gas"] = hex(estimate_gas(w3, tx, DEFAULT_GAS["contract_write"]))

    elif action == "log_checkpoint":
        tx = supply_chain_service.build_log_checkpoint_tx(
            w3, network, from_address,
            batch_id=str(intent.get("product_id") or intent.get("batch_id") or intent.get("recipient") or ""),
            location=str(intent.get("location") or "Unspecified"),
            status=str(intent.get("status") or "In Transit"),
            temperature_c=int(float(intent.get("temperature_c") or 0)),
            contract_address=intent.get("contract_address") or None,
        )
        meta = tx.pop("meta", None)
        tx["gas"] = hex(estimate_gas(w3, tx, DEFAULT_GAS["contract_write"]))

    else:  # pragma: no cover — guarded by SUPPORTED_ACTIONS check above
        raise ValueError(f"Unhandled action '{action}'")

    tx = _apply_fee_and_chain(tx, strategy, network)
    tx["_meta"] = meta
    tx["_simulation_warning"] = _simulate(w3, tx)
    return tx


def _build_native_transfer(w3: Web3, intent: dict, from_address: str) -> dict:
    recipient = intent.get("recipient")
    if not recipient or not Web3.is_address(str(recipient)):
        raise ValueError("A valid recipient address is required for a native transfer.")
    value_wei = w3.to_wei(float(intent.get("amount") or 0), "ether")
    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    Web3.to_checksum_address(recipient),
        "value": hex(value_wei),
    }


def _build_token_transfer(w3: Web3, network: str, intent: dict, from_address: str) -> dict:
    recipient = intent.get("recipient")
    if not recipient or not Web3.is_address(str(recipient)):
        raise ValueError("A valid recipient address is required for a token transfer.")

    token_ref = intent.get("token_address") or intent.get("token")
    address, decimals = erc20.resolve_token_address(network, token_ref)
    if not address:
        raise ValueError(
            f"Unknown token '{token_ref}' on network '{network}'. Pass a contract address, "
            f"or use a symbol from the known token list for this network."
        )
    return erc20.build_erc20_transfer_tx(w3, address, from_address, recipient,
                                          float(intent.get("amount") or 0), decimals)


def _build_token_approve(w3: Web3, network: str, intent: dict, from_address: str) -> dict:
    spender = intent.get("spender") or intent.get("recipient")
    if not spender or not Web3.is_address(str(spender)):
        raise ValueError("A valid spender address is required for a token approval.")

    token_ref = intent.get("token_address") or intent.get("token")
    address, decimals = erc20.resolve_token_address(network, token_ref)
    if not address:
        raise ValueError(f"Unknown token '{token_ref}' on network '{network}'.")
    return erc20.build_erc20_approve_tx(w3, address, from_address, spender,
                                         float(intent.get("amount") or 0), decimals)