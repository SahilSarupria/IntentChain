"""
ABI for SupplyChainTraceability.sol (app/blockchain/contracts/SupplyChainTraceability.sol).
Hand-authored to match the contract exactly — regenerate from the compiler
output (`solc --abi`) if the contract source changes.
"""

SUPPLY_CHAIN_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"},
                   {"internalType": "uint8", "name": "role", "type": "uint8"}],
        "name": "grantRole", "outputs": [], "stateMutability": "nonpayable", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"},
                   {"internalType": "string", "name": "name", "type": "string"},
                   {"internalType": "string", "name": "origin", "type": "string"}],
        "name": "registerProduct", "outputs": [], "stateMutability": "nonpayable", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"},
                   {"internalType": "string", "name": "location", "type": "string"},
                   {"internalType": "string", "name": "status", "type": "string"},
                   {"internalType": "int256", "name": "temperatureC", "type": "int256"}],
        "name": "logCheckpoint", "outputs": [], "stateMutability": "nonpayable", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"}],
        "name": "getProduct",
        "outputs": [
            {"internalType": "string", "name": "name", "type": "string"},
            {"internalType": "string", "name": "origin", "type": "string"},
            {"internalType": "address", "name": "manufacturer", "type": "address"},
            {"internalType": "uint256", "name": "registeredAt", "type": "uint256"},
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"}],
        "name": "getCheckpointCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"},
                   {"internalType": "uint256", "name": "index", "type": "uint256"}],
        "name": "getCheckpoint",
        "outputs": [
            {"internalType": "string", "name": "location", "type": "string"},
            {"internalType": "string", "name": "status", "type": "string"},
            {"internalType": "int256", "name": "temperatureC", "type": "int256"},
            {"internalType": "address", "name": "recordedBy", "type": "address"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "productId", "type": "bytes32"}],
        "name": "verifyAuthenticity",
        "outputs": [
            {"internalType": "bool", "name": "exists", "type": "bool"},
            {"internalType": "address", "name": "manufacturer", "type": "address"},
            {"internalType": "uint256", "name": "checkpointCount", "type": "uint256"},
        ],
        "stateMutability": "view", "type": "function",
    },
]

# Role enum mirror (must match the Solidity `enum Role` ordering)
ROLE_MANUFACTURER = 1
ROLE_DISTRIBUTOR = 2
ROLE_RETAILER = 3
ROLE_AUDITOR = 4
