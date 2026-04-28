import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
# ✅ WALLET_ADDRESS removed from env — the connected MetaMask address is passed in at runtime

w3 = Web3(Web3.HTTPProvider(INFURA_URL))


def evaluate_strategy(intent: dict, from_address: str = "") -> dict:
    """
    Estimate gas and choose a gas-price strategy.
    `from_address` is the MetaMask wallet address supplied by the frontend.
    Falls back to a safe 21 000 estimate if the node can't simulate.
    """
    gas_estimate = 21_000
    try:
        if from_address and w3.is_connected():
            gas_estimate = w3.eth.estimate_gas({
                "to":    Web3.to_checksum_address(intent["recipient"]),
                "from":  Web3.to_checksum_address(from_address),
                "value": w3.to_wei(intent["amount"], "ether"),
            })
    except Exception:
        gas_estimate = 21_000

    gas_price_strategy = "high" if intent["priority"] == "fast" else "standard"

    return {
        "gas_estimate":        gas_estimate,
        "gas_price_strategy":  gas_price_strategy,
    }
