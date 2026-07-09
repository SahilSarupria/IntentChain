"""
IntentChain — Etherscan Transaction History Client

Uses Etherscan's unified V2 API (one API key + `chainid` param covers
Ethereum, Polygon, Arbitrum, Optimism, BSC, etc.) to pull a wallet's recent
transaction history for display in the UI.

Requires ETHERSCAN_API_KEY (free tier: https://etherscan.io/apis). Degrades
gracefully — every function returns a structured `{"error": ...}` instead of
raising, since transaction history is a "nice to have" panel, not something
that should ever break the core intent -> tx flow.
"""
import os
import requests
from web3 import Web3

from app.config.networks import get_chain_id

BASE_URL = "https://api.etherscan.io/v2/api"
TIMEOUT = 8


def _api_key() -> str | None:
    return os.getenv("ETHERSCAN_API_KEY")


def _get(params: dict) -> dict:
    api_key = _api_key()
    if not api_key:
        return {"error": "ETHERSCAN_API_KEY not configured — add it to .env to enable transaction history."}
    params = {**params, "apikey": api_key}
    try:
        resp = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return {"error": f"Etherscan request failed: {exc}"}

    # Etherscan returns status "0" both for "no transactions found" and for
    # real errors — disambiguate on the message field.
    if payload.get("status") == "0" and payload.get("message") not in ("No transactions found", "No records found"):
        return {"error": payload.get("result") or payload.get("message") or "Unknown Etherscan error"}

    return {"result": payload.get("result", [])}


def get_native_tx_history(address: str, network: str, limit: int = 25) -> dict:
    data = _get({
        "chainid": get_chain_id(network),
        "module": "account",
        "action": "txlist",
        "address": Web3.to_checksum_address(address),
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "desc",
    })
    if "error" in data:
        return data
    return {"transactions": [_format_native_tx(tx, network) for tx in data["result"][:limit]]}


def get_token_tx_history(address: str, network: str, limit: int = 25) -> dict:
    data = _get({
        "chainid": get_chain_id(network),
        "module": "account",
        "action": "tokentx",
        "address": Web3.to_checksum_address(address),
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "desc",
    })
    if "error" in data:
        return data
    return {"transfers": [_format_token_tx(tx, network) for tx in data["result"][:limit]]}


def _format_native_tx(tx: dict, network: str) -> dict:
    from app.config.networks import get_explorer_base
    value_eth = float(Web3.from_wei(int(tx.get("value", 0)), "ether"))
    return {
        "hash": tx.get("hash"),
        "from": tx.get("from"),
        "to": tx.get("to"),
        "value": value_eth,
        "timestamp": int(tx.get("timeStamp", 0)),
        "gas_used": tx.get("gasUsed"),
        "is_error": tx.get("isError") == "1",
        "explorer_url": f"{get_explorer_base(network)}/tx/{tx.get('hash')}",
    }


def _format_token_tx(tx: dict, network: str) -> dict:
    from app.config.networks import get_explorer_base
    decimals = int(tx.get("tokenDecimal", 18) or 18)
    value = float(int(tx.get("value", 0)) / (10 ** decimals))
    return {
        "hash": tx.get("hash"),
        "from": tx.get("from"),
        "to": tx.get("to"),
        "token_symbol": tx.get("tokenSymbol"),
        "value": value,
        "timestamp": int(tx.get("timeStamp", 0)),
        "explorer_url": f"{get_explorer_base(network)}/tx/{tx.get('hash')}",
    }
