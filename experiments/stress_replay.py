"""
Stress + holdout replay for ICS (addresses faculty point 2 and part of 5).
Reuses replay_selection.py and injects (a) CONGESTION: each chain is congested with prob 0.3, its fees x MULT (quote and
realised, plus extra realised surge), (b) RPC OUTAGE: each chain is down with prob P for the request; a health probe
detects it with prob 0.9 (ICS excludes detected-down chains; naive baselines do not check), (c) extra baselines:
linear weighted-sum (min-max) and TOPSIS. Stress parameters are SIMULATED and must be labelled as such in the paper.
Usage:
  python stress_replay.py --data ../snapshots/data --out ../results --scenario baseline
  python stress_replay.py --data ../snapshots_new/data --out ../results --scenario congestion --mult 5
  python stress_replay.py --data ../snapshots_new/data --out ../results --scenario outage --p 0.15
  CLEAN_RUNS=runidA,runidB python stress_replay.py ...      # for NEW (unseen) collection runs
"""
import argparse, json, math, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay_selection as rp
from chain_selector import TIER_WEIGHTS

ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--out", default="../results")
ap.add_argument("--scenario", choices=["baseline", "congestion", "outage"], default="baseline")
ap.add_argument("--mult", type=float, default=5.0); ap.add_argument("--p", type=float, default=0.15); ap.add_argument("--per", type=int, default=300)
a = ap.parse_args()
if os.environ.get("CLEAN_RUNS"): rp.CLEAN_RUNS = set(os.environ["CLEAN_RUNS"].split(","))

_draw = rp.draw
def draw(rng, snaps, prices, pair):
    req = _draw(rng, snaps, prices, pair)
    for n, c in req["chains"].items():
        c["down"] = False; c["detected"] = False
        if a.scenario == "congestion" and rng.random() < 0.3:
            surge = 1 + rng.uniform(0, a.mult / 2)
            c["fee_q"] *= a.mult; c["fee_r"] *= a.mult * surge; c["t_q"] *= 1 + (a.mult - 1) * .1; c["t_r"] *= 1 + (a.mult - 1) * .1 * surge
        if a.scenario == "outage" and rng.random() < a.p:
            c["down"] = True; c["detected"] = rng.random() < 0.9
    return req
def ok(c): return c["supported"] and c["bal"] and not c.get("down", False)
def cands(req):
    return [rp.ChainCandidate(n, req["chains"][n]["fee_q"], req["chains"][n]["t_q"], 1.0, not (req["chains"][n].get("down") and req["chains"][n].get("detected")),
                              req["chains"][n]["supported"], req["chains"][n]["bal"]) for n in rp.ORDER]
rp.draw, rp.ok, rp.cands = draw, ok, cands

def feas(q): return [c for c in cands(q) if c.asset_supported and c.balance_ok and c.healthy]
def mm(v):
    lo, hi = min(v), max(v); return [0.0 if hi - lo < 1e-12 else (x - lo) / (hi - lo) for x in v]
def wsum(q, g):
    F = feas(q) or [c for c in cands(q) if c.asset_supported]; w = TIER_WEIGHTS[q["tier"]]
    s = [w[0] * f + w[1] * t for f, t in zip(mm([c.fee_usd for c in F]), mm([c.conf_time_s for c in F]))]
    return F[s.index(min(s))].network
def topsis(q, g):
    F = feas(q) or [c for c in cands(q) if c.asset_supported]; w = TIER_WEIGHTS[q["tier"]][:2]
    M = [[c.fee_usd, c.conf_time_s] for c in F]; nrm = [math.sqrt(sum(r[j] ** 2 for r in M)) or 1 for j in range(2)]
    V = [[w[j] * r[j] / nrm[j] for j in range(2)] for r in M]; best = [min(r[j] for r in V) for j in range(2)]; worst = [max(r[j] for r in V) for j in range(2)]
    d = lambda r, p: math.sqrt(sum((r[j] - p[j]) ** 2 for j in range(2)))
    cl = [d(r, worst) / ((d(r, best) + d(r, worst)) or 1) for r in V]
    return F[cl.index(max(cl))].network
rp.S.insert(-1, ("Weighted-sum + constr. (min-max)", wsum)); rp.S.insert(-1, ("TOPSIS + constr.", topsis))

snaps, prices = rp.load(a.data); pairs = rp.build_pairs(snaps, prices); print("cycles", len(snaps), "pairs", len(pairs), "scenario", a.scenario)
out = {"scenario": a.scenario, "mult": a.mult, "p": a.p, "pairs": len(pairs), "by_CK": {}}
for CK in (1.0, 25.0, 100.0):
    runs = [rp.run(s, snaps, prices, pairs, a.per, CK) for s in (11, 22, 33, 44, 55)]
    summ = {n: {m: dict(mean=st.mean(r[0][n][m] for r in runs), sd=st.stdev(r[0][n][m] for r in runs)) for m in ("D", "regret", "fail", "fee", "lat")} for n in runs[0][0]}
    out["by_CK"][str(CK)] = summ; print(f"\n== CK={CK}")
    for n, m in summ.items(): print(f"{n:34s} regret={m['regret']['mean']:.4f}+-{m['regret']['sd']:.4f} fail%={m['fail']['mean']:.2f} fee$={m['fee']['mean']:.4f} lat={m['lat']['mean']:.1f}")
os.makedirs(a.out, exist_ok=True)
json.dump(out, open(os.path.join(a.out, f"stress_{a.scenario}{'_' + str(int(a.mult)) if a.scenario == 'congestion' else '_' + str(int(a.p * 100)) if a.scenario == 'outage' else ''}.json"), "w"), indent=1)
