import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

w3 = Web3(Web3.HTTPProvider(INFURA_URL))


def evaluate_strategy(intent):
    # Estimate transfer gas if possible; keep a safe fallback for local/dev startup.
    gas_estimate = 21000
    try:
        if WALLET_ADDRESS and w3.is_connected():
            gas_estimate = w3.eth.estimate_gas(
                {
                    "to": intent["recipient"],
                    "from": Web3.to_checksum_address(WALLET_ADDRESS),
                    "value": w3.to_wei(intent["amount"], "ether"),
                }
            )
    except Exception:
        gas_estimate = 21000

    if intent["priority"] == "low_cost":
        gas_price = "standard"
    elif intent["priority"] == "fast":
        gas_price = "high"
    else:
        gas_price = "standard"

    return {
        "gas_estimate": gas_estimate,
        "gas_price_strategy": gas_price,
    }