"""
Experiment 1 (real data) -- chain selection replayed on LIVE snapshots.
Inputs: snapshots/data/{snapshots,prices}.csv produced by collect_snapshots.py.
Quotes come from snapshot t; realised cost/latency from snapshot t+1 (60 s later), so fee
drift is MEASURED. MODELLED (not measured): requests, wallet holdings, user latent weights,
and block-count-to-inclusion (confirmation = n_blocks(tier) x measured block time).
Usage: python replay_selection.py --data ../snapshots/data --out ../results
"""
import argparse, csv, json, math, os, random, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain_selector import ChainCandidate, select_chain, EQUAL_WEIGHTS

ORDER = ["ethereum", "arbitrum", "optimism", "polygon", "bsc"]
NATIVE = {"ethereum": "ETH", "arbitrum": "ETH", "optimism": "ETH", "polygon": "POL", "bsc": "BNB"}
CLEAN_RUNS = {"20261112T140034", "20261114T234312"}
TIERS = {"cheapest": dict(pi="tip_p10_wei", mult=1.0, blocks=3), "standard": dict(pi="tip_p50_wei", mult=1.1, blocks=2), "fastest": dict(pi="tip_p90_wei", mult=1.25, blocks=1)}
ASSETS = [("ETH", .20, "native"), ("POL", .05, "native"), ("BNB", .05, "native"), ("USDC", .45, "token"), ("OTHER", .25, "token")]
PRIORITIES = [("cheapest", .35), ("standard", .35), ("fastest", .30)]
W_C = {"cheapest": (.6, .9), "standard": (.35, .65), "fastest": (.1, .4)}
FAIL_D = 1.5


def load(data):
    snaps = {}
    for r in csv.DictReader(open(os.path.join(data, "snapshots.csv"))):
        if r["run_id"] in CLEAN_RUNS: snaps.setdefault((r["run_id"], int(r["cycle"])), {})[r["network"]] = r
    prices = sorted([(r["ts_utc"], r["symbol"], float(r["usd"])) for r in csv.DictReader(open(os.path.join(data, "prices.csv"))) if r["run_id"] in CLEAN_RUNS])
    return snaps, prices


def usd_price(prices, ts, sym):
    best = None
    for t, s, p in prices:
        if s == sym and t <= ts: best = p
    return best if best is not None else next(p for t, s, p in prices if s == sym)


def quote(row, tier, is_token, price):
    f = lambda k: float(row[k]) if row[k] not in ("", None) else 0.0
    base, gp, tip = f("base_fee_wei"), f("gas_price_wei"), f(TIERS[tier]["pi"]) * TIERS[tier]["mult"]
    unit = base + tip if base > 0 else gp          # BSC reports no base fee -> gasPrice
    gas = f("est_gas_erc20") if is_token else f("est_gas_native")
    wei = gas * unit + (f("op_l1_fee_wei") if row["network"] == "optimism" else 0.0)
    return wei * 1e-18 * price, TIERS[tier]["blocks"] * f("avg_block_time_s")


def build_pairs(snaps, prices):
    out = []
    keys = sorted(snaps)
    for k in keys:
        nxt = (k[0], k[1] + 1)
        if nxt in snaps and all(n in snaps[k] and n in snaps[nxt] for n in ORDER): out.append((k, nxt))
    return out


def draw(rng, snaps, prices, pair):
    a = rng.random(); acc = 0
    for sym, p, kind in ASSETS:
        acc += p
        if a <= acc: break
    tier = rng.choices([t for t, _ in PRIORITIES], [p for _, p in PRIORITIES])[0]
    amount = min(50000, max(1.0, math.exp(rng.gauss(math.log(100), 1.5))))
    w_c = rng.uniform(*W_C[tier]); is_token = kind == "token"
    other_ok = [rng.random() < .6 for _ in ORDER]
    if sym == "OTHER" and not any(other_ok): other_ok[rng.randrange(5)] = True
    chains = {}
    for i, n in enumerate(ORDER):
        supported = (sym == "USDC" or (sym == "OTHER" and other_ok[i])) if is_token else NATIVE[n] == sym
        holds = supported and rng.random() < .55
        r0, r1 = snaps[pair[0]][n], snaps[pair[1]][n]
        ts0, ts1 = r0["ts_utc"], r1["ts_utc"]
        fq, tq = quote(r0, tier, is_token, usd_price(prices, ts0, NATIVE[n]))
        fr, tr = quote(r1, tier, is_token, usd_price(prices, ts1, NATIVE[n]))
        chains[n] = dict(supported=supported, bal=holds and rng.random() < .85, fee_q=fq, t_q=tq, fee_r=fr, t_r=tr)
    return dict(tier=tier, amount=amount, w_c=w_c, chains=chains)


def ok(c): return c["supported"] and c["bal"]


def D(req, n, CK):
    c = req["chains"][n]
    if not ok(c): return FAIL_D, False
    ct = min(1.0, math.log10(1 + CK * c["fee_r"] / req["amount"] * 100) / 2)
    tt = min(1.0, math.log10(1 + c["t_r"]) / math.log10(601))
    return req["w_c"] * ct + (1 - req["w_c"]) * tt, True


def cands(req): return [ChainCandidate(n, req["chains"][n]["fee_q"], req["chains"][n]["t_q"], 1.0, True, req["chains"][n]["supported"], req["chains"][n]["bal"]) for n in ORDER]
def aa(req): return [n for n in ORDER if req["chains"][n]["supported"]]
def ics(req, **kw): r = select_chain(cands(req), kw.pop("tier", req["tier"]), req["amount"], **kw); return r.network or min(aa(req), key=lambda n: req["chains"][n]["fee_q"])
S = [("Wallet-active (manual)", lambda q, g: g.choice(aa(q))),
     ("Fixed Ethereum", lambda q, g: "ethereum" if "ethereum" in aa(q) else aa(q)[0]),
     ("Cheapest-fee (naive)", lambda q, g: min(aa(q), key=lambda n: q["chains"][n]["fee_q"])),
     ("Fastest (naive)", lambda q, g: min(aa(q), key=lambda n: q["chains"][n]["t_q"])),
     ("Cheapest + constraints", lambda q, g: ics(q, tier="cheapest", weights=(1, 0, 0), use_value_aware=False)),
     ("Fastest + constraints", lambda q, g: ics(q, tier="fastest", weights=(0, 1, 0), use_value_aware=False)),
     ("Equal-weight + constraints", lambda q, g: ics(q, weights=EQUAL_WEIGHTS, use_value_aware=False)),
     ("ICS-LN (log-minmax, initial)", lambda q, g: ics(q)),
     ("ICS - no value-aware", lambda q, g: ics(q, use_value_aware=False)),
     ("ICS-M (materiality, final)", lambda q, g: ics(q, normalization="materiality"))]


def run(seed, snaps, prices, pairs, per_pair, CK):
    rng = random.Random(seed); mk = lambda: dict(D=[], reg=[], fail=0, fee=[], fp=[], lat=[], tf={}, tl={}, sm_fp=[], sm_lat=[], sm_reg=[]); acc = {n: mk() for n, _ in S}; acc["Oracle"] = mk()
    N = 0
    for pair in pairs:
        for _ in range(per_pair):
            req = draw(rng, snaps, prices, pair)
            feas = [n for n in ORDER if ok(req["chains"][n])]
            if not feas: continue
            N += 1; best = min(feas, key=lambda n: D(req, n, CK)[0]); d0 = D(req, best, CK)[0]
            for name, fn in S + [("Oracle", None)]:
                n = best if fn is None else fn(req, rng); d, good = D(req, n, CK); a = acc[name]
                a["D"].append(d); a["reg"].append(d - d0)
                if req["amount"] < 10: a["sm_reg"].append(d - d0)
                if good:
                    if req["amount"] < 10: a["sm_fp"].append(100 * req["chains"][n]["fee_r"] / req["amount"]); a["sm_lat"].append(req["chains"][n]["t_r"])
                    c = req["chains"][n]; a["fee"].append(c["fee_r"]); a["fp"].append(100 * c["fee_r"] / req["amount"]); a["lat"].append(c["t_r"])
                    a["tf"].setdefault(req["tier"], []).append(c["fee_r"]); a["tl"].setdefault(req["tier"], []).append(c["t_r"])
                else: a["fail"] += 1
    res = {}
    for name, a in acc.items():
        res[name] = dict(D=st.mean(a["D"]), regret=st.mean(a["reg"]), fail=100 * a["fail"] / N, fee=st.mean(a["fee"]), fee_pct_med=st.median(a["fp"]), lat=st.mean(a["lat"]), small_fee_pct=st.mean(a["sm_fp"]), small_lat=st.mean(a["sm_lat"]), small_regret=st.mean(a["sm_reg"]),
                         **{f"fee_{t}": st.mean(v) for t, v in a["tf"].items()}, **{f"lat_{t}": st.mean(v) for t, v in a["tl"].items()})
    return res, N


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="../snapshots/data"); ap.add_argument("--out", default="../results"); a = ap.parse_args()
    snaps, prices = load(a.data); pairs = build_pairs(snaps, prices)
    print("clean cycles", len(snaps), "consecutive pairs", len(pairs))
    SEEDS = [11, 22, 33, 44, 55]; PER = 300; out = {"pairs": len(pairs), "per_pair": PER, "seeds": SEEDS, "by_CK": {}}
    for CK in (1.0, 25.0, 100.0):
        runs = [run(s, snaps, prices, pairs, PER, CK) for s in SEEDS]; ns = [r[1] for r in runs]
        summ = {n: {m: dict(mean=st.mean(r[0][n][m] for r in runs), sd=st.stdev(r[0][n][m] for r in runs)) for m in runs[0][0][n] if all(m in r[0][n] for r in runs)} for n in runs[0][0]}
        out["by_CK"][str(CK)] = summ; out["n_requests_per_seed"] = ns
        print(f"\n== CK={CK}  requests/seed ~{int(st.mean(ns))}")
        for n, m in summ.items():
            print(f"{n:28s} D={m['D']['mean']:.3f} regret={m['regret']['mean']:.4f}+-{m['regret']['sd']:.4f} fail%={m['fail']['mean']:.2f} fee$={m['fee']['mean']:.5f} fee%med={m['fee_pct_med']['mean']:.4f} lat={m['lat']['mean']:.1f} | <$10: fee%={m['small_fee_pct']['mean']:.3f} lat={m['small_lat']['mean']:.1f} reg={m['small_regret']['mean']:.4f} | lat c/s/f "
                  + "/".join(f"{m.get('lat_'+t, {'mean': float('nan')})['mean']:.1f}" for t in ("cheapest", "standard", "fastest")))
    # measured per-chain facts
    stats = {}
    for n in ORDER:
        rows = [snaps[k][n] for k in snaps]; rf = lambda k: [float(r[k]) for r in rows if r[k] not in ("", None)]
        fee_s = []; fee_e = []
        for k in snaps:
            r = snaps[k][n]; p = usd_price(prices, r["ts_utc"], NATIVE[n]); fee_s.append(quote(r, "standard", False, p)[0]); fee_e.append(quote(r, "standard", True, p)[0])
        drift = []
        for k0, k1 in pairs:
            f0 = quote(snaps[k0][n], "standard", True, usd_price(prices, snaps[k0][n]["ts_utc"], NATIVE[n]))[0]; f1 = quote(snaps[k1][n], "standard", True, usd_price(prices, snaps[k1][n]["ts_utc"], NATIVE[n]))[0]
            if f0 > 0: drift.append(abs(f1 - f0) / f0)
        calls_ok = sum(int(r["calls_ok"]) for r in rows); calls_tot = sum(int(r["calls_total"]) for r in rows)
        stats[n] = dict(samples=len(rows), fee_native_std_med=st.median(fee_s), fee_native_std_max=max(fee_s), fee_erc20_std_med=st.median(fee_e), fee_erc20_std_max=max(fee_e), block_time_s=st.median(rf("avg_block_time_s")),
                        rpc_lat_ms_med=st.median(rf("lat_ms_blocknumber")), call_success_pct=100 * calls_ok / calls_tot, block_call_failures=sum(1 for r in rows if r["block_number"] == ""),
                        drift_med_pct=100 * st.median(drift), drift_p95_pct=100 * sorted(drift)[int(.95 * len(drift))])
    out["chain_stats"] = stats
    json.dump(out, open(os.path.join(a.out, "replay_selection.json"), "w"), indent=1)
    for n, s in stats.items(): print(n, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()})
