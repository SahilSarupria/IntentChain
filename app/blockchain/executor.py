"""
IntentChain — Legacy native-transfer executor.

Superseded by app/services/tx_builder.py, which handles native transfers,
ERC-20 transfers/approvals, and supply-chain contract calls through one
dispatch path with shared fee logic. This module is kept as a thin,
backwards-compatible shim in case anything external still imports it
directly.
"""
from app.services.tx_builder import build_unsigned_tx as _build_unsigned_tx


def build_unsigned_tx(intent: dict, strategy: dict, from_address: str) -> dict:
    """Backwards-compatible wrapper around app.services.tx_builder."""
    return _build_unsigned_tx(intent, strategy, from_address)
