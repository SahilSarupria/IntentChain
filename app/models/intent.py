from pydantic import BaseModel

class IntentRequest(BaseModel):
    action: str
    amount: float
    recipient: str
    network: str = "sepolia"
    priority: str = "low_cost"