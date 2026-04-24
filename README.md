# The Firm — Autonomous Trading Intelligence System

A multi-agent Python system running 24/7 on a Raspberry Pi 5. One command (`python3 firm.py`) launches every agent. Agents scan live markets, identify statistical mispricings, execute trades autonomously within hard risk guardrails, and monitor positions around the clock.

**Live since April 2026. Real capital deployed on [Kalshi](https://kalshi.com).**

---

## Real Results

> **CPI Shelter trade — April 10, 2026**
> Donnie modeled a 40% probability that CPI shelter would print above 4.24%. Kalshi's market implied only 15%. Edge: +25 points. Donnie entered YES on `KXSHELTERCPI-26APR10-T424.0`. BLS printed 424.069 bps. Market resolved YES.
> **+275% return on position in a single trade.**

> **GDP Q1 2026 — Three concurrent NO positions**
> With Atlanta Fed GDPNow tracking at 1.24% annualized, Donnie entered NO on `KXGDP-26APR30-T2.0`, `T2.5`, and `T3.0`. Thesis: tariff drag + net export deterioration make sub-2% near-certain. Positions held at time of writing.

> **Post-trade analysis — BTC loss (April 2026)**
> A NO position entered 9 minutes before close with spot only 0.05% from threshold. Model was right directionally; execution failed — insufficient time buffer. This directly triggered `CRYPTO_MIN_MINUTES_TO_CLOSE = 30` and `CRYPTO_MIN_BUFFER_PCT = 0.005` as mandatory gates. Bad calls get logged and patched.

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
    │ Donnie │  │Weather │  │  Brad  │  │  Rugrat  │  │  Jordan   │
    │ v2.py  │  │ .py    │  │  .py   │  │  .py     │  │  .py      │
    │        │  │        │  │        │  │          │  │           │
    │Kalshi  │  │Temp    │  │Sports  │  │Congress  │  │Options &  │
    │Exec    │  │Markets │  │Stink   │  │Trade     │  │Portfolio  │
    │Engine  │  │19 cities│ │Bids    │  │Monitor   │  │Desk       │
    └────────┘  └────────┘  └────────┘  └──────────┘  └───────────┘
          │          │             │              │              │
          └──────────┴─────────────┴──────────────┴──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       shared_state.json      │
                    │    Live context layer —      │
                    │  each agent writes status,   │
                    │  positions, signals on run   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │          Discord             │
                    │   One bot token per agent    │
                    │   Channel-routed alerts      │
                    │   REST only, no gateway      │
                    └─────────────────────────────┘

Additional:
  supervisor.py  — 6-check health monitor (30 min)
  mark_hanna.py  — Weather intelligence + on-demand research
  chester.py     — Crypto whale tracker (in development)
  backtest.py    — Strategy validation against historical data
```

---

## firm.py — The Orchestrator

Every agent runs through `firm.py`. One process. One command.

```bash
python3 firm.py                   # Full startup — all agents, Discord announcement
python3 firm.py --bot donnie      # Run one agent standalone
python3 firm.py --bot rugrat      # Run congressional scanner standalone
python3 firm.py --scan weather    # Trigger a specific scanner immediately
python3 firm.py --once            # Run all scheduled agents once then exit
python3 firm.py --dry-run         # Full simulation — no Discord posts, no orders
python3 firm.py --status          # Print live scheduler status and exit
```

**Scheduler design:**
- Each agent runs in its own `threading.Thread` daemon
- `threading.Lock` per scanner prevents concurrent duplicate runs
- `_stop_event` (`threading.Event`) enables clean SIGTERM/SIGINT shutdown
- `RotatingFileHandler` (5MB, 3 backups) at `logs/firm.log`
- 429 rate-limit retry on all Discord posts (3 attempts, exponential backoff)
- Agents loaded via `importlib.util.spec_from_file_location` — no shared module state

**Live schedule:**
| Agent | Interval |
|-------|---------|
| Weather | 3 min |
| Brad | 15 min |
| Jordan (price monitor) | 15 min |
| Supervisor | 30 min |
| Donnie v2 | 120 min |
| Rugrat | 240 min |

---

## Repo Structure

```
the-firm/
├── firm.py                  # Master orchestrator — run this
├── bots/
│   ├── donnie_v2.py         # Kalshi execution engine
│   ├── weather.py           # Temperature market scanner
│   ├── brad.py              # Sports stink-bid strategy
│   ├── rugrat.py            # Congressional trade monitor
│   ├── jordan.py            # Options & portfolio desk
│   ├── mark_hanna.py        # Weather intelligence + research
│   ├── chester.py           # Crypto whale tracker
│   ├── supervisor.py        # System health monitor
│   └── shared_context.py    # Shared state writer (all agents)
├── tools/
│   ├── backtest.py          # Strategy backtester
│   └── strategy_review.py   # Weather experiment analyzer
├── config/
│   ├── bot-tokens.env       # Discord bot tokens (gitignored)
│   ├── keys.env             # API keys (gitignored)
│   └── jordan_positions.json # Open positions (gitignored)
├── data/
│   ├── shared_state.json    # Live context layer
│   ├── brad_paper_trades.json
│   └── rugrat_seen.json
└── logs/
    └── firm.log             # Rotating log (5MB, 3 backups)
```

---

## Donnie v2 — Core Execution Engine

**5-gate execution model.** Every candidate must clear all five gates before an order is placed:

1. **Price edge** — minimum 18¢ directional edge above Kalshi's fee structure
2. **Order book pressure** — bid/ask imbalance (`yes_bid_size_fp` vs `yes_ask_size_fp`)
3. **Market velocity** — price acceleration tracking; SPIKE (>10¢/min) → immediate re-score
4. **Whale accumulation** — large trade clustering in last hour (≥50 contracts, ≥$200 notional)
5. **Time-to-close penalty** — exponential confidence decay past 14 days; hard block past 90 days

**Data models:**
- **GDPNow** (Atlanta Fed via FRED) → GDP threshold markets (`KXGDP-*`)
- **Cleveland Fed CPI nowcast** → CPI/PCE markets (`KXCPI-*`, `KXPCE-*`)
- **CoinGecko** → BTC/ETH intraday range markets (`KXBTCD-*`, `KXETHD-*`)
- **Stooq** → Gold, crude, commodity markets

**Market classification tiers:**

| Class | Score Multiplier | Examples |
|-------|-----------------|---------|
| `ECONOMIC_DATA` | 3.0× | CPI, NFP, FOMC, GDP, PCE |
| `WEATHER` | 2.5× | Temperature markets (delegated to weather.py) |
| `COMMODITY` | 2.5× | Gold, oil, silver daily ranges |
| `CRYPTO_SHORT` | 2.0× | BTC/ETH intraday ranges |
| `POLITICAL_NEWS` | 1.0× | Breaking news — whale signal required |
| `JUNK` | 0.0× | Excluded entirely |

**Position sizing:** Confidence-scaled from 4% (≥0.65) to 12% (≥0.85, near-expiry). Hard cap: 35% per position, 70% total deployed.

**Post-trade guardrails added from live losses:**
- Crypto/commodity: min 30 min to close + 0.5% spot buffer from threshold
- Momentum conflict check: blocks NO bets when 24h drift contradicts direction
- Stale order cleanup: auto-cancels resting orders after 2 hours

---

## Weather Bot

Scans Kalshi's `KXHIGH*` series (19 cities) for daily high temperature mispricings.

**Three-source forecasting:**
- **Primary:** Tomorrow.io (UTC midnight-anchored to prevent truncation artifacts)
- **Fallback:** Open-Meteo (free, no auth, 6-day GFS forecast)
- **Tertiary:** NOAA gridpoint API

**Multi-model consensus:** `forecast_high` = mean of available models. `uncertainty` = std dev (capped 1.5°F–6°F). `agreement` ∈ HIGH/MEDIUM/LOW — LOW forecasts skipped entirely.

**Edge calculation:** Normal CDF against strike type (`less`, `greater`, `between`). Threshold markets require forecast at least `1.5 × uncertainty` from strike before signal fires.

**Bias calibration:** Rolling 7-day per-city correction from resolved trades. Activates after ≥3 settled samples.

**Prefetch engine:** Forecasts cached at model update windows (00:30, 06:30, 12:30, 18:30 UTC).

---

## Brad — Sports Stink-Bid Strategy

Places limit buy orders at a discount to market mid, waiting for volatility to fill the bid. Three parallel strategies:

| Strategy | Discount | Min Favorite | Max Bids | Capital Cap |
|----------|---------|-------------|---------|------------|
| S1 — Live game winners | 20–25% | 55¢ | 5 | 12% |
| S2 — Spread/prop markets | 20% | 60¢ | 3 | 8% |
| S3 — Tournament outrights | 35% | 70¢ | 2 | 5% |

**Intelligence layers:**
- **The Odds API** — sportsbook probability cross-check on S1 markets (DraftKings/FanDuel vig-normalized)
- **Grok xAI** — real-time injury alerts and sharp money signals for S1 games within 6 hours of tip-off
- **ESPN live scoreboards** — blowout detection; cancels bids if favorite losing badly in late periods

Paper mode by default. Trades logged to `data/brad_paper_trades.json`.

---

## Rugrat — Congressional Trade Intelligence

Monitors STOCK Act disclosures from 18 tracked members of Congress. Alerts when high-scoring members make significant moves.

**Scoring model (0–100 scale):**
- Track record (0–30): documented historical returns
- Transaction size (0–20): larger = more signal
- Committee relevance (0–20): Armed Services buys defense, Intelligence buys crypto, etc.
- Portfolio overlap (0–15): cross-referenced against Cody's open book
- Macro alignment (0–15): does the trade fit the current macro regime

**Top tracked members:** Nancy Pelosi (28), Michael McCaul (26), Ro Khanna (25), Josh Gottheimer (24), Dan Crenshaw (22), Mark Kelly (22)

**Data sources:** Senate Stock Watcher + House Stock Watcher public S3 APIs. New disclosures deduplicated against `data/rugrat_seen.json`.

**Alert tiers:** HIGH_CONVICTION → `#senator-tracker` + `#active-plays`. WATCH → `#senator-tracker`. Low-signal → batched daily summary.

---

## Jordan — Options & Portfolio Desk

Tracks options positions and personal portfolio. Replaces blind Discord group alerts with a systematic monitoring framework.

**For Discord group alerts:**
- Analyzes the alert independently (upside %, 52W context, risk/reward)
- Tracks the position with a `target_price` field
- Polls live prices every 15 minutes during market hours (9:30–16:00 ET)
- Fires alert to `#active-plays` when price crosses the target or comes within 1%

**For all positions:**
- DTE alerts: 🔴 critical at ≤7 DTE, ⚠️ warning at ≤21 DTE
- Roll calculator: three scenarios (roll out, roll + adjust strike, close)
- Rough delta estimates (ATM/ITM/OTM bucketing)
- Supports both `option` and `stock` position types

```bash
# Add an options position from a Discord group alert
python3 bots/jordan.py --add NVDA CALL 950 2026-04-25 2 8.50 --target 965 --source discord_group

# Analyze a ticker before entering
python3 bots/jordan.py --analyze NVDA

# Analyze a Discord alert directly
python3 bots/jordan.py --alert NVDA 965

# Run price target monitor
python3 bots/jordan.py --monitor
```

---

## Supervisor — System Health Monitor

Runs every 30 minutes. Six checks, posts to Discord `#general` on any anomaly (2-hour cooldown per alert type).

| Check | What it does |
|-------|-------------|
| 1. Service health | `stratton-firm.service` is active |
| 2. Log error rate | Error count in last 30 min from firm.log |
| 3. Kalshi balance | Balance vs. established baseline |
| 4. Resting orders | Unexpected live orders (Donnie stale cleanup) |
| 5. Paper mode integrity | Verifies brad.py and weather.py paper flags |
| 6. GDP stop-loss | T2.0 NO position price vs. 55¢ stop |

---

## Backtesting

`tools/backtest.py` validates strategies before live deployment.

**Weather backtest:**
- Settled paper trades with real Kalshi prices at signal time
- Kalshi finalized markets (last 30 days) cross-referenced with Open-Meteo GFS historical forecasts
- Analysis: edge bucket breakdown, per-city win rates, model calibration, forecast bias, optimal threshold search

**GDP analysis:**
- 8 quarters FRED data (Q1 2024–Q4 2025)
- GDPNow as live signal — flags when Atlanta Fed estimate diverges from Kalshi implied probability

---

## Setup

```bash
git clone https://github.com/your-username/the-firm.git
cd the-firm

cp .env.example .env
# Fill in: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH, Discord bot tokens, FRED_API_KEY

pip install -r requirements.txt

# Full startup
python3 firm.py

# Test without placing orders or posting to Discord
python3 firm.py --dry-run --once
```

**Kalshi auth:** RSA-PSS signing. Generate key pair, upload public key to Kalshi account, set `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in `.env`.

**Required env vars:**
```
KALSHI_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=
DONNIE_TOKEN=        # Discord bot tokens (one per agent)
WEATHER_TOKEN=
BRAD_TOKEN=
RUGRAT_TOKEN=
JORDAN_TOKEN=
MARK_HANNA_TOKEN=
CHESTER_TOKEN=
STRATTON_TOKEN=
FRED_API_KEY=
TOMORROW_API_KEY=
ODDS_API_KEY=
GROK_API_KEY=
```

---

## Stack

| Component | Purpose |
|-----------|---------|
| **Python 3.11+** | All agents — asyncio-free, threading only |
| **Raspberry Pi 5** | Production host, `stratton-firm.service` via systemd |
| **Kalshi API** | RSA-PSS auth, paginated events (~5,200 markets/scan) |
| **Discord API** | REST only, one bot token per agent |
| **FRED API** | GDPNow, CPI, PCE series |
| **Tomorrow.io** | Temperature forecasts (500/day free tier) |
| **Open-Meteo** | Free forecast fallback |
| **NOAA Gridpoint API** | Tertiary weather source |
| **CoinGecko** | BTC/ETH spot prices (free) |
| **The Odds API** | Sportsbook probabilities for Brad |
| **Grok xAI API** | Real-time sports intelligence for Brad |
| **Senate/House Stock Watcher** | STOCK Act disclosure feeds for Rugrat |
| **Yahoo Finance** | Live stock prices for Jordan |
| **ESPN API** | Live scoreboards for Brad |
| **requests** | All HTTP — synchronous, no async |
| **cryptography** | RSA-PSS signing for Kalshi |
| **scipy** | Normal CDF for weather edge calculation |

---

*Built by Cody Olson.*
