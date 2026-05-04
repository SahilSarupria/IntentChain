from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
# ✅ PRIVATE_KEY removed — signing now happens in the browser via MetaMask
# ✅ WALLET_ADDRESS removed — comes from the connected MetaMask wallet at runtime

w3 = Web3(Web3.HTTPProvider(INFURA_URL))

print("Loaded INFURA:", INFURA_URL)

# Network name to chain ID mapping
NETWORK_CHAIN_IDS = {
    "sepolia": "0xaa36a7",          # 11155111 in decimal
    "ethereum": "0x1",               # mainnet
    "polygon": "0x89",               # 137 in decimal
    "arbitrum": "0xa4b1",            # 42161 in decimal
    "optimism": "0xa",               # 10 in decimal
    "bsc": "0x38",                   # 56 in decimal (Binance Smart Chain)
}


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
    Includes chainId so MetaMask knows the intended network (Sepolia, mainnet, etc.).
    """
    checksum_from = Web3.to_checksum_address(from_address)
    checksum_to   = Web3.to_checksum_address(intent["recipient"])

    block    = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", w3.eth.gas_price)
    prio_fee = get_priority_fee(intent["priority"])
    max_fee  = base_fee + prio_fee

    value_wei = w3.to_wei(intent["amount"], "ether")
    
    # Get the chain ID for the intended network (default to Sepolia for safety)
    network = intent.get("network", "sepolia").lower()
    chain_id = NETWORK_CHAIN_IDS.get(network, "0xaa36a7")  # default: Sepolia

    return {
        "from":                 checksum_from,
        "to":                   checksum_to,
        "value":                hex(value_wei),
        "gas":                  hex(strategy["gas_estimate"]),
        "maxFeePerGas":         hex(max_fee),
        "maxPriorityFeePerGas": hex(prio_fee),
        "chainId":              chain_id,  # ✅ Now explicitly set so MetaMask knows the intended network
    }
