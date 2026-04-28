from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.llm.intent_parser import parse_intent
from app.models.intent import IntentRequest
from app.services.intent_engine import process_intent

router = APIRouter()

MISSING_SENTINEL = {"none", "", "null", "unknown", "n/a"}

class PromptRequest(BaseModel):
    prompt: str

class StructuredIntentRequest(BaseModel):
    action: str
    amount: float
    recipient: str
    network: str
    priority: str

def _detect_missing(parsed: dict) -> list[str]:
    """Return list of field names that are missing or placeholder values."""
    missing = []
    if not parsed.get("action") or str(parsed["action"]).lower() in MISSING_SENTINEL:
        missing.append("action")
    if not parsed.get("amount") or float(parsed.get("amount", 0)) <= 0:
        missing.append("amount")
    if not parsed.get("recipient") or str(parsed["recipient"]).lower() in MISSING_SENTINEL:
        missing.append("recipient")
    if not parsed.get("network") or str(parsed["network"]).lower() in MISSING_SENTINEL:
        missing.append("network")
    if not parsed.get("priority") or str(parsed["priority"]).lower() in MISSING_SENTINEL:
        missing.append("priority")
    return missing

@router.post("/parse-intent")
def parse_intent_only(request: PromptRequest):
    """Parse a natural-language prompt and return structured fields + any missing ones."""
    try:
        parsed = parse_intent(request.prompt)
        missing = _detect_missing(parsed)
        return {"parsed": parsed, "missing_fields": missing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.post("/execute-intent-structured")
def execute_intent_structured(request: StructuredIntentRequest):
    """Execute a fully-specified intent (all fields already validated by the caller)."""
    try:
        intent = IntentRequest(
            action=request.action,
            amount=request.amount,
            recipient=request.recipient,
            network=request.network,
            priority=request.priority,
        )
        return process_intent(intent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.post("/execute-intent")
def execute_intent(request: PromptRequest):
    try:
        parsed_intent = parse_intent(request.prompt)
        structured_intent = IntentRequest(**parsed_intent)
        return process_intent(structured_intent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc