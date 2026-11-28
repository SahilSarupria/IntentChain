"""
Pair-level (cluster) bootstrap for the ICS replay -- NEW script added with the revision.

Why: the 5 seeds in replay_selection.py / stress_replay.py resample *requests* from the same cycle pairs, so their SD
ignores uncertainty from the short measurement window. Here the unit of replication is the consecutive-cycle PAIR:
for every pair we compute each strategy's mean regret over PER modelled requests, then resample pairs with replacement
(B times) to get a 95% percentile interval for the regret DIFFERENCE  (ICS-M minus competitor). Negative = ICS-M better.

Usage (from experiments/):
  python pair_bootstrap.py --data ../snapshots/data --runs 20261112T140034,20261114T234312 --tag main    --out ../results
  python pair_bootstrap.py --data ../snapshots/data --runs 20261115T214836                  --tag heldout --out ../results
Scenarios (SIMULATED, as in stress_replay.py): baseline, congestion (x5), outage (p=0.15, 90% probe detection).
"""
import argparse, json, math, os, random, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay_selection as rp
from chain_selector import TIER_WEIGHTS

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="../snapshots/data"); ap.add_argument("--runs", required=True); ap.add_argument("--tag", required=True)
ap.add_argument("--out", default="../results"); ap.add_argument("--per", type=int, default=300); ap.add_argument("--B", type=int, default=5000)
ap.add_argument("--seed", type=int, default=11)
a = ap.parse_args()
rp.CLEAN_RUNS = set(a.runs.split(","))


def make_scenario(name):
    base_draw = rp.__dict__["_orig_draw"] if "_orig_draw" in rp.__dict__ else rp.draw
    rp._orig_draw = base_draw

    def draw(rng, snaps, prices, pair):
        req = base_draw(rng, snaps, prices, pair)
        for n, c in req["chains"].items():
            c["down"] = False; c["detected"] = False
            if name == "congestion" and rng.random() < 0.3:
                mult = 5.0; surge = 1 + rng.uniform(0, mult / 2)
                c["fee_q"] *= mult; c["fee_r"] *= mult * surge; c["t_q"] *= 1 + (mult - 1) * .1; c["t_r"] *= 1 + (mult - 1) * .1 * surge
            if name == "outage" and rng.random() < 0.15:
                c["down"] = True; c["detected"] = rng.random() < 0.9
        return req

    def ok(c): return c["supported"] and c["bal"] and not c.get("down", False)

    def cands(req):
        return [rp.ChainCandidate(n, req["chains"][n]["fee_q"], req["chains"][n]["t_q"], 1.0,
                                  not (req["chains"][n].get("down") and req["chains"][n].get("detected")),
                                  req["chains"][n]["supported"], req["chains"][n]["bal"]) for n in rp.ORDER]
    rp.draw, rp.ok, rp.cands = draw, ok, cands


def feas(q): return [c for c in rp.cands(q) if c.asset_supported and c.balance_ok and c.healthy]
def mm(v):
    lo, hi = min(v), max(v); return [0.0 if hi - lo < 1e-12 else (x - lo) / (hi - lo) for x in v]
def wsum(q, g):
    F = feas(q) or [c for c in rp.cands(q) if c.asset_supported]; w = TIER_WEIGHTS[q["tier"]]
    s = [w[0] * f + w[1] * t for f, t in zip(mm([c.fee_usd for c in F]), mm([c.conf_time_s for c in F]))]
    return F[s.index(min(s))].network
def topsis(q, g):
    F = feas(q) or [c for c in rp.cands(q) if c.asset_supported]; w = TIER_WEIGHTS[q["tier"]][:2]
    M = [[c.fee_usd, c.conf_time_s] for c in F]; nrm = [math.sqrt(sum(r[j] ** 2 for r in M)) or 1 for j in range(2)]
    V = [[w[j] * r[j] / nrm[j] for j in range(2)] for r in M]; best = [min(r[j] for r in V) for j in range(2)]; worst = [max(r[j] for r in V) for j in range(2)]
    d = lambda r, p: math.sqrt(sum((r[j] - p[j]) ** 2 for j in range(2)))
    cl = [d(r, worst) / ((d(r, best) + d(r, worst)) or 1) for r in V]
    return F[cl.index(max(cl))].network

KEEP = ["Fastest + constraints", "Equal-weight + constraints", "ICS-LN (log-minmax, initial)", "ICS-M (materiality, final)"]
STRATS = [(n, f) for n, f in rp.S if n in KEEP] + [("Weighted-sum + constr. (min-max)", wsum), ("TOPSIS + constr.", topsis)]

snaps, prices = rp.load(a.data); pairs = rp.build_pairs(snaps, prices)
print("tag", a.tag, "cycles", len(snaps), "pairs", len(pairs))
out = {"tag": a.tag, "runs": sorted(rp.CLEAN_RUNS), "pairs": len(pairs), "per_pair": a.per, "B": a.B, "scenarios": {}}
for scen in ("baseline", "congestion", "outage"):
    make_scenario(scen); out["scenarios"][scen] = {}
    for CK in (25.0, 100.0):
        rng = random.Random(a.seed); per_pair = {n: [] for n, _ in STRATS}
        for pair in pairs:
            acc = {n: [] for n, _ in STRATS}
            for _ in range(a.per):
                req = rp.draw(rng, snaps, prices, pair)
                fe = [n for n in rp.ORDER if rp.ok(req["chains"][n])]
                if not fe: continue
                d0 = min(rp.D(req, n, CK)[0] for n in fe)
                for name, fn in STRATS: acc[name].append(rp.D(req, fn(req, rng), CK)[0] - d0)
            for name in acc: per_pair[name].append(st.mean(acc[name]) if acc[name] else 0.0)
        res = {}; brng = random.Random(1234); P = len(pairs)
        for name, _ in STRATS:
            if name.startswith("ICS-M"): continue
            diff = [per_pair["ICS-M (materiality, final)"][i] - per_pair[name][i] for i in range(P)]
            boots = sorted(st.mean(diff[brng.randrange(P)] for _ in range(P)) for _ in range(a.B))
            res[name] = dict(mean_diff=st.mean(diff), ci95=[boots[int(.025 * a.B)], boots[int(.975 * a.B) - 1]],
                             pairs_icsm_better=sum(d < 0 for d in diff), pairs=P,
                             competitor_mean=st.mean(per_pair[name]), icsm_mean=st.mean(per_pair["ICS-M (materiality, final)"]))
        out["scenarios"][scen][str(CK)] = res
        print(f"[{scen} CK={CK}]")
        for n, r in res.items():
            print(f"   ICS-M - {n:34s} diff={r['mean_diff']:+.4f}  95%CI [{r['ci95'][0]:+.4f},{r['ci95'][1]:+.4f}]  ICS-M better on {r['pairs_icsm_better']}/{r['pairs']} pairs")
os.makedirs(a.out, exist_ok=True)
json.dump(out, open(os.path.join(a.out, f"pair_bootstrap_{a.tag}.json"), "w"), indent=1)
