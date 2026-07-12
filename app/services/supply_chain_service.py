"""
IntentChain — Supply Chain / Pharma Traceability Service

Bridges natural-language intents like "register batch #A123 of fair-trade
coffee from Huila, Colombia" or "log a checkpoint: batch A123 arrived at
Rotterdam port at 4°C" to the on-chain SupplyChainTraceability contract.

Follows the same non-custodial pattern as every other tx in this project:
this module only ever *builds* calldata. Signing happens in the user's
wallet (MetaMask), never on the server.

If no contract address is configured for a network, every write function
raises a clear `ValueError` (surfaced to the UI as "contract not deployed on
<network> yet — deploy SupplyChainTraceability.sol and set
SUPPLY_CHAIN_CONTRACT_ADDRESS_<NETWORK> in .env") and every read function
returns `{"deployed": False, ...}` instead of crashing, so the rest of the
app keeps working even before a user deploys the contract.
"""
from __future__ import annotations

import os

from web3 import Web3

from app.blockchain.contracts.supply_chain_abi import SUPPLY_CHAIN_ABI
from app.config.networks import contract_address_env_var, normalize_network

CONTRACT_ADDRESS_BASE_VAR = "SUPPLY_CHAIN_CONTRACT_ADDRESS"


def get_contract_address(network: str) -> str | None:
    # Per-network var first (SUPPLY_CHAIN_CONTRACT_ADDRESS_SEPOLIA), then a
    # single generic fallback (SUPPLY_CHAIN_CONTRACT_ADDRESS) for single-chain setups.
    addr = os.getenv(contract_address_env_var(CONTRACT_ADDRESS_BASE_VAR, network))
    if not addr:
        addr = os.getenv(CONTRACT_ADDRESS_BASE_VAR)
    return addr


def get_contract(w3: Web3, network: str):
    address = get_contract_address(network)
    if not address:
        return None
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=SUPPLY_CHAIN_ABI)


def product_id_from_string(sku_or_batch: str) -> bytes:
    """Deterministically derive a bytes32 product ID from a human batch/SKU
    string, e.g. "COFFEE-BATCH-A123" -> keccak256(...)."""
    return Web3.keccak(text=sku_or_batch)


def _require_contract(w3: Web3, network: str):
    contract = get_contract(w3, network)
    if contract is None:
        raise ValueError(
            f"SupplyChainTraceability is not deployed on '{network}' yet. "
            f"Deploy app/blockchain/contracts/SupplyChainTraceability.sol and set "
            f"{contract_address_env_var(CONTRACT_ADDRESS_BASE_VAR, network)} in .env."
        )
    return contract


def build_register_product_tx(w3: Web3, network: str, from_address: str,
                                batch_id: str, name: str, origin: str) -> dict:
    contract = _require_contract(w3, network)
    product_id = product_id_from_string(batch_id)
    data = contract.encodeABI(fn_name="registerProduct", args=[product_id, name, origin])
    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    contract.address,
        "value": hex(0),
        "data":  data,
        "meta":  {"product_id_hex": product_id.hex(), "batch_id": batch_id},
    }


def build_log_checkpoint_tx(w3: Web3, network: str, from_address: str, batch_id: str,
                              location: str, status: str, temperature_c: int = 0) -> dict:
    contract = _require_contract(w3, network)
    product_id = product_id_from_string(batch_id)
    data = contract.encodeABI(fn_name="logCheckpoint",
                               args=[product_id, location, status, int(temperature_c)])
    return {
        "from":  Web3.to_checksum_address(from_address),
        "to":    contract.address,
        "value": hex(0),
        "data":  data,
        "meta":  {"product_id_hex": product_id.hex(), "batch_id": batch_id},
    }


def read_product(w3: Web3, network: str, batch_id: str) -> dict:
    contract = get_contract(w3, network)
    if contract is None:
        return {"deployed": False, "network": network,
                "message": "SupplyChainTraceability is not deployed on this network yet."}

    product_id = product_id_from_string(batch_id)
    try:
        name, origin, manufacturer, registered_at, exists = contract.functions.getProduct(product_id).call()
    except Exception as exc:
        return {"deployed": True, "found": False, "error": str(exc)}

    if not exists:
        return {"deployed": True, "found": False, "batch_id": batch_id}

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
        "product_id_hex": product_id.hex(),
        "name": name, "origin": origin, "manufacturer": manufacturer,
        "registered_at": registered_at, "checkpoints": checkpoints,
    }


def verify_authenticity(w3: Web3, network: str, batch_id: str) -> dict:
    contract = get_contract(w3, network)
    if contract is None:
        return {"deployed": False, "network": network,
                "message": "SupplyChainTraceability is not deployed on this network yet."}

    product_id = product_id_from_string(batch_id)
    try:
        exists, manufacturer, checkpoint_count = contract.functions.verifyAuthenticity(product_id).call()
    except Exception as exc:
        return {"deployed": True, "error": str(exc)}

    return {
        "deployed": True, "authentic": bool(exists), "manufacturer": manufacturer,
        "checkpoint_count": checkpoint_count, "batch_id": batch_id,
    }
