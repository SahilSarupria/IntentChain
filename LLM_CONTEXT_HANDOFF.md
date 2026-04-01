# IntentChain Project Context Handoff

## 1. Project Identity

- Project name: IntentChain
- Current stated purpose (from README): AI-powered middleware for intent-aware, domain-agnostic blockchain orchestration.
- Current maturity: early prototype / Phase 1 style implementation with a working request-to-transaction pipeline.

## 2. Technology Stack

- Backend framework: FastAPI
- API server runtime: Uvicorn (declared dependency)
- Blockchain client: Web3.py
- Environment configuration: python-dotenv
- Validation/modeling: Pydantic
- Frontend/demo UI: Streamlit
- HTTP client in dashboard: requests

Dependencies are declared in `requirements.txt` and include:

- fastapi
- uvicorn
- web3
- python-dotenv
- pydantic
- streamlit
- requests

## 3. Repository and Branch State

- This is a Git repository.
- Current branch at time of handoff: `sahil`
- HEAD commit: `ab1ddd9`
- Recent commits observed:
  - `ab1ddd9`: Add pydantic, streamlit, and requests to requirements
  - `494e7fa`: Initial commit - Phase 1 v0.1 (JSON to Execution on testnet implementation)
  - `77fde82`: Initial commit
- Observed unstaged modification: `app/blockchain/executor.py`

## 4. Implemented Runtime Architecture

The implementation follows a linear orchestration flow:

1. Client submits an intent payload to FastAPI endpoint `POST /execute-intent`.
2. API validates payload against a Pydantic model (`IntentRequest`).
3. Intent engine normalizes intent into a structured internal dictionary.
4. Strategy engine produces a simple gas strategy object.
5. Blockchain executor signs and broadcasts an ETH transfer transaction via Web3.
6. API returns a combined response containing input intent, strategy, and execution result.

### 4.1 Backend Entry Point

- File: `app/main.py`
- Creates FastAPI app with title: "IntentChain Middleware"
- Includes API router from `app/api/routes.py`
- Root health-like endpoint: `GET /` returns `{ "message": "IntentChain Phase 1 Running" }`

### 4.2 API Layer

- File: `app/api/routes.py`
- Defines APIRouter with endpoint `POST /execute-intent`
- Request body type: `IntentRequest` from `app/models/intent.py`
- Delegates business logic to `process_intent` in `app/services/intent_engine.py`

### 4.3 Data Contract (Intent Model)

- File: `app/models/intent.py`
- Pydantic model fields:
  - `action: str`
  - `amount: float`
  - `recipient: str`
  - `network: str = "sepolia"`
  - `priority: str = "low_cost"`

Current validation is type-level only; there are no additional semantic validators (for example: address checksum validation, allowed action enum, non-negative amount constraints).

### 4.4 Intent Engine

- File: `app/services/intent_engine.py`
- Function: `process_intent(intent)`
- Responsibilities:
  - Constructs `structured_intent` dict from the request model
  - Calls `evaluate_strategy(structured_intent)`
  - Calls `execute_transaction(structured_intent, strategy)`
  - Returns aggregated response:
    - `intent`
    - `strategy`
    - `transaction_result`

### 4.5 Strategy Engine

- File: `app/services/strategy_engine.py`
- Function: `evaluate_strategy(intent)`
- Current strategy logic:
  - Attempts `eth_estimateGas` for transfer payload (`to`, `from`, `value`)
  - Falls back to `gas_estimate = 21000` when provider/env is unavailable
  - Maps `priority` to string strategy:
    - `low_cost` -> `standard`
    - `fast` -> `high`
    - default -> `standard`
- Returns:
  - `gas_estimate`
  - `gas_price_strategy`

This is a static heuristic and not yet chain-state-aware.

### 4.6 Blockchain Executor

- File: `app/blockchain/executor.py`
- Loads env vars via `load_dotenv()`:
  - `INFURA_URL`
  - `PRIVATE_KEY`
  - `WALLET_ADDRESS`
- Initializes provider:
  - `w3 = Web3(Web3.HTTPProvider(INFURA_URL))`
- Main function: `execute_transaction(intent, strategy)`
- Current behavior:
  - Gets account nonce from wallet address
  - Reads latest block and base fee (`baseFeePerGas`)
  - Derives priority fee from intent priority
  - Builds tx dict with:
    - nonce
    - to (`intent["recipient"]`)
    - value (`intent["amount"]` in ETH converted to wei)
    - gas (`strategy["gas_estimate"]`)
    - `maxFeePerGas = baseFeePerGas + priorityFee`
    - `maxPriorityFeePerGas = priorityFee`
    - `chainId`
  - Signs using private key
  - Broadcasts raw transaction
  - Returns `{"tx_hash": "..."}` on success
  - Returns `{"error": "..."}` on failure (broad exception handler)

Important implementation note:

- There are currently multiple debug print statements, including environment/connectivity diagnostics and transaction preflight information.

## 5. Dashboard / Demo UX

- File: `dashboard.py`
- Streamlit UI presents 4 module cards:
  - Intent Engine
  - Strategy Engine
  - Risk Evaluator
  - Blockchain Executor
- User enters natural language intent in text field, but current payload is hardcoded mock JSON (NLP parsing is not implemented yet).
- Calls backend endpoint at `http://127.0.0.1:8000/execute-intent`.
- Simulates staged pipeline progression with delays and visual status transitions.
- Displays tx hash and Etherscan link if transaction succeeds.

## 6. Effective API Contract (Observed)

### Request

`POST /execute-intent`

```json
{
  "action": "transfer",
  "amount": 0.0001,
  "recipient": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
  "network": "sepolia",
  "priority": "low_cost"
}
```

### Success-style Response Shape

```json
{
  "intent": {
    "action": "transfer",
    "amount": 0.0001,
    "recipient": "0x...",
    "network": "sepolia",
    "priority": "low_cost"
  },
  "strategy": {
    "gas_estimate": 21000,
    "gas_price_strategy": "standard"
  },
  "transaction_result": {
    "tx_hash": "<hex_without_0x_prefix_in_current_ui_rendering_logic>"
  }
}
```

### Error-style Response Shape

```json
{
  "intent": { "...": "..." },
  "strategy": { "...": "..." },
  "transaction_result": {
    "error": "<exception_message>"
  }
}
```

## 7. Known Gaps / Technical Debt

1. Natural language intent parsing is currently mocked in the dashboard.
2. Priority fee policy is static and heuristic-based (does not use mempool pressure or historical fee percentiles).
3. Network selection exists in schema but transaction builder does not enforce chain-specific configuration beyond provider.
4. Transaction dict does not explicitly set `chainId`.
5. Fee policy still has hardcoded gwei tiers for priority levels.
6. Broad exception handling makes error taxonomy coarse.
7. Security hardening is minimal (sensitive logging and key handling concerns).
8. No automated tests are present in the visible structure.
9. No explicit retry/backoff/circuit-breaker behavior for RPC failures.
10. No persistence/audit trail for submitted intents and execution outcomes.

## 8. Environment and Runtime Assumptions

The executor assumes these environment variables are present and valid:

- `INFURA_URL`: HTTPS RPC endpoint (expected for Sepolia in current default flow)
- `PRIVATE_KEY`: signer private key used for transaction signing
- `WALLET_ADDRESS`: sender account address

If these are missing or malformed, the transaction path fails and returns an error payload from exception handling.

## 9. How To Start (Current Practical Flow)

1. Install Python dependencies from `requirements.txt`.
2. Configure `.env` with RPC URL, wallet address, and private key.
3. Run FastAPI app (for example with Uvicorn) so endpoint is available at localhost:8000.
4. Run Streamlit dashboard (`dashboard.py`).
5. Execute a demo intent from UI; pipeline stages animate and backend response is rendered.

## 10. Recommended Prompt Seed For Next LLM

Use this block as initial instruction context for a new model:

"You are continuing development on IntentChain, a FastAPI + Streamlit prototype that translates structured user intent into EVM transaction execution on Sepolia via Web3.py. The current code path is API-driven (`POST /execute-intent`), with a Pydantic `IntentRequest`, static strategy heuristic (`gas_estimate=21000`, priority-to-string mapping), and a blockchain executor that signs/sends transactions with env-configured Infura and private key. Dashboard NLP is currently mocked by a hardcoded payload, and execution observability is mostly print-based. Continue from this baseline without rewriting architecture unless requested. Prioritize incremental improvements: validation, security, dynamic gas/chain handling, reliable error taxonomy, and tests." 

## 11. File Map (Primary Code Units)

- `app/main.py`: FastAPI app bootstrap
- `app/api/routes.py`: REST endpoint definition
- `app/models/intent.py`: request schema
- `app/services/intent_engine.py`: orchestration logic
- `app/services/strategy_engine.py`: strategy derivation
- `app/blockchain/executor.py`: transaction build/sign/send
- `dashboard.py`: Streamlit demonstration frontend
- `requirements.txt`: dependency manifest
- `README.md`: brief project statement
