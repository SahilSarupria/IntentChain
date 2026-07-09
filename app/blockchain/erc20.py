"""
IntentChain — ERC-20 Token Support

Covers the "token balance display" and "transfer/approve" use cases:
- Read ETH/native + ERC-20 balances for a wallet (before/after a tx).
- Build unsigned `transfer` / `approve` calldata for MetaMask to sign,
  following the same "never touch the private key" pattern as native
  transfers (app/blockchain/executor.py).
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from functools import lru_cache

from web3 import Web3

from app.config.networks import normalize_network

_TOKENS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "tokens.json")

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]


@lru_cache(maxsize=1)
def _load_token_registry() -> dict:
    try:
        with open(_TOKENS_PATH, "r") as fh:
            return json.load(fh)
    except Exception:
        return {}


def known_tokens_for_network(network: str) -> dict:
    registry = _load_token_registry()
    return {k: v for k, v in registry.get(normalize_network(network), {}).items()}


def resolve_token_address(network: str, symbol_or_address: str) -> tuple[str | None, int | None]:
    """Accepts either a known symbol (USDC) or a raw contract address."""
    if not symbol_or_address:
        return None, None
    if Web3.is_address(symbol_or_address):
        return Web3.to_checksum_address(symbol_or_address), None
    token = known_tokens_for_network(network).get(symbol_or_address.upper())
    if token:
        return Web3.to_checksum_address(token["address"]), token.get("decimals")
    return None, None


def get_native_balance(w3: Web3, address: str) -> Decimal:
    wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return Decimal(w3.from_wei(wei, "ether"))


def get_token_balance(w3: Web3, token_address: str, holder: str, decimals: int | None = None) -> dict:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    raw = contract.functions.balanceOf(Web3.to_checksum_address(holder)).call()
    if decimals is None:
        try:
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18
    try:
        symbol = contract.functions.symbol().call()
    except Exception:
        symbol = "?"
    human = Decimal(raw) / (Decimal(10) ** decimals)
    return {"symbol": symbol, "address": token_address, "decimals": decimals,
            "balance": float(human), "balance_raw": str(raw)}


def list_wallet_balances(w3: Web3, network: str, address: str) -> dict:
    """ETH/native balance + every known ERC-20 for this network, best-effort.
    A single failing token lookup (bad RPC, unusual contract) never sinks the
    whole response — it's just marked with an `error`."""
    from app.config.networks import get_network_config

    cfg = get_network_config(network)
    balances = []

    try:
        native_balance = float(get_native_balance(w3, address))
    except Exception as exc:
        native_balance = None
        native_error = str(exc)
    else:
        native_error = None

    balances.append({
        "symbol": cfg["native"], "address": None, "decimals": 18,
        "balance": native_balance, "native": True, "error": native_error,
    })

    for symbol, meta in known_tokens_for_network(network).items():
        try:
            info = get_token_balance(w3, meta["address"], address, meta.get("decimals"))
            info["native"] = False
            info["error"] = None
            balances.append(info)
        except Exception as exc:
            balances.append({
                "symbol": symbol, "address": meta["address"], "decimals": meta.get("decimals"),
                "balance": None, "native": False, "error": str(exc),
            })

    return {"network": network, "address": Web3.to_checksum_address(address), "balances": balances}


def build_erc20_transfer_tx(w3: Web3, token_address: str, from_address: str, to_address: str,
                             amount_human: float, decimals: int | None = None) -> dict:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    if decimals is None:
        try:
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18

    raw_amount = int(Decimal(str(amount_human)) * (Decimal(10) ** decimals))
    data = contract.encodeABI(fn_name="transfer",
                               args=[Web3.to_checksum_address(to_address), raw_amount])

    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    Web3.to_checksum_address(token_address),
        "value": hex(0),
        "data":  data,
    }


def build_erc20_approve_tx(w3: Web3, token_address: str, from_address: str, spender_address: str,
                            amount_human: float, decimals: int | None = None) -> dict:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    if decimals is None:
        try:
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18

    raw_amount = int(Decimal(str(amount_human)) * (Decimal(10) ** decimals))
    data = contract.encodeABI(fn_name="approve",
                               args=[Web3.to_checksum_address(spender_address), raw_amount])

    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    Web3.to_checksum_address(token_address),
        "value": hex(0),
        "data":  data,
    }
