from app.services.strategy_engine import evaluate_strategy
from app.blockchain.executor import build_unsigned_tx


def build_tx_for_wallet(intent_dict: dict, from_address: str) -> dict:
    """
    Evaluate strategy and build an unsigned transaction.
    Called after MetaMask wallet address is known.
    Returns tx_params (for MetaMask) + strategy metadata.
    """
    strategy  = evaluate_strategy(intent_dict, from_address=from_address)
    tx_params = build_unsigned_tx(intent_dict, strategy, from_address)

    return {
        "intent":    intent_dict,
        "strategy":  strategy,
        "tx_params": tx_params,   # handed to MetaMask for signing + broadcast
    }
