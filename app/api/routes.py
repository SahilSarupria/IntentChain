from fastapi import APIRouter
from app.models.intent import IntentRequest
from app.services.intent_engine import process_intent

router = APIRouter()

@router.post("/execute-intent")
def execute_intent(intent: IntentRequest):
    result = process_intent(intent)
    return result