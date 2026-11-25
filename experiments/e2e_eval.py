"""
End-to-end evaluation through the RUNNING backend (faculty point 4). Run backend first: uvicorn app.main:app --port 8000
  python e2e_eval.py --prompts prompts.csv --api http://127.0.0.1:8000 --wallet 0xYourSepoliaWallet --out e2e_results.csv --max 15
  python e2e_eval.py --scale --api http://127.0.0.1:8000 --wallet 0x... --intent-json '{"action":"send","token":"ETH","amount":0.001,"recipient":"0x...","network":"sepolia","priority":"normal"}'
Per prompt: parse -> (fill missing priority) -> build-tx. Records parse accuracy, parse/build/guard latency, gas fields, guard decision.
--scale: calls /build-tx (NO LLM, no quota) at concurrency 1,2,4,8,16 and reports throughput + p50/p95 latency.
"""
import argparse, csv, json, time, threading, urllib.request, statistics as st
def post(url, body, timeout=60):
    r = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}); t = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f: return f.status, json.loads(f.read()), (time.perf_counter() - t) * 1000
    except urllib.error.HTTPError as e: return e.code, {"detail": e.read().decode()[:200]}, (time.perf_counter() - t) * 1000
    except Exception as e: return 0, {"detail": str(e)[:200]}, (time.perf_counter() - t) * 1000
ap = argparse.ArgumentParser(); ap.add_argument("--api", default="http://127.0.0.1:8000"); ap.add_argument("--wallet", required=True); ap.add_argument("--prompts", default="prompts.csv")
ap.add_argument("--out", default="e2e_results.csv"); ap.add_argument("--max", type=int, default=15); ap.add_argument("--skip", type=int, default=0); ap.add_argument("--scale", action="store_true"); ap.add_argument("--intent-json"); ap.add_argument("--n", type=int, default=100)
a = ap.parse_args()
if a.scale:
    intent = json.loads(a.intent_json); res = {}
    for conc in (1, 2, 4, 8, 16):
        lat, errs, lock = [], [0], threading.Lock(); per = max(1, a.n // conc)
        def w():
            for _ in range(per):
                s, d, ms = post(a.api + "/build-tx", {"intent": intent, "from_address": a.wallet, "prompt": ""})
                with lock:
                    lat.append(ms); errs[0] += s != 200
        t0 = time.perf_counter(); th = [threading.Thread(target=w) for _ in range(conc)]; [t.start() for t in th]; [t.join() for t in th]; dur = time.perf_counter() - t0
        lat.sort(); res[conc] = dict(req=len(lat), errors=errs[0], rps=len(lat) / dur, p50=lat[len(lat) // 2], p95=lat[int(.95 * len(lat))])
        print(f"conc={conc:2d} req={len(lat)} err={errs[0]} {len(lat) / dur:.1f} req/s p50={lat[len(lat) // 2]:.0f}ms p95={lat[int(.95 * len(lat))]:.0f}ms")
    json.dump(res, open("scale_results.json", "w"), indent=1); raise SystemExit
rows = list(csv.DictReader(open(a.prompts)))[a.skip:a.skip + a.max]; out = []
for r in rows:
    s, p, pm = post(a.api + "/parse-intent", {"prompt": r["prompt"], "history": []})
    row = dict(id=r["id"], category=r["category"], parse_status=s, parse_ms=round(pm), build_status="", build_ms="", guard_ms="", guard="", gas_estimate="", tier="", correct="", note="")
    if s == 200:
        it = dict(p.get("parsed") or {}); 
        if str(it.get("priority", "none")).lower() in ("none", ""): it["priority"] = "normal"
        row["correct"] = int(str(it.get("action", "")).lower() in str(r["exp_action"]).lower() or str(r["exp_action"]).lower() in str(it.get("action", "")).lower()) if r["exp_action"] else ""
        if it.get("network") in (None, "", "none", "null") and "sepolia" in r["prompt"].lower(): it["network"] = "sepolia"
        s2, b, bm = post(a.api + "/build-tx", {"intent": it, "from_address": a.wallet, "prompt": r["prompt"]})
        row.update(build_status=s2, build_ms=round(bm), guard_ms=b.get("guard_ms", ""), guard=(b.get("guard") or {}).get("decision", ""), gas_estimate=(b.get("strategy") or {}).get("gas_estimate", ""), tier=(b.get("strategy") or {}).get("selected_tier", ""))
        if s2 != 200: row["note"] = str(b.get("detail", ""))[:100]
    else: row["note"] = str(p.get("detail", ""))[:100]
    out.append(row); print(row["id"], row["parse_status"], row["build_status"], row["guard"], row["parse_ms"], row["build_ms"])
w = csv.DictWriter(open(a.out, "a" if a.skip else "w", newline=""), fieldnames=list(out[0])); (not a.skip) and w.writeheader(); w.writerows(out)
ok = [r for r in out if r["build_status"] == 200]; print(f"\nbuilt {len(ok)}/{len(out)}; parse p50 {st.median(r['parse_ms'] for r in out):.0f}ms; build p50 {st.median(r['build_ms'] for r in ok) if ok else 0:.0f}ms; guard decisions " + str({d: sum(1 for r in ok if r['guard'] == d) for d in set(r['guard'] for r in ok)}))
