from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
import time
import os
from web3 import Web3

from app.llm.intent_parser import parse_intent
from app.services.intent_engine import build_tx_for_wallet
from app.core.logger import get_logs, clear_logs, log_event, get_stats
from app.blockchain.rpc import get_w3, is_connected
from app.blockchain import erc20, etherscan_client
from app.blockchain.gas_oracle import compute_gas_strategies
from app.services import supply_chain_service
from app.services import contract_registry
from app.services.strategy_engine import evaluate_strategy
from app.blockchain import contract_compiler
from app.config.networks import list_networks, normalize_network, get_network_config

router = APIRouter()

MISSING_SENTINEL = {"none", "", "null", "unknown", "n/a", "0"}

# Which fields actually matter per action — a balance check doesn't need a
# recipient, a checkpoint log doesn't need an amount, etc. Keeps the
# "missing fields" popup relevant instead of always demanding all 5 original
# fields regardless of what the user asked for.
REQUIRED_FIELDS_BY_ACTION = {
    "transfer":         ["action", "amount", "recipient", "network", "priority"],
    "send":             ["action", "amount", "recipient", "network", "priority"],
    "bridge":           ["action", "amount", "recipient", "network", "priority"],
    "transfer_token":   ["action", "token", "amount", "recipient", "network", "priority"],
    "send_token":       ["action", "token", "amount", "recipient", "network", "priority"],
    "approve_token":    ["action", "token", "amount", "spender", "network", "priority"],
    "check_balance":    ["action", "network"],
    "get_history":      ["action", "network"],
    "register_product": ["action", "product_id", "name", "origin", "network"],
    "log_checkpoint":   ["action", "product_id", "location", "status", "network"],
    "verify_product":   ["action", "product_id", "network"],
    "swap":             ["action", "token", "amount", "network"],
    "general_question": ["action"],
    "deploy_contract":  ["action", "network"],
}
DEFAULT_REQUIRED = ["action", "amount", "recipient", "network", "priority"]

# Every action funnels into one of four UX paths:
#  - "transaction": needs an unsigned tx + MetaMask signature (existing flow)
#  - "read":        answers directly from chain/API data, no signature needed
#  - "knowledge":   the LLM already answered it in the parse step, nothing else to do
#  - "deploy":      compiles + deploys the customer's own private supply-chain
#                    contract, then auto-registers it (persisted, not session-only)
TRANSACTION_ACTIONS = {"transfer", "send", "bridge", "transfer_token", "send_token",
                        "approve_token", "register_product", "log_checkpoint", "swap"}
READ_ACTIONS = {"check_balance", "get_history", "verify_product"}
KNOWLEDGE_ACTIONS = {"general_question"}
DEPLOY_ACTIONS = {"deploy_contract"}


def _mode_for_action(action: str) -> str:
    action = (action or "").lower()
    if action in READ_ACTIONS:
        return "read"
    if action in KNOWLEDGE_ACTIONS:
        return "knowledge"
    if action in DEPLOY_ACTIONS:
        return "deploy"
    return "transaction"


class PromptRequest(BaseModel):
    prompt: str

class ExecuteReadRequest(BaseModel):
    intent: dict
    wallet_address: str | None = None

class BuildTxRequest(BaseModel):
    intent:       dict
    from_address: str

class SupplyChainRegisterRequest(BaseModel):
    network: str = "sepolia"
    from_address: str
    product_id: str
    name: str
    origin: str

class SupplyChainCheckpointRequest(BaseModel):
    network: str = "sepolia"
    from_address: str
    product_id: str
    location: str
    status: str
    temperature_c: int = 0

class RegisterContractRequest(BaseModel):
    wallet_address: str
    network: str = "sepolia"
    contract_address: str
    label: str = ""

class ActivateContractRequest(BaseModel):
    wallet_address: str
    network: str = "sepolia"
    contract_address: str

class RemoveContractRequest(BaseModel):
    wallet_address: str
    network: str = "sepolia"
    contract_address: str

class DeployContractRequest(BaseModel):
    network: str = "sepolia"
    from_address: str


def _is_missing(val) -> bool:
    return val is None or str(val).strip().lower() in MISSING_SENTINEL


def _detect_missing(parsed: dict) -> list:
    action = str(parsed.get("action", "")).lower()
    required = REQUIRED_FIELDS_BY_ACTION.get(action, DEFAULT_REQUIRED)
    missing = []
    for field in required:
        if field == "amount":
            try:
                if float(parsed.get("amount", 0)) <= 0:
                    missing.append("amount")
            except (TypeError, ValueError):
                missing.append("amount")
        elif _is_missing(parsed.get(field)):
            missing.append(field)
    return missing


@router.post("/parse-intent")
async def parse_intent_only(request: PromptRequest, raw: Request):
    t0 = time.perf_counter()
    client_ip = raw.client.host if raw.client else "unknown"
    log_event("parse_intent", {"status": "started", "prompt_length": len(request.prompt), "client_ip": client_ip})
    try:
        parsed  = parse_intent(request.prompt)
        mode    = _mode_for_action(parsed.get("action"))
        missing = [] if mode == "knowledge" else _detect_missing(parsed)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("parse_intent", {
            "status": "success", "latency_ms": latency_ms,
            "parsed_action": parsed.get("action"), "parsed_network": parsed.get("network"),
            "mode": mode, "missing_fields": missing, "prompt_preview": request.prompt[:80],
        })
        return {"parsed": parsed, "missing_fields": missing, "mode": mode, "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("parse_intent", {"status": "error", "error": str(exc), "latency_ms": latency_ms})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────
# READ-ONLY EXECUTION — for actions that just answer a question about chain
# state (balance/history/product lookup) instead of building a signable tx.
# No MetaMask involved at all; this is a plain GET-style answer.
# ─────────────────────────────────────────────────────────────────────────
@router.post("/execute-read")
async def execute_read(request: ExecuteReadRequest):
    t0 = time.perf_counter()
    intent = request.intent
    action = str(intent.get("action") or "").lower()
    network = normalize_network(intent.get("network"))
    wallet = request.wallet_address

    try:
        if action == "check_balance":
            if not wallet:
                return {"answer": "Connect your wallet first so I know which address to check.", "data": None}
            w3 = get_w3(network)
            result = erc20.list_wallet_balances(w3, network, wallet)
            answer = _format_balances_answer(result, network)
            data, render_as = result, "balances"

        elif action == "get_history":
            if not wallet:
                return {"answer": "Connect your wallet first so I know which address to look up.", "data": None}
            native = etherscan_client.get_native_tx_history(wallet, network, 10)
            tokens = etherscan_client.get_token_tx_history(wallet, network, 10)
            answer = _format_history_answer(native, network)
            data, render_as = {"native": native, "tokens": tokens}, "history"

        elif action == "verify_product":
            product_id = intent.get("product_id")
            if not product_id or str(product_id).lower() in MISSING_SENTINEL:
                return {"answer": "Which batch/SKU ID should I look up?", "data": None}
            w3 = get_w3(network)
            result = supply_chain_service.read_product(w3, network, product_id, wallet_address=wallet)
            answer = _format_verify_answer(result, product_id)
            data, render_as = result, "provenance"

        else:
            return {"answer": f"'{action}' isn't a read action I know how to answer directly.", "data": None}

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("execute_read", {"status": "success", "action": action, "network": network, "latency_ms": latency_ms})
        return {"answer": answer, "data": data, "render_as": render_as, "latency_ms": latency_ms}

    except Exception as exc:
        log_event("execute_read", {"status": "error", "action": action, "network": network, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _format_balances_answer(result: dict, network: str) -> str:
    balances = result.get("balances", [])
    lines = []
    for b in balances:
        if b.get("error") or b.get("balance") is None or b["balance"] == 0:
            continue
        lines.append(f"{b['balance']:.6f} {b['symbol']}")
    if not lines:
        return f"Your wallet doesn't hold any tracked balances on {network} right now (or they're all zero)."
    return f"On {network}, you have: " + ", ".join(lines) + "."


def _format_history_answer(native: dict, network: str) -> str:
    if "error" in native:
        return f"Couldn't pull transaction history: {native['error']}"
    txs = native.get("transactions", [])
    if not txs:
        return f"No transactions found for your wallet on {network}."
    latest = txs[0]
    return (f"You have {len(txs)} recent transaction(s) on {network}. "
            f"Most recent: {latest['value']:.6f} to {latest['to'][:10]}… "
            f"({'failed' if latest['is_error'] else 'success'}).")


def _format_verify_answer(result: dict, product_id: str) -> str:
    if not result.get("deployed"):
        return result.get("message", "No supply-chain contract configured to check against.")
    if "error" in result:
        return f"Couldn't verify '{product_id}': {result['error']}"
    if not result.get("found"):
        return f"No record of '{product_id}' was found on this contract — it may not be genuine, or hasn't been registered yet."
    count = len(result.get("checkpoints", []))
    return (f"✓ '{product_id}' ({result.get('name')}) is authentic — registered by "
            f"{result['manufacturer'][:10]}… with {count} checkpoint(s) on record.")


@router.post("/build-tx")
async def build_tx(request: BuildTxRequest):
    t0 = time.perf_counter()
    log_event("build_tx", {
        "status": "started", "action": request.intent.get("action"),
        "network": request.intent.get("network"), "amount": request.intent.get("amount"),
        "from_address": (request.from_address[:10] + "…") if request.from_address else "unknown",
    })
    try:
        result = build_tx_for_wallet(request.intent, request.from_address)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        strategy = result.get("strategy", {})
        log_event("build_tx", {
            "status": "success", "latency_ms": latency_ms,
            "gas_estimate": strategy.get("gas_estimate"),
            "gas_price_strategy": strategy.get("gas_price_strategy"),
            "selected_tier": strategy.get("selected_tier"),
            "simulation_warning": bool(result.get("simulation_warning")),
        })
        result["latency_ms"] = latency_ms
        return result
    except ValueError as exc:
        # Expected, user-actionable errors (unsupported action, unknown token,
        # contract not deployed, bad address, ...) — 400, not 500.
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("build_tx", {"status": "error", "error": str(exc), "latency_ms": latency_ms})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("build_tx", {"status": "error", "error": str(exc), "latency_ms": latency_ms})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/logs")
async def get_activity_logs(limit: int = 100):
    return {"logs": get_logs(limit)}


@router.delete("/logs")
async def clear_activity_logs():
    clear_logs()
    return {"status": "cleared"}


@router.get("/health")
async def health_check():
    return {"status": "ok", "stats": get_stats()}


@router.post("/log-tx-result")
async def log_tx_result(payload: dict):
    log_event("tx_result", {
        "status": payload.get("status"),
        "tx_hash": payload.get("tx_hash", ""),
        "network": payload.get("network", ""),
        "amount_eth": payload.get("amount_eth"),
        "action": payload.get("action"),
        "user_latency_ms": payload.get("user_latency_ms"),
        "error": payload.get("error", ""),
    })
    return {"status": "logged"}


# ─────────────────────────────────────────────────────────────────────────
# NETWORKS
# ─────────────────────────────────────────────────────────────────────────
@router.get("/networks")
async def get_networks():
    networks = list_networks()
    for n in networks:
        n["connected"] = is_connected(n["id"])
    return {"networks": networks}


# ─────────────────────────────────────────────────────────────────────────
# WALLET — TOKEN BALANCES (ETH + ERC-20, before/after tx display)
# ─────────────────────────────────────────────────────────────────────────
@router.get("/wallet/balances")
async def wallet_balances(address: str, network: str = Query("sepolia")):
    t0 = time.perf_counter()
    try:
        w3 = get_w3(network)
        result = erc20.list_wallet_balances(w3, network, address)
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        log_event("wallet_balances", {"status": "success", "network": network, "latency_ms": result["latency_ms"]})
        return result
    except Exception as exc:
        log_event("wallet_balances", {"status": "error", "network": network, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────
# WALLET — TRANSACTION HISTORY (Etherscan v2 unified API)
# ─────────────────────────────────────────────────────────────────────────
@router.get("/wallet/history")
async def wallet_history(address: str, network: str = Query("sepolia"), limit: int = 25):
    native = etherscan_client.get_native_tx_history(address, network, limit)
    tokens = etherscan_client.get_token_tx_history(address, network, limit)
    log_event("wallet_history", {"status": "success" if "error" not in native else "error", "network": network})
    return {"native": native, "tokens": tokens}


# ─────────────────────────────────────────────────────────────────────────
# LIVE GAS PRICING
# ─────────────────────────────────────────────────────────────────────────
@router.get("/gas/live")
async def gas_live(network: str = Query("sepolia")):
    network = normalize_network(network)
    w3 = get_w3(network)
    connected = is_connected(network)
    cfg = get_network_config(network)
    result = compute_gas_strategies(w3) if connected else {"supports_1559": False, "base_fee_gwei": None, "tiers": {}}
    result["network"] = network
    result["native_symbol"] = cfg["native"]
    result["node_connected"] = connected
    log_event("gas_quote", {"status": "success", "network": network})
    return result


# ─────────────────────────────────────────────────────────────────────────
# SUPPLY CHAIN / PHARMA TRACEABILITY
# ─────────────────────────────────────────────────────────────────────────
@router.post("/contracts/supply-chain/register")
async def supply_chain_register(req: SupplyChainRegisterRequest):
    try:
        intent = {"action": "register_product", "network": req.network,
                  "product_id": req.product_id, "name": req.name, "origin": req.origin}
        result = build_tx_for_wallet(intent, req.from_address)
        log_event("supply_chain_register", {"status": "success", "network": req.network, "product_id": req.product_id})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/contracts/supply-chain/checkpoint")
async def supply_chain_checkpoint(req: SupplyChainCheckpointRequest):
    try:
        intent = {"action": "log_checkpoint", "network": req.network, "product_id": req.product_id,
                  "location": req.location, "status": req.status, "temperature_c": req.temperature_c}
        result = build_tx_for_wallet(intent, req.from_address)
        log_event("supply_chain_checkpoint", {"status": "success", "network": req.network, "product_id": req.product_id})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/contracts/supply-chain/product")
async def supply_chain_product(product_id: str, network: str = Query("sepolia"),
                                 wallet_address: str = Query(None), contract_address: str = Query(None)):
    w3 = get_w3(network)
    return supply_chain_service.read_product(w3, network, product_id,
                                              wallet_address=wallet_address, contract_address=contract_address)


@router.get("/contracts/supply-chain/verify")
async def supply_chain_verify(product_id: str, network: str = Query("sepolia"),
                                wallet_address: str = Query(None), contract_address: str = Query(None)):
    w3 = get_w3(network)
    return supply_chain_service.verify_authenticity(w3, network, product_id,
                                                      wallet_address=wallet_address, contract_address=contract_address)


@router.get("/contracts/supply-chain/source")
async def supply_chain_source():
    """Serves the raw .sol source so the UI can offer a one-click copy for
    customers who want to deploy their own private contract (e.g. via Remix)
    before registering its address through /contracts/supply-chain/registry."""
    path = os.path.join(os.path.dirname(__file__), "..", "blockchain", "contracts", "SupplyChainTraceability.sol")
    try:
        with open(path, "r") as fh:
            source = fh.read()
        return {"filename": "SupplyChainTraceability.sol", "source": source}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────
# CONTRACT REGISTRY — customers register their own private contract from
# the UI, no .env access required. Each wallet's registered contract is
# used automatically for that wallet's register/checkpoint/lookup calls.
# ─────────────────────────────────────────────────────────────────────────
@router.post("/contracts/supply-chain/registry")
async def registry_register(req: RegisterContractRequest):
    w3 = get_w3(req.network)
    if not supply_chain_service.verify_contract_deployed(w3, req.contract_address):
        raise HTTPException(
            status_code=400,
            detail=f"No contract code found at {req.contract_address} on '{req.network}'. "
                   f"Double-check the address and network before registering.",
        )
    try:
        result = contract_registry.register_contract(
            req.wallet_address, req.network, req.contract_address, req.label
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_event("contract_registered", {"status": "success", "network": req.network,
                                       "wallet": req.wallet_address[:10] + "…"})
    return result


@router.get("/contracts/supply-chain/registry")
async def registry_list(wallet_address: str, network: str = Query(None)):
    return {"contracts": contract_registry.list_contracts(wallet_address, network)}


@router.post("/contracts/supply-chain/registry/activate")
async def registry_activate(req: ActivateContractRequest):
    try:
        return contract_registry.set_active(req.wallet_address, req.network, req.contract_address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/contracts/supply-chain/registry")
async def registry_remove(req: RemoveContractRequest):
    contract_registry.remove_contract(req.wallet_address, req.network, req.contract_address)
    return {"status": "removed"}


# ─────────────────────────────────────────────────────────────────────────
# ONE-CLICK DEPLOY — compiles SupplyChainTraceability.sol server-side and
# hands back an unsigned contract-creation tx. No Remix, no copy-paste.
# Registration into the (persistent, file-backed, not session-scoped)
# contract registry happens as a separate step once the frontend has the
# deployed address from the mined receipt — see /contracts/supply-chain/registry.
# ─────────────────────────────────────────────────────────────────────────
@router.post("/contracts/supply-chain/deploy")
async def supply_chain_deploy(req: DeployContractRequest):
    network = normalize_network(req.network)
    try:
        compiled = contract_compiler.compile_supply_chain_contract()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    w3 = get_w3(network)
    from_address = Web3.to_checksum_address(req.from_address)

    intent = {"network": network}  # only used for gas-tier selection defaults
    strategy = evaluate_strategy(intent, from_address, network=network)

    tx = {
        "from": from_address,
        "data": compiled["bytecode"],
        "value": hex(0),
    }
    try:
        gas_estimate = w3.eth.estimate_gas(tx)
    except Exception:
        gas_estimate = 1_500_000  # contract creation is gas-heavy; safe fallback if estimation fails
    tx["gas"] = hex(gas_estimate)
    tx = {**tx, **strategy["fee_fields"]}
    tx["chainId"] = hex(get_network_config(network)["chain_id"])

    log_event("contract_deploy_built", {"status": "success", "network": network, "gas_estimate": gas_estimate})
    return {"tx_params": tx, "strategy": strategy, "abi": compiled["abi"]}