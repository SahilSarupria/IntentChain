from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
# ✅ PRIVATE_KEY removed — signing now happens in the browser via MetaMask
# ✅ WALLET_ADDRESS removed — comes from the connected MetaMask wallet at runtime

w3 = Web3(Web3.HTTPProvider(INFURA_URL))

print("Loaded INFURA:", INFURA_URL)


def get_priority_fee(priority: str) -> int:
    if priority == "fast":
        return w3.to_wei(3, "gwei")
    if priority == "low_cost":
        return w3.to_wei(1, "gwei")
    return w3.to_wei(2, "gwei")


def build_unsigned_tx(intent: dict, strategy: dict, from_address: str) -> dict:
    """
    Build an EIP-1559 transaction dict suitable for MetaMask's eth_sendTransaction.

    Returns hex-encoded fields (MetaMask expects hex strings, not ints).
    The nonce is intentionally omitted — MetaMask manages it automatically.
    The private key is never touched here.
    """
    checksum_from = Web3.to_checksum_address(from_address)
    checksum_to   = Web3.to_checksum_address(intent["recipient"])

    block    = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", w3.eth.gas_price)
    prio_fee = get_priority_fee(intent["priority"])
    max_fee  = base_fee + prio_fee

    value_wei = w3.to_wei(intent["amount"], "ether")

    return {
        "from":                 checksum_from,
        "to":                   checksum_to,
        "value":                hex(value_wei),
        "gas":                  hex(strategy["gas_estimate"]),
        "maxFeePerGas":         hex(max_fee),
        "maxPriorityFeePerGas": hex(prio_fee),
        # chainId omitted — MetaMask infers it from the user's active network
    }
