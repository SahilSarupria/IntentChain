import streamlit as st
import requests
import json
import streamlit.components.v1 as components

BACKEND_URL = "http://127.0.0.1:8000"
PARSE_URL   = f"{BACKEND_URL}/parse-intent"
BUILD_URL   = f"{BACKEND_URL}/build-tx"

st.set_page_config(page_title="IntentChain Dashboard", layout="wide")

# ── session-state bootstrap ────────────────────────────────────────────────
for key, default in {
    "wallet_address":   None,   # MetaMask connected address
    "parsed_intent":    None,
    "missing_fields":   [],
    "show_dialog":      False,
    "tx_params":        None,   # unsigned tx ready for MetaMask
    "strategy":         None,
    "pending_sign":     False,  # MetaMask signing widget visible
    "original_prompt":  "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── field metadata ─────────────────────────────────────────────────────────
FIELD_META = {
    "action":    {"label":"Action",           "type":"select",  "options":["transfer","send","swap","bridge"],           "hint":""},
    "amount":    {"label":"Amount (ETH)",      "type":"number",  "hint":"e.g. 0.002"},
    "recipient": {"label":"Recipient Address", "type":"text",    "hint":"e.g. 0xABC123…"},
    "network":   {"label":"Network",           "type":"select",  "options":["sepolia","ethereum","polygon","arbitrum","optimism","bsc"], "hint":""},
    "priority":  {"label":"Priority",          "type":"select",  "options":["low_cost","normal","fast"],                 "hint":""},
}
FIELD_ICONS = {"action":"⚡","amount":"💰","recipient":"📬","network":"🌐","priority":"🚀"}

# ══════════════════════════════════════════════════════════════════════════
# SHARED STYLES
# ══════════════════════════════════════════════════════════════════════════
SHARED_STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@500;700&display=swap');
:root{
  --bg:#0d1117;--surface:#161b22;--border:#21262d;
  --text:#e6edf3;--muted:#8b949e;--dim:#484f58;
  --blue:#58a6ff;--teal:#3fb950;--amber:#f0883e;--red:#f85149;
  --orange:#e36209;
  --blue-dim:rgba(88,166,255,.12);--teal-dim:rgba(63,185,80,.12);
  --amber-dim:rgba(240,136,62,.12);--red-dim:rgba(248,81,73,.12);
  --orange-dim:rgba(227,98,9,.12);
}
</style>
"""

# ══════════════════════════════════════════════════════════════════════════
# METAMASK CONNECT WIDGET
# ══════════════════════════════════════════════════════════════════════════
def metamask_connect_widget(current_address: str | None) -> None:
    """
    Renders a MetaMask connect button in an iframe.
    On connect, posts the wallet address to the parent page and stores it in
    parent localStorage for persistence across refreshes.
    """
    connected    = bool(current_address)
    addr_display = (current_address[:6] + "…" + current_address[-4:]) if connected else ""

    html = f"""
{SHARED_STYLES}
<style>
  body{{margin:0;padding:0;background:transparent;font-family:'Syne',sans-serif}}
  .mm-bar{{display:flex;align-items:center;gap:12px;padding:12px 16px;
    background:var(--surface);border:1px solid var(--border);border-radius:10px}}
  .mm-dot{{width:8px;height:8px;border-radius:50%;background:{'var(--teal)' if connected else 'var(--dim)'}; flex-shrink:0;
    {'box-shadow:0 0 8px var(--teal)' if connected else ''}}}
  .mm-label{{font-size:12px;color:{'var(--teal)' if connected else 'var(--muted)'};font-family:'JetBrains Mono',monospace;flex:1}}
  .mm-btn{{padding:7px 16px;border-radius:7px;border:1px solid;cursor:pointer;
    font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:600;
    background:{'var(--teal-dim)' if connected else 'var(--orange-dim)'};
    color:{'var(--teal)' if connected else 'var(--orange)'};
    border-color:{'rgba(63,185,80,.35)' if connected else 'rgba(227,98,9,.35)'};
    transition:opacity .2s}}
  .mm-btn:hover{{opacity:.8}}
  #err{{font-size:11px;color:var(--red);font-family:'JetBrains Mono',monospace;margin-top:6px;display:none}}
</style>
<div class="mm-bar">
  <div class="mm-dot"></div>
  <span class="mm-label" id="lbl">{'Connected: ' + addr_display if connected else 'Wallet not connected'}</span>
  <button class="mm-btn" id="btn" onclick="connectWallet()">
    {'✓ Connected' if connected else '🦊 Connect MetaMask'}
  </button>
</div>
<div id="err"></div>
<script>
async function connectWallet() {{
  const err = document.getElementById('err');
  err.style.display = 'none';

  const provider = window.parent?.ethereum || window.ethereum;
  if (typeof provider === 'undefined') {{
    err.style.display = 'block';
    err.textContent = '⚠ MetaMask not detected. Install the MetaMask browser extension.';
    return;
  }}

  try {{
    const accounts = await provider.request({{method:'eth_requestAccounts'}});
    const addr = accounts[0];
    document.getElementById('lbl').textContent = 'Connected: ' + addr.slice(0,6) + '…' + addr.slice(-4);
    document.getElementById('btn').textContent = '✓ Connected';

    try {{
      window.parent.localStorage.setItem('intentchain_wallet', addr);
    }} catch(e) {{ /* ignore localStorage issues */ }}
    window.parent.postMessage({{type:'wallet_connected', address: addr}}, '*');

    // Force a single rerun/reload so Streamlit picks up the persisted wallet immediately.
    setTimeout(() => {{
      try {{ window.parent.location.reload(); }} catch (e) {{ /* ignore */ }}
    }}, 150);
  }} catch(e) {{
    err.style.display = 'block';
    err.textContent = '⚠ ' + (e.message || 'Connection rejected');
  }}
}}
</script>
"""
    components.html(html, height=80, scrolling=False)


def wallet_listener() -> None:
    """
    Listens for wallet postMessage events and hydrates from localStorage on load.
    Writes the address into a hidden Streamlit text_input so Python can react.
    """
    html = """
<style>body{margin:0;padding:0}</style>
<script>
function pushWalletToStreamlit(addr) {
  if (!addr) return;

  try {
    window.parent.localStorage.setItem('intentchain_wallet', addr);
  } catch (e) { /* ignore */ }

  try {
    const url = new URL(window.parent.location.href);
    url.searchParams.set('wallet', addr);
    window.parent.history.replaceState({}, '', url.toString());
  } catch (e) { /* ignore */ }

  try {
    const inp = window.parent.document.querySelector('input[aria-label="wallet_capture"]');
    if (inp) {
      inp.value = addr;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    }
  } catch (e) { /* ignore */ }
}

window.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'wallet_connected') {
    pushWalletToStreamlit(e.data.address);
  }
});

// Hydrate wallet after manual refresh/new Streamlit session.
try {
  const saved = window.parent.localStorage.getItem('intentchain_wallet');
  if (saved) pushWalletToStreamlit(saved);
} catch (e) { /* ignore */ }
</script>
"""
    components.html(html, height=0, scrolling=False)



# ══════════════════════════════════════════════════════════════════════════
# TIMELINE
# ══════════════════════════════════════════════════════════════════════════
def render_timeline(stage: int = -1, tx_hash: str = "", error: str = "") -> str:
    steps = [
        {"icon":"01","label":"Intent\nReceived","title":"Intent Received",
         "desc":"Natural language input captured and queued for parsing.",
         "rows":[{"k":"status","v":"received","ok":True}]},
        {"icon":"02","label":"Intent\nParsed","title":"Intent Parsed",
         "desc":"NLP engine extracts action, token, amount, recipient and strategy.",
         "rows":[{"k":"status","v":"parsed","ok":True}]},
        {"icon":"03","label":"Strategy\nSelected","title":"Strategy Selected",
         "desc":"Gas oracle queried; optimal route and fee tier determined.",
         "rows":[{"k":"strategy","v":"LOWEST_GAS","ok":True}]},
        {"icon":"04","label":"Awaiting\nSignature","title":"Awaiting Signature",
         "desc":"Unsigned tx sent to MetaMask. Waiting for wallet approval.",
         "rows":[{"k":"signer","v":"MetaMask","hl":True}]},
        {"icon":"05","label":"Confirmed","title":"Confirmed",
         "desc":"Transaction included in block; receipt verified on-chain.",
         "rows":[{"k":"tx_hash","v":f"0x{tx_hash[:12]}…" if tx_hash else ("ERROR" if error else "pending"),"ok":bool(tx_hash),"warn":bool(error),"hl": not bool(tx_hash) and not bool(error)}]},
    ]
    sj  = json.dumps(steps)
    txd = f"0x{tx_hash}" if tx_hash else "—"
    cs  = stage

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@500;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  :root{{--bg:#0d1117;--surface:#161b22;--border:#21262d;--text:#e6edf3;--muted:#8b949e;--dim:#484f58;
    --blue:#58a6ff;--teal:#3fb950;--amber:#f0883e;--red:#f85149;
    --blue-dim:rgba(88,166,255,.12);--teal-dim:rgba(63,185,80,.12);--red-dim:rgba(248,81,73,.12);}}
  body{{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;padding:20px 16px}}
  .status-bar{{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;
    background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-bottom:20px;
    font-size:12px;font-family:'JetBrains Mono',monospace}}
  .status-dot{{width:7px;height:7px;border-radius:50%;background:var(--dim);flex-shrink:0;transition:background .4s,box-shadow .4s}}
  .status-dot.active{{background:var(--teal);box-shadow:0 0 8px var(--teal)}}
  .tx-hash{{color:var(--blue);font-size:11px}}
  .pipeline{{display:flex;align-items:flex-start;position:relative;padding:8px 0 28px}}
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
  .step.done .step-label{{color:var(--teal)}} .step.active .step-label{{color:var(--blue)}} .step.error .step-label{{color:var(--red)}}
  .detail-panel{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-height:64px}}
  .dp-head{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
  .dp-badge{{font-size:10px;font-family:'JetBrains Mono',monospace;padding:2px 8px;border-radius:4px;font-weight:600;letter-spacing:.5px}}
  .dp-badge.pending{{background:var(--border);color:var(--muted)}} .dp-badge.active{{background:var(--blue-dim);color:var(--blue)}}
  .dp-badge.done{{background:var(--teal-dim);color:var(--teal)}} .dp-badge.error{{background:var(--red-dim);color:var(--red)}}
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
    <div class="status-dot" id="dot"></div>
    <span id="lbl" style="color:var(--muted);font-size:11px">idle</span>
  </div>
  <span class="tx-hash">{txd}</span>
</div>
<div class="pipeline" id="pipeline"></div>
<div class="detail-panel" id="detail"></div>
<div class="progress-wrap"><div class="progress-fill" id="prog" style="width:0%"></div></div>
<script>
const S={sj},C={cs};
function build(){{const c=document.getElementById('pipeline');c.innerHTML='';S.forEach((s,i)=>{{const d=document.createElement('div');let cl='step ';cl+=i<C?'done':i===C?'active':'idle';d.className=cl;d.innerHTML=`<div class="connector"></div><div class="circle">${{s.icon}}</div><div class="step-label">${{s.label}}</div>`;c.appendChild(d);}});}}
function detail(){{if(C<0){{document.getElementById('detail').innerHTML='<div class="dp-head"><span class="dp-badge pending">IDLE</span><span class="dp-title">Transaction Lifecycle</span></div><div class="dp-desc">Waiting for intent…</div>';return;}}const prog=document.getElementById('prog');const dot=document.getElementById('dot');const lbl=document.getElementById('lbl');prog.style.width=((C+1)/S.length*100)+'%';dot.className='status-dot active';const done=C===S.length-1;lbl.textContent=done?'confirmed — transaction complete':'processing step '+(C+1)+' of '+S.length;lbl.style.color=done?'var(--teal)':'var(--blue)';const s=S[C];const badge=done?'done':'active';const bl=done?'CONFIRMED':'PROCESSING';let rh=s.rows.map(r=>{{const cl=r.ok?'ok':r.warn?'warn':r.hl?'hl':'';return`<div class="dp-row"><span class="k">${{r.k}}</span><span class="v ${{cl}}">${{r.v}}</span></div>`;}}).join('');document.getElementById('detail').innerHTML=`<div class="dp-head"><span class="dp-badge ${{badge}}">${{bl}}</span><span class="dp-title">${{s.title}}</span></div><div class="dp-desc">${{s.desc}}</div><div class="dp-rows">${{rh}}</div>`;}}
build();detail();
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════
# METAMASK SIGNING WIDGET
# ══════════════════════════════════════════════════════════════════════════
def metamask_sign_widget(tx_params: dict, strategy: dict) -> None:
    """
    Renders a self-contained signing panel.
    Calls window.parent.ethereum.request({method:'eth_sendTransaction'}) in the browser.
    On success, posts the tx hash back to Streamlit via the URL param trick.
    The private key never leaves MetaMask.
    """
    tx_json      = json.dumps(tx_params, indent=2)
    strategy_json = json.dumps(strategy)
    gas_eth      = int(tx_params.get("gas", "0x5208"), 16)
    value_wei    = int(tx_params.get("value", "0x0"), 16)
    value_eth    = value_wei / 1e18

    html = f"""
{SHARED_STYLES}
<style>
  body{{margin:0;padding:0;background:transparent;font-family:'Syne',sans-serif}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:12px}}
  .card-title{{font-size:11px;font-weight:700;letter-spacing:.7px;color:var(--muted);margin-bottom:14px;font-family:'JetBrains Mono',monospace}}
  .row{{display:flex;justify-content:space-between;font-size:12px;font-family:'JetBrains Mono',monospace;padding:4px 0;border-bottom:1px solid var(--border)}}
  .row:last-child{{border:none}}
  .row .k{{color:var(--dim)}} .row .v{{color:var(--text)}}
  .row .v.hi{{color:var(--blue)}} .row .v.green{{color:var(--teal)}}
  .sign-btn{{width:100%;padding:12px;border-radius:9px;border:none;cursor:pointer;
    background:linear-gradient(135deg,#f6851b,#e36209);
    color:#fff;font-family:'Syne',sans-serif;font-size:14px;font-weight:700;
    letter-spacing:.3px;transition:opacity .2s;display:flex;align-items:center;justify-content:center;gap:8px}}
  .sign-btn:hover{{opacity:.88}} .sign-btn:disabled{{opacity:.45;cursor:not-allowed}}
  .status-msg{{font-size:12px;font-family:'JetBrains Mono',monospace;margin-top:10px;
    padding:10px 14px;border-radius:7px;display:none}}
  .status-msg.info{{background:var(--blue-dim);color:var(--blue);border:1px solid rgba(88,166,255,.25)}}
  .status-msg.ok{{background:var(--teal-dim);color:var(--teal);border:1px solid rgba(63,185,80,.25)}}
  .status-msg.err{{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,81,73,.25)}}
  .tx-detail{{background:var(--bg);border:1px solid var(--border);border-radius:7px;
    padding:10px 12px;font-size:10px;font-family:'JetBrains Mono',monospace;
    color:var(--muted);word-break:break-all;white-space:pre;overflow-x:auto;margin-top:8px;display:none}}
  .toggle{{font-size:10px;color:var(--blue);cursor:pointer;font-family:'JetBrains Mono',monospace;
    margin-top:6px;display:inline-block}}
</style>

<div class="card">
  <div class="card-title">📋 TRANSACTION PREVIEW</div>
  <div class="row"><span class="k">from</span><span class="v hi">{tx_params.get('from','')[:10]}…</span></div>
  <div class="row"><span class="k">to</span><span class="v hi">{tx_params.get('to','')[:10]}…</span></div>
  <div class="row"><span class="k">value</span><span class="v green">{value_eth:.6f} ETH</span></div>
  <div class="row"><span class="k">gas limit</span><span class="v">{gas_eth:,}</span></div>
  <div class="row"><span class="k">gas price</span><span class="v">{strategy.get('gas_price_strategy','standard')}</span></div>
  <span class="toggle" onclick="document.getElementById('raw').style.display=document.getElementById('raw').style.display==='none'?'block':'none'">
    ⬛ show raw tx params
  </span>
  <div class="tx-detail" id="raw">{tx_json}</div>
</div>

<button class="sign-btn" id="signBtn" onclick="sendTx()">
  🦊 Sign &amp; Send with MetaMask
</button>
<div class="status-msg" id="msg"></div>

<script>
const TX_PARAMS = {tx_json};

function showMsg(text, type) {{
  const m = document.getElementById('msg');
  m.className = 'status-msg ' + type;
  m.style.display = 'block';
  m.textContent = text;
}}

async function sendTx() {{
  const btn = document.getElementById('signBtn');
  btn.disabled = true;
  btn.innerHTML = '⏳ Waiting for MetaMask…';
  showMsg('MetaMask popup should appear. Please review and confirm.', 'info');

  if (typeof window.parent.ethereum === 'undefined') {{
    showMsg('⚠ MetaMask not detected. Install the MetaMask extension.', 'err');
    btn.disabled = false;
    btn.innerHTML = '🦊 Sign & Send with MetaMask';
    return;
  }}

  try {{
    const txHash = await window.parent.ethereum.request({{
      method: 'eth_sendTransaction',
      params: [TX_PARAMS]
    }});

    showMsg('✅ Transaction sent! Hash: ' + txHash, 'ok');
    btn.innerHTML = '✓ Sent';

    // Push tx hash to parent Streamlit via URL param and force rerun
    const url = new URL(window.parent.location.href);
    url.searchParams.set('tx_hash', txHash);
<<<<<<< HEAD
    window.parent.postMessage({{type: 'tx_sent', tx_hash: txHash}}, '*');
=======
    window.parent.location.assign(url.toString());
>>>>>>> c9edd7931d4931ca04e4ee9b81d24ba0f31ccfba

    // Also postMessage for instant pickup
    window.parent.postMessage({{type: 'tx_sent', tx_hash: txHash}}, '*');

  }} catch(e) {{
    const msg = e.code === 4001
      ? 'Transaction rejected by user.'
      : (e.message || 'Unknown error');
    showMsg('❌ ' + msg, 'err');
    btn.disabled = false;
    btn.innerHTML = '🦊 Sign & Send with MetaMask';
  }}
}}
</script>
"""
    components.html(html, height=340, scrolling=False)


# ══════════════════════════════════════════════════════════════════════════
# MISSING-FIELDS DIALOG
# ══════════════════════════════════════════════════════════════════════════
@st.dialog("⚠️ Missing Transaction Details")
def missing_fields_dialog():
    intent  = st.session_state.parsed_intent
    missing = st.session_state.missing_fields

    st.markdown("""
    <style>
    .ic-banner{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px 16px;margin-bottom:16px}
    .ic-banner p{color:#8b949e;font-size:13px;margin:0;line-height:1.7}
    .ic-chip{display:inline-block;background:rgba(88,166,255,.12);color:#58a6ff;font-size:11px;
             font-family:monospace;padding:2px 8px;border-radius:4px;margin:2px 3px;border:1px solid rgba(88,166,255,.25)}
    .ic-filled{background:rgba(63,185,80,.07);border:1px solid rgba(63,185,80,.18);
               border-radius:8px;padding:10px 14px;margin-bottom:16px}
    .ic-filled-hdr{font-size:10px;color:#3fb950;font-weight:700;letter-spacing:.6px;margin-bottom:6px}
    .ic-filled-row{display:flex;justify-content:space-between;font-size:12px;font-family:monospace;padding:2px 0}
    .ic-filled-key{color:#484f58} .ic-filled-val{color:#3fb950}
    </style>""", unsafe_allow_html=True)

    chips = "".join(f'<span class="ic-chip">{FIELD_ICONS.get(f,"•")} {f}</span>' for f in missing)
    st.markdown(
        f'<div class="ic-banner"><p>I understood your intent but couldn\'t determine: '
        f'{chips}<br>Please fill in the missing details to continue.</p></div>',
        unsafe_allow_html=True)

    resolved = {k: v for k, v in intent.items() if k not in missing and k != "token"}
    if resolved:
        rows = "".join(
            f'<div class="ic-filled-row"><span class="ic-filled-key">{k}</span><span class="ic-filled-val">{v}</span></div>'
            for k, v in resolved.items())
        st.markdown(
            f'<div class="ic-filled"><div class="ic-filled-hdr">✓ ALREADY RESOLVED</div>{rows}</div>',
            unsafe_allow_html=True)

    updates = {}
    for field in missing:
        meta  = FIELD_META[field]
        label = f"{FIELD_ICONS.get(field,'')} {meta['label']}"
        if meta["type"] == "select":
            updates[field] = st.selectbox(label, meta["options"], key=f"dlg_{field}")
        elif meta["type"] == "number":
            updates[field] = st.number_input(label, min_value=0.0, step=0.0001, format="%.6f", key=f"dlg_{field}")
        else:
            updates[field] = st.text_input(label, placeholder=meta["hint"], key=f"dlg_{field}")

    st.divider()
    col_ok, col_cancel = st.columns([2, 1])
    with col_ok:
        if st.button("✅ Confirm & Continue", use_container_width=True, type="primary"):
            errors = []
            if "amount"    in updates and float(updates["amount"]) <= 0: errors.append("Amount must be > 0.")
            if "recipient" in updates and not str(updates["recipient"]).strip(): errors.append("Recipient cannot be empty.")
            if errors:
                for e in errors: st.error(e)
            else:
                st.session_state.parsed_intent = {**intent, **updates}
                st.session_state.show_dialog   = False
                st.session_state.pending_sign  = True   # go to build-tx next
                st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            for k in ["show_dialog","parsed_intent","missing_fields","pending_sign","tx_params","strategy"]:
                st.session_state[k] = None if "intent" in k or "params" in k or "strategy" in k else False if isinstance(st.session_state[k], bool) else []
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT
# ══════════════════════════════════════════════════════════════════════════
st.markdown("<h1 style='text-align:center;'>🚀 IntentChain Middleware Dashboard</h1>", unsafe_allow_html=True)

# ── Wallet connection bar ──
st.markdown("### 🦊 Wallet")

# Hidden bridge input used by wallet_listener JS; completely hidden from UI.
st.markdown(
    """
    <style>
      div[data-testid="stTextInput"]:has(input[aria-label="wallet_capture"]) {
        display:none !important;
      }
      input[aria-label="wallet_capture"] {
        display:none !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)
st.text_input("wallet_capture", key="wallet_capture", value="", label_visibility="collapsed")

wallet_listener()

metamask_connect_widget(st.session_state.wallet_address)

if st.session_state.get("wallet_capture") and st.session_state.get("wallet_capture") != st.session_state.wallet_address:
    st.session_state.wallet_address = st.session_state.get("wallet_capture")
    st.rerun()

# Backwards-compat: keep honoring query param path (older signing widget flow)
qp = st.query_params
if "wallet" in qp and qp["wallet"] != st.session_state.wallet_address:
    st.session_state.wallet_address = qp["wallet"]
    st.rerun()

# Pick up tx hash from URL query param (set by signing iframe JS)
if "tx_hash" in qp and st.session_state.pending_sign:
    tx_hash = qp["tx_hash"]
    st.session_state.pending_sign = False
    st.session_state.tx_params    = None
    st.session_state.parsed_intent = None
    st.query_params.clear()
    st.success(f"✅ Transaction broadcast!  Hash: {tx_hash}")
    st.markdown(f"[View on Etherscan ↗](https://sepolia.etherscan.io/tx/{tx_hash})")
    st.stop()

st.markdown("<hr>", unsafe_allow_html=True)

# ── Timeline ──
st.markdown("### ⛓ Transaction Lifecycle")
timeline_ph = st.empty()

# Determine current stage for timeline
if   st.session_state.pending_sign and st.session_state.tx_params: _stage = 3
elif st.session_state.pending_sign:                                  _stage = 1
elif st.session_state.parsed_intent:                                 _stage = 0
else:                                                                _stage = -1
with timeline_ph:
    components.html(render_timeline(stage=_stage), height=260, scrolling=False)

st.markdown("<hr>", unsafe_allow_html=True)

# ── Intent input ──
st.markdown("### 💬 Enter Natural Language Intent")

if not st.session_state.wallet_address:
    st.info("🦊 Connect your MetaMask wallet above before submitting an intent.")

user_input  = st.text_input("Example: Send 0.002 ETH to 0x742d… on Sepolia at lowest cost",
                             value=st.session_state.original_prompt)
execute_btn = st.button("Execute Intent", type="primary",
                        disabled=not bool(st.session_state.wallet_address))

# ── New prompt submitted ──
if execute_btn and user_input.strip():
    st.session_state.original_prompt = user_input
    st.session_state.pending_sign    = False
    st.session_state.tx_params       = None
    st.session_state.show_dialog     = False

    with st.spinner("Parsing intent…"):
        try:
            data = requests.post(PARSE_URL, json={"prompt": user_input}, timeout=30).json()
        except Exception as exc:
            st.error(f"Backend unreachable: {exc}")
            st.stop()

    st.session_state.parsed_intent  = data.get("parsed", {})
    st.session_state.missing_fields = data.get("missing_fields", [])

    if st.session_state.missing_fields:
        st.session_state.show_dialog = True
    else:
        st.session_state.pending_sign = True
    st.rerun()

# ── Missing fields dialog ──
if st.session_state.show_dialog:
    missing_fields_dialog()

# ── Build tx and show signing widget ──
if st.session_state.pending_sign and st.session_state.parsed_intent and not st.session_state.tx_params:
    with st.spinner("Building unsigned transaction…"):
        try:
            result = requests.post(BUILD_URL, json={
                "intent":       st.session_state.parsed_intent,
                "from_address": st.session_state.wallet_address,
            }, timeout=30).json()
        except Exception as exc:
            st.error(f"Backend error: {exc}")
            st.stop()
    st.session_state.tx_params = result.get("tx_params")
    st.session_state.strategy  = result.get("strategy")
    st.rerun()

if st.session_state.pending_sign and st.session_state.tx_params:
    st.markdown("### ✍️ Sign Transaction")
    st.caption("Review the transaction details below, then approve in MetaMask. Your private key never leaves your browser.")
    metamask_sign_widget(st.session_state.tx_params, st.session_state.strategy or {})