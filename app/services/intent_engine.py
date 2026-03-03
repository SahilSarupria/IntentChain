from app.services.strategy_engine import evaluate_strategy
from app.blockchain.executor import execute_transaction

def process_intent(intent):
    structured_intent = {
        "action": intent.action,
        "amount": intent.amount,
        "recipient": intent.recipient,
        "network": intent.network,
        "priority": intent.priority
    }

    strategy = evaluate_strategy(structured_intent)
    tx_result = execute_transaction(structured_intent, strategy)

    return {
        "intent": structured_intent,
        "strategy": strategy,
        "transaction_result": tx_result
    }