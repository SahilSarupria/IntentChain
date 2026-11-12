#!/usr/bin/env python3
"""
IntentChain -- Step 2: live LLM + guard evaluation.

For every prompt in prompts.csv it (1) calls your REAL app.llm.intent_parser.parse_intent
(Gemini 2.5 Flash), (2) applies the repo's missing-field rule, (3) builds the unsigned tx
offline (same selectors / scaling / chainId as tx_builder.py + erc20.py), and (4) runs the
ITCG guard. Nothing is sent on-chain and no wallet is needed.

Setup (once), from your IntentChain repo root:
    pip install -r requirements.txt            # google-genai + web3 etc., if not installed already
    copy tx_guard.py to app/services/tx_guard.py   (or keep it next to this script)
    put GEMINI_API_KEY=... in .env  (or set it as an environment variable)
Run (repo root is --repo; keep prompts.csv next to this script):
    python run_live_eval.py --repo . --limit 5          # 5-prompt smoke test first
    python run_live_eval.py --repo .                    # full run, ~157 prompts, ~20 min
    python run_live_eval.py --repo . --mock             # no API key: pipeline self-test only
Outputs (folder out/): results.jsonl, results.csv, run_meta.json. Safe to Ctrl+C and re-run:
finished (prompt, repeat) pairs are skipped. Use --repeats 2 only if your API quota allows.
"""
import argparse, csv, importlib.util, json, os, platform, re, sys, time
from datetime import datetime, timezone
from decimal import Decimal

CONTACTS = ["0x7eeb8a1ec9332a30a983694d1b428f3e50e86c2c", "0x5f2ca9635310f88053fc3fcdceab11b9d49fc08f", "0xbf54a2989d5e52e2a3c6439edc106b45dfb81df8", "0x2a1804225f969f1a6e7c618c8fc9d640075fcc2e", "0xfb086dfede1589caba552abb45deb93f1f4aefbb", "0x6e04f47729fa283962c00eedd19f5359fc591ce4"]
CHAIN_IDS = {"ethereum": 1, "sepolia": 11155111, "polygon": 137, "arbitrum": 42161, "optimism": 10, "bsc": 56}
SENTINEL = {"none", "", "null", "unknown", "n/a", "0"}
REQUIRED = {  # copied from app/api/routes.py (REQUIRED_FIELDS_BY_ACTION)
    "transfer": ["action", "amount", "recipient", "network", "priority"], "send": ["action", "amount", "recipient", "network", "priority"],
    "bridge": ["action", "amount", "recipient", "network", "priority"],
    "transfer_token": ["action", "token", "amount", "recipient", "network", "priority"], "send_token": ["action", "token", "amount", "recipient", "network", "priority"],
    "approve_token": ["action", "token", "amount", "spender", "network", "priority"], "check_balance": ["action", "network"], "get_history": ["action", "network"],
    "register_product": ["action", "product_id", "name", "origin", "network"], "log_checkpoint": ["action", "product_id", "location", "status", "network"],
    "verify_product": ["action", "product_id", "network"], "swap": ["action", "token", "amount", "network"], "general_question": ["action"], "deploy_contract": ["action", "network"]}
ACTION_CLASS = {"transfer": "native", "send": "native", "bridge": "native", "transfer_token": "erc20", "send_token": "erc20", "approve_token": "approve",
                "check_balance": "balance", "get_history": "history", "general_question": "knowledge", "register_product": "register",
                "log_checkpoint": "checkpoint", "verify_product": "verify", "swap": "swap", "deploy_contract": "deploy"}
SEL_TRANSFER, SEL_APPROVE = "0xa9059cbb", "0x095ea8ba"
ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_missing(v): return v is None or str(v).strip().lower() in SENTINEL


def detect_missing(p):
    out = []
    for f in REQUIRED.get(str(p.get("action", "")).lower(), ["action", "amount", "recipient", "network", "priority"]):
        if f == "amount":
            try:
                if float(p.get("amount", 0)) <= 0: out.append(f)
            except (TypeError, ValueError): out.append(f)
        elif is_missing(p.get(f)): out.append(f)
    return out


def enc(sel, target, raw): return sel + target[2:].lower().rjust(64, "0") + hex(raw)[2:].rjust(64, "0")


def build_tx(action, token, amount, target, network, tokens):
    a = Decimal(str(amount)); tx = {"from": "0x" + "11" * 20, "chainId": hex(CHAIN_IDS[network])}
    if action in ("transfer", "send", "bridge"):
        tx.update(to=target, value=hex(int(a * 10 ** 18))); return tx
    meta = tokens.get(network, {}).get(str(token).upper())
    if not meta: raise ValueError(f"token {token} not in registry for {network}")
    raw = int(a * Decimal(10) ** meta["decimals"])
    tx.update(to=meta["address"], value="0x0", data=enc(SEL_TRANSFER if action in ("transfer_token", "send_token") else SEL_APPROVE, target, raw))
    return tx


def mock_parse(prompt, history=None):   # self-test stub: NOT the LLM
    addrs = re.findall(r"0x[a-fA-F0-9]{40}", prompt); low = prompt.lower()
    nums = re.findall(r"(?<![\w.])\d+(?:\.\d+)?", re.sub(r"0x[a-fA-F0-9]+", " ", prompt))
    tok = next((t for t in ("USDC", "USDT", "DAI", "WETH", "LINK") if t.lower() in low), "ETH")
    net = next((n for n in ("sepolia", "ethereum", "polygon", "arbitrum", "optimism", "bsc") if n in low), "none")
    act = "approve_token" if re.search(r"approve|allow|authori", low) else ("transfer_token" if tok != "ETH" else "transfer")
    return dict(action=act, token=tok, amount=float(nums[0]) if nums else 0, recipient=addrs[-1] if addrs else "none", spender=addrs[-1] if addrs else "none",
                network=net, priority="normal", gas_mode="none", product_id="none", name="none", origin="none", location="none", status="none", temperature_c=0, answer="none")


def load_guard(repo):
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "tx_guard.py"), os.path.join(repo, "app", "services", "tx_guard.py")):
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("tx_guard", p); m = importlib.util.module_from_spec(spec); sys.modules["tx_guard"] = m; spec.loader.exec_module(m); return m
    sys.exit("tx_guard.py not found: put it next to this script or in <repo>/app/services/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="."); ap.add_argument("--prompts", default="prompts.csv"); ap.add_argument("--out", default="out")
    ap.add_argument("--repeats", type=int, default=1); ap.add_argument("--sleep", type=float, default=6.0, help="seconds between API calls (free-tier rate limit)")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--mock", action="store_true"); ap.add_argument("--default-network", default="sepolia")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo); sys.path.insert(0, repo)
    guard = load_guard(repo)
    tokens = json.load(open(os.path.join(repo, "app", "config", "tokens.json"), encoding="utf-8"))
    if a.mock: parse = mock_parse
    else:
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.path.exists(os.path.join(repo, ".env"))):
            sys.exit("No GEMINI_API_KEY found (env var or <repo>/.env).")
        from app.llm.intent_parser import parse_intent as parse
    os.makedirs(a.out, exist_ok=True)
    rows = list(csv.DictReader(open(a.prompts, encoding="utf-8")))
    if a.limit: rows = rows[:a.limit]
    rp = os.path.join(a.out, "results.jsonl")
    done = set()
    if os.path.exists(rp):
        for line in open(rp, encoding="utf-8"):
            try: j = json.loads(line); done.add((j["id"], j["rep"]))
            except Exception: pass
    json.dump(dict(start_utc=datetime.now(timezone.utc).isoformat(), model="gemini-2.5-flash (hardcoded in intent_parser.py)", mock=a.mock, python=sys.version.split()[0],
                   platform=platform.platform(), n_prompts=len(rows), repeats=a.repeats, sleep=a.sleep), open(os.path.join(a.out, "run_meta.json"), "w"), indent=1)
    f = open(rp, "a", encoding="utf-8")
    total = len(rows) * a.repeats; k = 0
    for rep in range(a.repeats):
        for r in rows:
            k += 1
            if (r["id"], rep) in done: continue
            rec = dict(id=r["id"], rep=rep, category=r["category"], prompt=r["prompt"], ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       expected={x: r[x] for x in ("exp_action", "exp_token", "exp_amount", "exp_target", "exp_network", "exp_priority", "exp_missing", "adversarial", "expect_flag_or_missing")})
            parsed, err, retries, lat = None, None, 0, None
            while True:
                t0 = time.perf_counter()
                try:
                    parsed = parse(r["prompt"]); lat = round((time.perf_counter() - t0) * 1000, 1); break
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    if retries < 5 and re.search(r"429|RESOURCE_EXHAUSTED|503|UNAVAILABLE|500|timeout|deadline", msg, re.I):
                        retries += 1; w = 15 * 2 ** (retries - 1); print(f"   rate/availability error, waiting {w}s ({retries}/5)"); time.sleep(w); continue
                    err = msg[:400]; lat = round((time.perf_counter() - t0) * 1000, 1); break
            rec.update(parse_error=err, parse_latency_ms=lat, retries=retries, parsed=parsed)
            if parsed is not None:
                act = str(parsed.get("action", "")).lower(); rec["action_class"] = ACTION_CLASS.get(act, "other")
                missing = [] if act == "general_question" else detect_missing(parsed)
                rec["missing_detected"] = missing
                if act in ("transfer", "send", "bridge", "transfer_token", "send_token", "approve_token"):
                    non_net = [m for m in missing if m != "network"]
                    if non_net: rec["stage"] = "stopped_missing_fields"
                    else:
                        net = str(parsed.get("network", "none")).lower()
                        rec["network_filled"] = net not in CHAIN_IDS
                        net = net if net in CHAIN_IDS else a.default_network
                        tgt = str(parsed.get("spender") if act == "approve_token" else parsed.get("recipient"))
                        rec["resolved_network"] = net; rec["target"] = tgt
                        try:
                            if not ADDR_RE.match(tgt): raise ValueError("target is not a valid 0x address")
                            tx = build_tx(act, parsed.get("token"), parsed.get("amount"), tgt, net, tokens); rec["tx"] = tx
                            intent = dict(action=act, token=parsed.get("token"), amount=parsed.get("amount"), recipient=parsed.get("recipient"), spender=parsed.get("spender"), network=net)
                            g = guard.verify(r["prompt"], intent, tx, known_tokens=tokens.get(net, {}), chain_ids=CHAIN_IDS, address_book=CONTACTS)
                            rec["stage"] = "guarded"; rec["guard_decision"] = g.decision
                            rec["guard_findings"] = [dict(check=x.check, code=x.code, severity=x.severity) for x in g.findings]
                        except Exception as e:
                            rec["stage"] = "build_error"; rec["build_error"] = str(e)[:200]
                else: rec["stage"] = "non_transaction"
            else: rec["stage"] = "parse_failed"
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n"); f.flush()
            print(f"[{k}/{total}] {r['id']} {rec.get('action_class','-'):9s} {rec['stage']:22s} {rec.get('guard_decision','')}  {lat}ms")
            if not a.mock: time.sleep(a.sleep)
    f.close()
    # flat CSV for quick viewing
    with open(os.path.join(a.out, "results.csv"), "w", newline="", encoding="utf-8") as cf:
        cols = ["id", "rep", "category", "prompt", "parsed_action", "parsed_token", "parsed_amount", "parsed_target", "parsed_network", "parsed_priority", "parsed_gas_mode",
                "missing_detected", "stage", "guard_decision", "guard_codes", "parse_latency_ms", "retries", "parse_error", "build_error"]
        w = csv.DictWriter(cf, cols); w.writeheader()
        for line in open(rp, encoding="utf-8"):
            j = json.loads(line); p = j.get("parsed") or {}
            w.writerow(dict(id=j["id"], rep=j["rep"], category=j["category"], prompt=j["prompt"].replace("\n", " "), parsed_action=p.get("action"), parsed_token=p.get("token"),
                            parsed_amount=p.get("amount"), parsed_target=p.get("spender") if p.get("action") == "approve_token" else p.get("recipient"), parsed_network=p.get("network"),
                            parsed_priority=p.get("priority"), parsed_gas_mode=p.get("gas_mode"), missing_detected=";".join(j.get("missing_detected", [])), stage=j.get("stage"),
                            guard_decision=j.get("guard_decision"), guard_codes=";".join(x["code"] for x in j.get("guard_findings", [])), parse_latency_ms=j.get("parse_latency_ms"),
                            retries=j.get("retries"), parse_error=j.get("parse_error"), build_error=j.get("build_error")))
    print(f"\ndone. Send me: {a.out}/results.jsonl  {a.out}/results.csv  {a.out}/run_meta.json")


if __name__ == "__main__":
    main()
