# The Firm — Autonomous Trading Intelligence System

I built this to trade prediction markets algorithmically and actually learn from what works and what doesn't. It runs on a Raspberry Pi 24/7, deploys real money, and has 8 specialized agents handling everything from Kalshi economic data trades to congressional stock disclosure tracking to daily temperature markets.

The thing I'm most proud of is the eval framework — after every trade resolves, a second LLM independently reviews the decision process. Not just whether we won or lost, but whether the reasoning was sound. A good process can lose. A bad process can win. Those are different things and tracking the difference is the only way to actually get better.

**The parts worth looking at:**
- `bots/rag_store.py` — RAG over 206 congressional trade disclosures. Multi-query embedding search with reranking, ChromaDB + sentence-transformers running locally on the Pi
- `bots/llm_client.py` — routes between Grok, Claude, and GPT-4o depending on the task. Has a consensus mode where both models have to agree before a high-stakes trade goes through
- `bots/eval_framework.py` — the LLM process reviewer mentioned above
- `bots/economics.py` — the main trading engine. Every real loss we've taken has added a new hard gate to this file. The BTC story below is a good example of how that works

---

## Real Results

> **CPI Shelter trade — April 10, 2026**
> Model: 40% probability that CPI shelter would print above 4.24%. Market implied 15%. Edge: +25 points. Entered YES on `KXSHELTERCPI-26APR10-T424.0`. BLS printed 424.069 bps. Resolved YES. **+275% return on position.**

> **GDP Q1 2026 — Three concurrent NO positions**
> GDPNow tracking 1.24% annualized vs thresholds of 1.0%, 1.5%, 2.0%, 2.5%, 3.0%. Positions open at time of writing.

> **BTC intraday loss — April 2026**
> Entered a NO position 9 minutes before close with spot only 0.05% from the threshold. Direction was right but we got caught by normal volatility in the final minutes. Took the loss, figured out exactly what went wrong, and added two hard gates to the code: `CRYPTO_MIN_MINUTES_TO_CLOSE = 30` and `CRYPTO_MIN_BUFFER_PCT = 0.005`. Now every crypto or commodity trade has to clear both before it executes. That's basically how this whole system evolves — lose money, understand why, make it impossible to do the same thing twice.

---

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │              firm.py                 │
                    │         Master Orchestrator          │
                    │                                      │
                    │  Thread-safe scheduler loop          │
                    │  importlib dynamic agent loading     │
                    │  RotatingFileHandler logging         │
                    │  SIGTERM/SIGINT clean shutdown       │
                    └──────────────┬──────────────────────┘
                                   │
          ┌──────────┬─────────────┼──────────────┬──────────────┐
          │          │             │              │              │
    ┌─────▼──┐  ┌────▼───┐  ┌─────▼──┐  ┌───────▼──┐  ┌────────▼──┐
    │Kalshi  │  │Weather │  │Sports  │  │Congress  │  │ Options   │
    │Exec    │  │Markets │  │Betting │  │Intel     │  │  Desk     │
    │Engine  │  │Scanner │  │Engine  │  │+ RAG     │  │ (Jordan)  │
    └────────┘  └────────┘  └────────┘  └──────────┘  └───────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │           Intelligence Layer           │
               │  LLM Reasoning (llm_client.py)         │
               │  RAG Pipeline (rag_store.py)           │
               │  Eval Framework (eval_framework.py)    │
               └───────────────────┬───────────────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │        Dashboard (Next.js + FastAPI)   │
               │   7 tabs: Economics | Weather | Sports │
               │   Intelligence | Portfolio | System    │
               └───────────────────────────────────────┘
```

---

## Quick Start

```bash
git clone https://github.com/cwolson03/the-firm.git
cd the-firm

cp .env.example .env
# Fill in: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH, FRED_API_KEY,
#          GROK_API_KEY, CLAUDE_API_KEY, OPENAI_API_KEY,
#          Discord bot tokens (one per agent)

pip install -r requirements.txt

# Run all agents
python3 firm.py

# Dry-run (no orders placed, no Discord posts)
python3 firm.py --dry-run --once

# Run a single agent
python3 firm.py --bot economics
```

**firm.py lives at the project root. Agents are in `bots/`.**

---

## firm.py — The Orchestrator

**Live schedule:**

| Agent | File | Purpose | Interval |
|-------|------|---------|---------|
| Economics | `bots/economics.py` | Kalshi prediction market execution | 120 min |
| Congressional | `bots/congressional.py` | Congressional trade tracker + RAG | 240 min |
| Weather | `bots/weather.py` | Temperature market scanner (19 cities) | 3 min |
| Sports | `bots/sports.py` | Sports stink-bid strategy (S1/S2/S3) | 15 min |
| Options | `bots/options.py` | SPY 0DTE price monitor | 15 min |
| System | `bots/supervisor.py` | Health monitor (6 checks) | 30 min |

```bash
python3 firm.py --status     # Print scheduler status
python3 firm.py --dry-run    # Simulate all agents — no orders, no posts
python3 firm.py --once       # Run all agents once then exit
python3 firm.py --bot congressional # Run one agent standalone
```

---

## Intelligence Layer

### RAG Pipeline (`bots/congressional.py` + `bots/rag_store.py`)

Tracks STOCK Act disclosures from 18 members of Congress. When a high-scoring member trades, the Congressional agent:

1. Embeds the disclosure query using `all-MiniLM-L6-v2` (local, no API cost)
2. Runs multi-query retrieval across 206 stored disclosures in ChromaDB
3. Reranks by semantic similarity + recency + member score weight
4. Feeds top-5 retrieved docs + member profile to Grok
5. Returns a structured trade brief: thesis, confidence, risks

**Scoring model (0–100):** Track record (30) + Trade size (20) + Committee relevance (20) + Portfolio overlap (15) + Macro alignment (15)

**Top tracked members by score:** Nancy Pelosi (28), Michael McCaul (26), Ro Khanna (25), Josh Gottheimer (24), Dan Crenshaw (22)

### LLM Reasoning (`bots/llm_client.py`)

Three-model dispatcher with consensus checking and graceful degradation.

| Model | Role |
|-------|------|
| Grok (xAI) | Real-time signals — live news, sports, market data |
| Claude Haiku | Deep reasoning, structured output, eval critique |
| GPT-4o Mini | Shadow consensus on high-conviction trades (>30pt edge) |

LLM failure defaults to `go=True` — the LLM is advisory, the quant model is authoritative. An LLM outage never stops trading.

### Eval Framework (`bots/eval_framework.py`)

After every trade resolves, an LLM critiques the **process quality** — independent of outcome:

```json
{
  "process_score": 3,
  "edge_quality": "weak",
  "lesson": "Never enter crypto range market inside 30 min to close.",
  "avoid_next_time": "Spot within 0.5% of threshold = insufficient buffer."
}
```

The BTC loss above scored 3/10 on process. The CPI shelter win scored 9/10. Process and outcome are different things — this is what makes the eval framework useful.

---

## Execution Engine (`bots/economics.py`)

**5 + 1 gate model.** Every trade clears all six or doesn't execute:

1. Price edge ≥ 18¢ above fee structure
2. Order book pressure (bid/ask imbalance)
3. Market velocity (SPIKE >10¢/min → immediate re-score)
4. Whale accumulation (≥50 contracts, ≥$200 notional in last hour)
5. Time-to-close penalty (exponential decay past 14 days)
6. **LLM Gate** — Grok reviews ECONOMIC_DATA trades with >20pt edge. Trades with >30pt edge require Grok + Claude consensus.

**Data sources:** Atlanta Fed GDPNow · Cleveland Fed CPI nowcast · CoinGecko · Stooq commodities

---

## Weather Scanner (`bots/weather.py`)

Scans Kalshi's `KXHIGH*` series across 19 cities for daily high temperature mispricings.

- **Forecasting:** Tomorrow.io → Open-Meteo → NOAA gridpoint (fallback chain)
- **Edge calculation:** Normal CDF vs market's strike type
- **Bias calibration:** 7-day rolling per-city correction from resolved trades
- **Source tracking:** Every paper trade logs which model was used (`forecast_source` field) for post-hoc calibration analysis

---

## Dashboard

FastAPI backend + Next.js frontend. 7 tabs covering every agent domain.

```bash
# Backend (runs as stratton-api.service on Atlas)
cd dashboard/api
pip install fastapi uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend
cd dashboard/web
cp .env.example .env.local
# Set NEXT_PUBLIC_API_URL=http://your-api-host:8000
npm install && npm run dev
```

**Fixture mode** (for demo without live API):
```bash
NEXT_PUBLIC_USE_FIXTURES=true npm run dev
```
Data fixtures in `dashboard/web/public/data/` are real snapshots from the live system.

---

## Setup — Full `.env` reference

```bash
# Kalshi (required for live trading)
KALSHI_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=

# LLM providers
GROK_API_KEY=
CLAUDE_API_KEY=
OPENAI_API_KEY=

# Data sources
FRED_API_KEY=
TOMORROW_API_KEY=
ODDS_API_KEY=

# Discord (one bot token per agent)
DONNIE_TOKEN=
WEATHER_TOKEN=
BRAD_TOKEN=
RUGRAT_TOKEN=
JORDAN_TOKEN=
STRATTON_TOKEN=
```

---

## Stack

| Component | Purpose |
|-----------|---------|
| Python 3.11+ | All agents — threading, no async |
| ChromaDB | Vector database for RAG (local, persistent) |
| sentence-transformers | `all-MiniLM-L6-v2` embeddings — runs locally, no API cost |
| Grok (xAI) | Real-time signals and market intelligence |
| Claude (Anthropic) | Reasoning, eval critique, structured output |
| GPT-4o (OpenAI) | Shadow consensus on high-stakes trades |
| FastAPI + uvicorn | Dashboard API |
| Next.js 15 + Tailwind | Dashboard frontend |
| FRED API | GDPNow, CPI, PCE data series |
| Tomorrow.io | Weather forecasts (500/day free tier) |
| The Odds API | Sportsbook probabilities |
| Kalshi API | RSA-PSS auth, prediction market execution |

---

*Built by Cody Olson.*
