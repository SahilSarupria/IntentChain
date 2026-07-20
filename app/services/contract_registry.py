"""
IntentChain — Contract Registry

Lets a customer register *their own* private, already-deployed
SupplyChainTraceability contract straight from the UI — no server access,
no .env edits. This is what makes the app genuinely multi-tenant: every
connected wallet can point IntentChain at its own contract, and reads/writes
for that wallet automatically use it.

Resolution order (see supply_chain_service.get_contract_address):
  1. The wallet's *active* registered contract for that network (this file)
  2. SUPPLY_CHAIN_CONTRACT_ADDRESS_<NETWORK> in .env (operator-wide default)
  3. SUPPLY_CHAIN_CONTRACT_ADDRESS in .env (single-chain fallback)

Storage: a single JSON file on disk (no DB dependency, matches the rest of
this project's lightweight footprint). Fine for demo/small-deployment scale;
swap in a real datastore before this needs to scale past a handful of
concurrent tenants.
"""
from __future__ import annotations

import json
import os
import threading
import time

from web3 import Web3

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "contract_registry.json")
_lock = threading.Lock()


def _wallet_key(wallet_address: str) -> str:
    return Web3.to_checksum_address(wallet_address).lower()


def _load() -> dict:
    try:
        with open(_REGISTRY_PATH, "r") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
    tmp_path = _REGISTRY_PATH + ".tmp"
    with open(tmp_path, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp_path, _REGISTRY_PATH)


def register_contract(wallet_address: str, network: str, contract_address: str,
                       label: str = "", make_active: bool = True) -> dict:
    """Register (or re-register) a contract for this wallet+network. The most
    recently registered contract for a network becomes the active one unless
    `make_active=False`."""
    contract_address = Web3.to_checksum_address(contract_address)
    wallet_key = _wallet_key(wallet_address)

    with _lock:
        data = _load()
        wallet_entries = data.setdefault(wallet_key, {"contracts": [], "active": {}})

        existing = next(
            (c for c in wallet_entries["contracts"]
             if c["network"] == network and c["address"].lower() == contract_address.lower()),
            None,
        )
        if existing:
            existing["label"] = label or existing.get("label", "")
            existing["updated_at"] = time.time()
        else:
            wallet_entries["contracts"].append({
                "network": network,
                "address": contract_address,
                "label": label or "My Supply Chain Contract",
                "registered_at": time.time(),
                "updated_at": time.time(),
            })

        if make_active:
            wallet_entries["active"][network] = contract_address

        _save(data)
        return {
            "wallet": wallet_key,
            "network": network,
            "address": contract_address,
            "active": wallet_entries["active"].get(network) == contract_address,
        }


def set_active(wallet_address: str, network: str, contract_address: str) -> dict:
    contract_address = Web3.to_checksum_address(contract_address)
    wallet_key = _wallet_key(wallet_address)

    with _lock:
        data = _load()
        wallet_entries = data.get(wallet_key)
        if not wallet_entries or not any(
            c["network"] == network and c["address"].lower() == contract_address.lower()
            for c in wallet_entries["contracts"]
        ):
            raise ValueError("That contract isn't registered to this wallet yet — register it first.")
        wallet_entries["active"][network] = contract_address
        _save(data)
        return {"wallet": wallet_key, "network": network, "active": contract_address}


def remove_contract(wallet_address: str, network: str, contract_address: str) -> None:
    contract_address = Web3.to_checksum_address(contract_address)
    wallet_key = _wallet_key(wallet_address)

    with _lock:
        data = _load()
        wallet_entries = data.get(wallet_key)
        if not wallet_entries:
            return
        wallet_entries["contracts"] = [
            c for c in wallet_entries["contracts"]
            if not (c["network"] == network and c["address"].lower() == contract_address.lower())
        ]
        if wallet_entries["active"].get(network, "").lower() == contract_address.lower():
            del wallet_entries["active"][network]
        _save(data)


def list_contracts(wallet_address: str, network: str | None = None) -> list[dict]:
    data = _load()
    wallet_entries = data.get(_wallet_key(wallet_address))
    if not wallet_entries:
        return []
    active = wallet_entries.get("active", {})
    contracts = wallet_entries.get("contracts", [])
    if network:
        contracts = [c for c in contracts if c["network"] == network]
    return [{**c, "active": active.get(c["network"], "").lower() == c["address"].lower()} for c in contracts]


def get_active_contract(wallet_address: str | None, network: str) -> str | None:
    if not wallet_address:
        return None
    data = _load()
    wallet_entries = data.get(_wallet_key(wallet_address))
    if not wallet_entries:
        return None
    return wallet_entries.get("active", {}).get(network)