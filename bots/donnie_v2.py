#!/usr/bin/env python3
"""
DONNIE V3 — Two-Tier Real-Time Kalshi Scanner
===============================================
Stratton Oakmont prediction market intelligence — next generation.

NEW IN V3:
  - Two-tier architecture: Discovery (30min) + Real-time Monitor (30sec)
  - Weather Signal Layer: Open-Meteo forecasts vs Kalshi weather markets
  - Polymarket Signal Layer: cross-platform arb detection (read-only)
  - Odds Velocity Tracker: price/volume acceleration signals
  - Stink Bid Strategy (importable by Brad — NOT used by Donnie)
  - Tighter scheduling: 30min discovery, 30sec watchlist pulse

KEPT FROM V2:
  - RSA-PSS auth (unchanged)
  - All guardrails (35% max, 70% deployed, 15¢ edge, 90-day horizon)
  - Time horizon penalty scoring
  - Whale scoring (compute_whale_boost)
  - Discord report format (format_donnie_report)
  - Execution engine (execute_trade, post_execution_result)
  - Path auto-detection (Atlas vs local)
  - --dry-run and --scan-once flags

API Notes:
  - Prices are in dollars (0.0–1.0), NOT cents
  - Volume is volume_fp (float), spread in dollars
  - Categories live on EVENTS, not markets
  - Trades endpoint: GET /markets/trades?ticker=X&limit=50

Usage:
    python3 donnie-v3.py                   # continuous mode
    python3 donnie-v3.py --scan-once       # single full scan + exit
    python3 donnie-v3.py --dry-run         # stdout only, no Discord
    python3 donnie-v3.py --dry-run --scan-once

Requirements:
    pip install requests cryptography scipy
"""

import os
import sys
import json
import math
import time
import uuid
import base64
import logging
import argparse
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import requests

try:
    from scipy.stats import norm
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID      = "28aebab3-8694-46bc-95f1-2d37d9e9266e"

# Paths — auto-detect Atlas (cody) vs local (stratton)
_HOME = os.path.expanduser("~")
if os.path.exists("/home/cody/stratton"):
    PRIVATE_KEY_PATH = "/home/cody/stratton/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/cody/stratton/config/bot-tokens.env"
    LOG_PATH         = "/home/cody/stratton/logs/donnie.log"
else:
    PRIVATE_KEY_PATH = "/home/stratton/.openclaw/workspace/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/stratton/.openclaw/workspace/config/bot-tokens.env"
    LOG_PATH         = "/home/stratton/.openclaw/workspace/logs/donnie.log"

DISCORD_CH_KALSHI  = 1491861941361180924   # #kalshi-signals
DISCORD_CH_RESULTS = 1491861943894671450   # #kalshi-results

# ── Scanner thresholds ────────────────────────────────────────────────────────
MIN_VOLUME         = 200
MAX_SPREAD_DOLLARS = 0.15
TOP_PER_CAT        = 10

# ── Whale detection ───────────────────────────────────────────────────────────
WHALE_MIN_CONTRACTS    = 50
WHALE_MIN_NOTIONAL     = 200
WHALE_WINDOW_HOURS     = 1
WHALE_ALERT_THRESHOLD  = 3
WHALE_FETCH_TOP_N      = 30

# ── Confidence thresholds ─────────────────────────────────────────────────────
CONFIDENCE_HIGH_THRESHOLD   = 0.65
CONFIDENCE_MEDIUM_THRESHOLD = 0.35

# ── Report config ─────────────────────────────────────────────────────────────
TOP_PLAYS_DISPLAY = 5
WATCHLIST_SIZE    = 25   # Tier 1 → Tier 2 watchlist

# ── Execution Guardrails (NON-NEGOTIABLE — never bypass) ──────────────────────
EXEC_MIN_EDGE_DOLLARS   = 0.18
EXEC_MAX_PER_POSITION   = 0.35
EXEC_MAX_TOTAL_DEPLOYED = 0.70
EXEC_POSITION_SIZE_PCT  = 0.05

# ── Stale order management ───────────────────────────────────────────────────
ORDER_MAX_AGE_HOURS = 2  # auto-cancel Donnie limit orders older than 2 hours

# ── Crypto/Commodity near-expiry cutoff ──────────────────────────────────────
CRYPTO_MIN_MINUTES_TO_CLOSE = 30   # never trade crypto/commodity range markets within 30 min of close
CRYPTO_MIN_BUFFER_PCT        = 0.005  # spot must be >0.5% away from threshold

# ── Scheduling ────────────────────────────────────────────────────────────────
DISCOVERY_SCAN_INTERVAL_SEC   = 1800   # 30 min — full market scan
REALTIME_MONITOR_INTERVAL_SEC = 30     # 30 sec — watchlist price pulse
WEATHER_REFRESH_INTERVAL_SEC  = 900    # 15 min — weather model refresh
POLYMARKET_REFRESH_INTERVAL_SEC = 300  # 5 min  — polymarket price sync
SMART_MONEY_INTERVAL_SEC        = 1800  # 30 min — smart money tracker
CRYPTO_MONITOR_INTERVAL_SEC     = 300   # 5 min  — crypto price monitor

# Daily economic data release windows (UTC) — Donnie runs more aggressive checks
RELEASE_WINDOWS = [
    (8, 25, 8, 45),    # 8:30 ET = 12:30 UTC — NFP, CPI, PPI, retail sales
    (13, 55, 14, 15),  # 2:00 ET = 18:00 UTC — FOMC decisions
    (9, 55, 10, 15),   # 10:00 ET = 14:00 UTC — ISM, housing
]

# ── Tier 2 real-time trigger thresholds ──────────────────────────────────────
TIER2_PRICE_MOVE_TRIGGER = 0.05        # 5¢ price move triggers re-score
TIER2_VOLUME_SPIKE_MULT  = 2.0         # 2x volume spike triggers re-score

# ── Velocity tracker thresholds (cents per minute) ───────────────────────────
VEL_MOVING    = 2.0    # log only
VEL_FAST_MOVE = 5.0    # +0.15 confidence boost
VEL_SPIKE     = 10.0   # +0.30 confidence boost + immediate exec check
VEL_VOL_SPIKE = 2.0    # 2x volume acceleration boost multiplier → +0.20

# ── Polymarket arb thresholds ─────────────────────────────────────────────────
POLY_ARB_MIN_SPREAD    = 0.10   # 10¢ — only fire on clear arb

# ── Market taker mode ─────────────────────────────────────────────────────────
MARKET_TAKER_THRESHOLD   = 0.80  # use market order (ask price) above this confidence
MARKET_TAKER_CATEGORIES  = set()  # WEATHER moved to weather.py; ECONOMIC_DATA re-enable when verified

# ── Thesis direction lock — Donnie CANNOT trade opposite to these ─────────────
THESIS_DIRECTION_LOCK = {
    "KXGDP-26APR30-T1.0": "YES",   # GDPNow 1.31% > 1.0% threshold — YES wins
    "KXGDP-26APR30-T2.0": "NO",    # GDPNow 1.31% < 2.0% — NO wins
    "KXGDP-26APR30-T2.5": "NO",    # GDPNow 1.31% < 2.5% — NO wins
    "KXGDP-26APR30-T3.0": "NO",    # GDPNow 1.31% < 3.0% — NO wins
}
def _get_active_thesis_locks() -> dict:
    """Return thesis locks, filtering out entries past their close date."""
    from datetime import date
    today = date.today()
    active = {}
    for ticker, lock_data in THESIS_DIRECTION_LOCK.items():
        try:
            date_match = re.search(r'-(\d{2})([A-Z]{3})(\d{2})-', ticker)
            if date_match:
                day = int(date_match.group(1))
                month_str = date_match.group(2)
                year = 2000 + int(date_match.group(3))
                month_map = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
                             'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
                month = month_map.get(month_str, 0)
                if month and date(year, month, day) < today:
                    log.info(f'[THESIS] Expiring lock for {ticker} (past close date {year}-{month:02d}-{day:02d})')
                    continue  # Skip expired
        except Exception:
            pass
        active[ticker] = lock_data
    return active

POLY_FETCH_LIMIT       = 200
POLY_MATCH_MIN_WORDS   = 2   # lowered from 3 — catches more valid matches

POLYMARKET_ENABLED = False  # Set True when Polymarket integration is ready

# Stop-loss config for open positions
STOP_LOSS_RULES = {
    "KXGDP-26APR30-T2.0": {"direction": "NO", "stop_if_no_below": 0.55},   # stop if NO price drops below 55¢
    "KXGDP-26APR30-T2.5": {"direction": "NO", "stop_if_no_below": 0.60},
    "KXGDP-26APR30-T3.0": {"direction": "NO", "stop_if_no_below": 0.75},
}

# ── Market category tiers — determines scoring multiplier ─────────────────────
# Tier 1: Donnie has quantifiable data edge
# Tier 2: Some signal available
# Tier 3: No edge — exclude or require strong whale signal
CATEGORY_TIERS = {
    "ECONOMIC_DATA":  3.0,   # CPI, NFP, FOMC, GDP, PCE, PPI — model vs market
    "WEATHER":        2.5,   # Open-Meteo model vs Kalshi price
    "COMMODITY":      2.5,   # Gold, Oil, Silver, S&P with real-time data
    "CRYPTO_SHORT":   2.0,   # BTC/ETH price range within 24 hours (KXBTCD/KXETHD)
    "POLITICAL_NEWS": 1.0,   # Breaking news / whale signal required
    "WORLD_EVENTS":   1.0,   # International events
    "POLITICAL_LONG": 0.2,   # 2028 elections, long-dated political
    "JUNK":           0.0,   # Exclude: "will X say Y", "will X leave office"
}

# ── Economic calendar — update periodically ───────────────────────────────────
# Format: (date_str, event_name, series_ticker_prefix)
ECONOMIC_CALENDAR = [
    ('2026-04-29', 'GDP Q1 Advance', 'KXGDP'),
    ('2026-04-30', 'PCE March',      'KXPCE'),
    ('2026-04-30', 'FOMC Decision',  'KXFEDMEET'),
    ('2026-05-02', 'NFP April',      'KXNFP'),
    ('2026-05-13', 'CPI April',      'KXCPI'),
    # Daily crypto markets refresh continuously — always check
    ('daily', 'BTC Daily Price', 'KXBTCD'),
    ('daily', 'ETH Daily Price', 'KXETHD'),
]

# ── Weather signal thresholds ─────────────────────────────────────────────────
WEATHER_EDGE_THRESHOLD = 0.12        # 12 cents (was 0.15 — lower slightly)
WEATHER_EDGE_THRESHOLD_WINDOW = 0.10  # even lower during model update window
WEATHER_MAX_DAYS       = 3           # must resolve within 3 days
WEATHER_TEMP_STD       = 4.0    # 4°F standard deviation for normal dist

WEATHER_CITIES = {
    "New York":    {"lat": 40.7128,  "lon": -74.0060},
    "Chicago":     {"lat": 41.8781,  "lon": -87.6298},
    "Los Angeles": {"lat": 34.0522,  "lon": -118.2437},
    "Miami":       {"lat": 25.7617,  "lon": -80.1918},
    "Dallas":      {"lat": 32.7767,  "lon": -96.7970},
    "Atlanta":     {"lat": 33.7490,  "lon": -84.3880},
    "Seattle":     {"lat": 47.6062,  "lon": -122.3321},
    "Houston":     {"lat": 29.7604,  "lon": -95.3698},
    "Phoenix":     {"lat": 33.4484,  "lon": -112.0740},
    "Denver":      {"lat": 39.7392,  "lon": -104.9903},
    "Boston":      {"lat": 42.3601,  "lon": -71.0589},
    "Las Vegas":   {"lat": 36.1699,  "lon": -115.1398},
    "Minneapolis": {"lat": 44.9778,  "lon": -93.2650},
    "Detroit":     {"lat": 42.3314,  "lon": -83.0458},
    "Portland":    {"lat": 45.5051,  "lon": -122.6750},
    "Nashville":   {"lat": 36.1627,  "lon": -86.7816},
    "London":      {"lat": 51.5074,  "lon": -0.1278},
    "Toronto":     {"lat": 43.6532,  "lon": -79.3832},
}

# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY STATE
# ─────────────────────────────────────────────────────────────────────────────

# Tier 1 → Tier 2 watchlist (top markets from last discovery scan)
watchlist: list = []

# Legacy: tickers from last scan that scored HIGH/MEDIUM (for whale checks)
last_scan_top_markets: list = []

# Tier 2: last known prices + volumes per ticker for delta comparison
# {ticker: {"mid": float, "volume": float, "timestamp": float}}
last_tier2_snapshot: dict = {}

# Odds velocity tracker: {ticker: [(timestamp, mid_price), ...]}
price_history: dict = defaultdict(list)
volume_history: dict = defaultdict(list)

# Cached weather forecasts: {city: {"date": [str], "temp_max": [float]}}
weather_cache: dict = {}
weather_cache_time: float = 0.0

# Cached Polymarket markets: list of market dicts
polymarket_cache: list = []
polymarket_cache_time: float = 0.0

# Smart money: {ticker: {"poly_side": str, "poly_wallet": str, "poly_size": float, "poly_price": float}}
smart_money_signals: dict = {}
last_smart_money: float = 0.0

# Crypto monitor signals: {ticker: {"side": str, "edge": float, "spot": float, "threshold": float, ...}}
crypto_signals: dict = {}

# Fill tracker: track known resting Donnie orders so we can alert when they fill
# {order_id: {ticker, direction, price_c, contracts, cost}}
_known_resting: dict = {}

_orderbook_boost_count = 0

# ── State persistence (survives module reloads by firm.py) ─────────────────
_DONNIE_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
DONNIE_STATE_FILE = os.path.join(_DONNIE_DATA_DIR, 'donnie_state.json')

def _save_donnie_state():
    """Persist in-memory state that must survive module reloads."""
    try:
        state = {
            'known_resting': {k: v for k, v in _known_resting.items()},
            'last_tier2_snapshot': {k: v for k, v in last_tier2_snapshot.items()} if last_tier2_snapshot else {},
            'saved_at': datetime.now(timezone.utc).isoformat()
        }
        os.makedirs(os.path.dirname(DONNIE_STATE_FILE), exist_ok=True)
        tmp = DONNIE_STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, DONNIE_STATE_FILE)
    except Exception as e:
        log.warning(f'[STATE] Save failed: {e}')

def _load_donnie_state():
    """Restore state from disk on module load."""
    global _known_resting, last_tier2_snapshot
    try:
        if os.path.exists(DONNIE_STATE_FILE):
            with open(DONNIE_STATE_FILE) as f:
                state = json.load(f)
            _known_resting.update(state.get('known_resting', {}))
            if state.get('last_tier2_snapshot'):
                last_tier2_snapshot.update(state['last_tier2_snapshot'])
            log.info(f'[STATE] Loaded: {len(_known_resting)} resting orders, {len(last_tier2_snapshot)} tier2 snapshots')
    except Exception as e:
        log.warning(f'[STATE] Load failed: {e}')

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

log = logging.getLogger("donnie-v3")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh  = logging.FileHandler(LOG_PATH)
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)

# Restore persisted state from disk
try:
    _load_donnie_state()
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# AUTH — RSA-PSS (unchanged from v2)
# ─────────────────────────────────────────────────────────────────────────────

_private_key = None

def _load_private_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    try:
        with open(PRIVATE_KEY_PATH, "rb") as f:
            _private_key = load_pem_private_key(f.read(), password=None)
        log.info("RSA private key loaded successfully")
        return _private_key
    except Exception as e:
        log.error(f"Failed to load private key: {e}")
        return None


def get_auth_headers(method: str, path: str) -> dict:
    """Return RSA-PSS signed headers for Kalshi API."""
    ts  = str(int(time.time() * 1000))
    key = _load_private_key()
    if key is None:
        log.error("No private key — auth will fail")
        return {
            "KALSHI-ACCESS-KEY": KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": "MISSING_KEY",
            "Content-Type": "application/json",
        }
    msg = ts + method.upper() + path
    sig = key.sign(
        msg.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256.digest_size,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type": "application/json",
    }

# ─────────────────────────────────────────────────────────────────────────────
# KALSHI API HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def kalshi_get(path: str, params: dict = None) -> dict:
    url     = KALSHI_BASE + path
    headers = get_auth_headers("GET", "/trade-api/v2" + path)
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        log.warning(f"Kalshi GET {path} → {r.status_code}: {r.text[:150]}")
        return {}
    except Exception as e:
        log.error(f"Kalshi GET {path} error: {e}")
        return {}


def kalshi_post(path: str, body: dict) -> dict:
    url     = KALSHI_BASE + path
    headers = get_auth_headers("POST", "/trade-api/v2" + path)
    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        if r.status_code in (200, 201):
            return r.json()
        log.warning(f"Kalshi POST {path} → {r.status_code}: {r.text[:200]}")
        return {"error": r.text[:200], "status_code": r.status_code}
    except Exception as e:
        log.error(f"Kalshi POST {path} error: {e}")
        return {"error": str(e)}


def get_all_open_events_with_markets() -> list:
    """
    Paginate /events endpoint with nested markets.
    ~27 pages @ 200/page = ~5200 events.
    """
    events = []
    cursor = None
    page   = 0

    while True:
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/events", params)
        if not data:
            break

        batch = data.get("events", [])
        events.extend(batch)
        page += 1

        if page % 5 == 0:
            log.info(f"Events pagination: page {page}, total {len(events)}")

        cursor = data.get("cursor")
        if not cursor or len(batch) < 200:
            break

    log.info(f"Fetched {len(events)} events across {page} pages")
    return events


def get_market_detail(ticker: str) -> dict:
    """Fetch fresh single market data by ticker."""
    data = kalshi_get(f"/markets/{ticker}")
    return data.get("market", {})


def get_trades_for_ticker(ticker: str, limit: int = 50) -> list:
    data = kalshi_get("/markets/trades", params={"ticker": ticker, "limit": limit})
    return data.get("trades", [])

# ─────────────────────────────────────────────────────────────────────────────
# MARKET ANALYSIS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_spread(m: dict) -> float:
    ask = float(m.get("yes_ask_dollars") or 1.0)
    bid = float(m.get("yes_bid_dollars") or 0.0)
    return ask - bid


def get_mid(m: dict) -> float:
    ask = float(m.get("yes_ask_dollars") or 1.0)
    bid = float(m.get("yes_bid_dollars") or 0.0)
    return (ask + bid) / 2.0


def get_volume(m: dict) -> float:
    return float(m.get("volume_fp") or 0.0)


def get_volume_24h(m: dict) -> float:
    return float(m.get("volume_24h_fp") or 0.0)


def liquidity_score(m: dict) -> float:
    vol    = get_volume(m)
    spread = get_spread(m)
    if spread <= 0:
        return 0.0
    return vol * (1.0 / spread)


MIN_TRADEABLE_PRICE = 0.05   # 5¢ — ignore near-certain NO (99¢ YES means terrible payout)
MAX_TRADEABLE_PRICE = 0.85   # 85¢ — ignore near-certain YES (bad risk/reward on expensive contracts)

def is_liquid(m: dict) -> bool:
    if not (get_volume(m) >= MIN_VOLUME and get_spread(m) < MAX_SPREAD_DOLLARS):
        return False
    # Filter out extreme-priced markets — terrible risk/reward
    mid = get_mid(m)
    if mid < MIN_TRADEABLE_PRICE or mid > MAX_TRADEABLE_PRICE:
        return False
    return True


def flatten_events_to_markets(events: list) -> list:
    flat = []
    for event in events:
        cat     = (event.get("category") or "UNCATEGORIZED").strip()
        markets = event.get("markets") or []
        for m in markets:
            m["_category"] = cat
            # Ensure order book depth fields are preserved from the API response
            # (yes_bid_size_fp, yes_ask_size_fp — used by analyze_order_book)
            for field in ("yes_bid_size_fp", "yes_ask_size_fp"):
                if field not in m:
                    m[field] = None
            flat.append(m)
    return flat


DONNIE_CATEGORIES = {
    "Economics", "Financials", "Politics", "Crypto",
    "Science and Technology", "Health",
    "World", "Transportation", "Entertainment", "Mentions",
    "Social", "UNCATEGORIZED",
}
BRAD_CATEGORIES = {"Sports"}
MARK_CATEGORIES = {"Climate and Weather"}  # Mark Hanna + weather.py own this


def group_by_category(markets: list) -> dict:
    grouped = defaultdict(list)
    for m in markets:
        cat = m.get("_category", "UNCATEGORIZED")
        if cat in BRAD_CATEGORIES:
            continue
        grouped[cat].append(m)
    return dict(grouped)


def top_markets_per_category(grouped: dict) -> dict:
    # Priority categories get higher limits so they aren't crowded out
    PRIORITY_CAT_LIMIT = {
        "ECONOMIC_DATA": 20,   # Always evaluate all economic data markets
        "CRYPTO_SHORT":  15,   # BTC/ETH daily price markets must be included
        "COMMODITY":     15,   # Gold/Oil/SP500 daily price markets
        "WEATHER":       20,   # Weather markets handled by weather bot but keep high
    }
    result = {}
    for cat, markets in grouped.items():
        liquid = [m for m in markets if is_liquid(m)]
        scored = sorted(liquid, key=liquidity_score, reverse=True)
        limit = PRIORITY_CAT_LIMIT.get(cat, TOP_PER_CAT)
        if scored:
            result[cat] = scored[:limit]
    return result


def get_top_volume_markets(markets: list, n: int = 20) -> list:
    return sorted(markets, key=get_volume_24h, reverse=True)[:n]


def days_until_close(m: dict) -> int:
    close_time_str = m.get("close_time", "")
    try:
        ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        return max(0, (ct - datetime.now(timezone.utc)).days)
    except Exception:
        return 999

# ─────────────────────────────────────────────────────────────────────────────
# WHALE TRACKER (unchanged from v2)
# ─────────────────────────────────────────────────────────────────────────────

def parse_trade_time(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def get_whale_stats(ticker: str) -> dict:
    trades = get_trades_for_ticker(ticker, limit=50)
    if not trades:
        return {"yes_count": 0, "no_count": 0, "yes_contracts": 0.0, "no_contracts": 0.0}

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WHALE_WINDOW_HOURS)

    yes_count = no_count = 0
    yes_contracts = no_contracts = 0.0

    for trade in trades:
        count    = float(trade.get("count_fp") or 0)
        price    = float(trade.get("yes_price_dollars") or 0)
        notional = count * price
        side     = trade.get("taker_side", "")
        ts_str   = trade.get("created_time", "")
        tt       = parse_trade_time(ts_str)

        if count < WHALE_MIN_CONTRACTS and notional < WHALE_MIN_NOTIONAL:
            continue
        if tt and tt < cutoff:
            continue

        if side == "yes":
            yes_count += 1; yes_contracts += count
        elif side == "no":
            no_count += 1; no_contracts += count

    return {
        "yes_count": yes_count, "no_count": no_count,
        "yes_contracts": yes_contracts, "no_contracts": no_contracts,
    }


def analyze_whale_trades(ticker: str) -> list:
    stats  = get_whale_stats(ticker)
    alerts = []
    for direction, count_key, contracts_key in [
        ("BUY YES", "yes_count", "yes_contracts"),
        ("BUY NO",  "no_count",  "no_contracts"),
    ]:
        if stats[count_key] > WHALE_ALERT_THRESHOLD:
            alerts.append({
                "ticker":          ticker,
                "direction":       direction,
                "total_contracts": stats[contracts_key],
                "total_notional":  round(stats[contracts_key] * 0.5, 2),
                "trade_count":     stats[count_key],
                "window_hours":    WHALE_WINDOW_HOURS,
            })
    return alerts


def compute_whale_boost(yes_count: int, no_count: int, direction: str) -> tuple:
    """
    Returns (boost_float, whale_summary_str).
    Same direction: +10/+25/+40. Opposing: -15 penalty.
    """
    if direction == "YES":
        same_dir, opp_dir, opp_label = yes_count, no_count, "NO"
    else:
        same_dir, opp_dir, opp_label = no_count, yes_count, "YES"

    if opp_dir > same_dir and opp_dir > 0:
        return -0.15, f"⚠️ {opp_dir} large {opp_label} trades opposing ({direction})"
    elif same_dir >= 6:
        return 0.40, f"🐋 {same_dir} large {direction} buys stacking"
    elif same_dir >= 3:
        return 0.25, f"🐋 {same_dir} large {direction} buys stacking"
    elif same_dir >= 1:
        return 0.10, f"🐋 {same_dir} large {direction} buys"
    else:
        return 0.0, "No whale activity"

# ─────────────────────────────────────────────────────────────────────────────
# WEATHER SIGNAL LAYER
# ─────────────────────────────────────────────────────────────────────────────

def norm_cdf(x: float, loc: float, scale: float) -> float:
    """Normal CDF — uses scipy if available, otherwise Abramowitz & Stegun approximation."""
    if SCIPY_AVAILABLE:
        return float(norm.cdf(x, loc=loc, scale=scale))
    # Approximation: erf-based
    import math
    z = (x - loc) / (scale * math.sqrt(2))
    return 0.5 * (1.0 + math.erf(z))


def in_weather_model_update_window() -> bool:
    """Returns True if we're within 30 minutes after a model update."""
    now = datetime.now(timezone.utc)
    h, m = now.hour, now.minute
    # Model updates at ~00:30, 06:30, 12:30, 18:30 UTC
    for update_hour in [0, 6, 12, 18]:
        # Window: 5 min before update to 30 min after
        start_min = update_hour * 60 + 25  # 5 min before
        end_min   = update_hour * 60 + 60  # 30 min after
        now_min   = h * 60 + m
        if start_min <= now_min <= end_min:
            log.debug(f"[Weather] In model update window (update_hour={update_hour}, now_min={now_min})")
            return True
    return False


def fetch_weather_forecasts() -> dict:
    """
    Fetch Open-Meteo max temp forecasts for all WEATHER_CITIES using 3 models.
    Returns {city_name: {"dates": [...], "temp_max": [...], "temp_std": [...]}}
    Multi-model consensus: GFS, ECMWF IFS, GEM Global.
    Free API — no key needed.
    """
    import statistics
    MODELS = ["gfs_seamless", "ecmwf_ifs025", "gem_global"]
    MODEL_KEYS = [
        "temperature_2m_max_gfs_seamless",
        "temperature_2m_max_ecmwf_ifs025",
        "temperature_2m_max_gem_global",
    ]

    result = {}
    for city, coords in WEATHER_CITIES.items():
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={coords['lat']}&longitude={coords['lon']}"
            f"&daily=temperature_2m_max"
            f"&models=gfs_seamless,ecmwf_ifs025,gem_global"
            f"&temperature_unit=fahrenheit"
            f"&forecast_days=7&timezone=auto"
        )
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                n_days = len(dates)

                # Collect per-model temps
                model_arrays = []
                for key in MODEL_KEYS:
                    vals = daily.get(key, [])
                    if vals and len(vals) == n_days:
                        model_arrays.append(vals)

                # Fall back to default key if multi-model not returned
                if not model_arrays:
                    fallback = daily.get("temperature_2m_max", [])
                    model_arrays = [fallback] if fallback else []

                if not model_arrays or not dates:
                    log.warning(f"[Weather] {city}: no usable forecast data")
                    continue

                # Consensus: mean and std across available models per day
                consensus_temps = []
                consensus_stds  = []
                for i in range(n_days):
                    day_vals = [arr[i] for arr in model_arrays if arr[i] is not None]
                    if not day_vals:
                        consensus_temps.append(None)
                        consensus_stds.append(WEATHER_TEMP_STD)
                        continue
                    mean_t = sum(day_vals) / len(day_vals)
                    if len(day_vals) >= 2:
                        std_t = statistics.stdev(day_vals)
                    else:
                        std_t = WEATHER_TEMP_STD
                    consensus_temps.append(round(mean_t, 1))
                    consensus_stds.append(round(std_t, 2))

                result[city] = {
                    "dates":    dates,
                    "temp_max": consensus_temps,
                    "temp_std": consensus_stds,
                }
                log.info(
                    f"[Weather] {city} ({len(model_arrays)} models): next 3 days max temps = "
                    f"{[f'{t:.0f}°F±{s:.1f}' for t, s in zip(consensus_temps[:3], consensus_stds[:3]) if t is not None]}"
                )
            else:
                log.warning(f"[Weather] Open-Meteo {city} → {r.status_code}")
        except Exception as e:
            log.error(f"[Weather] Fetch error for {city}: {e}")
        time.sleep(0.15)
    return result


def weather_implied_prob(forecast_temp: float, threshold: float, above: bool = True,
                         model_std: float = None) -> float:
    """
    P(max_temp > threshold) using normal dist centered on forecast_temp.
    Scale (std) determined by model consensus spread:
      std_dev < 2 → scale=2.5 (high model agreement)
      std_dev > 4 → scale=6.0 (high model disagreement)
      otherwise   → scale=4.0
    For "will high be above X?" → above=True.
    For "will high be below X?" → above=False.
    """
    if model_std is not None:
        if model_std < 2.0:
            scale = 2.5
        elif model_std > 4.0:
            scale = 6.0
        else:
            scale = 4.0
    else:
        scale = WEATHER_TEMP_STD
    prob_above = 1.0 - norm_cdf(threshold, loc=forecast_temp, scale=scale)
    return prob_above if above else (1.0 - prob_above)


def _extract_temp_threshold(title: str) -> Optional[float]:
    """
    Try to extract a temperature threshold from a market title.
    e.g. "Will NYC high exceed 75°F on April 15?" → 75.0
    """
    patterns = [
        r'(\d{2,3})\s*°?F',
        r'(\d{2,3})\s*degrees',
        r'above\s+(\d{2,3})',
        r'exceed\s+(\d{2,3})',
        r'over\s+(\d{2,3})',
        r'below\s+(\d{2,3})',
        r'under\s+(\d{2,3})',
    ]
    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _is_above_market(title: str) -> bool:
    """Does the market resolve YES if temp is above threshold?"""
    lower = title.lower()
    above_words = ["above", "exceed", "over", "high", "warm"]
    below_words = ["below", "under", "cold", "low"]
    above_score = sum(1 for w in above_words if w in lower)
    below_score = sum(1 for w in below_words if w in lower)
    return above_score >= below_score


def find_weather_signals(all_markets: list, weather_data: dict,
                         edge_threshold: float = None) -> list:
    """
    Cross-reference Kalshi weather markets against Open-Meteo forecasts.
    Returns list of weather signal dicts with edge calculation.
    """
    if not weather_data:
        return []

    effective_threshold = edge_threshold if edge_threshold is not None else WEATHER_EDGE_THRESHOLD

    # Filter to weather/climate markets
    weather_markets = [
        m for m in all_markets
        if "Climate" in m.get("_category", "") or "Weather" in m.get("_category", "")
    ]
    log.info(f"[Weather] Checking {len(weather_markets)} climate/weather markets")

    signals = []
    now = datetime.now(timezone.utc)

    for m in weather_markets:
        title  = (m.get("title") or "").strip()
        ticker = m.get("ticker", "")
        mid    = get_mid(m)

        if not title or not ticker or not is_liquid(m):
            continue

        # Must resolve within WEATHER_MAX_DAYS days
        close_dt_str = m.get("close_time", "")
        try:
            close_dt = datetime.fromisoformat(close_dt_str.replace("Z", "+00:00"))
            days_out  = (close_dt - now).days
        except Exception:
            days_out = 999

        if days_out > WEATHER_MAX_DAYS or days_out < 0:
            continue

        # Match to a city
        matched_city = None
        for city in WEATHER_CITIES:
            city_key = city.lower().replace(" ", "")
            title_key = title.lower().replace(" ", "")
            # Also check common abbreviations
            abbr_map = {
                "new york": ["nyc", "new york", "new york city"],
                "los angeles": ["la", "los angeles"],
                "chicago": ["chicago", "chi"],
                "miami": ["miami"],
                "dallas": ["dallas", "dfw"],
                "atlanta": ["atlanta", "atl"],
                "seattle": ["seattle", "sea"],
            }
            aliases = abbr_map.get(city.lower(), [city.lower()])
            if any(alias in title.lower() for alias in aliases):
                matched_city = city
                break

        if not matched_city or matched_city not in weather_data:
            continue

        forecast = weather_data[matched_city]
        if not forecast["dates"] or not forecast["temp_max"]:
            continue

        # Try to match forecast date to close date
        forecast_temp = None
        idx = None
        try:
            close_date_str = close_dt.strftime("%Y-%m-%d")
            if close_date_str in forecast["dates"]:
                idx = forecast["dates"].index(close_date_str)
                forecast_temp = forecast["temp_max"][idx]
            else:
                # Use nearest available day
                idx = min(days_out, len(forecast["temp_max"]) - 1)
                forecast_temp = forecast["temp_max"][idx]
        except Exception:
            forecast_temp = forecast["temp_max"][0] if forecast["temp_max"] else None

        if forecast_temp is None:
            continue

        # Extract threshold from title
        threshold = _extract_temp_threshold(title)
        if threshold is None:
            continue

        above = _is_above_market(title)

        # Get model std for this day if available
        forecast_std = None
        try:
            std_arr = forecast.get("temp_std", [])
            if std_arr and idx is not None:
                forecast_std = std_arr[min(idx, len(std_arr) - 1)]
        except Exception:
            pass

        model_prob = weather_implied_prob(forecast_temp, threshold, above=above, model_std=forecast_std)
        kalshi_prob = mid  # Kalshi mid IS the implied probability

        edge = abs(model_prob - kalshi_prob)

        log.info(
            f"[Weather] {ticker} | {matched_city} | "
            f"forecast={forecast_temp:.1f}°F threshold={threshold:.0f}°F "
            f"above={above} | model_prob={model_prob:.2f} kalshi_prob={kalshi_prob:.2f} "
            f"edge={edge:.2f} | days_out={days_out}"
        )

        if edge >= effective_threshold:
            direction = "YES" if model_prob > kalshi_prob else "NO"
            signals.append({
                "ticker":       ticker,
                "title":        title,
                "city":         matched_city,
                "forecast_temp":round(forecast_temp, 1),
                "threshold":    threshold,
                "above":        above,
                "model_prob":   round(model_prob, 3),
                "kalshi_prob":  round(kalshi_prob, 3),
                "edge":         round(edge, 3),
                "direction":    direction,
                "days_out":     days_out,
                "mid_c":        int(mid * 100),
                "_market":      m,
                "signal_type":  "WEATHER",
                "confidence_boost": 0.20,   # weather signals get boosted confidence
            })

    signals.sort(key=lambda x: x["edge"], reverse=True)
    log.info(f"[Weather] Found {len(signals)} signals above {effective_threshold:.0%} edge threshold")
    return signals


def run_weather_refresh(all_markets: list = None, dry_run: bool = False,
                        edge_threshold: float = None) -> list:
    """Refresh weather cache and compute new signals. Returns signal list."""
    global weather_cache, weather_cache_time

    in_window = in_weather_model_update_window()
    if edge_threshold is None:
        edge_threshold = WEATHER_EDGE_THRESHOLD_WINDOW if in_window else WEATHER_EDGE_THRESHOLD

    log.info(
        f"[Weather] Refreshing Open-Meteo forecasts... "
        f"(model_update_window={in_window}, edge_threshold={edge_threshold:.2f})"
    )
    weather_cache      = fetch_weather_forecasts()
    weather_cache_time = time.time()

    if all_markets:
        signals = find_weather_signals(all_markets, weather_cache, edge_threshold=edge_threshold)
        if signals and dry_run:
            log.info("[Weather] === WEATHER SIGNALS ===")
            for s in signals:
                log.info(
                    f"  {s['city']}: forecast {s['forecast_temp']}°F vs threshold {s['threshold']}°F "
                    f"| model_prob={s['model_prob']:.0%} kalshi_prob={s['kalshi_prob']:.0%} "
                    f"| edge={s['edge']:.0%} | {s['ticker']}"
                )
        return signals
    return []

# ─────────────────────────────────────────────────────────────────────────────
# GDPNOW PROBABILITY CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

FRED_API_KEY = os.environ.get("FRED_API_KEY", "c642f045085a5318c95d0f38d44b42d2")

def fetch_fred_series(series_id: str) -> Optional[float]:
    """Fetch latest value from any FRED series using the API key."""
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            if obs and obs[0].get("value") != ".":
                val = float(obs[0]["value"])
                log.info(f"[FRED] {series_id}: {val}")
                return val
    except Exception as e:
        log.debug(f"[FRED] {series_id} failed: {e}")
    return None

def fetch_gdpnow_realtime() -> Optional[float]:
    """Fetch latest Atlanta Fed GDPNow estimate via FRED API."""
    val = fetch_fred_series("GDPNOW")
    if val is not None:
        log.info(f"[EconModel] GDPNow: {val:.2f}%")
        return val
    # Fallback to CSV scraper
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDPNOW",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            lines = [l for l in r.text.strip().split('\n') if l and not l.startswith('DATE')]
            if lines:
                val = float(lines[-1].split(',')[1])
                log.info(f"[EconModel] GDPNow (CSV fallback): {val:.2f}%")
                return val
    except Exception as e:
        log.debug(f"[EconModel] GDPNow CSV fallback failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DAILY CRYPTO/COMMODITY MARKET SEED
# ─────────────────────────────────────────────────────────────────────────────

DAILY_PRICE_SERIES = {
    'KXBTCD':  {'asset': 'BTC',  'category': 'CRYPTO_SHORT'},
    'KXETHD':  {'asset': 'ETH',  'category': 'CRYPTO_SHORT'},
    'KXGOLDD': {'asset': 'GOLD', 'category': 'COMMODITY'},
    'KXWTI':   {'asset': 'OIL',  'category': 'COMMODITY'},
}

def fetch_daily_price_markets() -> list:
    """
    Explicitly fetch today's BTC, ETH, Gold, WTI daily price markets.
    These close at specific times and need to be on the watchlist early.
    Returns list of market dicts ready for scoring.
    """
    markets = []
    now = datetime.now(timezone.utc)

    for series, info in DAILY_PRICE_SERIES.items():
        try:
            data = kalshi_get("/events", params={
                "series_ticker": series,
                "with_nested_markets": "true",
                "limit": 10,
                "status": "open"
            })
            for event in data.get("events", []):
                for m in event.get("markets", []):
                    if m.get("status") != "active":
                        continue
                    # Only include markets closing today or tomorrow
                    ct_str = m.get("close_time", "")
                    try:
                        ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
                        days_out = (ct - now).days
                        if days_out > 1:
                            continue
                    except Exception:
                        continue
                    m["_category"] = info["category"]
                    m["_asset"] = info["asset"]
                    markets.append(m)
        except Exception as e:
            log.debug(f"[DailyPrice] {series} failed: {e}")

    log.info(f"[DailyPrice] Found {len(markets)} daily price markets (BTC/ETH/Gold/Oil)")
    return markets


# ─────────────────────────────────────────────────────────────────────────────
# CLEVELAND FED CPI NOWCAST
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cleveland_cpi() -> dict:
    """
    Fetch Cleveland Fed Inflation Nowcasting estimates via FRED.
    Returns dict with current CPI estimates.

    FRED series:
    - CPILFESL: Core CPI (monthly)
    - CPIAUCSL: All items CPI
    - Cleveland Fed Nowcast scraped from their website
    """
    result = {}

    # Try FRED series for CPI
    for series_id, label in [
        ("CPIAUCSL", "headline_cpi"),
        ("CPILFESL", "core_cpi"),
        ("PCEPI",    "pce"),
        ("PCEPILFE", "core_pce"),
    ]:
        val = fetch_fred_series(series_id)
        if val:
            result[label] = val

    # Try fetching Cleveland Fed nowcast page for real-time estimate
    try:
        r = requests.get(
            "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        # Look for nowcast estimate in page
        match = re.search(r'(\d+\.\d+)\s*%.*?(?:CPI|inflation)', r.text, re.IGNORECASE)
        if match:
            result["nowcast"] = float(match.group(1))
            log.info(f"[Cleveland] CPI nowcast: {result['nowcast']:.2f}%")
    except Exception as e:
        log.debug(f"[Cleveland] Nowcast fetch failed: {e}")

    return result


def calculate_cpi_edge(ticker: str, kalshi_mid: float) -> tuple:
    """Edge calculation for CPI/PCE markets using Cleveland Fed data."""
    ticker_upper = ticker.upper()

    cpi_data = fetch_cleveland_cpi()

    # Parse threshold from ticker
    match = re.search(r'T(-?\d+\.?\d*)', ticker_upper.split('-')[-1])
    if not match:
        return None, 0.0, "NO", "parse_error"
    threshold = float(match.group(1))

    # Use headline CPI as the model estimate
    model_cpi = cpi_data.get("nowcast") or cpi_data.get("headline_cpi")
    if not model_cpi:
        return None, 0.0, "NO", "unavailable"

    if model_cpi < threshold:
        gap = threshold - model_cpi
        model_prob_no = min(0.95, 0.5 + gap * 0.12)
        edge = model_prob_no - (1.0 - kalshi_mid)
        return model_prob_no, edge, "NO", f"ClevFed={model_cpi:.2f}%<{threshold}%"
    else:
        gap = model_cpi - threshold
        model_prob_yes = min(0.95, 0.5 + gap * 0.12)
        edge = model_prob_yes - kalshi_mid
        return model_prob_yes, edge, "YES", f"ClevFed={model_cpi:.2f}%>{threshold}%"


def calculate_gdp_edge(ticker: str, kalshi_mid: float) -> tuple:
    """Returns (model_prob, edge, direction, source) for GDP markets."""
    match = re.search(r'T(-?\d+\.?\d*)', ticker.upper())
    if not match:
        return None, 0.0, "NO", "parse_error"
    threshold = float(match.group(1))
    gdpnow = fetch_gdpnow_realtime()
    if gdpnow is None:
        return None, 0.0, "NO", "unavailable"
    if gdpnow < threshold:
        gap = threshold - gdpnow
        model_prob_no = min(0.95, 0.5 + gap * 0.12)
        edge = model_prob_no - (1.0 - kalshi_mid)
        return model_prob_no, edge, "NO", f"GDPNow={gdpnow:.1f}%<{threshold}%"
    else:
        gap = gdpnow - threshold
        model_prob_yes = min(0.95, 0.5 + gap * 0.12)
        edge = model_prob_yes - kalshi_mid
        return model_prob_yes, edge, "YES", f"GDPNow={gdpnow:.1f}%>{threshold}%"


# ─────────────────────────────────────────────────────────────────────────────
# CRYPTO & COMMODITY PRICE EDGE CALCULATORS
# ─────────────────────────────────────────────────────────────────────────────

def get_crypto_spot() -> dict:
    """Get current BTC and ETH spot prices from CoinGecko."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "BTC": data.get("bitcoin", {}).get("usd", 0),
                "ETH": data.get("ethereum", {}).get("usd", 0),
            }
    except Exception as e:
        log.debug(f"[Crypto] CoinGecko failed: {e}")
    return {}


def calculate_crypto_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    For KXBTCD/KXETHD markets, compare spot price against market threshold.
    Parses ticker e.g. KXBTCD-26APR16-T82000 = above $82k.
    Returns (model_prob, edge, direction, source)
    """
    spot_prices = get_crypto_spot()
    ticker_upper = ticker.upper()
    asset = "BTC" if "KXBTCD" in ticker_upper else "ETH"
    spot = spot_prices.get(asset, 0)
    if not spot:
        return None, 0.0, "NO", "unavailable"

    # Parse "T{price}" = above threshold from last segment of ticker
    above_match = re.search(r'T(-?\d+)', ticker_upper.split('-')[-1])
    if above_match:
        threshold = float(above_match.group(1))
        pct_above = (spot - threshold) / threshold
        if pct_above > 0.02:    # spot >2% above threshold
            model_prob = 0.95
        elif pct_above > 0.005:
            model_prob = 0.85
        elif pct_above > 0:
            model_prob = 0.65
        elif pct_above > -0.005:
            model_prob = 0.40
        else:
            model_prob = 0.15

        edge = model_prob - kalshi_mid
        direction = "YES" if edge > 0 else "NO"
        log.info(
            f"[CryptoEdge] {ticker} | {asset} spot=${spot:,.0f} thr=${threshold:,.0f} "
            f"({pct_above:+.1%}) | model={model_prob:.2f} kalshi={kalshi_mid:.2f} "
            f"edge={edge:+.3f} → {direction}"
        )
        return model_prob, edge, direction, f"spot=${spot:,.0f} thr=${threshold:,.0f} {pct_above:+.1%}"

    return None, 0.0, "NO", "parse_error"


def fetch_gas_price() -> float:
    """Fetch latest US gas price from FRED (GASREGCOVW series)."""
    return fetch_fred_series("GASREGCOVW")


def get_commodity_prices() -> dict:
    """
    Get commodity spot prices from Stooq (free, no auth required).
    Falls back to Yahoo Finance scrape if Stooq fails.
    """
    # Stooq symbol map: name → stooq_ticker
    stooq_map = {
        "GOLD":   "gc.f",    # Gold futures
        "SILVER": "si.f",    # Silver futures
        "OIL":    "cl.f",    # WTI crude
        "BRENT":  "co.f",    # Brent crude
        "SP500":  "^spx",    # S&P 500
        "NASDAQ": "^ndq",    # NASDAQ
    }
    prices = {}
    for name, sym in stooq_map.items():
        try:
            r = requests.get(
                f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&e=csv",
                timeout=8
            )
            if r.status_code == 200:
                # Stooq returns: Symbol,Date,Time,Open,High,Low,Close,Volume (no header)
                lines = [l.strip() for l in r.text.strip().split('\n') if l.strip()]
                if lines:
                    parts = lines[0].split(',')
                    # Format: Symbol,Date,Time,Open,High,Low,Close,Volume
                    if len(parts) >= 7:
                        close_str = parts[6]
                        if close_str:
                            close = float(close_str)
                            if close > 0:
                                prices[name] = close
        except Exception as e:
            log.debug(f"[Commodity] Stooq {sym} failed: {e}")
        time.sleep(0.1)

    if prices:
        log.info("[Commodity] Stooq prices: " + " | ".join(f"{k}={v:,.1f}" for k, v in prices.items()))
    else:
        log.warning("[Commodity] No prices from Stooq — all commodity edge calcs will skip")
    return prices


def calculate_commodity_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    For KXGOLD/KXSILVER/KXWTI/KXBRENT/KXSPX/KXNDX markets,
    compare spot price against Kalshi threshold.
    Returns (model_prob, edge, direction, source)
    """
    ticker_upper = ticker.upper()

    asset_map = {
        'KXGOLDD':   'GOLD',    # daily gold
        'KXGOLDW':   'GOLD',    # weekly gold
        'KXGOLDMON': 'GOLD',    # monthly gold
        'KXGOLD':    'GOLD',    # generic gold
        'KXSILVER':  'SILVER',
        'KXWTI':     'OIL',
        'KXBRENT':   'BRENT',
        'KXSPX':     'SP500',
        'KXNDX':     'NASDAQ',
    }
    asset = next((v for k, v in asset_map.items() if ticker_upper.startswith(k)), None)
    if not asset:
        return None, 0.0, "NO", "unknown"

    prices = get_commodity_prices()
    spot = prices.get(asset, 0)
    if not spot:
        return None, 0.0, "NO", "unavailable"

    # Parse "T{price}" = above threshold from last segment of ticker
    above_match = re.search(r'T(-?\d+\.?\d*)', ticker_upper.split('-')[-1])
    if above_match:
        threshold = float(above_match.group(1))
        pct_diff = (spot - threshold) / max(threshold, 1)
        if pct_diff > 0.01:     model_prob = 0.92
        elif pct_diff > 0.003:  model_prob = 0.80
        elif pct_diff > 0:      model_prob = 0.62
        elif pct_diff > -0.003: model_prob = 0.40
        elif pct_diff > -0.01:  model_prob = 0.22
        else:                   model_prob = 0.08

        edge = model_prob - kalshi_mid
        direction = "YES" if edge > 0 else "NO"
        log.info(
            f"[CommodityEdge] {ticker} | {asset} spot={spot:,.1f} thr={threshold:,.1f} "
            f"({pct_diff:+.2%}) | model={model_prob:.2f} kalshi={kalshi_mid:.2f} "
            f"edge={edge:+.3f} → {direction}"
        )
        return model_prob, edge, direction, f"spot={spot:,.1f} thr={threshold:,.1f}"

    return None, 0.0, "NO", "parse_error"


# ─────────────────────────────────────────────────────────────────────────────
# NEWS RSS SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def run_news_scan(watchlist_markets: list, dry_run: bool = False) -> list:
    """
    Scan RSS news feeds for headlines matching watchlist markets.
    Returns list of (ticker, headline, sentiment) tuples.
    """
    import xml.etree.ElementTree as ET

    RSS_FEEDS = [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.politico.com/politics-news.xml",
    ]

    BULLISH_WORDS = {"passes", "signed", "approved", "confirmed", "wins", "victory", "rises", "increases", "advances"}
    BEARISH_WORDS = {"fails", "blocked", "rejected", "loses", "falls", "drops", "vetoed", "denied", "collapses"}

    headlines = []
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text.lower())
        except Exception as e:
            log.debug(f"RSS feed failed {feed_url}: {e}")

    log.info(f"[News] Collected {len(headlines)} headlines from {len(RSS_FEEDS)} feeds")

    matches = []
    for m in watchlist_markets:
        ticker = m.get("ticker", "")
        title = m.get("title", "").lower()
        title_words = set(w for w in title.split() if len(w) > 4)

        for headline in headlines:
            hl_words = set(headline.split())
            overlap = title_words & hl_words
            if len(overlap) >= 3:
                # Determine sentiment
                hl_set = set(headline.split())
                bullish = bool(hl_set & BULLISH_WORDS)
                bearish = bool(hl_set & BEARISH_WORDS)
                sentiment = "bullish" if bullish and not bearish else "bearish" if bearish and not bullish else "neutral"
                matches.append((ticker, headline[:100], sentiment))
                log.info(f"[News] Match: {ticker} | {sentiment} | {headline[:80]}")

    return matches


# ─────────────────────────────────────────────────────────────────────────────
# POLYMARKET SIGNAL LAYER
# ─────────────────────────────────────────────────────────────────────────────

STOP_WORDS = {"will", "the", "a", "in", "of", "by", "be", "to", "on", "at",
              "is", "it", "an", "or", "and", "for", "with", "that", "this"}


def tokenize_title(title: str) -> set:
    """Lowercase, remove punctuation, split to words, remove stop words."""
    words = re.findall(r'\b[a-z0-9]+\b', title.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 2}


def fuzzy_match_titles(kalshi_title: str, poly_title: str) -> int:
    """Returns number of overlapping significant keywords."""
    k_tokens = tokenize_title(kalshi_title)
    p_tokens = tokenize_title(poly_title)
    return len(k_tokens & p_tokens)


def fetch_polymarket_markets(limit: int = POLY_FETCH_LIMIT) -> list:
    """
    Fetch active Polymarket markets sorted by volume descending.
    No auth needed — public API.
    """
    all_markets = []
    offset = 0
    per_page = 100

    while len(all_markets) < limit:
        try:
            url = (
                f"https://gamma-api.polymarket.com/markets"
                f"?limit={per_page}&offset={offset}&active=true&closed=false"
            )
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                batch = r.json()
                if not batch:
                    break
                all_markets.extend(batch)
                offset += per_page
                if len(batch) < per_page:
                    break
            else:
                log.warning(f"[Polymarket] API → {r.status_code}")
                break
        except Exception as e:
            log.error(f"[Polymarket] Fetch error: {e}")
            break
        time.sleep(0.3)

    # Sort by volume descending
    def _poly_volume(m):
        try:
            return float(m.get("volume", 0) or 0)
        except Exception:
            return 0.0

    all_markets.sort(key=_poly_volume, reverse=True)
    log.info(f"[Polymarket] Fetched {len(all_markets)} markets")
    return all_markets[:limit]


def get_poly_mid(poly_market: dict) -> Optional[float]:
    """Extract YES mid price from Polymarket market. Returns None if can't parse."""
    try:
        prices = poly_market.get("outcomePrices", [])
        if isinstance(prices, list) and len(prices) >= 1:
            return float(prices[0])
        if isinstance(prices, str):
            parsed = json.loads(prices)
            if isinstance(parsed, list) and len(parsed) >= 1:
                return float(parsed[0])
    except Exception:
        pass
    return None


def find_polymarket_arb(watchlist_markets: list, poly_markets: list) -> list:
    """
    Cross-reference watchlist markets vs Polymarket.
    Returns list of arb opportunity dicts with spread > POLY_ARB_MIN_SPREAD.
    """
    if not poly_markets:
        return []

    arb_opportunities = []

    for km in watchlist_markets:
        k_title  = km.get("title", "") or ""
        k_ticker = km.get("ticker", "")
        k_mid    = get_mid(km)

        if not k_title or not k_ticker:
            continue

        best_match  = None
        best_score  = 0
        best_poly   = None

        for pm in poly_markets:
            p_title = pm.get("question", "") or ""
            score   = fuzzy_match_titles(k_title, p_title)
            if score > best_score:
                best_score  = score
                best_match  = p_title
                best_poly   = pm

        if best_score < POLY_MATCH_MIN_WORDS or best_poly is None:
            continue

        poly_mid = get_poly_mid(best_poly)
        if poly_mid is None:
            continue

        # Quality check: both markets should resolve within 60 days
        k_days = days_until_close(km)
        poly_end_date_str = best_poly.get("endDate") or best_poly.get("end_date_iso") or ""
        poly_days = 999
        try:
            if poly_end_date_str:
                poly_end = datetime.fromisoformat(poly_end_date_str.replace("Z", "+00:00"))
                poly_days = max(0, (poly_end - datetime.now(timezone.utc)).days)
        except Exception:
            pass

        if k_days > 60 or poly_days > 60:
            log.debug(
                f"[Polymarket] Skip arb {k_ticker}: resolve too far out "
                f"(kalshi={k_days}d poly={poly_days}d)"
            )
            continue

        spread = abs(k_mid - poly_mid)

        log.info(
            f"[Polymarket] Match ({best_score} kw): {k_ticker} "
            f"| Kalshi={k_mid:.2f} Poly={poly_mid:.2f} spread={spread:.2f} "
            f"kalshi_days={k_days} poly_days={poly_days}"
        )

        if spread >= POLY_ARB_MIN_SPREAD:
            log.info(
                f"[Polymarket] ARB FOUND: {k_ticker} | Kalshi={k_mid:.2f} "
                f"Poly={poly_mid:.2f} spread={spread:.2f}"
            )
            arb_opportunities.append({
                "ticker":        k_ticker,
                "kalshi_title":  k_title,
                "poly_title":    best_match,
                "kalshi_mid":    round(k_mid, 3),
                "poly_mid":      round(poly_mid, 3),
                "arb_spread":    round(spread, 3),
                "match_score":   best_score,
                "poly_volume":   float(best_poly.get("volume", 0) or 0),
            })
        elif spread >= 0.04:
            log.info(
                f"[Polymarket] Near-arb: {k_ticker} | spread={spread:.2f} "
                f"(need {POLY_ARB_MIN_SPREAD}) Kalshi={k_mid:.2f} Poly={poly_mid:.2f}"
            )

    arb_opportunities.sort(key=lambda x: x["arb_spread"], reverse=True)
    log.info(f"[Polymarket] Found {len(arb_opportunities)} arb opportunities (>{POLY_ARB_MIN_SPREAD:.0%} spread)")
    return arb_opportunities


def run_polymarket_sync(watchlist_markets: list = None, dry_run: bool = False) -> list:
    """Refresh Polymarket cache and find arb. Returns arb opportunity list."""
    global polymarket_cache, polymarket_cache_time

    log.info("[Polymarket] Syncing market prices...")
    polymarket_cache      = fetch_polymarket_markets()
    polymarket_cache_time = time.time()

    if not watchlist_markets or not polymarket_cache:
        return []

    arbs = find_polymarket_arb(watchlist_markets, polymarket_cache)

    # Polymarket arb alerts disabled — fuzzy matching produces false positives
    # Polymarket has no GDP markets; spurious matches generate garbage alerts
    # Re-enable when exact market ID matching is implemented
    if POLYMARKET_ENABLED and arbs:
        lines = ["🔀 **POLYMARKET ARB ALERT**", ""]
        for arb in arbs[:5]:  # top 5
            k_c = int(arb["kalshi_mid"] * 100)
            p_c = int(arb["poly_mid"] * 100)
            s_c = int(arb["arb_spread"] * 100)
            if arb["kalshi_mid"] < arb["poly_mid"]:
                cheaper_side = "Kalshi"
                direction = "YES"
            else:
                cheaper_side = "Polymarket"
                direction = "NO"
            lines.append(
                f"🔀 ARB: {arb['ticker']} Kalshi={k_c}¢ Poly={p_c}¢ spread={s_c}¢ "
                f"— buy cheaper side ({cheaper_side}), direction={direction}"
            )
        msg = "\n".join(lines)
        post_discord(msg, dry_run=dry_run)

    return arbs

# ─────────────────────────────────────────────────────────────────────────────
# ODDS VELOCITY TRACKER
# ─────────────────────────────────────────────────────────────────────────────

MAX_PRICE_HISTORY = 20   # max samples per ticker


def update_price_history(ticker: str, mid_price: float, volume: float = 0.0):
    """Record a price + volume sample for velocity tracking."""
    ts = time.time()

    price_history[ticker].append((ts, mid_price))
    volume_history[ticker].append((ts, volume))

    # Trim to last MAX_PRICE_HISTORY samples
    if len(price_history[ticker]) > MAX_PRICE_HISTORY:
        price_history[ticker] = price_history[ticker][-MAX_PRICE_HISTORY:]
    if len(volume_history[ticker]) > MAX_PRICE_HISTORY:
        volume_history[ticker] = volume_history[ticker][-MAX_PRICE_HISTORY:]


def compute_velocity(ticker: str, lookback_sec: float = 60.0) -> dict:
    """
    Compute price velocity (cents/min) and volume acceleration over the last lookback window.
    Returns:
        {
            "velocity_c_per_min": float,
            "velocity_label": str,
            "confidence_boost": float,
            "volume_spike": bool,
            "volume_boost": float,
        }
    """
    result = {
        "velocity_c_per_min": 0.0,
        "velocity_label":     "STABLE",
        "confidence_boost":   0.0,
        "volume_spike":       False,
        "volume_boost":       0.0,
    }

    history = price_history.get(ticker, [])
    if len(history) < 2:
        return result

    now = time.time()
    # Find oldest sample within lookback window
    cutoff = now - lookback_sec
    recent = [(t, p) for t, p in history if t >= cutoff]

    if len(recent) < 2:
        # Fall back to comparing first vs last in history
        recent = [history[0], history[-1]]

    oldest_t, oldest_p = recent[0]
    newest_t, newest_p = recent[-1]
    elapsed_min = max((newest_t - oldest_t) / 60.0, 0.001)

    price_change_c  = abs((newest_p - oldest_p) * 100.0)  # convert to cents
    velocity_c_min  = price_change_c / elapsed_min

    result["velocity_c_per_min"] = round(velocity_c_min, 2)

    if velocity_c_min >= VEL_SPIKE:
        result["velocity_label"]   = "SPIKE"
        result["confidence_boost"] = 0.30
    elif velocity_c_min >= VEL_FAST_MOVE:
        result["velocity_label"]   = "FAST MOVE"
        result["confidence_boost"] = 0.15
    elif velocity_c_min >= VEL_MOVING:
        result["velocity_label"]   = "MOVING"
        result["confidence_boost"] = 0.0   # log only, no boost

    # Volume acceleration
    vol_hist = volume_history.get(ticker, [])
    if len(vol_hist) >= 2:
        recent_vol = [(t, v) for t, v in vol_hist if t >= cutoff]
        if len(recent_vol) >= 2:
            vol_old = recent_vol[0][1]
            vol_new = recent_vol[-1][1]
            if vol_old > 0 and vol_new >= vol_old * VEL_VOL_SPIKE:
                result["volume_spike"] = True
                result["volume_boost"] = 0.20
                log.info(
                    f"[Velocity] {ticker} VOLUME SPIKE: "
                    f"{vol_old:.0f} → {vol_new:.0f} ({vol_new/vol_old:.1f}x)"
                )

    if result["velocity_label"] in ("SPIKE", "FAST MOVE"):
        log.info(
            f"[Velocity] {ticker} {result['velocity_label']}: "
            f"{velocity_c_min:.1f}¢/min | boost={result['confidence_boost']:.2f}"
        )

    return result

# ─────────────────────────────────────────────────────────────────────────────
# MARKET CLASSIFIER — edge-quality tier assignment
# ─────────────────────────────────────────────────────────────────────────────

def classify_market(ticker: str, title: str, category: str, days_until_close_val: int) -> str:
    """Classify market into scoring tier based on edge quality."""
    title_lower  = title.lower()
    ticker_upper = ticker.upper()

    # ── CPI/PCE markets → ECONOMIC_DATA (before other checks) ───────────────
    if ticker_upper.startswith('KXCPI') or ticker_upper.startswith('KXPCE'):
        return 'ECONOMIC_DATA'

    # ── CRYPTO_SHORT — daily BTC/ETH price range markets (must check FIRST) ──
    # These appear in ECONOMIC_CALENDAR for awareness but are CRYPTO_SHORT, not ECONOMIC_DATA
    if ('KXBTCD' in ticker_upper or 'KXETHD' in ticker_upper) and days_until_close_val <= 1:
        return 'CRYPTO_SHORT'

    # ── COMMODITY — Gold, Oil, Silver, S&P price range markets (check before calendar) ─
    commodity_tickers = ['KXGOLDD', 'KXGOLDW', 'KXGOLDMON', 'KXSILVER', 'KXWTI', 'KXBRENT',
                         'KXCOPPER', 'KXSPX', 'KXNDX', 'KXGAS']
    if any(ticker_upper.startswith(p.upper()) for p in commodity_tickers) and days_until_close_val <= 1:
        return 'COMMODITY'

    # ── Economic calendar fast-path ───────────────────────────────────────────
    # If ticker matches an upcoming calendar event within 30 days → ECONOMIC_DATA
    today = datetime.now(timezone.utc).date()
    for (date_str, event_name, prefix) in ECONOMIC_CALENDAR:
        try:
            if date_str == 'daily':
                # Skip KXBTCD/KXETHD here — they're handled above as CRYPTO_SHORT
                if prefix.upper() in ('KXBTCD', 'KXETHD'):
                    continue
                if ticker_upper.startswith(prefix.upper()):
                    log.debug(f"[Classify] {ticker} → ECONOMIC_DATA (daily calendar: {event_name})")
                    return 'ECONOMIC_DATA'
                continue
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_to_event = (event_date - today).days
            if 0 <= days_to_event <= 30 and ticker_upper.startswith(prefix.upper()):
                log.debug(f"[Classify] {ticker} → ECONOMIC_DATA (calendar: {event_name})")
                return 'ECONOMIC_DATA'
        except Exception:
            pass

    # ── JUNK — filter entirely ────────────────────────────────────────────────
    junk_patterns = [
        'say ', 'says ', 'tweet', 'mention', 'utter',
        'leave office', 'resign', 'fired', 'removed',
        'step down', 'quit',
        'pardon', 'arrest', 'indicted',
        'marry', 'divorce', 'dating',
    ]
    if any(p in title_lower for p in junk_patterns):
        return 'JUNK'

    junk_tickers = ['KXTRUMPSAY', 'KXTRUMPMENTION', 'KXTRUMPTWEET',
                    'KXLEAVE', 'KXRESIGN', 'KXPARDONS', 'KXBOYCOT',
                    'KXUSAIRANAGREEMENT']  # political guesses with no data edge
    if any(ticker_upper.startswith(p) for p in junk_tickers):
        return 'JUNK'

    # ── ECONOMIC_DATA — high priority ─────────────────────────────────────────
    econ_patterns = [
        'cpi', 'inflation', 'consumer price', 'nfp', 'payroll',
        'unemployment', 'fomc', 'federal reserve', 'fed rate',
        'interest rate', 'gdp', 'gross domestic', 'pce',
        'personal consumption', 'ppi', 'producer price',
        'jobs report', 'non-farm', 'trade deficit', 'retail sales',
        'housing starts', 'durable goods', 'ism manufacturing',
    ]
    if any(p in title_lower for p in econ_patterns):
        return 'ECONOMIC_DATA'

    # ── Weekly gas price markets (EIA via FRED) ───────────────────────────────
    if ticker_upper.startswith('KXAAAGASW') and days_until_close_val <= 3:
        return 'ECONOMIC_DATA'

    # ── WEATHER ───────────────────────────────────────────────────────────────
    if category in ('Climate and Weather',) or 'temperature' in title_lower or 'weather' in title_lower:
        return 'WEATHER'

    # ── CRYPTO_SHORT — broader crypto (title-based), resolves within 30 days ──
    crypto_patterns = ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto']
    if any(p in title_lower for p in crypto_patterns) and days_until_close_val <= 30:
        return 'CRYPTO_SHORT'

    # ── POLITICAL_LONG — 2028 elections, long nominations ────────────────────
    if days_until_close_val > 365 or '2028' in title or '2027' in title_lower:
        return 'POLITICAL_LONG'
    if 'presidential' in title_lower or 'nomination' in title_lower or 'nominee' in title_lower:
        return 'POLITICAL_LONG'

    # ── Default ───────────────────────────────────────────────────────────────
    return 'POLITICAL_NEWS'


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 7: ORDER BOOK DEPTH ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_order_book(m: dict) -> float:
    """
    Detect order book imbalance. Returns a boost score 0.0-0.20.
    Lopsided YES bid (more buyers than sellers) = smart money accumulating YES.
    Lopsided NO bid (more sellers than buyers) = smart money accumulating NO.
    """
    global _orderbook_boost_count
    yes_bid_size = float(m.get("yes_bid_size_fp") or 0)
    yes_ask_size = float(m.get("yes_ask_size_fp") or 0)

    if yes_bid_size + yes_ask_size < 100:
        return 0.0  # not enough depth to read

    # Imbalance ratio: how lopsided is the book?
    total = yes_bid_size + yes_ask_size
    bid_pct = yes_bid_size / total

    ticker = m.get("ticker", "?")
    log.debug(
        f"[OrderBook] {ticker} | bid_size={yes_bid_size:.0f} ask_size={yes_ask_size:.0f} "
        f"bid_pct={bid_pct:.2f}"
    )

    result = 0.0
    if bid_pct > 0.75:  # heavy YES accumulation
        log.info(f"[OrderBook] {ticker} HEAVY YES ACCUMULATION bid_pct={bid_pct:.2f} → +0.15 boost")
        result = 0.15
    elif bid_pct > 0.65:
        log.info(f"[OrderBook] {ticker} YES LEAN bid_pct={bid_pct:.2f} → +0.08 boost")
        result = 0.08
    elif bid_pct < 0.25:  # heavy NO accumulation (selling pressure on YES)
        log.info(f"[OrderBook] {ticker} HEAVY NO ACCUMULATION bid_pct={bid_pct:.2f} → +0.08 boost")
        result = 0.08  # could be smart money on NO side

    if result > 0:
        _orderbook_boost_count += 1
        if _orderbook_boost_count <= 5:  # Log first 5 hits
            log.info(f'[OB] Order book boost fired! count={_orderbook_boost_count}, score={result:.3f}')
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 8: TIME-OF-DAY DATA RELEASE ALERTS
# ─────────────────────────────────────────────────────────────────────────────

def in_release_window() -> bool:
    """Returns True if we're currently within a data release window."""
    now_utc = datetime.now(timezone.utc)
    h, m = now_utc.hour, now_utc.minute
    for (sh, sm, eh, em) in RELEASE_WINDOWS:
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        now_min = h * 60 + m
        if start_min <= now_min <= end_min:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SCORING — liquidity + whale + velocity + weather signals
# ─────────────────────────────────────────────────────────────────────────────

def score_market(m: dict, weather_signals: dict = None) -> dict:
    """
    Score a single market. Returns a play dict with all scoring components.
    weather_signals: {ticker: signal_dict} for pre-computed weather edges.
    """
    ticker = m.get("ticker", "")
    mid    = get_mid(m)
    liq    = liquidity_score(m)

    # Normalize base confidence by volume proxy
    vol = get_volume(m)
    base_conf = min(1.0, vol / 50000.0) * 0.5 + min(1.0, liq / 1e8) * 0.5

    # Whale stats
    whale_stats = get_whale_stats(ticker)
    yes_count   = whale_stats["yes_count"]
    no_count    = whale_stats["no_count"]

    # Direction
    if yes_count > no_count and yes_count > 0:
        direction = "YES"
    elif no_count > yes_count and no_count > 0:
        direction = "NO"
    else:
        direction = "YES" if mid <= 0.5 else "NO"

    # Time horizon penalty
    d = days_until_close(m)

    # Near-resolution boost — markets closing soon get priority (fast money)
    if 0 < d <= 1:
        near_res_boost = 0.20
    elif 1 < d <= 2:
        near_res_boost = 0.10
    elif 2 < d <= 3:
        near_res_boost = 0.05
    else:
        near_res_boost = 0.0

    if d <= 14:       time_penalty = 0.0
    elif d <= 30:     time_penalty = 0.10
    elif d <= 60:     time_penalty = 0.25
    elif d <= 90:     time_penalty = 0.45
    else:             time_penalty = 0.99

    whale_boost, whale_summary = compute_whale_boost(yes_count, no_count, direction)

    # Weather signal boost
    weather_boost   = 0.0
    weather_note    = ""
    if weather_signals and ticker in weather_signals:
        ws = weather_signals[ticker]
        weather_boost = ws.get("confidence_boost", 0.20)
        weather_note  = (
            f"☁️ Weather edge {ws['edge']:.0%}: "
            f"model={ws['model_prob']:.0%} kalshi={ws['kalshi_prob']:.0%}"
        )

    # Velocity boost
    vel = compute_velocity(ticker)
    vel_boost = vel["confidence_boost"] + vel["volume_boost"]

    final_conf = max(0.0, min(1.0,
        base_conf + whale_boost + weather_boost + vel_boost - time_penalty + near_res_boost
    ))

    conf_label = (
        "HIGH"   if final_conf >= CONFIDENCE_HIGH_THRESHOLD else
        "MEDIUM" if final_conf >= CONFIDENCE_MEDIUM_THRESHOLD else
        "LOW"
    )

    if direction == "YES":
        edge_pct = ((1.0 - mid) / mid * 100) if mid > 0 else 0.0
    else:
        edge_pct = (mid / (1.0 - mid) * 100) if mid < 1.0 else 0.0

    mid_c = int(mid * 100)
    yes_ask = float(m.get("yes_ask_dollars", 0.5) or 0.5)
    yes_ask_c = int(yes_ask * 100)

    bankroll_rec = "3-5%" if conf_label == "HIGH" else ("1-2%" if conf_label == "MEDIUM" else "0%")

    whale_contracts_k = round(
        (whale_stats["yes_contracts"] if direction == "YES" else whale_stats["no_contracts"]) / 1000, 1
    )

    return {
        "ticker":            ticker,
        "title":             m.get("title") or "",
        "category":          m.get("_category", ""),
        "direction":         direction,
        "mid_c":             mid_c,
        "yes_ask_c":         yes_ask_c,
        "edge_pct":          round(edge_pct, 1),
        "whale_summary":     whale_summary,
        "whale_contracts_k": whale_contracts_k,
        "confidence":        final_conf,
        "conf_label":        conf_label,
        "bankroll_rec":      bankroll_rec,
        "yes_whale_count":   yes_count,
        "no_whale_count":    no_count,
        "days_until_close":  d,
        "weather_note":      weather_note,
        "velocity_label":    vel["velocity_label"],
        "velocity_c_min":    vel["velocity_c_per_min"],
        "_market":           m,
    }


def score_and_rank_markets(
    top_by_cat: dict,
    weather_signals: list = None,
    smart_money: dict = None,
    crypto_sigs: dict = None,
) -> tuple:
    """
    Flatten top_by_cat, score top candidates, return ranked plays.
    Uses edge-quality composite scoring: tier_multiplier * (base_liq*0.4 + edge_score*0.6).
    JUNK markets (tier 0.0) are excluded entirely.
    weather_signals: list from find_weather_signals().
    smart_money: dict from run_smart_money_tracker().
    crypto_sigs: dict from run_crypto_monitor().
    Returns (plays_list, whale_events_total).
    """
    from collections import Counter

    # Build lookup by ticker
    ws_by_ticker = {}
    if weather_signals:
        for ws in weather_signals:
            ws_by_ticker[ws["ticker"]] = ws

    # Build weather edge scores (0-1 normalized) for composite
    weather_edge_scores = {}
    if weather_signals:
        for signal in weather_signals:
            t = signal.get("ticker", "")
            e = signal.get("edge", 0.0)
            weather_edge_scores[t] = min(e, 1.0)

    # Merge crypto signals into edge scores
    if crypto_sigs:
        for t, sig in crypto_sigs.items():
            e = sig.get("edge", 0.0)
            weather_edge_scores[t] = max(weather_edge_scores.get(t, 0.0), min(e, 1.0))

    # Flatten all liquid markets
    all_tops = []
    for cat, markets in top_by_cat.items():
        for m in markets:
            all_tops.append((m, liquidity_score(m)))

    if not all_tops:
        return [], 0

    max_liq = max(liq for _, liq in all_tops)
    if max_liq <= 0:
        max_liq = 1.0

    # Compute composite edge-quality score for each market
    scored_markets = []
    junk_count = 0
    for m, liq_val in all_tops:
        ticker        = m.get("ticker", "")
        title         = m.get("title") or ""
        category      = m.get("_category", "")
        d             = days_until_close(m)
        market_class  = classify_market(ticker, title, category, d)
        tier_mult     = CATEGORY_TIERS.get(market_class, 1.0)

        if tier_mult == 0.0:
            log.debug(f"Skipping JUNK market: {ticker} — {title[:60]}")
            junk_count += 1
            continue

        base_liq   = liq_val / max_liq
        edge_score = weather_edge_scores.get(ticker, 0.0)
        composite  = tier_mult * (base_liq * 0.4 + edge_score * 0.6)
        scored_markets.append((m, composite, market_class))

    # Sort by composite score, take top N candidates
    scored_markets.sort(key=lambda x: x[1], reverse=True)
    candidates = scored_markets[:WHALE_FETCH_TOP_N]

    log.info(
        f"[Scoring] {junk_count} JUNK excluded | "
        f"{len(candidates)} candidates after edge-quality filter"
    )

    plays              = []
    whale_events_total = 0

    for m, composite, market_class in candidates:
        ticker = m.get("ticker", "")
        if not ticker:
            continue

        play = score_market(m, ws_by_ticker)
        play["market_class"]    = market_class
        tier_mult               = CATEGORY_TIERS.get(market_class, 1.0)
        play["tier_multiplier"] = tier_mult
        # Recompute base_liq for this market (needed for econ edge composite override)
        _liq_val = liquidity_score(m)
        base_liq = _liq_val / max_liq if max_liq > 0 else 0.0

        # Store yes_bid_dollars for direction-aware market taker execution
        play["yes_bid_dollars"] = float(m.get("yes_bid_dollars") or 0.0)

        # EconModel: data-driven edge override for ECONOMIC_DATA markets
        mid = get_mid(m)
        if market_class == "ECONOMIC_DATA":
            model_prob, econ_edge, econ_direction, econ_source = calculate_economic_edge(ticker, mid)
            if model_prob is not None and abs(econ_edge) > 0.10:
                # Override composite with data-driven edge — edge dominates
                edge_score = min(abs(econ_edge), 1.0)
                composite = tier_mult * (base_liq * 0.2 + edge_score * 0.8)
                play["econ_edge"]      = round(econ_edge, 3)
                play["econ_direction"] = econ_direction
                play["econ_source"]    = econ_source
                # Override signal direction with model direction
                play["direction"] = econ_direction
                log.info(
                    f"[EconModel] {ticker}: model={model_prob:.0%} kalshi={mid:.0%} "
                    f"edge={econ_edge:+.2f} → {econ_direction} ({econ_source})"
                )

        # GDPNow direct edge calculator for KXGDP markets
        if market_class == "ECONOMIC_DATA" and 'KXGDP' in ticker.upper():
            model_prob, econ_edge, econ_dir, econ_src = calculate_gdp_edge(ticker, mid)
            if model_prob is not None:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir  # override direction with data model
                if abs(econ_edge) > 0.10:
                    # Boost composite score proportional to edge
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[EconModel] {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} → {econ_dir} ({econ_src})")

        # Cleveland Fed CPI edge calculator for KXCPI/KXPCE markets
        if market_class == "ECONOMIC_DATA" and (ticker.upper().startswith('KXCPI') or ticker.upper().startswith('KXPCE')):
            model_prob, econ_edge, econ_dir, econ_src = calculate_cpi_edge(ticker, mid)
            if model_prob is not None:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[Cleveland] {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} → {econ_dir} ({econ_src})")

        # Crypto edge calculator for KXBTCD/KXETHD daily price range markets
        if market_class == 'CRYPTO_SHORT' and ('KXBTCD' in ticker.upper() or 'KXETHD' in ticker.upper()):
            model_prob, econ_edge, econ_dir, econ_src = calculate_crypto_edge(ticker, mid)
            if model_prob is not None and abs(econ_edge) > 0.08:  # lower threshold for crypto
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[CryptoEdge] Wired {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} → {econ_dir}")

        # Commodity edge calculator for KXGOLD/KXWTI/KXSPX etc.
        if market_class == 'COMMODITY':
            model_prob, econ_edge, econ_dir, econ_src = calculate_commodity_edge(ticker, mid)
            if model_prob is not None and abs(econ_edge) > 0.08:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[CommodityEdge] Wired {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} → {econ_dir}")

        # Feature 7: Order book depth analysis
        orderbook_boost = analyze_order_book(m)
        if orderbook_boost > 0:
            composite = round(composite + orderbook_boost, 4)
            play["confidence"] = min(1.0, play["confidence"] + orderbook_boost)
            play["conf_label"] = (
                "HIGH"   if play["confidence"] >= CONFIDENCE_HIGH_THRESHOLD else
                "MEDIUM" if play["confidence"] >= CONFIDENCE_MEDIUM_THRESHOLD else
                "LOW"
            )
        play["orderbook_boost"] = orderbook_boost
        play["composite_score"] = round(composite, 4)

        # Smart money boost
        if smart_money and ticker in smart_money:
            sm = smart_money[ticker]
            smart_money_boost = 0.25
            play["confidence"] = min(1.0, play["confidence"] + smart_money_boost)
            play["conf_label"] = (
                "HIGH"   if play["confidence"] >= CONFIDENCE_HIGH_THRESHOLD else
                "MEDIUM" if play["confidence"] >= CONFIDENCE_MEDIUM_THRESHOLD else
                "LOW"
            )
            play["composite_score"] = round(play["composite_score"] + smart_money_boost, 4)
            play["smart_money_note"] = (
                f"SMART MONEY: {ticker} — top Polymarket trader positioned "
                f"{sm['poly_side']} @ {sm['poly_price']:.2f}"
            )
            log.info(play["smart_money_note"])

        # Crypto signal boost
        if crypto_sigs and ticker in crypto_sigs:
            cs = crypto_sigs[ticker]
            crypto_boost = cs.get("confidence_boost", 0.15)
            play["confidence"] = min(1.0, play["confidence"] + crypto_boost)
            play["conf_label"] = (
                "HIGH"   if play["confidence"] >= CONFIDENCE_HIGH_THRESHOLD else
                "MEDIUM" if play["confidence"] >= CONFIDENCE_MEDIUM_THRESHOLD else
                "LOW"
            )
            play["composite_score"] = round(play["composite_score"] + crypto_boost, 4)
            play["crypto_note"] = (
                f"CRYPTO: {cs['symbol']} spot ${cs['spot_price']:,.0f} vs "
                f"threshold ${cs['threshold']:,.0f} | edge={cs['edge']:.2f}"
            )
            log.info(f"[Crypto] Boosted {ticker}: {play['crypto_note']}")

        whale_events_total += play["yes_whale_count"] + play["no_whale_count"]

        # Update price history for velocity tracking
        update_price_history(ticker, get_mid(m), get_volume_24h(m))

        log.info(
            f"  Scored {ticker} | class={market_class} tier={play['tier_multiplier']:.1f}x "
            f"composite={composite:.4f} | conf={play['confidence']:.2f} ({play['conf_label']}) | "
            f"whale YES={play['yes_whale_count']} NO={play['no_whale_count']} | "
            f"velocity={play['velocity_label']}"
        )
        plays.append(play)
        time.sleep(0.1)

    # Include weather-signal markets not already in candidates
    ticker_set = {p["ticker"] for p in plays}
    if weather_signals:
        for ws in weather_signals:
            if ws["ticker"] not in ticker_set:
                m_raw = ws.get("_market")
                if m_raw:
                    play = score_market(m_raw, ws_by_ticker)
                    e    = weather_edge_scores.get(ws["ticker"], 0.0)
                    play["market_class"]    = "WEATHER"
                    play["tier_multiplier"] = CATEGORY_TIERS["WEATHER"]
                    play["composite_score"] = round(CATEGORY_TIERS["WEATHER"] * (0.0 * 0.4 + e * 0.6), 4)
                    plays.append(play)

    # Final sort: composite score, then confidence as tiebreaker
    plays.sort(key=lambda x: (x.get("composite_score", 0.0), x["confidence"]), reverse=True)

    # Log class breakdown
    class_counts = Counter(p.get("market_class", "?") for p in plays)
    log.info(f"[Scoring] Class breakdown: {dict(class_counts)} | JUNK excluded: {junk_count}")

    return plays, whale_events_total

# ─────────────────────────────────────────────────────────────────────────────
# GDP STOP-LOSS MONITOR
# ─────────────────────────────────────────────────────────────────────────────

def check_stop_losses(dry_run: bool = False):
    """Check if any open position has hit stop-loss threshold."""
    for ticker, rule in STOP_LOSS_RULES.items():
        try:
            data   = kalshi_get(f"/markets/{ticker}")
            market = data.get("market", {})
            yes_bid = float(market.get("yes_bid_dollars", 0) or 0)
            no_mid  = round(1.0 - yes_bid, 3)

            if rule["direction"] == "NO":
                stop_threshold = rule.get("stop_if_no_below", 0.55)
                if no_mid < stop_threshold:
                    log.warning(
                        f"[STOPLOSS] {ticker}: NO price={no_mid:.2f} < threshold={stop_threshold} "
                        f"— STOP TRIGGERED"
                    )
                    if not dry_run:
                        msg = (
                            f"🛑 STOP LOSS TRIGGERED — {ticker}\n"
                            f"NO price dropped to {int(no_mid*100)}¢ "
                            f"(below {int(stop_threshold*100)}¢ threshold)\n"
                            f"GDPNow still: {fetch_gdpnow_realtime() or 'unavailable'}%\n"
                            f"Action: Review position — consider closing"
                        )
                        post_discord(msg, channel_id=DISCORD_CH_RESULTS)
                else:
                    log.debug(f"[STOPLOSS] {ticker}: NO={no_mid:.2f} OK (>{stop_threshold})")
        except Exception as e:
            log.debug(f"[STOPLOSS] Error checking {ticker}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def _load_donnie_token() -> str:
    token = os.environ.get("DONNIE_TOKEN", "")
    if token:
        return token
    try:
        with open(BOT_TOKENS_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DONNIE_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        log.error(f"Could not load DONNIE_TOKEN: {e}")
    return ""


DONNIE_TOKEN = _load_donnie_token()


def post_discord(message: str, channel_id: int = DISCORD_CH_KALSHI, dry_run: bool = False) -> bool:
    """Post message to a Discord channel via Donnie bot (max 2000 chars per chunk)."""
    if dry_run:
        print("\n" + "─" * 60)
        print(f"[DRY RUN — Discord → channel {channel_id}]")
        print(message[:2000])
        print("─" * 60)
        return True

    if not DONNIE_TOKEN:
        log.error("DONNIE_TOKEN not set — cannot post to Discord")
        return False

    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {DONNIE_TOKEN}", "Content-Type": "application/json"}

    chunks = [message[i:i+1990] for i in range(0, len(message), 1990)]
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                log.error(f"Discord post failed: {r.status_code} {r.text[:200]}")
                return False
            time.sleep(0.5)
        except Exception as e:
            log.error(f"Discord request error: {e}")
            return False
    return True


def format_donnie_report(
    plays: list,
    total_scanned: int,
    n_cats: int,
    whale_events: int,
    weather_signals: list = None,
    arb_opps: list = None,
) -> str:
    """
    Format the single curated Donnie report Discord post.
    Returns empty string if no plays meet MEDIUM+ threshold.
    Now includes weather and polymarket arb sections.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    actionable = [p for p in plays if p["conf_label"] in ("HIGH", "MEDIUM")][:TOP_PLAYS_DISPLAY]

    if not actionable:
        return ""

    sep = "━" * 24

    lines = [
        f"🎯 **DONNIE REPORT** — {ts}",
        f"{total_scanned:,} markets scanned | {n_cats} categories | {whale_events} whale events tracked",
        "",
        sep,
        "🏆 **TOP PLAYS RIGHT NOW**",
        sep,
        "",
    ]

    for i, p in enumerate(actionable, 1):
        title_short     = p["title"][:60]
        whale_k_display = f"{p['whale_contracts_k']}k" if p["whale_contracts_k"] > 0 else "—"
        vel_display     = f" | ⚡ {p['velocity_label']}" if p["velocity_label"] != "STABLE" else ""
        weather_display = f"\n   {p['weather_note']}" if p.get("weather_note") else ""
        lines += [
            f"**{i}. {p['ticker']}** — {title_short}",
            f"   Position: BUY {p['direction']} @ {p['mid_c']}¢ | Edge: +{p['edge_pct']}%{vel_display}",
            f"   Whale signal: {p['whale_summary']} ({whale_k_display} contracts){weather_display}",
            f"   Confidence: {p['conf_label']} | Rec: {p['bankroll_rec']} bankroll",
            "",
        ]

    # Weather signals section
    if weather_signals:
        lines += [sep, "☁️ **WEATHER SIGNALS**", ""]
        for ws in weather_signals[:3]:
            lines.append(
                f"  {ws['city']} — forecast {ws['forecast_temp']}°F vs {ws['threshold']:.0f}°F threshold | "
                f"model={ws['model_prob']:.0%} kalshi={ws['kalshi_prob']:.0%} | edge={ws['edge']:.0%} → **{ws['ticker']}**"
            )
        lines.append("")

    # Polymarket arb section
    if arb_opps:
        lines += [sep, "🔀 **POLYMARKET ARB**", ""]
        for arb in arb_opps[:3]:
            k_c = int(arb["kalshi_mid"] * 100)
            p_c = int(arb["poly_mid"] * 100)
            s_c = int(arb["arb_spread"] * 100)
            lines.append(
                f"  ARB: {arb['kalshi_title'][:55]} — "
                f"Kalshi: {k_c}¢ vs Poly: {p_c}¢ | Spread: {s_c}¢"
            )
        lines.append("")

    lines += [
        sep,
        f"Next discovery: 30min | Realtime monitor: 30sec | Weather: 15min | Poly: 5min",
    ]

    return "\n".join(lines)


def format_whale_update(ticker: str, title: str, alert: dict) -> str:
    contracts = int(alert["total_contracts"])
    direction = alert["direction"].replace("BUY ", "")
    return (
        f"🐋 **WHALE UPDATE** — `{ticker}`\n"
        f"{title[:80]}\n"
        f"New accumulation: +{contracts:,} contracts {direction} in last 15min\n"
        f"This market was already flagged — conviction increasing."
    )


def format_tier2_alert(ticker: str, title: str, trigger: str, play: dict) -> str:
    return (
        f"⚡ **TIER2 TRIGGER** — `{ticker}`\n"
        f"{title[:80]}\n"
        f"Trigger: {trigger}\n"
        f"Re-scored: BUY {play['direction']} @ {play['mid_c']}¢ | "
        f"Conf: {play['conf_label']} ({play['confidence']:.2f}) | "
        f"Velocity: {play['velocity_label']}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION ENGINE (unchanged from v2)
# ─────────────────────────────────────────────────────────────────────────────

def get_balance() -> float:
    data = kalshi_get("/portfolio/balance")
    balance_cents   = data.get("balance", 0.0)
    balance_dollars = float(balance_cents) / 100.0
    log.info(f"[EXEC] Portfolio balance: ${balance_dollars:.2f} (raw: {balance_cents}¢)")
    return balance_dollars


def get_open_orders_tickers() -> set:
    data   = kalshi_get("/portfolio/orders", params={"status": "resting"})
    orders = data.get("orders", [])
    tickers = {o.get("ticker", "") for o in orders if o.get("ticker")}
    if tickers:
        log.info(f"[EXEC] Open orders on tickers: {tickers}")
    return tickers


def get_open_positions() -> dict:
    data      = kalshi_get("/portfolio/positions")
    positions = data.get("market_positions", [])
    result    = {}
    for pos in positions:
        ticker         = pos.get("ticker", "")
        exposure       = float(pos.get("market_exposure_dollars", 0.0) or 0.0)
        position_count = pos.get("position", 0) or 0
        # Include if either has exposure OR has non-zero position count
        if ticker and (exposure > 0 or position_count != 0):
            result[ticker] = exposure
    # Also include resting orders
    for ticker in get_open_orders_tickers():
        if ticker not in result:
            result[ticker] = 0.0
    log.info(f"[EXEC] Open positions+orders ({len(result)}): {sorted(result.keys())}")
    return result


def get_total_exposure(positions: dict = None) -> float:
    if positions is None:
        positions = get_open_positions()
    total = sum(positions.values())
    log.info(f"[EXEC] Total exposure: ${total:.2f}")
    return total


def calculate_contracts(signal: dict, balance: float) -> int:
    conf = signal.get("confidence", 0.0)
    days = signal.get("days_until_close", 90)

    if conf >= 0.85:
        size_pct = 0.08
    elif conf >= 0.75:
        size_pct = 0.06
    else:
        size_pct = 0.04

    if days <= 3:
        size_pct = min(size_pct * 1.5, 0.12)

    price_dollars = max(signal.get("mid_c", 50) / 100.0, 0.01)
    base_spend = balance * size_pct
    max_spend = balance * EXEC_MAX_PER_POSITION
    spend = min(base_spend, max_spend)
    contracts = max(1, math.floor(spend / price_dollars))

    return contracts


def should_execute(signal: dict, balance: float, positions: dict, total_exposure: float) -> tuple:
    """
    Check all guardrails. Returns (ok: bool, reason: str).
    NON-NEGOTIABLE — never bypass.
    """
    ticker    = signal.get("ticker", "UNKNOWN")
    direction = signal.get("direction", "YES")
    mid_c     = signal.get("mid_c", 50)
    conf      = signal.get("confidence", 0.0)
    days_out  = signal.get("days_until_close", 999)

    if days_out > 90:
        return False, f"market resolves in {days_out} days (>90 day auto-exec limit)"

    # Only execute on categories where we have data model edge
    EXECUTABLE_CATEGORIES = {"ECONOMIC_DATA", "CRYPTO_SHORT", "COMMODITY"}  # WEATHER is Mark Hanna/weather.py's domain
    market_class = signal.get("market_class", "")
    if market_class not in EXECUTABLE_CATEGORIES:
        return False, f"category '{market_class}' is inform-only — no autonomous execution"

    if conf < CONFIDENCE_HIGH_THRESHOLD:
        return False, f"confidence {conf:.2f} below HIGH threshold {CONFIDENCE_HIGH_THRESHOLD}"

    # ── FIX (Apr 21): Use directional model edge, not payout edge ─────────────
    # Payout edge ((1-price) or price) can be positive even when the model says
    # the trade has negative directional edge. We must check model edge first.
    price_dollars = mid_c / 100.0
    model_edge = signal.get("edge", None)
    if model_edge is not None:
        # model_edge is always positive (abs value). Check direction matches signal side.
        signal_side = signal.get("side", direction).upper()
        directional_edge = model_edge if signal_side == direction.upper() else -model_edge
        if directional_edge < EXEC_MIN_EDGE_DOLLARS:
            return False, ("model edge {:+.3f} < min +{:.2f} (HARD BLOCK — negative edge rejected)".format(
                directional_edge, EXEC_MIN_EDGE_DOLLARS))
    else:
        edge_dollars = (1.0 - price_dollars) if direction == "YES" else price_dollars
        if edge_dollars < EXEC_MIN_EDGE_DOLLARS:
            return False, ("edge ${:.2f} < min ${:.2f}".format(edge_dollars, EXEC_MIN_EDGE_DOLLARS))

    # ── FIX (Apr 21): Crypto/Commodity near-expiry hard cutoff ───────────────
    if market_class in ("CRYPTO_SHORT", "COMMODITY"):
        close_time_str = signal.get("close_time", "")
        if close_time_str:
            try:
                ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                minutes_to_close = (ct - datetime.now(timezone.utc)).total_seconds() / 60
                if minutes_to_close < CRYPTO_MIN_MINUTES_TO_CLOSE:
                    return False, ("CRYPTO near-expiry block: {:.0f} min to close < {} min cutoff".format(
                        minutes_to_close, CRYPTO_MIN_MINUTES_TO_CLOSE))
            except Exception:
                pass

        # ── FIX (Apr 21): Minimum buffer — spot must be >0.5% from threshold ─
        spot_price   = signal.get("spot_price")
        threshold    = signal.get("threshold")
        if spot_price and threshold:
            buffer_pct = abs(spot_price - threshold) / threshold
            if buffer_pct < CRYPTO_MIN_BUFFER_PCT:
                return False, ("spot buffer {:.3%} < {:.1%} min — too close to threshold".format(
                    buffer_pct, CRYPTO_MIN_BUFFER_PCT))

        # ── FIX (Apr 21): Momentum conflict check ────────────────────────────
        # If we're betting NO on an above-threshold market but price has been
        # rising all day toward threshold, reject (order book likely market-maker hedging).
        change_24h   = signal.get("change_24h", 0.0)
        above_market = signal.get("above", True)
        if direction == "NO" and above_market:
            hourly_est = change_24h / 24
            if hourly_est > 0.5:
                return False, ("momentum conflict: NO on above-threshold but 24h={:+.2f}% (~{:+.2f}%/hr toward threshold)".format(
                    change_24h, hourly_est))
        elif direction == "YES" and not above_market:
            hourly_est = change_24h / 24
            if hourly_est < -0.5:
                return False, ("momentum conflict: YES on below-threshold but 24h={:+.2f}% (~{:+.2f}%/hr toward threshold)".format(
                    change_24h, hourly_est))

    if ticker in positions:
        return False, f"already have open position in {ticker}"

    # Thesis direction lock — never trade against hard-coded thesis
    locked_direction = _get_active_thesis_locks().get(ticker)
    if locked_direction and direction.upper() != locked_direction:
        return False, f"thesis lock: {ticker} must be {locked_direction}, signal says {direction}"

    total_balance = balance + total_exposure
    deployed_pct  = total_exposure / total_balance if total_balance > 0 else 0.0
    if deployed_pct >= EXEC_MAX_TOTAL_DEPLOYED:
        return False, f"total deployed {deployed_pct*100:.1f}% >= {EXEC_MAX_TOTAL_DEPLOYED*100:.0f}%"

    contracts = calculate_contracts(signal, balance)
    if contracts <= 0:
        return False, f"calculated 0 contracts (balance=${balance:.2f} price={mid_c}¢)"

    cost = contracts * price_dollars
    if cost > balance * EXEC_MAX_PER_POSITION:
        return False, f"cost ${cost:.2f} > 35% of balance"
    if cost > balance:
        return False, f"cost ${cost:.2f} exceeds available balance ${balance:.2f}"

    return True, "all guardrails passed"


def execute_trade(signal: dict, dry_run: bool = False) -> dict:
    ticker    = signal.get("ticker", "")
    direction = signal.get("direction", "YES")
    mid_c     = signal.get("mid_c", 50)

    balance   = get_balance()
    contracts = calculate_contracts(signal, balance)

    if contracts <= 0:
        return {"status": "skipped", "error": "0 contracts calculated", "cost": 0.0}

    # ── Direction from signal ─────────────────────────────────────────────────
    direction = signal.get("direction", "YES").upper()

    # ── Market taker mode for high-confidence time-sensitive plays ────────────
    market_class = signal.get("market_class", "")
    use_market_order = (
        signal.get("confidence", 0) >= MARKET_TAKER_THRESHOLD
        and market_class in MARKET_TAKER_CATEGORIES
    )
    if use_market_order:
        if direction == "YES":
            # Hit the YES ask for immediate fill
            price_c = signal.get("yes_ask_c", signal.get("mid_c", 50))
            log.info(f"[EXEC] Market taker mode for {ticker} (YES) — hitting YES ask @ {price_c}c")
        else:
            # Direction is NO — hit the NO ask (= 100 - yes_bid)
            yes_bid_c = int(float(signal.get("yes_bid_dollars", signal.get("mid_c", 50) / 100)) * 100) \
                if "yes_bid_dollars" in signal else signal.get("mid_c", 50)
            price_c = 100 - yes_bid_c  # NO ask ≈ 100 - YES bid
            log.info(f"[EXEC] Market taker mode for {ticker} (NO) — hitting NO ask @ {price_c}c (yes_bid={yes_bid_c}c)")
    else:
        price_c = mid_c

    side             = "yes" if direction == "YES" else "no"
    price_cents_int  = int(round(price_c))
    client_order_id  = f"donnie-{uuid.uuid4()}"
    price_dollars    = price_cents_int / 100.0
    cost             = round(contracts * price_dollars, 2)

    order_body = {
        "ticker":          ticker,
        "client_order_id": client_order_id,
        "type":            "limit",
        "action":          "buy",
        "side":            side,
        "count":           contracts,
        "yes_price":       price_cents_int,
    }

    log.info(
        f"[EXEC] {'[DRY RUN] ' if dry_run else ''}Placing order: "
        f"{ticker} BUY {direction} @ {price_cents_int}¢ x{contracts} (cost=${cost:.2f})"
    )

    if dry_run:
        return {
            "status":    "dry_run",
            "order_id":  f"dry-run-{client_order_id}",
            "filled":    0,
            "remaining": contracts,
            "cost":      cost,
            "contracts": contracts,
            "price_c":   price_cents_int,
            "direction": direction,
        }

    resp  = kalshi_post("/portfolio/orders", order_body)

    if "error" in resp and "order" not in resp:
        log.error(f"[EXEC] Order FAILED for {ticker}: {resp.get('error')}")
        return {
            "status":    "failed",
            "error":     resp.get("error", "unknown error"),
            "cost":      cost,
            "contracts": contracts,
            "price_c":   price_cents_int,
            "direction": direction,
        }

    order = resp.get("order", {})
    result = {
        "status":    order.get("status", "unknown"),
        "order_id":  order.get("order_id", client_order_id),
        "filled":    order.get("filled_count", 0),
        "remaining": order.get("remaining_count", contracts),
        "cost":      cost,
        "contracts": contracts,
        "price_c":   price_cents_int,
        "direction": direction,
    }
    log.info(
        f"[EXEC] Order result: {ticker} status={result['status']} "
        f"filled={result['filled']} order_id={result['order_id']}"
    )
    return result


def post_execution_result(signal: dict, result: dict, dry_run: bool = False):
    ticker    = signal.get("ticker", "UNKNOWN")
    title     = signal.get("title", "")[:80]
    direction = result.get("direction", signal.get("direction", "YES"))
    price_c   = result.get("price_c", signal.get("mid_c", 0))
    contracts = result.get("contracts", 0)
    cost      = result.get("cost", 0.0)
    status    = result.get("status", "unknown")

    if status in ("failed", "dry_run") and result.get("error"):
        msg = (
            f"❌ DONNIE ORDER FAILED — {ticker}\n"
            f"Error: {result.get('error', 'unknown')}\n"
            f"Action: flagged for manual review"
        )
    else:
        # Clarify status language — "resting" means order is in the book waiting to match
        if status == "resting":
            status_emoji = "🟡"
            status_display = "resting in order book (waiting to match)"
            status_note = "⚠️ Order is PLACED but not yet filled — will fill when market price reaches your bid."
        elif status in ("filled", "executed"):
            status_emoji = "✅"
            status_display = "filled — trade matched!"
            status_note = "Trade confirmed. Holding to expiration."
        elif status == "dry_run":
            status_emoji = "🧪"
            status_display = "dry run (not placed)"
            status_note = ""
        else:
            status_emoji = "⚠️"
            status_display = status
            status_note = ""
        try:
            balance_str = f"${get_balance():.2f}"
        except Exception:
            balance_str = "N/A"
        msg = (
            f"📋 DONNIE ORDER PLACED — {ticker}\n"
            f"{title}\n"
            f"Side: BUY {direction} @ {price_c}¢\n"
            f"Contracts: {contracts} | Cost if filled: ${cost:.2f}\n"
            f"Order status: {status_display} {status_emoji}\n"
            + (f"{status_note}\n" if status_note else "")
            + f"Balance: {balance_str}"
        )

    post_discord(msg, channel_id=DISCORD_CH_RESULTS, dry_run=dry_run)


def run_execution_check(plays: list, dry_run: bool = False):
    """Check HIGH-confidence signals and execute if guardrails pass."""
    high_signals = [p for p in plays if p["confidence"] >= CONFIDENCE_HIGH_THRESHOLD]
    log.info(f"[EXEC] {len(high_signals)} HIGH confidence signals to evaluate")

    for signal in high_signals:
        try:
            balance    = get_balance()
            positions  = get_open_positions()
            total_exp  = get_total_exposure(positions)
            ok, reason = should_execute(signal, balance, positions, total_exp)

            if ok:
                # ── Gate 6 — LLM reasoning check (ECONOMIC_DATA tier, edge > 20%) ──────
                _market_class_g6 = signal.get('market_class', '')
                _econ_edge_g6    = abs(signal.get('econ_edge', 0.0))
                if _market_class_g6 == 'ECONOMIC_DATA' and _econ_edge_g6 > 0.20:
                    try:
                        import sys as _sys_g6
                        if '/home/cody/stratton/bots' not in _sys_g6.path:
                            _sys_g6.path.insert(0, '/home/cody/stratton/bots')
                        from llm_client import llm_reason, trade_review_prompt
                        _g6_ticker  = signal.get('ticker', '')
                        _g6_dir     = signal.get('direction', 'YES')
                        _g6_mid_c   = signal.get('mid_c', 50)
                        _g6_src     = signal.get('econ_source', 'quant model')
                        _g6_prompt  = trade_review_prompt(
                            market        = _g6_ticker,
                            direction     = "YES" if _g6_dir.upper() == "YES" else "NO",
                            edge_pct      = _econ_edge_g6 * 100,
                            data_summary  = (
                                f"Model edge: {_econ_edge_g6:.2f} | "
                                f"Market mid: {_g6_mid_c}c | "
                                f"Source: {_g6_src}"
                            ),
                            macro_context = "Tariff environment, Iran energy shock, Fed on hold",
                        )
                        # Shadow consensus for high-edge trades (>30pt)
                        _use_shadow = _econ_edge_g6 > 0.30
                        _g6_result = llm_reason(
                            _g6_prompt,
                            primary="grok",
                            shadow="claude" if _use_shadow else None,
                            require_consensus=_use_shadow
                        )
                        if not _g6_result.get("go", True):  # default True if LLM fails
                            _g6_reason = _g6_result.get('reasoning', 'no reason')[:100]
                            log.info(f"[DONNIE] LLM gate BLOCKED {_g6_ticker}: {_g6_reason}")
                            post_discord(
                                "\U0001f9e0 LLM BLOCK: " + _g6_ticker + " | " + _g6_result.get('reasoning', '')[:200],
                                channel_id=DISCORD_CH_RESULTS,
                                dry_run=dry_run,
                            )
                            continue  # skip this candidate — LLM said no
                        _g6_conf = _g6_result.get('confidence', 'unknown')
                        _g6_rsn  = _g6_result.get('reasoning', '')[:80]
                        log.info(f"[DONNIE] LLM gate PASSED {_g6_ticker}: confidence={_g6_conf} | {_g6_rsn}")
                    except Exception as _g6_err:
                        log.warning(f"[DONNIE] LLM gate error (trade proceeds): {_g6_err}")
                        # LLM failure = never block the trade (graceful degradation)

                log.info(f"[EXEC] ✅ Executing {signal['ticker']}{' (DRY RUN)' if dry_run else ''}")
                result = execute_trade(signal, dry_run=dry_run)
                post_execution_result(signal, result, dry_run=dry_run)
                # ── Eval Framework: log trade at entry ────────────────────────
                if result.get('status') not in ('failed', 'skipped'):
                    try:
                        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                        from eval_framework import log_trade_entry as _ef_log
                        _ef_log(
                            trade_id=signal.get('ticker', 'unknown'),
                            agent='donnie',
                            market=signal.get('ticker', 'unknown'),
                            direction=signal.get('direction', 'YES'),
                            entry_edge_pct=abs(signal.get('edge_dollars', 0)) * 100,
                            llm_confidence=str(signal.get('confidence', '')),
                            raw_thesis=f"mid={signal.get('mid_c', 0)}c model_prob={signal.get('model_prob', 0):.3f}",
                            raw_llm_reason=signal.get('llm_reason', '')[:500] if signal.get('llm_reason') else '',
                        )
                    except Exception:
                        pass  # eval logging never blocks execution
            else:
                log.info(f"[EXEC] Skipping {signal['ticker']}: {reason}")

        except Exception as e:
            log.error(f"[EXEC] Error processing {signal.get('ticker', '?')}: {e}", exc_info=True)

# ─────────────────────────────────────────────────────────────────────────────
# TIER 1 — DISCOVERY SCAN
# ─────────────────────────────────────────────────────────────────────────────

def run_discovery_scan(dry_run: bool = False) -> tuple:
    """
    Full market scan. Updates global watchlist for Tier 2.
    Returns (all_markets_flat, plays, weather_signals, arb_opps).
    """
    global watchlist, last_scan_top_markets, weather_cache, polymarket_cache

    log.info("=" * 60)
    log.info("TIER 1 — DISCOVERY SCAN starting")
    log.info("=" * 60)

    events = get_all_open_events_with_markets()
    if not events:
        log.error("No events returned — check auth/connectivity")
        return [], [], [], []

    all_markets = flatten_events_to_markets(events)
    log.info(f"Total markets: {len(all_markets)}")

    # Explicitly add daily price markets (BTC/ETH/Gold/Oil) — they may not surface via normal scoring
    daily_price_markets = fetch_daily_price_markets()
    if daily_price_markets:
        all_markets.extend(daily_price_markets)
        log.info(f"[DailyPrice] Added {len(daily_price_markets)} price markets to scan pool")

    grouped    = group_by_category(all_markets)
    top_by_cat = top_markets_per_category(grouped)
    log.info(f"Categories with liquid markets: {len(top_by_cat)}")

    # Weather is Mark Hanna's domain — Donnie does not process weather signals
    weather_signals = []

    # Score everything (inject smart money + crypto signals)
    log.info(f"Scoring top {WHALE_FETCH_TOP_N} candidates...")
    plays, whale_events = score_and_rank_markets(
        top_by_cat,
        weather_signals=weather_signals,
        smart_money=smart_money_signals,
        crypto_sigs=crypto_signals,
    )

    # Update watchlist (top WATCHLIST_SIZE markets for Tier 2)
    watchlist = [p["_market"] for p in plays[:WATCHLIST_SIZE] if "_market" in p]
    last_scan_top_markets = [p["ticker"] for p in plays if p["conf_label"] in ("HIGH", "MEDIUM")]
    log.info(f"Watchlist updated: {len(watchlist)} markets | Top radar: {len(last_scan_top_markets)}")

    # News RSS scan — apply +0.08 confidence boost for matching headlines
    news_matches = run_news_scan(watchlist, dry_run=dry_run)
    if news_matches:
        news_tickers = {m[0] for m in news_matches}
        # Build sentiment lookup: ticker → list of sentiments
        news_sentiment_map: dict = {}
        for (t, headline, sentiment) in news_matches:
            news_sentiment_map.setdefault(t, []).append(sentiment)

        log.info(f"[News] {len(news_matches)} headline matches across {len(news_tickers)} tickers")
        for play in plays:
            ticker = play["ticker"]
            if ticker not in news_tickers:
                continue
            old_conf = play["confidence"]
            # Base boost for any news match
            play["confidence"] = min(1.0, play["confidence"] + 0.08)

            # Extra +0.10 sentiment-aligned boost for ECONOMIC_DATA markets
            if play.get("market_class") == "ECONOMIC_DATA":
                sentiments = news_sentiment_map.get(ticker, [])
                for sentiment in sentiments:
                    if (sentiment == "bullish" and play["direction"] == "YES") or \
                       (sentiment == "bearish" and play["direction"] == "NO"):
                        play["confidence"] = min(play["confidence"] + 0.10, 1.0)
                        log.info(
                            f"[News] Confidence boost for {ticker}: "
                            f"{sentiment} news aligns with {play['direction']}"
                        )
                        break  # only boost once per market

            play["conf_label"] = (
                "HIGH"   if play["confidence"] >= CONFIDENCE_HIGH_THRESHOLD else
                "MEDIUM" if play["confidence"] >= CONFIDENCE_MEDIUM_THRESHOLD else
                "LOW"
            )
            log.info(
                f"[News] Boosted {play['ticker']}: conf {old_conf:.2f} → {play['confidence']:.2f}"
            )

    # Polymarket arb (use cached if fresh enough)
    arb_opps = []
    if POLYMARKET_ENABLED:
        if polymarket_cache:
            arb_opps = find_polymarket_arb(watchlist, polymarket_cache)
        else:
            arb_opps = run_polymarket_sync(watchlist, dry_run=dry_run)

    # Silent mode — only post to Discord on trade execution or arb alerts
    # Scan reports are suppressed to avoid noise. Daily heartbeat handled separately.
    report = format_donnie_report(plays, len(all_markets), len(top_by_cat), whale_events,
                                   weather_signals=weather_signals, arb_opps=arb_opps)
    if report:
        log.info(f"Scan found {len([p for p in plays if p.get('conf_label') in ('HIGH','MEDIUM')])} actionable plays — suppressing Discord report (silent mode)")
    else:
        log.info("No actionable plays this scan")

    # Execution check
    run_execution_check(plays, dry_run=dry_run)

    log.info(f'[SCAN] Order book boost fired {_orderbook_boost_count} times this session')

    return all_markets, plays, weather_signals, arb_opps

# ─────────────────────────────────────────────────────────────────────────────
# TIER 2 — REAL-TIME MONITOR
# ─────────────────────────────────────────────────────────────────────────────

def check_order_fills(dry_run: bool = False):
    """
    Check if any Donnie resting orders have filled.
    Runs every 30 seconds alongside the realtime monitor.
    Posts a FILL ALERT to Discord when an order matches.
    Also runs stop-loss checks for GDP positions.
    """
    # Run stop-loss check every time order fills are checked (every 30s)
    check_stop_losses(dry_run=dry_run)

    global _known_resting
    try:
        # Fetch current resting orders
        data = kalshi_get("/portfolio/orders", params={"status": "resting", "limit": 50})
        resting = {o["order_id"]: o for o in data.get("orders", [])
                   if str(o.get("client_order_id", "")).startswith("donnie-")}

        # Detect fills: orders that were resting before but aren't now
        prev_ids = set(_known_resting.keys())
        curr_ids = set(resting.keys())
        filled_ids = prev_ids - curr_ids

        for oid in filled_ids:
            o = _known_resting[oid]
            ticker = o.get("ticker", "UNKNOWN")
            direction = o.get("side", "?").upper()
            price_c = o.get("yes_price") or o.get("no_price") or 0
            contracts = o.get("remaining_count", 0)
            cost = round(contracts * price_c / 100, 2)
            log.info(f"[FILL] Order filled: {ticker} BUY {direction} @ {price_c}c x{contracts}")
            if not dry_run:
                msg = (
                    f"✅ DONNIE ORDER FILLED — {ticker}\n"
                    f"BUY {direction} @ {price_c}¢ × {contracts} contracts\n"
                    f"Cost: ${cost:.2f} | Holding to expiration"
                )
                post_discord(msg, channel_id=DISCORD_CH_RESULTS, dry_run=dry_run)

        # Update known resting
        _known_resting = resting
        _save_donnie_state()

    except Exception as e:
        log.debug(f"[FILL] check_order_fills error: {e}")


def run_realtime_monitor(dry_run: bool = False):
    """
    30-second watchlist pulse.
    Checks for price moves, volume spikes, velocity triggers.
    Re-scores and executes if guardrails pass.
    """
    global last_tier2_snapshot

    if not watchlist:
        log.debug("[Tier2] Watchlist empty — skipping realtime monitor")
        return

    # Feature 8: Release window fast-scan mode
    release_active = in_release_window()
    if release_active:
        log.info("[RELEASE WINDOW] Active — running fast scan")
        # Trigger immediate execution check for any ECONOMIC_DATA market on watchlist
        # with confidence >= MEDIUM
        econ_plays = []
        for m in watchlist:
            ticker = m.get("ticker", "")
            title  = m.get("title") or ""
            cat    = m.get("_category", "")
            d      = days_until_close(m)
            mc     = classify_market(ticker, title, cat, d)
            if mc == "ECONOMIC_DATA":
                play = score_market(m, {})
                if play["confidence"] >= CONFIDENCE_MEDIUM_THRESHOLD:
                    econ_plays.append(play)
        if econ_plays:
            log.info(
                f"[RELEASE WINDOW] Triggering immediate execution check for "
                f"{len(econ_plays)} ECONOMIC_DATA markets"
            )
            run_execution_check(econ_plays, dry_run=dry_run)

    log.debug(f"[Tier2] Scanning {len(watchlist)} watchlist markets")
    now = time.time()

    for m in watchlist:
        ticker = m.get("ticker", "")
        if not ticker:
            continue

        # Fetch fresh data (only price fields — lightweight)
        fresh = get_market_detail(ticker)
        if not fresh:
            continue

        fresh["_category"] = m.get("_category", "")

        current_mid = get_mid(fresh)
        current_vol = get_volume_24h(fresh)

        # Update velocity tracking
        update_price_history(ticker, current_mid, current_vol)

        # Compare to last snapshot
        prev = last_tier2_snapshot.get(ticker, {})
        prev_mid = prev.get("mid", current_mid)
        prev_vol = prev.get("volume", current_vol)
        prev_ts  = prev.get("timestamp", now)

        # Update snapshot
        last_tier2_snapshot[ticker] = {
            "mid":       current_mid,
            "volume":    current_vol,
            "timestamp": now,
        }

        # Triggers
        price_move_c = abs((current_mid - prev_mid) * 100)
        vol_spike    = (current_vol >= prev_vol * TIER2_VOLUME_SPIKE_MULT and prev_vol > 0)
        triggered    = False
        trigger_desc = ""

        if price_move_c >= TIER2_PRICE_MOVE_TRIGGER * 100:
            triggered    = True
            trigger_desc = f"Price move {price_move_c:.1f}¢ in {(now-prev_ts):.0f}s"

        vel = compute_velocity(ticker)
        if vel["velocity_label"] == "SPIKE":
            triggered    = True
            trigger_desc = f"Velocity SPIKE {vel['velocity_c_per_min']:.1f}¢/min"

        if vol_spike:
            triggered    = True
            trigger_desc = f"Volume spike ({current_vol/max(prev_vol,1):.1f}x)"

        if not triggered:
            continue

        log.info(f"[Tier2] TRIGGER on {ticker}: {trigger_desc}")

        # Re-score
        play = score_market(fresh, {})

        # Alert to Discord (Tier2 trigger notification)
        alert_msg = format_tier2_alert(ticker, fresh.get("title", ticker), trigger_desc, play)
        post_discord(alert_msg, dry_run=dry_run)

        # Execution check if HIGH confidence
        if play["confidence"] >= CONFIDENCE_HIGH_THRESHOLD:
            try:
                balance    = get_balance()
                positions  = get_open_positions()
                total_exp  = get_total_exposure(positions)
                ok, reason = should_execute(play, balance, positions, total_exp)
                if ok:
                    log.info(f"[Tier2] ✅ Executing triggered market {ticker}")
                    result = execute_trade(play, dry_run=dry_run)
                    post_execution_result(play, result, dry_run=dry_run)
                    # ── Eval Framework: log trade at entry ────────────────────
                    if result.get('status') not in ('failed', 'skipped'):
                        try:
                            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                            from eval_framework import log_trade_entry as _ef_log2
                            _ef_log2(
                                trade_id=play.get('ticker', 'unknown'),
                                agent='donnie',
                                market=play.get('ticker', 'unknown'),
                                direction=play.get('direction', 'YES'),
                                entry_edge_pct=abs(play.get('edge_dollars', 0)) * 100,
                                llm_confidence=str(play.get('confidence', '')),
                                raw_thesis=f"Tier2 trigger: mid={play.get('mid_c', 0)}c",
                                raw_llm_reason='',
                            )
                        except Exception:
                            pass  # eval logging never blocks execution
                else:
                    log.info(f"[Tier2] Guardrail blocked {ticker}: {reason}")
            except Exception as e:
                log.error(f"[Tier2] Exec error on {ticker}: {e}")

        time.sleep(0.2)

# ─────────────────────────────────────────────────────────────────────────────
# WHALE SCAN (legacy 15-min check — kept from v2)
# ─────────────────────────────────────────────────────────────────────────────

def run_whale_scan(markets: list, dry_run: bool = False):
    """Legacy 15-min whale scan. Internal only — posts Discord only for radar tickers."""
    log.info("-" * 60)
    log.info("[Whale] Interim whale check on top volume markets")
    log.info("-" * 60)

    if not markets:
        return

    top   = get_top_volume_markets(markets, n=20)
    found = 0

    for m in top:
        ticker = m.get("ticker", "")
        if not ticker:
            continue

        alerts = analyze_whale_trades(ticker)
        for alert in alerts:
            found += 1
            log.info(
                f"  🐋 [INTERNAL] {ticker} | {alert['direction']} | "
                f"{int(alert['total_contracts']):,} contracts | "
                f"{alert['trade_count']} trades"
            )
            if ticker in last_scan_top_markets:
                title = m.get("title", ticker)
                post_discord(format_whale_update(ticker, title, alert), dry_run=dry_run)

        time.sleep(0.2)

    if found == 0:
        log.info("[Whale] No accumulation detected")

# ─────────────────────────────────────────────────────────────────────────────
# POLYMARKET SMART MONEY TRACKER
# ─────────────────────────────────────────────────────────────────────────────

SMART_MONEY_TOP_N = 50   # top traders to track

def fetch_polymarket_top_traders() -> list:
    """
    Fetch top Polymarket traders by PnL.
    Strategy:
      1. Scrape polymarket.com/leaderboard HTML for proxyWallet addresses (most reliable)
      2. Fallback: gamma-api JSON endpoint (sometimes available)
      3. Fallback: data-api users endpoint
    Returns list of dicts with at least a proxyWallet field.
    """
    # Strategy 1: Scrape leaderboard HTML — proxyWallet addresses are embedded as JSON
    try:
        r = requests.get(
            "https://polymarket.com/leaderboard",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DonnieBot/3.0)"},
        )
        if r.status_code == 200:
            # Extract proxyWallet addresses from the page HTML
            wallets = re.findall(r'"proxyWallet"\s*:\s*"(0x[0-9a-fA-F]{40})"', r.text)
            if wallets:
                # Deduplicate preserving order
                seen = set()
                unique_wallets = []
                for w in wallets:
                    if w.lower() not in seen:
                        seen.add(w.lower())
                        unique_wallets.append(w)
                traders = [{"proxyWallet": w} for w in unique_wallets[:SMART_MONEY_TOP_N]]
                log.info(f"[SmartMoney] Scraped {len(traders)} wallet addresses from leaderboard page")
                return traders
        log.warning(f"[SmartMoney] leaderboard page scrape → {r.status_code}")
    except Exception as e:
        log.warning(f"[SmartMoney] leaderboard page scrape error: {e}")

    # Strategy 2: gamma-api leaderboard JSON (may return 404 — keep as future-proofing)
    try:
        url = f"https://gamma-api.polymarket.com/leaderboard?window=monthly&limit={SMART_MONEY_TOP_N}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                log.info(f"[SmartMoney] gamma leaderboard returned {len(data)} traders")
                return data
            if isinstance(data, dict):
                for key in ("leaderboard", "data", "results", "profiles"):
                    if key in data and isinstance(data[key], list):
                        log.info(f"[SmartMoney] gamma leaderboard[{key}]: {len(data[key])} traders")
                        return data[key]
        log.debug(f"[SmartMoney] gamma leaderboard → {r.status_code} (expected if API deprecated)")
    except Exception as e:
        log.debug(f"[SmartMoney] gamma leaderboard error: {e}")

    # Strategy 3: data-api users
    try:
        url = (
            f"https://data-api.polymarket.com/users"
            f"?limit={SMART_MONEY_TOP_N}&orderBy=profitAndLoss&sortOrder=DESC&window=30d"
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                log.info(f"[SmartMoney] data-api users returned {len(data)} traders")
                return data
            if isinstance(data, dict):
                for key in ("data", "users", "results"):
                    if key in data and isinstance(data[key], list):
                        log.info(f"[SmartMoney] data-api users[{key}]: {len(data[key])} traders")
                        return data[key]
        log.debug(f"[SmartMoney] data-api users → {r.status_code}")
    except Exception as e:
        log.error(f"[SmartMoney] data-api users error: {e}")

    log.warning("[SmartMoney] All leaderboard sources exhausted — no traders fetched")
    return []


def _extract_wallet(trader: dict) -> Optional[str]:
    """Extract wallet address from a Polymarket trader profile dict."""
    for field in ("proxyWallet", "proxy_wallet", "address", "wallet", "user"):
        val = trader.get(field, "")
        if val and isinstance(val, str) and val.startswith("0x"):
            return val.lower()
    return None


def fetch_trader_positions(wallet: str, limit: int = 20) -> list:
    """
    Fetch top positions for a given Polymarket wallet address.
    Returns list of position dicts.
    """
    try:
        url = (
            f"https://data-api.polymarket.com/positions"
            f"?user={wallet}&limit={limit}&sortBy=size&sortOrder=DESC"
        )
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("data", "positions", "results"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
        else:
            log.debug(f"[SmartMoney] positions {wallet[:10]}… → {r.status_code}")
    except Exception as e:
        log.debug(f"[SmartMoney] positions fetch error ({wallet[:10]}…): {e}")
    return []


def run_smart_money_tracker(dry_run: bool = False) -> dict:
    """
    Step 1–3: Fetch top traders, get their positions, cross-ref with Kalshi watchlist.
    Updates global smart_money_signals.
    Returns {ticker: signal_dict}.
    """
    global smart_money_signals, last_smart_money

    log.info("[SmartMoney] Starting smart money tracker scan...")

    traders = fetch_polymarket_top_traders()
    if not traders:
        log.warning("[SmartMoney] No traders returned — smart money signals unavailable this cycle")
        return smart_money_signals

    log.info(f"[SmartMoney] Processing {len(traders)} top traders")

    # Build watchlist title lookup (ticker → title)
    watchlist_titles = {}
    if watchlist:
        for m in watchlist:
            t = m.get("ticker", "")
            title = m.get("title", "")
            if t and title:
                watchlist_titles[t] = title

    new_signals = {}
    wallets_checked = 0

    for trader in traders[:SMART_MONEY_TOP_N]:
        wallet = _extract_wallet(trader)
        if not wallet:
            continue

        positions = fetch_trader_positions(wallet, limit=20)
        wallets_checked += 1

        for pos in positions:
            # Extract position fields — Polymarket uses various field names
            poly_title = (
                pos.get("title") or pos.get("market", {}).get("question", "")
                if isinstance(pos.get("market"), dict)
                else pos.get("title", "")
            )
            side  = (pos.get("side") or pos.get("outcome", "YES")).upper()
            size  = float(pos.get("size") or pos.get("currentValue") or pos.get("value") or 0)
            price = float(pos.get("avgPrice") or pos.get("averagePrice") or pos.get("entryPrice") or 0)

            if not poly_title or size <= 0:
                continue

            # Cross-reference with our Kalshi watchlist
            for k_ticker, k_title in watchlist_titles.items():
                score = fuzzy_match_titles(k_title, poly_title)
                if score >= POLY_MATCH_MIN_WORDS:
                    # Merge: if multiple traders in same market, keep largest position
                    if k_ticker not in new_signals or size > new_signals[k_ticker]["poly_size"]:
                        new_signals[k_ticker] = {
                            "poly_side":   side,
                            "poly_wallet": wallet,
                            "poly_size":   size,
                            "poly_price":  price,
                            "poly_title":  poly_title[:80],
                            "match_score": score,
                        }
                        log.info(
                            f"[SmartMoney] MATCH: {k_ticker} ↔ '{poly_title[:60]}' "
                            f"| wallet={wallet[:12]}… side={side} size={size:.0f} price={price:.2f}"
                        )

        # Pace requests — don't hammer the API
        time.sleep(0.2)
        if wallets_checked % 10 == 0:
            log.info(f"[SmartMoney] Checked {wallets_checked}/{len(traders)} wallets, {len(new_signals)} signals so far")

    smart_money_signals = new_signals
    last_smart_money = time.time()

    log.info(
        f"[SmartMoney] Scan complete: {wallets_checked} wallets checked, "
        f"{len(smart_money_signals)} Kalshi cross-references found"
    )

    if smart_money_signals and dry_run:
        log.info("[SmartMoney] === SMART MONEY SIGNALS ===")
        for ticker, sig in smart_money_signals.items():
            log.info(
                f"  SMART MONEY: {ticker} — top Polymarket trader positioned {sig['poly_side']} "
                f"@ {sig['poly_price']:.2f} (size={sig['poly_size']:.0f}) | '{sig['poly_title']}'"
            )

    return smart_money_signals


# ─────────────────────────────────────────────────────────────────────────────
# REAL-TIME CRYPTO PRICE MONITOR
# ─────────────────────────────────────────────────────────────────────────────

CRYPTO_IDS = {
    "bitcoin":  {"symbol": "BTC", "coingecko_id": "bitcoin"},
    "ethereum": {"symbol": "ETH", "coingecko_id": "ethereum"},
}

def fetch_crypto_prices() -> dict:
    """
    Fetch BTC and ETH spot prices + 24h change from CoinGecko free API.
    No API key required.
    Returns {"bitcoin": {"usd": float, "usd_24h_change": float}, "ethereum": {...}}
    """
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        r = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        if r.status_code == 200:
            data = r.json()
            log.info(
                f"[Crypto] BTC=${data.get('bitcoin', {}).get('usd', 'N/A'):,} "
                f"({data.get('bitcoin', {}).get('usd_24h_change', 0):+.2f}% 24h) | "
                f"ETH=${data.get('ethereum', {}).get('usd', 'N/A'):,} "
                f"({data.get('ethereum', {}).get('usd_24h_change', 0):+.2f}% 24h)"
            )
            return data
        log.warning(f"[Crypto] CoinGecko → {r.status_code}: {r.text[:150]}")
    except Exception as e:
        log.error(f"[Crypto] Price fetch error: {e}")
    return {}


def _spot_to_prob(spot: float, threshold: float) -> float:
    """
    Convert spot price vs threshold into an implied probability for a
    "Will {asset} be above ${threshold}?" YES contract.
    """
    pct_diff = (spot - threshold) / threshold * 100  # percent

    if pct_diff > 2.0:   return 0.92
    if pct_diff > 1.0:   return 0.80
    if pct_diff > 0.0:   return 0.60
    if pct_diff > -1.0:  return 0.40
    if pct_diff > -2.0:  return 0.20
    return 0.08


def _extract_crypto_threshold(title: str) -> Optional[float]:
    """
    Extract a dollar price threshold from a crypto market title.
    e.g. "Will BTC be above $85,000 by end of day?" → 85000.0
    """
    # Match patterns like $85,000 or $85000 or 85000 or 85,000
    patterns = [
        r'\$([0-9]{2,6}(?:,[0-9]{3})*(?:\.[0-9]+)?)',   # $85,000 or $85000
        r'above\s+\$?([0-9]{2,6}(?:,[0-9]{3})*)',       # above $X
        r'over\s+\$?([0-9]{2,6}(?:,[0-9]{3})*)',        # over $X
        r'below\s+\$?([0-9]{2,6}(?:,[0-9]{3})*)',       # below $X
        r'exceed\s+\$?([0-9]{2,6}(?:,[0-9]{3})*)',      # exceed $X
        r'([0-9]{5,6}(?:,[0-9]{3})*)',                  # bare large number like 85000
    ]
    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                # Sanity: BTC ranges 1k-1M, ETH ranges 100-50k
                if 100 <= val <= 1_000_000:
                    return val
            except Exception:
                pass
    return None


def _is_above_crypto_market(title: str) -> bool:
    """Return True if market resolves YES when price is ABOVE threshold."""
    lower = title.lower()
    below_words = ["below", "under", "less than", "no higher"]
    return not any(w in lower for w in below_words)


def _market_resolves_within_hours(m: dict, hours: int = 24) -> bool:
    """Return True if the market resolves within `hours` hours from now."""
    close_time_str = m.get("close_time", "")
    try:
        ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        delta = (ct - datetime.now(timezone.utc)).total_seconds() / 3600
        return 0 < delta <= hours
    except Exception:
        return False


def run_crypto_monitor(dry_run: bool = False) -> dict:
    """
    Module 2: Real-time crypto price monitor.

    1. Fetch BTC/ETH spot prices from CoinGecko.
    2. Flag VOLATILE if estimated 1h move > 3% (approx 24h change / 24).
    3. Find Kalshi crypto markets in watchlist resolving within 24 hours.
    4. Calculate implied prob from spot vs threshold.
    5. If edge > 0.12: add to crypto_signals with HIGH confidence.
    Returns {ticker: signal_dict}.
    """
    global crypto_signals

    log.info("[Crypto] Running crypto price monitor...")

    prices = fetch_crypto_prices()
    if not prices:
        log.warning("[Crypto] No price data — skipping crypto monitor")
        return crypto_signals

    new_crypto_signals = {}

    # Build a lookup of symbol → spot_price + 24h_change
    spot = {}
    for coin_id, meta in CRYPTO_IDS.items():
        coin_data = prices.get(coin_id, {})
        usd_price  = float(coin_data.get("usd", 0))
        change_24h = float(coin_data.get("usd_24h_change", 0))
        if usd_price > 0:
            spot[meta["symbol"]] = {
                "price":      usd_price,
                "change_24h": change_24h,
                "volatile":   abs(change_24h / 24) > 3.0,  # approx 1h > 3%
            }

    for sym, data in spot.items():
        vol_tag = " ⚠️ VOLATILE" if data["volatile"] else ""
        log.info(
            f"[Crypto] {sym} spot=${data['price']:,.0f} "
            f"24h={data['change_24h']:+.2f}%{vol_tag}"
        )

    if not watchlist:
        crypto_signals = new_crypto_signals
        return crypto_signals

    # Scan watchlist for crypto markets resolving within 24h
    for m in watchlist:
        ticker  = m.get("ticker", "")
        title   = m.get("title") or ""
        title_l = title.lower()

        # Match BTC or ETH
        matched_sym = None
        if any(kw in title_l for kw in ("bitcoin", "btc")):
            matched_sym = "BTC"
        elif any(kw in title_l for kw in ("ethereum", "eth")):
            matched_sym = "ETH"

        if not matched_sym or matched_sym not in spot:
            continue

        if not _market_resolves_within_hours(m, hours=24):
            continue

        threshold = _extract_crypto_threshold(title)
        if threshold is None:
            log.debug(f"[Crypto] {ticker}: could not extract price threshold from '{title[:60]}'")
            continue

        spot_price  = spot[matched_sym]["price"]
        above       = _is_above_crypto_market(title)
        kalshi_mid  = get_mid(m)

        # Implied prob from spot (for YES = above threshold)
        if above:
            spot_prob = _spot_to_prob(spot_price, threshold)
        else:
            # "will price be BELOW X?" — invert
            spot_prob = 1.0 - _spot_to_prob(spot_price, threshold)

        edge = abs(spot_prob - kalshi_mid)

        pct_vs_threshold = (spot_price - threshold) / threshold * 100

        log.info(
            f"[Crypto] {ticker} | {matched_sym} spot=${spot_price:,.0f} "
            f"threshold=${threshold:,.0f} ({pct_vs_threshold:+.2f}%) | "
            f"above={above} spot_prob={spot_prob:.2f} kalshi_mid={kalshi_mid:.2f} edge={edge:.2f}"
        )

        if edge > EXEC_MIN_EDGE_DOLLARS:
            side = "YES" if spot_prob > kalshi_mid else "NO"
            new_crypto_signals[ticker] = {
                "ticker":      ticker,
                "symbol":      matched_sym,
                "spot_price":  spot_price,
                "threshold":   threshold,
                "above":       above,
                "side":        side,
                "spot_prob":   round(spot_prob, 3),
                "kalshi_mid":  round(kalshi_mid, 3),
                "edge":        round(edge, 3),
                "volatile":    spot[matched_sym]["volatile"],
                "change_24h":  spot[matched_sym]["change_24h"],
                "confidence_boost": 0.25 if edge > 0.20 else 0.15,
            }
            log.info(
                f"[Crypto] SIGNAL: {ticker} — {matched_sym} spot ${spot_price:,.0f} "
                f"vs threshold ${threshold:,.0f} | edge={edge:.2f} → BUY {side}"
            )

    crypto_signals = new_crypto_signals

    if crypto_signals and dry_run:
        log.info("[Crypto] === CRYPTO SIGNALS ===")
        for ticker, sig in crypto_signals.items():
            log.info(
                f"  CRYPTO: {ticker} | {sig['symbol']} ${sig['spot_price']:,.0f} "
                f"vs threshold ${sig['threshold']:,.0f} | "
                f"spot_prob={sig['spot_prob']:.0%} kalshi={sig['kalshi_mid']:.0%} "
                f"edge={sig['edge']:.2f} → BUY {sig['side']}"
            )
    else:
        log.info(f"[Crypto] No crypto signals above {EXEC_MIN_EDGE_DOLLARS:.2f} edge this cycle")

    return crypto_signals


# ─────────────────────────────────────────────────────────────────────────────
# STALE ORDER CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def cancel_stale_orders(dry_run: bool = False):
    """Cancel Donnie limit orders that have been resting for more than ORDER_MAX_AGE_HOURS."""
    try:
        data = kalshi_get("/portfolio/orders", params={"status": "resting", "limit": 50})
        orders = [o for o in data.get("orders", [])
                  if str(o.get("client_order_id", "")).startswith("donnie-")]

        now = datetime.now(timezone.utc)
        cutoff_hours = ORDER_MAX_AGE_HOURS

        for o in orders:
            order_id = o.get("order_id", "")
            created_str = o.get("created_time", "")
            if not created_str:
                continue
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > cutoff_hours:
                    ticker = o.get("ticker", "")
                    if not dry_run:
                        path = f"/trade-api/v2/portfolio/orders/{order_id}"
                        requests.delete(KALSHI_BASE + f"/portfolio/orders/{order_id}",
                                        headers=get_auth_headers("DELETE", path), timeout=10)
                    log.info(f"[STALE] Cancelled order {order_id[:8]} on {ticker} (age={age_hours:.1f}h)")
            except Exception as e:
                log.debug(f"[STALE] Error checking order {order_id}: {e}")
    except Exception as e:
        log.debug(f"[STALE] cancel_stale_orders error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STINK BID STRATEGY — for Brad (importable, NOT used by Donnie)
# ─────────────────────────────────────────────────────────────────────────────

def run_stink_bid_strategy(markets: list, balance: float, dry_run: bool = False) -> list:
    """
    BRAD'S STINK BID STRATEGY — importable function, NOT called by Donnie.

    For each sports market: place a limit BUY at 30% discount from current mid.
    Cancel and re-place every 15 minutes if unfilled.
    If filled: hold to expiration.
    Max allocation: 2% of balance per market.

    Args:
        markets: list of market dicts (with ticker, title, yes_ask_dollars, etc.)
        balance: current cash balance in dollars
        dry_run: if True, log only — don't place orders

    Returns:
        list of order result dicts
    """
    results         = []
    max_per_market  = balance * 0.02   # 2% max per stink bid

    for m in markets:
        ticker = m.get("ticker", "")
        title  = (m.get("title") or "")[:60]
        mid    = get_mid(m)

        if not ticker or mid <= 0:
            continue

        # Stink price: 30% discount from current mid
        stink_price_dollars = mid * 0.70
        stink_price_c       = max(1, min(99, int(round(stink_price_dollars * 100))))

        # Max contracts at stink price within 2% allocation
        if stink_price_dollars > 0:
            max_contracts = math.floor(max_per_market / stink_price_dollars)
        else:
            max_contracts = 0

        if max_contracts <= 0:
            log.info(f"[StinkBid] {ticker}: 0 contracts at 2% allocation — skipping")
            continue

        cost = max_contracts * stink_price_dollars
        client_id = f"brad-stink-{uuid.uuid4()}"

        order_body = {
            "ticker":          ticker,
            "client_order_id": client_id,
            "type":            "limit",
            "action":          "buy",
            "side":            "yes",
            "count":           max_contracts,
            "yes_price":       stink_price_c,
        }

        log.info(
            f"[StinkBid] {'[DRY RUN] ' if dry_run else ''}{ticker} | "
            f"mid={int(mid*100)}¢ stink={stink_price_c}¢ x{max_contracts} "
            f"(cost=${cost:.2f} | {title})"
        )

        if dry_run:
            results.append({
                "ticker":          ticker,
                "status":          "dry_run",
                "stink_price_c":   stink_price_c,
                "contracts":       max_contracts,
                "cost":            cost,
                "client_order_id": client_id,
            })
            continue

        resp  = kalshi_post("/portfolio/orders", order_body)
        order = resp.get("order", {})

        results.append({
            "ticker":          ticker,
            "status":          order.get("status", "failed"),
            "order_id":        order.get("order_id", client_id),
            "stink_price_c":   stink_price_c,
            "contracts":       max_contracts,
            "cost":            cost,
        })
        time.sleep(0.3)

    return results

# ─────────────────────────────────────────────────────────────────────────────
# GDP RESEARCH — Q1 2026 ADVANCE ESTIMATE (April 30, 2026)
# ─────────────────────────────────────────────────────────────────────────────

GDP_THESIS = {
    "release_date":   "2026-04-30",
    "consensus_range": (-2.4, 1.0),  # GDPNow to market consensus
    "our_estimate":   -1.5,          # tariff drag, soft Q1
    "positions": {
        "KXGDP-26APR30-T2.0": "NO — Q1 GDP almost certainly below 2.0% given tariff drag",
        "KXGDP-26APR30-T2.5": "NO — Even more certain below 2.5%",
        "KXGDP-26APR30-T3.0": "NO — Near certainty below 3.0%",
    },
    "verdict": "HOLD all three NO positions — strong consensus for weak Q1 GDP",
    "risk": "If Q1 data excludes tariff impact or is revised strongly upward",
}


def fetch_gdpnow() -> Optional[float]:
    """
    Fetch latest Atlanta Fed GDPNow estimate. Returns annualized % or None.
    Primary: FRED API CSV for GDPNOW series.
    Fallback: Atlanta Fed page scrape.
    """
    # Primary: FRED API CSV
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDPNOW",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            lines = r.text.strip().split('\n')
            # Last line has latest value: DATE,VALUE
            last = lines[-1].split(',')
            if len(last) == 2:
                val = float(last[1])
                log.info(f"[EconModel] GDPNow: {val:.2f}%")
                return val
    except Exception as e:
        log.debug(f"[EconModel] GDPNow FRED fetch failed: {e}")

    # Fallback: Atlanta Fed page scrape
    try:
        r = requests.get("https://www.atlantafed.org/cqer/research/gdpnow", timeout=10)
        match = re.search(r'GDPNow.*?(-?\d+\.\d+)%', r.text)
        if match:
            val = float(match.group(1))
            log.info(f"[EconModel] GDPNow (scrape fallback): {val:.2f}%")
            return val
    except Exception:
        pass
    return None


def fetch_fedwatch_hold_prob() -> float:
    """Fetch CME FedWatch implied probability of Fed hold at next meeting."""
    try:
        r = requests.get(
            "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/305/G",
            timeout=10, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json()
            quotes = data.get("quotes", [])
            if quotes:
                # 30-day fed funds futures: implied rate = 100 - price
                latest = quotes[0]
                price = float(latest.get("last", 0))
                implied_rate = 100 - price
                # Current rate 3.375-3.5% range — if implied < 3.25, cut is priced in
                hold_prob = 1.0 if implied_rate >= 3.25 else 0.5
                log.info(f"[EconModel] FedWatch implied rate: {implied_rate:.2f}% → hold_prob={hold_prob:.2f}")
                return hold_prob
    except Exception as e:
        log.debug(f"[EconModel] FedWatch fetch failed: {e}")
    return 0.95  # fallback: assume ~95% hold


def fetch_cleveland_inflation() -> Optional[float]:
    """Fetch Cleveland Fed inflation nowcast for CPI estimate."""
    try:
        r = requests.get(
            "https://www.clevelandfed.org/en/our-research/indicators-and-data/inflation-nowcasting.aspx",
            timeout=10
        )
        # Look for current CPI estimate in page
        match = re.search(r'(\d+\.\d+)\s*%?\s*(?:CPI|inflation)', r.text, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            log.info(f"[EconModel] Cleveland CPI nowcast: {val:.2f}%")
            return val
    except Exception as e:
        log.debug(f"[EconModel] Cleveland inflation fetch failed: {e}")
    return None


def calculate_economic_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    For a given economic market ticker, fetch the relevant model estimate
    and calculate edge vs Kalshi price.

    Returns: (model_prob: float, edge: float, direction: str, source: str)
    """
    ticker_upper = ticker.upper()

    # GDP markets: KXGDP-26APR30-T{threshold}
    if 'KXGDP' in ticker_upper and 'APR30' in ticker_upper:
        gdpnow = fetch_gdpnow()
        if gdpnow is None:
            return None, 0.0, "NO", "unavailable"

        # Extract threshold from ticker
        match = re.search(r'T(-?\d+\.?\d*)', ticker_upper)
        if not match:
            return None, 0.0, "NO", "parse_error"
        threshold = float(match.group(1))

        # If GDPNow < threshold: NO is likely to win
        if gdpnow < threshold:
            # How confident? Bigger gap = more confident
            gap = threshold - gdpnow
            model_prob_no = min(0.95, 0.5 + gap * 0.15)  # 0.5 at gap=0, up to 0.95
            edge = model_prob_no - (1.0 - kalshi_mid)     # edge on NO = our_prob - kalshi_no_prob
            return model_prob_no, edge, "NO", f"GDPNow={gdpnow:.1f}%<{threshold}%"
        else:
            gap = gdpnow - threshold
            model_prob_yes = min(0.95, 0.5 + gap * 0.15)
            edge = model_prob_yes - kalshi_mid
            return model_prob_yes, edge, "YES", f"GDPNow={gdpnow:.1f}%>{threshold}%"

    # FOMC markets
    if 'KXFEDMEET' in ticker_upper or 'KXFEDDECISION' in ticker_upper:
        hold_prob = fetch_fedwatch_hold_prob()
        if 'HOLD' in ticker_upper or 'UNCHANGED' in ticker_upper:
            edge = hold_prob - kalshi_mid
            return hold_prob, edge, "YES" if edge > 0 else "NO", "CME_FedWatch"

    return None, 0.0, "NO", "no_model"


def run_gdp_research(open_positions: dict = None) -> dict:
    """
    Build and log the GDP thesis for the Q1 2026 advance estimate (April 30).
    Call once at startup if any GDP market is in open positions.
    Returns the GDP_THESIS dict (with live GDPNow estimate if fetchable).
    """
    thesis = dict(GDP_THESIS)

    # Try to get live GDPNow estimate
    gdpnow = fetch_gdpnow()
    if gdpnow is not None:
        thesis["gdpnow_live"] = gdpnow
        log.info(f"[GDP] Atlanta Fed GDPNow live estimate: {gdpnow:+.1f}%")
    else:
        thesis["gdpnow_live"] = None
        log.info("[GDP] GDPNow estimate unavailable — using cached consensus")

    log.info("=" * 60)
    log.info("GDP THESIS — Q1 2026 Advance Estimate")
    log.info("=" * 60)
    log.info(f"  Release date:     {thesis['release_date']}")
    log.info(f"  Consensus range:  {thesis['consensus_range'][0]:+.1f}% to {thesis['consensus_range'][1]:+.1f}%")
    log.info(f"  Our estimate:     {thesis['our_estimate']:+.1f}%")
    if gdpnow is not None:
        log.info(f"  GDPNow (live):    {gdpnow:+.1f}%")
    log.info(f"  Verdict:          {thesis['verdict']}")
    log.info(f"  Risk:             {thesis['risk']}")
    log.info("  Positions:")
    for ticker, rationale in thesis["positions"].items():
        log.info(f"    {ticker}: {rationale}")
    log.info("=" * 60)

    return thesis


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Donnie v3 — Two-tier Kalshi scanner")
    parser.add_argument("--dry-run",   action="store_true", help="Stdout only, no Discord posts")
    parser.add_argument("--scan-once", action="store_true", help="Single discovery scan, then exit")
    args = parser.parse_args()

    dry_run   = args.dry_run
    scan_once = args.scan_once

    modes = []
    if dry_run:   modes.append("DRY-RUN")
    if scan_once: modes.append("SCAN-ONCE")
    log.info(f"Donnie v3 starting [{', '.join(modes) if modes else 'CONTINUOUS'}]")
    log.info(f"Scipy available: {SCIPY_AVAILABLE}")

    if scan_once:
        # GDP research — always run at startup; checks for GDP markets in open positions
        run_gdp_research()

        # Stop-loss check — always run at startup
        log.info("[STOPLOSS] Running startup stop-loss check...")
        check_stop_losses(dry_run=dry_run)

        # Log weather model update window status
        in_window = in_weather_model_update_window()
        log.info(f"[Weather] Model update window active: {in_window}")

        # Run smart money + crypto monitors first so signals are ready for scoring
        run_smart_money_tracker(dry_run=dry_run)
        run_crypto_monitor(dry_run=dry_run)

        # One full discovery scan
        all_markets, plays, weather_signals, arb_opps = run_discovery_scan(dry_run=dry_run)

        if dry_run:
            print("\n" + "=" * 60)
            print("SCAN-ONCE SUMMARY")
            print("=" * 60)
            print(f"Total markets scanned: {len(all_markets)}")
            print(f"Weather signals found: {len(weather_signals)}")
            if weather_signals:
                for ws in weather_signals:
                    print(
                        f"  [{ws['city']}] forecast={ws['forecast_temp']}°F "
                        f"threshold={ws['threshold']:.0f}°F "
                        f"model={ws['model_prob']:.0%} kalshi={ws['kalshi_prob']:.0%} "
                        f"edge={ws['edge']:.0%} ticker={ws['ticker']}"
                    )
            print(f"Polymarket arb opps: {len(arb_opps)}")
            if arb_opps:
                for arb in arb_opps[:3]:
                    print(
                        f"  [{arb['ticker']}] kalshi={int(arb['kalshi_mid']*100)}¢ "
                        f"poly={int(arb['poly_mid']*100)}¢ spread={int(arb['arb_spread']*100)}¢"
                    )
            print(f"Velocity tracker initialized: {len(price_history)} tickers")

            # Top 5 plays from new edge-quality scoring
            print(f"\nTop 5 plays (edge-quality scored):")
            for i, p in enumerate(plays[:5], 1):
                mc  = p.get("market_class", "?")
                tm  = p.get("tier_multiplier", 1.0)
                cs  = p.get("composite_score", 0.0)
                mid_c = p.get("mid_c", 0)
                print(f"  {i}. [{mc} x{tm:.1f}] {p['ticker']} @ {mid_c}¢ "
                      f"composite={cs:.4f} conf={p['confidence']:.2f} ({p['conf_label']}) "
                      f"— {p['title'][:55]}")

            # Category breakdown
            from collections import Counter
            class_counts = Counter(p.get("market_class", "?") for p in plays)
            print(f"\nCategory breakdown: {dict(class_counts)}")
            print("=" * 60)

        log.info("Scan-once complete. Exiting.")
        return

    # ── GDP research at startup ───────────────────────────────────────────────
    run_gdp_research()

    # ── Continuous two-tier loop ──────────────────────────────────────────────
    last_discovery   = 0.0
    last_realtime    = 0.0
    last_weather     = 0.0
    last_polymarket  = 0.0
    last_smart_money_ts = 0.0
    last_crypto_ts   = 0.0
    last_heartbeat   = 0.0
    HEARTBEAT_INTERVAL_SEC = 86400  # daily heartbeat at 9am UTC
    cached_markets   = []

    while True:
        now = time.time()

        # Feature 8: During release windows, check more frequently
        interval_multiplier = 0.5 if in_release_window() else 1.0

        if now - last_discovery >= DISCOVERY_SCAN_INTERVAL_SEC * interval_multiplier:
            cached_markets, _, _, _ = run_discovery_scan(dry_run=dry_run)
            cancel_stale_orders(dry_run=dry_run)
            last_discovery = time.time()

        realtime_interval = 10 if in_release_window() else REALTIME_MONITOR_INTERVAL_SEC
        if now - last_realtime >= realtime_interval:
            check_order_fills(dry_run=dry_run)
            run_realtime_monitor(dry_run=dry_run)
            last_realtime = time.time()

        # Weather is Mark Hanna/weather.py's domain — Donnie does not scan or execute weather

        if POLYMARKET_ENABLED and now - last_polymarket >= POLYMARKET_REFRESH_INTERVAL_SEC * interval_multiplier:
            run_polymarket_sync(watchlist, dry_run=dry_run)
            last_polymarket = time.time()

        if POLYMARKET_ENABLED and now - last_smart_money_ts >= SMART_MONEY_INTERVAL_SEC * interval_multiplier:
            run_smart_money_tracker(dry_run=dry_run)
            last_smart_money_ts = time.time()

        if now - last_crypto_ts >= CRYPTO_MONITOR_INTERVAL_SEC * interval_multiplier:
            run_crypto_monitor(dry_run=dry_run)
            last_crypto_ts = time.time()

        # Daily heartbeat — posts once per day so you know Donnie is alive
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
            try:
                bal = get_balance()
                positions = get_open_positions()
                heartbeat_msg = (
                    f"🤖 **DONNIE DAILY CHECK-IN**\n"
                    f"Status: Online ✅ | Balance: ${bal:.2f}\n"
                    f"Open positions: {len(positions)} | Watchlist: {len(watchlist)} markets\n"
                    f"Scanning every 30min | Realtime pulse every 30sec"
                )
                if not dry_run:
                    post_discord(heartbeat_msg)
                log.info("Daily heartbeat posted")
            except Exception as e:
                log.warning(f"Heartbeat failed: {e}")
            last_heartbeat = time.time()

        time.sleep(5)


def run_scan(post=None, **kwargs):
    """Entry point for firm.py orchestrator. Runs a single discovery scan."""
    run_discovery_scan(dry_run=False)
    _save_donnie_state()
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from shared_context import write_agent_status
        write_agent_status('donnie', {'status': 'ran', 'markets_scanned': 0})
    except Exception:
        pass


if __name__ == "__main__":
    main()
