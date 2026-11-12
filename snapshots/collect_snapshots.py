#!/usr/bin/env python3
"""
IntentChain -- Step 1: live chain-condition collector.

Samples 5 EVM networks every --interval seconds for --minutes minutes and writes
data/snapshots.csv (one row per network per cycle), data/prices.csv and
data/meta.json. Pure standard library: no pip install needed (Python 3.9+).

    python collect_snapshots.py --minutes 90 --interval 60

Stop early any time with Ctrl+C: everything already collected is kept.
Re-running appends to the same files (a new run_id is recorded in meta.json).
Override an RPC with env vars, e.g.  set RPC_URL_ETHEREUM=https://...  (optional).
"""
import argparse, csv, json, os, platform, sys, threading, time, urllib.request, urllib.error
from datetime import datetime, timezone

NETWORKS = {
    # default_rpc = same public endpoints IntentChain's networks.py uses
    "ethereum": dict(rpc="https://eth.llamarpc.com",          env="RPC_URL_ETHEREUM", span=20,  native="ETH", usdc="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
    "arbitrum": dict(rpc="https://arb1.arbitrum.io/rpc",      env="RPC_URL_ARBITRUM", span=400, native="ETH", usdc="0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
    "optimism": dict(rpc="https://mainnet.optimism.io",       env="RPC_URL_OPTIMISM", span=60,  native="ETH", usdc="0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"),
    "polygon":  dict(rpc="https://polygon-rpc.com",           env="RPC_URL_POLYGON",  span=60,  native="POL", usdc="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
    "bsc":      dict(rpc="https://bsc-dataseed.binance.org",  env="RPC_URL_BSC",      span=60,  native="BNB", usdc="0x55d398326f99059fF775485246999027B3197955"),  # USDT on BSC
}
DEAD = "0x000000000000000000000000000000000000dEaD"
ONE = "0x0000000000000000000000000000000000000001"
OP_ORACLE = "0x420000000000000000000000000000000000000F"   # OP-stack GasPriceOracle
FIELDS = ["run_id", "ts_utc", "cycle", "network", "rpc_host", "calls_ok", "calls_total", "ok", "err",
          "lat_ms_blocknumber", "lat_ms_cycle", "block_number", "block_ts", "base_fee_wei", "gas_used_ratio",
          "avg_block_time_s", "gas_price_wei", "tip_p10_wei", "tip_p50_wei", "tip_p90_wei",
          "est_gas_native", "est_gas_erc20", "op_l1_fee_wei"]
PRICE_FIELDS = ["run_id", "ts_utc", "cycle", "symbol", "usd", "source"]


def rpc(url, method, params, timeout=10):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "intentchain-collector/1.0"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode())
    ms = (time.perf_counter() - t0) * 1000
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"], ms


def hx(v): return int(v, 16) if isinstance(v, str) and v.startswith("0x") else None


def sample(name, cfg, run_id, cycle):
    url = os.getenv(cfg["env"]) or cfg["rpc"]
    row = dict(run_id=run_id, ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), cycle=cycle, network=name,
               rpc_host=url.split("//")[1].split("/")[0].split("?")[0], calls_ok=0, calls_total=0, err="")
    errs = []
    t_cycle = time.perf_counter()

    def call(key, method, params, parse=None, lat_key=None):
        row["calls_total"] += 1
        try:
            res, ms = rpc(url, method, params)
            row["calls_ok"] += 1
            if lat_key: row[lat_key] = round(ms, 1)
            return res
        except Exception as e:
            errs.append(f"{key}:{type(e).__name__}:{str(e)[:80]}")
            return None

    bn = call("blockNumber", "eth_blockNumber", [], lat_key="lat_ms_blocknumber")
    n = hx(bn)
    if n is not None:
        row["block_number"] = n
        latest = call("latest", "eth_getBlockByNumber", [hex(n), False])
        if latest:
            row["block_ts"] = hx(latest.get("timestamp"))
            row["base_fee_wei"] = hx(latest.get("baseFeePerGas"))
            gl, gu = hx(latest.get("gasLimit")), hx(latest.get("gasUsed"))
            if gl: row["gas_used_ratio"] = round(gu / gl, 4)
            old = call("old", "eth_getBlockByNumber", [hex(n - cfg["span"]), False])
            if old and row.get("block_ts"):
                row["avg_block_time_s"] = round((row["block_ts"] - hx(old["timestamp"])) / cfg["span"], 4)
        fh = call("feeHistory", "eth_feeHistory", [hex(20), "latest", [10, 50, 90]])
        if fh and fh.get("reward"):
            rw = [[hx(x) for x in r] for r in fh["reward"] if r]
            if rw:
                for i, k in enumerate(("tip_p10_wei", "tip_p50_wei", "tip_p90_wei")):
                    row[k] = int(sum(r[i] for r in rw) / len(rw))
    row["gas_price_wei"] = hx(call("gasPrice", "eth_gasPrice", []))
    row["est_gas_native"] = hx(call("estNative", "eth_estimateGas", [{"from": DEAD, "to": ONE, "value": "0x0"}]))
    data = "0xa9059cbb" + ONE[2:].rjust(64, "0") + "0" * 64          # transfer(0x..01, 0)
    row["est_gas_erc20"] = hx(call("estErc20", "eth_estimateGas", [{"from": DEAD, "to": cfg["usdc"], "data": data}]))
    if name == "optimism":   # L1 data fee for a ~110-byte payload (best effort; selector getL1Fee(bytes))
        payload = "00" * 110
        calldata = "0x49948e0e" + (32).to_bytes(32, "big").hex() + (110).to_bytes(32, "big").hex() + payload.ljust(((len(payload) + 63) // 64) * 64, "0")
        row["op_l1_fee_wei"] = hx(call("opL1Fee", "eth_call", [{"to": OP_ORACLE, "data": calldata}, "latest"]))
    row["lat_ms_cycle"] = round((time.perf_counter() - t_cycle) * 1000, 1)
    row["ok"] = int(row["calls_ok"] == row["calls_total"] and row["calls_total"] > 0)
    row["err"] = " | ".join(errs)[:300]
    return row


def fetch_prices():
    out = []
    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "intentchain-collector/1.0"}), timeout=10) as r:
            return json.loads(r.read().decode())
    try:
        j = get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum,polygon-ecosystem-token,binancecoin&vs_currencies=usd")
        for sym, k in (("ETH", "ethereum"), ("POL", "polygon-ecosystem-token"), ("BNB", "binancecoin")):
            if k in j: out.append((sym, j[k]["usd"], "coingecko"))
    except Exception:
        pass
    have = {s for s, _, _ in out}
    for sym, pair in (("ETH", "ETHUSDT"), ("POL", "POLUSDT"), ("BNB", "BNBUSDT")):
        if sym in have: continue
        try:
            out.append((sym, float(get(f"https://api.binance.com/api/v3/ticker/price?symbol={pair}")["price"]), "binance"))
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=90)
    ap.add_argument("--interval", type=float, default=60, help="seconds between cycles")
    ap.add_argument("--outdir", default="data")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    meta_path = os.path.join(a.outdir, "meta.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {"runs": []}
    meta["runs"].append(dict(run_id=run_id, start_utc=datetime.now(timezone.utc).isoformat(), minutes=a.minutes, interval_s=a.interval,
                             python=sys.version.split()[0], platform=platform.platform(),
                             rpcs={k: (os.getenv(v["env"]) and "custom(env)") or v["rpc"] for k, v in NETWORKS.items()}))
    json.dump(meta, open(meta_path, "w"), indent=1)

    sp, pp = os.path.join(a.outdir, "snapshots.csv"), os.path.join(a.outdir, "prices.csv")
    new_s, new_p = not os.path.exists(sp), not os.path.exists(pp)
    fs, fp = open(sp, "a", newline=""), open(pp, "a", newline="")
    ws, wp = csv.DictWriter(fs, FIELDS), csv.DictWriter(fp, PRICE_FIELDS)
    if new_s: ws.writeheader()
    if new_p: wp.writeheader()

    end = time.time() + a.minutes * 60
    cycle = 0
    print(f"run {run_id}: {a.minutes} min, every {a.interval}s -> {a.outdir}/  (Ctrl+C to stop early)")
    try:
        while time.time() < end:
            t0 = time.time(); cycle += 1
            rows = {}
            def work(n, c): rows[n] = sample(n, c, run_id, cycle)
            th = [threading.Thread(target=work, args=(n, c)) for n, c in NETWORKS.items()]
            [t.start() for t in th]; [t.join() for t in th]
            for n in NETWORKS: ws.writerow({k: rows[n].get(k, "") for k in FIELDS})
            fs.flush()
            if cycle % 5 == 1:   # prices every 5 cycles
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for sym, usd, src in fetch_prices(): wp.writerow(dict(run_id=run_id, ts_utc=ts, cycle=cycle, symbol=sym, usd=usd, source=src))
                fp.flush()
            ok = sum(r["ok"] for r in rows.values())
            print(f"[{datetime.now().strftime('%H:%M:%S')}] cycle {cycle}: {ok}/5 networks fully OK  " +
                  " ".join(f"{n}={'ok' if r['ok'] else 'FAIL'}" for n, r in rows.items()), flush=True)
            time.sleep(max(0, a.interval - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopped early; data kept.")
    finally:
        fs.close(); fp.close()
        print(f"done. Send me: {sp}, {pp}, {meta_path}")


if __name__ == "__main__":
    main()
