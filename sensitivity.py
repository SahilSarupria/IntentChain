"""
Sensitivity / ablation of ICS-M's own design constants -- NEW script added with the revision.
Varies materiality rho (MAT_TAU) and time scale tau (MAT_TREF_S), and replaces tier weights by equal weights.
Same request generator, oracle and disutility as replay_selection.py; baseline scenario; single seed (11), 300 requests/pair.
Usage: python sensitivity.py --data ../snapshots/data --runs 20261115T214836 --tag heldout --out ../results
"""
import argparse, json, os, random, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay_selection as rp
import chain_selector as cs
ap = argparse.ArgumentParser(); ap.add_argument("--data", default="../snapshots/data"); ap.add_argument("--runs", required=True)
ap.add_argument("--tag", required=True); ap.add_argument("--out", default="../results"); ap.add_argument("--per", type=int, default=300); ap.add_argument("--seed", type=int, default=11)
a = ap.parse_args(); rp.CLEAN_RUNS = set(a.runs.split(","))
snaps, prices = rp.load(a.data); pairs = rp.build_pairs(snaps, prices)
CONF = [("rho=0.01, tau=60 (default)", dict(rho=.01, tau=60.)), ("rho=0.003", dict(rho=.003, tau=60.)), ("rho=0.03", dict(rho=.03, tau=60.)),
        ("tau=30", dict(rho=.01, tau=30.)), ("tau=120", dict(rho=.01, tau=120.)), ("equal weights (rho=0.01, tau=60)", dict(rho=.01, tau=60., eq=True))]
out = {"tag": a.tag, "pairs": len(pairs), "per_pair": a.per, "configs": {}}
for CK in (1.0, 25.0, 100.0):
    rng = random.Random(a.seed); reg = {n: [] for n, _ in CONF}
    for pair in pairs:
        for _ in range(a.per):
            req = rp.draw(rng, snaps, prices, pair); fe = [n for n in rp.ORDER if rp.ok(req["chains"][n])]
            if not fe: continue
            d0 = min(rp.D(req, n, CK)[0] for n in fe)
            for name, kw in CONF:
                cs.MAT_TAU, cs.MAT_TREF_S = kw["rho"], kw["tau"]
                r = cs.select_chain(rp.cands(req), req["tier"], req["amount"], normalization="materiality", **({"weights": cs.EQUAL_WEIGHTS} if kw.get("eq") else {}))
                reg[name].append(rp.D(req, r.network, CK)[0] - d0)
    cs.MAT_TAU, cs.MAT_TREF_S = .01, 60.
    out["configs"][str(CK)] = {n: st.mean(v) for n, v in reg.items()}
    print(f"[{a.tag} K={CK}]"); [print(f"   {n:34s} regret={st.mean(v):.4f}") for n, v in reg.items()]
os.makedirs(a.out, exist_ok=True); json.dump(out, open(os.path.join(a.out, f"sensitivity_{a.tag}.json"), "w"), indent=1)
