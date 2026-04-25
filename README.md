# The Firm — Autonomous Trading Intelligence System

A multi-agent Python system running 24/7 on a Raspberry Pi 5. One command (`python3 firm.py`) launches every agent. Agents scan live markets, identify statistical mispricings using quantitative models and LLM reasoning, execute trades autonomously within hard risk guardrails, and monitor positions around the clock.

**Live since April 2026. Real capital deployed on [Kalshi](https://kalshi.com).**

---

## Real Results

> **CPI Shelter trade — April 10, 2026**
> Donnie modeled a 40% probability that CPI shelter would print above 4.24%. Kalshi's market implied only 15%. Edge: +25 points. Donnie entered YES on `KXSHELTERCPI-26APR10-T424.0`. BLS printed 424.069 bps. Market resolved YES.
> **+275% return on position in a single trade.**

> **GDP Q1 2026 — Three concurrent NO positions**
> With Atlanta Fed GDPNow tracking at 1.24% annualized, Donnie entered NO on `KXGDP-26APR30-T2.0`, `T2.5`, and `T3.0`. Thesis: tariff drag + net export deterioration make sub-2% near-certain. Positions held at time of writing.

> **Post-trade analysis — BTC loss (April 2026)**
> A NO position entered 9 minutes before close with spot only 0.05% from threshold. Model was right directionally; execution failed — insufficient time buffer. This triggered `CRYPTO_MIN_MINUTES_TO_CLOSE = 30` and `CRYPTO_MIN_BUFFER_PCT = 0.005` as mandatory gates. Bad calls get logged and patched.

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
          │          │             │              │              │
          └──────────┴─────────────┴──────────────┴──────────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │           Intelligence Layer           │
               │                                        │
               │  LLM Reasoning    RAG Pipeline         │
               │  Grok + Claude    ChromaDB             │
               │  + GPT-4o         206 disclosures      │
               │  (llm_client.py)  (rag_store.py)       │
               └───────────────────┬───────────────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │           shared_state.json            │
               │    Every agent writes status here      │
               │    Dashboard API reads from here       │
               └───────────────────┬───────────────────┘
                                   │
               ┌───────────────────▼───────────────────┐
               │        Dashboard (Next.js + FastAPI)   │
               │   7 tabs: Economics | Weather | Sports │
               │   Intelligence | Portfolio | System    │
               └───────────────────────────────────────┘
```

---

## firm.py — The Orchestrator

Every agent runs through `firm.py`. One process. One command.

```bash
python3 firm.py                   # Full startup — all agents, Discord announcement
python3 firm.py --bot donnie      # Run Kalshi execution engine standalone
python3 firm.py --bot rugrat      # Run congressional scanner standalone
python3 firm.py --scan weather    # Trigger weather scanner immediately
python3 firm.py --once            # Run all scheduled agents once then exit
python3 firm.py --dry-run         # Full simulation — no Discord posts, no orders
python3 firm.py --status          # Print live scheduler status and exit
```

**Live schedule:**
| Agent | Purpose | Interval |
|-------|---------|---------|
| Weather (weather.py) | Temperature market scanner | 3 min |
| Sports (brad.py) | Sports stink-bid strategy | 15 min |
| Options (jordan.py) | SPY 0DTE price monitor | 15 min |
| Supervisor (supervisor.py) | System health monitor | 30 min |
| Kalshi (donnie_v2.py) | Prediction market execution | 120 min |
| Congressional (rugrat.py) | Congressional trade tracker | 240 min |

---

## Repo Structure

```
the-firm/
├── firm.py                  # Master orchestrator — run this
├── bots/
│   ├── donnie_v2.py         # Kalshi prediction market execution engine
│   ├── weather.py           # Temperature market scanner (19 cities)
│   ├── brad.py              # Sports stink-bid strategy (S1/S2/S3)
│   ├── rugrat.py            # Congressional trade monitor + RAG pipeline
│   ├── jordan.py            # SPY 0DTE options desk
│   ├── mark_hanna.py        # Macro research analyst (on-demand)
│   ├── chester.py           # Crypto whale tracker (in development)
│   ├── supervisor.py        # System health monitor (6 checks)
│   ├── llm_client.py        # Multi-model LLM: Grok + Claude + GPT-4o
│   ├── rag_store.py         # ChromaDB vector store (206 disclosures)
│   ├── rag_ingest.py        # RAG bootstrap + incremental update
│   ├── eval_framework.py    # Trade evaluation + LLM process scoring
│   └── shared_context.py    # Shared state writer (all agents)
├── dashboard/
│   ├── api/main.py          # FastAPI backend (8 endpoints)
│   └── web/                 # Next.js frontend (7 tabs)
├── tools/
│   ├── backtest.py          # Strategy backtester
│   └── strategy_review.py   # Weather experiment analyzer
├── data/
│   └── shared_state.json    # Live context layer (all agents write here)
└── logs/
    └── firm.log             # Rotating log (5MB, 3 backups)
```

---

## Intelligence Layer

### LLM Reasoning (llm_client.py)

Every significant decision gets an independent LLM review before execution or alert.

| Model | Role | Used By |
|-------|------|---------|
| **Grok (xAI)** | Real-time signals, live news, sports intelligence | Donnie Gate 6, Brad S1, Rugrat alerts, Jordan exits |
| **Claude Haiku** | Deep analysis, structured reasoning, eval critique | Eval framework, Rugrat brief |
| **GPT-4o Mini** | Shadow check on high-conviction trades | Available for consensus checks |

**Multi-model consensus:** When models disagree on go/no-go, confidence is downgraded. LLM failure never blocks execution — graceful degradation hardwired throughout.

---

## Kalshi Execution Engine (donnie_v2.py)

**5 + 1 gate model.** Every trade candidate must clear all six gates:

1. **Price edge** — minimum 18¢ directional edge above Kalshi's fee structure
2. **Order book pressure** — bid/ask imbalance analysis
3. **Market velocity** — price acceleration tracking; SPIKE >10¢/min triggers re-score
4. **Whale accumulation** — large trade clustering (≥50 contracts, ≥$200 notional)
5. **Time-to-close penalty** — exponential confidence decay past 14 days
6. **LLM Gate** — Grok reviews ECONOMIC_DATA trades with >20pt edge before execution

**Data models:**
- **GDPNow** (Atlanta Fed via FRED) → GDP threshold markets
- **Cleveland Fed CPI nowcast** → CPI/PCE markets
- **CoinGecko** → BTC/ETH intraday ranges
- **Stooq** → Gold, crude, commodity markets

**Market tiers:**

| Class | Score Multiplier | Examples |
|-------|-----------------|---------|
| `ECONOMIC_DATA` | 3.0× | CPI, NFP, FOMC, GDP, PCE |
| `WEATHER` | 2.5× | Temperature markets |
| `COMMODITY` | 2.5× | Gold, oil, silver |
| `CRYPTO_SHORT` | 2.0× | BTC/ETH intraday |
| `JUNK` | 0.0× | Excluded entirely |

---

## Congressional Intelligence + RAG (rugrat.py + rag_store.py)

Tracks STOCK Act disclosures from 18 members of Congress. When a high-scoring member makes a significant trade, Rugrat retrieves historical context via RAG and generates an LLM brief.

**RAG Pipeline:**
1. New disclosure detected
2. Multi-query embedding search across 206 stored disclosures
3. Retrieve top 5 semantically relevant prior trades + member profile
4. Feed context to Grok → structured trade brief
5. Post to Discord with confidence + risks

**Scoring model (0–100):** Track record (30) + Trade size (20) + Committee relevance (20) + Portfolio overlap (15) + Macro alignment (15)

**Top tracked members:** Nancy Pelosi (28/30), Michael McCaul (26), Ro Khanna (25), Josh Gottheimer (24), Dan Crenshaw (22)

**The pitch:** Replace STOCK Act disclosures with any institutional knowledge base — ATS contacts, deal histories, relationship graphs — and it's the same architecture.

---

## Weather Scanner (weather.py)

Scans Kalshi's `KXHIGH*` series (19 cities) for daily high temperature mispricings.

**Three-source forecasting:** Tomorrow.io (primary) → Open-Meteo (fallback) → NOAA gridpoint
**990 paper trades since April 2026 — 35.6% win rate**
**Source tagged per trade** for calibration: `forecast_source` field tracks Tomorrow.io vs Open-Meteo

---

## Sports Engine (brad.py)

Stink-bid strategy across Kalshi sports markets. Three parallel strategies:

| Strategy | Discount | Capital Cap |
|----------|---------|------------|
| S1 — Live game winners | 20–25% | 12% |
| S2 — Spread/props | 20% | 8% |
| S3 — Tournament outrights | 35% | 5% |

**Intelligence:** The Odds API (sportsbook cross-check) + Grok xAI (injury/sharp signals within 6hr of tip-off)

---

## Options Desk (jordan.py)

SPY 0DTE monitoring. Tracks options positions with price targets, polls live prices every 15 minutes during market hours, fires alerts when targets are hit or approached.

**Time-based alerts:** Morning brief (9:30 ET) → 2PM warning → 3PM final hour → 3:45PM last chance → EOD sweep (4:15PM)

---

## Eval Framework (eval_framework.py)

After every trade resolves, an LLM critiques the decision quality — not just the outcome.

```json
{
  "process_score": 8,
  "edge_quality": "strong",
  "what_worked": "Cleveland Fed nowcast was predictive signal",
  "what_to_improve": "Position sizing was conservative given confidence",
  "lesson": "Add momentum confirmation for CPI components",
  "avoid_next_time": "Don't enter crypto markets within 30 min of close"
}
```

Weekly health report synthesizes patterns across all resolved trades.

---

## System Monitor (supervisor.py)

Runs every 30 minutes. Six checks, posts to Discord on anomaly (2-hour cooldown).

| Check | Condition |
|-------|-----------|
| Service health | stratton-firm.service is active |
| Log error rate | Errors in last 30 min |
| Kalshi balance | Balance vs baseline |
| Resting orders | No unexpected live orders |
| Paper mode | brad.py and weather.py paper flags intact |
| Stop-loss | GDP T2.0 position < 55¢ |

---

## Dashboard

FastAPI backend (8 endpoints) + Next.js frontend (7 tabs).

```bash
# Start API (runs as stratton-api.service on Atlas)
cd dashboard/api && uvicorn main:app --host 0.0.0.0 --port 8000

# Start frontend
cd dashboard/web && npm run dev
```

**API endpoints:** `/api/status` `/api/activity` `/api/positions` `/api/weather` `/api/brad` `/api/eval` `/api/rag-demo` `/api/portfolio`

---

## Setup

```bash
git clone https://github.com/cwolson03/the-firm.git
cd the-firm

cp .env.example .env
# Add: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH, Discord bot tokens,
# FRED_API_KEY, TOMORROW_API_KEY, GROK_API_KEY, CLAUDE_API_KEY, OPENAI_API_KEY

pip install -r requirements.txt

# Full startup
python3 firm.py

# Test without orders or Discord posts
python3 firm.py --dry-run --once
```

---

## Stack

| Component | Purpose |
|-----------|---------|
| Python 3.11+ | All agents — threading only, no async |
| Raspberry Pi 5 | Production host, `stratton-firm.service` via systemd |
| Kalshi API | RSA-PSS auth, 5,200+ markets scanned |
| Discord API | REST only, one bot token per agent |
| FRED API | GDPNow, CPI, PCE series |
| Tomorrow.io | Temperature forecasts (500/day free tier) |
| Open-Meteo | Free forecast fallback |
| NOAA Gridpoint | Tertiary weather source |
| CoinGecko | BTC/ETH spot prices (free) |
| The Odds API | Sportsbook probabilities for sports engine |
| Grok xAI | Real-time sports + market intelligence |
| Claude (Anthropic) | Deep reasoning, eval critique |
| GPT-4o (OpenAI) | Shadow checks, general reasoning |
| ChromaDB | Local vector database for RAG |
| sentence-transformers | all-MiniLM-L6-v2 embeddings (local, free) |
| Senate/House Stock Watcher | STOCK Act disclosure feeds |
| Yahoo Finance | Live stock prices for portfolio + Jordan |
| ESPN API | Live scoreboards for sports engine |
| FastAPI + uvicorn | Dashboard backend |
| Next.js 15 + Tailwind | Dashboard frontend |

---

*Built by Cody Olson.*
