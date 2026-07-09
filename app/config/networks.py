"""
IntentChain — Network Registry
Central source of truth for every chain the middleware understands.

Design goals:
- Works out-of-the-box on public RPCs so balance/gas/history features are
  usable even before a user configures their own Infura/Alchemy keys.
- Any network's RPC can be overridden via env var (see `rpc_env`), which lets
  production deployments swap in dedicated, rate-limit-friendly endpoints.
- `chain_id` doubles as the identifier Etherscan's unified v2 API expects.
"""
import os

NETWORKS: dict[str, dict] = {
    "ethereum": {
        "label":       "Ethereum Mainnet",
        "chain_id":    1,
        "native":      "ETH",
        "rpc_env":     "RPC_URL_ETHEREUM",
        "default_rpc": "https://eth.llamarpc.com",
        "explorer":    "https://etherscan.io",
        "explorer_api_supported": True,
    },
    "sepolia": {
        "label":       "Sepolia Testnet",
        "chain_id":    11155111,
        "native":      "ETH",
        # Backwards compatible: original project used INFURA_URL for sepolia.
        "rpc_env":     "INFURA_URL",
        "rpc_env_fallback": "RPC_URL_SEPOLIA",
        "default_rpc": "https://ethereum-sepolia-rpc.publicnode.com",
        "explorer":    "https://sepolia.etherscan.io",
        "explorer_api_supported": True,
    },
    "polygon": {
        "label":       "Polygon PoS",
        "chain_id":    137,
        "native":      "MATIC",
        "rpc_env":     "RPC_URL_POLYGON",
        "default_rpc": "https://polygon-rpc.com",
        "explorer":    "https://polygonscan.com",
        "explorer_api_supported": True,
    },
    "arbitrum": {
        "label":       "Arbitrum One",
        "chain_id":    42161,
        "native":      "ETH",
        "rpc_env":     "RPC_URL_ARBITRUM",
        "default_rpc": "https://arb1.arbitrum.io/rpc",
        "explorer":    "https://arbiscan.io",
        "explorer_api_supported": True,
    },
    "optimism": {
        "label":       "OP Mainnet",
        "chain_id":    10,
        "native":      "ETH",
        "rpc_env":     "RPC_URL_OPTIMISM",
        "default_rpc": "https://mainnet.optimism.io",
        "explorer":    "https://optimistic.etherscan.io",
        "explorer_api_supported": True,
    },
    "bsc": {
        "label":       "BNB Smart Chain",
        "chain_id":    56,
        "native":      "BNB",
        "rpc_env":     "RPC_URL_BSC",
        "default_rpc": "https://bsc-dataseed.binance.org",
        "explorer":    "https://bscscan.com",
        "explorer_api_supported": True,
    },
}

DEFAULT_NETWORK = "sepolia"


def normalize_network(network: str | None) -> str:
    key = (network or DEFAULT_NETWORK).strip().lower()
    return key if key in NETWORKS else DEFAULT_NETWORK


def get_network_config(network: str | None) -> dict:
    return NETWORKS[normalize_network(network)]


def get_rpc_url(network: str | None) -> str:
    cfg = get_network_config(network)
    url = os.getenv(cfg["rpc_env"])
    if not url and cfg.get("rpc_env_fallback"):
        url = os.getenv(cfg["rpc_env_fallback"])
    return url or cfg["default_rpc"]


def get_chain_id(network: str | None) -> int:
    return get_network_config(network)["chain_id"]


def get_explorer_base(network: str | None) -> str:
    return get_network_config(network)["explorer"]


def list_networks() -> list[dict]:
    return [
        {
            "id": key,
            "label": cfg["label"],
            "chain_id": cfg["chain_id"],
            "native": cfg["native"],
            "explorer": cfg["explorer"],
        }
        for key, cfg in NETWORKS.items()
    ]

def contract_address_env_var(base_var: str, network: str) -> str:
    """e.g. SUPPLY_CHAIN_CONTRACT_ADDRESS -> SUPPLY_CHAIN_CONTRACT_ADDRESS_SEPOLIA"""
    return f"{base_var}_{normalize_network(network).upper()}"
