from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.logger import track_active_user

app = FastAPI(title="IntentChain Middleware", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def active_user_tracking(request: Request, call_next):
    # Heartbeat every inbound request (not just business events) so the
    # "active users" metric reflects real traffic, not just successful
    # intent parses. Client IP is the only identifier available without an
    # auth layer — good enough for a live "how many people are poking the
    # app right now" gauge.
    client_ip = request.client.host if request.client else None
    track_active_user(client_ip)
    return await call_next(request)


app.include_router(router)

@app.get("/")
def root():
    return {"message": "IntentChain v3.0 Running", "docs": "/docs"}