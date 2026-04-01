from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

w3 = Web3(Web3.HTTPProvider(INFURA_URL))

print("Loaded INFURA:", INFURA_URL)


def get_priority_fee(priority):
    if priority == "fast":
        return w3.to_wei(3, "gwei")
    if priority == "low_cost":
        return w3.to_wei(1, "gwei")
    return w3.to_wei(2, "gwei")

def execute_transaction(intent, strategy):
    try:
        
        print("INFURA:", INFURA_URL)
        print("WALLET:", WALLET_ADDRESS)
        print("Connected:", w3.is_connected())
        print("Chain ID:", w3.eth.chain_id)
        print("Wallet:", WALLET_ADDRESS)
        print("Nonce:", w3.eth.get_transaction_count(WALLET_ADDRESS))
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(WALLET_ADDRESS))
        block = w3.eth.get_block("latest")
        base_fee = block["baseFeePerGas"] if "baseFeePerGas" in block else w3.eth.gas_price
        priority_fee = get_priority_fee(intent["priority"])

        tx = {
            'nonce': nonce,
            'to': intent["recipient"],
            'value': w3.to_wei(intent["amount"], 'ether'),
            'gas': strategy["gas_estimate"],
            'maxFeePerGas': base_fee + priority_fee,
            'maxPriorityFeePerGas': priority_fee,
            'chainId': w3.eth.chain_id,
        }

        signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        return {"tx_hash": tx_hash.hex()}

    except Exception as e:
        print("ERROR:", str(e))
        return {"error": str(e)}