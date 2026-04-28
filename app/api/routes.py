from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.llm.intent_parser import parse_intent
from app.services.intent_engine import build_tx_for_wallet

router = APIRouter()

MISSING_SENTINEL = {"none", "", "null", "unknown", "n/a"}


# ── request / response models ───────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str

class BuildTxRequest(BaseModel):
    intent:       dict          # fully resolved intent fields
    from_address: str           # MetaMask wallet address (supplied by frontend)

class ParseResponse(BaseModel):
    parsed:         dict
    missing_fields: list[str]


# ── helpers ─────────────────────────────────────────────────────────────────

def _detect_missing(parsed: dict) -> list[str]:
    missing = []
    if not parsed.get("action")    or str(parsed["action"]).lower()    in MISSING_SENTINEL:
        missing.append("action")
    if not parsed.get("amount")    or float(parsed.get("amount", 0))   <= 0:
        missing.append("amount")
    if not parsed.get("recipient") or str(parsed["recipient"]).lower() in MISSING_SENTINEL:
        missing.append("recipient")
    if not parsed.get("network")   or str(parsed["network"]).lower()   in MISSING_SENTINEL:
        missing.append("network")
    if not parsed.get("priority")  or str(parsed["priority"]).lower()  in MISSING_SENTINEL:
        missing.append("priority")
    return missing


# ── endpoints ────────────────────────────────────────────────────────────────

@router.post("/parse-intent")
def parse_intent_only(request: PromptRequest):
    """
    Step 1 — Parse natural-language prompt into structured fields.
    Returns the parsed intent AND any fields still missing so the
    frontend can surface a fill-in dialog before asking for a signature.
    No private key is used here.
    """
    try:
        parsed  = parse_intent(request.prompt)
        missing = _detect_missing(parsed)
        return {"parsed": parsed, "missing_fields": missing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/build-tx")
def build_tx(request: BuildTxRequest):
    """
    Step 2 — Build an *unsigned* EIP-1559 transaction from the resolved intent.

    The frontend (MetaMask) signs and broadcasts.
    The backend never sees or stores any private key.

    Returns:
        tx_params  — hex-encoded tx object ready for window.ethereum.request()
        strategy   — gas estimate metadata for display
    """
    try:
        result = build_tx_for_wallet(request.intent, request.from_address)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
