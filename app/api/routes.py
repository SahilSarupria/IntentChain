from fastapi import APIRouter
from pydantic import BaseModel
from app.llm.intent_parser import parse_intent

router = APIRouter()

class PromptRequest(BaseModel):
    prompt: str

@router.post("/execute-intent")
def execute_intent(request: PromptRequest):
    intent = parse_intent(request.prompt)
    return intent