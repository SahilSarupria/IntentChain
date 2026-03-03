from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

INFURA_URL = os.getenv("INFURA_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

w3 = Web3(Web3.HTTPProvider(INFURA_URL))

print("Loaded INFURA:", INFURA_URL)

def execute_transaction(intent, strategy):
    try:
        print("INFURA:", INFURA_URL)
        print("WALLET:", WALLET_ADDRESS)

        nonce = w3.eth.get_transaction_count(WALLET_ADDRESS, "pending")

        tx = {
            'nonce': nonce,
            'to': intent["recipient"],
            'value': w3.to_wei(intent["amount"], 'ether'),
            'gas': strategy["gas_estimate"],
            'gasPrice': w3.to_wei('20', 'gwei'),
        }

        signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        return {"tx_hash": tx_hash.hex()}

    except Exception as e:
        print("ERROR:", str(e))
        return {"error": str(e)}