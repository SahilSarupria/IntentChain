"""
IntentChain — Strategy Engine

Turns a parsed intent + live chain state into a concrete gas/fee strategy.
Upgrades over the original static heuristic:
  - Real fee-market data via app.blockchain.gas_oracle (EIP-1559 fee history)
  - Three transparent optimization tiers (fastest / standard / cheapest) so
    the UI can show the trade-off, not just a single opaque number
  - Multi-network aware (was hardcoded to a single INFURA_URL/sepolia client)
  - USD cost estimate via a live price feed, for human-readable comparison
"""
from web3 import Web3

from app.blockchain.rpc import get_w3, is_connected
from app.blockchain.gas_oracle import compute_gas_strategies, resolve_tier
from app.config.networks import get_network_config, normalize_network
from app.services.price_feed import estimate_usd_cost

DEFAULT_NATIVE_GAS = 21_000


def _fee_fields_for_tier(tier: dict) -> dict:
    if tier.get("type") == "eip1559":
        return {
            "maxFeePerGas": hex(tier["max_fee_wei"]),
            "maxPriorityFeePerGas": hex(tier["priority_fee_wei"]),
        }
    return {"gasPrice": hex(tier["gas_price_wei"])}


def evaluate_strategy(intent: dict, from_address: str = "", network: str | None = None) -> dict:
    network = normalize_network(network or intent.get("network"))
    cfg = get_network_config(network)
    w3 = get_w3(network)
    connected = is_connected(network)

    gas_oracle_result = compute_gas_strategies(w3) if connected else {
        "supports_1559": False,
        "base_fee_gwei": None,
        "tiers": {
            "cheapest": {"type": "legacy", "gas_price_gwei": 1.0, "gas_price_wei": Web3.to_wei(1, "gwei"), "eta": "unknown — RPC unreachable"},
            "standard": {"type": "legacy", "gas_price_gwei": 2.0, "gas_price_wei": Web3.to_wei(2, "gwei"), "eta": "unknown — RPC unreachable"},
            "fastest":  {"type": "legacy", "gas_price_gwei": 3.0, "gas_price_wei": Web3.to_wei(3, "gwei"), "eta": "unknown — RPC unreachable"},
        },
    }

    tier_name = resolve_tier(intent.get("priority"), intent.get("gas_mode"))
    chosen_tier = gas_oracle_result["tiers"][tier_name]

    gas_estimate = DEFAULT_NATIVE_GAS
    try:
        if connected and from_address and intent.get("recipient") and Web3.is_address(str(intent.get("recipient"))):
            gas_estimate = w3.eth.estimate_gas({
                "to":    Web3.to_checksum_address(intent["recipient"]),
                "from":  Web3.to_checksum_address(from_address),
                "value": w3.to_wei(float(intent.get("amount") or 0), "ether"),
            })
    except Exception:
        gas_estimate = DEFAULT_NATIVE_GAS

    display_fee_gwei = chosen_tier.get("max_fee_gwei", chosen_tier.get("gas_price_gwei"))
    usd_estimate = estimate_usd_cost(gas_estimate, display_fee_gwei, cfg["native"]) if display_fee_gwei else None

    # Legacy field kept for any older callers: "high" for fastest, else "standard"
    legacy_strategy_label = "high" if tier_name == "fastest" else "standard"

    return {
        "network":              network,
        "chain_id":             cfg["chain_id"],
        "native_symbol":        cfg["native"],
        "native_display":       cfg.get("display_native", cfg["native"]),
        "is_testnet":           cfg.get("is_testnet", False),
        "faucets":              cfg.get("faucets", []),
        "gas_estimate":         gas_estimate,
        "gas_price_strategy":   legacy_strategy_label,
        "selected_tier":        tier_name,
        "tier_detail":          chosen_tier,
        "gas_tiers":            gas_oracle_result["tiers"],
        "supports_1559":        gas_oracle_result["supports_1559"],
        "base_fee_gwei":        gas_oracle_result["base_fee_gwei"],
        "estimated_cost_usd":   usd_estimate,
        "node_connected":       connected,
        "gas_fallback":         not connected,
        "fee_fields":           _fee_fields_for_tier(chosen_tier),
        # kept for backwards compatibility with the original UI field names
        "max_fee_gwei":         chosen_tier.get("max_fee_gwei", chosen_tier.get("gas_price_gwei")),
    }