import streamlit as st
import requests
import time
import json
import streamlit.components.v1 as components

BACKEND_URL  = "http://127.0.0.1:8000"
PARSE_URL    = f"{BACKEND_URL}/parse-intent"
EXEC_URL     = f"{BACKEND_URL}/execute-intent-structured"

st.set_page_config(page_title="IntentChain Dashboard", layout="wide")

# ── session-state bootstrap ────────────────────────────────────────────────
for key, default in {
    "parsed_intent":    None,
    "missing_fields":   [],
    "show_dialog":      False,
    "ready_to_execute": False,
    "original_prompt":  "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── field metadata ─────────────────────────────────────────────────────────
FIELD_META = {
    "action":    {"label": "Action",           "hint": "e.g. transfer, send, swap",       "type": "select",  "options": ["transfer", "send", "swap", "bridge"]},
    "amount":    {"label": "Amount (ETH)",      "hint": "e.g. 0.002",                      "type": "number"},
    "recipient": {"label": "Recipient Address", "hint": "e.g. 0xABC123…",                  "type": "text"},
    "network":   {"label": "Network",           "hint": "e.g. sepolia, ethereum, polygon", "type": "select",  "options": ["sepolia", "ethereum", "polygon", "arbitrum", "optimism", "bsc"]},
    "priority":  {"label": "Priority",          "hint": "Speed vs. cost trade-off",        "type": "select",  "options": ["low_cost", "normal", "fast"]},
}

FIELD_ICONS = {"action": "⚡", "amount": "💰", "recipient": "📬", "network": "🌐", "priority": "🚀"}

# ── timeline renderer ──────────────────────────────────────────────────────
def render_timeline(stage: int = -1, tx_hash: str = "", error: str = ""):
    steps_json = [
        {"icon":"01","label":"Intent\nReceived","title":"Intent Received",
         "desc":"Natural language input captured and queued for parsing.",
         "rows":[{"k":"status","v":"received","ok":True}]},
        {"icon":"02","label":"Intent\nParsed","title":"Intent Parsed",
         "desc":"NLP engine extracts action, token, amount, recipient and strategy.",
         "rows":[{"k":"status","v":"parsed","ok":True}]},
        {"icon":"03","label":"Strategy\nSelected","title":"Strategy Selected",
         "desc":"Gas oracle queried; optimal route and fee tier determined.",
         "rows":[{"k":"strategy","v":"LOWEST_GAS","ok":True}]},
        {"icon":"04","label":"Transaction\nSent","title":"Transaction Sent",
         "desc":"Signed and broadcast to the mempool via RPC.",
         "rows":[{"k":"tx_hash","v":f"0x{tx_hash[:12]}…" if tx_hash else "pending","hl":True}]},
        {"icon":"05","label":"Confirmed","title":"Confirmed",
         "desc":"Transaction included in block; receipt verified on-chain.",
         "rows":[{"k":"status","v":"SUCCESS ✓" if not error else f"ERROR: {error}","ok":not error,"warn":bool(error)}]},
    ]
    steps_json_str  = json.dumps(steps_json)
    current_step    = stage
    tx_hash_display = f"0x{tx_hash}" if tx_hash else "—"

    return f"""
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@500;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  :root{{--bg:#0d1117;--surface:#161b22;--border:#21262d;--text:#e6edf3;--muted:#8b949e;--dim:#484f58;
    --blue:#58a6ff;--teal:#3fb950;--amber:#f0883e;--red:#f85149;
    --blue-dim:rgba(88,166,255,.12);--teal-dim:rgba(63,185,80,.12);
    --amber-dim:rgba(240,136,62,.12);--red-dim:rgba(248,81,73,.12);}}
  body{{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;padding:20px 16px;min-height:auto}}
  .status-bar{{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;
    background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-bottom:20px;
    font-size:12px;font-family:'JetBrains Mono',monospace}}
  .status-dot{{width:7px;height:7px;border-radius:50%;background:var(--dim);flex-shrink:0;transition:background .4s,box-shadow .4s}}
  .status-dot.active{{background:var(--teal);box-shadow:0 0 8px var(--teal)}}
  .tx-hash{{color:var(--blue);font-size:11px}}
  .pipeline{{display:flex;align-items:flex-start;gap:0;position:relative;padding:8px 0 28px}}
  .step{{flex:1;display:flex;flex-direction:column;align-items:center;position:relative}}
  .step:last-child .connector{{display:none}}
  .connector{{position:absolute;top:20px;left:50%;width:100%;height:2px;background:var(--border);z-index:0;transition:background .5s}}
  .step.done .connector{{background:var(--teal)}}
  .step.active .connector{{background:linear-gradient(90deg,var(--blue),var(--border))}}
  .circle{{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;
    position:relative;z-index:1;border:2px solid var(--dim);background:var(--bg);
    transition:all .4s;font-size:13px;font-weight:600;font-family:'JetBrains Mono',monospace;color:var(--dim);flex-shrink:0}}
  .step.done .circle{{border-color:var(--teal);color:var(--teal);background:var(--teal-dim)}}
  .step.active .circle{{border-color:var(--blue);color:var(--blue);background:var(--blue-dim);animation:pulse 1.5s ease-in-out infinite}}
  .step.error .circle{{border-color:var(--red);color:var(--red);background:var(--red-dim)}}
  @keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(88,166,255,.4)}}50%{{box-shadow:0 0 0 8px rgba(88,166,255,0)}}}}
  .step-label{{margin-top:8px;font-size:10px;text-align:center;color:var(--muted);transition:color .4s;line-height:1.3;padding:0 2px;letter-spacing:.3px}}
  .step.done .step-label{{color:var(--teal)}}
  .step.active .step-label{{color:var(--blue)}}
  .step.error .step-label{{color:var(--red)}}
  .detail-panel{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-height:64px}}
  .dp-head{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
  .dp-badge{{font-size:10px;font-family:'JetBrains Mono',monospace;padding:2px 8px;border-radius:4px;font-weight:600;letter-spacing:.5px}}
  .dp-badge.pending{{background:var(--border);color:var(--muted)}}
  .dp-badge.active{{background:var(--blue-dim);color:var(--blue)}}
  .dp-badge.done{{background:var(--teal-dim);color:var(--teal)}}
  .dp-badge.error{{background:var(--red-dim);color:var(--red)}}
  .dp-title{{font-size:13px;font-weight:700;color:var(--text)}}
  .dp-desc{{font-size:12px;color:var(--muted);line-height:1.6;font-family:'JetBrains Mono',monospace}}
  .dp-rows{{display:flex;flex-direction:column;gap:4px;margin-top:8px}}
  .dp-row{{display:flex;justify-content:space-between;font-size:11px;font-family:'JetBrains Mono',monospace}}
  .dp-row .k{{color:var(--dim)}} .dp-row .v{{color:var(--text)}}
  .dp-row .v.hl{{color:var(--blue)}} .dp-row .v.ok{{color:var(--teal)}} .dp-row .v.warn{{color:var(--amber)}}
  .progress-wrap{{height:3px;background:var(--border);border-radius:2px;margin-top:14px;overflow:hidden}}
  .progress-fill{{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--blue),var(--teal));transition:width .8s cubic-bezier(.4,0,.2,1)}}
</style></head><body>
<div class="status-bar">
  <div style="display:flex;align-items:center;gap:8px">
    <div class="status-dot" id="global-dot"></div>
    <span id="global-label" style="color:var(--muted);font-size:11px">idle</span>
  </div>
  <span class="tx-hash">{tx_hash_display}</span>
</div>
<div class="pipeline" id="pipeline"></div>
<div class="detail-panel" id="detail-panel">
  <div class="dp-head"><span class="dp-badge pending">IDLE</span><span class="dp-title">Transaction Lifecycle</span></div>
  <div class="dp-desc">Waiting for intent…</div>
</div>
<div class="progress-wrap"><div class="progress-fill" id="prog" style="width:0%"></div></div>
<script>
const STEPS={steps_json_str};const currentStep={current_step};
function buildPipeline(){{const c=document.getElementById('pipeline');c.innerHTML='';STEPS.forEach((s,i)=>{{const d=document.createElement('div');let cls='step ';if(i<currentStep)cls+='done';else if(i===currentStep)cls+='active';else cls+='idle';d.className=cls;d.innerHTML=`<div class="connector"></div><div class="circle">${{s.icon}}</div><div class="step-label">${{s.label}}</div>`;c.appendChild(d);}});}}
function renderDetail(){{const prog=document.getElementById('prog');const dot=document.getElementById('global-dot');const lbl=document.getElementById('global-label');if(currentStep<0)return;prog.style.width=((currentStep+1)/STEPS.length*100)+'%';dot.className='status-dot active';if(currentStep===STEPS.length-1){{lbl.textContent='confirmed — transaction complete';lbl.style.color='var(--teal)';}}else{{lbl.textContent='processing step '+(currentStep+1)+' of '+STEPS.length;lbl.style.color='var(--blue)';}}const s=STEPS[currentStep];const isDone=currentStep===STEPS.length-1;const badge=isDone?'done':'active';const badgeLabel=isDone?'CONFIRMED':'PROCESSING';let rowsHTML=s.rows.map(r=>{{const cls=r.ok?'ok':r.warn?'warn':r.hl?'hl':'';return`<div class="dp-row"><span class="k">${{r.k}}</span><span class="v ${{cls}}">${{r.v}}</span></div>`;}}).join('');document.getElementById('detail-panel').innerHTML=`<div class="dp-head"><span class="dp-badge ${{badge}}">${{badgeLabel}}</span><span class="dp-title">${{s.title}}</span></div><div class="dp-desc">${{s.desc}}</div><div class="dp-rows">${{rowsHTML}}</div>`;}}
buildPipeline();renderDetail();
</script></body></html>"""


# ── missing-fields dialog ──────────────────────────────────────────────────
@st.dialog("⚠️ Missing Transaction Details")
def missing_fields_dialog():
    intent  = st.session_state.parsed_intent
    missing = st.session_state.missing_fields

    st.markdown(
        """
        <style>
        .ic-banner{background:#161b22;border:1px solid #21262d;border-radius:10px;
                   padding:14px 16px;margin-bottom:16px}
        .ic-banner p{color:#8b949e;font-size:13px;margin:0;line-height:1.7}
        .ic-chip{display:inline-block;background:rgba(88,166,255,.12);color:#58a6ff;
                 font-size:11px;font-family:monospace;padding:2px 8px;border-radius:4px;
                 margin:2px 3px;border:1px solid rgba(88,166,255,.25)}
        .ic-filled{background:rgba(63,185,80,.07);border:1px solid rgba(63,185,80,.18);
                   border-radius:8px;padding:10px 14px;margin-bottom:16px}
        .ic-filled-hdr{font-size:10px;color:#3fb950;font-weight:700;
                       letter-spacing:.6px;margin-bottom:6px}
        .ic-filled-row{display:flex;justify-content:space-between;
                       font-size:12px;font-family:monospace;padding:2px 0}
        .ic-filled-key{color:#484f58} .ic-filled-val{color:#3fb950}
        </style>
        """,
        unsafe_allow_html=True,
    )

    chips = "".join(
        f'<span class="ic-chip">{FIELD_ICONS.get(f,"•")} {f}</span>'
        for f in missing
    )
    st.markdown(
        f'<div class="ic-banner"><p>I understood your intent but couldn\'t determine: '
        f'{chips}<br>Please fill in the missing details to continue.</p></div>',
        unsafe_allow_html=True,
    )

    # show already-resolved fields
    resolved = {k: v for k, v in intent.items() if k not in missing and k != "token"}
    if resolved:
        rows = "".join(
            f'<div class="ic-filled-row"><span class="ic-filled-key">{k}</span>'
            f'<span class="ic-filled-val">{v}</span></div>'
            for k, v in resolved.items()
        )
        st.markdown(
            f'<div class="ic-filled"><div class="ic-filled-hdr">✓ ALREADY RESOLVED</div>'
            f'{rows}</div>',
            unsafe_allow_html=True,
        )

    # inputs for missing fields only
    updates = {}
    for field in missing:
        meta  = FIELD_META[field]
        label = f"{FIELD_ICONS.get(field,'')} {meta['label']}"
        if meta["type"] == "select":
            updates[field] = st.selectbox(label, meta["options"], key=f"dlg_{field}")
        elif meta["type"] == "number":
            updates[field] = st.number_input(
                label, min_value=0.0, step=0.0001, format="%.6f",
                key=f"dlg_{field}"
            )
        else:
            updates[field] = st.text_input(label, placeholder=meta["hint"], key=f"dlg_{field}")

    st.divider()
    col_ok, col_cancel = st.columns([2, 1])

    with col_ok:
        if st.button("✅ Confirm & Execute", use_container_width=True, type="primary"):
            errors = []
            if "amount"    in updates and float(updates["amount"]) <= 0:
                errors.append("Amount must be greater than 0.")
            if "recipient" in updates and not str(updates["recipient"]).strip():
                errors.append("Recipient address cannot be empty.")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.session_state.parsed_intent    = {**intent, **updates}
                st.session_state.show_dialog      = False
                st.session_state.ready_to_execute = True
                st.rerun()

    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state.show_dialog    = False
            st.session_state.parsed_intent  = None
            st.session_state.missing_fields = []
            st.rerun()


# ── execute transaction ────────────────────────────────────────────────────
def run_transaction(timeline_ph, intent: dict):
    with timeline_ph:
        components.html(render_timeline(stage=0), height=260, scrolling=False)
    time.sleep(0.6)

    try:
        resp   = requests.post(EXEC_URL, json=intent, timeout=30)
        result = resp.json()
    except Exception as exc:
        st.error(f"Backend error: {exc}")
        return

    with timeline_ph:
        components.html(render_timeline(stage=1), height=260, scrolling=False)
    time.sleep(0.8)
    with timeline_ph:
        components.html(render_timeline(stage=2), height=260, scrolling=False)
    time.sleep(1.0)
    with timeline_ph:
        components.html(render_timeline(stage=3), height=260, scrolling=False)
    time.sleep(1.0)

    tx_result = result.get("transaction_result", {})
    tx_hash   = tx_result.get("tx_hash", "")

    if tx_hash:
        with timeline_ph:
            components.html(render_timeline(stage=4, tx_hash=tx_hash), height=260, scrolling=False)
        st.success(f"Transaction Hash: 0x{tx_hash}")
        st.markdown(f"[View on Etherscan](https://sepolia.etherscan.io/tx/0x{tx_hash})")
    else:
        err = tx_result.get("error", "Unknown error")
        with timeline_ph:
            components.html(render_timeline(stage=4, error=err), height=260, scrolling=False)
        st.error(err)

    # reset
    st.session_state.parsed_intent    = None
    st.session_state.missing_fields   = []
    st.session_state.ready_to_execute = False
    st.session_state.original_prompt  = ""


# ══════════════════════════════════════════════════════════════════════════
# PAGE
# ══════════════════════════════════════════════════════════════════════════
st.markdown("<h1 style='text-align:center;'>🚀 IntentChain Middleware Dashboard</h1>",
            unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

st.markdown("### ⛓ Transaction Lifecycle")
timeline_placeholder = st.empty()
with timeline_placeholder:
    components.html(render_timeline(stage=-1), height=260, scrolling=False)

st.markdown("<hr>", unsafe_allow_html=True)

st.markdown("### 💬 Enter Natural Language Intent")
user_input  = st.text_input(
    "Example: Send 0.0001 ETH to 0x742d… at lowest cost",
    value=st.session_state.original_prompt,
)
execute_btn = st.button("Execute Intent", type="primary")

# ── new prompt ──
if execute_btn and user_input.strip():
    st.session_state.original_prompt  = user_input
    st.session_state.ready_to_execute = False
    st.session_state.show_dialog      = False

    with st.spinner("Parsing intent…"):
        try:
            data = requests.post(PARSE_URL, json={"prompt": user_input}, timeout=30).json()
        except Exception as exc:
            st.error(f"Could not reach backend: {exc}")
            st.stop()

    st.session_state.parsed_intent  = data.get("parsed", {})
    st.session_state.missing_fields = data.get("missing_fields", [])

    if st.session_state.missing_fields:
        st.session_state.show_dialog = True   # ← opens the modal
    else:
        st.session_state.ready_to_execute = True

    st.rerun()

# ── open dialog ──
if st.session_state.show_dialog:
    missing_fields_dialog()

# ── execute ──
if st.session_state.ready_to_execute and st.session_state.parsed_intent:
    run_transaction(timeline_placeholder, st.session_state.parsed_intent)
