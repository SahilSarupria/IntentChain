from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
import logging
import time
import os

from app.llm.intent_parser import parse_intent
from app.services.intent_engine import build_tx_for_wallet
from app.core.logger import get_logs, clear_logs, log_event, get_stats
from app.blockchain.rpc import get_w3, is_connected
from app.blockchain import erc20, etherscan_client
from app.blockchain.gas_oracle import compute_gas_strategies
from app.services import supply_chain_service
from app.services import contract_registry
from app.config.networks import list_networks, normalize_network, get_network_config

router = APIRouter()
logger = logging.getLogger(__name__)

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
}
DEFAULT_REQUIRED = ["action", "amount", "recipient", "network", "priority"]


class PromptRequest(BaseModel):
    prompt: str

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
        missing = _detect_missing(parsed)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("parse_intent", {
            "status": "success", "latency_ms": latency_ms,
            "parsed_action": parsed.get("action"), "parsed_network": parsed.get("network"),
            "missing_fields": missing, "prompt_preview": request.prompt[:80],
        })
        return {"parsed": parsed, "missing_fields": missing, "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log_event("parse_intent", {"status": "error", "error": str(exc), "latency_ms": latency_ms})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        logger.warning("/build-tx validation error: %s", exc)
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