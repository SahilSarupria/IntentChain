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
