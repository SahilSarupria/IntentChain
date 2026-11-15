"""
IntentChain -- Intent-Aware Multi-Chain Selector (ICS)

Chooses WHICH EVM network to execute on when the user did not (or should not
have to) name one, e.g. "send 25 USDC to 0x... cheap".

Design (see paper Sec. V-E):
  1. Hard feasibility filter  -- asset exists on the chain, wallet holds
     amount + gas there, and the chain is not flagged unhealthy by the
     circuit breaker. An infeasible chain is never ranked, however cheap.
  2. Intent-weighted utility  -- remaining chains are scored with
        S(c) = w_cost*C(c) + w_time*T(c) + w_rel*(1-R(c))
     where C, T are log-scaled min-max normalised across the feasible set
     (fees span orders of magnitude between L1 and L2/sidechains) and the
     weights come from the user's stated priority (cheapest/standard/fastest).
  3. Value-aware weight shift -- if even the cheapest feasible chain costs a
     large fraction of the transfer value, cost weight is raised, so a $5
     transfer is not sent through a $2 fee just because the user said "normal".

Pure Python, no web3 dependency: the caller supplies ChainCandidate values
(built from gas_oracle / price_feed / wallet balances). `candidates_from_live`
at the bottom shows the wiring and is the only part that touches the network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# (w_cost, w_time, w_reliability) per gas tier (tiers as in gas_oracle.TIER_META)
TIER_WEIGHTS = {
    "cheapest": (0.70, 0.15, 0.15),
    "standard": (0.40, 0.40, 0.20),
    "fastest":  (0.15, 0.70, 0.15),
}
EQUAL_WEIGHTS = (1 / 3, 1 / 3, 1 / 3)

VALUE_GAMMA = 0.5    # max share of the remaining weight moved to cost
VALUE_TAU = 0.05     # fee/value ratio at which the shift saturates


@dataclass
class ChainCandidate:
    network: str
    fee_usd: float                 # quoted total fee for THIS transaction type
    conf_time_s: float             # expected time to inclusion at chosen tier
    reliability: float = 0.97      # EWMA of recent RPC/inclusion success, 0..1
    healthy: bool = True           # circuit-breaker state from latest probe
    asset_supported: bool = True   # token/native asset exists on this chain
    balance_ok: bool = True        # wallet holds amount (+ native gas) here


@dataclass
class Selection:
    network: str | None
    ranking: list = field(default_factory=list)   # [(network, score, parts)]
    excluded: dict = field(default_factory=dict)  # network -> reason
    weights: tuple = ()
    explanation: str = ""


def _lognorm(values: list[float]) -> list[float]:
    logs = [math.log(max(v, 1e-9)) for v in values]
    lo, hi = min(logs), max(logs)
    if hi - lo < 1e-12:
        return [0.0] * len(values)
    return [(x - lo) / (hi - lo) for x in logs]


def select_chain(cands: list[ChainCandidate], tier: str, amount_usd: float,
                 *, use_balance: bool = True, use_health: bool = True,
                 use_value_aware: bool = True, weights: tuple | None = None) -> Selection:
    """Return the best feasible chain. Flags exist so the ablations in the
    paper can switch single components off; production use leaves defaults."""
    w_c, w_t, w_r = weights or TIER_WEIGHTS.get(tier, TIER_WEIGHTS["standard"])

    feasible, excluded = [], {}
    for c in cands:
        if not c.asset_supported:
            excluded[c.network] = "asset not available on this chain"
        elif use_balance and not c.balance_ok:
            excluded[c.network] = "wallet lacks required balance/gas here"
        elif use_health and not c.healthy:
            excluded[c.network] = "RPC/health probe failing"
        else:
            feasible.append(c)

    if not feasible:
        return Selection(None, [], excluded, (w_c, w_t, w_r),
                         "No feasible chain: " + "; ".join(f"{k}: {v}" for k, v in excluded.items()))

    if use_value_aware and amount_usd > 0:
        ratio = min(c.fee_usd for c in feasible) / amount_usd
        shift = VALUE_GAMMA * min(1.0, ratio / VALUE_TAU)
        rest = w_t + w_r
        w_c_adj = w_c + (1 - w_c) * shift
        scale = (1 - w_c_adj) / rest if rest > 0 else 0
        w_c, w_t, w_r = w_c_adj, w_t * scale, w_r * scale

    cn = _lognorm([c.fee_usd for c in feasible])
    tn = _lognorm([c.conf_time_s for c in feasible])
    ranked = []
    for c, ci, ti in zip(feasible, cn, tn):
        parts = {"cost": ci, "time": ti, "unreliability": 1 - c.reliability}
        score = w_c * ci + w_t * ti + w_r * (1 - c.reliability)
        ranked.append((c.network, score, parts))
    ranked.sort(key=lambda r: r[1])
    best = ranked[0]
    expl = (f"Chose {best[0]} (score {best[1]:.3f}; weights cost/time/rel = "
            f"{w_c:.2f}/{w_t:.2f}/{w_r:.2f}). "
            + (f"Excluded: {', '.join(f'{k} ({v})' for k, v in excluded.items())}." if excluded else ""))
    return Selection(best[0], ranked, excluded, (w_c, w_t, w_r), expl)


def candidates_from_live(intent: dict, from_address: str, networks: list[str]) -> list[ChainCandidate]:
    """Wiring to the existing modules (UNTESTED against live RPCs in the
    paper's sandbox -- it needs network access). Imports are local so the
    selector itself stays dependency-free."""
    from app.blockchain.rpc import get_w3, is_connected
    from app.blockchain.gas_oracle import compute_gas_strategies, resolve_tier
    from app.blockchain import erc20
    from app.config.networks import get_network_config
    from app.services.price_feed import estimate_usd_cost

    tier = resolve_tier(intent.get("priority"), intent.get("gas_mode"))
    is_token = str(intent.get("action", "")).lower() in ("transfer_token", "send_token", "approve_token")
    gas_units = 65_000 if is_token else 21_000
    out = []
    for n in networks:
        cfg = get_network_config(n)
        up = is_connected(n)
        cand = ChainCandidate(network=n, fee_usd=float("inf"), conf_time_s=60.0, healthy=up)
        if up:
            w3 = get_w3(n)
            tiers = compute_gas_strategies(w3)["tiers"]
            fee_gwei = tiers[tier].get("max_fee_gwei", tiers[tier].get("gas_price_gwei"))
            usd = estimate_usd_cost(gas_units, fee_gwei, cfg["native"])
            cand.fee_usd = usd if usd is not None else float("inf")
            if is_token:
                addr, _ = erc20.resolve_token_address(n, intent.get("token"))
                cand.asset_supported = addr is not None
            else:
                cand.asset_supported = str(intent.get("token", "")).upper() == cfg["native"]
            # balance_ok: compare erc20.get_token_balance / get_native_balance with amount + gas
        out.append(cand)
    return out
