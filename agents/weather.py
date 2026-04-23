#!/usr/bin/env python3
"""
WEATHER BOT v4 — Kalshi Daily High Temperature Market Scanner
=============================================================
Stratton Oakmont prediction market intelligence — weather edition.

v4 architecture:
  - Correct series: KXHIGHNY, KXHIGHLAX, etc. (daily HIGH temp markets)
  - Ticker format: {SERIES}-{YYMONDD}-{T|B}{value}
    - T = threshold market (<N or >N°F), B = between/range market
    - strike_type: "less", "between", "greater"
  - Tomorrow.io DAILY forecast (temperatureMax)
  - Range-based edge detection: P(temp in [low, high]) vs Kalshi mid
  - Paper mode (dry_run=True by default)
  - Posts to #mark-signals via Mark Hanna bot

Ticker examples:
  KXHIGHNY-26APR17-T77   → NYC high <77°F on Apr 17 (strike_type=less)
  KXHIGHNY-26APR17-B77.5 → NYC high 77-78°F on Apr 17 (strike_type=between)
  KXHIGHNY-26APR17-T84   → NYC high >84°F on Apr 17 (strike_type=greater)

Usage:
    python3 weather.py                   # continuous mode
    python3 weather.py --scan-once       # single scan + exit
    python3 weather.py --dry-run         # paper: no real trades
    python3 weather.py --dry-run --scan-once

Requirements:
    pip install requests cryptography
"""

import os
import sys
import math
import time
import uuid
import json
import base64
import logging
import argparse
import re
import statistics
from datetime import datetime, timezone, timedelta
ET = timezone(timedelta(hours=-4))  # EDT (UTC-4); update to -5 for EST in Nov
from typing import Optional

import requests

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# ─────────────────────────────────────────────────────────────────────────────
# PATH DETECTION — Atlas vs local
# ─────────────────────────────────────────────────────────────────────────────

# Paths — resolved from environment
PRIVATE_KEY_PATH  = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
BOT_TOKENS_ENV    = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
_BASE_DIR = os.environ.get("FIRM_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
LOG_PATH          = os.path.join(_LOG_DIR, "weather.log")
DATA_DIR          = os.path.join(_BASE_DIR, "data")

BLOCKS_FILE               = os.path.join(DATA_DIR, "weather_blocks.json")
WEATHER_PAPER_TRADES_FILE = os.path.join(DATA_DIR, "weather_paper_trades.json")
FORECAST_CACHE_FILE       = os.path.join(DATA_DIR, "weather_forecast_cache.json")
PAPER_DEDUP_FILE          = os.path.join(DATA_DIR, "weather_paper_dedup.json")
PAPER_EXPERIMENTS_FILE    = os.path.join(DATA_DIR, "weather_experiments.json")
WEATHER_ACCURACY_FILE     = os.path.join(DATA_DIR, "weather_accuracy.json")
BIAS_CACHE_FILE           = os.path.join(DATA_DIR, "weather_bias_cache.json")
BIAS_WINDOW_DAYS          = 7     # rolling window for bias calculation
PREFETCH_LOCK_FILE        = os.path.join(DATA_DIR, "weather_prefetch_state.json")
PREFETCH_WINDOWS_UTC      = [30, 390, 750, 1110]  # minutes from midnight: 00:30, 06:30, 12:30, 18:30
PREFETCH_WINDOW_MIN       = 20   # minutes before/after window to trigger prefetch
BIAS_MIN_SAMPLES          = 3     # minimum samples before applying correction

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

KALSHI_BASE      = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID = os.environ.get("KALSHI_KEY_ID", "")
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "")

# Discord — #mark-signals
WEATHER_CHANNEL = 1491861985162432634

# Kalshi daily high temperature series (KXHIGH* format)
WEATHER_SERIES = [
    'KXHIGHNY',    # New York
    'KXHIGHLAX',   # Los Angeles
    'KXHIGHCHI',   # Chicago
    'KXHIGHMIA',   # Miami
    'KXHIGHTDAL',  # Dallas
    'KXHIGHTHOU',  # Houston
    'KXHIGHTBOS',  # Boston
    'KXHIGHTATL',  # Atlanta
    'KXHIGHTPHX',  # Phoenix
    'KXHIGHTLV',   # Las Vegas
    'KXHIGHTSEA',  # Seattle
    'KXHIGHTMIN',  # Minneapolis
    'KXHIGHAUS',   # Austin
    'KXHIGHTDC',   # Washington DC
    'KXHIGHTNOLA', # New Orleans
    'KXHIGHTOKC',  # Oklahoma City
    'KXHIGHTSFO',  # San Francisco
    'KXHIGHTSATX', # San Antonio
    'KXHIGHPHIL',  # Philadelphia
]

# City info keyed by series prefix
SERIES_CITY_MAP = {
    'KXHIGHNY':    {'name': 'New York',       'lat': 40.7128,  'lon': -74.0060},
    'KXHIGHLAX':   {'name': 'Los Angeles',    'lat': 34.0522,  'lon': -118.2437},
    'KXHIGHCHI':   {'name': 'Chicago',        'lat': 41.8781,  'lon': -87.6298},
    'KXHIGHMIA':   {'name': 'Miami',          'lat': 25.7617,  'lon': -80.1918},
    'KXHIGHTDAL':  {'name': 'Dallas',         'lat': 32.7767,  'lon': -96.7970},
    'KXHIGHTHOU':  {'name': 'Houston',        'lat': 29.7604,  'lon': -95.3698},
    'KXHIGHTBOS':  {'name': 'Boston',         'lat': 42.3601,  'lon': -71.0589},
    'KXHIGHTATL':  {'name': 'Atlanta',        'lat': 33.7490,  'lon': -84.3880},
    'KXHIGHTPHX':  {'name': 'Phoenix',        'lat': 33.4484,  'lon': -112.0740},
    'KXHIGHTLV':   {'name': 'Las Vegas',      'lat': 36.1699,  'lon': -115.1398},
    'KXHIGHTSEA':  {'name': 'Seattle',        'lat': 47.6062,  'lon': -122.3321},
    'KXHIGHTMIN':  {'name': 'Minneapolis',    'lat': 44.9778,  'lon': -93.2650},
    'KXHIGHAUS':   {'name': 'Austin',         'lat': 30.2672,  'lon': -97.7431},
    'KXHIGHTDC':   {'name': 'Washington DC',  'lat': 38.9072,  'lon': -77.0369},
    'KXHIGHTNOLA': {'name': 'New Orleans',    'lat': 29.9511,  'lon': -90.0715},
    'KXHIGHTOKC':  {'name': 'Oklahoma City',  'lat': 35.4676,  'lon': -97.5164},
    'KXHIGHTSFO':  {'name': 'San Francisco',  'lat': 37.7749,  'lon': -122.4194},
    'KXHIGHTSATX': {'name': 'San Antonio',    'lat': 29.4241,  'lon': -98.4936},
    'KXHIGHPHIL':  {'name': 'Philadelphia',   'lat': 39.9526,  'lon': -75.1652},
}

# Edge thresholds
WEATHER_EDGE_THRESHOLD    = 0.15   # 15¢ minimum edge
MIN_FORECAST_MARGIN_MULT  = 1.5    # forecast must be >= this * uncertainty from threshold to signal
MAX_ACTIVE_WEATHER_ORDERS = 3      # max simultaneous LIVE positions
MAX_PAPER_SIGNALS         = 10     # max paper signals per scan cycle (prevent spam)
MAX_POSITION_PCT          = 0.03   # 3% of balance per trade

# Market filters
MIN_VOLUME       = 0      # volume check handled inline (API returns None for new markets)

# Cache TTL
TOMORROW_CACHE_TTL = 14400  # 4 hours — conserve API quota (500/day limit each)

# Polling
POLL_INTERVAL_SEC = 300   # 5 minutes

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

log = logging.getLogger("weather-bot")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh  = logging.FileHandler(LOG_PATH)
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)

# ─────────────────────────────────────────────────────────────────────────────
# AUTH — RSA-PSS
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
        log.error("Failed to load private key: %s", e)
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
# PAPER TRADE DEDUPLICATION — persistent file-based, survives restarts
# ─────────────────────────────────────────────────────────────────────────────

def _load_dedup() -> dict:
    """Load dedup file. Purges date keys older than 2 days; preserves non-date keys (e.g. daily_summary)."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%d')
    try:
        if os.path.exists(PAPER_DEDUP_FILE):
            with open(PAPER_DEDUP_FILE) as f:
                data = json.load(f)
            # Keep: non-date keys (like daily_summary) + dates within last 2 days
            return {k: v for k, v in data.items()
                    if not k.startswith('20') or k >= cutoff}
    except Exception:
        pass
    return {}

def _save_dedup(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PAPER_DEDUP_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log.warning("[Dedup] Save failed: %s", e)

def is_paper_logged(ticker: str) -> bool:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    data = _load_dedup()
    return ticker in data.get(today, [])

def mark_paper_logged(ticker: str):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    data = _load_dedup()
    if today not in data:
        data[today] = []
    if ticker not in data[today]:
        data[today].append(ticker)
    _save_dedup(data)

def reset_paper_log_if_new_day():
    """No-op — file-based dedup auto-purges stale dates on load."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# PARALLEL PAPER STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────
# Each strategy is a filter on (abs_edge, series, scan_time_utc).
# A signal qualifies for a strategy if it passes all its filters.
# All strategies share a single scan pass — no extra API calls.

_THIN_CITY_SERIES = {
    'KXHIGHTOKC', 'KXHIGHTNOLA', 'KXHIGHTMIN',
    'KXHIGHTSATX', 'KXHIGHTSEA', 'KXHIGHTATL',
}
_MODEL_WINDOW_MINUTES = [30, 390, 750, 1110]  # 00:30, 06:30, 12:30, 18:30 UTC (8:30PM, 2:30AM, 8:30AM, 2:30PM ET)


def _minutes_since_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    return now.hour * 60 + now.minute


def _near_model_update(window_min: int = 90) -> bool:
    """True if current UTC time is within window_min of a model update."""
    m = _minutes_since_midnight_utc()
    for w in _MODEL_WINDOW_MINUTES:
        if abs(m - w) <= window_min:
            return True
    return False


def classify_strategies(abs_edge: float, series: str) -> list:
    """Return list of strategy labels this signal qualifies for."""
    labels = []
    near_window = _near_model_update(90)

    # A — Conservative: 15c+, any city (matches live threshold)
    if abs_edge >= 0.15:
        labels.append('A_conservative_15c')

    # B — Aggressive: 10c+, any city
    if abs_edge >= 0.10:
        labels.append('B_aggressive_10c')

    # C — Thin city: 15c+, low-liquidity cities only
    if abs_edge >= 0.15 and series in _THIN_CITY_SERIES:
        labels.append('C_thin_city_15c')

    # D — High conviction: 25c+, any city
    if abs_edge >= 0.25:
        labels.append('D_high_conviction_25c')

    # E — Time-gated: 15c+, within 90 min of model update window
    if abs_edge >= 0.15 and near_window:
        labels.append('E_time_gated_15c')

    return labels


def log_experiment_signal(market: dict, parsed: dict, model_prob: float,
                           kalshi_mid: float, edge: float, direction: str,
                           strategies: list, balance: float):
    """Append signal to experiments file with strategy tags."""
    price     = kalshi_mid if direction == 'YES' else (1.0 - kalshi_mid)
    price     = max(price, 0.01)
    contracts = max(1, int(math.floor((balance * MAX_POSITION_PCT) / price)))
    cost      = round(contracts * price, 2)

    entry = {
        'ticker':        market.get('ticker', ''),
        'series':        parsed['series'],
        'city_name':     parsed['city_name'],
        'date':          parsed['date'],
        'strike_type':   parsed['strike_type'],
        'low':           parsed.get('low'),
        'high':          parsed.get('high'),
        'threshold':     parsed.get('threshold'),
        'direction':     direction,
        'forecast_high': market.get('_forecast_high'),
        'kalshi_prob':   round(kalshi_mid, 4),
        'model_prob':    round(model_prob, 4),
        'edge':          round(abs(edge), 4),
        'contracts':     contracts,
        'cost':          cost,
        'strategies':    strategies,
        'timestamp':     datetime.now(timezone.utc).isoformat(),
        'status':        'OPEN',
        'result':        None,
    }

    try:
        if os.path.exists(PAPER_EXPERIMENTS_FILE):
            with open(PAPER_EXPERIMENTS_FILE) as f:
                data = json.load(f)
        else:
            data = []
        data.append(entry)
        with open(PAPER_EXPERIMENTS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning("[Experiments] Save failed: %s", e)

    log.info("[Experiments] %s → strategies=%s edge=%.0f%%",
             entry['ticker'], strategies, abs(edge) * 100)


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
        log.warning("Kalshi GET %s → %s: %s", path, r.status_code, r.text[:150])
        return {}
    except Exception as e:
        log.error("Kalshi GET %s error: %s", path, e)
        return {}


def kalshi_post(path: str, body: dict) -> dict:
    url     = KALSHI_BASE + path
    headers = get_auth_headers("POST", "/trade-api/v2" + path)
    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        if r.status_code in (200, 201):
            return r.json()
        log.warning("Kalshi POST %s → %s: %s", path, r.status_code, r.text[:200])
        return {"error": r.text[:200], "status_code": r.status_code}
    except Exception as e:
        log.error("Kalshi POST %s error: %s", path, e)
        return {"error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD
# ─────────────────────────────────────────────────────────────────────────────

def _load_mark_token() -> str:
    """Load Mark Hanna's Discord token — weather posts go through him."""
    token = os.environ.get("MARK_HANNA_TOKEN", "")
    if token:
        return token
    try:
        with open(BOT_TOKENS_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MARK_HANNA_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        log.error("Could not load MARK_HANNA_TOKEN: %s", e)
    return ""


WEATHER_BOT_TOKEN = _load_mark_token()


def post_discord(message: str, dry_run: bool = False) -> bool:
    """Post message to #mark-signals via Mark Hanna bot."""
    if dry_run:
        print("\n" + "─" * 60)
        print("[DRY RUN — Discord → channel %d]" % WEATHER_CHANNEL)
        print(message[:2000])
        print("─" * 60)
        return True

    if not WEATHER_BOT_TOKEN:
        log.error("MARK_HANNA_TOKEN not set — cannot post to Discord")
        return False

    url     = "https://discord.com/api/v10/channels/%d/messages" % WEATHER_CHANNEL
    headers = {"Authorization": "Bot %s" % WEATHER_BOT_TOKEN, "Content-Type": "application/json"}

    chunks = [message[i:i+1990] for i in range(0, len(message), 1990)]
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                log.error("Discord post failed: %s %s", r.status_code, r.text[:200])
                return False
            time.sleep(0.5)
        except Exception as e:
            log.error("Discord request error: %s", e)
            return False
    return True

# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_balance() -> float:
    data = kalshi_get("/portfolio/balance")
    balance_cents   = data.get("balance", 0.0)
    balance_dollars = float(balance_cents) / 100.0
    log.info("[Portfolio] Balance: $%.2f", balance_dollars)
    return balance_dollars


def get_open_positions() -> dict:
    """Returns {ticker: exposure_dollars} for all open positions + resting orders."""
    data      = kalshi_get("/portfolio/positions")
    positions = data.get("market_positions", [])
    result    = {}
    for pos in positions:
        ticker    = pos.get("ticker", "")
        exposure  = float(pos.get("market_exposure_dollars", 0.0) or 0.0)
        pos_count = pos.get("position", 0) or 0
        if ticker and (exposure > 0 or pos_count != 0):
            result[ticker] = exposure

    # Also include resting orders
    orders_data = kalshi_get("/portfolio/orders", params={"status": "resting"})
    for o in orders_data.get("orders", []):
        t = o.get("ticker", "")
        if t and t not in result:
            result[t] = 0.0

    log.info("[Portfolio] Open positions: %d", len(result))
    return result


def get_open_weather_positions() -> dict:
    """Returns only WEATHER positions (KXHIGH* tickers)."""
    WEATHER_PREFIXES = ('KXHIGH', 'KXRAIN', 'KXFROST', 'KXPRECIP', 'KXSNOW')
    all_pos = get_open_positions()
    weather_pos = {t: e for t, e in all_pos.items()
                   if any(t.upper().startswith(p) for p in WEATHER_PREFIXES)}
    log.info("[Portfolio] Weather positions: %d", len(weather_pos))
    return weather_pos

# ─────────────────────────────────────────────────────────────────────────────
# TICKER PARSER — v4 KXHIGH* format
# ─────────────────────────────────────────────────────────────────────────────

def get_series_for_ticker(ticker: str) -> Optional[str]:
    """Return the series prefix matching a ticker, or None."""
    ticker_upper = ticker.upper()
    # Sort by length descending so longer prefixes match first
    for series in sorted(WEATHER_SERIES, key=len, reverse=True):
        if ticker_upper.startswith(series + "-"):
            return series
    return None


def parse_ticker(ticker: str, market_data: dict = None) -> Optional[dict]:
    """
    Parse a Kalshi daily high temp market ticker.

    Formats:
      {SERIES}-{YYMONDD}-T{threshold}   → below (<) or above (>) threshold
      {SERIES}-{YYMONDD}-B{midpoint}    → between [floor_strike, cap_strike]°F

    If market_data dict is provided (from events API), uses strike_type,
    floor_strike, cap_strike directly. Otherwise parses from ticker suffix.

    Returns:
      {
        'series':       str,        # e.g. 'KXHIGHNY'
        'city_name':    str,        # e.g. 'New York'
        'lat': float, 'lon': float,
        'date':         str,        # 'YYYY-MM-DD'
        'strike_type':  str,        # 'less' | 'between' | 'greater'
        'low':          float,      # lower bound (for 'between')
        'high':         float,      # upper bound (for 'between')
        'threshold':    float,      # for 'less'/'greater'
        'close_time':   str,        # ISO datetime string
      }
    or None if cannot parse.
    """
    ticker_upper = ticker.upper().strip()

    series = get_series_for_ticker(ticker)
    if not series:
        return None

    city = SERIES_CITY_MAP.get(series)
    if not city:
        return None

    # Extract date portion: {SERIES}-{YYMONDD}-...
    rest = ticker_upper[len(series) + 1:]   # e.g. "26APR17-B77.5"
    m = re.match(r'(\d{2})([A-Z]{3})(\d{2})-(.+)$', rest)
    if not m:
        log.debug("parse_ticker: no date match in '%s'", rest)
        return None

    year_2, month_str, day_str, suffix = m.group(1), m.group(2), m.group(3), m.group(4)

    try:
        dt       = datetime.strptime("20%s%s%s" % (year_2, month_str, day_str), '%Y%b%d')
        date_iso = dt.strftime('%Y-%m-%d')
    except Exception:
        log.debug("parse_ticker: date parse failed for %s", ticker)
        return None

    # Parse strike type and bounds from market_data if provided
    if market_data:
        strike_type  = market_data.get("strike_type", "")
        floor_strike = market_data.get("floor_strike")
        cap_strike   = market_data.get("cap_strike")
        close_time   = market_data.get("close_time", "")

        if strike_type == "between" and floor_strike is not None and cap_strike is not None:
            return {
                'series':      series,
                'city_name':   city['name'],
                'lat':         city['lat'],
                'lon':         city['lon'],
                'date':        date_iso,
                'strike_type': 'between',
                'low':         float(floor_strike),
                'high':        float(cap_strike),
                'threshold':   None,
                'close_time':  close_time,
            }
        elif strike_type == "less" and cap_strike is not None:
            return {
                'series':      series,
                'city_name':   city['name'],
                'lat':         city['lat'],
                'lon':         city['lon'],
                'date':        date_iso,
                'strike_type': 'less',
                'low':         None,
                'high':        None,
                'threshold':   float(cap_strike),
                'close_time':  close_time,
            }
        elif strike_type == "greater" and floor_strike is not None:
            return {
                'series':      series,
                'city_name':   city['name'],
                'lat':         city['lat'],
                'lon':         city['lon'],
                'date':        date_iso,
                'strike_type': 'greater',
                'low':         None,
                'high':        None,
                'threshold':   float(floor_strike),
                'close_time':  close_time,
            }
        # Fallthrough to suffix parsing if strike_type missing

    # Parse from suffix: B{float} = between, T{float} = threshold
    sm = re.match(r'([BT])(-?\d+\.?\d*)$', suffix)
    if not sm:
        log.debug("parse_ticker: suffix parse failed '%s' for %s", suffix, ticker)
        return None

    marker, val_str = sm.group(1), sm.group(2)
    val = float(val_str)

    if marker == 'B':
        # B midpoint: range is [floor(val), ceil(val)] = [val-0.5, val+0.5]
        # e.g. B77.5 → 77° to 78°
        low  = math.floor(val)
        high = math.ceil(val)
        return {
            'series':      series,
            'city_name':   city['name'],
            'lat':         city['lat'],
            'lon':         city['lon'],
            'date':        date_iso,
            'strike_type': 'between',
            'low':         float(low),
            'high':        float(high),
            'threshold':   None,
            'close_time':  '',
        }
    else:  # T
        # T marker: need to determine less vs greater from market_data or title
        # Without market_data we can't know definitively; default to guessing
        # from surrounding context — return both possibilities
        # This branch is fallback only; real parsing uses market_data
        return {
            'series':      series,
            'city_name':   city['name'],
            'lat':         city['lat'],
            'lon':         city['lon'],
            'date':        date_iso,
            'strike_type': 'threshold_ambiguous',
            'low':         None,
            'high':        None,
            'threshold':   val,
            'close_time':  '',
        }

# ─────────────────────────────────────────────────────────────────────────────
# WEATHER MARKET FETCHER — v4 series-ticker based
# ─────────────────────────────────────────────────────────────────────────────

def get_weather_markets() -> list:
    """
    Fetch active weather markets by querying each series in WEATHER_SERIES.
    Returns list of raw market dicts (with parsed info attached as _parsed).
    """
    markets    = []
    seen_ticks = set()

    for series in WEATHER_SERIES:
        try:
            data = kalshi_get(
                "/events",
                params={
                    "series_ticker":       series,
                    "with_nested_markets": "true",
                    "limit":               10,
                    "status":              "open",
                }
            )
            events = data.get("events", [])
            for event in events:
                for m in event.get("markets", []):
                    ticker = m.get("ticker", "")
                    if not ticker or ticker in seen_ticks:
                        continue
                    if m.get("status") != "active":
                        continue
                    # volume_fp returns None from Kalshi API — use volume field instead
                    vol = m.get("volume") or m.get("volume_fp") or 0
                    try:
                        vol = float(vol)
                    except (TypeError, ValueError):
                        vol = 0
                    # Only skip if volume is explicitly reported AND too low
                    # (None volume = market just opened, still valid)
                    if vol is not None and vol > 0 and vol < MIN_VOLUME:
                        continue

                    # Attach parsed info immediately using market_data
                    parsed = parse_ticker(ticker, market_data=m)
                    if parsed is None:
                        log.debug("[Markets] Could not parse ticker: %s", ticker)
                        continue

                    m["_parsed"] = parsed
                    seen_ticks.add(ticker)
                    markets.append(m)

        except Exception as e:
            log.debug("[Markets] %s: %s", series, e)
        time.sleep(0.1)   # pace API requests

    log.info("[Markets] Found %d active weather markets (vol>=%d)", len(markets), MIN_VOLUME)
    return markets

# ─────────────────────────────────────────────────────────────────────────────
# BLOCKS — persistent series+date blocks
# ─────────────────────────────────────────────────────────────────────────────

_blocks_cache: dict = {}
_blocks_loaded: bool = False


def load_blocks() -> dict:
    global _blocks_cache, _blocks_loaded
    if not _blocks_loaded:
        try:
            if os.path.exists(BLOCKS_FILE):
                with open(BLOCKS_FILE) as f:
                    _blocks_cache = json.load(f)
        except Exception:
            _blocks_cache = {}
        _blocks_loaded = True
    return _blocks_cache


def add_block(series: str, date: str):
    blocks = load_blocks()
    key = "%s_%s" % (series, date)
    blocks[key] = True
    _blocks_cache[key] = True
    try:
        os.makedirs(os.path.dirname(BLOCKS_FILE), exist_ok=True)
        with open(BLOCKS_FILE, 'w') as f:
            json.dump(blocks, f)
        log.info("[Block] Blocking %s on %s — prior market resolved NO", series, date)
    except Exception as e:
        log.debug("[Block] Save failed: %s", e)


def is_blocked(series: str, date: str) -> bool:
    return "%s_%s" % (series, date) in load_blocks()

# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADE LOG
# ─────────────────────────────────────────────────────────────────────────────

def load_paper_trades() -> list:
    try:
        if os.path.exists(WEATHER_PAPER_TRADES_FILE):
            with open(WEATHER_PAPER_TRADES_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_paper_trades(trades: list):
    try:
        os.makedirs(os.path.dirname(WEATHER_PAPER_TRADES_FILE), exist_ok=True)
        with open(WEATHER_PAPER_TRADES_FILE, 'w') as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        log.error("[PaperTrade] Save failed: %s", e)


def log_paper_trade(market: dict, parsed: dict, model_prob: float,
                     kalshi_mid: float, edge: float, direction: str,
                     balance: float) -> dict:
    price     = kalshi_mid if direction == 'YES' else (1.0 - kalshi_mid)
    price     = max(price, 0.01)
    contracts = max(1, int(math.floor((balance * MAX_POSITION_PCT) / price)))
    cost      = round(contracts * price, 2)

    entry = {
        'ticker':        market.get('ticker', ''),
        'series':        parsed['series'],
        'city_name':     parsed['city_name'],
        'date':          parsed['date'],
        'strike_type':   parsed['strike_type'],
        'low':           parsed.get('low'),
        'high':          parsed.get('high'),
        'threshold':     parsed.get('threshold'),
        'direction':     direction,
        'forecast_high': market.get('_forecast_high'),
        'kalshi_prob':   round(kalshi_mid, 4),
        'model_prob':    round(model_prob, 4),
        'edge':          round(edge, 4),
        'contracts':     contracts,
        'cost':          cost,
        'timestamp':     datetime.now(timezone.utc).isoformat(),
        'status':        'OPEN',
        'result':        None,
    }

    trades = load_paper_trades()
    trades.append(entry)
    save_paper_trades(trades)
    log.info("[PaperTrade] Logged: %s BUY %s @ %.2f x%d cost=$%.2f",
             entry['ticker'], direction, price, contracts, cost)
    return entry


def update_paper_trade_results(resolved_tickers: dict):
    trades  = load_paper_trades()
    changed = False
    for trade in trades:
        if trade['status'] != 'OPEN':
            continue
        ticker = trade['ticker']
        if ticker not in resolved_tickers:
            continue
        result    = resolved_tickers[ticker]
        direction = trade['direction']
        if (direction == 'YES' and result == 'yes') or (direction == 'NO' and result == 'no'):
            trade['status'] = 'WIN'
        else:
            trade['status'] = 'LOSS'
        trade['result'] = result
        changed = True
        log.info("[PaperTrade] %s: %s (dir=%s, result=%s)",
                 ticker, trade['status'], direction, result)
    if changed:
        save_paper_trades(trades)

# ─────────────────────────────────────────────────────────────────────────────
# DAILY FORECAST — Tomorrow.io (primary) + Open-Meteo (fallback)
# Returns {date_str: temp_max_f} e.g. {"2026-04-17": 81.4}
# ─────────────────────────────────────────────────────────────────────────────

_forecast_mem_cache: dict = {}   # {cache_key: {'data': dict, 'ts': float}}


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST PREFETCH ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _near_prefetch_window() -> bool:
    """True if current time is within PREFETCH_WINDOW_MIN of a model update window."""
    now_utc = datetime.now(timezone.utc)
    m = now_utc.hour * 60 + now_utc.minute
    for w in PREFETCH_WINDOWS_UTC:
        if abs(m - w) <= PREFETCH_WINDOW_MIN:
            return True
    return False


def _last_prefetch_time() -> float:
    """Returns timestamp of last successful prefetch, or 0."""
    try:
        if os.path.exists(PREFETCH_LOCK_FILE):
            with open(PREFETCH_LOCK_FILE) as f:
                return json.load(f).get('last_prefetch', 0.0)
    except Exception:
        pass
    return 0.0


def _mark_prefetch_done():
    try:
        with open(PREFETCH_LOCK_FILE, 'w') as f:
            json.dump({'last_prefetch': time.time(),
                       'at': datetime.now(timezone.utc).isoformat()}, f)
    except Exception as e:
        log.warning("[Prefetch] Could not write lock file: %s", e)


def prefetch_all_forecasts():
    """
    Fetch forecasts for all 19 cities from Tomorrow.io and Open-Meteo.
    Writes results to disk cache. Called at model update windows.
    This is the ONLY place that calls the weather APIs — scan loop reads from cache.
    """
    log.info("[Prefetch] Starting forecast prefetch for all %d series...", len(WEATHER_SERIES))
    start = time.time()
    success_t = 0
    success_o = 0
    errors = []

    # Load existing disk cache
    disk_cache = {}
    try:
        if os.path.exists(FORECAST_CACHE_FILE):
            with open(FORECAST_CACHE_FILE) as f:
                disk_cache = json.load(f)
    except Exception:
        disk_cache = {}

    now_ts = time.time()

    for series in WEATHER_SERIES:
        city = SERIES_CITY_MAP.get(series)
        if not city:
            continue

        # Tomorrow.io
        try:
            t_data = _fetch_tomorrow_io_daily(series, city)
            if t_data:
                key = "daily_%s" % series
                disk_cache[key] = {'data': t_data, 'ts': now_ts}
                _forecast_mem_cache[key] = {'data': t_data, 'ts': now_ts}
                success_t += 1
        except Exception as e:
            log.warning("[Prefetch] Tomorrow.io %s: %s", series, e)
            errors.append("T.io:%s" % series)
        time.sleep(0.15)  # pace Tomorrow.io calls

        # Open-Meteo
        try:
            o_data = _fetch_open_meteo_daily(series, city)
            if o_data:
                key = "openmeteo_%s" % series
                disk_cache[key] = {'data': o_data, 'ts': now_ts}
                _forecast_mem_cache[key] = {'data': o_data, 'ts': now_ts}
                success_o += 1
        except Exception as e:
            log.debug("[Prefetch] Open-Meteo %s: %s", series, e)
        time.sleep(0.05)

    # Write updated disk cache
    try:
        with open(FORECAST_CACHE_FILE, 'w') as f:
            json.dump(disk_cache, f)
    except Exception as e:
        log.warning("[Prefetch] Cache write failed: %s", e)

    elapsed = time.time() - start
    log.info("[Prefetch] Done in %.1fs | Tomorrow.io: %d/%d | Open-Meteo: %d/%d | errors: %s",
             elapsed, success_t, len(WEATHER_SERIES), success_o, len(WEATHER_SERIES),
             errors if errors else "none")

    if success_t + success_o > 0:
        _mark_prefetch_done()

    return success_t, success_o


def fetch_daily_highs(series: str) -> dict:
    """
    Returns {date_str: temp_max_fahrenheit} for the next ~5 days.
    Uses Tomorrow.io 1d timesteps (temperatureMax field).
    Falls back to Open-Meteo if Tomorrow.io fails.
    Cache per series, TTL = TOMORROW_CACHE_TTL.
    """
    now_ts   = time.time()
    cache_key = "daily_%s" % series

    # In-memory cache
    cached = _forecast_mem_cache.get(cache_key)
    if cached and (now_ts - cached['ts']) < TOMORROW_CACHE_TTL:
        return cached['data']

    # Disk cache
    try:
        if os.path.exists(FORECAST_CACHE_FILE):
            with open(FORECAST_CACHE_FILE) as f:
                disk = json.load(f)
            entry = disk.get(cache_key, {})
            if (now_ts - entry.get('ts', 0)) < TOMORROW_CACHE_TTL:
                data = entry.get('data', {})
                _forecast_mem_cache[cache_key] = {'data': data, 'ts': entry['ts']}
                return data
    except Exception:
        pass

    city = SERIES_CITY_MAP.get(series)
    if not city:
        return {}

    result = _fetch_tomorrow_io_daily(series, city)
    if not result:
        result = _fetch_open_meteo_daily(series, city)
    if not result:
        result = _fetch_noaa_daily(series, city)

    if result:
        _forecast_mem_cache[cache_key] = {'data': result, 'ts': now_ts}
        _save_forecast_to_disk(cache_key, result, now_ts)

    return result


def _fetch_tomorrow_io_daily(series: str, coords: dict) -> dict:
    """Fetch daily forecast from Tomorrow.io. Returns {date: tempMax_f} or {}.

    CRITICAL: startTime must be anchored to UTC midnight, NOT nowPlus0h.
    nowPlus0h causes the first interval to cover only remaining hours of today,
    giving a truncated temperatureMax that can be wrong by 4-8F.
    """
    try:
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        r = requests.get(
            "https://api.tomorrow.io/v4/timelines",
            params={
                "location":  "%s,%s" % (coords['lat'], coords['lon']),
                "fields":    "temperatureMax",
                "units":     "imperial",
                "timesteps": "1d",
                "startTime": today_utc,
                "endTime":   "nowPlus5d",
                "apikey":    TOMORROW_API_KEY,
            },
            timeout=15,
        )
        if r.status_code == 200:
            intervals = r.json()["data"]["timelines"][0]["intervals"]
            result    = {}
            for iv in intervals:
                date_str = iv["startTime"][:10]   # "2026-04-17"
                temp_max = iv["values"].get("temperatureMax")
                if temp_max is not None:
                    result[date_str] = float(temp_max)
            log.info("[Tomorrow.io] %s: %d daily highs (UTC midnight anchor)", series, len(result))
            return result
        elif r.status_code == 429:
            log.warning("[Tomorrow.io] Rate limited for %s", series)
        else:
            log.debug("[Tomorrow.io] %s: HTTP %s", series, r.status_code)
    except Exception as e:
        log.debug("[Tomorrow.io] %s failed: %s", series, e)
    return {}


def _fetch_open_meteo_daily(series: str, coords: dict) -> dict:
    """Fetch daily max temp from Open-Meteo (fallback). Returns {date: tempMax_f} or {}."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":         coords["lat"],
                "longitude":        coords["lon"],
                "daily":            "temperature_2m_max",
                "temperature_unit": "fahrenheit",
                "forecast_days":    6,
                "timezone":         "UTC",
            },
            timeout=15,
        )
        if r.status_code == 200:
            data  = r.json()
            dates = data["daily"]["time"]        # ["2026-04-17", ...]
            temps = data["daily"]["temperature_2m_max"]
            result = {}
            for d, t in zip(dates, temps):
                if t is not None:
                    result[d] = float(t)
            log.info("[Open-Meteo] %s: %d daily highs", series, len(result))
            return result
    except Exception as e:
        log.debug("[Open-Meteo] %s failed: %s", series, e)
    return {}



def _fetch_noaa_daily(series: str, coords: dict) -> dict:
    """
    Fetch NOAA gridpoint forecast — highly accurate US weather.
    Free, no API key. Two-step: get gridpoint, then get forecast.
    Returns {date: tempMax_f} or {}.
    """
    try:
        lat = coords['lat']
        lon = coords['lon']
        # Step 1: get gridpoint
        r1 = requests.get(
            f'https://api.weather.gov/points/{lat:.4f},{lon:.4f}',
            headers={'User-Agent': 'StrattonOakmont/1.0 stratton@example.com'},
            timeout=10
        )
        if r1.status_code != 200:
            log.debug('[NOAA] %s: gridpoint HTTP %s', series, r1.status_code)
            return {}
        forecast_url = r1.json().get('properties', {}).get('forecast', '')
        if not forecast_url:
            return {}
        # Step 2: get forecast
        r2 = requests.get(
            forecast_url,
            headers={'User-Agent': 'StrattonOakmont/1.0 stratton@example.com'},
            timeout=10
        )
        if r2.status_code != 200:
            log.debug('[NOAA] %s: forecast HTTP %s', series, r2.status_code)
            return {}
        periods = r2.json().get('properties', {}).get('periods', [])
        # Extract daily highs (daytime periods), already in Fahrenheit
        daily_highs = {}
        for period in periods:
            if period.get('isDaytime', False):
                start_time = period.get('startTime', '')[:10]
                temp = period.get('temperature', None)
                if start_time and temp is not None:
                    daily_highs[start_time] = float(temp)
        log.info('[NOAA] %s: %d daily highs', series, len(daily_highs))
        return daily_highs
    except Exception as e:
        log.debug('[NOAA] %s failed: %s', series, e)
    return {}

def _save_forecast_to_disk(cache_key: str, data: dict, ts: float):
    try:
        disk = {}
        if os.path.exists(FORECAST_CACHE_FILE):
            with open(FORECAST_CACHE_FILE) as f:
                disk = json.load(f)
        disk[cache_key] = {'data': data, 'ts': ts}
        with open(FORECAST_CACHE_FILE, 'w') as f:
            json.dump(disk, f)
    except Exception as e:
        log.debug("[Forecast] Disk cache save failed: %s", e)

# ─────────────────────────────────────────────────────────────────────────────
# PROBABILITY CALCULATION — range brackets
# ─────────────────────────────────────────────────────────────────────────────

def normal_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def calc_prob_in_range(forecast_high: float, low: float, high: float,
                        uncertainty: float = 3.0) -> float:
    """
    P(actual_high in [low, high]) given forecast_high and uncertainty (std dev °F).
    Uses normal distribution: CDF(high + 0.5) - CDF(low - 0.5)
    Clipped to [0.01, 0.99].
    """
    sigma = max(uncertainty, 0.5)
    z_hi  = (high + 0.5 - forecast_high) / sigma
    z_lo  = (low  - 0.5 - forecast_high) / sigma
    prob  = normal_cdf(z_hi) - normal_cdf(z_lo)
    return max(0.01, min(0.99, prob))


def calc_prob_above(forecast_high: float, threshold: float, uncertainty: float = 3.0) -> float:
    """P(actual_high > threshold) given forecast and uncertainty."""
    sigma = max(uncertainty, 0.5)
    z     = (threshold + 0.5 - forecast_high) / sigma
    prob  = 1.0 - normal_cdf(z)
    return max(0.01, min(0.99, prob))


def calc_prob_below(forecast_high: float, threshold: float, uncertainty: float = 3.0) -> float:
    """P(actual_high < threshold) given forecast and uncertainty."""
    sigma = max(uncertainty, 0.5)
    z     = (threshold - 0.5 - forecast_high) / sigma
    prob  = normal_cdf(z)
    return max(0.01, min(0.99, prob))


def fetch_daily_highs_consensus(series: str) -> tuple:
    """
    Fetch daily high forecasts from Tomorrow.io AND Open-Meteo.
    Returns (forecast_high, uncertainty_f) where:
      - forecast_high = mean of available models
      - uncertainty_f = std_dev if both models available, else fixed fallback
    Returns None if no data at all.
    """
    city = SERIES_CITY_MAP.get(series)
    if not city:
        return None, None

    forecasts = []

    # Tomorrow.io
    try:
        t = _fetch_tomorrow_io_daily(series, city)
        if t:
            forecasts.append(("tomorrow_io", t))
    except Exception as e:
        log.debug("[Consensus] Tomorrow.io %s: %s", series, e)

    # Open-Meteo
    try:
        o = _fetch_open_meteo_daily(series, city)
        if o:
            forecasts.append(("open_meteo", o))
    except Exception as e:
        log.debug("[Consensus] Open-Meteo %s: %s", series, e)

    if not forecasts:
        return None, None

    return forecasts  # list of (source, {date: temp})


def get_consensus_forecast(series: str, date: str) -> tuple:
    """
    Returns (forecast_high_f, uncertainty_f, agreement) for a given series + date.
    agreement: 'HIGH' (<2F std), 'MEDIUM' (2-4F), 'LOW' (>4F or single model)
    """
    city = SERIES_CITY_MAP.get(series)
    if not city:
        return None, 5.0, 'LOW'

    temps = []

    # Tomorrow.io (cached via fetch_daily_highs)
    try:
        t_data = _fetch_tomorrow_io_daily(series, city)
        if t_data and date in t_data:
            temps.append(t_data[date])
    except Exception:
        pass

    # Open-Meteo — use prefetch cache first, fall back to live call
    try:
        om_key = "openmeteo_%s" % series
        cached_om = _forecast_mem_cache.get(om_key)
        if cached_om and date in cached_om.get("data", {}):
            temps.append(cached_om["data"][date])
        else:
            # Try disk cache
            o_data = None
            try:
                if os.path.exists(FORECAST_CACHE_FILE):
                    with open(FORECAST_CACHE_FILE) as _f:
                        _dc = json.load(_f)
                    _entry = _dc.get(om_key, {})
                    if (time.time() - _entry.get("ts", 0)) < TOMORROW_CACHE_TTL:
                        o_data = _entry.get("data", {})
                        if o_data:
                            _forecast_mem_cache[om_key] = {"data": o_data, "ts": _entry["ts"]}
            except Exception:
                pass
            if o_data and date in o_data:
                temps.append(o_data[date])
    except Exception:
        pass

    if not temps:
        return None, 5.0, 'LOW'

    forecast_high = sum(temps) / len(temps)

    if len(temps) >= 2:
        import statistics as _stats
        std = _stats.stdev(temps) if len(temps) >= 2 else 0
        if std < 2.0:
            agreement = 'HIGH'
        elif std < 4.0:
            agreement = 'MEDIUM'
        else:
            agreement = 'LOW'
        # Use actual std_dev as uncertainty (min 1.5F, max 6F)
        uncertainty = max(1.5, min(6.0, std * 1.5))
    else:
        # Single model — no cross-check, mark LOW to skip signal
        today = datetime.now(timezone.utc).date()
        try:
            target = datetime.strptime(date, '%Y-%m-%d').date()
            days_out = (target - today).days
        except Exception:
            days_out = 1
        uncertainty = 2.5 if days_out <= 0 else (3.5 if days_out == 1 else 5.0)
        agreement = 'LOW'  # single model = no cross-check, skip signal

    return forecast_high, uncertainty, agreement


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST BIAS CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

_bias_cache: dict = {}
_bias_cache_ts: float = 0.0
_BIAS_CACHE_TTL = 3600  # recompute hourly


def compute_city_bias() -> dict:
    """
    Read accuracy log, compute per-city rolling forecast bias over last BIAS_WINDOW_DAYS.
    Returns {city_name: bias_f} where positive = forecast was too low (actual ran hotter).
    Only applied if >= BIAS_MIN_SAMPLES resolved trades exist for that city.
    """
    global _bias_cache, _bias_cache_ts
    now = time.time()
    if _bias_cache and (now - _bias_cache_ts) < _BIAS_CACHE_TTL:
        return _bias_cache

    if not os.path.exists(WEATHER_ACCURACY_FILE):
        return {}

    try:
        with open(WEATHER_ACCURACY_FILE) as f:
            entries = json.load(f)
    except Exception:
        return {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=BIAS_WINDOW_DAYS)).strftime('%Y-%m-%d')

    # Load paper trades for strike info
    try:
        with open(WEATHER_PAPER_TRADES_FILE) as f:
            trades = json.load(f)
        trade_map = {t['ticker']: t for t in trades if t['status'] in ('WIN', 'LOSS')}
    except Exception:
        trade_map = {}

    city_biases = {}
    from collections import defaultdict
    raw = defaultdict(list)

    for entry in entries:
        date = entry.get('date', '')
        if date < cutoff:
            continue
        city = entry.get('city_name')
        ticker = entry.get('ticker', '')
        if not city:
            continue

        trade = trade_map.get(ticker)
        if not trade:
            continue

        st = trade.get('strike_type')
        thresh = trade.get('threshold')
        fc = entry.get('forecast_high')
        result = entry.get('market_result')

        if not all([st in ('less', 'greater'), thresh, fc, result]):
            continue

        # Infer bias direction from result vs forecast
        if st == 'greater':
            if result == 'yes' and fc < thresh:
                raw[city].append(thresh - fc + 1.0)   # actual > thresh, forecast was below
            elif result == 'no' and fc > thresh:
                raw[city].append(-(fc - thresh + 1.0))  # forecast too high
        elif st == 'less':
            if result == 'yes' and fc > thresh:
                raw[city].append(-(fc - thresh + 1.0))  # forecast too high, yet actual < thresh
            elif result == 'no' and fc < thresh:
                raw[city].append(thresh - fc + 1.0)   # forecast too low

    for city, biases in raw.items():
        if len(biases) >= BIAS_MIN_SAMPLES:
            avg = sum(biases) / len(biases)
            # Cap bias correction at ±6°F to avoid over-correction
            city_biases[city] = max(-6.0, min(6.0, avg))
            log.info("[Bias] %s: %.1fF correction (n=%d, window=%dd)",
                     city, avg, len(biases), BIAS_WINDOW_DAYS)

    _bias_cache = city_biases
    _bias_cache_ts = now
    return city_biases


def get_bias_corrected_forecast(series: str, date: str) -> tuple:
    """
    Get consensus forecast with per-city bias correction applied.
    Returns (corrected_forecast_high, uncertainty, agreement, raw_forecast, bias_applied).
    """
    raw_high, uncertainty, agreement = get_consensus_forecast(series, date)
    if raw_high is None:
        return None, uncertainty, agreement, None, 0.0

    city_info = SERIES_CITY_MAP.get(series, {})
    city_name = city_info.get('name', '')

    bias = compute_city_bias().get(city_name, 0.0)
    corrected = raw_high + bias

    if abs(bias) >= 0.5:
        log.debug("[Bias] %s %s: raw=%.1fF bias=%+.1fF corrected=%.1fF",
                  series, date, raw_high, bias, corrected)

    return corrected, uncertainty, agreement, raw_high, bias


def uncertainty_for_date(date: str) -> float:
    """
    Forecast uncertainty (°F std dev) based on how far out the date is.
    Today: 2.5°F, Tomorrow: 3.5°F, 2+ days: 5.0°F
    """
    today = datetime.now(timezone.utc).date()
    try:
        target = datetime.strptime(date, '%Y-%m-%d').date()
        days_out = (target - today).days
    except Exception:
        return 4.0

    if days_out <= 0:
        return 2.5
    elif days_out == 1:
        return 3.5
    else:
        return 5.0

# ─────────────────────────────────────────────────────────────────────────────
# TRADE EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

def execute_weather_trade(market: dict, direction: str, edge: float,
                           model_prob: float, balance: float) -> dict:
    """Place a limit order on a weather market. 3% of balance position sizing."""
    ticker     = market.get("ticker", "")
    yes_bid    = float(market.get("yes_bid_dollars", 0) or 0)
    yes_ask    = float(market.get("yes_ask_dollars", 1) or 1)
    kalshi_mid = (yes_bid + yes_ask) / 2.0

    if direction == "YES":
        price_dollars = kalshi_mid
        side          = "yes"
    else:
        price_dollars = 1.0 - kalshi_mid
        side          = "no"

    price_f   = max(price_dollars, 0.01)
    price_c   = int(round(price_f * 100))
    contracts = max(1, int(math.floor((balance * MAX_POSITION_PCT) / price_f)))
    cost      = round(contracts * price_f, 2)

    client_order_id = "weather-%s" % uuid.uuid4()
    order_body = {
        "ticker":          ticker,
        "client_order_id": client_order_id,
        "type":            "limit",
        "action":          "buy",
        "side":            side,
        "count":           contracts,
        "yes_price":       price_c if side == "yes" else (100 - price_c),
    }

    log.info("[Trade] Placing: %s BUY %s @ %d¢ x%d cost=$%.2f | model=%.0f%% edge=%+.0f%%",
             ticker, direction, price_c, contracts, cost, model_prob * 100, edge * 100)

    resp = kalshi_post("/portfolio/orders", order_body)

    if "error" in resp and "order" not in resp:
        log.error("[Trade] FAILED for %s: %s", ticker, resp.get("error"))
        return {"status": "failed", "error": resp.get("error", "unknown"),
                "cost": cost, "contracts": contracts, "price_c": price_c, "direction": direction}

    order = resp.get("order", {})
    return {
        "status":    order.get("status", "unknown"),
        "order_id":  order.get("order_id", client_order_id),
        "filled":    order.get("filled_count", 0),
        "remaining": order.get("remaining_count", contracts),
        "cost":      cost,
        "contracts": contracts,
        "price_c":   price_c,
        "direction": direction,
        "ticker":    ticker,
    }

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD MESSAGE FORMAT
# ─────────────────────────────────────────────────────────────────────────────

def _range_label(parsed: dict) -> str:
    st = parsed['strike_type']
    if st == 'between':
        return "%.0f-%.0f°F" % (parsed['low'], parsed['high'])
    elif st == 'less':
        return "<%.0f°F" % parsed['threshold']
    elif st == 'greater':
        return ">%.0f°F" % parsed['threshold']
    return "?°F"


def format_paper_msg(ticker: str, parsed: dict, direction: str, edge: float,
                      model_prob: float, kalshi_mid: float, forecast_high: float,
                      uncertainty: float, contracts: int, cost: float) -> str:
    city      = parsed['city_name']
    rng       = _range_label(parsed)
    date      = parsed['date']
    price_c   = int(kalshi_mid * 100) if direction == 'YES' else int((1 - kalshi_mid) * 100)

    return "\n".join([
        "📋 PAPER WEATHER — %s" % ticker,
        "City: %s | Date: %s | Range: %s" % (city, date, rng),
        "Tomorrow.io high: %.1f°F ±%.1f°F" % (forecast_high, uncertainty),
        "Model: %.0f%% | Kalshi: %d¢ | Edge: %+.0f%%" % (model_prob*100, int(kalshi_mid*100), edge*100),
        "Would BUY %s @ %d¢ | %d contracts | $%.2f" % (direction, price_c, contracts, cost),
    ])


def format_live_msg(ticker: str, parsed: dict, direction: str, edge: float,
                     model_prob: float, kalshi_mid: float, forecast_high: float,
                     result: dict) -> str:
    city    = parsed['city_name']
    rng     = _range_label(parsed)
    date    = parsed['date']
    n       = result.get('contracts', 0)
    cost    = result.get('cost', 0.0)
    price_c = result.get('price_c', 0)

    now_et_str = datetime.now(ET).strftime("%I:%M %p ET")
    return "\n".join([
        "🌤️ WEATHER TRADE — %s" % ticker,
        "City: %s | Date: %s | Range: %s" % (city, date, rng),
        "Forecast high: %.1f°F | Model: %.0f%% | Kalshi: %d¢ | Edge: %+.0f%%" % (
            forecast_high, model_prob*100, int(kalshi_mid*100), edge*100),
        "BUY %s @ %d¢ | %d contracts | $%.2f | %s" % (direction, price_c, n, cost, now_et_str),
    ])

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# DAILY SUMMARY — post once per day to #mark-signals
# ─────────────────────────────────────────────────────────────────────────────

_SUMMARY_DEDUP_KEY = 'daily_summary'


def _summary_posted_today() -> bool:
    today = datetime.now(ET).strftime('%Y-%m-%d')
    data  = _load_dedup()
    return today in data.get(_SUMMARY_DEDUP_KEY, [])


def _mark_summary_posted():
    today = datetime.now(ET).strftime('%Y-%m-%d')
    data  = _load_dedup()
    if _SUMMARY_DEDUP_KEY not in data:
        data[_SUMMARY_DEDUP_KEY] = []
    if today not in data[_SUMMARY_DEDUP_KEY]:
        data[_SUMMARY_DEDUP_KEY].append(today)
    _save_dedup(data)


def post_daily_summary():
    """
    Post a paper trade EOD summary to #mark-signals once per day.
    Fires when UTC hour is between 20-23 (after US market closes) and not yet posted today.
    """
    now_utc = datetime.now(timezone.utc)
    now_et  = datetime.now(ET)

    # Only post after 4:00 PM ET (markets mostly resolved by then)
    if now_et.hour < 16:
        return

    if _summary_posted_today():
        return

    trades = load_paper_trades()
    today  = now_et.strftime('%Y-%m-%d')

    # Trades that were open for today's date
    today_trades = [t for t in trades if t.get('date') == today or
                    t.get('timestamp', '').startswith(today)]

    if not today_trades:
        log.debug("[Summary] No paper trades for today — skipping EOD summary")
        return

    wins   = [t for t in today_trades if t['status'] == 'WIN']
    losses = [t for t in today_trades if t['status'] == 'LOSS']
    open_  = [t for t in today_trades if t['status'] == 'OPEN']

    resolved = len(wins) + len(losses)
    hit_pct  = (len(wins) / resolved * 100) if resolved > 0 else 0.0
    avg_edge = (sum(abs(t['edge']) for t in today_trades) / len(today_trades) * 100) if today_trades else 0.0

    lines = [
        "📋 PAPER SUMMARY — %s ET" % today,
        "Signals: %d | Resolved: %d | Open: %d" % (len(today_trades), resolved, len(open_)),
    ]
    if resolved > 0:
        lines.append("Results: %d WIN / %d LOSS (%.0f%% hit rate)" % (len(wins), len(losses), hit_pct))
    lines.append("Avg edge: %.0f¢ | Threshold: %.0f¢" % (avg_edge, WEATHER_EDGE_THRESHOLD * 100))

    if today_trades:
        lines.append("")
        lines.append("Signals today:")
        for t in sorted(today_trades, key=lambda x: abs(x['edge']), reverse=True)[:8]:
            status_icon = {"WIN": "✅", "LOSS": "❌", "OPEN": "⏳"}.get(t['status'], "?")
            lines.append("  %s %s %s @ %.0f¢ edge" % (
                status_icon, t['ticker'], t['direction'], abs(t['edge']) * 100))

    msg = chr(10).join(lines)
    post_discord(msg, dry_run=False)
    _mark_summary_posted()
    log.info("[Summary] EOD paper summary posted to Discord")


# ─────────────────────────────────────────────────────────────────────────────
# RESOLUTION ENGINE — fetch finalized Kalshi markets, update paper trades
# ─────────────────────────────────────────────────────────────────────────────

def fetch_finalized_results(tickers: list, timeout_sec: int = 60) -> dict:
    """
    Given a list of tickers, fetch any that are finalized from Kalshi.
    Returns {ticker: 'yes'|'no'} for settled markets only.
    Caps total execution at timeout_sec to prevent scan hangs.
    """
    results = {}
    deadline = time.time() + timeout_sec
    unique = list(set(tickers))
    log.debug("[Resolve] Checking %d unique tickers (timeout=%ds)", len(unique), timeout_sec)
    for ticker in unique:
        if time.time() > deadline:
            log.warning("[Resolve] Timeout hit after %ds — stopping resolution check", timeout_sec)
            break
        try:
            data = kalshi_get("/markets/%s" % ticker)
            market = data.get("market", data)
            status = market.get("status", "")
            result = market.get("result", "")
            if status == "finalized" and result in ("yes", "no"):
                results[ticker] = result
                log.info("[Resolve] %s settled → %s", ticker, result)
        except Exception as e:
            log.debug("[Resolve] %s fetch error: %s", ticker, e)
        time.sleep(0.05)
    return results


def resolve_open_trades():
    """
    Check all OPEN paper trades against Kalshi. Update WIN/LOSS.
    Only checks tickers from prior days — today's markets can't be settled yet.
    """
    trades = load_paper_trades()
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    # Only check prior-day trades — today can't be settled
    open_trades = [t for t in trades
                   if t.get("status") == "OPEN" and t.get("date", today_str) < today_str]
    if not open_trades:
        log.debug("[Resolve] No prior-day open trades to check")
        return 0

    tickers = [t["ticker"] for t in open_trades]
    log.info("[Resolve] Checking %d prior-day open trades for settlement...", len(open_trades))

    results = fetch_finalized_results(tickers)
    if not results:
        log.debug("[Resolve] No settlements found yet")
        return 0

    changed = 0
    accuracy_entries = []

    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        ticker = trade["ticker"]
        if ticker not in results:
            continue

        market_result = results[ticker]
        direction     = trade["direction"]

        if (direction == "YES" and market_result == "yes") or            (direction == "NO"  and market_result == "no"):
            trade["status"] = "WIN"
        else:
            trade["status"] = "LOSS"

        trade["result"]      = market_result
        trade["resolved_at"] = datetime.now(timezone.utc).isoformat()
        changed += 1

        log.info("[Resolve] %s: %s (dir=%s result=%s edge=%.0f%%)",
                 ticker, trade["status"], direction, market_result,
                 abs(trade.get("edge", 0)) * 100)

        # Accuracy entry — how close was the forecast?
        accuracy_entries.append({
            "ticker":        ticker,
            "city_name":     trade.get("city_name"),
            "date":          trade.get("date"),
            "forecast_high": trade.get("forecast_high"),
            "direction":     direction,
            "edge":          trade.get("edge"),
            "kalshi_prob":   trade.get("kalshi_prob"),
            "model_prob":    trade.get("model_prob"),
            "market_result": market_result,
            "trade_status":  trade["status"],
            "resolved_at":   trade["resolved_at"],
        })

    if changed:
        save_paper_trades(trades)
        log.info("[Resolve] Updated %d trades (WIN/LOSS)", changed)

        # Update experiments file too
        _resolve_experiments(results)

        # Append to accuracy log
        _append_accuracy(accuracy_entries)

    return changed


def _resolve_experiments(results: dict):
    """Update experiments file with same resolution results."""
    try:
        if not os.path.exists(PAPER_EXPERIMENTS_FILE):
            return
        with open(PAPER_EXPERIMENTS_FILE) as f:
            data = json.load(f)
        changed = False
        for entry in data:
            if entry.get("status") != "OPEN":
                continue
            ticker = entry.get("ticker")
            if ticker not in results:
                continue
            market_result = results[ticker]
            direction     = entry["direction"]
            entry["status"] = "WIN" if (
                (direction == "YES" and market_result == "yes") or
                (direction == "NO"  and market_result == "no")
            ) else "LOSS"
            entry["result"]      = market_result
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        if changed:
            with open(PAPER_EXPERIMENTS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            log.info("[Resolve] Experiments file updated")
    except Exception as e:
        log.warning("[Resolve] Experiment update error: %s", e)


def _append_accuracy(entries: list):
    """Append resolution entries to accuracy log."""
    try:
        existing = []
        if os.path.exists(WEATHER_ACCURACY_FILE):
            with open(WEATHER_ACCURACY_FILE) as f:
                existing = json.load(f)
        existing.extend(entries)
        with open(WEATHER_ACCURACY_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        log.info("[Accuracy] Logged %d resolved trades to accuracy file", len(entries))
    except Exception as e:
        log.warning("[Accuracy] Write error: %s", e)


def run_weather_scan(dry_run: bool = False) -> dict:
    """
    Full weather scan: fetch markets → parse → get daily high forecasts
    → calculate range edges → execute paper or live trades.
    """
    log.info("=" * 60)
    log.info("WEATHER SCAN v4 | dry_run=%s", dry_run)
    log.info("=" * 60)

    # ── 0a. Prefetch forecasts if near model update window ───────────────────
    try:
        last_pf = _last_prefetch_time()
        if _near_prefetch_window() and (time.time() - last_pf) > 1800:
            log.info("[Scan] Near model update window — triggering prefetch")
            prefetch_all_forecasts()
        elif (time.time() - last_pf) > TOMORROW_CACHE_TTL:
            # Safety: if cache is older than TTL, prefetch regardless of window
            log.info("[Scan] Cache stale (%.0fh) — triggering prefetch",
                     (time.time() - last_pf) / 3600)
            prefetch_all_forecasts()
    except Exception as e:
        log.warning("[Scan] Prefetch error: %s", e)

    # ── 0b. Resolve any open paper trades ────────────────────────────────────
    if dry_run:
        try:
            resolve_open_trades()
        except Exception as e:
            log.warning("[Scan] resolve_open_trades error: %s", e)

    now_utc = datetime.now(timezone.utc)
    today   = now_utc.strftime('%Y-%m-%d')

    summary = {
        "markets_found":  0,
        "markets_parsed": 0,
        "edges":          [],
        "trades":         0,
        "errors":         [],
        "cities_found":   set(),
    }

    # ── 1. Fetch markets ─────────────────────────────────────────────────────
    try:
        markets = get_weather_markets()
    except Exception as e:
        log.error("[Scan] get_weather_markets failed: %s", e)
        summary["errors"].append(str(e))
        return summary

    summary["markets_found"] = len(markets)

    if not markets:
        log.info("[Scan] No active weather markets found")
        return summary

    # ── 2. Check current position count ─────────────────────────────────────
    try:
        weather_positions = get_open_weather_positions()
    except Exception as e:
        log.warning("[Scan] Could not fetch positions: %s", e)
        weather_positions = {}

    n_open = len(weather_positions)
    if n_open >= MAX_ACTIVE_WEATHER_ORDERS:
        log.info("[Scan] At max weather positions (%d/%d) — skip", n_open, MAX_ACTIVE_WEATHER_ORDERS)
        return summary

    # ── 3. Fetch balance ─────────────────────────────────────────────────────
    try:
        balance = get_balance()
    except Exception as e:
        log.warning("[Scan] Could not fetch balance: %s", e)
        balance = 0.0

    # ── 4. Cache daily high forecasts per series ─────────────────────────────
    series_forecasts: dict = {}

    # ── 5. PASS 1: Score all markets, collect candidates above threshold ──────
    candidates = []

    for market in markets:
        ticker = market.get("ticker", "?")
        parsed = market.get("_parsed")

        if parsed is None:
            continue

        if parsed['strike_type'] == 'threshold_ambiguous':
            log.debug("[Scan] %s: ambiguous threshold market — skipping", ticker)
            continue

        summary["markets_parsed"] += 1
        series    = parsed['series']
        city_name = parsed['city_name']
        date      = parsed['date']

        summary["cities_found"].add(city_name)

        if is_blocked(series, date):
            log.info("[Scan] %s: BLOCKED (%s %s resolved NO previously)", ticker, series, date)
            continue

        if ticker in weather_positions:
            log.debug("[Scan] %s: already in positions", ticker)
            continue

        close_time = parsed.get('close_time', '')
        if close_time:
            try:
                ct = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                if ct < now_utc:
                    log.debug("[Scan] %s: market closed at %s", ticker, close_time)
                    continue
            except Exception:
                pass

        # Get bias-corrected consensus forecast
        cache_key = series + "_" + date
        if cache_key not in series_forecasts:
            fc_high, fc_unc, fc_agree, fc_raw, fc_bias = get_bias_corrected_forecast(series, date)
            series_forecasts[cache_key] = (fc_high, fc_unc, fc_agree, fc_raw, fc_bias)
        else:
            fc_high, fc_unc, fc_agree, fc_raw, fc_bias = series_forecasts[cache_key]

        if fc_high is None:
            log.warning("[Scan] %s: no forecast data for %s %s", ticker, series, date)
            continue

        forecast_high = fc_high
        uncertainty   = fc_unc

        # Skip LOW agreement (models disagree >4F std — too noisy)
        if fc_agree == "LOW":
            log.debug("[Scan] %s: model agreement LOW (unc=%.1fF) -- skipping", ticker, uncertainty)
            continue

        market["_forecast_high"] = forecast_high

        st = parsed['strike_type']
        if st == 'between':
            model_prob = calc_prob_in_range(forecast_high, parsed['low'], parsed['high'], uncertainty)
        elif st == 'less':
            model_prob = calc_prob_below(forecast_high, parsed['threshold'], uncertainty)
        elif st == 'greater':
            model_prob = calc_prob_above(forecast_high, parsed['threshold'], uncertainty)
        else:
            continue

        yes_bid    = market.get("yes_bid_dollars")
        yes_ask    = market.get("yes_ask_dollars")
        # Both None = market has no quotes yet, skip
        if yes_bid is None and yes_ask is None:
            log.debug("[Scan] %s: no bid/ask — skipping", ticker)
            continue
        yes_bid  = float(yes_bid) if yes_bid is not None else 0.0
        yes_ask  = float(yes_ask) if yes_ask is not None else 1.0
        # If bid=0 and ask=0 or ask=0.01, use ask only to avoid 0 mid
        if yes_bid == 0.0 and yes_ask <= 0.01:
            log.debug("[Scan] %s: no real liquidity (bid=0 ask=%.2f) — skipping", ticker, yes_ask)
            continue
        kalshi_mid = (yes_bid + yes_ask) / 2.0
        # Floor: mid must be at least 1¢ to be meaningful
        if kalshi_mid < 0.01:
            kalshi_mid = yes_ask if yes_ask > 0 else 0.01

        edge      = model_prob - kalshi_mid
        direction = "YES" if edge > 0 else "NO"
        abs_edge  = abs(edge)

        rng = _range_label(parsed)

        # Margin check: for threshold markets, forecast must be clearly on the
        # winning side. Within 1.5x uncertainty of threshold = too close to call.
        st_check = parsed['strike_type']
        thresh_check = parsed.get('threshold')
        if thresh_check is not None and st_check in ('less', 'greater'):
            min_margin = MIN_FORECAST_MARGIN_MULT * uncertainty
            if st_check == 'less':
                margin = thresh_check - forecast_high  # positive = forecast below threshold (good for YES)
            else:  # greater
                margin = forecast_high - thresh_check  # positive = forecast above threshold (good for YES)
            if direction == 'YES' and margin < min_margin:
                log.debug("[Scan] %s: YES margin %.1fF < min %.1fF (unc=%.1fF) — skip",
                          ticker, margin, min_margin, uncertainty)
                # Still log to edges for tracking, but don't add to candidates
                pass  # falls through to edges.append, skipped in candidates below
            elif direction == 'NO' and margin > -min_margin:
                pass  # NO bets are fine when forecast is near threshold

        bias_str = ("%+.1fF" % fc_bias) if abs(fc_bias) >= 0.5 else ""
        log.info("[Edge] %s | %s %s | fc=%.1f°F%s range=%s unc=%.1f°F agree=%s | "
                 "model=%.0f%% kalshi=%.0f%% edge=%+.0f%% → %s",
                 ticker, city_name, date, forecast_high,
                 (" bias"+bias_str if bias_str else ""),
                 rng, uncertainty, fc_agree,
                 model_prob*100, kalshi_mid*100, edge*100, direction)

        summary["edges"].append({
            "ticker":        ticker,
            "series":        series,
            "city":          city_name,
            "date":          date,
            "range":         rng,
            "forecast_high": round(forecast_high, 1),
            "uncertainty":   uncertainty,
            "model_prob":    round(model_prob, 3),
            "kalshi_prob":   round(kalshi_mid, 3),
            "edge":          round(abs_edge, 3),
            "direction":     direction,
        })

        # Apply margin filter for YES threshold signals
        _skip_yes = False
        if direction == 'YES' and st_check in ('less', 'greater', 'between'):
            _min_m = MIN_FORECAST_MARGIN_MULT * uncertainty
            if st_check == 'less' and thresh_check:
                _margin = thresh_check - forecast_high
            elif st_check == 'greater' and thresh_check:
                _margin = forecast_high - thresh_check
            elif st_check == 'between':
                _lo = parsed.get('low', forecast_high)
                _hi = parsed.get('high', forecast_high)
                _margin = min(forecast_high - _lo, _hi - forecast_high)
            else:
                _margin = _min_m
            if _margin < _min_m:
                _skip_yes = True
                log.debug("[Scan] %s: YES skipped — margin %.1fF < min %.1fF", ticker, _margin, _min_m)

        if abs_edge >= WEATHER_EDGE_THRESHOLD and not _skip_yes:
            candidates.append((abs_edge, edge, market, parsed, model_prob,
                               kalshi_mid, forecast_high, uncertainty, direction))

    # ── 6. PASS 2: Sort by edge descending, execute best signals first ────────
    candidates.sort(key=lambda x: x[0], reverse=True)
    log.info("[Scan] %d candidates above %.0f%% threshold (sorted by edge desc)",
             len(candidates), WEATHER_EDGE_THRESHOLD * 100)

    paper_signals_this_scan = 0

    # ── Experiment tracking: log ALL candidates >=10c with strategy tags ───────
    if dry_run:
        exp_dedup_key = 'experiments'
        exp_dedup_data = _load_dedup()
        today_exp_logged = set(exp_dedup_data.get(exp_dedup_key + '_' + datetime.now(timezone.utc).strftime('%Y-%m-%d'), []))

        for (abs_edge_e, edge_e, market_e, parsed_e, model_prob_e,
             kalshi_mid_e, forecast_high_e, uncertainty_e, direction_e) in candidates:
            ticker_e = market_e.get("ticker", "?")
            if abs_edge_e < 0.10:
                continue
            if ticker_e in today_exp_logged:
                continue
            strategies = classify_strategies(abs_edge_e, parsed_e['series'])
            if strategies:
                log_experiment_signal(market_e, parsed_e, model_prob_e, kalshi_mid_e,
                                      edge_e, direction_e, strategies, balance)
                today_exp_logged.add(ticker_e)

        # Persist experiment dedup
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        exp_dedup_data[exp_dedup_key + '_' + today_str] = list(today_exp_logged)
        _save_dedup(exp_dedup_data)

    for (abs_edge, edge, market, parsed, model_prob, kalshi_mid,
         forecast_high, uncertainty, direction) in candidates:

        ticker    = market.get("ticker", "?")
        city_name = parsed['city_name']

        if dry_run:
            if paper_signals_this_scan >= MAX_PAPER_SIGNALS:
                log.info("[PAPER] Hit MAX_PAPER_SIGNALS (%d) — skipping remaining candidates",
                         MAX_PAPER_SIGNALS)
                break

            if is_paper_logged(ticker):
                log.debug("[PAPER] Already logged %s today — skipping", ticker)
                continue

            mark_paper_logged(ticker)

            price     = kalshi_mid if direction == 'YES' else (1.0 - kalshi_mid)
            price     = max(price, 0.01)
            contracts = max(1, int(math.floor((balance * MAX_POSITION_PCT) / price)))
            cost      = round(contracts * price, 2)

            log_paper_trade(market, parsed, model_prob, kalshi_mid, edge, direction, balance)

            msg = format_paper_msg(
                ticker, parsed, direction, edge, model_prob, kalshi_mid,
                forecast_high, uncertainty, contracts, cost)
            # Paper trades: log only — no Discord post (signals channel = live trades only)
            log.info("[PAPER] %s", msg[:120])
            summary["trades"] += 1
            paper_signals_this_scan += 1

        else:
            if balance <= 0:
                log.warning("[Scan] Zero balance — cannot trade")
                break

            if n_open >= MAX_ACTIVE_WEATHER_ORDERS:
                log.info("[Scan] At max live positions (%d/%d) — best edge already taken",
                         n_open, MAX_ACTIVE_WEATHER_ORDERS)
                break

            try:
                result = execute_weather_trade(market, direction, edge, model_prob, balance)
                if result.get("status") not in ("failed",):
                    summary["trades"] += 1
                    n_open += 1
                    msg = format_live_msg(ticker, parsed, direction, edge, model_prob,
                                         kalshi_mid, forecast_high, result)
                    post_discord(msg, dry_run=False)
                else:
                    log.warning("[Scan] Trade failed for %s: %s", ticker, result.get("error"))
            except Exception as e:
                log.error("[Scan] Execution error for %s: %s", ticker, e)

        time.sleep(0.2)

    # ── Daily summary (paper EOD) ────────────────────────────────────────────
    if dry_run:
        try:
            post_daily_summary()
        except Exception as e:
            log.warning("[Summary] post_daily_summary error: %s", e)

    # ── Summary ───────────────────────────────────────────────────────────────
    summary["cities_found"] = list(summary["cities_found"])

    log.info("=" * 60)
    log.info("SCAN COMPLETE | markets=%d parsed=%d edges=%d trades=%d",
             summary['markets_found'], summary['markets_parsed'],
             len(summary['edges']), summary['trades'])
    if summary["edges"]:
        for e in sorted(summary["edges"], key=lambda x: x["edge"], reverse=True):
            log.info("  %-40s | %s %s | fc=%.1f°F %s | model=%.0f%% kalshi=%.0f%% edge=%.0f%% %s",
                     e['ticker'], e['city'], e['date'], e['forecast_high'], e['range'],
                     e['model_prob']*100, e['kalshi_prob']*100, e['edge']*100, e['direction'])
    log.info("Cities found: %s", ", ".join(sorted(summary['cities_found'])))
    log.info("=" * 60)

    return summary

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────────

def run_scan(post=None, **kwargs):
    """Entry point for firm.py orchestrator. PAPER MODE until Cody approves live."""
    run_weather_scan(dry_run=True)


def main():
    parser = argparse.ArgumentParser(description="Weather Bot v4 — Kalshi daily high temp scanner")
    parser.add_argument("--dry-run",   action="store_true", help="Paper mode — no real orders")
    parser.add_argument("--scan-once", action="store_true", help="Single scan then exit")
    args = parser.parse_args()

    modes = []
    if args.dry_run:   modes.append("DRY-RUN / PAPER MODE")
    if args.scan_once: modes.append("SCAN-ONCE")
    log.info("Weather Bot v4 starting [%s]", ', '.join(modes) if modes else 'CONTINUOUS')
    log.info("Key ID: %s", KEY_ID)
    log.info("Config: edge_threshold=%.0f%% | max_positions=%d | min_volume=%d | position_pct=%.0f%%",
             WEATHER_EDGE_THRESHOLD*100, MAX_ACTIVE_WEATHER_ORDERS, MIN_VOLUME, MAX_POSITION_PCT*100)
    log.info("Series: %s", ', '.join(WEATHER_SERIES))

    if args.scan_once:
        summary = run_weather_scan(dry_run=args.dry_run)

        print("\n" + "=" * 60)
        print("SCAN-ONCE SUMMARY")
        print("=" * 60)
        print("Weather markets found:    %d" % summary['markets_found'])
        print("Markets parsed:           %d" % summary['markets_parsed'])
        print("Cities with markets:      %s" % ', '.join(sorted(summary.get('cities_found', []))))
        print("Edges calculated:         %d" % len(summary['edges']))
        if summary["edges"]:
            print("\nAll edges (sorted by size):")
            for e in sorted(summary["edges"], key=lambda x: x["edge"], reverse=True):
                print("  %-42s | %s %s | fc=%.1f°F %s | model=%.0f%% kalshi=%.0f%% edge=%.0f%% %s" % (
                    e['ticker'], e['city'], e['date'], e['forecast_high'], e['range'],
                    e['model_prob']*100, e['kalshi_prob']*100, e['edge']*100, e['direction']))
        print("\nTrades signalled:         %d" % summary['trades'])
        if summary["errors"]:
            print("\nErrors: %s" % summary['errors'])
        print("=" * 60)
        return

    # ── Continuous loop ───────────────────────────────────────────────────────
    while True:
        try:
            run_weather_scan(dry_run=args.dry_run)
        except Exception as e:
            log.error("[Main] Scan loop error: %s", e, exc_info=True)
        log.info("[Main] Sleeping %ds until next scan", POLL_INTERVAL_SEC)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
