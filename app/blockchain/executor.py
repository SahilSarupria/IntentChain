"""
IntentChain — Legacy native-transfer executor.

Superseded by app/services/tx_builder.py, which handles native transfers,
ERC-20 transfers/approvals, and supply-chain contract calls through one
dispatch path with shared fee logic. This module is kept as a thin,
backwards-compatible shim in case anything external still imports it
directly.
"""
from app.blockchain.rpc import get_w3
from app.services.tx_builder import build_unsigned_tx as _build_unsigned_tx


def get_priority_fee(priority: str) -> int:
    w3 = get_w3("sepolia")
    fees = {"fast": 3, "low_cost": 1}
    return w3.to_wei(fees.get(priority, 2), "gwei")


def build_unsigned_tx(intent: dict, strategy: dict, from_address: str) -> dict:
    """Deprecated — use app.services.tx_builder.build_unsigned_tx instead."""
    return _build_unsigned_tx(intent, strategy, from_address)
