"""
IntentChain — Live Gas Oracle & Fee Optimizer

Replaces the old static "1/2/3 gwei tier" heuristic with a real fee-market
read: it pulls recent block base fees + priority-fee percentiles via
`eth_feeHistory` and derives three optimization profiles:

  FASTEST   — optimizes for confirmation speed. Pays a high priority fee
              (90th percentile of recent tips) and a generous maxFeePerGas
              buffer so the tx doesn't get stuck if the base fee jumps.
              "Fast transaction requirement, gas price is not the concern —
              but still don't overpay stupidly."

  STANDARD  — balanced default. Median (50th percentile) tip, moderate
              buffer. Confirms within a few blocks under normal load.

  CHEAPEST  — optimizes purely for lowest cost. Minimal viable priority fee
              (10th percentile, floored) and the smallest safe maxFee buffer.
              Explicitly trades speed for cost and says so.

Every tier reports an estimated confirmation window so the trade-off is
visible, not just the price.
"""
from __future__ import annotations

from web3 import Web3

FEE_HISTORY_BLOCKS = 20
PERCENTILES = [10, 50, 90]

# floor so we never quote literally 0 gwei priority fee, which real nodes
# will often drop / deprioritize indefinitely
MIN_PRIORITY_FEE_WEI = Web3.to_wei(0.05, "gwei")

TIER_META = {
    "cheapest": {
        "percentile_index": 0,        # 10th percentile tip
        "tip_multiplier":   1.0,
        "base_fee_buffer":  1.15,     # small cushion — riskier if base fee spikes
        "eta": "may take several blocks (~1-3 min); can stall if network activity spikes",
    },
    "standard": {
        "percentile_index": 1,        # 50th percentile tip
        "tip_multiplier":   1.1,
        "base_fee_buffer":  1.35,
        "eta": "typically confirms within 2-3 blocks (~30-45s)",
    },
    "fastest": {
        "percentile_index": 2,        # 90th percentile tip
        "tip_multiplier":   1.25,
        "base_fee_buffer":  2.0,      # generous cushion for next-block inclusion
        "eta": "optimized for next-block inclusion (~12-15s)",
    },
}

PRIORITY_TO_TIER = {
    "fast": "fastest",
    "high": "fastest",
    "urgent": "fastest",
    "normal": "standard",
    "standard": "standard",
    "balanced": "standard",
    "low_cost": "cheapest",
    "low": "cheapest",
    "cheap": "cheapest",
    "cheapest": "cheapest",
}


def resolve_tier(priority: str | None, gas_mode: str | None = None) -> str:
    """gas_mode (explicit: fastest/standard/cheapest) always wins over the
    looser natural-language `priority` field when both are present."""
    if gas_mode and gas_mode.lower() in TIER_META:
        return gas_mode.lower()
    return PRIORITY_TO_TIER.get((priority or "normal").lower(), "standard")


def _legacy_gas_price_strategies(w3: Web3) -> dict:
    """Fallback for chains/nodes that don't support eth_feeHistory (pre-1559)."""
    gas_price = w3.eth.gas_price
    out = {}
    for tier, mult in (("cheapest", 0.9), ("standard", 1.0), ("fastest", 1.5)):
        price = int(gas_price * mult)
        out[tier] = {
            "type": "legacy",
            "gas_price_gwei": round(float(w3.from_wei(price, "gwei")), 4),
            "gas_price_wei": price,
            "eta": TIER_META[tier]["eta"],
        }
    return out


def compute_gas_strategies(w3: Web3) -> dict:
    """
    Returns:
    {
      "supports_1559": bool,
      "base_fee_gwei": float | None,
      "tiers": {
         "cheapest":  {priority_fee_gwei, max_fee_gwei, max_fee_wei, priority_fee_wei, eta},
         "standard":  {...},
         "fastest":   {...},
      }
    }
    """
    try:
        latest = w3.eth.get_block("latest")
        base_fee_wei = latest.get("baseFeePerGas")
    except Exception:
        base_fee_wei = None

    if base_fee_wei is None:
        legacy = _legacy_gas_price_strategies(w3)
        return {"supports_1559": False, "base_fee_gwei": None, "tiers": legacy}

    try:
        history = w3.eth.fee_history(FEE_HISTORY_BLOCKS, "latest", PERCENTILES)
        rewards = [r for r in history.get("reward", []) if r]
    except Exception:
        rewards = []

    tiers: dict[str, dict] = {}
    for tier_name, meta in TIER_META.items():
        idx = meta["percentile_index"]
        if rewards:
            samples = [r[idx] for r in rewards if len(r) > idx]
            avg_tip = int(sum(samples) / len(samples)) if samples else MIN_PRIORITY_FEE_WEI
        else:
            avg_tip = MIN_PRIORITY_FEE_WEI

        priority_fee_wei = max(int(avg_tip * meta["tip_multiplier"]), MIN_PRIORITY_FEE_WEI)
        max_fee_wei = int(base_fee_wei * meta["base_fee_buffer"]) + priority_fee_wei

        tiers[tier_name] = {
            "type": "eip1559",
            "priority_fee_gwei": round(float(w3.from_wei(priority_fee_wei, "gwei")), 5),
            "priority_fee_wei":  priority_fee_wei,
            "max_fee_gwei":      round(float(w3.from_wei(max_fee_wei, "gwei")), 5),
            "max_fee_wei":       max_fee_wei,
            "eta":               meta["eta"],
        }

    return {
        "supports_1559": True,
        "base_fee_gwei": round(float(w3.from_wei(base_fee_wei, "gwei")), 5),
        "tiers": tiers,
    }


def estimate_gas(w3: Web3, tx_call: dict, fallback: int) -> int:
    """Best-effort `eth_estimateGas` with a safe fallback. Never raises —
    a bad/underfunded `from` address is common in demo flows (unsigned tx
    preview before the user has funded their wallet) and shouldn't 500 the
    whole request."""
    try:
        return int(w3.eth.estimate_gas(tx_call))
    except Exception:
        return fallback
