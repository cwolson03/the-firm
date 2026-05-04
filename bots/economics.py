#!/usr/bin/env python3
"""
economics.py — Kalshi trading engine (Economics)

Two-tier scanner: discovery (30min) + realtime monitor (30sec).
Scores markets with edge calculators (GDP, CPI, crypto, commodity),
whale tracking, velocity detection, and order book analysis.
Executes trades through a 5-gate guardrail system.

API notes:
  - prices in dollars (0.0-1.0), volume is volume_fp
  - categories on events, not markets
  - GET /markets/trades?ticker=X&limit=50

Usage:
    python3 economics.py                   # continuous
    python3 economics.py --scan-once       # single scan + exit
    python3 economics.py --dry-run         # no Discord, stdout only
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
    from scipy.stats import norm as _scipy_norm
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# CONFIG

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID      = "2e462103-bdd5-4a1b-b231-17191bded0bb"

# Paths — auto-detect Atlas (cody) vs local (stratton)
_HOME = os.path.expanduser("~")
if os.path.exists("/home/cody/stratton"):
    PRIVATE_KEY_PATH = os.environ.get("KALSHI_KEY_PATH", "")
    BOT_TOKENS_ENV   = "/home/cody/stratton/config/bot-tokens.env"
    LOG_PATH         = "/home/cody/stratton/logs/economics.log"
else:
    PRIVATE_KEY_PATH = os.environ.get("KALSHI_KEY_PATH", "")
    BOT_TOKENS_ENV   = "/home/stratton/.openclaw/workspace/config/bot-tokens.env"
    LOG_PATH         = "/home/stratton/.openclaw/workspace/logs/economics.log"

DISCORD_CH_KALSHI  = 1491861941361180924   # #kalshi-signals
DISCORD_CH_RESULTS = 1491861943894671450   # #kalshi-results

# --- Scanner thresholds ---
MIN_VOLUME         = 200
MAX_SPREAD_DOLLARS = 0.15
TOP_PER_CAT        = 10

# --- Whale detection ---
WHALE_MIN_CONTRACTS    = 50
WHALE_MIN_NOTIONAL     = 200
WHALE_WINDOW_HOURS     = 1
WHALE_ALERT_THRESHOLD  = 3
WHALE_FETCH_TOP_N      = 60

# --- Confidence thresholds ---
CONFIDENCE_HIGH_THRESHOLD   = 0.65
CONFIDENCE_MEDIUM_THRESHOLD = 0.35

# --- Report config ---
TOP_PLAYS_DISPLAY = 5
WATCHLIST_SIZE    = 50   # Tier 1 → Tier 2 watchlist

# --- Execution Guardrails (NON-NEGOTIABLE — never bypass) ---
EXEC_MIN_EDGE_DOLLARS   = 0.18
EXEC_MAX_PER_POSITION   = 0.35
EXEC_MAX_TOTAL_DEPLOYED = 0.70
EXEC_MAX_TOTAL_DOLLARS    = 150.0  # Hard stop: never exceed $150 total exposure regardless of balance
EXEC_MAX_PER_UNDERLYING   = 30.0   # Max $ per single underlying (WTI, BTC, Gold separately)
EXEC_POSITION_SIZE_PCT  = 0.05
MAX_LONGSHOT_DOLLARS    = 8.0    # Max spend on any market priced < 15c (longshot cap)

# --- Daily loss kill switch ---
DAILY_LOSS_LIMIT_DOLLARS = 25.0
DAILY_LOSS_PATH          = "/home/cody/stratton/data/donnie_daily_loss.json"

# --- Skip reason logging ---
SKIP_LOG_PATH            = "/home/cody/stratton/data/skip_log.jsonl"
SKIP_LOG_MAX_BYTES       = 10 * 1024 * 1024  # 10 MB

# --- Correlation cap ---
MAX_POSITIONS_PER_CLASS  = {"COMMODITY": 2, "CRYPTO_SHORT": 2, "ECONOMIC_DATA": 5}

# --- Stale order management ---
ORDER_MAX_AGE_HOURS = 2  # auto-cancel stale limit orders older than 2 hours

# --- Crypto/Commodity near-expiry cutoff ---
CRYPTO_MIN_MINUTES_TO_CLOSE = 30   # never trade crypto/commodity range markets within 30 min of close
CRYPTO_MIN_BUFFER_PCT        = 0.005  # spot must be >0.5% away from threshold (same-day)

# Horizon-scaled buffer minimums — longer horizon needs more buffer because BTC is volatile
# Daily vol ~1.9%, so N days requires sqrt(N) * 1.9% buffer to maintain same confidence level
# We use 2x safety margin: buffer_min = 2 * daily_vol * sqrt(days_to_close)
CRYPTO_BUFFER_DAILY_VOL      = 0.019  # 1.9% daily vol baseline (matches realized vol model)
CRYPTO_MAX_HORIZON_DAYS      = 2      # never take BTC/ETH positions more than 2 days out
# Buffer requirements by horizon:
# same-day (0-1 day): 0.5% minimum
# 1-2 days: 2.0% minimum  
# >2 days: BLOCKED entirely

# --- Scheduling ---
DISCOVERY_SCAN_INTERVAL_SEC   = 1800   # 30 min — full market scan
REALTIME_MONITOR_INTERVAL_SEC = 30     # 30 sec — watchlist price pulse
CRYPTO_MONITOR_INTERVAL_SEC     = 300   # 5 min  — crypto price monitor

# data release windows (UTC) — scan faster during these
RELEASE_WINDOWS = [
    (8, 25, 8, 45),    # 8:30 ET = 12:30 UTC — NFP, CPI, PPI, retail sales
    (13, 55, 14, 15),  # 2:00 ET = 18:00 UTC — FOMC decisions
    (9, 55, 10, 15),   # 10:00 ET = 14:00 UTC — ISM, housing
]

# --- Tier 2 real-time trigger thresholds ---
TIER2_PRICE_MOVE_TRIGGER = 0.05        # 5¢ price move triggers re-score
TIER2_VOLUME_SPIKE_MULT  = 2.0         # 2x volume spike triggers re-score

# --- Velocity tracker thresholds (cents per minute) ---
VEL_MOVING    = 2.0    # log only
VEL_FAST_MOVE = 5.0    # +0.15 confidence boost
VEL_SPIKE     = 10.0   # +0.30 confidence boost + immediate exec check
VEL_VOL_SPIKE = 2.0    # 2x volume acceleration boost multiplier → +0.20

# --- Polymarket arb thresholds ---

# --- Market taker mode ---
MARKET_TAKER_THRESHOLD   = 0.80  # use market order (ask price) above this confidence
MARKET_TAKER_CATEGORIES  = set()  # WEATHER moved to weather.py; ECONOMIC_DATA re-enable when verified

# --- Thesis direction lock — Economics engine CANNOT trade opposite to these ---
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
    # Merge dynamic locks built from live model each cycle
    active.update(_dynamic_thesis_locks)
    return active


# Runtime dynamic thesis locks — rebuilt each scan cycle
_dynamic_thesis_locks: dict = {}

def build_thesis_locks() -> dict:
    """
    Dynamically build thesis direction locks from live GDPNow model.
    Fetches open KXGDP markets and locks direction based on GDPNow vs threshold.
    Called at start of each run_scan() cycle.
    Returns {ticker: direction} dict.
    """
    locks = {}
    gdpnow = fetch_gdpnow_realtime()
    if gdpnow is None:
        return locks
    try:
        data = kalshi_get("/markets", {"series_ticker": "KXGDP", "status": "open", "limit": 50})
        for m in data.get("markets", []):
            ticker = m.get("ticker", "")
            match = re.search(r"T(-?\d+\.?\d*)", ticker.upper())
            if not match:
                continue
            threshold = float(match.group(1))
            direction = "NO" if gdpnow < threshold else "YES"
            locks[ticker] = direction
            log.info("[THESIS] Auto-lock: %s -> %s (GDPNow=%.2f%% vs thr=%.2f%%)",
                     ticker, direction, gdpnow, threshold)
    except Exception as e:
        log.warning("[THESIS] GDP lock build failed: %s", e)
    return locks

# Stop-loss config for open positions
STOP_LOSS_RULES = {
    "KXGDP-26APR30-T2.0": {"direction": "NO", "stop_if_no_below": 0.55},   # stop if NO price drops below 55¢
    "KXGDP-26APR30-T2.5": {"direction": "NO", "stop_if_no_below": 0.60},
    "KXGDP-26APR30-T3.0": {"direction": "NO", "stop_if_no_below": 0.75},
}

# --- Market category tiers — determines scoring multiplier ---
# Tier 1: Economics has quantifiable data edge
# Tier 2: Some signal available
# Tier 3: No edge — exclude or require strong whale signal
CATEGORY_TIERS = {
    "ECONOMIC_DATA":  3.0,   # CPI, NFP, FOMC, GDP, PCE, PPI — model vs market
    "WEATHER":        2.5,   # Open-Meteo model vs Kalshi price
    "COMMODITY":      2.5,   # Gold, Oil, Silver, S&P with real-time data
    "CRYPTO_SHORT":   2.0,   # BTC/ETH price range within 24 hours (KXBTCD/KXETHD)
    "CRYPTO_15M":     1.5,   # 15-minute BTC/ETH momentum markets (KXBTC15M/KXETH15M)
    "POLITICAL_NEWS": 1.0,   # Breaking news / whale signal required
    "WORLD_EVENTS":   1.0,   # International events
    "POLITICAL_LONG": 0.2,   # 2028 elections, long-dated political
    "JUNK":           0.0,   # Exclude: "will X say Y", "will X leave office"
}

# --- Economic calendar — update periodically ---
# Format: (date_str, event_name, series_ticker_prefix)
ECONOMIC_CALENDAR = [
    ('2026-04-29', 'GDP Q1 Advance', 'KXGDP'),
    ('2026-04-30', 'PCE March',      'KXPCE'),
    ('2026-04-30', 'FOMC Decision',  'KXFEDMEET'),
    ('2026-05-02', 'NFP April',      'KXPAYROLLS'),  # ticker prefix corrected from KXNFP
    ('2026-05-13', 'CPI April',      'KXCPI'),
    # Daily crypto markets refresh continuously — always check
    ('daily', 'BTC Daily Price', 'KXBTCD'),
    ('daily', 'ETH Daily Price', 'KXETHD'),
]

# --- Weather signal thresholds ---

# IN-MEMORY STATE

# watchlist from last discovery scan
watchlist: list = []

# tickers that scored HIGH/MEDIUM last scan
last_scan_top_markets: list = []

# last known prices/volumes per ticker

last_tier2_snapshot: dict = {}

# velocity tracker
price_history: dict = defaultdict(list)
volume_history: dict = defaultdict(list)

# crypto monitor signals
crypto_signals: dict = {}

# BTC/ETH spot prices from previous scan — used for momentum trigger
_prev_btc_spot: float = 0.0
_prev_eth_spot: float = 0.0
BTC_MOMENTUM_THRESHOLD = 0.005  # 0.5% move triggers BTC edge scan

# BTC/ETH price history for momentum detection
_btc_price_history: list = []  # last 12 readings (1h at 5-min intervals)
_btc_scan_triggered: bool = False  # flag set by watcher, cleared after scan
_btc_last_trigger_reason: str = ""

MAX_BTC_POSITIONS_PER_CLOSE = 2  # max positions per single close time

# resting order tracker — alerts on fills
_known_resting: dict = {}

_orderbook_boost_count = 0

# --- State persistence (survives module reloads by firm.py) ---
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

# LOGGING

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

log = logging.getLogger("economics")
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

# AUTH — RSA-PSS (unchanged from v2)

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
    """RSA-PSS signed auth headers."""
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

# KALSHI API HELPERS

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
    """Paginate /events — ~27 pages @ 200/page."""
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
    """Fetch single market by ticker."""
    data = kalshi_get(f"/markets/{ticker}")
    return data.get("market", {})

def get_trades_for_ticker(ticker: str, limit: int = 50) -> list:
    data = kalshi_get("/markets/trades", params={"ticker": ticker, "limit": limit})
    return data.get("trades", [])

# MARKET ANALYSIS HELPERS

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
                    # preserve order book fields for analyze_order_book
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
        ticker = m.get("ticker", "")
        title  = m.get("title") or ""
        # Use classify_market() to get proper category — API category field is unreliable
        d = days_until_close(m)
        api_cat = m.get("category") or m.get("_category") or "UNCATEGORIZED"
        proper_cat = classify_market(ticker, title, api_cat, d)
        # Store computed class back on market dict for downstream use
        m["_category"] = proper_cat
        m["market_class"] = proper_cat
        if api_cat in BRAD_CATEGORIES or proper_cat in BRAD_CATEGORIES:
            continue
        grouped[proper_cat].append(m)
    return dict(grouped)

def top_markets_per_category(grouped: dict) -> dict:
    # Priority categories get higher limits so they aren't crowded out
    PRIORITY_CAT_LIMIT = {
        "ECONOMIC_DATA": 20,   # Always evaluate all economic data markets
        "CRYPTO_SHORT":  15,   # BTC/ETH daily price markets must be included
        "CRYPTO_15M":   20,   # 15-minute BTC/ETH momentum markets — include all
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

# WHALE TRACKER (unchanged from v2)

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
    """Whale boost: same direction +10/+25/+40, opposing -15."""
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

# WEATHER SIGNAL LAYER

# GDPNOW PROBABILITY CALCULATOR

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

# DAILY CRYPTO/COMMODITY MARKET SEED

DAILY_PRICE_SERIES = {
    'KXBTCD':  {'asset': 'BTC',  'category': 'CRYPTO_SHORT'},
    'KXETHD':  {'asset': 'ETH',  'category': 'CRYPTO_SHORT'},
    'KXGOLDD': {'asset': 'GOLD', 'category': 'COMMODITY'},
    'KXEURUSD': {'asset': 'EURUSD', 'category': 'ECONOMIC_DATA'},
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

# CLEVELAND FED CPI NOWCAST

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

def fetch_cpi_component_estimate() -> dict:
    """
    Component-based CPI MoM estimate using major BLS sub-indices from FRED.
    
    Components and approximate BLS weights:
      - CUSR0000SAH1  (Shelter)             35%
      - CPIENGSL      (Energy)               7%
      - CPIFABSL      (Food at home)        14%
      - CUSR0000SACL1E (Core services ex-shelter) 30%
      - CPIAUCSL      (All items, validation) —

    For each component: fetch 6 months, compute MoM changes, apply weight, sum.
    
    Returns dict with mom_estimate, std_dev, components breakdown, source.
    """
    import statistics

    COMPONENTS = [
        ("CUSR0000SAH1",   "shelter",       0.35),
        ("CPIENGSL",       "energy",        0.07),
        ("CPIFABSL",       "food",          0.14),
        ("CUSR0000SACL1E", "core_services", 0.30),
    ]
    # Core goods gets residual weight (0.14) — approximated via CPIAUCSL validation
    CORE_GOODS_WEIGHT = 0.14

    component_moms = {}
    weighted_contributions = []

    for series_id, label, weight in COMPONENTS:
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": series_id,
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 8,
            }, timeout=10)
            if not r.ok:
                raise ValueError(f"HTTP {r.status_code}")
            obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", [])
                   if o.get("value") not in (".", None)]
            if len(obs) < 2:
                raise ValueError("insufficient data")
            # MoM % changes (most recent first)
            moms = [(obs[i][1] - obs[i+1][1]) / obs[i+1][1] * 100 for i in range(len(obs)-1)]
            # 3m average as component estimate
            est = sum(moms[:3]) / min(3, len(moms))
            component_moms[label] = round(est, 4)
            weighted_contributions.append(est * weight)
            log.debug(f"[CPI-Component] {label} ({series_id}): 3m avg MoM = {est:.3f}%")
        except Exception as e:
            log.warning(f"[CPI-Component] {label} ({series_id}) failed: {e}")
            raise  # bubble up so caller falls back

    # Core goods estimated as residual (not directly available, use ~0 for now)
    # We do NOT add it to weighted_contributions to avoid double-counting;
    # the four components above cover ~86% of the index.
    total_weight_covered = sum(w for _, _, w in COMPONENTS)
    mom_estimate = sum(weighted_contributions)

    # Std dev from shelter (most stable) and energy (most volatile) spread
    values = list(component_moms.values())
    std_dev = statistics.stdev(values) if len(values) >= 2 else 0.20

    result = {
        "mom_estimate": round(mom_estimate, 4),
        "std_dev":      round(std_dev, 4),
        "components":   component_moms,
        "weight_coverage": round(total_weight_covered, 2),
        "source":       "FRED/CPI_components",
    }
    log.info(
        "[CPI-Component] estimate=%.3f%% std=%.3f%% "
        "shelter=%.3f%% energy=%.3f%% food=%.3f%% core_svc=%.3f%%",
        mom_estimate, std_dev,
        component_moms.get("shelter", 0),
        component_moms.get("energy", 0),
        component_moms.get("food", 0),
        component_moms.get("core_services", 0),
    )
    return result


def fetch_cpi_mom_estimate() -> dict:
    """
    Calculate expected CPI month-over-month change.
    
    PRIMARY: Component-based model using major BLS sub-indices (IMPROVEMENT 1).
    FALLBACK: Simple CPIAUCSL 3m/6m blended trend.
    
    The Kalshi CPI markets ask about MoM % change (e.g. "will CPI rise > 0.5%"),
    NOT the raw index level. This function computes the model estimate correctly.
    
    Returns dict: mom_estimate (%), std_dev (%), source, history
    """
    import statistics

    # --- PRIMARY: component-based model ---
    try:
        result = fetch_cpi_component_estimate()
        if result and result.get("mom_estimate") is not None:
            return result
    except Exception as e:
        log.warning("[CPI] Component model failed (%s), falling back to CPIAUCSL trend", e)

    # --- FALLBACK: simple CPIAUCSL trend ---
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": "CPIAUCSL",
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 8,
        }, timeout=10)
        if not r.ok:
            return {}
        obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", [])
               if o.get("value") not in (".", None)]
        if len(obs) < 4:
            return {}
        
        # MoM % changes
        moms = [(obs[i][1] - obs[i+1][1]) / obs[i+1][1] * 100 for i in range(len(obs)-1)]
        
        trend_3m = sum(moms[:3]) / 3
        trend_6m = sum(moms[:6]) / 6 if len(moms) >= 6 else trend_3m
        
        # Blended estimate: 60% recent, 40% longer trend
        estimate = trend_3m * 0.60 + trend_6m * 0.40
        
        std_dev = statistics.stdev(moms[:6]) if len(moms) >= 3 else 0.20
        
        result = {
            "mom_estimate": round(estimate, 3),
            "std_dev":      round(std_dev, 3),
            "trend_3m":     round(trend_3m, 3),
            "trend_6m":     round(trend_6m, 3),
            "last_mom":     round(moms[0], 3),
            "last_date":    obs[0][0],
            "source":       "FRED/CPIAUCSL_MoM(fallback)",
        }
        log.info("[CPI] Fallback MoM estimate: %.3f%% (3m=%.3f%% 6m=%.3f%% last=%.3f%%)",
                 estimate, trend_3m, trend_6m, moms[0])
        return result
    except Exception as e:
        log.warning("[CPI] fetch_cpi_mom_estimate failed: %s", e)
        return {}

# Cache CPI MoM estimate — 4 hour TTL (FRED data is monthly, no need to refresh often)
_cpi_mom_cache: dict = {}
_cpi_mom_cache_ts: float = 0.0

def get_cpi_mom_estimate() -> dict:
    global _cpi_mom_cache, _cpi_mom_cache_ts
    now = time.time()
    if _cpi_mom_cache and (now - _cpi_mom_cache_ts) < 14400:
        return _cpi_mom_cache
    result = fetch_cpi_mom_estimate()
    if result:
        _cpi_mom_cache = result
        _cpi_mom_cache_ts = now
    return _cpi_mom_cache

def calculate_cpi_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    Edge calculation for CPI/PCE markets.
    
    FIX (2026-05-02): Now uses actual MoM % change estimate, not the raw index level.
    The Kalshi threshold (e.g. 0.5) is a MoM % change, not an index level.
    Previous model was comparing index level (330) vs threshold (0.5) — wrong units.
    
    Model: CPIAUCSL 3m/6m blended MoM trend with normal distribution.
    """
    ticker_upper = ticker.upper()

    # Parse threshold from ticker (e.g. KXCPI-26APR-T0.5 -> 0.5% MoM threshold)
    match = re.search(r'T(-?\d+\.?\d*)', ticker_upper.split('-')[-1])
    if not match:
        return None, 0.0, "NO", "parse_error"
    threshold = float(match.group(1))

    # Get MoM estimate
    cpi = get_cpi_mom_estimate()
    if not cpi:
        return None, 0.0, "NO", "unavailable"

    estimate = cpi["mom_estimate"]   # e.g. 0.35% expected MoM
    std_dev  = cpi["std_dev"]        # e.g. 0.25% historical std dev

    # P(actual MoM > threshold) using normal distribution
    if not SCIPY_AVAILABLE:
        # Fallback: simple comparison
        if estimate > threshold:
            gap = estimate - threshold
            model_prob = min(0.85, 0.55 + gap * 2.0)
        else:
            gap = threshold - estimate
            model_prob = max(0.15, 0.45 - gap * 2.0)
    else:
        from scipy.stats import norm
        z = (threshold - estimate) / max(std_dev, 0.05)
        model_prob = float(1.0 - norm.cdf(z))

    edge = model_prob - kalshi_mid
    direction = "YES" if edge > 0 else "NO"

    source = "CPIAUCSL_MoM(est=%.3f%%,thr=%.3f%%)" % (estimate, threshold)
    log.info("[CPI] %s: MoM_est=%.3f%% thr=%.3f%% sigma=%.3f -> P(>thr)=%.0f%% kalshi=%.0f%% edge=%+.2f -> %s",
             ticker, estimate, threshold, std_dev, model_prob*100, kalshi_mid*100, edge, direction)
    return model_prob, edge, direction, source


# ─────────────────────────────────────────────────────────────────────────────
# NFP MODEL — Nonfarm Payrolls edge calculator
# ─────────────────────────────────────────────────────────────────────────────

def fetch_adp_estimate() -> dict:
    """
    Fetch ADP National Employment Report.  IMPROVEMENT 2: multi-source fallback.
    
    Sources tried in order:
      1. FRED ADPWNFP  — private nonfarm payrolls (usually populated)
      2. FRED ADPNFP   — alternative series ID
      3. FRED ADPWNFM  — original series (often empty)
      4. ADP website   — scrape headline number
      5. Return unavailable if all fail
    
    Returns dict: value (thousands), date, available (bool), source (str)
    """
    # Series: (id, label, is_level) — is_level=True means absolute employment, compute MoM diff
    FRED_SERIES = [
        ("ADPMNUSNERSA",  "FRED/ADPMNUSNERSA",  True),   # Monthly SA total employment (level)
        ("ADPWNUSNERSA",  "FRED/ADPWNUSNERSA",  True),   # Weekly SA total employment (level)
        ("ADPMNUSNERNSA", "FRED/ADPMNUSNERNSA", True),   # Monthly NSA total employment (level)
        ("ADPWNFM",       "FRED/ADPWNFM",       False),  # Legacy monthly change series
    ]

    # 1-4: Try FRED series in order
    for series_id, source_label, is_level in FRED_SERIES:
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": series_id,
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 4,   # need 2+ for level series diff
            }, timeout=10)
            if r.ok:
                obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", [])
                       if o.get("value") not in (".", None)]
                if not obs:
                    log.debug("[NFP] %s returned no observations", series_id)
                    continue
                if is_level:
                    # Series returns absolute employment level (e.g. 132M).
                    # Compute MoM change in thousands for NFP blending.
                    if len(obs) < 2:
                        log.debug("[NFP] %s: need 2 obs for diff, only have %d", series_id, len(obs))
                        continue
                    mom_change_k = (obs[0][1] - obs[1][1]) / 1000.0  # convert to thousands
                    # Sanity: ADP monthly change should be -1000K to +1000K
                    if not (-1000 <= mom_change_k <= 1000):
                        log.debug("[NFP] %s: MoM change %.1fK out of sanity range, skipping", series_id, mom_change_k)
                        continue
                    log.info("[NFP] ADP from %s: %.1fK MoM change (level %s→%s, as of %s)",
                             source_label, mom_change_k, obs[1][0], obs[0][0], obs[0][0])
                    return {"value": mom_change_k, "date": obs[0][0], "available": True, "source": source_label}
                else:
                    # Series returns change directly
                    log.info("[NFP] ADP from %s: %.0fK (as of %s)", source_label, obs[0][1], obs[0][0])
                    return {"value": obs[0][1], "date": obs[0][0], "available": True, "source": source_label}
        except Exception as e:
            log.debug("[NFP] %s fetch failed: %s", series_id, e)

    # 4: Scrape ADP employment report website
    try:
        r = requests.get(
            "https://adpemploymentreport.com/",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; EconBot/1.0)"},
        )
        if r.ok:
            text = r.text
            # Look for patterns like "147,000" or "147K" near "jobs" or "employment"
            # Common headline formats: "added 147,000 jobs" or "147K jobs"
            patterns = [
                r'added\s+([\d,]+)\s+jobs',
                r'([\d,]+)\s+jobs\s+(?:added|created|gained)',
                r'headline[^\d]+([\d,]+)',
                r'total\s+nonfarm[^\d]+([\d,]+)',
                r'private[^\d]+([\d,]+)\s+(?:jobs|workers)',
                r'(\d{2,3}),(\d{3})',  # matches like "147,000"
            ]
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    # Extract and clean the number
                    raw = m.group(1).replace(",", "") if m.lastindex >= 1 else None
                    if raw and raw.isdigit():
                        val = float(raw)
                        # Sanity check: ADP readings are usually 50K-500K
                        if 10000 <= val <= 1000000:
                            val = val / 1000  # convert to thousands
                        if 10 <= val <= 1000:
                            log.info("[NFP] ADP from adpemploymentreport.com: %.0fK", val)
                            return {"value": val, "date": None, "available": True, "source": "adpemploymentreport.com"}
            log.debug("[NFP] ADP website parsed but no headline number found")
    except Exception as e:
        log.debug("[NFP] ADP website scrape failed: %s", e)

    log.warning("[NFP] All ADP sources failed — will use PAYEMS-only model")
    return {"value": None, "date": None, "available": False, "source": "none"}

def fetch_nfp_estimate() -> dict:
    """
    Build an NFP model estimate using FRED PAYEMS trend data.

    Model:
      - 3-month average of MoM PAYEMS changes → near-term trend
      - 6-month average → longer-term baseline
      - Blend: 70% 3-month, 30% 6-month
      - Uncertainty band: ±75K (NFP has historically high revision noise)

    Returns dict with keys: estimate, std_dev, trend_3m, trend_6m, last_reading, source
    """
    try:
        import requests as _req
        r = _req.get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": "PAYEMS",
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 8,
        }, timeout=10)
        if not r.ok:
            return {}
        obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", [])
               if o.get("value") != "."]
        if len(obs) < 4:
            return {}

        # MoM changes (thousands)
        changes = [obs[i][1] - obs[i+1][1] for i in range(len(obs)-1)]

        trend_3m = sum(changes[:3]) / 3
        trend_6m = sum(changes[:6]) / 6 if len(changes) >= 6 else trend_3m

        # Blended estimate: 70% recent, 30% longer-term
        estimate = round(trend_3m * 0.70 + trend_6m * 0.30, 0)

        # Blend with ADP if available
        adp = fetch_adp_estimate()
        adp_blended = False
        std_dev = 75000
        if adp["available"] and adp["value"] is not None:
            adp_k = adp["value"]
            estimate = round(estimate * 0.60 + adp_k * 0.40, 0)
            std_dev  = 60000
            adp_blended = True
            log.info("[NFP] ADP blend: base=%.0fK ADP=%.0fK -> blended=%.0fK sigma=60K",
                     trend_3m * 0.70 + trend_6m * 0.30, adp_k, estimate)
        result = {
            "estimate":     estimate,
            "std_dev":      std_dev,
            "trend_3m":     round(trend_3m, 0),
            "trend_6m":     round(trend_6m, 0),
            "last_reading": changes[0],
            "last_date":    obs[0][0],
            "adp_value":    adp.get("value"),
            "adp_blended":  adp_blended,
            "source":       "FRED/PAYEMS+ADP" if adp_blended else "FRED/PAYEMS",
        }
        log.info("[NFP] Estimate: %.0fK (3m=%.0fK 6m=%.0fK adp=%s src=%s)",
            estimate, trend_3m, trend_6m,
            "%.0fK" % adp["value"] if adp["available"] else "N/A", result["source"])
        return result
    except Exception as e:
        log.warning("[NFP] fetch_nfp_estimate failed: %s", e)
        return {}

# Cache NFP estimate — refresh every 4 hours
_nfp_cache: dict = {}
_nfp_cache_ts: float = 0.0

def get_nfp_estimate() -> dict:
    """Cached wrapper around fetch_nfp_estimate."""
    global _nfp_cache, _nfp_cache_ts
    now = time.time()
    if _nfp_cache and (now - _nfp_cache_ts) < 14400:  # 4-hour cache
        return _nfp_cache
    result = fetch_nfp_estimate()
    if result:
        _nfp_cache = result
        _nfp_cache_ts = now
    return _nfp_cache

def calculate_nfp_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    Edge calculation for KXPAYROLLS markets.

    Approach:
      - Model estimate from PAYEMS 3/6-month blended trend
      - Normal distribution with std_dev=75K to compute P(actual > threshold)
      - Compare to Kalshi mid price
      - Direction: YES if model_prob > kalshi_mid, NO if model_prob < kalshi_mid

    Guardrails enforced here (in addition to should_execute):
      - Do not execute if estimate is within 1 std_dev of threshold (edge too thin)
      - Only return valid edge if |model_prob - kalshi_mid| >= EXEC_MIN_EDGE_DOLLARS

    Returns: (model_prob, edge, direction, source)
    """
    try:
        from scipy.stats import norm as _norm_check
    except ImportError:
        log.warning("[NFP] scipy unavailable -- cannot calculate NFP edge")
        return None, 0.0, "NO", "scipy_unavailable"

    ticker_upper = ticker.upper()

    # Extract threshold from ticker e.g. KXPAYROLLS-26APR-T175000 → 175000
    # Handles negative thresholds: T-100000 → -100000
    match = re.search(r"T(-?\d+)", ticker_upper)
    if not match:
        return None, 0.0, "NO", "parse_error"
    threshold = float(match.group(1))

    nfp = get_nfp_estimate()
    if not nfp:
        return None, 0.0, "NO", "unavailable"

    # UNITS: PAYEMS MoM changes come from FRED in thousands (52.0 = 52K jobs).
    # Ticker threshold is full number (T150000 = 150,000 jobs).
    # Normalize everything to full job counts for z-score.
    estimate_full  = nfp["estimate"] * 1000   # 52.0 -> 52,000
    std_dev_full   = nfp["std_dev"]           # 75,000 (already full units)
    threshold_full = threshold                # from ticker regex: already full number

    # P(actual > threshold) using normal distribution
    from scipy.stats import norm
    z = (threshold_full - estimate_full) / std_dev_full
    model_prob = float(1.0 - norm.cdf(z))  # P(X > threshold)

    edge = model_prob - kalshi_mid
    direction = "YES" if edge > 0 else "NO"

    # Thin-edge guard: if estimate is within 1 std_dev of threshold, flag it
    if abs(estimate_full - threshold_full) < std_dev_full:
        log.info(
            "[NFP] %s: estimate=%.0fK threshold=%.0fK -- within 1-sigma (75K), edge unreliable -- min_edge filter will gate",
            ticker, estimate_full/1000, threshold_full/1000
        )

    log.info(
        "[NFP] %s: model_est=%.0fK thr=%.0fK sigma=75K -> P(>thr)=%.0f%% kalshi=%.0f%% edge=%+.2f -> %s",
        ticker, estimate_full/1000, threshold_full/1000, model_prob*100, kalshi_mid*100, edge, direction
    )

    source = "PAYEMS_trend(%.0fK+/-75K)" % (estimate_full/1000)
    return model_prob, edge, direction, source

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

# CRYPTO & COMMODITY PRICE EDGE CALCULATORS

def get_crypto_spot() -> dict:
    """
    Get current BTC and ETH spot prices.  IMPROVEMENT 3: Coinbase primary, CoinGecko fallback.
    
    Sources:
      1. Coinbase public API (no auth, reliable, no rate limits for spot)
      2. CoinGecko free tier (fallback)
    
    Returns {"BTC": float, "ETH": float}
    """
    # 1. Try Coinbase first
    try:
        btc_r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=8)
        eth_r = requests.get("https://api.coinbase.com/v2/prices/ETH-USD/spot", timeout=8)
        if btc_r.status_code == 200 and eth_r.status_code == 200:
            btc_price = float(btc_r.json()["data"]["amount"])
            eth_price = float(eth_r.json()["data"]["amount"])
            log.info(f"[Crypto] Coinbase: BTC=${btc_price:,.0f} ETH=${eth_price:,.0f}")
            return {"BTC": btc_price, "ETH": eth_price, "_source": "Coinbase"}
    except Exception as e:
        log.debug(f"[Crypto] Coinbase failed: {e}")

    # 2. Fallback to CoinGecko
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            btc_price = data.get("bitcoin", {}).get("usd", 0)
            eth_price = data.get("ethereum", {}).get("usd", 0)
            log.info(f"[Crypto] CoinGecko (fallback): BTC=${btc_price:,.0f} ETH=${eth_price:,.0f}")
            return {"BTC": btc_price, "ETH": eth_price, "_source": "CoinGecko"}
    except Exception as e:
        log.debug(f"[Crypto] CoinGecko failed: {e}")

    log.warning("[Crypto] All price sources failed")
    return {}


# Cache for BTC realized volatility (1-hour TTL)
_btc_vol_cache: dict = {"vol": None, "ts": 0.0}

def fetch_btc_realized_vol() -> float:
    """
    Fetch BTC 30-day realized annualized volatility from CoinGecko.
    Calculates: stdev(ln(p_t/p_{t-1})) * sqrt(365) * 100
    Returns annualized vol as percentage (e.g. 45.0 for 45%).
    Cached for 1 hour. Falls back to 55.0% if fetch fails.
    """
    global _btc_vol_cache
    now = time.time()
    if _btc_vol_cache["vol"] is not None and (now - _btc_vol_cache["ts"]) < 3600:
        return _btc_vol_cache["vol"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": 30, "interval": "daily"},
            timeout=15
        )
        if r.status_code == 200:
            prices_raw = r.json().get("prices", [])
            if len(prices_raw) >= 10:
                prices = [p[1] for p in prices_raw]
                log_returns = [math.log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
                n = len(log_returns)
                mean = sum(log_returns) / n
                variance = sum((x - mean) ** 2 for x in log_returns) / (n - 1)
                daily_std = math.sqrt(variance)
                annualized_vol = daily_std * math.sqrt(365) * 100
                # Sanity clamp: BTC vol rarely goes below 20% or above 200%
                annualized_vol = max(20.0, min(200.0, annualized_vol))
                _btc_vol_cache = {"vol": annualized_vol, "ts": now}
                log.info(f"[Vol] BTC 30d realized vol: {annualized_vol:.1f}% annualized ({daily_std*100:.2f}% daily)")
                return annualized_vol
    except Exception as e:
        log.warning(f"[Vol] CoinGecko vol fetch failed: {e}")
    # Fallback: conservative estimate for BTC
    fallback = 55.0
    _btc_vol_cache = {"vol": fallback, "ts": now}
    log.warning(f"[Vol] Using fallback vol: {fallback}% annualized")
    return fallback


def calculate_crypto15m_edge(ticker: str, market_dict: dict) -> tuple:
    """
    Multi-signal edge model for KXBTC15M / KXETH15M 15-minute momentum markets.
    Requires 2-of-3 signal consensus before returning any edge.

    Signals:
      1. Price momentum proxy (Kalshi mid directional bias)
      2. Kalshi order book imbalance
      3. Spread tightness (liquidity / volatility proxy)

    Returns (model_prob, edge, direction, source)
    """
    ticker_upper = ticker.upper()
    yes_bid  = float(market_dict.get("yes_bid", 0) or 0)
    yes_ask  = float(market_dict.get("yes_ask", 100) or 100)
    # Kalshi prices may be in cents (0-100) — normalize to 0-1
    if yes_bid > 1.0:
        yes_bid = yes_bid / 100.0
    if yes_ask > 1.0:
        yes_ask = yes_ask / 100.0

    mid = (yes_bid + yes_ask) / 2.0

    # ── Signal 1: Price momentum proxy ──────────────────────────────────────
    # If mid > 0.50 → market implies bullish momentum; if < 0.50 → bearish
    sig1 = None
    if mid > 0.52:
        sig1 = "YES"
    elif mid < 0.48:
        sig1 = "NO"
    # else neutral

    # ── Signal 2: Order book imbalance ──────────────────────────────────────
    sig2 = None
    yes_bid_size = float(market_dict.get("yes_bid_size_fp") or market_dict.get("yes_bid_size") or 0)
    no_bid_size  = float(market_dict.get("no_bid_size_fp")  or market_dict.get("no_bid_size")  or 0)
    if yes_bid_size > 0 and no_bid_size > 0:
        ratio = yes_bid_size / no_bid_size
        if ratio > 1.20:
            sig2 = "YES"
        elif ratio < (1.0 / 1.20):
            sig2 = "NO"
    # else neutral (sizes unavailable or within 20%)

    # ── Signal 3: Spread tightness ──────────────────────────────────────────
    spread = yes_ask - yes_bid
    sig3_valid = spread < 0.08  # signal is valid if spread not too wide
    # Spread < 0.04 = tight book (active market) → counts as a confirming signal
    # Spread 0.04-0.08 = moderate — valid but doesn't add signal direction
    # Spread > 0.08 = unreliable, marks as no-signal

    # -- Signal 4: Fear & Greed Index -------------------------------------------
    fg = fetch_crypto_fear_greed()
    fg_value = fg.get("value", 50)
    sig4 = None
    if fg_value > 55:
        sig4 = "YES"   # greed supports upward continuation
    elif fg_value < 45:
        sig4 = "NO"    # fear supports downward continuation

    # -- Aggregate (4-signal model, sig3 is validity check not directional) -----
    yes_signals = sum(1 for s in [sig1, sig2, sig4] if s == "YES")
    no_signals  = sum(1 for s in [sig1, sig2, sig4] if s == "NO")

    # Tight spread as confirming bonus
    if sig3_valid and spread < 0.04:
        if yes_signals > no_signals:
            yes_signals += 1
        elif no_signals > yes_signals:
            no_signals += 1

    # Variable model_prob based on signal strength
    total_aligned = max(yes_signals, no_signals)
    if total_aligned >= 4:
        prob_yes, prob_no, size_note = 0.66, 0.34, "4-signal HIGH"
    elif total_aligned >= 3:
        prob_yes, prob_no, size_note = 0.64, 0.36, "3-signal MED+"
    else:
        prob_yes, prob_no, size_note = 0.62, 0.38, "2-signal BASE"

    if yes_signals >= 2 and mid < 0.60:
        model_prob = prob_yes
        edge = model_prob - mid
        log.info("[Crypto15M] %s YES consensus (%s) sig1=%s sig2=%s sig4=%s fg=%d yes=%d no=%d edge=%+.3f",
                 ticker, size_note, sig1, sig2, sig4, fg_value, yes_signals, no_signals, edge)
        return model_prob, edge, "YES", ("15M_4sig(yes=%d,fg=%d,%s)" % (yes_signals, fg_value, size_note))

    if no_signals >= 2 and mid > 0.40:
        model_prob = prob_no
        edge = model_prob - mid
        log.info("[Crypto15M] %s NO consensus (%s) sig1=%s sig2=%s sig4=%s fg=%d yes=%d no=%d edge=%+.3f",
                 ticker, size_note, sig1, sig2, sig4, fg_value, yes_signals, no_signals, edge)
        return model_prob, edge, "NO", ("15M_4sig(no=%d,fg=%d,%s)" % (no_signals, fg_value, size_note))

    log.info("[Crypto15M] %s no consensus fg=%d sig1=%s sig2=%s sig4=%s yes=%d no=%d",
             ticker, fg_value, sig1, sig2, sig4, yes_signals, no_signals)
    return None, 0.0, "NO", "insufficient_signal_consensus"

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

        # Horizon-scaled buffer: longer horizon = more buffer required
        _close_str = ""
        try:
            _md = kalshi_get("/markets/%s" % ticker)
            _close_str = _md.get("market", {}).get("close_time", "")
        except Exception:
            pass
        _buf_ok, _buf_reason, _ = crypto_horizon_buffer_check(ticker, spot, threshold, _close_str)
        if not _buf_ok:
            log.info("[Crypto] %s: %s", ticker, _buf_reason)
            return None, 0.0, "NO", "buffer_too_thin"

        pct_above = (spot - threshold) / threshold

        # ── Dynamic volatility: use realized 30d vol instead of fixed 2.5% ──
        annualized_vol = fetch_btc_realized_vol()  # e.g. 55%
        daily_vol = annualized_vol / math.sqrt(365) / 100  # convert % → decimal daily

        # Scale edge thresholds by current vol
        # At 2% buffer with 55% ann vol (~2.9% daily): very comfortable
        # At 2% buffer with 80% ann vol (~4.2% daily): less certain
        vol_ratio = daily_vol / 0.025  # normalize against baseline 2.5%
        adj_factor = max(0.5, min(1.5, 1.0 / vol_ratio))  # higher vol → lower confidence

        if pct_above > 0.02:    # spot >2% above threshold
            model_prob = min(0.97, 0.95 * adj_factor + 0.05)
        elif pct_above > 0.005:
            model_prob = min(0.90, 0.85 * adj_factor + 0.03)
        elif pct_above > 0:
            model_prob = 0.65
        elif pct_above > -0.005:
            model_prob = 0.40
        else:
            model_prob = max(0.05, 0.15 / adj_factor)

        edge = model_prob - kalshi_mid
        direction = "YES" if edge > 0 else "NO"
        log.info(
            f"[CryptoEdge] {ticker} | {asset} spot=${spot:,.0f} thr=${threshold:,.0f} "
            f"({pct_above:+.1%}) pct_above={pct_above:.3%} | ann_vol={annualized_vol:.1f}% "
            f"daily_vol={daily_vol*100:.2f}% | model={model_prob:.2f} kalshi={kalshi_mid:.2f} "
            f"edge={edge:+.3f} → {direction}"
        )
        return model_prob, edge, direction, (
            f"spot=${spot:,.0f} thr=${threshold:,.0f} {pct_above:+.1%} "
            f"vol={annualized_vol:.0f}%ann pct={pct_above:.2%}"
        )

    return None, 0.0, "NO", "parse_error"

def fetch_gas_price() -> float:
    """Fetch latest US gas price from FRED (GASREGCOVW series)."""
    return fetch_fred_series("GASREGCOVW")

# Cache commodity prices — single Stooq fetch per scan cycle (5 min TTL)
_commodity_price_cache: dict = {}
_commodity_price_ts: float = 0.0

def get_commodity_prices() -> dict:
    """
    Get commodity spot prices from Stooq (free, no auth required).
    Cached for 5 minutes so bulk scoring doesn't hammer Stooq.
    Falls back to Yahoo Finance scrape if Stooq fails.
    """
    global _commodity_price_cache, _commodity_price_ts
    now = time.time()
    if _commodity_price_cache and (now - _commodity_price_ts) < 300:
        return _commodity_price_cache
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
                                # Silver futures (SI.F) quote in cents/oz on Stooq — convert to $/oz
                                if name == "SILVER" and close > 1000:
                                    close = close / 100.0
                                prices[name] = close
        except Exception as e:
            log.debug(f"[Commodity] Stooq {sym} failed: {e}")
        time.sleep(0.1)

    if prices:
        log.info("[Commodity] Stooq prices: " + " | ".join(f"{k}={v:,.1f}" for k, v in prices.items()))
        _commodity_price_cache = prices
        _commodity_price_ts = time.time()
    else:
        log.warning("[Commodity] No prices from Stooq — all commodity edge calcs will skip")
    return prices


# =============================================================================
# WTI OIL THESIS MODEL — EIA inventory-driven direction signal
# =============================================================================

def fetch_eia_inventory() -> dict:
    """
    Fetch EIA weekly crude oil commercial stocks (Ending Stocks Excluding SPR).
    Returns dict: current (MBBL), previous (MBBL), change (MBBL), signal, date
    
    Inventory draw (negative change) = BULLISH for oil (less supply)
    Inventory build (positive change) = BEARISH for oil (more supply)
    """
    try:
        r = requests.get("https://api.eia.gov/v2/petroleum/stoc/wstk/data/", params={
            "api_key": "DEMO_KEY",
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": "EPC0",
            "facets[duoarea][]": "NUS",
            # No process filter — filter in code by process-name
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 8,
        }, timeout=10)
        if not r.ok:
            return {}
        data = r.json().get("response", {}).get("data", [])
        # Filter to Ending Stocks Excluding SPR specifically
        # Filter to commercial crude (Ending Stocks Excluding SPR)
        stocks = [d for d in data if "Excluding SPR" in d.get("process-name", "")]
        if len(stocks) < 2:
            return {}
        curr = float(stocks[0].get("value", 0))
        prev = float(stocks[1].get("value", 0))
        chg = curr - prev
        # Normalize: >3M barrel build = bearish, >3M draw = bullish
        if chg < -3000:
            signal = "BULLISH"  # large draw
        elif chg > 3000:
            signal = "BEARISH"  # large build
        elif chg < 0:
            signal = "SLIGHTLY_BULLISH"
        else:
            signal = "SLIGHTLY_BEARISH"
        result = {
            "current_mbbl": curr,
            "previous_mbbl": prev,
            "change_mbbl": round(chg, 0),
            "signal": signal,
            "date": stocks[0].get("period", ""),
        }
        log.info("[EIA] Crude inventory: %+.0f MBBL (%s) as of %s",
                 chg, signal, result["date"])
        return result
    except Exception as e:
        log.warning("[EIA] fetch failed: %s", e)
        return {}

# Cache EIA data — update weekly (releases every Wednesday)
_eia_cache: dict = {}
_eia_cache_ts: float = 0.0

def get_eia_inventory() -> dict:
    global _eia_cache, _eia_cache_ts
    now = time.time()
    if _eia_cache and (now - _eia_cache_ts) < 86400:  # 24h cache
        return _eia_cache
    result = fetch_eia_inventory()
    if result:
        _eia_cache = result
        _eia_cache_ts = now
    return _eia_cache

def get_oil_thesis_direction() -> str:
    """
    Returns "BULLISH", "BEARISH", or "NEUTRAL" for WTI based on:
    1. EIA inventory signal (primary)
    2. Recent price momentum (secondary)
    
    Used to filter WTI Kalshi positions — only trade in direction of thesis.
    """
    eia = get_eia_inventory()
    signal = eia.get("signal", "")
    
    if signal in ("BULLISH", "SLIGHTLY_BULLISH"):
        return "BULLISH"   # Look for YES positions (oil stays above threshold)
    elif signal in ("BEARISH", "SLIGHTLY_BEARISH"):
        return "BEARISH"   # Look for NO positions (oil won't hit high threshold)
    return "NEUTRAL"

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

# NEWS RSS SCANNER

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

# POLYMARKET SIGNAL LAYER

# ODDS VELOCITY TRACKER

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

# MARKET CLASSIFIER — edge-quality tier assignment

def classify_market(ticker: str, title: str, category: str, days_until_close_val: int) -> str:
    """Classify market into scoring tier based on edge quality."""
    title_lower  = title.lower()
    ticker_upper = ticker.upper()

    # ── CPI/PCE markets → ECONOMIC_DATA (before other checks) ───────────────
    if ticker_upper.startswith('KXCPI') or ticker_upper.startswith('KXPCE'):
        return 'ECONOMIC_DATA'

    # ── CRYPTO_15M — 15-minute BTC/ETH momentum markets (check BEFORE CRYPTO_SHORT) ──
    if ('KXBTC15M' in ticker_upper or 'KXETH15M' in ticker_upper):
        return 'CRYPTO_15M'

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
    # Ticker-based ECONOMIC_DATA classification (highest priority)
    econ_tickers_cls = ['KXGDP', 'KXCPI', 'KXPCE', 'KXPPI', 'KXNFP', 'KXPAYROLLS', 'KXEURUSD',
                        'KXFEDDECISION', 'KXFEDMEET', 'KXFEDDOT', 'KXDOTPLOT',
                        'KXFOMC', 'KXUNRATE', 'KXU3', 'KXRECESSION', 'KXAAAGASW',
                        'KXSHELTERCPI', 'KXCORECPI']
    if any(ticker_upper.startswith(p) for p in econ_tickers_cls):
        return 'ECONOMIC_DATA'

    econ_patterns = [
        'cpi', 'inflation', 'consumer price', 'nfp', 'payroll',
        'unemployment', 'fomc', 'federal reserve', 'fed rate',
        'interest rate', 'gdp', 'gross domestic', 'pce',
        'personal consumption', 'ppi', 'producer price',
        'jobs report', 'non-farm', 'trade deficit', 'retail sales',
        'housing starts', 'durable goods', 'ism manufacturing',
        'dot plot', 'basis points', 'rate cut', 'rate hike',
    ]
    if any(p in title_lower for p in econ_patterns):
        return 'ECONOMIC_DATA'

    # ── Weekly gas price markets (EIA via FRED) ───────────────────────────────
    if ticker_upper.startswith('KXAAAGASW') and days_until_close_val <= 3:
        return 'ECONOMIC_DATA'

    # ── WEATHER ───────────────────────────────────────────────────────────────
    if category in ('Climate and Weather',) or 'temperature' in title_lower or 'weather' in title_lower:
        return 'WEATHER'

    # ── CRYPTO_SHORT — only same-day/next-day BTC/ETH (max 2-day horizon) ──
    crypto_patterns = ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto']
    if any(p in title_lower for p in crypto_patterns):
        if days_until_close_val <= CRYPTO_MAX_HORIZON_DAYS:
            return 'CRYPTO_SHORT'
        else:
            return 'JUNK'  # crypto >2 days = too uncertain

    # ── COMMODITY — gold, silver, oil, S&P futures (ticker-based, date-limited) ──
    commodity_tickers_cls = [
        'KXGOLDD', 'KXGOLDW', 'KXGOLDMON', 'KXGOLD',
        'KXSILVERD', 'KXSILVER',
        'KXWTI', 'KXBRENT', 'KXOIL',
        'KXSPYD', 'KXSPY',
        'KXSPXD', 'KXSPX',
        'KXNDXD', 'KXNDX',
    ]
    if any(ticker_upper.startswith(p.upper()) for p in commodity_tickers_cls) and days_until_close_val <= 7:
        return 'COMMODITY'

    # Commodity by title keywords
    commodity_title_patterns = ['gold close price', 'silver close price', 'wti', 'crude oil',
                                 'oil price', 'gold price', 'silver price']
    if any(p in title_lower for p in commodity_title_patterns) and days_until_close_val <= 7:
        return 'COMMODITY'

    # ── POLITICAL_LONG — 2028 elections, long nominations ────────────────────
    if days_until_close_val > 365 or '2028' in title or '2027' in title_lower:
        return 'POLITICAL_LONG'
    if 'presidential' in title_lower or 'nomination' in title_lower or 'nominee' in title_lower:
        return 'POLITICAL_LONG'

    # ── Default ───────────────────────────────────────────────────────────────
    return 'POLITICAL_NEWS'

# FEATURE 7: ORDER BOOK DEPTH ANALYSIS

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

# FEATURE 8: TIME-OF-DAY DATA RELEASE ALERTS

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

# MARKET SCORING — liquidity + whale + velocity + weather signals

def score_market(m: dict, weather_signals: dict = None) -> dict:
    """
    Score a single market. Returns a play dict with all scoring components.
    weather_signals: {ticker: signal_dict} for pre-computed weather edges.
    """
    ticker = m.get("ticker", "")
    mid    = get_mid(m)
    liq    = liquidity_score(m)

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

    # Commodity edge boost — bypasses volume-based base_conf for data-driven plays
    # Low-volume commodity markets (gold/oil/silver) have real model edge but thin book depth
    commodity_boost = 0.0
    commodity_note  = ""
    market_class_sc = classify_market(ticker, m.get("title") or "", m.get("_category",""), d)
    if market_class_sc == "COMMODITY":
        try:
            _cm_prob, _cm_edge, _cm_dir, _cm_src = calculate_commodity_edge(ticker, mid)
            if _cm_prob is not None and abs(_cm_edge) >= 0.18:
                # Strong model edge → inject confidence boost proportional to edge
                commodity_boost = min(0.55, abs(_cm_edge) * 1.5)
                commodity_note  = "commodity_edge=%.2f" % _cm_edge
                # Pre-wire the direction and edge so should_execute can use them
                if "direction" not in m or m.get("direction") != _cm_dir:
                    pass  # will be overridden in later block
        except Exception:
            pass

    final_conf = max(0.0, min(1.0,
        base_conf + whale_boost + weather_boost + vel_boost + commodity_boost - time_penalty + near_res_boost
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

    # Store commodity boost for downstream use
    _commodity_boost_val  = commodity_boost
    _commodity_note_val   = commodity_note
    _market_class_sc_val  = market_class_sc

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
        "commodity_boost":   commodity_boost,
        "market_class":      market_class_sc,
        "_market":           m,
    }

def score_and_rank_markets(
    top_by_cat: dict,
    weather_signals: list = None,

    crypto_sigs: dict = None,
) -> tuple:
    """
    Flatten top_by_cat, score top candidates, return ranked plays.
    Uses edge-quality composite scoring: tier_multiplier * (base_liq*0.4 + edge_score*0.6).
    JUNK markets (tier 0.0) are excluded entirely.
    weather_signals: list of weather signal dicts.
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

    # Flatten all liquid markets — COMMODITY and CRYPTO_SHORT first so they score
    # before any timeout, since they have direct data model edge
    PRIORITY_ORDER = ["COMMODITY", "CRYPTO_SHORT", "ECONOMIC_DATA"]
    priority_cats  = {cat: top_by_cat[cat] for cat in PRIORITY_ORDER if cat in top_by_cat}
    other_cats     = {cat: mkts for cat, mkts in top_by_cat.items() if cat not in PRIORITY_ORDER}
    ordered_cats   = {**priority_cats, **other_cats}

    all_tops = []
    for cat, markets in ordered_cats.items():
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
        # IMPROVEMENT 4: edge-dominant scoring for markets with real model edge
        # econ_edge pre-computed here via weather_edge_scores proxy (crypto/weather signals);
        # full econ-model override happens later in the candidate loop.
        # For initial ranking, use edge-dominant formula when edge_score is set.
        if edge_score > 0.0:
            composite = tier_mult * (base_liq * 0.1 + edge_score * 0.9)
        else:
            composite = tier_mult * (base_liq * 0.4 + edge_score * 0.6)
        scored_markets.append((m, composite, market_class))

    # Sort by composite score, take top N candidates
    scored_markets.sort(key=lambda x: x[1], reverse=True)
    # Ensure COMMODITY and CRYPTO_SHORT markets are always in candidates
    # They may rank low on volume but have strong model edge
    priority_mc = {"COMMODITY", "CRYPTO_SHORT"}
    priority_cands = [(m, c, mc) for m, c, mc in scored_markets if mc in priority_mc]
    other_cands    = [(m, c, mc) for m, c, mc in scored_markets if mc not in priority_mc]
    # Put priority markets first, fill remaining slots with top others
    merged = priority_cands + other_cands
    candidates = [(m, c, mc) for m, c, mc in merged[:WHALE_FETCH_TOP_N]]

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
                # Override composite with data-driven edge — edge dominates (IMPROVEMENT 4)
                edge_score = min(abs(econ_edge), 1.0)
                composite = tier_mult * (base_liq * 0.1 + edge_score * 0.9)
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

        # EUR/USD edge calculator
        if market_class == "ECONOMIC_DATA" and ticker.upper().startswith('KXEURUSD'):
            model_prob, econ_edge, econ_dir, econ_src = calculate_eurusd_edge(ticker, mid)
            if model_prob is not None:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[Forex] {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} -> {econ_dir} ({econ_src})")

        # FOMC edge calculator for KXFEDDECISION markets
        if market_class == "ECONOMIC_DATA" and ticker.upper().startswith('KXFEDDECISION'):
            model_prob, econ_edge, econ_dir, econ_src = calculate_fomc_edge(ticker, mid)
            if model_prob is not None:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[FOMC] {ticker}: model={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} -> {econ_dir} ({econ_src})")

        # NFP edge calculator for KXPAYROLLS markets
        if market_class == "ECONOMIC_DATA" and ticker.upper().startswith('KXPAYROLLS'):
            model_prob, econ_edge, econ_dir, econ_src = calculate_nfp_edge(ticker, mid)
            if model_prob is not None:
                play["econ_edge"] = round(econ_edge, 3)
                play["econ_direction"] = econ_dir
                play["econ_source"] = econ_src
                play["direction"] = econ_dir
                if abs(econ_edge) > 0.10:
                    play["composite"] = play.get("composite", 0) + abs(econ_edge) * tier_mult
                log.info(f"[NFP] {ticker}: model_prob={model_prob:.0%} kalshi={mid:.0%} edge={econ_edge:+.2f} → {econ_dir} ({econ_src})")

        # CRYPTO_15M edge calculator for KXBTC15M/KXETH15M 15-minute momentum markets
        if market_class == 'CRYPTO_15M':
            prob, edge_val, edge_dir, edge_src = calculate_crypto15m_edge(ticker, m)
            if prob is not None:
                play["econ_edge"] = round(edge_val, 3)
                play["econ_direction"] = edge_dir
                play["econ_source"] = edge_src
                play["direction"] = edge_dir
                play["market_class"] = "CRYPTO_15M"
                if abs(edge_val) >= 0.10:
                    play["composite"] = play.get("composite", 0) + abs(edge_val) * tier_mult
                log.info(f"[Crypto15M] Wired {ticker}: prob={prob:.2f} kalshi={mid:.2f} edge={edge_val:+.3f} → {edge_dir}")

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

                # WTI-specific: apply EIA thesis direction filter
                # Only trade in the direction supported by inventory data
                if "KXWTI" in ticker.upper() or "KXBRENT" in ticker.upper():
                    _oil_thesis = get_oil_thesis_direction()
                    if _oil_thesis != "NEUTRAL":
                        # BULLISH thesis: prefer YES (oil stays high) — block NO positions below threshold
                        # BEARISH thesis: prefer NO (oil won't spike) — block YES positions above threshold
                        _direction_ok = (
                            (_oil_thesis == "BULLISH" and econ_dir == "YES") or
                            (_oil_thesis == "BEARISH" and econ_dir == "NO") or
                            True  # neutral = allow both
                        )
                        if not _direction_ok:
                            log.info("[EIA] Blocking %s %s — EIA thesis is %s", econ_dir, ticker, _oil_thesis)
                            play["composite"] = 0  # zero out composite so selector won't pick it

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
        # Log signal for replay engine
        try:
            log_signal(play)
        except Exception:
            pass
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

# GDP STOP-LOSS MONITOR

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

# DISCORD OUTPUT

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
    """Post message to a Discord channel via Economics bot (max 2000 chars per chunk)."""
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
) -> str:
    """
    Format the single curated Economics report Discord post.
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

    lines += [
        sep,
        f"Next discovery: 30min | Realtime monitor: 30sec",
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

# EXECUTION ENGINE (unchanged from v2)

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
    """Size position using half-Kelly when edge is known, else fixed-pct fallback."""
    mid_c         = signal.get("mid_c", 50)
    price_dollars = max(mid_c / 100.0, 0.01)
    edge          = signal.get("edge", None)

    if edge is not None:
        # Half-Kelly: f = 0.5 * |edge| / (1 - entry_price)
        denom = max(1.0 - price_dollars, 0.01)
        f     = 0.5 * abs(float(edge)) / denom
        # Floor 2%, cap at EXEC_MAX_PER_POSITION (35%)
        f     = max(0.02, min(f, EXEC_MAX_PER_POSITION))
        spend = balance * f
        log.debug(f"[KELLY] edge={edge:.3f} price={price_dollars:.2f} f={f:.3f} spend=${spend:.2f}")
    else:
        # Fallback: fixed 5% (original logic)
        spend = balance * EXEC_POSITION_SIZE_PCT
        log.debug(f"[KELLY] No edge — fixed size_pct={EXEC_POSITION_SIZE_PCT:.2f} spend=${spend:.2f}")

    max_spend = balance * EXEC_MAX_PER_POSITION
    spend     = min(spend, max_spend)

    # Longshot cap: markets priced < 15c capped at MAX_LONGSHOT_DOLLARS
    # Prevents Kelly from over-sizing cheap contracts with thin edge
    if price_dollars < 0.15:
        spend = min(spend, MAX_LONGSHOT_DOLLARS)
        log.debug("[KELLY] Longshot cap applied (price=%.0fc): spend capped at $%.2f",
                  price_dollars * 100, spend)

    contracts = max(1, math.floor(spend / price_dollars))
    return contracts


def log_signal(play: dict, scan_ts: str = None):
    """
    Log every scored market signal to data/signals.jsonl for replay analysis.
    Called once per scored play in score_and_rank_markets().
    Fields needed by the replay engine: model_p, market_p, edge, buffer, close_time, asset_class.
    """
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz

    try:
        sig_path = _Path("/home/cody/stratton/data/signals.jsonl")
        sig_path.parent.mkdir(parents=True, exist_ok=True)

        # Rotate if > 50MB
        if sig_path.exists() and sig_path.stat().st_size > 52428800:
            sig_path.rename(str(sig_path) + ".old")

        mid_c = play.get("mid_c", 50)
        econ_edge = play.get("econ_edge", 0) or 0
        market_p = mid_c / 100.0
        model_p = min(0.99, market_p + abs(econ_edge))

        # For crypto/commodity: pull edge directly from signal if econ_edge not set
        _spot_price = play.get("spot_price", 0)
        _threshold = play.get("threshold", 0)
        _crypto_edge = play.get("edge", 0) or econ_edge  # crypto uses 'edge' field
        _direction = play.get("direction", play.get("econ_direction", "YES"))

        record = {
            "ts":           scan_ts or _dt.now(_tz.utc).isoformat(),
            "ticker":       play.get("ticker", ""),
            "asset_class":  play.get("market_class", "UNKNOWN"),
            "underlying":   play.get("ticker", "")[:8].replace("KX",""),
            "direction":    _direction,
            "mid_c":        mid_c,
            "market_p":     round(market_p, 4),
            "model_p":      round(min(0.99, market_p + abs(_crypto_edge)), 4),
            "econ_edge":    round(float(_crypto_edge), 4),
            "confidence":   round(float(play.get("confidence", 0)), 4),
            "conf_label":   play.get("conf_label", "LOW"),
            "composite":    round(float(play.get("composite_score", 0)), 6),
            "close_time":   play.get("_market", {}).get("close_time", "") if play.get("_market") else "",
            "days_to_close": play.get("days_until_close", 999),
            "econ_source":  play.get("econ_source", ""),
            "spot_price":   _spot_price,
            "threshold":    _threshold,
            "executed":     False,
        }
        with open(sig_path, "a") as _f:
            _f.write(_json.dumps(record) + "\n")
    except Exception as _e:
        log.debug("[SignalLog] Failed to log signal: %s", _e)


def _should_execute_inner(signal: dict, balance: float, positions: dict, total_exposure: float) -> tuple:
    """Check all guardrails. Returns (ok, reason). Never bypass."""
    ticker    = signal.get("ticker", "UNKNOWN")
    direction = signal.get("direction", "YES")
    mid_c     = signal.get("mid_c", 50)
    conf      = signal.get("confidence", 0.0)
    days_out  = signal.get("days_until_close", 999)

    if days_out > 90:
        return False, f"market resolves in {days_out} days (>90 day auto-exec limit)"

    # Only execute on categories where we have data model edge
    EXECUTABLE_CATEGORIES = {"ECONOMIC_DATA", "CRYPTO_SHORT", "COMMODITY"}  # 15M still paused — selector enforced  # WEATHER is Mark Hanna/weather.py's domain
    market_class = signal.get("market_class", "")
    if market_class not in EXECUTABLE_CATEGORIES:
        return False, f"category '{market_class}' is inform-only — no autonomous execution"

    # ── CRYPTO_15M special guardrails (lower edge bar, daily cap) ────────────
    if market_class == 'CRYPTO_15M':
        # Lower edge bar: 10c minimum (not 18c)
        crypto15m_edge = abs(signal.get('econ_edge', 0.0))
        if crypto15m_edge < 0.10:
            return False, "CRYPTO_15M edge %.2f < 0.10 minimum" % crypto15m_edge
        # Daily cap: max 2 CRYPTO_15M positions per UTC day
        today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        try:
            import json as _j
            from pathlib import Path as _p
            _es = _j.loads(_p('/home/cody/stratton/data/eval_store.json').read_text())
            _today_15m = sum(1 for t in _es
                            if t.get('market_class') == 'CRYPTO_15M'
                            and today_utc in (t.get('entry_date', '') or ''))
            if _today_15m >= 2:
                return False, "CRYPTO_15M daily cap: already %d positions today" % _today_15m
        except Exception:
            pass  # eval_store missing or unreadable — allow trade
        # Skip standard confidence threshold for 15M — use our own edge check above
        # Fall through to remaining guardrails (balance, exposure, etc.)


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

    # ── Correlation Cap — limit open positions per market class ──────────────
    _cap = MAX_POSITIONS_PER_CLASS.get(market_class)
    if _cap is not None:
        # Count open positions in same class (classify each open ticker)
        _open_in_class = 0
        for _open_ticker in positions:
            try:
                _open_class = classify_market(_open_ticker, "", "", datetime.now(timezone.utc))
            except Exception:
                _open_class = ""
            if _open_class == market_class:
                _open_in_class += 1
        if _open_in_class >= _cap:
            return False, (f"correlation cap: already {_open_in_class}/{_cap} open {market_class} positions")

    # Thesis direction lock — never trade against hard-coded thesis
    locked_direction = _get_active_thesis_locks().get(ticker)
    if locked_direction and direction.upper() != locked_direction:
        return False, f"thesis lock: {ticker} must be {locked_direction}, signal says {direction}"

    # Hard dollar cap — never exceed $150 total exposure regardless of % of balance
    if total_exposure >= EXEC_MAX_TOTAL_DOLLARS:
        return False, ("total exposure $%.2f >= hard cap $%.2f" % (total_exposure, EXEC_MAX_TOTAL_DOLLARS))

    # Per-underlying cap — max $30 per single asset (WTI separate from Gold separate from BTC)
    _underlying = ticker.split("-")[0].replace("KX","") if "-" in ticker else ticker[:8]
    _underlying_exp = sum(
        float(p.get("market_exposure_dollars", 0) or 0)
        for t, p in positions.items()
        if t.split("-")[0].replace("KX","") == _underlying or _underlying in t.split("-")[0]
    ) if positions else 0.0
    if _underlying_exp >= EXEC_MAX_PER_UNDERLYING:
        return False, ("underlying cap for %s: $%.2f >= $%.2f max" % (_underlying, _underlying_exp, EXEC_MAX_PER_UNDERLYING))

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

def should_execute(signal: dict, balance: float, positions: dict, total_exposure: float) -> tuple:
    """Public wrapper: calls _should_execute_inner and logs skips via log_skip."""
    result = _should_execute_inner(signal, balance, positions, total_exposure)
    ok, reason = result
    if not ok:
        try:
            log_skip(signal.get("ticker", "UNKNOWN"), reason, signal)
        except Exception as _lse:
            log.debug(f"[SKIP_LOG] Error in log_skip: {_lse}")
    return result

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


# ── Daily Loss Kill Switch helpers ───────────────────────────────────────────

def _load_daily_loss() -> dict:
    """Load today's daily loss state; reset if date mismatch."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if os.path.exists(DAILY_LOSS_PATH):
            with open(DAILY_LOSS_PATH) as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "realized_loss": 0.0, "halt_trading": False}

def _save_daily_loss(state: dict) -> None:
    """Persist daily loss state to JSON."""
    try:
        os.makedirs(os.path.dirname(DAILY_LOSS_PATH), exist_ok=True)
        with open(DAILY_LOSS_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.warning(f"[DAILY_LOSS] Failed to save state: {e}")

def update_daily_loss(amount: float) -> None:
    """Add realized loss (positive = loss) and check limit."""
    state = _load_daily_loss()
    state["realized_loss"] = round(state["realized_loss"] + amount, 4)
    if state["realized_loss"] >= DAILY_LOSS_LIMIT_DOLLARS and not state["halt_trading"]:
        state["halt_trading"] = True
        log.warning(f"[DAILY_LOSS] Daily loss limit hit: ${state['realized_loss']:.2f} >= ${DAILY_LOSS_LIMIT_DOLLARS:.2f} — trading halted")
    _save_daily_loss(state)


# ── Skip Reason Logger ────────────────────────────────────────────────────────

def log_skip(ticker: str, reason: str, signal: dict) -> None:
    """Append a skip record to skip_log.jsonl; rotate if > 10 MB."""
    try:
        # Rotate if oversized
        if os.path.exists(SKIP_LOG_PATH) and os.path.getsize(SKIP_LOG_PATH) > SKIP_LOG_MAX_BYTES:
            os.replace(SKIP_LOG_PATH, SKIP_LOG_PATH + ".old")
        os.makedirs(os.path.dirname(SKIP_LOG_PATH), exist_ok=True)
        record = {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "ticker":      ticker,
            "reason":      reason,
            "edge":        round(float(signal.get("edge", 0.0) or 0.0), 4),
            "model_prob":  round(float(signal.get("model_prob", 0.0) or 0.0), 4),
            "kalshi_mid":  round(float(signal.get("mid_c", 0.0) or 0.0) / 100.0, 4),
            "direction":   signal.get("direction", ""),
            "market_class": signal.get("market_class", ""),
        }
        with open(SKIP_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log.debug(f"[SKIP_LOG] Write error: {e}")

def run_execution_check(plays: list, dry_run: bool = False):
    """Check HIGH-confidence signals and execute if guardrails pass."""
    # ── Daily Loss Kill Switch ────────────────────────────────────────────────
    _daily = _load_daily_loss()
    if _daily.get("halt_trading"):
        _loss = _daily.get("realized_loss", 0.0)
        _msg  = (f"🛑 TRADING HALTED — daily loss ${_loss:.2f} >= limit ${DAILY_LOSS_LIMIT_DOLLARS:.2f}. "
                 f"Resets at UTC midnight.")
        log.warning(f"[DAILY_LOSS] {_msg}")
        # Alert only once per halt (check flag)
        _halted_flag = DAILY_LOSS_PATH + ".alerted"
        if not os.path.exists(_halted_flag):
            post_discord(_msg, channel_id=DISCORD_CH_RESULTS, dry_run=dry_run)
            try:
                open(_halted_flag, "w").close()
            except Exception:
                pass
        return

    # Clear alert flag if we're a new day (state was reset)
    _halted_flag = DAILY_LOSS_PATH + ".alerted"
    if os.path.exists(_halted_flag):
        try:
            os.remove(_halted_flag)
        except Exception:
            pass

    high_signals = [p for p in plays if p["confidence"] >= CONFIDENCE_HIGH_THRESHOLD]
    log.info(f"[EXEC] {len(high_signals)} HIGH confidence signals to evaluate")

    # ── Selector import (cluster-based signal filtering) ─────────────────────
    try:
        import sys as _sys_sel
        if '/home/cody/stratton/bots' not in _sys_sel.path:
            _sys_sel.path.insert(0, '/home/cody/stratton/bots')
        from selector import Signal as _Signal, SelectorParams as _SParams, select_from_cluster as _select, portfolio_gate as _pgate, OpenPosition as _OPos, PortfolioState as _PState, make_cluster_key as _cluster_key
        _SELECTOR_AVAILABLE = True
    except ImportError as _e:
        log.warning("[Selector] selector.py not available: %s — running without cluster filter", _e)
        _SELECTOR_AVAILABLE = False

    # CRYPTO TIER FILTER: For CRYPTO_SHORT markets, enforce 2-tier selection
    # (1 primary per close time + 1 global longshot) to prevent spray of positions.
    # Score = edge * kelly * payout_multiple. Select best per close time.
    _crypto_executed_closes: set = set()
    _crypto_longshot_used: bool = False

    def _crypto_allowed(sig: dict) -> tuple:
        """Returns (allowed: bool, reason: str) for CRYPTO_SHORT signals."""
        nonlocal _crypto_longshot_used
        mc = sig.get("market_class", "")
        if mc != "CRYPTO_SHORT":
            return True, "not crypto"
        mid_c = sig.get("mid_c", 50)
        price = mid_c / 100.0
        ticker = sig.get("ticker", "")
        close_key = ""
        try:
            from datetime import timezone as _tz
            m = sig.get("_market", {})
            ct_str = m.get("close_time", "") if m else ""
            close_key = ct_str[:13]  # e.g. "2026-05-03T22"
        except Exception:
            close_key = ticker[:15]

        econ_edge = abs(sig.get("econ_edge", 0) or 0)
        denom = max(1.0 - price, 0.01)
        kelly_f = 0.5 * econ_edge / denom if econ_edge else 0
        payout = (1.0 - price) / max(price, 0.01) if price > 0 else 1.0
        score = econ_edge * kelly_f * payout

        # Longshot tier: entry < 15c
        if price < 0.15:
            if _crypto_longshot_used:
                return False, "CRYPTO_SHORT global longshot cap (1 per scan)"
            _crypto_longshot_used = True
            return True, "CRYPTO_SHORT LONGSHOT tier (score=%.4f)" % score

        # Primary tier: 1 per close time
        if close_key in _crypto_executed_closes:
            return False, "CRYPTO_SHORT primary already executed for close %s" % close_key
        _crypto_executed_closes.add(close_key)
        return True, "CRYPTO_SHORT PRIMARY tier (score=%.4f close=%s)" % (score, close_key)

    # ── Selector cluster filter ──────────────────────────────────────────────
    _selector_picks: set = set()  # set of market_ticker strings that selector approved
    if _SELECTOR_AVAILABLE:
        _sel_signals = []
        for play in high_signals:
            try:
                _ticker = play.get("ticker", "")
                _mc = play.get("market_class", "UNKNOWN")
                _mid_c = play.get("mid_c", 50)
                _price = _mid_c / 100.0
                _edge = abs(play.get("econ_edge", 0) or 0)
                _conf = play.get("confidence", 0.5)
                _m = play.get("_market", {})
                _close_str = _m.get("close_time", "") if _m else ""
                _res_dt = None
                if _close_str:
                    try:
                        _res_dt = datetime.fromisoformat(_close_str.replace("Z", "+00:00"))
                    except Exception:
                        pass
                _hours = 999.0
                if _res_dt:
                    _hours = max(0.01, (_res_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
                _sig = _Signal(
                    market_ticker=_ticker,
                    asset_class=_mc,
                    underlying=_ticker.split("-")[0].replace("KX",""),
                    direction=play.get("direction", "YES"),
                    price_cents=_mid_c,
                    model_p=min(0.99, _price + _edge),
                    market_p=_price,
                    directional_edge=_edge,
                    confidence=_conf,
                    buffer_pct=max(0.001, _edge),
                    resolution_dt=_res_dt,
                    hours_to_resolution=_hours,
                    proposed_size=play.get("proposed_size", 20.0) or 20.0,
                )
                _sel_signals.append(_sig)
            except Exception as _se:
                log.debug("[Selector] signal build failed for %s: %s", play.get("ticker","?"), _se)
                _selector_picks.add(play.get("ticker",""))  # allow through if we can't build Signal

        if _sel_signals:
            _sel_params = _SParams()
            _selected, _rejected = _select(_sel_signals, _sel_params)
            _selector_picks = {s.market_ticker for s in _selected}
            log.info("[Selector] %d signals -> %d selected, %d rejected by cluster filter",
                     len(_sel_signals), len(_selected), len(_rejected))
            for _r in _rejected:
                log.info("[Selector] REJECTED %s: %s", _r.market_ticker, _r.rejection_reason)
                log_skip(_r.market_ticker, "selector: " + (_r.rejection_reason or ""), {})
    else:
        # No selector available — allow all through (fallback to old behavior)
        _selector_picks = {p.get("ticker","") for p in high_signals}

    for signal in high_signals:
        try:
            balance    = get_balance()
            positions  = get_open_positions()
            total_exp  = get_total_exposure(positions)
            # Selector cluster filter — skip if not selected
            if _SELECTOR_AVAILABLE and signal.get("ticker","") not in _selector_picks:
                log.info("[Selector] %s filtered out by cluster selector", signal.get("ticker",""))
                continue

            # Apply crypto tier filter before standard guardrails
            _allowed, _allow_reason = _crypto_allowed(signal)
            if not _allowed:
                log.info("[EXEC] Skipping %s: %s", signal.get("ticker","?"), _allow_reason)
                log_skip(signal.get("ticker","?"), _allow_reason, signal)
                continue

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
                        # Gate 6 logic:
                        # edge > 40c: auto-approve — overwhelming model confidence, LLM adds noise
                        # edge 20-40c: Grok single-LLM check, no consensus required
                        # Rationale: strong quant edge should NOT require dual-LLM consensus.
                        #   High edge = model is confident. Consensus gate was blocking best signals.
                        #   LLM value is in flagging marginal trades, not vetoing strong ones.
                        if _econ_edge_g6 >= 0.40:
                            log.info(f"[ECONOMICS] Gate 6 AUTO-APPROVE {_g6_ticker}: edge={_econ_edge_g6:.2f} >= 0.40 threshold")
                            _g6_result = {"go": True, "reasoning": "auto-approved: edge >= 40c", "confidence": "HIGH"}
                        else:
                            # Grok only — no consensus required for 20-40c edge
                            _g6_result = llm_reason(
                                _g6_prompt,
                                primary="grok",
                                shadow=None,
                                require_consensus=False
                            )
                        if not _g6_result.get("go", True):  # default True if LLM fails
                            _g6_reason = _g6_result.get('reasoning', 'no reason')[:100]
                            log.info(f"[ECONOMICS] LLM gate BLOCKED {_g6_ticker}: {_g6_reason}")
                            post_discord(
                                "\U0001f9e0 LLM BLOCK: " + _g6_ticker + " | " + _g6_result.get('reasoning', '')[:200],
                                channel_id=DISCORD_CH_RESULTS,
                                dry_run=dry_run,
                            )
                            continue  # skip this candidate — LLM said no
                        _g6_conf = _g6_result.get('confidence', 'unknown')
                        _g6_rsn  = _g6_result.get('reasoning', '')[:80]
                        log.info(f"[ECONOMICS] LLM gate PASSED {_g6_ticker}: confidence={_g6_conf} | {_g6_rsn}")
                    except Exception as _g6_err:
                        log.warning(f"[ECONOMICS] LLM gate error (trade proceeds): {_g6_err}")
                        # LLM failure = never block the trade (graceful degradation)

                log.info(f"[EXEC] ✅ Executing {signal['ticker']}{' (DRY RUN)' if dry_run else ''}")
                result = execute_trade(signal, dry_run=dry_run)
                post_execution_result(signal, result, dry_run=dry_run)
                # Mark this signal as executed in the signal log
                try:
                    import json as _jex
                    from pathlib import Path as _pex
                    _sp = _pex("/home/cody/stratton/data/signals.jsonl")
                    if _sp.exists():
                        _lines = _sp.read_text().splitlines()
                        _ticker = signal.get("ticker","")
                        _updated = []
                        for _line in _lines:
                            try:
                                _rec = _jex.loads(_line)
                                if _rec.get("ticker") == _ticker and not _rec.get("executed"):
                                    _rec["executed"] = True
                                    _line = _jex.dumps(_rec)
                            except Exception:
                                pass
                            _updated.append(_line)
                        _sp.write_text("\n".join(_updated) + "\n")
                except Exception:
                    pass
                # ── Eval Framework: log trade at entry ────────────────────────
                if result.get('status') not in ('failed', 'skipped'):
                    try:
                        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                        from eval_framework import log_trade_entry as _ef_log
                        _ef_log(
                            trade_id=signal.get('ticker', 'unknown'),
                            agent='economics',
                            market=signal.get('ticker', 'unknown'),
                            direction=signal.get('direction', 'YES'),
                            entry_edge_pct=abs(signal.get('edge_dollars', 0)) * 100,
                            llm_confidence=str(signal.get('confidence', '')),
                            raw_thesis=f"mid={signal.get('mid_c', 0)}c model_prob={signal.get('model_prob', 0):.3f}",
                            raw_llm_reason=signal.get('llm_reason', '')[:500] if signal.get('llm_reason') else '',
                            exposure_dollars=round(signal.get('contracts', 1) * (signal.get('mid_c', 50) / 100), 2),
                            market_class=signal.get('market_class', ''),
                            entry_date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                        )
                    except Exception:
                        pass  # eval logging never blocks execution
            else:
                log.info(f"[EXEC] Skipping {signal['ticker']}: {reason}")

        except Exception as e:
            log.error(f"[EXEC] Error processing {signal.get('ticker', '?')}: {e}", exc_info=True)

# TIER 1 — DISCOVERY SCAN

def run_discovery_scan(dry_run: bool = False) -> tuple:
    """
    Full market scan. Updates global watchlist for Tier 2.
    Returns (all_markets_flat, plays, weather_signals, arb_opps).
    """
    global watchlist, last_scan_top_markets

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

    weather_signals = []  # weather is Mark Hanna's domain

    # Score everything (inject smart money + crypto signals)
    log.info(f"Scoring top {WHALE_FETCH_TOP_N} candidates...")
    plays, whale_events = score_and_rank_markets(
        top_by_cat,
        weather_signals=weather_signals,
        crypto_sigs=crypto_signals,
    )

    # Update watchlist (top WATCHLIST_SIZE markets for Tier 2)
    # Always include COMMODITY and CRYPTO_SHORT markets with edge — they may rank low
    # on composite score due to thin volume but have strong model edge
    _priority_markets = []
    _seen_tickers = set()
    for p in plays:
        mc = p.get("market_class", "")
        if mc in ("COMMODITY", "CRYPTO_SHORT", "CRYPTO_15M") and abs(p.get("econ_edge", 0)) >= 0.10:
            if "_market" in p and p["ticker"] not in _seen_tickers:
                _priority_markets.append(p["_market"])
                _seen_tickers.add(p["ticker"])
    # Fill remaining slots with top composite plays
    _remaining = WATCHLIST_SIZE - len(_priority_markets)
    for p in plays:
        if p.get("ticker") not in _seen_tickers and "_market" in p and _remaining > 0:
            _priority_markets.append(p["_market"])
            _seen_tickers.add(p["ticker"])
            _remaining -= 1
    watchlist = _priority_markets[:WATCHLIST_SIZE]
    last_scan_top_markets = [p["ticker"] for p in plays if p["conf_label"] in ("HIGH", "MEDIUM")]
    commodity_in_wl = sum(1 for m in watchlist if classify_market(
        m.get("ticker",""), m.get("title",""), m.get("_category",""),
        days_until_close(m)) in ("COMMODITY", "CRYPTO_SHORT", "CRYPTO_15M"))
    log.info(f"Watchlist updated: {len(watchlist)} markets ({commodity_in_wl} commodity/crypto) | Top radar: {len(last_scan_top_markets)}")

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

    arb_opps = []

    # Silent mode — only post to Discord on trade execution or arb alerts
    # Scan reports are suppressed to avoid noise. Daily heartbeat handled separately.
    report = format_donnie_report(plays, len(all_markets), len(top_by_cat), whale_events,
                                   weather_signals=weather_signals)
    if report:
        log.info(f"Scan found {len([p for p in plays if p.get('conf_label') in ('HIGH','MEDIUM')])} actionable plays — suppressing Discord report (silent mode)")
    else:
        log.info("No actionable plays this scan")

    # Execution check
    run_execution_check(plays, dry_run=dry_run)

    log.info(f'[SCAN] Order book boost fired {_orderbook_boost_count} times this session')

    return all_markets, plays, weather_signals, arb_opps

# TIER 2 — REAL-TIME MONITOR

def check_order_fills(dry_run: bool = False):
    """
    Check if any Economics resting orders have filled.
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
                                agent='economics',
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

# WHALE SCAN (legacy 15-min check — kept from v2)

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

# POLYMARKET SMART MONEY TRACKER

# REAL-TIME CRYPTO PRICE MONITOR

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


# Fear & Greed cache (1-hour TTL — index changes daily)
_fng_cache: dict = {}
_fng_cache_ts: float = 0.0

def fetch_crypto_fear_greed() -> dict:
    """
    Fetch Crypto Fear & Greed Index from alternative.me (free, no auth).
    Cached for 1 hour.
    Returns {"value": int, "classification": str, "timestamp": str}
    """
    import time as _time
    global _fng_cache, _fng_cache_ts
    now = _time.time()
    if _fng_cache and (now - _fng_cache_ts) < 3600:
        return _fng_cache
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=8, headers={"Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json().get("data", [{}])[0]
            val = int(data.get("value", 50))
            cls = data.get("value_classification", "Neutral")
            ts  = data.get("timestamp", "")
            _fng_cache = {"value": val, "classification": cls, "timestamp": ts}
            _fng_cache_ts = now
            log.info("[Crypto] Fear & Greed: %d (%s)", val, cls)
            return _fng_cache
        log.warning("[Crypto] FNG API → %d", r.status_code)
    except Exception as e:
        log.warning("[Crypto] FNG fetch error: %s", e)
    return {"value": 50, "classification": "Neutral", "timestamp": ""}


def score_crypto_signal(
    spot_prob: float,
    kalshi_mid: float,
    edge: float,
    entry_price: float,
    fear_greed_value: int,
    direction: str,
) -> tuple:
    """
    Mathematical score to select the single best BTC/ETH position per close time.

    Components:
    1. Expected Value = edge (model_prob - market_prob) — primary driver
    2. Kelly fraction = 0.5 * abs(edge) / (1 - entry_price) — sizing efficiency
    3. Risk/Reward = payout_multiple = (1 - entry_price) / entry_price — asymmetry
    4. Sentiment alignment bonus:
       +0.05 if direction == "YES" and fear_greed > 55  (greed supports up moves)
       +0.05 if direction == "NO"  and fear_greed < 45  (fear supports down moves)
       0 otherwise

    Score = edge * kelly_f * payout_multiple * (1 + sentiment_bonus)
    Returns (score: float, sentiment_aligned: bool)
    """
    kelly_f = 0.5 * abs(edge) / max(1.0 - entry_price, 0.01)
    payout_multiple = max(1.0 - entry_price, 0.01) / max(entry_price, 0.01)
    if direction == "YES" and fear_greed_value > 55:
        sentiment_bonus = 0.05
        sentiment_aligned = True
    elif direction == "NO" and fear_greed_value < 45:
        sentiment_bonus = 0.05
        sentiment_aligned = True
    else:
        sentiment_bonus = 0.0
        sentiment_aligned = False
    score = edge * kelly_f * payout_multiple * (1.0 + sentiment_bonus)
    return score, sentiment_aligned

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



def fetch_btc_intraday_vol(symbol: str = "BTC") -> dict:
    """
    Fetch last 6 hourly OHLCV candles from Binance public API.
    Compute intraday realized volatility as std dev of log returns.
    
    Returns dict:
        current_vol_pct: float  -- current session hourly vol as % (annualized basis)
        candles: int            -- number of candles used
        session_trend: str      -- "UP" | "DOWN" | "FLAT" based on last 3 candles
        source: str
    """
    try:
        import math as _math
        # Use Kraken public OHLCV API (no geo-restrictions, no auth)
        pair = "XBTUSD" if symbol == "BTC" else "ETHUSD"
        r = requests.get(
            "https://api.kraken.com/0/public/OHLC",
            params={"pair": pair, "interval": 60},  # 60 = 1 hour
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if not r.ok:
            return {"current_vol_pct": 1.9, "candles": 0, "session_trend": "FLAT", "source": "fallback"}
        
        data = r.json()
        if data.get("error"):
            return {"current_vol_pct": 1.9, "candles": 0, "session_trend": "FLAT", "source": "fallback"}
        
        # Kraken OHLC: [time, open, high, low, close, vwap, volume, count]
        result_key = list(data.get("result", {}).keys())[0] if data.get("result") else None
        if not result_key:
            return {"current_vol_pct": 1.9, "candles": 0, "session_trend": "FLAT", "source": "fallback"}
        
        candles_raw = data["result"][result_key][-8:]  # last 8 candles
        if len(candles_raw) < 4:
            return {"current_vol_pct": 1.9, "candles": 0, "session_trend": "FLAT", "source": "fallback"}
        
        closes = [float(c[4]) for c in candles_raw[:-1]]  # exclude current open candle
        log_returns = [_math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
        
        import statistics as _stats
        if len(log_returns) >= 2:
            hourly_vol = _stats.stdev(log_returns)
            # Annualize: hourly -> daily -> annualized
            daily_vol_pct = hourly_vol * _math.sqrt(24) * 100
        else:
            daily_vol_pct = 1.9  # fallback
        
        # Session trend: compare last close to 3 candles ago
        if len(closes) >= 4:
            pct_change = (closes[-1] - closes[-4]) / closes[-4] * 100
            session_trend = "UP" if pct_change > 0.3 else ("DOWN" if pct_change < -0.3 else "FLAT")
        else:
            session_trend = "FLAT"
        
        log.info("[IntradayVol] %s: daily_vol=%.2f%% session=%s (%d candles)",
                 symbol, daily_vol_pct, session_trend, len(closes))
        return {
            "current_vol_pct": round(daily_vol_pct, 3),
            "candles": len(closes),
            "session_trend": session_trend,
            "source": "Binance/1h",
        }
    except Exception as e:
        log.debug("[IntradayVol] fetch failed: %s", e)
        return {"current_vol_pct": 1.9, "candles": 0, "session_trend": "FLAT", "source": "fallback"}

# Cache intraday vol — 30 min TTL (refreshes before each scan cycle)
_intraday_vol_cache: dict = {}
_intraday_vol_ts: float = 0.0

def get_btc_intraday_vol(symbol: str = "BTC") -> dict:
    global _intraday_vol_cache, _intraday_vol_ts
    now = time.time()
    cache_key = symbol
    if _intraday_vol_cache.get(cache_key) and (now - _intraday_vol_ts) < 1800:
        return _intraday_vol_cache[cache_key]
    result = fetch_btc_intraday_vol(symbol)
    _intraday_vol_cache[cache_key] = result
    _intraday_vol_ts = now
    return result


def crypto_horizon_buffer_check(ticker: str, spot: float, threshold: float, 
                                  close_time_str: str) -> tuple:
    """
    Enforce horizon-scaled minimum buffer for crypto range markets.
    Longer horizon = more buffer required because BTC can move more.
    
    Returns (passes: bool, reason: str, required_buffer: float)
    
    Rules:
      - >2 days: BLOCKED (too uncertain for directional thesis)
      - 1-2 days: 2.0% min buffer
      - 0-1 days: 0.5% min buffer
    """
    import math
    actual_buffer = abs(spot - threshold) / max(threshold, 1)
    
    hours_to_close = 999.0
    if close_time_str:
        try:
            ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            hours_to_close = max(0.01, (ct - datetime.now(timezone.utc)).total_seconds() / 3600)
        except Exception:
            pass
    
    days_to_close = hours_to_close / 24.0
    
    # Block positions >2 days out entirely
    if days_to_close > CRYPTO_MAX_HORIZON_DAYS:
        return False, ("horizon block: %.1f days > %d day max for crypto range markets" % (
            days_to_close, CRYPTO_MAX_HORIZON_DAYS)), 1.0
    
    # Scale buffer requirement: 2 * daily_vol * sqrt(days)
    # Floor at 0.5% for same-day, 2.0% for 1-2 day
    # Use current intraday vol if available — adapts to market conditions
    _sym = "BTC" if "BTC" in ticker.upper() else "ETH"
    _vol_data = get_btc_intraday_vol(_sym)
    _current_daily_vol = _vol_data.get("current_vol_pct", 1.9) / 100.0
    _session_trend = _vol_data.get("session_trend", "FLAT")

    if days_to_close <= 1.0:
        # Same-day: use current vol. High vol session = wider buffer required
        if _current_daily_vol > 0.025:  # >2.5% daily vol = elevated
            required_buffer = _current_daily_vol * 0.5  # 50% of daily vol as buffer
        else:
            required_buffer = 0.005  # 0.5% standard
    else:
        # Multi-day: scale by sqrt(days) using current vol
        import math as _mth
        required_buffer = 1.5 * _current_daily_vol * _mth.sqrt(days_to_close)
        required_buffer = max(0.020, required_buffer)  # floor at 2.0% for multi-day
    
    if actual_buffer < required_buffer:
        return False, ("buffer %.3f%% < %.3f%% required for %.1fd horizon" % (
            actual_buffer * 100, required_buffer * 100, days_to_close)), required_buffer
    
    return True, "ok", required_buffer



def update_btc_price_history(symbol: str = "BTC") -> dict:
    """
    Lightweight BTC price tracker. Called every 5 min by btc_watch scanner.
    Checks three conditions for triggering a full Kalshi crypto scan:
      1. Price moved >1.0% in last 15 min (last 3 readings)
      2. Intraday vol > 1.5%
      3. Kraken hourly volume > 1.2x 6-candle average
    Sets _btc_scan_triggered flag if all three conditions met.
    """
    global _btc_price_history, _btc_scan_triggered, _btc_last_trigger_reason

    # Fetch current spot price
    spot_data = get_crypto_spot()
    price = 0.0
    if isinstance(spot_data, dict):
        sym_data = spot_data.get(symbol, {})
        if isinstance(sym_data, dict):
            price = float(sym_data.get("price", 0))
        elif isinstance(sym_data, (int, float)):
            price = float(sym_data)

    if price <= 0:
        log.warning("[BTC_WATCH] Could not fetch %s spot price", symbol)
        return {"price": 0, "pct_change_15m": 0, "vol_pct": 0, "vol_ratio": 0, "triggered": False}

    now = datetime.now(timezone.utc)
    _btc_price_history.append((now, price))
    # Keep only last 12 readings (1 hour at 5-min intervals)
    if len(_btc_price_history) > 12:
        _btc_price_history = _btc_price_history[-12:]

    # ── Condition 1: price move >1.0% in last 15 min (last 3 readings) ──
    pct_change_15m = 0.0
    cond1 = False
    if len(_btc_price_history) >= 3:
        price_15m_ago = _btc_price_history[-3][1]
        if price_15m_ago > 0:
            pct_change_15m = (price - price_15m_ago) / price_15m_ago * 100
            cond1 = abs(pct_change_15m) > 1.0
    elif len(_btc_price_history) >= 2:
        price_prev = _btc_price_history[-2][1]
        if price_prev > 0:
            pct_change_15m = (price - price_prev) / price_prev * 100

    # ── Condition 2: intraday vol > 1.5% ──
    vol_pct = 0.0
    cond2 = False
    try:
        vol_data = get_btc_intraday_vol(symbol)
        vol_pct = vol_data.get("current_vol_pct", 0.0)
        cond2 = vol_pct > 1.5
    except Exception as e:
        log.warning("[BTC_WATCH] Vol check failed: %s", e)

    # ── Condition 3: Kraken hourly volume > 1.2x 6-candle average ──
    vol_ratio = 0.0
    cond3 = False
    try:
        kraken_url = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60&limit=7"
        resp = requests.get(kraken_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Kraken OHLC: result[pair] = list of [time, open, high, low, close, vwap, volume, count]
        pair_key = next((k for k in data.get("result", {}) if k != "last"), None)
        if pair_key:
            candles = data["result"][pair_key]
            # candles[-1] is current (incomplete), candles[:-1] are closed
            if len(candles) >= 7:
                closed_volumes = [float(c[6]) for c in candles[:-1]]  # last 6 closed candles
                current_volume = float(candles[-1][6])
                avg_volume = sum(closed_volumes) / len(closed_volumes) if closed_volumes else 0
                if avg_volume > 0:
                    vol_ratio = current_volume / avg_volume
                    cond3 = vol_ratio > 1.2
    except Exception as e:
        log.warning("[BTC_WATCH] Kraken volume check failed: %s", e)

    log.info("[BTC_WATCH] %s=$%.0f pct15m=%+.2f%% vol=%.2f%% vol_ratio=%.2fx | C1=%s C2=%s C3=%s",
             symbol, price, pct_change_15m, vol_pct, vol_ratio, cond1, cond2, cond3)

    triggered = cond1 and cond2 and cond3
    reason = ""
    if triggered:
        reason = f"BTC {pct_change_15m:+.1f}% in 15min, vol={vol_pct:.1f}%, vol_ratio={vol_ratio:.2f}x"
        _btc_scan_triggered = True
        _btc_last_trigger_reason = reason
        log.info("[BTC_WATCH] *** MOMENTUM SIGNAL: %s ***", reason)

    return {
        "price": price,
        "pct_change_15m": round(pct_change_15m, 3),
        "vol_pct": round(vol_pct, 2),
        "vol_ratio": round(vol_ratio, 3),
        "triggered": triggered,
        "reason": reason,
    }


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
    candidates_by_close: dict = {}   # close_time_key → list of candidate signals
    longshot_candidates: list = []   # entry_price < 0.15

    # Fetch Fear & Greed Index once (cached 1h)
    fg = fetch_crypto_fear_greed()

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

    # MOMENTUM TRIGGER: BTC_WATCH sets flag OR apply three-condition gate
    global _prev_btc_spot, _prev_eth_spot, _btc_scan_triggered, _btc_last_trigger_reason
    btc_now = spot.get("BTC", {}).get("price", 0)
    eth_now = spot.get("ETH", {}).get("price", 0)

    # Check if scan was triggered by price watcher (already validated 3 conditions)
    # OR fall back to basic momentum check for regular :15/:45 scans
    if _btc_scan_triggered:
        log.info("[Crypto] BTC_WATCH triggered scan: %s", _btc_last_trigger_reason)
        _btc_scan_triggered = False  # consume the trigger
        # Already confirmed conditions — proceed
        btc_direction = 1 if btc_now >= _prev_btc_spot else -1
        eth_direction = 1 if eth_now >= _prev_eth_spot else -1
    else:
        # Regular scheduled scan: apply three-condition gate
        btc_moved = abs(btc_now - _prev_btc_spot) / max(_prev_btc_spot, 1) if _prev_btc_spot > 0 else 1.0
        eth_moved = abs(eth_now - _prev_eth_spot) / max(_prev_eth_spot, 1) if _prev_eth_spot > 0 else 1.0
        btc_direction = 1 if btc_now > _prev_btc_spot else -1
        eth_direction = 1 if eth_now > _prev_eth_spot else -1

        # Condition 1: minimum move
        if _prev_btc_spot > 0 and btc_moved < BTC_MOMENTUM_THRESHOLD and eth_moved < BTC_MOMENTUM_THRESHOLD:
            log.info("[Crypto] No momentum signal (BTC %+.3f%%, ETH %+.3f%%) — skipping", btc_moved*100, eth_moved*100)
            _prev_btc_spot = btc_now
            _prev_eth_spot = eth_now
            crypto_signals = new_crypto_signals
            return crypto_signals

        # Condition 2: vol confirmation
        _vol_data = get_btc_intraday_vol("BTC")
        _current_vol = _vol_data.get("current_vol_pct", 1.9)
        if _current_vol < 1.2 and _prev_btc_spot > 0:
            log.info("[Crypto] Vol too low (%.2f%% < 1.2%%) — skipping low-vol noise", _current_vol)
            _prev_btc_spot = btc_now
            _prev_eth_spot = eth_now
            crypto_signals = new_crypto_signals
            return crypto_signals

        log.info("[Crypto] Momentum confirmed: BTC %+.3f%% vol=%.2f%%", btc_moved*100, _current_vol)

    _prev_btc_spot = btc_now
    _prev_eth_spot = eth_now

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

            # HORIZON-SCALED BUFFER CHECK
            # Longer horizon needs more buffer. >2 days = blocked entirely.
            _ct_str = m.get("close_time", "")
            _buf_ok, _buf_reason, _buf_req = crypto_horizon_buffer_check(
                ticker, spot_price, threshold, _ct_str)
            if not _buf_ok:
                log.info("[Crypto] %s: %s", ticker, _buf_reason)
                continue

            # 30-MIN EXPIRY CHECK — same as should_execute() guardrail
            ct_str = m.get("close_time", "")
            if ct_str:
                try:
                    ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
                    mins_to_close = (ct - datetime.now(timezone.utc)).total_seconds() / 60
                    if mins_to_close < 30:
                        log.info(
                            "[Crypto] %s: %.0f min to close < 30 min cutoff — skipping signal",
                            ticker, mins_to_close
                        )
                        continue
                except Exception:
                    pass

            # DIRECTION ALIGNMENT: only take positions in the direction of the momentum
            # If BTC moved UP, prefer YES (above threshold). If DOWN, prefer NO (below threshold).
            asset_direction = btc_direction if matched_sym == "BTC" else eth_direction
            if asset_direction > 0 and side == "NO" and above:
                log.info("[Crypto] %s: skipping NO bet against upward momentum", ticker)
                continue
            elif asset_direction < 0 and side == "YES" and above:
                log.info("[Crypto] %s: skipping YES bet against downward momentum", ticker)
                continue

            close_time_key = m.get("close_time", "")[:13]  # e.g. "2026-05-03T22"
            entry_price = kalshi_mid if side == "YES" else (1.0 - kalshi_mid)
            opp_score, sent_aligned = score_crypto_signal(
                spot_prob=spot_prob,
                kalshi_mid=kalshi_mid,
                edge=edge,
                entry_price=entry_price,
                fear_greed_value=fg["value"],
                direction=side,
            )

            # Accumulate candidates keyed by close_time_key
            candidate = {
                "ticker":            ticker,
                "symbol":            matched_sym,
                "spot_price":        spot_price,
                "threshold":         threshold,
                "above":             above,
                "side":              side,
                "spot_prob":         round(spot_prob, 3),
                "kalshi_mid":        round(kalshi_mid, 3),
                "edge":              round(edge, 3),
                "volatile":          spot[matched_sym]["volatile"],
                "change_24h":        spot[matched_sym]["change_24h"],
                "close_time_key":    close_time_key,
                "momentum_pct":      round(btc_moved * 100 if matched_sym == "BTC" else eth_moved * 100, 3),
                "confidence_boost":  0.25 if edge > 0.20 else 0.15,
                "opportunity_score": round(opp_score, 6),
                "fear_greed":        fg["value"],
                "sentiment_aligned": sent_aligned,
                "entry_price":       round(entry_price, 4),
                "buffer_pct":        round(buffer_pct * 100, 4),  # stored for tier-2 filter
            }
            candidates_by_close.setdefault(close_time_key, []).append(candidate)

            # Longshot pool — collected here, selected after loop
            if entry_price < 0.15:
                longshot_candidates.append(candidate)

    # ── TIER 1: SINGLE BEST PRIMARY SIGNAL PER CLOSE TIME ───────────────────
    # Primary = entry_price in [0.20, 0.50] — solid edge, ~1-2% buffer from spot
    for close_time_key, candidates in candidates_by_close.items():
        primaries = [c for c in candidates if 0.20 <= c["entry_price"] <= 0.50]
        if not primaries:
            log.info("[Crypto] %s close: no primary-tier candidates (20-50c) — skipping", close_time_key)
            continue
        best = max(primaries, key=lambda c: c["opportunity_score"])
        best["tier"] = "PRIMARY"
        new_crypto_signals[best["ticker"]] = best
        log.info(
            "[Crypto] Best signal for %s close: %s score=%.4f edge=%+.2f direction=%s entry=%.2f",
            close_time_key, best["ticker"], best["opportunity_score"],
            best["edge"], best["side"], best["entry_price"]
        )
        rejected = len(primaries) - 1
        skipped_tier = len(candidates) - len(primaries)
        if rejected > 0:
            log.info(
                "[Crypto] Rejected %d lower-scoring primary candidate(s) for this close time",
                rejected
            )
        if skipped_tier > 0:
            log.info(
                "[Crypto] Skipped %d out-of-tier candidate(s) (<20c or >50c) for this close time",
                skipped_tier
            )

    # ── TIER 2: SINGLE GLOBAL LONGSHOT ───────────────────────────────────────
    # Longshot = entry_price < 0.15, edge >= 0.12, buffer >= 1.0%
    # One per scan session total — pick the single highest-scoring qualifying candidate
    LONGSHOT_MIN_EDGE   = 0.12
    LONGSHOT_MIN_BUFFER = 1.0  # percent

    qualified_longshots = [
        c for c in longshot_candidates
        if c["edge"] >= LONGSHOT_MIN_EDGE
        and c.get("buffer_pct", 0.0) >= LONGSHOT_MIN_BUFFER
    ]

    if qualified_longshots:
        best_ls = max(qualified_longshots, key=lambda c: c["opportunity_score"])
        best_ls["tier"] = "LONGSHOT"
        # Don't double-add if it also won a primary slot (unlikely given <15c filter)
        if best_ls["ticker"] not in new_crypto_signals:
            new_crypto_signals[best_ls["ticker"]] = best_ls
            log.info(
                "[Crypto] LONGSHOT selected: %s score=%.4f edge=%+.2f entry=%.2f buffer=%.2f%%",
                best_ls["ticker"], best_ls["opportunity_score"],
                best_ls["edge"], best_ls["entry_price"], best_ls.get("buffer_pct", 0.0)
            )
        rejected_ls = len(qualified_longshots) - 1
        if rejected_ls > 0:
            log.info("[Crypto] Rejected %d lower-scoring longshot candidate(s)", rejected_ls)
        unqualified = len(longshot_candidates) - len(qualified_longshots)
        if unqualified > 0:
            log.info(
                "[Crypto] %d longshot candidate(s) failed edge/buffer filter "
                "(need edge>=%.2f, buffer>=%.1f%%)",
                unqualified, LONGSHOT_MIN_EDGE, LONGSHOT_MIN_BUFFER
            )
    else:
        if longshot_candidates:
            log.info(
                "[Crypto] %d longshot candidate(s) found but none passed filter "
                "(edge>=%.2f + buffer>=%.1f%%)",
                len(longshot_candidates), LONGSHOT_MIN_EDGE, LONGSHOT_MIN_BUFFER
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

# STALE ORDER CLEANUP

def cancel_stale_orders(dry_run: bool = False):
    """Cancel stale limit orders that have been resting for more than ORDER_MAX_AGE_HOURS."""
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

# STINK BID STRATEGY — for Brad (importable, NOT used by Economics)

# GDP RESEARCH — Q1 2026 ADVANCE ESTIMATE (April 30, 2026)

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

def fetch_fed_rate_data() -> dict:
    """
    Fetch current Fed funds target rate and SOFR forward rate from FRED.
    Used as inputs for FOMC probability model.
    Returns dict: current_upper, current_lower, sofr_90d, implied_forward_rate
    """
    try:
        upper = fetch_fred_series("DFEDTARU")   # Upper bound of target range
        lower = fetch_fred_series("DFEDTARL")   # Lower bound
        sofr  = fetch_fred_series("SOFR90DAYAVG")  # 90-day SOFR avg (forward-looking)
        dff   = fetch_fred_series("DFF")         # Effective Fed funds rate (spot)
        result = {
            "upper":    upper or 3.75,
            "lower":    lower or 3.50,
            "midpoint": ((upper or 3.75) + (lower or 3.50)) / 2,
            "sofr_90d": sofr,
            "dff":      dff,
        }
        log.info("[FOMC] Rate data: upper=%.2f lower=%.2f SOFR_90d=%s DFF=%s",
                 result["upper"], result["lower"], sofr, dff)
        return result
    except Exception as e:
        log.warning("[FOMC] fetch_fed_rate_data failed: %s", e)
        return {"upper": 3.75, "lower": 3.50, "midpoint": 3.625, "sofr_90d": None, "dff": None}

# Cache fed rate data — refresh every 4 hours
_fed_rate_cache: dict = {}
_fed_rate_cache_ts: float = 0.0

def get_fed_rate_data() -> dict:
    global _fed_rate_cache, _fed_rate_cache_ts
    now = time.time()
    if _fed_rate_cache and (now - _fed_rate_cache_ts) < 14400:
        return _fed_rate_cache
    result = fetch_fed_rate_data()
    if result:
        _fed_rate_cache = result
        _fed_rate_cache_ts = now
    return _fed_rate_cache

def calculate_fomc_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    Edge calculation for KXFEDDECISION markets.

    Ticker format: KXFEDDECISION-{YY}{MON}-{ACTION}
    Actions:
      C25  = cut 25bps
      C26+ = cut >25bps
      H0   = hold (0bps change)
      H25  = hike 25bps
      H26+ = hike >25bps

    Model:
      Uses SOFR 90-day avg vs current Fed funds target to infer market expectation.
      SOFR_90d below current rate midpoint implies cuts are priced in.
      Gap between SOFR and current rate scales cut/hold/hike probabilities.

    Fallback: if SOFR unavailable, use DFF spread vs target.

    Returns: (model_prob, edge, direction, source)
    """
    ticker_upper = ticker.upper()

    # Parse action from ticker
    action = None
    if ticker_upper.endswith('-C25'):   action = 'cut25'
    elif ticker_upper.endswith('-C26'): action = 'cutmore'
    elif '-C25' in ticker_upper:       action = 'cut25'
    elif '-H0' in ticker_upper:        action = 'hold'
    elif '-H25' in ticker_upper:       action = 'hike25'
    elif '-H26' in ticker_upper:       action = 'hikemore'
    elif 'HOLD' in ticker_upper or 'UNCHANGED' in ticker_upper: action = 'hold'
    elif 'EMERGENCY' in ticker_upper:  action = 'emergency'
    elif 'KXFEDMEET' in ticker_upper:  action = 'emergency'  # KXFEDMEET = emergency meeting markets

    if action is None:
        log.debug("[FOMC] Cannot parse action from ticker: %s", ticker)
        return None, 0.0, "NO", "parse_error"

    rates = get_fed_rate_data()
    midpoint   = rates["midpoint"]    # e.g. 3.625%
    sofr_90d   = rates["sofr_90d"]    # e.g. 3.67% (from FRED)
    dff        = rates["dff"]         # effective daily rate

    # Forward rate signal: SOFR_90d vs midpoint
    # SOFR_90d > midpoint -> market expects no cut (rates staying up)
    # SOFR_90d < midpoint -> cuts priced in
    if sofr_90d is not None:
        rate_signal = sofr_90d - midpoint  # positive = market says rates staying/rising
        forward_source = "SOFR_90d=%.2f vs target=%.3f" % (sofr_90d, midpoint)
    elif dff is not None:
        rate_signal = dff - midpoint
        forward_source = "DFF=%.2f vs target=%.3f" % (dff, midpoint)
    else:
        rate_signal = 0.10  # conservative fallback: slight hold bias
        forward_source = "fallback"

    # Convert rate signal to probabilities
    # signal > +0.10: strong hold/hike signal
    # signal near 0: uncertain
    # signal < -0.10: cut expected
    cut_prob  = max(0.02, min(0.95, 0.50 - rate_signal * 3.0))
    hold_prob = max(0.02, min(0.95, 0.50 + rate_signal * 2.0))
    hike_prob = max(0.01, min(0.95, rate_signal * 2.0))

    # Normalize
    total = cut_prob + hold_prob + hike_prob
    cut_prob  /= total
    hold_prob /= total
    hike_prob /= total

    # Select model_prob for this market's action
    if action == 'cut25':
        model_prob = cut_prob * 0.8   # 25bps specifically (vs >25bps)
    elif action == 'cutmore':
        model_prob = cut_prob * 0.2
    elif action == 'hold':
        model_prob = hold_prob
    elif action == 'hike25':
        model_prob = hike_prob * 0.7
    elif action == 'hikemore':
        model_prob = hike_prob * 0.3
    elif action == 'emergency':
        # Emergency meeting: very low probability baseline
        model_prob = 0.05
    else:
        return None, 0.0, "NO", "unknown_action"

    model_prob = round(float(model_prob), 3)
    edge = model_prob - kalshi_mid
    direction = "YES" if edge > 0 else "NO"

    log.info("[FOMC] %s: action=%s rate_signal=%+.3f cut=%.0f%% hold=%.0f%% hike=%.0f%% "
             "-> model=%.0f%% kalshi=%.0f%% edge=%+.2f %s",
             ticker, action, rate_signal, cut_prob*100, hold_prob*100, hike_prob*100,
             model_prob*100, kalshi_mid*100, edge, direction)

    source = "SOFR_model(%s)" % forward_source
    return model_prob, edge, direction, source

def fetch_fedwatch_hold_prob() -> float:
    """Legacy wrapper — returns hold probability from new FOMC model."""
    rates = get_fed_rate_data()
    midpoint = rates.get("midpoint", 3.625)
    sofr = rates.get("sofr_90d")
    if sofr is not None:
        rate_signal = sofr - midpoint
        hold_prob = max(0.05, min(0.95, 0.50 + rate_signal * 2.0))
    else:
        hold_prob = 0.92   # current default: Fed on hold
    log.info("[FOMC] hold_prob=%.2f (legacy fedwatch_hold_prob)", hold_prob)
    return hold_prob


# =============================================================================
# FOREX MODEL — EUR/USD edge calculator
# =============================================================================

def fetch_eurusd_spot() -> float:
    """Fetch EUR/USD spot rate from Yahoo Finance (primary) or FRED (fallback)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "2d"},
            timeout=8
        )
        if r.ok:
            price = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
            log.info("[Forex] EUR/USD Yahoo: %.5f", price)
            return price
    except Exception as e:
        log.debug("[Forex] Yahoo failed: %s", e)
    # FRED fallback: DEXUSEU = USD per EUR (same as EUR/USD)
    val = fetch_fred_series("DEXUSEU")
    if val:
        log.info("[Forex] EUR/USD FRED: %.5f", val)
        return val
    return 0.0

# Cache EUR/USD — 15 minute TTL
_eurusd_cache: float = 0.0
_eurusd_cache_ts: float = 0.0

def get_eurusd_spot() -> float:
    global _eurusd_cache, _eurusd_cache_ts
    now = time.time()
    if _eurusd_cache and (now - _eurusd_cache_ts) < 900:
        return _eurusd_cache
    val = fetch_eurusd_spot()
    if val:
        _eurusd_cache = val
        _eurusd_cache_ts = now
    return _eurusd_cache

def calculate_eurusd_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    Edge calculation for KXEURUSD markets.

    Ticker formats:
      KXEURUSD-{date}-T{threshold}  = above/below markets
      KXEURUSD-{date}-B{bracket}    = bracket markets (1.173 = 1.172-1.174 range)

    Model: spot rate with normal distribution std_dev = 0.005 (50 pips) for 1-day horizon.
    Returns (model_prob, edge, direction, source)
    """
    import re as _re
    ticker_upper = ticker.upper()
    spot = get_eurusd_spot()
    if not spot:
        return None, 0.0, "NO", "unavailable"

    std_dev = 0.005  # ~50 pips daily std dev for EUR/USD

    # Parse ticker type
    last_part = ticker_upper.split("-")[-1]  # e.g. T1.18799, B1.177

    if last_part.startswith("T"):
        # Above/below threshold market
        try:
            threshold = float(last_part[1:])
        except ValueError:
            return None, 0.0, "NO", "parse_error"

        # Determine direction from title
        title_lower = (ticker.lower())
        above = "above" in ticker.lower() or not "below" in ticker.lower()

        from scipy.stats import norm as _norm
        z = (threshold - spot) / std_dev
        prob_above = float(1.0 - _norm.cdf(z))

        if above:
            model_prob = prob_above
            edge = model_prob - kalshi_mid
            direction = "YES" if edge > 0 else "NO"
        else:
            model_prob = 1.0 - prob_above
            edge = model_prob - kalshi_mid
            direction = "YES" if edge > 0 else "NO"

        log.info("[Forex] %s: spot=%.5f thr=%.5f above=%s model=%.2f kalshi=%.2f edge=%+.2f",
                 ticker, spot, threshold, above, model_prob, kalshi_mid, edge)
        return model_prob, edge, direction, "EURUSD_spot=%.5f" % spot

    elif last_part.startswith("B"):
        # Bracket market — e.g. B1.177 = 1.176-1.178 range (200 pip bracket)
        try:
            bracket_floor = float(last_part[1:])
        except ValueError:
            return None, 0.0, "NO", "parse_error"
        # Brackets are typically 0.002 wide on Kalshi EUR/USD
        bracket_width = 0.002
        bracket_ceil  = bracket_floor + bracket_width

        from scipy.stats import norm as _norm
        # P(bracket_floor < spot_at_open < bracket_ceil)
        prob = float(_norm.cdf((bracket_ceil - spot) / std_dev) -
                     _norm.cdf((bracket_floor - spot) / std_dev))

        edge = prob - kalshi_mid
        direction = "YES" if edge > 0 else "NO"

        log.info("[Forex] %s bracket [%.5f-%.5f]: spot=%.5f model=%.2f kalshi=%.2f edge=%+.2f",
                 ticker, bracket_floor, bracket_ceil, spot, prob, kalshi_mid, edge)
        return prob, edge, direction, "EURUSD_spot=%.5f" % spot

    return None, 0.0, "NO", "unknown_format"

def calculate_economic_edge(ticker: str, kalshi_mid: float) -> tuple:
    """
    For a given economic market ticker, fetch the relevant model estimate
    and calculate edge vs Kalshi price.

    Returns: (model_prob: float, edge: float, direction: str, source: str)
    """
    ticker_upper = ticker.upper()

    # GDP markets: any KXGDP ticker — generalized (not just APR30)
    if 'KXGDP' in ticker_upper:
        return calculate_gdp_edge(ticker, kalshi_mid)

    # NFP/Payrolls markets
    if 'KXPAYROLLS' in ticker_upper:
        return calculate_nfp_edge(ticker, kalshi_mid)

    # FOMC markets — full decision model (cut/hold/hike)
    if 'KXFEDMEET' in ticker_upper or 'KXFEDDECISION' in ticker_upper:
        return calculate_fomc_edge(ticker, kalshi_mid)

    return None, 0.0, "NO", "no_model"

def run_gdp_research(open_positions: dict = None) -> dict:
    """
    Build and log the GDP thesis for the Q1 2026 advance estimate (April 30).
    Call once at startup if any GDP market is in open positions.
    Returns GDP thesis with live GDPNow.
    """
    thesis = {
        "release_date": "2026-04-30",
        "our_estimate": -1.5,
        "verdict": "HOLD NO positions — consensus for weak Q1 GDP",
        "risk": "Q1 data excludes tariff impact or revised upward",
    }

    # Try to get live GDPNow estimate
    gdpnow = fetch_gdpnow_realtime()
    if gdpnow is not None:
        thesis["gdpnow_live"] = gdpnow
        log.info(f"[GDP] Atlanta Fed GDPNow live estimate: {gdpnow:+.1f}%")
    else:
        thesis["gdpnow_live"] = None
        log.info("[GDP] GDPNow estimate unavailable — using cached consensus")

    log.info("=" * 60)
    log.info("GDP THESIS — Current GDPNow")
    log.info("=" * 60)
    log.info(f"  Release date:     {thesis['release_date']}")
    log.info(f"  Our estimate:     {thesis['our_estimate']:+.1f}%")
    if gdpnow is not None:
        log.info(f"  GDPNow (live):    {gdpnow:+.1f}%")
    log.info(f"  Verdict:          {thesis['verdict']}")
    log.info(f"  Risk:             {thesis['risk']}")
    log.info("=" * 60)

    return thesis

# MAIN

def main():
    parser = argparse.ArgumentParser(description="Economics v3 — Two-tier Kalshi scanner")
    parser.add_argument("--dry-run",   action="store_true", help="Stdout only, no Discord posts")
    parser.add_argument("--scan-once", action="store_true", help="Single discovery scan, then exit")
    args = parser.parse_args()

    dry_run   = args.dry_run
    scan_once = args.scan_once

    modes = []
    if dry_run:   modes.append("DRY-RUN")
    if scan_once: modes.append("SCAN-ONCE")
    log.info(f"Economics v3 starting [{', '.join(modes) if modes else 'CONTINUOUS'}]")

    if scan_once:
        # GDP research — always run at startup; checks for GDP markets in open positions
        run_gdp_research()

        # Stop-loss check — always run at startup
        log.info("[STOPLOSS] Running startup stop-loss check...")
        check_stop_losses(dry_run=dry_run)

        # Log weather model update window status
        in_window = False  # weather model window check removed
        log.info(f"[Weather] Model update window active: {in_window}")

        run_crypto_monitor(dry_run=dry_run)

        # One full discovery scan
        all_markets, plays, weather_signals, arb_opps = run_discovery_scan(dry_run=dry_run)

        if dry_run:
            print("\n" + "=" * 60)
            print("SCAN-ONCE SUMMARY")
            print("=" * 60)
            print(f"Total markets scanned: {len(all_markets)}")
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

        if now - last_crypto_ts >= CRYPTO_MONITOR_INTERVAL_SEC * interval_multiplier:
            run_crypto_monitor(dry_run=dry_run)
            last_crypto_ts = time.time()

        # Daily heartbeat — posts once per day so you know Economics is alive
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


def check_pre_release_briefs(dry_run: bool = False):
    """
    Post pre-release briefs to #donnie-results for economic releases within 24 hours.
    Called once per run_scan() cycle.
    """
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)

    RELEASE_SCHEDULE = [
        ("CPI April (May 13)",     "KXCPI",        datetime(2026,  5, 13, 12, 30, tzinfo=timezone.utc)),
        ("June FOMC + Dot Plot",   "KXFEDDECISION", datetime(2026, 6, 17, 18,  0, tzinfo=timezone.utc)),
        ("NFP May (June 5)",       "KXPAYROLLS",    datetime(2026,  6,  5, 12, 30, tzinfo=timezone.utc)),
        ("NFP June (July 2)",      "KXPAYROLLS",    datetime(2026,  7,  2, 12, 30, tzinfo=timezone.utc)),
    ]

    for name, prefix, release_dt in RELEASE_SCHEDULE:
        hours_until = (release_dt - now_utc).total_seconds() / 3600
        if not (0 < hours_until <= 24):
            continue
        log.info("[PRE-RELEASE] %s in %.1fh -- building brief", name, hours_until)
        lines = [
            "**PRE-RELEASE: %s**" % name,
            "Release in %.1f hours (%s UTC)" % (hours_until, release_dt.strftime("%b %d %H:%M")),
            "",
        ]
        if prefix == "KXPAYROLLS":
            nfp = get_nfp_estimate()
            if nfp:
                lines.append("NFP model: %.0fK expected (adp=%s, sigma=%.0fK)" % (
                    nfp["estimate"],
                    "%.0fK" % nfp["adp_value"] if nfp.get("adp_blended") else "N/A",
                    nfp["std_dev"]/1000))
        elif prefix in ("KXFEDDECISION", "KXDOTPLOT"):
            rates = get_fed_rate_data()
            lines.append("Fed target: %.2f-%.2f%% | SOFR_90d: %.2f%%" % (
                rates["lower"], rates["upper"], rates.get("sofr_90d") or 0))
        elif prefix == "KXCPI":
            cpi = fetch_cleveland_cpi()
            if cpi:
                lines.append("Cleveland CPI nowcast: %.2f%%" % (cpi.get("nowcast") or cpi.get("headline_cpi") or 0))

        brief = "\n".join(lines)
        log.info("[PRE-RELEASE] Brief:\n%s", brief)
        if not dry_run:
            try:
                _post_discord(DISCORD_CH_RESULTS, "📅 " + brief)
            except Exception as e:
                log.warning("[PRE-RELEASE] Discord post failed: %s", e)



# =============================================================================
# TRADE RESOLUTION MONITOR
# =============================================================================

def resolve_pending_trades(dry_run: bool = False):
    """
    Check all PENDING trades in eval_store against Kalshi API.
    Update outcome, post Discord result. Called each scan cycle.
    """
    import json as _json
    from pathlib import Path as _Path

    eval_path = _Path("/home/cody/stratton/data/eval_store.json")
    if not eval_path.exists():
        return

    try:
        data = _json.loads(eval_path.read_text())
    except Exception as e:
        log.warning("[Resolver] Failed to load eval_store: %s", e)
        return

    pending = [t for t in data if t.get("outcome") in ("PENDING", None, "")]
    if not pending:
        return

    log.info("[Resolver] Checking %d pending trades", len(pending))
    updated = 0

    for trade in pending:
        ticker = trade.get("market") or trade.get("trade_id", "")
        if not ticker or ticker.startswith("TEST"):
            continue
        try:
            market_data = kalshi_get("/markets/%s" % ticker)
            m = market_data.get("market", {})
            status = m.get("status", "")
            result = m.get("result", "")

            if status not in ("finalized", "determined"):
                continue

            direction = (trade.get("direction") or trade.get("side") or "YES").upper()
            won = (result.lower() == "yes" and direction == "YES") or                   (result.lower() == "no"  and direction == "NO")

            entry_price = trade.get("entry_price_dollars", 0.5)
            if entry_price and 0 < entry_price < 1:
                pnl_pct = round((1.0 - entry_price) / entry_price * 100, 1) if won else -100.0
            else:
                pnl_pct = 100.0 if won else -100.0

            # Try to get actual realized PnL
            pos_data = kalshi_get("/portfolio/positions", params={"ticker": ticker})
            actual_pnl = None
            for p in pos_data.get("market_positions", []):
                if p.get("ticker") == ticker:
                    actual_pnl = float(p.get("realized_pnl_dollars", 0) or 0)

            trade["outcome"] = "WIN" if won else "LOSS"
            trade["resolved_date"] = m.get("close_time", "")[:10] or "2026-05-02"
            trade["result_direction"] = result
            trade["pnl_pct"] = pnl_pct
            if actual_pnl is not None:
                trade["realized_pnl_dollars"] = actual_pnl

            updated += 1
            log.info("[Resolver] %s: %s -> %s (pnl=%.1f%%)", ticker, direction, trade["outcome"], pnl_pct)

            # Post Discord
            check = "\u2705" if won else "\u274c"
            pnl_str = ("($%+.2f)" % actual_pnl) if actual_pnl else ""
            msg = "%s **RESOLVED: %s** | %s | Result: %s | %s %s%+.1f%%" % (
                check, ticker, direction, result.upper(),
                "WIN" if won else "LOSS", pnl_str, pnl_pct)
            if not dry_run:
                try:
                    _post_discord(DISCORD_CH_RESULTS, msg)
                except Exception:
                    pass

        except Exception as e:
            log.debug("[Resolver] %s: %s", ticker, e)

    if updated > 0 and not dry_run:
        eval_path.write_text(_json.dumps(data, indent=2))
        log.info("[Resolver] Updated %d outcomes in eval_store", updated)


def run_scan(post=None, **kwargs):
    """Entry point for firm.py -- runs one discovery scan."""
    # FIX: Rebuild dynamic thesis direction locks from live model each cycle
    global _dynamic_thesis_locks
    _dynamic_thesis_locks = build_thesis_locks()
    log.info("[THESIS] Dynamic locks built: %d active", len(_dynamic_thesis_locks))

    # Check for upcoming releases and post pre-release briefs
    _dry = (post is False)
    check_pre_release_briefs(dry_run=_dry)

    # Resolve pending trades and update eval_store
    resolve_pending_trades(dry_run=_dry)

    run_discovery_scan(dry_run=False)
    _save_donnie_state()
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from shared_context import write_agent_status
        write_agent_status("economics", {'status': 'ran', 'markets_scanned': 0})
    except Exception:
        pass

if __name__ == "__main__":
    main()
