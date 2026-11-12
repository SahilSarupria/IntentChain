# IntentChain v3.0

**Natural-language middleware for blockchain.** Describe what you want in plain
English ("send 0.01 ETH to 0x… fast", "register batch COFFEE-A123 from
Colombia") and IntentChain parses it, prices the gas market in real time, and
hands MetaMask an unsigned transaction to sign. The server never touches a
private key.

```
User prompt → LLM intent parser → gas-tier strategy engine → unsigned tx → MetaMask signs → broadcast
```

## What's in v3.0

**Multi-network** — Ethereum, Sepolia, Polygon, Arbitrum, Optimism, BSC, all
usable out of the box on public RPCs (override with your own key in `.env`).

**Live gas pricing & optimization** (`app/blockchain/gas_oracle.py`) — reads
real `eth_feeHistory` data instead of static gwei guesses, and exposes three
transparent trade-offs:
- **fastest** — optimized purely for speed (next-block inclusion); price is
  not the concern, but it's not wasteful either.
- **standard** — balanced default.
- **cheapest** — optimized purely for cost; explicitly trades speed for a
  lower gas price, and says so in the ETA.

Ask for it in plain language ("send this fast", "cheapest way to send this")
or pick a tier directly in the tx preview card / via `gas_mode`.

**ERC-20 tokens** (`app/blockchain/erc20.py`) — balance reads and unsigned
`transfer`/`approve` calldata for any known token symbol (USDC, USDT, DAI,
WETH, …, editable in `app/config/tokens.json`) or a raw contract address.

**Token balance display** — `GET /wallet/balances` returns native + every
known ERC-20 balance for a wallet on a given network. The UI snapshots
balances right before you sign and diffs them once the tx lands.

**Transaction history** (`app/blockchain/etherscan_client.py`) — `GET
/wallet/history` pulls recent native + token transfers via Etherscan's
unified v2 API (one `ETHERSCAN_API_KEY` covers every supported chain).

**Supply chain & pharma traceability** — a real Solidity contract
(`app/blockchain/contracts/SupplyChainTraceability.sol`) for product
provenance and cold-chain checkpoints:
- `registerProduct` — put a batch/SKU on-chain with name + origin (fair-trade
  coffee, organic produce, luxury goods anti-counterfeiting, etc).
- `logCheckpoint` — log a custody handoff with location, status, and
  temperature (drug batch cold-chain verification).
- `getProduct` / `getCheckpoint` / `verifyAuthenticity` — read the full
  provenance trail for a batch.

Deploy the contract (e.g. via Remix) and set
`SUPPLY_CHAIN_CONTRACT_ADDRESS_<NETWORK>` in `.env`. Until you do, the
read/write endpoints respond with a clear "not deployed yet" message instead
of failing — the rest of the app works either way.

**USD-denominated gas estimates** (`app/services/price_feed.py`) — live
CoinGecko price feed converts a gwei quote into an approximate dollar cost.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY at minimum
uvicorn app.main:app --reload
```

Open `frontend/index.html` in a browser (or serve it statically) with
MetaMask installed. Everything else — networks, gas ticker, balances — is
optional but works better the more of `.env` you fill in.

## Project layout

```
app/
  api/routes.py                  all HTTP endpoints
  llm/intent_parser.py           natural language -> structured intent (Gemini)
  services/
    strategy_engine.py           live gas tiers + USD estimate
    tx_builder.py                dispatches intent -> unsigned tx (native/ERC-20/contract)
    supply_chain_service.py      supply-chain contract read/write helpers
    price_feed.py                CoinGecko USD price feed
    intent_engine.py             glue: strategy + tx_builder
  blockchain/
    rpc.py                       cached Web3 client per network
    gas_oracle.py                EIP-1559 fee-history based gas tiers
    erc20.py                     ERC-20 ABI, balances, transfer/approve builders
    etherscan_client.py          Etherscan v2 unified API client
    contracts/
      SupplyChainTraceability.sol
      supply_chain_abi.py
    executor.py                  legacy shim (native transfers only) — see tx_builder.py
  config/
    networks.py                  chain registry (RPCs, chain IDs, explorers)
    tokens.json                  known ERC-20 token registry, editable
frontend/index.html              chat UI + Insights panel (balances/history/supply chain)
dashboard.py                     legacy Streamlit debug dashboard (secondary, not the main UI)
```

## Roadmap / known gaps

- **Swaps** (`action: "swap"`) are parsed but not executed — routing through
  a DEX aggregator (0x/1inch) is the natural next step.
- The supply-chain contract's access control is a minimal owner/role
  mapping for demo purposes; swap in OpenZeppelin `AccessControl` before
  production use.
- Token/contract-address inputs are trusted as given — there's no allowlist
  beyond `app/config/tokens.json`; always double-check what you're signing.

## Architecture and workflow

IntentChain is a non-custodial natural-language blockchain agent. The backend
interprets the user's request and prepares data or an unsigned transaction;
MetaMask remains responsible for the private key, signature, and broadcast.

### Request lifecycle

```text
User prompt in frontend/index.html
        |
        v
POST /parse-intent
        |
        v
Gemini returns normalized intent JSON
        |
        +--> knowledge: show the LLM's explanation
        |
        +--> read: POST /execute-read and query RPC/Etherscan/contracts
        |
        +--> deploy: compile contract, prepare creation tx, MetaMask signs
        |
        +--> transaction: strategy engine -> tx builder -> unsigned tx
                                                     |
                                                     v
                                      MetaMask eth_sendTransaction
                                                     |
                                                     v
                                           signed transaction on-chain
```

For a transaction request, `app/api/routes.py` checks which fields are needed
for the selected action. `app/services/intent_engine.py` calls
`app/services/strategy_engine.py` for the network, gas tier, fee fields, gas
estimate, and approximate USD cost. `app/services/tx_builder.py` dispatches to
the correct builder: a native transfer, ERC-20 transfer/approval, or
supply-chain contract call. The result contains `tx_params`, which the
frontend passes to MetaMask. The backend does not sign transactions.

The frontend also performs a client-side chain check before signing. It asks
MetaMask to switch to the transaction's chain, renders fee alternatives, and
shows any best-effort `eth_call` simulation warning returned by the backend.

### Four application paths

- **Transaction:** native transfers, bridge-style native transfers, ERC-20
  transfers/approvals, product registration, and checkpoint logging. These
  require a signature.
- **Read:** balance checks, transaction history, and product verification. These
  query chain/API data and do not require a signature.
- **Knowledge:** general blockchain questions are answered by the LLM without
  building a transaction.
- **Deploy:** the supply-chain Solidity contract is compiled server-side, an
  unsigned contract-creation transaction is returned, and MetaMask deploys it.

`swap` is parsed but intentionally rejected by the transaction builder because
no DEX aggregator is connected yet.

### Current versus legacy code

The current transaction path is:

```text
app/api/routes.py
  -> app/services/intent_engine.py
  -> app/services/strategy_engine.py
  -> app/services/tx_builder.py
  -> MetaMask in frontend/index.html
```

`app/blockchain/executor.py` is now only a backwards-compatible wrapper around
`tx_builder.py`. The original backend-signing design is described in
`LLM_CONTEXT_HANDOFF.md`, but it is historical and should not be treated as the
current security model. `dashboard.py` is a legacy Streamlit debug/demo UI;
`frontend/index.html` is the primary interface.

## Claude Code context guide

Do not pass the entire repository for every task. Start with the smallest set
below and add adjacent files only when the change crosses that boundary.

### Always provide for backend workflow changes

- `README.md`
- `app/main.py`
- `app/api/routes.py`
- `app/llm/intent_parser.py`
- `app/services/intent_engine.py`
- `app/services/strategy_engine.py`
- `app/services/tx_builder.py`
- `app/config/networks.py`
- `requirements.txt`

Also provide `frontend/index.html` whenever the API response shape, wallet
signing behavior, network switching, or visible transaction preview changes.

### Add these files by feature

**Native transfers and gas:**

- `app/blockchain/rpc.py`
- `app/blockchain/gas_oracle.py`
- `app/services/price_feed.py`
- `frontend/index.html`

**ERC-20 tokens:**

- `app/blockchain/erc20.py`
- `app/config/tokens.json`
- `app/blockchain/rpc.py`
- `frontend/index.html`

**Balances and transaction history:**

- `app/api/routes.py`
- `app/blockchain/erc20.py`
- `app/blockchain/etherscan_client.py`
- `app/config/networks.py`
- `frontend/index.html`

**Supply-chain registration, checkpoints, or verification:**

- `app/services/supply_chain_service.py`
- `app/services/tx_builder.py`
- `app/blockchain/contracts/SupplyChainTraceability.sol`
- `app/blockchain/contracts/supply_chain_abi.py`
- `app/services/contract_registry.py`
- `app/data/contract_registry.json` only when investigating existing local
  registrations, never as a general reusable context file
- `frontend/index.html`

**One-click contract deployment:**

- `app/api/routes.py`
- `app/blockchain/contract_compiler.py`
- `app/blockchain/contracts/SupplyChainTraceability.sol`
- `app/services/contract_registry.py`
- `frontend/index.html`
- `requirements.txt`

**Wallet/signing or MetaMask issues:**

- `frontend/index.html`
- `app/api/routes.py`
- `app/services/tx_builder.py`
- `app/config/networks.py`
- `app/blockchain/rpc.py`

**Observability or health endpoints:**

- `app/core/logger.py`
- `app/api/routes.py`
- `frontend/index.html` if the displayed status or metrics change

### Files to exclude from Claude Code context

- `.env` and any private keys, seed phrases, API keys, or RPC URLs containing
  credentials
- `app/data/contract_registry.json` unless the task specifically concerns its
  current contents
- large logs, local caches, virtual environments, and generated compiler files
- `LLM_CONTEXT_HANDOFF.md` for current implementation work unless historical
  comparison is needed
- `dashboard.py` unless the task explicitly targets the legacy Streamlit UI

When asking Claude Code for an improvement, include the desired behavior, the
relevant endpoint or UI action, an example input and expected output, and the
network used for testing. Mention whether the change must preserve the
non-custodial rule: the server must never receive or use a private key.
