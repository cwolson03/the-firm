# The Firm — Autonomous Prediction Market Intelligence System

A multi-agent Python system that scans prediction markets on [Kalshi](https://kalshi.com), identifies statistical mispricing using quantitative models, executes trades autonomously based on configurable guardrails, and monitors positions around the clock. Built to run 24/7 on a Raspberry Pi 5.

---

## Architecture

`firm.py` is the master orchestrator. It runs a thread-safe scheduler loop that launches each agent in its own isolated daemon thread on a configurable interval. Agents share no state — each loads independently via Python's `importlib`.

**Key implementation details:**
- `threading.Lock` per scanner prevents concurrent duplicate runs
- `threading.Event` (`_stop_event`) for clean SIGTERM/SIGINT shutdown
- `RotatingFileHandler` (5MB per file, 3 backups) at `logs/firm.log`
- Discord REST API for real-time alerts and full audit trail
- All agent paths resolved from `FIRM_BASE_DIR` environment variable — no hardcoded paths

```
the-firm/
├── firm.py              # Master orchestrator — scheduler + signal handling
├── agents/
│   ├── donnie.py        # Kalshi prediction market execution engine
│   ├── weather.py       # Daily high temperature market scanner
│   ├── brad.py          # Sports market stink-bid strategy
│   ├── rugrat.py        # Congressional trade monitor
│   ├── chester.py       # Crypto whale tracker
│   ├── jordan.py        # Options position monitor
│   └── mark_hanna.py    # Macro research & deep dives
├── tools/
│   ├── backtest.py      # Strategy backtester
│   └── supervisor.py    # Operational watchdog
└── research/
    ├── macro-april-2026.md
    └── roth-ira-options-framework.md
```

---

## Agents

| Agent | File | Domain | Interval |
|-------|------|--------|----------|
| Donnie | `agents/donnie.py` | Kalshi prediction market execution | 2 hours |
| Weather | `agents/weather.py` | Temperature market forecasting | 5 min |
| Brad | `agents/brad.py` | Sports market stink-bid strategy | 30 min |
| Rugrat | `agents/rugrat.py` | Congressional trade monitoring | 4 hours |
| Chester | `agents/chester.py` | Crypto whale tracking | 30 min |
| Jordan | `agents/jordan.py` | Options position monitoring | 1 hour |
| Mark Hanna | `agents/mark_hanna.py` | Macro research & deep dives | Weekly |

---

## Donnie — Core Execution Engine

Donnie is the primary trading agent. It runs a two-tier architecture: a 30-minute full discovery scan across all Kalshi markets, and a 30-second real-time pulse on a watchlist of top candidates.

**5-gate execution model.** Every trade candidate must clear all five gates before an order is placed:

1. **Price edge** — minimum 18¢ directional edge above Kalshi's fee structure
2. **Order book pressure** — bid/ask imbalance analysis (`yes_bid_size_fp` vs `yes_ask_size_fp`)
3. **Market velocity** — price acceleration tracking; SPIKE (>10¢/min) triggers immediate re-score
4. **Whale accumulation** — large trade clustering in the last hour (≥50 contracts, ≥$200 notional)
5. **Time-to-close penalty** — exponential confidence decay past 14 days, hard block past 90 days

**Data models powering the edge:**

- **GDPNow** (Atlanta Fed via FRED API) for GDP threshold markets — `KXGDP-*`
- **Cleveland Fed CPI nowcast** for CPI/PCE markets — `KXCPI-*`, `KXPCE-*`
- **CoinGecko spot price** for BTC/ETH daily range markets — `KXBTCD-*`, `KXETHD-*`
- **Stooq commodity prices** for gold, crude, S&P markets

**Market classification tiers** (determines scoring multiplier):

| Class | Multiplier | Examples |
|-------|-----------|---------|
| `ECONOMIC_DATA` | 3.0× | CPI, NFP, FOMC, GDP, PCE |
| `WEATHER` | 2.5× | Temperature markets (delegated to weather.py) |
| `COMMODITY` | 2.5× | Gold, oil, silver daily ranges |
| `CRYPTO_SHORT` | 2.0× | BTC/ETH intraday price ranges |
| `POLITICAL_NEWS` | 1.0× | Breaking news — whale signal required |
| `POLITICAL_LONG` | 0.2× | 2028 elections, long-dated nominations |
| `JUNK` | 0.0× | "Will X say Y" — excluded entirely |

**Position sizing** is confidence-scaled: 4% of balance at ≥0.65 confidence, up to 12% for near-expiry HIGH conviction plays with ≥0.85 confidence. Hard cap: 35% per position, 70% total deployed.

**Additional safeguards introduced after post-trade analysis:**
- Crypto/commodity markets: minimum 30 minutes to close + 0.5% spot buffer from threshold
- Momentum conflict check: blocks NO bets when 24h drift contradicts direction
- Stale order cleanup: auto-cancels resting Donnie orders after 2 hours

Dry-run mode (`--dry-run`) runs full logic without placing orders — all output to stdout and Discord.

---

## Weather Bot

The weather bot scans Kalshi's daily high temperature markets (`KXHIGH*` series, 19 cities) and identifies pricing discrepancies against forecast models.

**Dual-source forecasting:**
- **Primary:** Tomorrow.io (daily `temperatureMax`, UTC midnight-anchored to prevent truncation artifacts)
- **Fallback:** Open-Meteo (free, no auth, 6-day forecast)
- **Tertiary:** NOAA gridpoint API for US cities

**Multi-model consensus.** When both sources are available, the bot computes:
- `forecast_high` = mean of available models
- `uncertainty` = actual standard deviation (capped 1.5°F–6°F)
- `agreement` ∈ `HIGH` (<2°F std), `MEDIUM` (2–4°F), `LOW` (>4°F)

LOW-agreement forecasts are skipped entirely — no signal fires when models disagree.

**Edge calculation** uses a normal CDF against the market's strike type (`less`, `greater`, or `between` range). Threshold markets require the forecast to be at least `1.5 × uncertainty` from the strike before a YES signal fires (the margin gate).

**Edge thresholds:** 15¢ minimum. Paper mode runs continuously; live execution gated on position count.

**Bias calibration:** A rolling 7-day per-city correction is computed from resolved trades and applied to forecasts. Requires ≥3 settled samples before correction activates.

**Prefetch engine:** Forecasts are prefetched at model update windows (00:30, 06:30, 12:30, 18:30 UTC) so the scan loop reads from cache rather than hitting APIs on every 5-minute tick.

---

## Brad — Sports Stink-Bid Strategy

Brad places limit buy orders at a discount to current market mid on Kalshi sports markets, waiting for volatility to bring prices down to the bid. Three concurrent strategies run in parallel:

| Strategy | Discount | Min Favorite | Max Bids | Capital Cap |
|----------|---------|-------------|---------|------------|
| S1 — Live game winners | 20–25% | 55¢ | 5 | 12% of balance |
| S2 — Spread/prop markets | 20% | 60¢ | 3 | 8% of balance |
| S3 — Tournament outrights | 35% | 70¢ | 2 | 5% of balance |

Brad integrates ESPN live scoreboards to detect games in progress and blowout situations (cancels bids if the favorite is losing badly in a late period). The Odds API provides sportsbook probability cross-checks for S1 markets. Hard cap: 25% of total account balance across all Brad activity.

Brad runs in paper mode by default — all logic executes, trades are logged to `data/brad_paper_trades.json`, but no real orders are placed unless explicitly enabled.

---

## Backtesting

`tools/backtest.py` validates strategies against historical data before live deployment.

**Weather strategy backtest:**
- Primary source: settled paper trades with real Kalshi prices captured at signal time
- Secondary source: Kalshi finalized markets with `previous_yes_bid/ask` fields (last 30 days), cross-referenced against Open-Meteo GFS historical forecasts
- Analysis: edge bucket breakdown, per-city win rates, model calibration (predicted probability vs. actual outcome frequency), forecast bias (GFS vs. NWS actual temperature), optimal threshold search with minimum-sample guard

**GDP strategy analysis** (run at startup when GDP positions are open):
- 8 quarters of FRED data (Q1 2024–Q4 2025) to characterize the distribution
- GDPNow as the live edge signal — when the Atlanta Fed estimate diverges significantly from Kalshi's implied probability, Donnie enters

---

## Real Results

This system has been running live on Kalshi since early April 2026.

**CPI Shelter trade (April 10, 2026):** Donnie modeled a 40% probability that CPI shelter would print above 4.24% — Kalshi's market implied 15%. The model: Bureau of Labor Statistics shelter component has documented 6-month momentum; the prior reading was 4.21%. Donnie entered YES on `KXSHELTERCPI-26APR10-T424.0`. The BLS print came in at 424.069 bps. Market resolved YES. Account balance: $102 → $383 on that single trade.

**GDP Q1 2026 positions:** With Atlanta Fed GDPNow tracking at 1.31% annualized (vs. Kalshi's market implied probability of ~35-40% for GDP above 2.0%), Donnie entered NO on three threshold markets: `KXGDP-26APR30-T2.0`, `KXGDP-26APR30-T2.5`, `KXGDP-26APR30-T3.0`. Thesis: tariff drag, net export deterioration, and inventory destocking make sub-2% GDP near-certain. Positions held at time of writing.

**Post-trade analysis — BTC intraday loss (April 2026):** A NO position on a BTC above-threshold market was entered 9 minutes before close with spot only $49 away from the threshold. The trade triggered correctly by the model but failed in execution: insufficient time buffer for a crypto range market + spot too close to threshold for the edge calculation to hold. This led directly to the implementation of `CRYPTO_MIN_MINUTES_TO_CLOSE = 30` and `CRYPTO_MIN_BUFFER_PCT = 0.005` guardrails — both now mandatory gates for any crypto or commodity trade.

---

## Setup

```bash
git clone https://github.com/your-username/the-firm.git
cd the-firm

# Copy environment template and fill in your credentials
cp .env.example .env
# Edit .env — add Kalshi key ID, private key path, Discord tokens, FRED API key

pip install -r requirements.txt

# Run all agents (continuous mode)
python3 firm.py

# Run a single agent
python3 firm.py --bot donnie

# Dry-run Donnie (no orders placed)
python3 agents/donnie.py --dry-run --scan-once

# Run the backtester
python3 tools/backtest.py
```

**Kalshi authentication** uses RSA-PSS signing. Generate a key pair, upload the public key to your Kalshi account, and set `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in `.env`.

---

## Stack

- **Python 3.11+** — asyncio-free; all threading via `threading.Thread` daemon threads
- **Raspberry Pi 5** — primary production host, runs as `stratton-firm.service` via systemd
- **Kalshi API** — RSA-PSS signed headers, paginated events endpoint (~5,200 markets per scan)
- **Discord API** — REST only (no gateway), one bot token per agent for channel routing
- **FRED API** — GDPNow (`GDPNOW`), CPI (`CPIAUCSL`), PCE (`PCEPI`) series
- **Tomorrow.io** — daily high temperature forecasts, 500/day free tier
- **Open-Meteo** — free forecast fallback, no API key required
- **CoinGecko** — BTC/ETH spot prices, no API key required
- **requests** — all HTTP calls, synchronous
- **cryptography** — RSA-PSS signing for Kalshi auth
- **scipy** — normal distribution CDF for probability calculations (optional; falls back to `math.erf`)
- **feedparser** — Whale Alert RSS for Chester

---

*Built by Cody Olson.*
