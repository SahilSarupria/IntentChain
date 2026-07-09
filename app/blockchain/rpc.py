"""
IntentChain — RPC connection manager.

Provides a cached Web3 instance per network so we don't re-open an HTTP
provider on every request, plus a small connectivity probe used by health
and /networks endpoints.
"""
from web3 import Web3

from app.config.networks import get_rpc_url, normalize_network

_clients: dict[str, Web3] = {}


def get_w3(network: str | None = None) -> Web3:
    key = normalize_network(network)
    if key not in _clients:
        url = get_rpc_url(key)
        _clients[key] = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
    return _clients[key]


def is_connected(network: str | None = None) -> bool:
    try:
        return get_w3(network).is_connected()
    except Exception:
        return False


def reset_cache() -> None:
    """Useful in tests or after env vars change at runtime."""
    _clients.clear()
