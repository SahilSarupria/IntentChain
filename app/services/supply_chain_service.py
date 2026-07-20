"""
IntentChain — Supply Chain / Pharma Traceability Service

Bridges natural-language intents like "register batch #A123 of fair-trade
coffee from Huila, Colombia" or "log a checkpoint: batch A123 arrived at
Rotterdam port at 4°C" to an on-chain SupplyChainTraceability contract.

Multi-tenant by design: any connected wallet can register its own private,
already-deployed contract straight from the UI (see contract_registry.py) —
no .env access needed. Resolution order for "which contract do we use":

  1. The wallet's active registered contract for this network
     (contract_registry.py — set via the UI)
  2. SUPPLY_CHAIN_CONTRACT_ADDRESS_<NETWORK> in .env (operator-wide default)
  3. SUPPLY_CHAIN_CONTRACT_ADDRESS in .env (single-chain fallback)

Follows the same non-custodial pattern as every other tx in this project:
this module only ever *builds* calldata. Signing happens in the user's
wallet (MetaMask), never on the server.
"""
from __future__ import annotations

import os

from web3 import Web3

from app.blockchain.contracts.supply_chain_abi import SUPPLY_CHAIN_ABI
from app.blockchain.web3_compat import encode_fn
from app.config.networks import contract_address_env_var, normalize_network
from app.services import contract_registry

CONTRACT_ADDRESS_BASE_VAR = "SUPPLY_CHAIN_CONTRACT_ADDRESS"


def get_contract_address(network: str, wallet_address: str | None = None) -> str | None:
    # 1) wallet's own registered contract (UI-driven, per-tenant)
    if wallet_address:
        addr = contract_registry.get_active_contract(wallet_address, network)
        if addr:
            return addr

    # 2) per-network operator default, 3) generic operator fallback
    addr = os.getenv(contract_address_env_var(CONTRACT_ADDRESS_BASE_VAR, network))
    if not addr:
        addr = os.getenv(CONTRACT_ADDRESS_BASE_VAR)
    return addr


def get_contract(w3: Web3, network: str, wallet_address: str | None = None):
    address = get_contract_address(network, wallet_address)
    if not address:
        return None
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=SUPPLY_CHAIN_ABI)


def verify_contract_deployed(w3: Web3, address: str) -> bool:
    """Sanity check that *something* is actually deployed at this address
    before we let a customer register it — catches typos and EOA addresses.
    Doesn't guarantee it's a SupplyChainTraceability contract specifically
    (that would require bytecode/ABI introspection), just that it's a
    contract at all."""
    try:
        code = w3.eth.get_code(Web3.to_checksum_address(address))
        return code not in (b"", b"0x", None) and len(code) > 0
    except Exception:
        return False


def product_id_from_string(sku_or_batch: str) -> bytes:
    """Deterministically derive a bytes32 product ID from a human batch/SKU
    string, e.g. "COFFEE-BATCH-A123" -> keccak256(...)."""
    return Web3.keccak(text=sku_or_batch)


def _require_contract(w3: Web3, network: str, wallet_address: str | None = None):
    contract = get_contract(w3, network, wallet_address)
    if contract is None:
        raise ValueError(
            f"No supply-chain contract configured for '{network}'. Register your own contract "
            f"from the Supply Chain panel in the UI (Settings → My Private Contract), or ask the "
            f"operator to set {contract_address_env_var(CONTRACT_ADDRESS_BASE_VAR, network)} in .env."
        )
    return contract


def build_register_product_tx(w3: Web3, network: str, from_address: str,
                                batch_id: str, name: str, origin: str,
                                contract_address: str | None = None) -> dict:
    contract = (w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=SUPPLY_CHAIN_ABI)
                if contract_address else _require_contract(w3, network, from_address))
    product_id = product_id_from_string(batch_id)
    data = encode_fn(contract, "registerProduct", [product_id, name, origin])
    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    contract.address,
        "value": hex(0),
        "data":  data,
        "meta":  {"product_id_hex": product_id.hex(), "batch_id": batch_id, "contract": contract.address},
    }


def build_log_checkpoint_tx(w3: Web3, network: str, from_address: str, batch_id: str,
                              location: str, status: str, temperature_c: int = 0,
                              contract_address: str | None = None) -> dict:
    contract = (w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=SUPPLY_CHAIN_ABI)
                if contract_address else _require_contract(w3, network, from_address))
    product_id = product_id_from_string(batch_id)
    data = encode_fn(contract, "logCheckpoint", [product_id, location, status, int(temperature_c)])
    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    contract.address,
        "value": hex(0),
        "data":  data,
        "meta":  {"product_id_hex": product_id.hex(), "batch_id": batch_id, "contract": contract.address},
    }


def read_product(w3: Web3, network: str, batch_id: str,
                  wallet_address: str | None = None, contract_address: str | None = None) -> dict:
    contract = (w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=SUPPLY_CHAIN_ABI)
                if contract_address else get_contract(w3, network, wallet_address))
    if contract is None:
        return {"deployed": False, "network": network,
                "message": "No supply-chain contract configured for this network/wallet yet."}

    product_id = product_id_from_string(batch_id)
    try:
        name, origin, manufacturer, registered_at, exists = contract.functions.getProduct(product_id).call()
    except Exception as exc:
        return {"deployed": True, "found": False, "error": str(exc), "contract": contract.address}

    if not exists:
        return {"deployed": True, "found": False, "batch_id": batch_id, "contract": contract.address}

    try:
        count = contract.functions.getCheckpointCount(product_id).call()
        checkpoints = []
        for i in range(count):
            location, status, temp_c, recorded_by, ts = contract.functions.getCheckpoint(product_id, i).call()
            checkpoints.append({
                "location": location, "status": status, "temperature_c": temp_c,
                "recorded_by": recorded_by, "timestamp": ts,
            })
    except Exception:
        checkpoints = []

    return {
        "deployed": True, "found": True, "batch_id": batch_id,
        "product_id_hex": product_id.hex(), "contract": contract.address,
        "name": name, "origin": origin, "manufacturer": manufacturer,
        "registered_at": registered_at, "checkpoints": checkpoints,
    }


def verify_authenticity(w3: Web3, network: str, batch_id: str,
                         wallet_address: str | None = None, contract_address: str | None = None) -> dict:
    contract = (w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=SUPPLY_CHAIN_ABI)
                if contract_address else get_contract(w3, network, wallet_address))
    if contract is None:
        return {"deployed": False, "network": network,
                "message": "No supply-chain contract configured for this network/wallet yet."}

    product_id = product_id_from_string(batch_id)
    try:
        exists, manufacturer, checkpoint_count = contract.functions.verifyAuthenticity(product_id).call()
    except Exception as exc:
        return {"deployed": True, "error": str(exc), "contract": contract.address}

    return {
        "deployed": True, "authentic": bool(exists), "manufacturer": manufacturer,
        "checkpoint_count": checkpoint_count, "batch_id": batch_id, "contract": contract.address,
    }