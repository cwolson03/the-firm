#!/usr/bin/env python3
"""
WEATHER MARKET BACKTEST — Stratton Oakmont
==========================================
Two data sources:
  1. PAPER TRADES: /home/cody/stratton/data/weather_paper_trades.json
     - Has real Kalshi prices at signal time (760+ settled trades, Apr 16-21)
     - Use this for actual trading performance analysis
  2. KALSHI FINALIZED + OPEN-METEO (last 2 days with previous_* price fields)
     - Secondary source for recent data

Runs full edge model analysis, outputs comprehensive diagnostics.

Usage:
    python3 backtest.py           # use paper trades + Kalshi API
    python3 backtest.py --refetch # re-fetch from Kalshi API
    python3 backtest.py --paper-only  # use only paper trades file
"""

import os
import sys
import math
import time
import json
import base64
import argparse
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

KALSHI_BASE      = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID = os.getenv("KALSHI_KEY_ID", "")
PRIVATE_KEY_PATH = os.environ.get("KALSHI_KEY_PATH", "")
DATA_DIR         = "/home/cody/stratton/data"

RAW_CACHE_FILE      = os.path.join(DATA_DIR, "backtest_raw.json")
RESULTS_FILE        = os.path.join(DATA_DIR, "backtest_results.json")
PAPER_TRADES_FILE   = os.path.join(DATA_DIR, "weather_paper_trades.json")
CACHE_TTL_SEC       = 86400

BACKTEST_DAYS    = 30
EDGE_MIN         = 0.10
EDGE_THRESHOLD   = 0.15
TRADE_COST       = 6.90
UNCERTAINTY      = 3.5

SERIES = {
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

# ─────────────────────────────────────────────────────────────────────────────
# RSA-PSS AUTH
# ─────────────────────────────────────────────────────────────────────────────

_private_key = None

def _load_private_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    with open(PRIVATE_KEY_PATH, "rb") as f:
        _private_key = load_pem_private_key(f.read(), password=None)
    return _private_key

def get_auth_headers(method: str, path: str) -> dict:
    ts  = str(int(time.time() * 1000))
    key = _load_private_key()
    msg = (ts + method.upper() + "/trade-api/v2" + path).encode()
    sig = key.sign(msg, padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.DIGEST_LENGTH
    ), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type":            "application/json",
    }

def kalshi_get(path: str, params: dict = None) -> dict:
    headers = get_auth_headers("GET", path)
    r = requests.get(KALSHI_BASE + path, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

# ─────────────────────────────────────────────────────────────────────────────
# EDGE MODEL (exact copy from spec)
# ─────────────────────────────────────────────────────────────────────────────

def normal_cdf(z):
    return (1 + math.erf(z / math.sqrt(2))) / 2

def calc_prob_in_range(forecast, low, high, uncertainty=3.5):
    sigma = max(uncertainty, 0.5)
    return max(0.01, min(0.99, normal_cdf((high+0.5-forecast)/sigma) - normal_cdf((low-0.5-forecast)/sigma)))

def calc_prob_above(forecast, threshold, uncertainty=3.5):
    sigma = max(uncertainty, 0.5)
    return max(0.01, min(0.99, 1.0 - normal_cdf((threshold+0.5-forecast)/sigma)))

def calc_prob_below(forecast, threshold, uncertainty=3.5):
    sigma = max(uncertainty, 0.5)
    return max(0.01, min(0.99, normal_cdf((threshold-0.5-forecast)/sigma)))

# ─────────────────────────────────────────────────────────────────────────────
# TICKER PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_ticker(ticker: str, market_data: dict = None) -> Optional[dict]:
    """Parse Kalshi weather ticker. Returns dict or None."""
    ticker_upper = ticker.upper()
    series = None
    for s in SERIES:
        if ticker_upper.startswith(s + "-"):
            series = s
            break
    if not series:
        return None

    rest = ticker_upper[len(series) + 1:]
    m = re.match(r'^(\d{2}[A-Z]{3}\d{2})-(.+)$', rest)
    if not m:
        return None
    date_part, strike_part = m.group(1), m.group(2)

    try:
        dt       = datetime.strptime(date_part, "%y%b%d")
        date_str = dt.strftime("%Y-%m-%d")
    except ValueError:
        return None

    if market_data:
        strike_type  = market_data.get("strike_type", "")
        floor_strike = market_data.get("floor_strike")
        cap_strike   = market_data.get("cap_strike")

        if strike_type == "between" and floor_strike is not None and cap_strike is not None:
            return {
                'series': series, 'date': date_str, 'strike_type': 'between',
                'low': float(floor_strike), 'high': float(cap_strike),
            }
        elif strike_type in ("less", "greater") and (cap_strike or floor_strike) is not None:
            threshold = float(cap_strike) if strike_type == "less" else float(floor_strike)
            return {
                'series': series, 'date': date_str, 'strike_type': strike_type,
                'threshold': threshold, 'low': None, 'high': None,
            }
        return None

    # Suffix parsing
    if strike_part.startswith("B"):
        try:
            mid  = float(strike_part[1:])
            low  = math.floor(mid)
            return {
                'series': series, 'date': date_str, 'strike_type': 'between',
                'low': float(low), 'high': float(low + 1),
            }
        except ValueError:
            return None

    if strike_part.startswith("T"):
        try:
            threshold = float(strike_part[1:])
            return {
                'series': series, 'date': date_str,
                'strike_type': 'threshold_ambiguous',
                'threshold': threshold, 'low': None, 'high': None,
            }
        except ValueError:
            return None

    return None

# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADES → BACKTEST RECORDS
# ─────────────────────────────────────────────────────────────────────────────

def load_paper_trades_as_signals() -> list:
    """
    Load paper trades file. Convert settled trades into backtest signal records.
    These have real Kalshi prices captured at signal time.
    Edge in paper trades = model_prob - kalshi_prob
      positive = YES signal, negative = NO signal
    """
    with open(PAPER_TRADES_FILE) as f:
        trades = json.load(f)

    signals = []
    for t in trades:
        if t['status'] not in ('WIN', 'LOSS'):
            continue  # skip OPEN trades

        series     = t.get('series', '')
        if series not in SERIES:
            continue

        ticker     = t['ticker']
        date       = t['date']
        direction  = t['direction']       # 'YES' or 'NO'
        strike_type = t.get('strike_type', '')
        low        = t.get('low')
        high       = t.get('high')
        threshold  = t.get('threshold')
        forecast   = t.get('forecast_high')
        kalshi_mid = t.get('kalshi_prob')
        model_prob = t.get('model_prob')
        edge_raw   = t.get('edge', 0)     # signed: positive=YES, negative=NO
        won        = t['status'] == 'WIN'
        result     = t.get('result', '')

        if kalshi_mid is None or model_prob is None:
            continue

        abs_edge = abs(edge_raw)
        if abs_edge < EDGE_MIN:
            continue

        # Compute margin
        margin = None
        if strike_type == 'between' and low is not None and high is not None:
            if forecast is not None:
                margin = min(abs(forecast - low), abs(forecast - high))
        elif threshold is not None and forecast is not None:
            margin = abs(forecast - threshold)

        signals.append({
            'ticker':       ticker,
            'series':       series,
            'city':         SERIES[series]['name'],
            'date':         date,
            'strike_type':  strike_type,
            'low':          low,
            'high':         high,
            'threshold':    threshold,
            'forecast':     forecast,
            'model_prob':   round(model_prob, 4),
            'kalshi_mid':   round(kalshi_mid, 4),
            'edge':         round(abs_edge, 4),
            'direction':    direction,
            'result':       result,
            'won':          won,
            'margin':       round(margin, 2) if margin is not None else None,
            'source':       'paper_trade',
        })

    return signals

# ─────────────────────────────────────────────────────────────────────────────
# KALSHI FETCH (for markets with real previous_* prices)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_kalshi_finalized(series_list: list, days: int = 30) -> list:
    """
    Paginate events for each series, collect finalized between-markets
    that have real previous_yes_bid/ask prices (not 0/1 artifacts).
    """
    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days + 2)
    markets    = []
    seen       = set()

    for series in series_list:
        print(f"  {series}...", end="", flush=True)
        cursor     = None
        stop_early = False

        while not stop_early:
            params = {
                "series_ticker":       series,
                "with_nested_markets": "true",
                "limit":               100,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                data = kalshi_get("/events", params=params)
            except Exception as e:
                print(f" ERR:{e}", flush=True)
                break

            events = data.get("events", [])
            if not events:
                break

            for event in events:
                event_ticker = event.get("event_ticker", "")
                event_dt     = None
                m = re.search(r'(\d{2}[A-Z]{3}\d{2})', event_ticker.upper())
                if m:
                    try:
                        event_dt = datetime.strptime(m.group(1), "%y%b%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                if event_dt and event_dt < start_date:
                    stop_early = True
                    break

                for mkt in event.get("markets", []):
                    ticker = mkt.get("ticker", "")
                    if not ticker or ticker in seen:
                        continue
                    if mkt.get("status") != "finalized":
                        continue
                    result = mkt.get("result", "")
                    if result not in ("yes", "no"):
                        continue

                    parsed = parse_ticker(ticker, market_data=mkt)
                    if parsed is None or parsed['strike_type'] != 'between':
                        continue

                    # Only collect markets with real price data
                    def gp(field):
                        v = mkt.get(field)
                        try:
                            return float(v) if v is not None else None
                        except (TypeError, ValueError):
                            return None

                    yes_bid = gp("previous_yes_bid_dollars")
                    yes_ask = gp("previous_yes_ask_dollars")

                    # Filter out post-settlement artifacts (0/1 spread = no real price)
                    if yes_bid is None or yes_ask is None:
                        continue
                    if yes_bid == 0.0 and yes_ask == 1.0:
                        continue
                    # Also filter extreme prices
                    kalshi_mid = (yes_bid + yes_ask) / 2.0
                    if kalshi_mid <= 0.01 or kalshi_mid >= 0.99:
                        continue

                    actual_temp = gp("expiration_value")

                    markets.append({
                        'ticker':      ticker,
                        'series':      parsed['series'],
                        'date':        parsed['date'],
                        'strike_type': parsed['strike_type'],
                        'low':         parsed['low'],
                        'high':        parsed['high'],
                        'yes_bid':     yes_bid,
                        'yes_ask':     yes_ask,
                        'kalshi_mid':  round(kalshi_mid, 4),
                        'result':      result,
                        'actual_temp': actual_temp,
                        'volume':      mkt.get("volume_fp"),
                    })
                    seen.add(ticker)

            cursor = data.get("cursor")
            if not cursor or stop_early:
                break
            time.sleep(0.1)

        print(f" {sum(1 for m in markets if m['series'] == series)}", flush=True)
        time.sleep(0.1)

    return markets

# ─────────────────────────────────────────────────────────────────────────────
# OPEN-METEO HISTORICAL FORECAST
# ─────────────────────────────────────────────────────────────────────────────

def fetch_openmeteo_forecasts(series_list: list, start_date: str, end_date: str) -> dict:
    """Returns {series: {date: temp_f}} from GFS historical forecast API."""
    forecasts = {}
    for series in series_list:
        city = SERIES[series]
        print(f"  {city['name']}...", end="", flush=True)
        try:
            r = requests.get(
                "https://historical-forecast-api.open-meteo.com/v1/forecast",
                params={
                    "latitude":         city['lat'],
                    "longitude":        city['lon'],
                    "daily":            "temperature_2m_max",
                    "temperature_unit": "fahrenheit",
                    "start_date":       start_date,
                    "end_date":         end_date,
                    "models":           "gfs_seamless",
                    "timezone":         "UTC",
                },
                timeout=20,
            )
            r.raise_for_status()
            data  = r.json()
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            day_map = {d: float(t) for d, t in zip(dates, temps) if t is not None}
            forecasts[series] = day_map
            print(f" {len(day_map)} days", flush=True)
        except Exception as e:
            print(f" ERR:{e}", flush=True)
            forecasts[series] = {}
        time.sleep(0.05)
    return forecasts

# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

def load_raw_cache():
    if not os.path.exists(RAW_CACHE_FILE):
        return None
    age = time.time() - os.path.getmtime(RAW_CACHE_FILE)
    if age > CACHE_TTL_SEC:
        print(f"  Cache {age/3600:.1f}h old — will re-fetch")
        return None
    print(f"  Cache {age/3600:.1f}h old — reusing")
    with open(RAW_CACHE_FILE) as f:
        return json.load(f)

def save_raw_cache(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RAW_CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved → {RAW_CACHE_FILE}")

# ─────────────────────────────────────────────────────────────────────────────
# KALSHI SIGNALS FROM RAW MARKETS + OPEN-METEO
# ─────────────────────────────────────────────────────────────────────────────

def markets_to_signals(markets: list, forecasts: dict) -> list:
    """
    Convert raw Kalshi between-markets + Open-Meteo forecasts into signal records.
    These use the model against real Kalshi prices.
    """
    signals = []
    for mkt in markets:
        series     = mkt['series']
        date       = mkt['date']
        low        = mkt['low']
        high       = mkt['high']
        result     = mkt['result']
        kalshi_mid = mkt['kalshi_mid']

        forecast = forecasts.get(series, {}).get(date)
        if forecast is None:
            continue

        model_prob = calc_prob_in_range(forecast, low, high, UNCERTAINTY)
        edge_yes   = model_prob - kalshi_mid
        abs_edge   = abs(edge_yes)

        if abs_edge < EDGE_MIN:
            continue

        direction = 'YES' if edge_yes > 0 else 'NO'
        won       = (direction == 'YES' and result == 'yes') or \
                    (direction == 'NO'  and result == 'no')
        margin    = min(abs(forecast - low), abs(forecast - high))

        signals.append({
            'ticker':       mkt['ticker'],
            'series':       series,
            'city':         SERIES[series]['name'],
            'date':         date,
            'strike_type':  'between',
            'low':          low,
            'high':         high,
            'threshold':    None,
            'forecast':     round(forecast, 1),
            'actual_temp':  mkt.get('actual_temp'),
            'model_prob':   round(model_prob, 4),
            'kalshi_mid':   round(kalshi_mid, 4),
            'edge':         round(abs_edge, 4),
            'direction':    direction,
            'result':       result,
            'won':          won,
            'margin':       round(margin, 2),
            'source':       'kalshi_api',
        })

    return signals

# ─────────────────────────────────────────────────────────────────────────────
# COMBINE & DEDUPLICATE
# ─────────────────────────────────────────────────────────────────────────────

def merge_signals(paper_signals: list, api_signals: list) -> list:
    """Merge paper trade signals with API signals, deduplicating by ticker."""
    seen    = {s['ticker'] for s in paper_signals}
    merged  = list(paper_signals)
    added   = 0
    for s in api_signals:
        if s['ticker'] not in seen:
            merged.append(s)
            seen.add(s['ticker'])
            added += 1
    print(f"  Paper signals: {len(paper_signals)}, API signals added: {added}, total: {len(merged)}")
    return merged

# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def pct(w, n):
    return f"{100*w/n:.0f}%" if n else "N/A"

def wr_label(records):
    wins = sum(1 for r in records if r['won'])
    n    = len(records)
    return f"{wins}/{n}  {pct(wins, n)}"

def edge_bucket(e):
    if e < 0.15:  return "10-15¢"
    if e < 0.20:  return "15-20¢"
    if e < 0.25:  return "20-25¢"
    if e < 0.30:  return "25-30¢"
    return ">30¢"

def margin_bucket(m):
    if m is None: return "N/A"
    if m < 2:  return "<2°F"
    if m < 4:  return "2-4°F"
    if m < 6:  return "4-6°F"
    return ">6°F"

def find_optimal_threshold(signals: list, min_n: int = 20) -> tuple:
    best_thr, best_rate, best_n = None, 0.0, 0
    for cent in range(10, 51):
        thr    = cent / 100.0
        subset = [s for s in signals if s['edge'] >= thr]
        if len(subset) < min_n:
            continue
        wins = sum(1 for s in subset if s['won'])
        rate = wins / len(subset)
        if rate > best_rate:
            best_rate, best_thr, best_n = rate, thr, len(subset)
    return best_thr, best_rate, best_n

def print_results(all_10: list, all_15: list, total_raw: int, start_date: str, end_date: str):
    print()
    print("=" * 66)
    print(f"=== BACKTEST RESULTS ({start_date} → {end_date}) ===")
    print(f"  Raw market data points:   {total_raw}")
    print(f"Total signals (>=10¢ edge): {len(all_10)}")
    print(f"Total signals (>=15¢ edge): {len(all_15)}")
    dates = sorted(set(s['date'] for s in all_10))
    if dates:
        print(f"  Dates covered: {dates[0]} → {dates[-1]} ({len(dates)} trading days)")
    print()

    # ── BY DIRECTION ─────────────────────────────────────────────────────────
    print("BY DIRECTION (>=15¢):")
    for d in ('YES', 'NO'):
        subset = [s for s in all_15 if s['direction'] == d]
        print(f"  {d}: {wr_label(subset)}")
    print()

    # ── BY EDGE BUCKET ───────────────────────────────────────────────────────
    print("BY EDGE BUCKET (all signals >=10¢):")
    for b in ["10-15¢", "15-20¢", "20-25¢", "25-30¢", ">30¢"]:
        subset = [s for s in all_10 if edge_bucket(s['edge']) == b]
        print(f"  {b}: {wr_label(subset)}")
    print()

    # ── BY CITY ───────────────────────────────────────────────────────────────
    print("BY CITY (>=15¢ threshold):")
    city_names = sorted([(s, SERIES[s]['name']) for s in SERIES], key=lambda x: x[1])
    for series, name in city_names:
        subset = [s for s in all_15 if s['series'] == series]
        if subset:
            print(f"  {name:<20}: {wr_label(subset)}")
    print()

    # ── YES SIGNALS — MARGIN ANALYSIS ────────────────────────────────────────
    print("YES SIGNALS — MARGIN DISTANCE ANALYSIS (>=15¢):")
    yes_sigs = [s for s in all_15 if s['direction'] == 'YES']
    if yes_sigs:
        for mb in ("<2°F", "2-4°F", "4-6°F", ">6°F"):
            subset = [s for s in yes_sigs if margin_bucket(s.get('margin')) == mb]
            print(f"  {mb:<25}: {wr_label(subset)}")
    else:
        print("  (no YES signals)")
    print()

    # ── OPTIMAL THRESHOLD ────────────────────────────────────────────────────
    opt_thr, opt_rate, opt_n = find_optimal_threshold(all_10, min_n=20)
    if opt_thr is not None:
        print(f"OPTIMAL THRESHOLD: {int(opt_thr*100)}¢ (hit rate {opt_rate:.0%} with n={opt_n} signals)")
    else:
        print("OPTIMAL THRESHOLD: N/A (not enough data for n>=20)")
    print()

    # ── P&L ──────────────────────────────────────────────────────────────────
    print(f"SIMULATED P&L (15¢ threshold, ${TRADE_COST}/trade):")
    print(f"  Gross trades: {len(all_15)}")
    win_pnl = loss_pnl = 0.0
    for s in all_15:
        if s['won']:
            if s['direction'] == 'YES':
                win_pnl += TRADE_COST * (1 - s['kalshi_mid'])
            else:
                win_pnl += TRADE_COST * s['kalshi_mid']
        else:
            if s['direction'] == 'YES':
                loss_pnl -= TRADE_COST * s['kalshi_mid']
            else:
                loss_pnl -= TRADE_COST * (1 - s['kalshi_mid'])
    net = win_pnl + loss_pnl
    print(f"  Win: ${win_pnl:.2f} | Loss: ${loss_pnl:.2f}")
    print(f"  Net: ${net:.2f}")
    print("=" * 66)

    # ── EXTENDED DIAGNOSIS ───────────────────────────────────────────────────
    print()
    print("─" * 66)
    print("DIAGNOSIS — WHY YES BETS ARE BROKEN:")
    yes_10 = [s for s in all_10 if s['direction'] == 'YES']
    no_10  = [s for s in all_10 if s['direction'] == 'NO']
    print(f"  YES (>=10¢): {wr_label(yes_10)}")
    print(f"  NO  (>=10¢): {wr_label(no_10)}")
    print()

    if yes_10:
        avg_fc     = sum(s['forecast'] for s in yes_10 if s['forecast']) / max(1, sum(1 for s in yes_10 if s['forecast']))
        avg_mid    = sum(s['kalshi_mid'] for s in yes_10) / len(yes_10)
        avg_model  = sum(s['model_prob'] for s in yes_10) / len(yes_10)
        avg_edge   = sum(s['edge'] for s in yes_10) / len(yes_10)
        print(f"  YES avg: forecast={avg_fc:.1f}°F, model={avg_model:.2f}, kalshi={avg_mid:.2f}, edge={avg_edge:.2f}")

        # YES signal detail — what are we betting on?
        print(f"  YES signal examples:")
        for s in sorted(yes_10, key=lambda x: -x['edge'])[:5]:
            print(f"    {s['ticker']}: model={s['model_prob']:.2f} vs kalshi={s['kalshi_mid']:.2f}, "
                  f"fc={s['forecast']}°F, bracket={s.get('low')}-{s.get('high') or s.get('threshold')}, "
                  f"result={s['result']}, won={s['won']}")

    if no_10:
        avg_fc    = sum(s['forecast'] for s in no_10 if s['forecast']) / max(1, sum(1 for s in no_10 if s['forecast']))
        avg_mid   = sum(s['kalshi_mid'] for s in no_10) / len(no_10)
        avg_model = sum(s['model_prob'] for s in no_10) / len(no_10)
        avg_edge  = sum(s['edge'] for s in no_10) / len(no_10)
        print(f"  NO  avg: forecast={avg_fc:.1f}°F, model={avg_model:.2f}, kalshi={avg_mid:.2f}, edge={avg_edge:.2f}")

    # ── NO SIGNAL — WHAT'S DRIVING WINS? ────────────────────────────────────
    print()
    print("NO SIGNAL REGIME (kalshi_mid distribution, >=15¢):")
    no_15 = [s for s in all_15 if s['direction'] == 'NO']
    price_buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    for lo, hi in price_buckets:
        subset = [s for s in no_15 if lo <= s['kalshi_mid'] < hi]
        if subset:
            wins = sum(1 for s in subset if s['won'])
            avg_model = sum(s['model_prob'] for s in subset) / len(subset)
            print(f"  kalshi {lo:.0%}-{hi:.0%}: {wins}/{len(subset)} ({pct(wins,len(subset))}), avg_model={avg_model:.2f}")

    # ── MODEL CALIBRATION ───────────────────────────────────────────────────
    print()
    print("MODEL CALIBRATION (>=10¢ signals, model_prob vs actual YES rate):")
    prob_buckets = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
                    (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    for lo, hi in prob_buckets:
        subset = [s for s in all_10 if lo <= s['model_prob'] < hi]
        if len(subset) >= 5:
            actual_yes = sum(1 for s in subset if s['result'] == 'yes')
            avg_prob   = sum(s['model_prob'] for s in subset) / len(subset)
            print(f"  {lo:.0%}-{hi:.0%}: n={len(subset):<4} avg_model={avg_prob:.2f}, "
                  f"actual_yes={pct(actual_yes, len(subset))}")

    # ── FORECAST ACCURACY ────────────────────────────────────────────────────
    with_actual = [s for s in all_10 if s.get('actual_temp') is not None and s.get('forecast') is not None]
    if with_actual:
        print()
        print("FORECAST ACCURACY (GFS vs actual NWS temp, on signaled markets):")
        errors = [s['forecast'] - s['actual_temp'] for s in with_actual]
        mae    = sum(abs(e) for e in errors) / len(errors)
        bias   = sum(errors) / len(errors)
        w1     = sum(1 for e in errors if abs(e) <= 1)
        w3     = sum(1 for e in errors if abs(e) <= 3)
        w5     = sum(1 for e in errors if abs(e) <= 5)
        print(f"  n={len(errors)}")
        print(f"  Bias: {bias:+.2f}°F  MAE: {mae:.2f}°F")
        print(f"  Within 1°F: {pct(w1, len(errors))}  Within 3°F: {pct(w3, len(errors))}  Within 5°F: {pct(w5, len(errors))}")

    # ── YES FAILURE ROOT CAUSE ───────────────────────────────────────────────
    print()
    print("ROOT CAUSE ANALYSIS — YES FAILURES:")
    if yes_10:
        # Check: when YES signal fires, what's the actual outcome distribution
        yes_actual_yes = sum(1 for s in yes_10 if s['result'] == 'yes')
        yes_actual_no  = sum(1 for s in yes_10 if s['result'] == 'no')
        print(f"  YES signals → actual yes: {yes_actual_yes}/{len(yes_10)}  actual no: {yes_actual_no}/{len(yes_10)}")

        # kalshi_mid range for YES signals
        mids = [s['kalshi_mid'] for s in yes_10]
        print(f"  kalshi_mid range: {min(mids):.2f} - {max(mids):.2f}  avg: {sum(mids)/len(mids):.2f}")

        # model_prob range
        mps = [s['model_prob'] for s in yes_10]
        print(f"  model_prob range: {min(mps):.2f} - {max(mps):.2f}  avg: {sum(mps)/len(mps):.2f}")

        # For YES signals where model is HIGH (>0.5), what happens?
        high_conf = [s for s in yes_10 if s['model_prob'] > 0.5]
        if high_conf:
            wins = sum(1 for s in high_conf if s['won'])
            print(f"  YES with model_prob >0.5: {wins}/{len(high_conf)}")

        # Strike type breakdown
        b_sigs = [s for s in yes_10 if s['strike_type'] == 'between']
        t_sigs = [s for s in yes_10 if s['strike_type'] in ('less', 'greater', 'threshold_ambiguous')]
        if b_sigs:
            w = sum(1 for s in b_sigs if s['won'])
            print(f"  YES on B (between) markets: {w}/{len(b_sigs)} ({pct(w, len(b_sigs))})")
        if t_sigs:
            w = sum(1 for s in t_sigs if s['won'])
            print(f"  YES on T (threshold) markets: {w}/{len(t_sigs)} ({pct(w, len(t_sigs))})")
    else:
        print("  No YES signals in dataset")

    print()

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refetch",    action="store_true", help="Re-fetch Kalshi API data")
    parser.add_argument("--paper-only", action="store_true", help="Use only paper trades file")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    now        = datetime.now(timezone.utc)
    end_date   = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=BACKTEST_DAYS + 1)).strftime("%Y-%m-%d")

    all_signals = []

    # ── SOURCE 1: PAPER TRADES ───────────────────────────────────────────────
    print("\n[1/3] Loading paper trades...")
    if os.path.exists(PAPER_TRADES_FILE):
        paper_signals = load_paper_trades_as_signals()
        dates = sorted(set(s['date'] for s in paper_signals))
        print(f"  Loaded {len(paper_signals)} settled signals ({dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'})")
    else:
        paper_signals = []
        print("  Paper trades file not found")

    # ── SOURCE 2: KALSHI API (recent with real prices) ───────────────────────
    api_signals = []
    if not args.paper_only:
        raw = None
        if not args.refetch:
            print("\n[2/3] Checking Kalshi API cache...")
            raw = load_raw_cache()

        if raw is None:
            print(f"\n[2/3] Fetching Kalshi finalized markets ({start_date} → {end_date})...")
            series_list = list(SERIES.keys())
            markets     = fetch_kalshi_finalized(series_list, days=BACKTEST_DAYS)
            print(f"  Found {len(markets)} real-price finalized markets")

            print(f"\n  Fetching Open-Meteo forecasts ({start_date} → {end_date})...")
            forecasts = fetch_openmeteo_forecasts(series_list, start_date, end_date)

            raw = {
                'fetched_at': now.isoformat(),
                'start_date': start_date,
                'end_date':   end_date,
                'markets':    markets,
                'forecasts':  forecasts,
            }
            save_raw_cache(raw)
        else:
            markets   = raw['markets']
            forecasts = raw['forecasts']
            print(f"  Using cached {len(markets)} markets")

        api_signals = markets_to_signals(markets, forecasts)
        print(f"  Generated {len(api_signals)} signals from Kalshi API data")
    else:
        print("\n[2/3] Skipped (--paper-only mode)")

    # ── MERGE ────────────────────────────────────────────────────────────────
    print("\n[3/3] Running analysis...")
    if not args.paper_only:
        print("  Merging sources...")
        all_signals = merge_signals(paper_signals, api_signals)
    else:
        all_signals = paper_signals

    total_raw = len(set(s['ticker'] for s in all_signals))

    # Split by threshold
    all_10 = [s for s in all_signals if s['edge'] >= EDGE_MIN]
    all_15 = [s for s in all_signals if s['edge'] >= EDGE_THRESHOLD]

    # Save results
    results = {
        'run_at':       now.isoformat(),
        'start_date':   start_date,
        'end_date':     end_date,
        'total_raw':    total_raw,
        'signals_10':   all_10,
        'signals_15':   all_15,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Saved → {RESULTS_FILE}")

    print_results(all_10, all_15, total_raw, start_date, end_date)


if __name__ == "__main__":
    main()
