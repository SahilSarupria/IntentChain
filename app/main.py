from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.routes import router



app = FastAPI(title="IntentChain Middleware")

app.include_router(router)

@app.get("/")
def root():
    return {"message": "IntentChain Phase 1 Running"}