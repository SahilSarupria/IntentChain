def evaluate_strategy(intent):
    # Simple demo scoring logic
    gas_estimate = 21000

    if intent["priority"] == "low_cost":
        gas_price = "standard"
    elif intent["priority"] == "fast":
        gas_price = "high"
    else:
        gas_price = "standard"

    return {
        "gas_estimate": gas_estimate,
        "gas_price_strategy": gas_price
    }