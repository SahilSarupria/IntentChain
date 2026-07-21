from app.services.strategy_engine import evaluate_strategy
from app.services.tx_builder import build_unsigned_tx


def build_tx_for_wallet(intent: dict, from_address: str) -> dict:
    """
    Build an unsigned tx ready for MetaMask, for any supported intent action
    (native transfer, ERC-20 transfer/approve, or supply-chain contract call).
    Returns tx_params + strategy metadata (including the full fastest /
    standard / cheapest gas-tier comparison for the UI), plus:
      - meta: extra context about the built tx (e.g. product_id_hex, the
        resolved contract address) — informational only, not part of the
        object sent to eth_sendTransaction.
      - simulation_warning: set if a pre-flight eth_call dry run suggests
        this tx would revert, so the UI can flag it before the user signs.
    """
    network = intent.get("network")
    strategy = evaluate_strategy(intent, from_address, network=network)
    tx_params = build_unsigned_tx(intent, strategy, from_address)

    meta = tx_params.pop("_meta", None)
    simulation_warning = tx_params.pop("_simulation_warning", None)

    return {
        "tx_params": tx_params,
        "strategy":  strategy,
        "meta": meta,
        "simulation_warning": simulation_warning,
    }