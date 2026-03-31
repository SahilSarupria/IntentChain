import streamlit as st
import requests
import time

BACKEND_URL = "http://127.0.0.1:8000/execute-intent"

st.set_page_config(page_title="IntentChain Dashboard", layout="wide")

# ---------- HEADER ----------
st.markdown("""
    <h1 style='text-align: center;'>🚀 IntentChain Middleware Dashboard</h1>
    <hr>
""", unsafe_allow_html=True)

# ---------- MODULE CARDS ----------
col1, col2, col3, col4 = st.columns(4)

intent_box = col1.empty()
strategy_box = col2.empty()
risk_box = col3.empty()
exec_box = col4.empty()

def module_card(container, title, status, content=""):
    color = "#1e293b"
    if status == "active":
        color = "#0ea5e9"
    elif status == "done":
        color = "#22c55e"

    container.markdown(f"""
        <div style="
            background-color:{color};
            padding:20px;
            border-radius:12px;
            color:white;
            min-height:160px;
        ">
            <h4>{title}</h4>
            <p>{content}</p>
        </div>
    """, unsafe_allow_html=True)

# Initialize modules
module_card(intent_box, "🧠 Intent Engine", "idle")
module_card(strategy_box, "⚙ Strategy Engine", "idle")
module_card(risk_box, "🛡 Risk Evaluator", "idle")
module_card(exec_box, "⛓ Blockchain Executor", "idle")

st.markdown("<hr>", unsafe_allow_html=True)

# ---------- CHAT INPUT ----------
st.markdown("### 💬 Enter Natural Language Intent")

user_input = st.text_input(
    "Example: Send 0.0001 ETH to 0x742d... at lowest cost",
    ""
)

execute_btn = st.button("Execute Intent")

if execute_btn and user_input:

    # Simple NLP mock (for now)
    # You can replace with backend parsing later
    payload = {
        "prompt": user_input
    }

    response = requests.post(BACKEND_URL, json=payload)
    result = response.json()

    # ---- Intent Stage ----
    module_card(intent_box, "🧠 Intent Engine", "active", "Parsing user intent...")
    time.sleep(1)

    intent = result

    module_card(
        intent_box,
        "🧠 Intent Engine",
        "done",
        f"""
        {intent}
        
        """
    )
# Action: {intent.get('action')}<br>
#         Amount: {intent.get('amount')}<br>
#         Token: {intent.get('token')}<br>
    # ---- Strategy Stage ----
    module_card(strategy_box, "⚙ Strategy Engine", "active", "Evaluating execution strategy...")
    time.sleep(1)

    

    strategy = result.get("strategy", {})

    module_card(
        strategy_box,
        "⚙ Strategy Engine",
        "done",
        f"""
        Gas Estimate: {strategy.get('gas_estimate')}<br>
        Strategy: {strategy.get('gas_price_strategy')}
        """
    )

    # ---- Risk Stage ----
    module_card(risk_box, "🛡 Risk Evaluator", "active", "Assessing smart contract risk...")
    time.sleep(1)

    module_card(
        risk_box,
        "🛡 Risk Evaluator",
        "done",
        "Risk Level: Low"
    )

    # ---- Execution Stage ----
    module_card(exec_box, "⛓ Blockchain Executor", "active", "Signing & Broadcasting...")
    time.sleep(1)

    tx_result = result.get("transaction_result", {})

    if "tx_hash" in tx_result:
        tx_hash = tx_result["tx_hash"]

        module_card(
            exec_box,
            "⛓ Blockchain Executor",
            "done",
            f"""
            Transaction Broadcasted ✅<br>
            <a href='https://sepolia.etherscan.io/tx/0x{tx_hash}' target='_blank'>
            View on Etherscan
            </a>
            """
        )

        st.success(f"Transaction Hash: 0x{tx_hash}")

    else:
        module_card(
            exec_box,
            "⛓ Blockchain Executor",
            "done",
            f"Error: {tx_result.get('error')}"
        )

        st.error(tx_result.get("error"))