#!/usr/bin/env python3
"""
BRAD — Kalshi Sports Prediction Market Stink Bid Bot
======================================================
Stratton Oakmont prediction market intelligence — sports division.

STRATEGIES (parallel):
  S1 — Live Game Winner Stink Bid (30% below ask, favorites > 55¢, max 5 bids)
  S2 — Spread/Prop Market Stink Bid (25% below ask, favorites > 60¢, max 3 bids)
  S3 — Tournament/Series Outright (35% below ask, favorites > 70¢, max 2 bids)

  Total max capital: 25% of balance (same as before, split across 3 buckets).

HOW IT WORKS:
  1. Scan all Kalshi Sports markets every 30 min
  2. Run all 3 strategies in parallel on different market types
  3. Place limit BUY orders at strategy-specific discounts below current ask
  4. Cancel and re-place every 15 min as prices shift
  5. If filled: hold to expiration — never sell early
  6. Each strategy has independent capital cap + max bids

Usage:
    python3 brad.py                         # paper mode continuous (default — safe)
    python3 brad.py --live                  # LIVE mode — real orders
    python3 brad.py --scan-once             # paper single scan + exit
    python3 brad.py --live --scan-once      # live single scan + exit
    python3 brad.py --dry-run               # stdout only, no orders, no Discord
    python3 brad.py --paper                 # explicit paper mode (same as default)

firm.py calling run_scan() → always LIVE (Cody approved it by wiring Brad in).

Auth: RSA-PSS (same as Donnie). Paths auto-detect Atlas vs local.
"""

import os
import sys
import json
import math
import time
import base64
import logging
import argparse
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADING NOTE
# ─────────────────────────────────────────────────────────────────────────────
# Default: paper mode (--paper or no --live flag → no real orders placed).
# Pass --live to execute real orders from CLI.
# run_scan() called by firm.py always goes LIVE — firm.py = Cody approved it.

import requests

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# ============================================================
# PAPER MODE CONSTANT — NEVER CHANGE WITHOUT CODY APPROVAL
# ============================================================
BRAD_PAPER_MODE = True  # Set to False only when Cody explicitly authorizes live trading

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID      = "28aebab3-8694-46bc-95f1-2d37d9e9266e"

# Paths — auto-detect Atlas (cody) vs local (stratton)
if os.path.exists("/home/cody/stratton"):
    PRIVATE_KEY_PATH = "/home/cody/stratton/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/cody/stratton/config/bot-tokens.env"
    LOG_PATH         = "/home/cody/stratton/logs/brad.log"
else:
    PRIVATE_KEY_PATH = "/home/stratton/.openclaw/workspace/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/stratton/.openclaw/workspace/config/bot-tokens.env"
    LOG_PATH         = "/home/stratton/.openclaw/workspace/logs/brad.log"

BRAD_DISCORD_CH  = 1491861968355590242   # #sports-signals
BRAD_DISCORD_CH2 = 1491861971635540108   # #promo-tracker

# ── Ticker blocklist (secondary guard for non-sports markets) ─────────────────
BLOCKED_TICKER_PREFIXES = ['KXAAAGASW', 'KXTRUMPSAY', 'KXTRUMPMENTION', 'KXTRUMPUFC']

# ── Strategy definitions ──────────────────────────────────────────────────────
# S1: Live Game Winners
STINK_BID_DISCOUNT_S1 = 0.20    # 20% below current mid (reduced from 30% — more fills, still strong edge)
MIN_FAVORITE_PRICE_S1 = 0.55    # only bid where favorite > 55¢
MAX_ACTIVE_BIDS_S1    = 5       # max 5 open stink bids for S1

# S2: Spread/Prop Markets
STINK_BID_DISCOUNT_S2 = 0.20    # 20% below current mid (reduced from 25% — matches S1 for consistency)
MIN_FAVORITE_PRICE_S2 = 0.60    # only bid where favorite > 60¢
MAX_ACTIVE_BIDS_S2    = 3       # max 3 open stink bids for S2

# S3: Tournament/Outright Markets
STINK_BID_DISCOUNT_S3 = 0.35    # 35% below current mid (more aggressive, longer time horizon)
MIN_FAVORITE_PRICE_S3 = 0.70    # only bid where favorite > 70¢ (clear frontrunners only)
MAX_ACTIVE_BIDS_S3    = 2       # max 2 open stink bids for S3
MAX_DAYS_UNTIL_CLOSE_S3 = 30    # tournament markets can close up to 30 days out

# Tournament tickers for S3
TOURNAMENT_TICKER_PREFIXES = [
    'KXUCLRO4',        # UEFA Champions League
    'KXNBAPLAYOFF',    # NBA Playoffs
    'KXMLBSERIES',     # MLB Series
    'KXNHLPLAYOFF',    # NHL Playoffs
    'KXUFC',           # UFC
    'KXMASTERS',       # The Masters
]

# Spread/Prop keywords for S2
SPREAD_PROP_KEYWORDS = ['SPREAD', 'TOTAL', 'HR', 'KS', 'STRIKEOUT']

# Legacy compat (used by refresh/shared logic)
STINK_BID_DISCOUNT     = STINK_BID_DISCOUNT_S1

def get_s1_discount(favorite_price: float) -> float:
    """
    Two-tier S1 discount:
    - Strong favorites (>70¢): 20% off — they rarely dump hard
    - Weaker favorites (55-70¢): 25% off — more volatile, slightly deeper
    """
    if favorite_price >= 0.70:
        return 0.20
    return 0.25
MIN_FAVORITE_PRICE     = MIN_FAVORITE_PRICE_S1
MAX_ACTIVE_BIDS        = MAX_ACTIVE_BIDS_S1 + MAX_ACTIVE_BIDS_S2 + MAX_ACTIVE_BIDS_S3  # total

# ── Capital allocation across strategies ─────────────────────────────────────
STRATEGY_CAPS = {
    "s1": 0.12,   # 12% of balance for live game winners
    "s2": 0.08,   # 8% of balance for spread/prop markets
    "s3": 0.05,   # 5% of balance for tournament outright
    # total max: 25%
}
BRAD_MAX_BALANCE_PCT   = 0.25    # Brad's hard cap: max 25% of account balance in stink bids
MAX_STINK_BID_PCT      = 0.02    # max 2% of balance per individual stink bid
MAX_TOTAL_EXPOSURE_PCT = 0.20    # never exceed 20% of balance in total exposure (guardrail)

STINK_BID_REFRESH_SEC = 900     # cancel and re-place every 15 min

LIVE_GAME_PRIORITY    = True    # Sort stink bid targets: live games first, then pre-game, then other

# ── Grok X Search ────────────────────────────────────────────────────────────
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
GROK_MAX_CALLS_PER_SCAN = 5   # max Grok calls per scan (cost control)

# ── Odds API (The Odds API — sportsbook line comparison) ──────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_MAX_CALLS_PER_SCAN = 10  # free tier: 500 requests/month; cap per scan

# ── Scanning ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC    = 1800    # full scan every 30 min
REFRESH_INTERVAL_SEC = 900     # refresh/replace stink bids every 15 min
MAX_DAYS_UNTIL_CLOSE = 7       # S1/S2: only bid on markets closing within 7 days
MIN_MARKET_VOLUME    = 50      # minimum volume to consider a market (lowered for IPL/tennis)

# ── Paper trading ─────────────────────────────────────────────────────────────
if os.path.exists("/home/cody/stratton"):
    PAPER_TRADES_FILE = "/home/cody/stratton/data/brad_paper_trades.json"
else:
    PAPER_TRADES_FILE = "/home/stratton/.openclaw/workspace/data/brad_paper_trades.json"

# ── ESPN scoreboard URLs ──────────────────────────────────────────────────────
ESPN_SCOREBOARDS = {
    "nba":     "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "mlb":     "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    "nhl":     "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "nfl":     "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    # cricket: ESPN cricket API is dead (404). IPL timing uses ticker time-proxy instead.
}

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

log = logging.getLogger("brad")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh  = logging.FileHandler(LOG_PATH)
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)

# ─────────────────────────────────────────────────────────────────────────────
# AUTH — RSA-PSS (identical to Donnie)
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


def kalshi_delete(path: str) -> bool:
    url     = KALSHI_BASE + path
    headers = get_auth_headers("DELETE", "/trade-api/v2" + path)
    try:
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code in (200, 204):
            return True
        log.warning(f"Kalshi DELETE {path} → {r.status_code}: {r.text[:150]}")
        return False
    except Exception as e:
        log.error(f"Kalshi DELETE {path} error: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# MARKET HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_mid(m: dict) -> float:
    ask = float(m.get("yes_ask") or m.get("yes_ask_dollars") or 1.0)
    bid = float(m.get("yes_bid") or m.get("yes_bid_dollars") or 0.0)
    # Kalshi API returns prices in cents (0–99) OR dollars (0.0–1.0)
    # Normalize to dollars if values look like cents
    if ask > 1.0:
        ask = ask / 100.0
    if bid > 1.0:
        bid = bid / 100.0
    return (ask + bid) / 2.0


def get_yes_ask(m: dict) -> float:
    ask = float(m.get("yes_ask") or m.get("yes_ask_dollars") or 1.0)
    if ask > 1.0:
        ask = ask / 100.0
    return ask


def get_volume(m: dict) -> float:
    return float(m.get("volume") or m.get("volume_fp") or 0.0)


def days_until_close(m: dict) -> float:
    close_time_str = m.get("close_time", "")
    try:
        ct = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        delta = (ct - datetime.now(timezone.utc)).total_seconds() / 86400.0
        return max(0.0, delta)
    except Exception:
        return 999.0

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD
# ─────────────────────────────────────────────────────────────────────────────

def _load_brad_token() -> str:
    # Use Brad's own token; fall back to Donnie's if missing
    token = os.environ.get("BRAD_TOKEN", "") or os.environ.get("DONNIE_TOKEN", "")
    if token:
        return token
    try:
        with open(BOT_TOKENS_ENV) as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if line.startswith("BRAD_TOKEN="):
                return line.split("=", 1)[1].strip()
        for line in lines:
            line = line.strip()
            if line.startswith("DONNIE_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception as e:
        log.error(f"Could not load BRAD_TOKEN: {e}")
    return ""


BRAD_TOKEN = _load_brad_token()


def post_discord(message: str, channel_id: int = BRAD_DISCORD_CH, dry_run: bool = False) -> bool:
    if dry_run:
        print("\n" + "─" * 60)
        print(f"[DRY RUN — Discord → channel {channel_id}]")
        print(message[:2000])
        print("─" * 60)
        return True

    if not BRAD_TOKEN:
        log.error("DONNIE_TOKEN not set — cannot post to Discord")
        return False

    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {BRAD_TOKEN}", "Content-Type": "application/json"}
    chunks  = [message[i:i+1990] for i in range(0, len(message), 1990)]

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

# ─────────────────────────────────────────────────────────────────────────────
# ESPN INTEGRATION (optional — validates favorites against live scores)
# ─────────────────────────────────────────────────────────────────────────────

# Cache ESPN data to avoid hammering the API
_espn_cache: dict = {}
_espn_cache_time: float = 0.0
ESPN_CACHE_TTL = 120  # 2 minutes


def fetch_espn_scoreboards() -> dict:
    """
    Fetch live scoreboards from ESPN for NBA, MLB, NHL, NFL.
    Returns dict of {league: [game_dict, ...]}
    Each game_dict has: home_team, away_team, home_score, away_score,
                        status (in-progress/final/scheduled), clock
    """
    global _espn_cache, _espn_cache_time

    now = time.time()
    if now - _espn_cache_time < ESPN_CACHE_TTL and _espn_cache:
        return _espn_cache

    result = {}
    for league, url in ESPN_SCOREBOARDS.items():
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                log.debug(f"[ESPN] {league.upper()} → {r.status_code}")
                continue

            data   = r.json()
            events = data.get("events", [])
            games  = []

            for event in events:
                comps = event.get("competitions", [{}])
                if not comps:
                    continue
                comp = comps[0]
                competitors = comp.get("competitors", [])
                status_obj  = comp.get("status", {})
                status_type = status_obj.get("type", {})

                game = {
                    "id":               event.get("id", ""),
                    "name":             event.get("name", ""),
                    "status":           status_type.get("name", ""),        # "STATUS_IN_PROGRESS" etc
                    "clock":            status_obj.get("displayClock", ""),
                    "period":           status_obj.get("period", 0),
                    "home_team":        "",
                    "away_team":        "",
                    "home_score":       0,
                    "away_score":       0,
                    "home_abbr":        "",
                    "away_abbr":        "",
                    "_raw_competitors": competitors,  # keep for cricket inning detection
                }

                for comp_team in competitors:
                    team_data = comp_team.get("team", {})
                    score_str = comp_team.get("score", "0") or "0"
                    try:
                        score = int(score_str)
                    except (ValueError, TypeError):
                        score = 0
                    is_home = comp_team.get("homeAway", "") == "home"
                    name    = team_data.get("displayName", team_data.get("name", ""))
                    abbr    = team_data.get("abbreviation", "")

                    if is_home:
                        game["home_team"]  = name
                        game["home_score"] = score
                        game["home_abbr"]  = abbr
                    else:
                        game["away_team"]  = name
                        game["away_score"] = score
                        game["away_abbr"]  = abbr

                games.append(game)

            result[league] = games
            log.info(f"[ESPN] {league.upper()}: {len(games)} games loaded")

        except Exception as e:
            log.debug(f"[ESPN] Error fetching {league}: {e}")

    _espn_cache      = result
    _espn_cache_time = now
    return result


def get_live_game_tickers(scoreboards: dict) -> tuple:
    """
    Extract team keywords from ESPN scoreboard data.
    Returns (live_keywords, today_keywords):
      live_keywords  — teams in games with STATUS_IN_PROGRESS (live right now)
      today_keywords — teams in games with STATUS_SCHEDULED (not started yet today)
    Keywords include displayName, abbreviation, and location (city name).
    """
    live_keywords  = set()
    today_keywords = set()

    for league, games in scoreboards.items():
        for game in games:
            status = game.get("status", "")

            # Extract raw team data from the ESPN response
            # (fetch_espn_scoreboards already parsed home/away into flat fields)
            team_keywords = set()
            for field in ("home_team", "away_team"):
                name = game.get(field, "")
                if name:
                    team_keywords.add(name)
            for field in ("home_abbr", "away_abbr"):
                abbr = game.get(field, "")
                if abbr:
                    team_keywords.add(abbr)

            # city/location is embedded in the full team name (e.g. "New York Yankees")
            # split first word as a loose city proxy — also keep full name
            for field in ("home_team", "away_team"):
                name = game.get(field, "")
                if name:
                    parts = name.split()
                    if parts:
                        team_keywords.add(parts[0])   # e.g. "New"
                    if len(parts) >= 2:
                        team_keywords.add(" ".join(parts[:2]))  # e.g. "New York"

            if status == "STATUS_IN_PROGRESS":
                live_keywords.update(team_keywords)
                log.info(
                    f"[ESPN] LIVE game: {game.get('away_team','')} @ {game.get('home_team','')} "
                    f"({league.upper()}) — keywords: {sorted(team_keywords)}"
                )
            elif status == "STATUS_SCHEDULED":
                today_keywords.update(team_keywords)

    # Remove very short keywords that would cause false positives
    live_keywords  = {k for k in live_keywords  if len(k) >= 2}
    today_keywords = {k for k in today_keywords if len(k) >= 2}

    return live_keywords, today_keywords


def espn_upset_in_progress(market_title: str, favorite_side: str, scoreboards: dict) -> bool:
    """
    Check if ESPN data indicates an upset is happening for the market's game.
    Returns True if underdog is winning (→ skip this market).
    Matching is fuzzy: look for team names in market title.
    """
    if not scoreboards:
        return False

    title_lower = market_title.lower()

    for league, games in scoreboards.items():
        for game in games:
            if game["status"] not in ("STATUS_IN_PROGRESS",):
                continue  # Only care about live games

            home_name = game.get("home_team", "").lower()
            away_name = game.get("away_team", "").lower()
            home_abbr = game.get("home_abbr", "").lower()
            away_abbr = game.get("away_abbr", "").lower()

            # Check if this game is relevant to the market title
            home_match = home_name in title_lower or home_abbr in title_lower
            away_match = away_name in title_lower or away_abbr in title_lower

            if not (home_match or away_match):
                continue

            home_score = game.get("home_score", 0)
            away_score = game.get("away_score", 0)

            if home_score == away_score:
                continue  # Tied — no upset determination

            # Determine who's winning
            winning_side = "home" if home_score > away_score else "away"

            # Map favorite_side (YES/NO) to home/away — heuristic:
            # Kalshi titles usually say "Will [team] win?" where YES = that team wins
            # We look for YES-team in title, check if they're losing
            if favorite_side == "YES":
                # The YES favorite should be winning; if they're losing → upset
                # We need to find which team the "YES" side refers to
                # Heuristic: the first team named in the title is usually the YES team
                for word in title_lower.split():
                    if word in home_name or word in home_abbr:
                        # YES team is home
                        if winning_side == "away":
                            log.info(
                                f"[ESPN] Upset detected: {game['home_team']} ({home_score}) "
                                f"losing to {game['away_team']} ({away_score}) — skipping {market_title[:50]}"
                            )
                            return True
                        break
                    elif word in away_name or word in away_abbr:
                        # YES team is away
                        if winning_side == "home":
                            log.info(
                                f"[ESPN] Upset detected: {game['away_team']} ({away_score}) "
                                f"losing to {game['home_team']} ({home_score}) — skipping {market_title[:50]}"
                            )
                            return True
                        break

    return False


# ─────────────────────────────────────────────────────────────────────────────
# GROK X SEARCH INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

def query_grok_x(prompt: str, max_tokens: int = 200) -> str:
    """Query Grok with X search enabled for real-time data."""
    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "grok-4.20-0309-non-reasoning",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                # search_parameters removed — live_search deprecated by xAI  # enables X/web search
            },
            timeout=15
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        else:
            log.debug(f"[Grok] Error {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.debug(f"[Grok] Failed: {e}")
    return ""


def get_grok_game_signal(home_team: str, away_team: str, sport: str) -> dict:
    """
    Query Grok for injury news and sharp money signals for a specific game.
    Returns dict with: injury_alert (bool), sharp_signal (bool), summary (str)
    """
    prompt = (
        f"For the upcoming {sport} game between {away_team} vs {home_team}: "
        f"1) Any injury news or lineup changes in the last 6 hours? "
        f"2) Any sharp betting action or line movement mentioned on X/Twitter? "
        f"Answer in 2-3 sentences max. Be specific about player names if injuries exist."
    )
    result = query_grok_x(prompt, max_tokens=150)
    if not result:
        return {"injury_alert": False, "sharp_signal": False, "summary": ""}

    result_lower = result.lower()
    injury_keywords = ["injured", "out", "doubtful", "questionable", "scratch", "dnp", "il", "disabled"]
    sharp_keywords  = ["sharp", "steam", "line move", "reverse line", "public fade", "wiseguy"]

    return {
        "injury_alert": any(kw in result_lower for kw in injury_keywords),
        "sharp_signal": any(kw in result_lower for kw in sharp_keywords),
        "summary":      result[:200]
    }


def _extract_teams_from_title(title: str) -> tuple:
    """
    Heuristically extract home/away team names from a Kalshi market title.
    e.g. "New York Yankees vs Tampa Bay Winner?" → ("Yankees", "Tampa Bay")
    Returns (home_team, away_team) — best-effort, falls back to raw halves.
    """
    import re
    # Strip trailing question mark / common suffixes
    cleaned = re.sub(r'\?.*$', '', title).strip()
    cleaned = re.sub(r'\s+(Winner|Win|to Win|Game Winner)$', '', cleaned, flags=re.IGNORECASE).strip()

    # Split on " vs ", " @ ", " at "
    for sep in (' vs ', ' @ ', ' at '):
        if sep.lower() in cleaned.lower():
            idx   = cleaned.lower().index(sep.lower())
            away  = cleaned[:idx].strip()
            home  = cleaned[idx + len(sep):].strip()
            # Use last word as short team name if long
            away_short = away.split()[-1] if away else away
            home_short = home.split()[-1] if home else home
            return home_short, away_short

    # No separator found — return whole title as home, empty as away
    return cleaned, ""


# ─────────────────────────────────────────────────────────────────────────────
# ODDS API — SPORTSBOOK LINE COMPARISON
# ─────────────────────────────────────────────────────────────────────────────

def get_sport_key(ticker: str, title: str) -> str:
    """Map Kalshi ticker to Odds API sport key."""
    ticker_upper = ticker.upper()
    if "MLB" in ticker_upper: return "baseball_mlb"
    if "NBA" in ticker_upper or "NBAGAME" in ticker_upper: return "basketball_nba"
    if "NHL" in ticker_upper: return "icehockey_nhl"
    if "NFL" in ticker_upper: return "americanfootball_nfl"
    if "IPL" in ticker_upper or "PSL" in ticker_upper: return "cricket_ipl"
    if "UCL" in ticker_upper or "UEFA" in ticker_upper: return "soccer_uefa_champs_league"
    if "WTA" in ticker_upper or "ATP" in ticker_upper: return "tennis_wta"
    if "UFC" in ticker_upper or "MMA" in ticker_upper: return "mma_mixed_martial_arts"
    return ""


def get_sportsbook_odds(sport_key: str, home_team: str, away_team: str) -> dict:
    """
    Fetch moneyline odds from DraftKings/FanDuel for a specific game.
    Returns {home_prob: float, away_prob: float, source: str} or {}

    sport_key examples: baseball_mlb, basketball_nba, icehockey_nhl, cricket_ipl
    """
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "bookmakers": "draftkings,fanduel",
                "oddsFormat": "decimal",
            },
            timeout=10
        )

        # Log remaining API credits from response headers
        remaining = r.headers.get("x-requests-remaining", "unknown")
        log.debug(f"[OddsAPI] Requests remaining: {remaining}")

        if r.status_code != 200:
            log.debug(f"[OddsAPI] {sport_key} → {r.status_code}")
            return {}

        games = r.json()
        home_lower = home_team.lower()
        away_lower = away_team.lower()

        for game in games:
            game_home = game.get("home_team", "").lower()
            game_away = game.get("away_team", "").lower()

            # Fuzzy match on team names
            if not (home_lower in game_home or game_home in home_lower or
                    away_lower in game_away or game_away in away_lower):
                continue

            # Get best odds available
            for bookmaker in game.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = market.get("outcomes", [])
                    if len(outcomes) < 2:
                        continue

                    # Convert decimal odds to implied probability
                    probs = {}
                    for o in outcomes:
                        team = o.get("name", "").lower()
                        dec_odds = float(o.get("price", 2.0))
                        prob = 1.0 / dec_odds
                        probs[team] = prob

                    # Normalize (remove vig)
                    total = sum(probs.values())
                    if total > 0:
                        probs = {k: v / total for k, v in probs.items()}

                    # Match to home/away
                    home_prob = next(
                        (v for k, v in probs.items() if home_lower in k or k in home_lower), None
                    )
                    away_prob = next(
                        (v for k, v in probs.items() if away_lower in k or k in away_lower), None
                    )

                    if home_prob and away_prob:
                        log.info(
                            f"[OddsAPI] {home_team} vs {away_team}: "
                            f"home={home_prob:.0%} away={away_prob:.0%} via {bookmaker['key']} "
                            f"| credits_left={remaining}"
                        )
                        return {
                            "home_prob": home_prob,
                            "away_prob": away_prob,
                            "source": bookmaker["key"],
                        }

        return {}
    except Exception as e:
        log.debug(f"[OddsAPI] Failed for {sport_key}: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_balance() -> float:
    """Return available cash balance in dollars."""
    data = kalshi_get("/portfolio/balance")
    balance_cents   = data.get("balance", 0.0)
    balance_dollars = float(balance_cents) / 100.0
    log.info(f"[Brad] Portfolio balance: ${balance_dollars:.2f} (raw: {balance_cents})")
    return balance_dollars


def get_all_open_positions_exposure() -> float:
    """
    Calculate total dollar exposure across ALL open positions and resting orders
    in the shared Kalshi account (Brad + Donnie combined).
    Brad must see the full picture before placing new bids.
    Returns total exposure in dollars.
    """
    total = 0.0

    # All open market positions
    pos_data  = kalshi_get("/portfolio/positions")
    positions = pos_data.get("market_positions", [])
    for pos in positions:
        exposure = float(pos.get("market_exposure", 0.0) or 0.0)
        if pos.get("position", 0) != 0:
            total += abs(exposure)

    # All resting orders (including Donnie's — they represent reserved capital)
    ord_data = kalshi_get("/portfolio/orders", params={"status": "resting"})
    orders   = ord_data.get("orders", [])
    for order in orders:
        price_c   = float(order.get("yes_price", 0) or 0)
        remaining = float(order.get("remaining_count", 0) or 0)
        total    += (price_c / 100.0) * remaining

    log.info(f"[Brad] Total account exposure (all bots): ${total:.2f}")
    return total


def get_sports_markets(max_days: int = None) -> list:
    """
    Fetch all open Sports markets from Kalshi events API.
    Filters: status=open, volume > MIN_MARKET_VOLUME, closes within max_days days.
    Returns list of market dicts with _category injected.

    max_days: override for MAX_DAYS_UNTIL_CLOSE (used by S3 which needs 30-day window)
    """
    if max_days is None:
        max_days = MAX_DAYS_UNTIL_CLOSE

    log.info(f"[Brad] Fetching Sports markets (max_days={max_days})...")
    sports_markets = []
    cursor = None
    page   = 0

    while True:
        params = {
            "status":              "open",
            "limit":               200,
            "with_nested_markets": "true",
            "category":            "Sports",
        }
        if cursor:
            params["cursor"] = cursor

        data  = kalshi_get("/events", params)
        if not data:
            break

        events = data.get("events", [])
        page  += 1

        for event in events:
            cat     = (event.get("category") or "").strip()
            markets = event.get("markets") or []

            # Hard filter: only Sports category — exclude gas, political, etc.
            if cat.lower() != "sports":
                log.debug(f"[Brad] Skipping non-Sports event: {event.get('event_ticker','?')} (category={cat!r})")
                continue

            for m in markets:
                m["_category"] = cat
                # Volume filter — sport-aware thresholds
                # MLB/NBA/NHL are high liquidity; IPL/tennis are thinner markets
                ticker_up = m.get("ticker", "").upper()
                is_thin_market = any(x in ticker_up for x in ("IPLGAME","WTAMATCH","ATPMATCH","KXIPL","KXWTA","KXATP"))
                vol_threshold = 25 if is_thin_market else MIN_MARKET_VOLUME
                if get_volume(m) < vol_threshold:
                    continue
                # Days-until-close filter
                d = days_until_close(m)
                if d > max_days or d < 0:
                    continue
                # Must be open
                if m.get("status", "") not in ("open", "active", ""):
                    continue
                sports_markets.append(m)

        cursor = data.get("cursor")
        if not cursor or len(events) < 200:
            break

    log.info(f"[Brad] Found {len(sports_markets)} qualifying Sports markets across {page} pages")
    return sports_markets


def get_game_phase(ticker: str, title: str, espn_data: dict, favorite_side: str = "YES") -> str:
    """
    Determine what phase a game is in for timing decisions.
    Returns: "pre_game", "first_half", "second_half", "finished", "unknown", "blowout"

    - "blowout": favorite is losing badly in late game — cancel any bids
    - For cricket: first_half = first inning, second_half = chase
    - For MLB:     first_half = innings 1-6, second_half = inning 7+
    - For others:  first_half = period 1, second_half = period 2+
    """
    ticker_upper = ticker.upper()
    title_lower  = title.lower()

    # Detect sport type from ticker/title
    is_cricket = any(x in ticker_upper for x in ('IPL', 'PSL', 'BBL')) or \
                 any(x in title_lower for x in ('cricket',))
    is_mlb     = 'KXMLB' in ticker_upper or 'MLB' in ticker_upper
    is_tennis  = any(x in ticker_upper for x in ('WTA', 'ATP', 'ATPMATCH', 'WTAMATCH')) or \
                 'tennis' in title_lower

    for sport, games in espn_data.items():
        for game in games:
            home = game.get('home_team', '').lower()
            away = game.get('away_team', '').lower()
            home_abbr = game.get('home_abbr', '').lower()
            away_abbr = game.get('away_abbr', '').lower()

            # Match this ESPN game to the Kalshi market by team name in title
            home_match = home in title_lower or home_abbr in title_lower or \
                         (len(home) >= 3 and home[:3] in title_lower)
            away_match = away in title_lower or away_abbr in title_lower or \
                         (len(away) >= 3 and away[:3] in title_lower)
            if not (home_match or away_match):
                continue

            status = game.get('status', '')
            period = game.get('period', 0)
            try:
                period = int(period) if period else 0
            except (ValueError, TypeError):
                period = 0

            if status == 'STATUS_FINAL':
                return 'finished'
            elif status == 'STATUS_SCHEDULED':
                return 'pre_game'
            elif status == 'STATUS_IN_PROGRESS':
                # Blowout check — if favorite is losing badly in late game, cancel bids
                home_score = game.get('home_score', 0) or 0
                away_score = game.get('away_score', 0) or 0
                score_diff = abs(home_score - away_score)
                favorite_winning = (favorite_side == 'YES' and home_score >= away_score) or                                    (favorite_side == 'NO' and away_score >= home_score)
                # Late game blowout: period 7+ in MLB with 4+ run deficit, or 2nd half with 10+ point deficit
                is_late = period >= 7 if is_mlb else period >= 2
                blowout_threshold = 4 if is_mlb else 10
                if is_late and not favorite_winning and score_diff >= blowout_threshold:
                    return 'blowout'  # favorite is losing badly in late game
                if is_cricket:
                    # Cricket: period > 1 or linescores with 2+ entries = second inning (chase)
                    linescores = []
                    competitors = game.get('_raw_competitors', [])
                    for comp in competitors:
                        ls = comp.get('linescores', [])
                        if ls:
                            linescores = ls
                            break
                    if period >= 2 or len(linescores) >= 2:
                        return 'second_half'
                    return 'first_half'
                elif is_mlb:
                    # MLB: inning 7+ = second half (use period as inning proxy)
                    if period >= 7:
                        return 'second_half'
                    return 'first_half'
                else:
                    # General sports: period 1 = first half, 2+ = second half
                    if period >= 2:
                        return 'second_half'
                    return 'first_half'

    # No ESPN match found — use timing proxy if we have start time from ticker
    # Ticker format often encodes date: e.g. KXMLBGAME-26APR131840CHCPHI
    # Try to parse start time for timing proxy
    import re
    time_match = re.search(r'(\d{2}[A-Z]{3}\d{2})(\d{4})', ticker_upper)
    if time_match:
        try:
            date_str = time_match.group(1)   # e.g. 26APR13
            time_str = time_match.group(2)   # e.g. 1840
            # Parse: 26APR13 → day=26, month=APR, year=2013? No — year likely omitted, use current
            month_map = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
                         'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
            day   = int(date_str[:2])
            mon   = month_map.get(date_str[2:5], 0)
            hour  = int(time_str[:2])
            minute = int(time_str[2:])
            if mon:
                now = datetime.now(timezone.utc)
                start = datetime(now.year, mon, day, hour, minute, tzinfo=timezone.utc)
                elapsed = (now - start).total_seconds() / 3600.0
                if elapsed < 0:
                    return 'pre_game'
                if is_cricket:
                    # Cricket matches ~7-8 hrs; first inning ~3.5 hrs
                    if elapsed < 3.5:
                        return 'first_half'
                    return 'second_half'
                elif is_mlb:
                    # MLB ~3 hrs; 7th inning ~2.5 hrs in
                    if elapsed < 2.5:
                        return 'first_half'
                    return 'second_half'
                elif is_tennis:
                    # Tennis sets ~45min; use 45min rolling window
                    return 'first_half'  # can't determine set without live data
                else:
                    if elapsed < 1.5:
                        return 'first_half'
                    return 'second_half'
        except Exception:
            pass

    return 'unknown'


def find_favorites(markets: list, live_keywords: set = None, today_keywords: set = None,
                   espn_data: dict = None) -> list:
    """
    S1: ONE BET PER GAME — only the BIGGER favorite (highest-priced side).

    Groups markets by base event ticker (strips last hyphen segment).
    For each game group, picks the single highest-priced side.
    Applies timing rules: cricket first-inning only, MLB <7th-inning,
    tennis live-only. Skips pre-game unless today_keywords confirm it's today.

    Returns list of favorite dicts sorted: live-first, then volume.
    """
    from collections import defaultdict

    if live_keywords  is None: live_keywords  = set()
    if today_keywords is None: today_keywords = set()
    if espn_data      is None: espn_data      = {}

    # Reset OddsAPI call counter for this scan
    find_favorites._odds_call_count = 0

    # ── Step 1: Group all markets by base event ticker ──────────────────────
    by_game = defaultdict(list)
    for m in markets:
        ticker = m.get('ticker', '')
        if not ticker:
            continue
        # Only include S1-type markets (game winner markets, not spread/total)
        ticker_upper = ticker.upper()
        # Skip S2-type markets (spreads/props) — they'll be handled by find_spread_favorites
        if any(kw in ticker_upper for kw in SPREAD_PROP_KEYWORDS):
            continue
        # Skip S3-type markets (tournaments) — handled by find_tournament_favorites
        if any(ticker_upper.startswith(prefix.upper()) for prefix in TOURNAMENT_TICKER_PREFIXES):
            continue
        parts = ticker.split('-')
        base  = '-'.join(parts[:-1]) if len(parts) > 1 else ticker
        by_game[base].append(m)

    favorites = []

    for base, game_markets in by_game.items():
        if not game_markets:
            continue

        # ── Step 2: Find the single highest-priced side across both markets ─
        def best_price(m):
            yes_mid = get_mid(m)
            no_mid  = 1.0 - yes_mid
            return max(yes_mid, no_mid)

        best = max(game_markets, key=best_price)
        yes_mid = get_mid(best)
        no_mid  = 1.0 - yes_mid
        mid     = max(yes_mid, no_mid)

        if mid < MIN_FAVORITE_PRICE_S1:
            continue

        ticker = best.get('ticker', '')
        title  = best.get('title', '')

        # ── Step 3: Blocked ticker guard ────────────────────────────────────
        if any(ticker.upper().startswith(p) for p in BLOCKED_TICKER_PREFIXES):
            log.debug(f"[Brad] Blocked ticker: {ticker}")
            continue

        # ── Step 4: Detect sport type ────────────────────────────────────────
        ticker_upper = ticker.upper()
        title_lower  = title.lower()
        is_cricket = any(x in ticker_upper for x in ('IPL', 'PSL', 'BBL')) or \
                     'cricket' in title_lower
        is_tennis  = any(x in ticker_upper for x in ('WTA', 'ATP', 'ATPMATCH', 'WTAMATCH')) or \
                     'tennis' in title_lower
        is_mlb     = 'KXMLB' in ticker_upper

        # ── Step 5: Timing check ─────────────────────────────────────────────
        phase = get_game_phase(ticker, title, espn_data)

        if phase == 'blowout':
            # Favorite is losing badly in late game — skip and cancel any open bids
            log.info(f"[Timing] BLOWOUT: {ticker} — favorite losing in late game, skipping")
            continue
        elif is_cricket:
            # Cricket: ONLY place bids in first inning. Cancel at second.
            if phase not in ('first_half', 'unknown'):
                log.debug(f"[Timing] Cricket {ticker} phase={phase} — skipping")
                continue
        elif phase == 'finished':
            continue
        elif phase == 'second_half' and is_mlb:
            # MLB: stop after 7th inning proxy
            log.debug(f"[Timing] MLB {ticker} past 7th-inning proxy — skipping")
            continue
        elif phase == 'pre_game':
            # Only bid on pre-game if we have confirmation the game is today
            title_lower_check = title_lower
            has_today_keyword = any(kw.lower() in title_lower_check for kw in today_keywords) or \
                                any(kw.lower() in title_lower_check for kw in live_keywords)
            if not has_today_keyword:
                log.debug(f"[Timing] {ticker} pre_game with no today keyword — skipping")
                continue

        # ── Step 6: Determine YES/NO direction ──────────────────────────────
        if yes_mid >= no_mid:
            favorite_side = 'YES'
            favorite_price = yes_mid
            yes_ask_val = get_yes_ask(best)
        else:
            favorite_side = 'NO'
            favorite_price = no_mid
            yes_bid = float(best.get('yes_bid') or best.get('yes_bid_dollars') or 0.0)
            if yes_bid > 1.0:
                yes_bid = yes_bid / 100.0
            yes_ask_val = 1.0 - yes_bid   # NO ask = 1 - YES bid

        # ── Step 7: Live score for sorting ──────────────────────────────────
        live_score = 2 if phase == 'first_half' else \
                     1 if phase in ('pre_game', 'unknown') else 0

        fav_entry = {
            'ticker':           ticker,
            'title':            title,
            'favorite_side':    favorite_side,
            'favorite_price':   favorite_price,
            'yes_ask':          yes_ask_val,
            'days_until_close': days_until_close(best),
            'volume':           get_volume(best),
            'live_score':       live_score,
            'phase':            phase,
            'is_cricket':       is_cricket,
            'is_tennis':        is_tennis,
            'is_mlb':           is_mlb,
            'strategy':         's1',
            '_market':          best,
        }

        # ── Step 8: OddsAPI sportsbook cross-check (S1 only, capped per scan) ─
        _odds_call_count = getattr(find_favorites, '_odds_call_count', 0)
        if _odds_call_count < ODDS_API_MAX_CALLS_PER_SCAN:
            sport_key = get_sport_key(ticker, title)
            if sport_key:
                teams = _extract_teams_from_title(title)
                if teams and len(teams) >= 2 and teams[0] and teams[1]:
                    sbook_odds = get_sportsbook_odds(sport_key, teams[0], teams[1])
                    find_favorites._odds_call_count = _odds_call_count + 1
                    if sbook_odds:
                        fav_side = fav_entry['favorite_side']
                        # YES = home team wins, NO = away team wins (rough heuristic)
                        sbook_fav_prob = sbook_odds.get('home_prob', 0) if fav_side == 'YES' \
                                         else sbook_odds.get('away_prob', 0)
                        kalshi_prob = fav_entry['favorite_price']
                        sbook_edge = sbook_fav_prob - kalshi_prob
                        fav_entry['sbook_prob']   = round(sbook_fav_prob, 3)
                        fav_entry['sbook_edge']   = round(sbook_edge, 3)
                        fav_entry['sbook_source'] = sbook_odds.get('source', '')
                        if sbook_edge > 0.05:
                            log.info(
                                f"[OddsAPI] EDGE: {ticker} | "
                                f"Kalshi={kalshi_prob:.0%} Sbook={sbook_fav_prob:.0%} edge={sbook_edge:+.0%}"
                            )
                        elif sbook_edge < -0.05:
                            log.info(
                                f"[OddsAPI] FADE: {ticker} | "
                                f"Kalshi={kalshi_prob:.0%} Sbook={sbook_fav_prob:.0%} — sbooks disagree"
                            )

        favorites.append(fav_entry)

    # ── Step 8: Sort live first, then volume ────────────────────────────────
    favorites.sort(key=lambda x: (-x['live_score'], -x['volume']))

    live_count  = sum(1 for f in favorites if f['live_score'] == 2)
    today_count = sum(1 for f in favorites if f['live_score'] == 1)
    log.info(
        f"[S1] ONE BET PER GAME: {len(favorites)} unique-game favorites "
        f"(price > {MIN_FAVORITE_PRICE_S1:.0%}) | "
        f"live={live_count} today={today_count} other={len(favorites)-live_count-today_count}"
    )
    return favorites


def find_spread_favorites(markets: list, live_keywords: set = None, espn_data: dict = None) -> list:
    """
    S2: Find spread/total/prop market favorites for stink bidding.

    Filters: markets with SPREAD, TOTAL, HR, KS, or STRIKEOUT in ticker
    Timing: live games only (same ESPN phase check)
    Min favorite price: 60¢
    Returns top 3 by volume.
    """
    from collections import defaultdict

    if live_keywords is None: live_keywords = set()
    if espn_data     is None: espn_data     = {}

    # ── Filter to spread/prop markets ────────────────────────────────────────
    spread_markets = []
    for m in markets:
        ticker = m.get('ticker', '').upper()
        if any(kw in ticker for kw in SPREAD_PROP_KEYWORDS):
            spread_markets.append(m)

    log.info(f"[S2] Found {len(spread_markets)} spread/prop markets pre-filter")

    # ── Group by game-prop combination ────────────────────────────────────────
    # Key: base ticker = strip last segment (strips the YES/NO side identifier)
    by_game_prop = defaultdict(list)
    for m in spread_markets:
        ticker = m.get('ticker', '')
        if not ticker:
            continue
        parts = ticker.split('-')
        base  = '-'.join(parts[:-1]) if len(parts) > 1 else ticker
        by_game_prop[base].append(m)

    favorites = []

    for base, prop_markets in by_game_prop.items():
        if not prop_markets:
            continue

        # Pick best price market in group
        def best_price(m):
            yes_mid = get_mid(m)
            no_mid  = 1.0 - yes_mid
            return max(yes_mid, no_mid)

        best = max(prop_markets, key=best_price)
        yes_mid = get_mid(best)
        no_mid  = 1.0 - yes_mid
        mid     = max(yes_mid, no_mid)

        if mid < MIN_FAVORITE_PRICE_S2:
            continue

        ticker = best.get('ticker', '')
        title  = best.get('title', '')
        ticker_upper = ticker.upper()

        # Blocked ticker guard
        if any(ticker_upper.startswith(p) for p in BLOCKED_TICKER_PREFIXES):
            continue

        # ── Timing: S2 is live games only ────────────────────────────────────
        phase = get_game_phase(ticker, title, espn_data)

        # S2 requires confirmed live game — skip pre-game and unknown
        if phase not in ('first_half', 'second_half'):
            # Allow unknown if live keywords match
            if phase == 'unknown' and live_keywords:
                title_lower = title.lower()
                has_live = any(kw.lower() in title_lower for kw in live_keywords)
                if not has_live:
                    log.debug(f"[S2] {ticker} phase={phase} no live keyword — skipping")
                    continue
            elif phase != 'unknown':
                log.debug(f"[S2] {ticker} phase={phase} — skipping (not live)")
                continue

        if phase == 'finished':
            continue

        # Determine direction
        if yes_mid >= no_mid:
            favorite_side  = 'YES'
            favorite_price = yes_mid
            yes_ask_val    = get_yes_ask(best)
        else:
            favorite_side  = 'NO'
            favorite_price = no_mid
            yes_bid = float(best.get('yes_bid') or best.get('yes_bid_dollars') or 0.0)
            if yes_bid > 1.0:
                yes_bid = yes_bid / 100.0
            yes_ask_val = 1.0 - yes_bid

        favorites.append({
            'ticker':           ticker,
            'title':            title,
            'favorite_side':    favorite_side,
            'favorite_price':   favorite_price,
            'yes_ask':          yes_ask_val,
            'days_until_close': days_until_close(best),
            'volume':           get_volume(best),
            'live_score':       2,  # S2 only runs on live games
            'phase':            phase,
            'is_cricket':       False,
            'is_tennis':        False,
            'is_mlb':           'KXMLB' in ticker_upper,
            'strategy':         's2',
            '_market':          best,
        })

    # Sort by volume, return top 3
    favorites.sort(key=lambda x: -x['volume'])
    favorites = favorites[:MAX_ACTIVE_BIDS_S2]

    log.info(f"[S2] {len(favorites)} spread/prop favorites identified (price > {MIN_FAVORITE_PRICE_S2:.0%})")
    for fav in favorites:
        log.info(
            f"  [S2] {fav['ticker']} | {fav['favorite_side']} @ {int(fav['favorite_price']*100)}¢ | "
            f"stink→{calculate_stink_bid_price(fav['favorite_price'], STINK_BID_DISCOUNT_S2)}¢ | "
            f"vol={int(fav['volume'])} | phase={fav.get('phase','?')}"
        )
    return favorites


def find_tournament_favorites(markets: list) -> list:
    """
    S3: Find tournament/series outright favorites for stink bidding.

    Filters: markets matching TOURNAMENT_TICKER_PREFIXES
    Timing: no live game check — tournament markets resolve over days/weeks
    Min favorite price: 70¢ (only clear frontrunners)
    Max days to close: 30
    Returns top 2 by volume.
    """
    tournament_markets = []
    for m in markets:
        ticker = m.get('ticker', '').upper()
        if any(ticker.startswith(prefix.upper()) for prefix in TOURNAMENT_TICKER_PREFIXES):
            d = days_until_close(m)
            if 0 < d <= MAX_DAYS_UNTIL_CLOSE_S3:
                tournament_markets.append(m)

    log.info(f"[S3] Found {len(tournament_markets)} tournament markets pre-filter")

    favorites = []

    for m in tournament_markets:
        yes_mid = get_mid(m)
        no_mid  = 1.0 - yes_mid
        mid     = max(yes_mid, no_mid)

        if mid < MIN_FAVORITE_PRICE_S3:
            continue

        ticker = m.get('ticker', '')
        title  = m.get('title', '')

        # Blocked ticker guard
        if any(ticker.upper().startswith(p) for p in BLOCKED_TICKER_PREFIXES):
            continue

        # Determine direction
        if yes_mid >= no_mid:
            favorite_side  = 'YES'
            favorite_price = yes_mid
            yes_ask_val    = get_yes_ask(m)
        else:
            favorite_side  = 'NO'
            favorite_price = no_mid
            yes_bid = float(m.get('yes_bid') or m.get('yes_bid_dollars') or 0.0)
            if yes_bid > 1.0:
                yes_bid = yes_bid / 100.0
            yes_ask_val = 1.0 - yes_bid

        favorites.append({
            'ticker':           ticker,
            'title':            title,
            'favorite_side':    favorite_side,
            'favorite_price':   favorite_price,
            'yes_ask':          yes_ask_val,
            'days_until_close': days_until_close(m),
            'volume':           get_volume(m),
            'live_score':       0,  # S3 is not live-game timing dependent
            'phase':            'tournament',
            'is_cricket':       False,
            'is_tennis':        False,
            'is_mlb':           False,
            'strategy':         's3',
            '_market':          m,
        })

    # Sort by volume, return top 2
    favorites.sort(key=lambda x: -x['volume'])
    favorites = favorites[:MAX_ACTIVE_BIDS_S3]

    log.info(f"[S3] {len(favorites)} tournament favorites identified (price > {MIN_FAVORITE_PRICE_S3:.0%})")
    for fav in favorites:
        log.info(
            f"  [S3] {fav['ticker']} | {fav['favorite_side']} @ {int(fav['favorite_price']*100)}¢ | "
            f"stink→{calculate_stink_bid_price(fav['favorite_price'], STINK_BID_DISCOUNT_S3)}¢ | "
            f"vol={int(fav['volume'])} | closes {fav['days_until_close']:.1f}d"
        )
    return favorites


def calculate_stink_bid_price(favorite_price: float, discount: float = None) -> int:
    """
    Calculate stink bid price: discount% below current mid.
    discount defaults to S1 rate if not specified.
    Returns integer cents. Min 1¢, max 99¢.
    """
    if discount is None:
        discount = STINK_BID_DISCOUNT_S1
    stink_price = favorite_price * (1.0 - discount)
    stink_cents = int(round(stink_price * 100))
    return max(1, min(99, stink_cents))


def _strategy_discount(strategy: str, favorite_price: float = None) -> float:
    """Return the discount rate for a given strategy.
    S1 uses two-tier dynamic discount based on favorite strength.
    """
    if strategy == "s1":
        return get_s1_discount(favorite_price) if favorite_price is not None else STINK_BID_DISCOUNT_S1
    return {
        "s2": STINK_BID_DISCOUNT_S2,
        "s3": STINK_BID_DISCOUNT_S3,
    }.get(strategy, STINK_BID_DISCOUNT_S1)


def get_open_brad_orders() -> dict:
    """
    Fetch all resting orders placed by Brad (client_order_id starts with 'brad-').
    Returns {ticker: order_dict}
    """
    data   = kalshi_get("/portfolio/orders", params={"status": "resting"})
    orders = data.get("orders", [])

    brad_orders = {}
    for order in orders:
        client_id = order.get("client_order_id", "")
        if client_id.startswith("brad-"):
            ticker = order.get("ticker", "")
            if ticker:
                brad_orders[ticker] = order
                log.debug(f"[Brad] Open order: {ticker} @ {order.get('yes_price',0)}¢ x{order.get('remaining_count',0)}")

    log.info(f"[Brad] Found {len(brad_orders)} open Brad orders")
    return brad_orders


def get_open_brad_orders_by_strategy(paper: bool = False) -> dict:
    """
    Fetch all resting Brad orders grouped by strategy prefix.
    In paper mode: reads open paper trades from disk instead of Kalshi API.
    Returns {"s1": {ticker: order}, "s2": {ticker: order}, "s3": {ticker: order}}
    """
    by_strategy = {"s1": {}, "s2": {}, "s3": {}}

    if paper:
        # Paper mode: count open paper trades as active slots
        trades = _load_paper_trades()
        for t in trades:
            if t.get("status", "").lower() != "open":
                continue
            ticker   = t.get("ticker", "")
            strategy = t.get("strategy", "s1")
            if ticker and strategy in by_strategy:
                # Use ticker as key, store minimal order-like dict
                if ticker not in by_strategy[strategy]:
                    by_strategy[strategy][ticker] = t
    else:
        data   = kalshi_get("/portfolio/orders", params={"status": "resting"})
        orders = data.get("orders", [])
        for order in orders:
            client_id = order.get("client_order_id", "")
            ticker    = order.get("ticker", "")
            if not client_id.startswith("brad-") or not ticker:
                continue
            if client_id.startswith("brad-s2-"):
                by_strategy["s2"][ticker] = order
            elif client_id.startswith("brad-s3-"):
                by_strategy["s3"][ticker] = order
            else:
                by_strategy["s1"][ticker] = order

    for s, orders_dict in by_strategy.items():
        log.info(f"[Brad] Open {s.upper()} orders: {len(orders_dict)}")

    return by_strategy


def get_filled_brad_orders() -> list:
    """
    Check for recently filled Brad orders (for fill notifications).
    Returns list of filled order dicts.
    """
    data   = kalshi_get("/portfolio/orders", params={"status": "filled", "limit": 50})
    orders = data.get("orders", [])
    filled = []
    for order in orders:
        client_id = order.get("client_order_id", "")
        if client_id.startswith("brad-"):
            filled.append(order)
    return filled


def get_total_brad_exposure(open_orders: dict) -> float:
    """Calculate total dollar exposure from open Brad stink bids."""
    total = 0.0
    for ticker, order in open_orders.items():
        price_c    = float(order.get("yes_price", 0))
        remaining  = float(order.get("remaining_count", 0))
        price_d    = price_c / 100.0
        total     += price_d * remaining
    return total


def get_strategy_exposure(orders_by_strategy: dict, strategy: str) -> float:
    """Calculate dollar exposure for a specific strategy."""
    orders = orders_by_strategy.get(strategy, {})
    total  = 0.0
    for ticker, order in orders.items():
        price_c   = float(order.get("yes_price", 0))
        remaining = float(order.get("remaining_count", 0))
        total    += (price_c / 100.0) * remaining
    return total


def place_stink_bid(fav: dict, balance: float, dry_run: bool = False,
                    strategy: str = "s1") -> Optional[dict]:
    """
    Place a limit BUY order at stink bid price for the given favorite market.
    fav: dict from find_favorites() / find_spread_favorites() / find_tournament_favorites()
    strategy: "s1", "s2", or "s3" — determines discount rate and order label prefix
    Returns order result dict or None on failure.
    """
    ticker         = fav["ticker"]
    title          = fav["title"][:60]
    favorite_price = fav["favorite_price"]
    favorite_side  = fav["favorite_side"]
    discount       = _strategy_discount(strategy, favorite_price)

    stink_price_c = calculate_stink_bid_price(favorite_price, discount)
    stink_price_d = stink_price_c / 100.0

    # Calculate contracts: floor((balance * 2%) / stink_price_dollars), min 1
    if stink_price_d <= 0:
        return None

    contracts = max(1, math.floor((balance * MAX_STINK_BID_PCT) / stink_price_d))
    cost      = round(contracts * stink_price_d, 2)

    client_order_id = f"brad-{strategy}-{uuid4()}"

    # For NO favorites, we buy NO contracts via yes_price = 100 - stink_price_c
    if favorite_side == "NO":
        yes_price_for_no = 100 - stink_price_c
        actual_yes_price = max(1, min(99, yes_price_for_no))
        order_side = "no"
        log.info(
            f"[{strategy.upper()}] {'[DRY] ' if dry_run else ''}Stink bid NO: {ticker} | "
            f"NO fav @ {int(favorite_price*100)}¢ | stink target: {stink_price_c}¢ NO "
            f"(yes_price={actual_yes_price}¢) x{contracts} (${cost:.2f}) | {title}"
        )
    else:
        actual_yes_price = stink_price_c
        order_side = "yes"
        log.info(
            f"[{strategy.upper()}] {'[DRY] ' if dry_run else ''}Stink bid YES: {ticker} | "
            f"YES fav @ {int(favorite_price*100)}¢ | stink: {stink_price_c}¢ ({int(discount*100)}% off) "
            f"x{contracts} (${cost:.2f}) | {title}"
        )

    order_body = {
        "ticker":          ticker,
        "client_order_id": client_order_id,
        "type":            "limit",
        "action":          "buy",
        "side":            order_side,
        "count":           contracts,
        "yes_price":       actual_yes_price,
    }

    if dry_run:
        return {
            "status":           "dry_run",
            "ticker":           ticker,
            "client_order_id":  client_order_id,
            "stink_price_c":    stink_price_c,
            "favorite_price_c": int(favorite_price * 100),
            "favorite_side":    favorite_side,
            "contracts":        contracts,
            "cost":             cost,
            "strategy":         strategy,
            "_fav":             fav,
        }

    resp  = kalshi_post("/portfolio/orders", order_body)
    if "error" in resp and "order" not in resp:
        log.error(f"[{strategy.upper()}] Order failed for {ticker}: {resp.get('error')}")
        return None

    order = resp.get("order", {})
    log.info(
        f"[{strategy.upper()}] Order placed: {ticker} status={order.get('status','?')} "
        f"order_id={order.get('order_id','?')}"
    )
    return {
        "status":           order.get("status", "unknown"),
        "order_id":         order.get("order_id", client_order_id),
        "client_order_id":  client_order_id,
        "ticker":           ticker,
        "stink_price_c":    stink_price_c,
        "favorite_price_c": int(favorite_price * 100),
        "favorite_side":    favorite_side,
        "contracts":        contracts,
        "cost":             cost,
        "strategy":         strategy,
        "_fav":             fav,
    }


def cancel_order(order_id: str) -> bool:
    """Cancel an open order by order_id."""
    ok = kalshi_delete(f"/portfolio/orders/{order_id}")
    if ok:
        log.info(f"[Brad] Cancelled order {order_id}")
    else:
        log.warning(f"[Brad] Failed to cancel order {order_id}")
    return ok


def refresh_stink_bids(open_orders: dict, markets: list, balance: float, dry_run: bool = False):
    """
    Cancel and re-place each open Brad stink bid at the current market price * discount.
    Keeps Brad competitive as prices shift during live games.
    Detects strategy from client_order_id prefix to apply correct discount.
    """
    if not open_orders:
        log.info("[Brad] No open orders to refresh")
        return

    # Build ticker → market lookup
    market_by_ticker = {m.get("ticker", ""): m for m in markets}

    log.info(f"[Brad] Refreshing {len(open_orders)} stink bids...")

    for ticker, order in list(open_orders.items()):
        order_id     = order.get("order_id", "")
        order_status = order.get("status", "")
        client_id    = order.get("client_order_id", "")

        # Determine strategy from order label
        if client_id.startswith("brad-s2-"):
            strategy = "s2"
        elif client_id.startswith("brad-s3-"):
            strategy = "s3"
        else:
            strategy = "s1"

        # Check if already filled
        if order_status == "filled":
            log.info(f"[Brad] Order {ticker} already filled — skipping cancel")
            continue

        # Cancel existing order
        if order_id and not dry_run:
            cancel_order(order_id)
        elif dry_run:
            log.info(f"[Brad] [DRY] Would cancel {ticker} order_id={order_id}")

        # Re-place if we still have market data
        m = market_by_ticker.get(ticker)
        if not m:
            log.info(f"[Brad] {ticker} no longer in sports markets — not re-placing")
            continue

        # Check market is still open and within time horizon
        d         = days_until_close(m)
        max_days  = MAX_DAYS_UNTIL_CLOSE_S3 if strategy == "s3" else MAX_DAYS_UNTIL_CLOSE
        if d > max_days or d < 0:
            log.info(f"[Brad] {ticker} closing in {d:.1f} days — outside {strategy} window, not re-placing")
            continue

        # Re-calculate favorite at current price
        yes_mid   = get_mid(m)
        no_mid    = 1.0 - yes_mid
        min_price = {
            "s1": MIN_FAVORITE_PRICE_S1,
            "s2": MIN_FAVORITE_PRICE_S2,
            "s3": MIN_FAVORITE_PRICE_S3,
        }.get(strategy, MIN_FAVORITE_PRICE_S1)

        if yes_mid > min_price:
            fav_price = yes_mid
            fav_side  = "YES"
        elif no_mid > min_price:
            fav_price = no_mid
            fav_side  = "NO"
        else:
            log.info(f"[Brad] {ticker} no longer has a clear {strategy} favorite — not re-placing")
            continue

        fav = {
            "ticker":           ticker,
            "title":            m.get("title", ticker),
            "favorite_side":    fav_side,
            "favorite_price":   fav_price,
            "yes_ask":          get_yes_ask(m),
            "days_until_close": d,
            "volume":           get_volume(m),
            "strategy":         strategy,
            "_market":          m,
        }

        time.sleep(0.3)
        result = place_stink_bid(fav, balance, dry_run=dry_run, strategy=strategy)
        if result:
            log.info(f"[Brad] Re-placed {strategy} stink bid: {ticker} @ {result['stink_price_c']}¢")
        time.sleep(0.3)


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD FORMATTING
# ─────────────────────────────────────────────────────────────────────────────

def format_brad_report(
    placed_s1: list, placed_s2: list, placed_s3: list,
    balance: float,
    active_s1: int, active_s2: int, active_s3: int,
    fills_s1: int = 0, fills_s2: int = 0, fills_s3: int = 0,
    paper: bool = False
) -> str:
    """
    Format the combined Discord report for all 3 strategies.
    Called every scan — always posts ONE report.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header_emoji = "📋" if paper else "🏆"
    lines = [
        f"{header_emoji} **BRAD SCAN** — {ts}",
        f"Strategy S1 (Live Winners): {active_s1} bids | fills: {fills_s1}",
        f"Strategy S2 (Spreads/Props): {active_s2} bids | fills: {fills_s2}",
        f"Strategy S3 (Tournaments): {active_s3} bids | fills: {fills_s3}",
        "",
    ]

    all_placed = [
        ("S1", placed_s1, STINK_BID_DISCOUNT_S1),
        ("S2", placed_s2, STINK_BID_DISCOUNT_S2),
        ("S3", placed_s3, STINK_BID_DISCOUNT_S3),
    ]

    new_bids_total = sum(len(p) for _, p, _ in all_placed)
    if new_bids_total > 0:
        lines.append(f"**New bids this scan: {new_bids_total}**")
        lines.append("")

    for strat_label, placed, discount in all_placed:
        if not placed:
            continue
        lines.append(f"── {strat_label} New Bids ──")
        for i, order in enumerate(placed, 1):
            fav         = order.get("_fav", {})
            title       = (fav.get("title") or order.get("ticker", ""))[:60]
            ticker      = order.get("ticker", "")
            fav_side    = order.get("favorite_side", "YES")
            fav_price_c = order.get("favorite_price_c", 0)
            stink_c     = order.get("stink_price_c", 0)
            contracts   = order.get("contracts", 0)
            cost        = order.get("cost", 0.0)
            days        = fav.get("days_until_close", 0)
            close_date  = ""
            try:
                m = fav.get("_market", {})
                ct_str = m.get("close_time", "")
                if ct_str:
                    ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
                    close_date = ct.strftime("%b %d")
            except Exception:
                close_date = f"{days:.1f}d"

            pct_off     = round(discount * 100)
            grok_sig    = fav.get("grok_signal", {})
            grok_summary = grok_sig.get("summary", "") if grok_sig else ""
            grok_line   = f"\n   🤖 Grok: {grok_summary[:120]}" if grok_summary else ""
            sbook_prob   = fav.get("sbook_prob")
            sbook_edge   = fav.get("sbook_edge")
            sbook_source = fav.get("sbook_source", "")
            sbook_line   = ""
            if sbook_prob is not None and sbook_edge is not None:
                sbook_line = (
                    f"\n   📊 Sbooks: {sbook_source or 'DraftKings/FanDuel'}="
                    f"{sbook_prob:.0%} | Kalshi={fav_price_c/100:.0%} | Edge={sbook_edge:+.0%}"
                )
            lines += [
                f"**{i}. {title}**",
                f"   Fav: {fav_side} @ {fav_price_c}¢ | Stink: {stink_c}¢ ({pct_off}% off) | "
                f"x{contracts} | Cost: ${cost:.2f} | Closes: {close_date}"
                f"{grok_line}{sbook_line}",
            ]
        lines.append("")

    total_exposure = sum(o["cost"] for _, placed, _ in all_placed for o in placed)
    lines.append(f"Balance: ${balance:.2f} | New exposure this scan: ${total_exposure:.2f}")

    return "\n".join(lines)


def format_fill_notification(order: dict) -> str:
    """Format Discord notification when a stink bid gets filled."""
    ticker    = order.get("ticker", "UNKNOWN")
    contracts = int(order.get("filled_count") or order.get("count") or 0)
    price_c   = int(order.get("yes_price") or 0)
    cost      = round(contracts * price_c / 100, 2)
    payout    = contracts * 1.00
    roi_pct   = round(((payout - cost) / cost * 100), 1) if cost > 0 else 0

    # Determine strategy from client_order_id
    client_id = order.get("client_order_id", "")
    if client_id.startswith("brad-s2-"):
        strategy_label = "S2 (Spreads/Props)"
    elif client_id.startswith("brad-s3-"):
        strategy_label = "S3 (Tournament)"
    else:
        strategy_label = "S1 (Live Winners)"

    # Get close time from Kalshi market data
    expiry_str = ""
    try:
        m_data = kalshi_get(f"/markets/{ticker}")
        m      = m_data.get("market", {})
        ct_str = m.get("expiration_time") or m.get("close_time", "")
        if ct_str:
            ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
            expiry_str = ct.strftime("%b %d, %Y")
    except Exception:
        expiry_str = "at expiration"

    return (
        f"⚡ **BRAD FILLED** [{strategy_label}] — `{ticker}`\n"
        f"Bought: {contracts} contracts @ {price_c}¢ | Cost: ${cost:.2f}\n"
        f"Holding to expiration: {expiry_str}\n"
        f"Payout if wins: ${payout:.2f} | ROI: {roi_pct}%"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FILL TRACKING
# ─────────────────────────────────────────────────────────────────────────────

# Track which orders we've already notified about (in-memory, resets on restart)
_notified_fills: set = set()

# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _load_paper_trades() -> list:
    """Load paper trade log from disk. Returns list of trade dicts."""
    trades = []
    try:
        os.makedirs(os.path.dirname(PAPER_TRADES_FILE), exist_ok=True)
        if os.path.exists(PAPER_TRADES_FILE):
            with open(PAPER_TRADES_FILE) as f:
                trades = json.load(f)
    except Exception as e:
        log.error(f"[Paper] Failed to load paper trades: {e}")
        return []
    # Backfill missing strategy field on old trades (Bug 6 fix)
    for t in trades:
        if 'strategy' not in t:
            ticker = t.get('ticker', '')
            if any(x in ticker.upper() for x in ['UCL', 'UFC', 'NBAPLAYOFF']):
                t['strategy'] = 's3'
            else:
                t['strategy'] = 's1'  # default old trades to S1
    return trades


def _save_paper_trades(trades: list):
    """Persist paper trade log to disk."""
    try:
        os.makedirs(os.path.dirname(PAPER_TRADES_FILE), exist_ok=True)
        with open(PAPER_TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception as e:
        log.error(f"[Paper] Failed to save paper trades: {e}")


def record_paper_trade(fav: dict, stink_price_c: int, contracts: int, cost: float,
                       strategy: str = "s1"):
    """Log a paper trade entry to disk with strategy tag."""
    trades = _load_paper_trades()
    m      = fav.get("_market", {})
    grok   = fav.get("grok_signal", {})
    entry  = {
        "id":             f"paper-{uuid4()}",
        "ticker":         fav["ticker"],
        "title":          fav.get("title", ""),
        "favorite_side":  fav["favorite_side"],
        "favorite_price": fav["favorite_price"],
        "stink_price":    stink_price_c / 100.0,
        "stink_price_c":  stink_price_c,
        "contracts":      contracts,
        "cost":           cost,
        "payout_if_win":  round(contracts * 1.0, 2),   # each contract pays $1.00 if wins (Bug 1 fix)
        "discount_pct":   stink_price_c / max(int(fav["favorite_price"] * 100), 1),
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "close_time":     m.get("close_time", ""),
        "status":         "open",      # open | filled | expired_win | expired_loss
        "fill_price":     None,
        "fill_timestamp": None,
        "result":         None,
        "strategy":       strategy,    # S1 / S2 / S3 tracking
        "grok_summary":   grok.get("summary", "") if grok else "",
        "grok_injury":    grok.get("injury_alert", False) if grok else False,
        "grok_sharp":     grok.get("sharp_signal", False) if grok else False,
        "sbook_prob":     fav.get("sbook_prob"),
        "sbook_edge":     fav.get("sbook_edge"),
        "sbook_source":   fav.get("sbook_source", ""),
    }
    trades.append(entry)
    _save_paper_trades(trades)
    log.info(
        f"[Paper] [{strategy.upper()}] Recorded: {entry['ticker']} | stink={stink_price_c}¢ "
        f"x{contracts} | cost=${cost:.2f}"
    )
    return entry


def check_paper_fills(dry_run: bool = False):
    """
    For each open paper trade, check if current market price has dropped
    to or below the stink bid price (simulated fill).
    Also checks for expiration and logs win/loss.
    """
    trades = _load_paper_trades()
    if not trades:
        return

    changed = False
    now     = datetime.now(timezone.utc)

    for trade in trades:
        if trade.get("status") != "open":
            continue

        ticker      = trade["ticker"]
        stink_c     = trade["stink_price_c"]
        fav_side    = trade["favorite_side"]
        close_time  = trade.get("close_time", "")
        strategy    = trade.get("strategy", "s1")

        # Check expiration
        expired = False
        if close_time:
            try:
                ct = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if now >= ct:
                    expired = True
            except Exception:
                pass

        # Fetch current market price
        m_data = kalshi_get(f"/markets/{ticker}")
        m      = m_data.get("market", {})

        if not m:
            log.debug(f"[Paper] Could not fetch market data for {ticker}")
            if expired:
                trade["status"] = "expired_unknown"
                trade["result"] = "market data unavailable at expiry"
                log.info(f"[Paper] {ticker} expired — no market data to determine result")
                changed = True
            continue

        current_mid = get_mid(m)

        # Simulate fill: did the price drop to our stink bid?
        if fav_side == "YES":
            current_ask_c = int(get_yes_ask(m) * 100)
            would_fill = current_ask_c <= stink_c
        else:
            # NO side: YES bid would need to have risen (NO ask dropped)
            yes_bid = float(m.get("yes_bid") or m.get("yes_bid_dollars") or 0.0)
            if yes_bid > 1.0:
                yes_bid = yes_bid / 100.0
            no_ask_c = int((1.0 - yes_bid) * 100)
            would_fill = no_ask_c <= stink_c

        if would_fill and trade["status"] == "open":
            trade["status"]         = "filled"
            trade["fill_price"]     = stink_c / 100.0
            trade["fill_timestamp"] = now.isoformat()
            changed = True
            log.info(
                f"[Paper] [{strategy.upper()}] PAPER FILL: {ticker} | stink={stink_c}¢ | "
                f"current_mid={int(current_mid*100)}¢ | "
                f"contracts={trade['contracts']} | cost=${trade['cost']:.2f}"
            )
            if not dry_run:
                grok_summary = trade.get("grok_summary", "")
                grok_line    = f"\n🤖 Grok: {grok_summary}" if grok_summary else ""
                sbook_prob   = trade.get("sbook_prob")
                sbook_edge   = trade.get("sbook_edge")
                sbook_source = trade.get("sbook_source", "")
                sbook_line   = ""
                if sbook_prob is not None and sbook_edge is not None:
                    kalshi_prob = trade.get("favorite_price", 0)
                    sbook_line = (
                        f"\n📊 Sbooks: {sbook_source or 'DraftKings/FanDuel'}="
                        f"{sbook_prob:.0%} | Kalshi={kalshi_prob:.0%} | Edge={sbook_edge:+.0%}"
                    )
                msg = (
                    f"📋 **BRAD PAPER FILL** [{strategy.upper()}] — `{ticker}`\n"
                    f"{trade['title'][:80]}\n"
                    f"[PAPER] Would have bought: {trade['contracts']} contracts @ {stink_c}¢ | Cost: ${trade['cost']:.2f}\n"
                    f"Simulated fill triggered — tracking to expiration"
                    f"{grok_line}{sbook_line}"
                )
                post_discord(msg, channel_id=BRAD_DISCORD_CH)

        # Handle expiration for filled paper trades
        if expired and trade["status"] == "filled":
            # Determine win/loss: did the favorite actually win?
            result_data = kalshi_get(f"/markets/{ticker}")
            result_m    = result_data.get("market", {})
            result_val  = result_m.get("result", "")

            if fav_side == "YES":
                won = result_val == "yes"
            else:
                won = result_val == "no"

            if result_val:
                contracts  = trade["contracts"]
                fill_price = trade.get("fill_price", stink_c / 100.0)
                cost       = round(contracts * fill_price, 2)
                payout     = contracts * 1.0 if won else 0.0
                pnl        = round(payout - cost, 2)

                trade["status"] = "expired_win" if won else "expired_loss"
                trade["result"] = f"{'WIN' if won else 'LOSS'}: PnL=${pnl:+.2f}"
                changed = True

                result_emoji = "✅" if won else "❌"
                log.info(
                    f"[Paper] [{strategy.upper()}] PAPER RESULT {result_emoji}: {ticker} | "
                    f"{'WIN' if won else 'LOSS'} | PnL=${pnl:+.2f} | "
                    f"{contracts} contracts @ {int(fill_price*100)}¢"
                )
                if not dry_run:
                    msg = (
                        f"📋 **BRAD PAPER RESULT** {result_emoji} [{strategy.upper()}] — `{ticker}`\n"
                        f"{trade['title'][:80]}\n"
                        f"[PAPER] {'WIN' if won else 'LOSS'}: {contracts} contracts | "
                        f"Fill: {int(fill_price*100)}¢ | Payout: ${payout:.2f} | PnL: ${pnl:+.2f}"
                    )
                    post_discord(msg, channel_id=BRAD_DISCORD_CH)

        elif expired and trade["status"] == "open":
            trade["status"] = "expired_unfilled"
            trade["result"] = "stink bid never filled"
            changed = True
            log.info(f"[Paper] [{strategy.upper()}] {ticker} expired unfilled — stink price never hit")

        # Catch open bids where Kalshi finalized early (close_time parse failures)
        elif trade["status"] == "open" and m and m.get("status") in ("finalized", "determined"):
            trade["status"] = "expired_unfilled"
            trade["result"] = m.get("result", "finalized_unfilled")
            changed = True
            log.info(f"[Paper] [{strategy.upper()}] {ticker} — market finalized, clearing stale open bid")

    if changed:
        _save_paper_trades(trades)


def check_paper_expirations(dry_run: bool = False):
    """Check if filled paper trades have resolved and log WIN/LOSS."""
    trades = _load_paper_trades()
    now = datetime.now(timezone.utc)
    updated = False

    for trade in trades:
        if trade.get('status') not in ('filled',):
            continue

        close_time_str = trade.get('close_time', '')
        if not close_time_str:
            continue

        try:
            ct = datetime.fromisoformat(close_time_str.replace('Z', '+00:00'))
        except Exception:
            continue

        if now < ct:
            continue

        ticker   = trade.get('ticker', '')
        strategy = trade.get('strategy', 's1')
        path     = f'/trade-api/v2/markets/{ticker}'
        r = requests.get(KALSHI_BASE + f'/markets/{ticker}', headers=get_auth_headers('GET', path), timeout=10)
        if r.status_code != 200:
            log.debug(f"[Paper] Could not fetch result for {ticker}: HTTP {r.status_code}")
            continue

        market  = r.json().get('market', {})
        result  = market.get('result', '')
        mstatus = market.get('status', '')

        if mstatus not in ('finalized', 'settled') and result == '':
            continue

        # Bug 3 fix: exact side comparison to prevent both sides marking as WIN
        result_lower = result.lower().strip()
        fav_side = trade.get('favorite_side', 'YES').lower().strip()

        if result_lower == fav_side:
            # Our side matched the actual result — we won
            won = True
        elif result_lower in ('yes', 'no'):
            # Result is clear and our side DIDN'T match — we lost
            won = False
        else:
            continue  # No result yet

        if won:
            # Bug 2 fix: correct payout calculation
            payout = float(trade.get('payout_if_win') or trade.get('contracts', 1))
            cost = float(trade.get('cost', 0))
            pnl = round(payout - cost, 2)  # net profit
            trade['result'] = f'WIN: PnL=+${pnl:.2f}'
            trade['status'] = 'WIN'
            log.info(f'[Paper] [{strategy.upper()}] WIN: {ticker} | pnl=${pnl:.2f}')
            if not dry_run:
                msg = (
                    f"✅ **BRAD PAPER WIN** [{strategy.upper()}] — `{ticker}`\n"
                    f"{trade.get('title','')[:80]}\n"
                    f"[PAPER] WIN: {trade.get('contracts',0)} contracts | "
                    f"Fill: {int(trade.get('stink_price',0)*100)}¢ | PnL: ${pnl:+.2f}"
                )
                post_discord(msg, channel_id=BRAD_DISCORD_CH)
        else:
            # Bug 2 fix: LOSS pnl is the cost paid
            cost = float(trade.get('cost', 0))
            trade['result'] = f'LOSS: PnL=-${cost:.2f}'
            trade['status'] = 'LOSS'
            log.info(f'[Paper] [{strategy.upper()}] LOSS: {ticker} | cost=${cost:.2f}')
            if not dry_run:
                msg = (
                    f"❌ **BRAD PAPER LOSS** [{strategy.upper()}] — `{ticker}`\n"
                    f"{trade.get('title','')[:80]}\n"
                    f"[PAPER] LOSS: {trade.get('contracts',0)} contracts | "
                    f"Fill: {int(trade.get('stink_price',0)*100)}¢ | PnL: ${-cost:+.2f}"
                )
                post_discord(msg, channel_id=BRAD_DISCORD_CH)

        updated = True

    if updated:
        _save_paper_trades(trades)


def place_paper_trade(fav: dict, balance: float, strategy: str = "s1") -> Optional[dict]:
    """
    Paper trade version of place_stink_bid.
    Logs the trade, records to disk, posts to Discord. No real orders.
    Deduplicates: skips if this ticker already has an open paper bid.
    """
    ticker         = fav["ticker"]
    favorite_price = fav["favorite_price"]

    # Dedup: skip if already have an open paper bid on this ticker
    existing = _load_paper_trades()
    for t in existing:
        if t.get("ticker") == ticker and t.get("status", "").lower() == "open":
            log.debug(f"[Paper] Already have open bid on {ticker} — skipping dedup")
            return None
    favorite_side  = fav["favorite_side"]
    discount       = _strategy_discount(strategy, favorite_price)

    stink_price_c = calculate_stink_bid_price(favorite_price, discount)
    stink_price_d = stink_price_c / 100.0

    if stink_price_d <= 0:
        return None

    contracts = max(1, math.floor((balance * MAX_STINK_BID_PCT) / stink_price_d))
    cost      = round(contracts * stink_price_d, 2)

    log.info(
        f"[Paper] [{strategy.upper()}] Would place stink bid: {contracts} contracts @ {stink_price_c}¢ "
        f"on {ticker} (favorite {favorite_side} @ {int(favorite_price*100)}¢ | "
        f"discount={int(discount*100)}% | cost=${cost:.2f})"
    )

    entry = record_paper_trade(fav, stink_price_c, contracts, cost, strategy=strategy)

    return {
        "status":           "paper",
        "ticker":           ticker,
        "client_order_id":  entry["id"],
        "stink_price_c":    stink_price_c,
        "favorite_price_c": int(favorite_price * 100),
        "favorite_side":    favorite_side,
        "contracts":        contracts,
        "cost":             cost,
        "strategy":         strategy,
        "_fav":             fav,
    }


def check_and_notify_fills(dry_run: bool = False):
    """Check for newly filled Brad orders and post Discord notifications."""
    filled = get_filled_brad_orders()
    for order in filled:
        order_id = order.get("order_id", "")
        if order_id and order_id not in _notified_fills:
            _notified_fills.add(order_id)
            msg = format_fill_notification(order)
            log.info(f"[Brad] FILLED: {order.get('ticker','?')} — posting notification")
            post_discord(msg, channel_id=BRAD_DISCORD_CH, dry_run=dry_run)
            time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN + REFRESH LOOPS
# ─────────────────────────────────────────────────────────────────────────────

def run_brad_scan(dry_run: bool = False, paper: bool = True):
    """
    Full sports market scan. Finds favorites across all 3 strategies and places stink bids.
    Called every 30 minutes.

    paper=True  → no real orders; logs to brad_paper_trades.json (default for CLI)
    paper=False → live mode; real orders placed (used by firm.py / --live flag)
    dry_run=True → no orders, no Discord, stdout only (overrides paper)

    Strategies run in parallel:
      S1: Live game winner markets (30% discount, max 5 bids, 12% of balance)
      S2: Spread/prop markets     (25% discount, max 3 bids,  8% of balance)
      S3: Tournament outrights    (35% discount, max 2 bids,  5% of balance)
    """
    log.info("=" * 60)
    mode_label = "DRY RUN" if dry_run else ("PAPER" if paper else "LIVE")
    log.info(f"[Brad] Starting multi-strategy sports scan [{mode_label}]...")
    log.info("=" * 60)

    # Get current balance
    balance = get_balance()
    if balance <= 0:
        log.warning("[Brad] Zero balance — skipping scan")
        return

    # ── SHARED CAPITAL CAP (NON-NEGOTIABLE) ──────────────────────────────────
    total_account_exposure = get_all_open_positions_exposure()
    brad_hard_cap          = balance * BRAD_MAX_BALANCE_PCT

    # Get per-strategy open orders
    orders_by_strategy = get_open_brad_orders_by_strategy(paper=paper)
    all_open_orders    = {
        **orders_by_strategy["s1"],
        **orders_by_strategy["s2"],
        **orders_by_strategy["s3"],
    }
    brad_total_exposure = get_total_brad_exposure(all_open_orders)

    log.info(
        f"[Brad] Capital check: balance=${balance:.2f} | "
        f"account_exposure=${total_account_exposure:.2f} | "
        f"brad_total_exposure=${brad_total_exposure:.2f} | "
        f"brad_hard_cap=${brad_hard_cap:.2f} ({BRAD_MAX_BALANCE_PCT:.0%})"
    )

    # Per-strategy exposure check
    s1_exposure = get_strategy_exposure(orders_by_strategy, "s1")
    s2_exposure = get_strategy_exposure(orders_by_strategy, "s2")
    s3_exposure = get_strategy_exposure(orders_by_strategy, "s3")
    s1_cap      = balance * STRATEGY_CAPS["s1"]
    s2_cap      = balance * STRATEGY_CAPS["s2"]
    s3_cap      = balance * STRATEGY_CAPS["s3"]

    log.info(
        f"[Brad] Strategy caps: "
        f"S1=${s1_exposure:.2f}/${s1_cap:.2f} ({STRATEGY_CAPS['s1']:.0%}) | "
        f"S2=${s2_exposure:.2f}/${s2_cap:.2f} ({STRATEGY_CAPS['s2']:.0%}) | "
        f"S3=${s3_exposure:.2f}/${s3_cap:.2f} ({STRATEGY_CAPS['s3']:.0%})"
    )

    if brad_total_exposure >= brad_hard_cap:
        log.info(
            f"[Brad] Brad hard cap reached: ${brad_total_exposure:.2f} >= ${brad_hard_cap:.2f} — "
            f"skipping all new bids"
        )
        if paper:
            check_paper_fills(dry_run=dry_run)
        else:
            check_and_notify_fills(dry_run=dry_run)
        return

    # ── ESPN data ─────────────────────────────────────────────────────────────
    try:
        scoreboards    = fetch_espn_scoreboards()
        espn_available = bool(scoreboards)
    except Exception as e:
        log.debug(f"[Brad] ESPN unavailable: {e}")
        scoreboards    = {}
        espn_available = False

    live_keywords, today_keywords = get_live_game_tickers(scoreboards) if espn_available else (set(), set())
    if live_keywords:
        log.info(f"[Brad] Live game keywords ({len(live_keywords)}): {sorted(live_keywords)[:20]}")

    # ── Fetch markets ─────────────────────────────────────────────────────────
    # S1 + S2 need 7-day window; S3 needs 30-day window
    # Fetch once with 30-day window — S1/S2 filtering happens in find_* functions
    markets_all = get_sports_markets(max_days=MAX_DAYS_UNTIL_CLOSE_S3)

    # Narrow market pools
    markets_s1_s2 = [m for m in markets_all if days_until_close(m) <= MAX_DAYS_UNTIL_CLOSE]
    markets_s3    = markets_all  # all markets eligible for S3 (up to 30 days)

    if not markets_all:
        log.warning("[Brad] No sports markets found")
        return

    log.info(
        f"[Brad] Market pools: S1/S2={len(markets_s1_s2)} (≤7d) | "
        f"S3={len(markets_s3)} (≤30d)"
    )

    # Check fills / paper simulation
    if paper:
        check_paper_fills(dry_run=dry_run)
    else:
        check_and_notify_fills(dry_run=dry_run)

    # ── STRATEGY S1: Live Game Winners ────────────────────────────────────────
    placed_s1 = []
    s1_active  = len(orders_by_strategy["s1"])
    s1_fills   = 0  # would need DB query; placeholder

    if s1_exposure < s1_cap and s1_active < MAX_ACTIVE_BIDS_S1:
        favorites_s1 = find_favorites(
            markets_s1_s2,
            live_keywords=live_keywords,
            today_keywords=today_keywords,
            espn_data=scoreboards,
        )
        existing_s1   = set(orders_by_strategy["s1"].keys())
        s1_slots      = MAX_ACTIVE_BIDS_S1 - s1_active
        s1_budget     = s1_cap - s1_exposure
        s1_spent      = 0.0
        grok_calls    = 0

        for fav in favorites_s1:
            if s1_slots <= 0 or s1_spent >= s1_budget:
                break
            ticker = fav["ticker"]
            if ticker in existing_s1:
                continue
            if espn_available and espn_upset_in_progress(fav["title"], fav["favorite_side"], scoreboards):
                log.info(f"[S1] ESPN upset in progress for {ticker} — skipping")
                continue

            # ── Grok X signal (live/within-6h S1 markets only) ──────────────
            grok_signal = {}
            days_left   = fav.get("days_until_close", 999)
            is_live     = fav.get("phase") in ("first_half", "second_half")
            within_6h   = days_left <= 0.25  # 0.25 days = 6 hours
            if (is_live or within_6h) and grok_calls < GROK_MAX_CALLS_PER_SCAN:
                home_team, away_team = _extract_teams_from_title(fav.get("title", ticker))
                sport = "MLB" if fav.get("is_mlb") else \
                        "NHL" if "NHL" in ticker.upper() else \
                        "NBA" if "NBA" in ticker.upper() else \
                        "NFL" if "NFL" in ticker.upper() else "sports"
                log.info(f"[Grok] Querying signal for {home_team} vs {away_team} ({sport}) [{ticker}]")
                grok_signal = get_grok_game_signal(home_team, away_team, sport)
                grok_calls += 1
                log.info(
                    f"[Grok] {ticker} → injury_alert={grok_signal.get('injury_alert')} "
                    f"sharp_signal={grok_signal.get('sharp_signal')} "
                    f"summary={grok_signal.get('summary','')[:80]}"
                )

                # Injury on FAVORITE → skip (bad stink bid)
                if grok_signal.get("injury_alert"):
                    log.info(f"[Grok] Injury alert for {ticker} — skipping (injured star = bad stink bid)")
                    continue

            fav["grok_signal"] = grok_signal

            result = _place_or_simulate(fav, balance, dry_run, paper, strategy="s1")
            if result:
                placed_s1.append(result)
                s1_slots  -= 1
                s1_spent  += result.get("cost", 0.0)
                time.sleep(0.5)
    else:
        log.info(
            f"[S1] Cap or slot limit reached: exposure=${s1_exposure:.2f}/{s1_cap:.2f} "
            f"active={s1_active}/{MAX_ACTIVE_BIDS_S1} — skipping S1 placements"
        )

    # ── STRATEGY S2: Spread/Prop Markets ─────────────────────────────────────
    placed_s2 = []
    s2_active  = len(orders_by_strategy["s2"])
    s2_fills   = 0

    if s2_exposure < s2_cap and s2_active < MAX_ACTIVE_BIDS_S2:
        favorites_s2 = find_spread_favorites(markets_s1_s2, live_keywords=live_keywords, espn_data=scoreboards)
        existing_s2  = set(orders_by_strategy["s2"].keys())
        s2_slots     = MAX_ACTIVE_BIDS_S2 - s2_active
        s2_budget    = s2_cap - s2_exposure
        s2_spent     = 0.0

        for fav in favorites_s2:
            if s2_slots <= 0 or s2_spent >= s2_budget:
                break
            ticker = fav["ticker"]
            if ticker in existing_s2:
                continue

            result = _place_or_simulate(fav, balance, dry_run, paper, strategy="s2")
            if result:
                placed_s2.append(result)
                s2_slots  -= 1
                s2_spent  += result.get("cost", 0.0)
                time.sleep(0.5)
    else:
        log.info(
            f"[S2] Cap or slot limit reached: exposure=${s2_exposure:.2f}/{s2_cap:.2f} "
            f"active={s2_active}/{MAX_ACTIVE_BIDS_S2} — skipping S2 placements"
        )

    # ── STRATEGY S3: Tournament/Outright Markets ──────────────────────────────
    placed_s3 = []
    s3_active  = len(orders_by_strategy["s3"])
    s3_fills   = 0

    if s3_exposure < s3_cap and s3_active < MAX_ACTIVE_BIDS_S3:
        favorites_s3 = find_tournament_favorites(markets_s3)
        existing_s3  = set(orders_by_strategy["s3"].keys())
        s3_slots     = MAX_ACTIVE_BIDS_S3 - s3_active
        s3_budget    = s3_cap - s3_exposure
        s3_spent     = 0.0

        for fav in favorites_s3:
            if s3_slots <= 0 or s3_spent >= s3_budget:
                break
            ticker = fav["ticker"]
            if ticker in existing_s3:
                continue

            result = _place_or_simulate(fav, balance, dry_run, paper, strategy="s3")
            if result:
                placed_s3.append(result)
                s3_slots  -= 1
                s3_spent  += result.get("cost", 0.0)
                time.sleep(0.5)
    else:
        log.info(
            f"[S3] Cap or slot limit reached: exposure=${s3_exposure:.2f}/{s3_cap:.2f} "
            f"active={s3_active}/{MAX_ACTIVE_BIDS_S3} — skipping S3 placements"
        )

    # ── Discord report ────────────────────────────────────────────────────────
    report = format_brad_report(
        placed_s1=placed_s1, placed_s2=placed_s2, placed_s3=placed_s3,
        balance=balance,
        active_s1=s1_active + len(placed_s1),
        active_s2=s2_active + len(placed_s2),
        active_s3=s3_active + len(placed_s3),
        fills_s1=s1_fills, fills_s2=s2_fills, fills_s3=s3_fills,
        paper=paper,
    )
    post_discord(report, channel_id=BRAD_DISCORD_CH, dry_run=dry_run)

    total_placed = len(placed_s1) + len(placed_s2) + len(placed_s3)
    total_spent  = sum(o.get("cost", 0) for o in placed_s1 + placed_s2 + placed_s3)
    log.info(
        f"[Brad] Scan complete: S1={len(placed_s1)} S2={len(placed_s2)} S3={len(placed_s3)} | "
        f"total={total_placed} bids | spent=${total_spent:.2f} | "
        f"mode={'paper' if paper else 'live'}"
    )


def _place_or_simulate(fav: dict, balance: float, dry_run: bool, paper: bool,
                       strategy: str = "s1") -> Optional[dict]:
    """Helper: route to dry_run / paper / live placement."""
    if dry_run:
        discount  = _strategy_discount(strategy, fav["favorite_price"])
        stink_c   = calculate_stink_bid_price(fav["favorite_price"], discount)
        stink_d   = stink_c / 100.0
        contracts = max(1, math.floor((balance * MAX_STINK_BID_PCT) / stink_d))
        cost      = round(contracts * stink_d, 2)
        log.info(
            f"[DRY RUN] [{strategy.upper()}] Would stink bid: {fav['ticker']} @ {stink_c}¢ x{contracts} "
            f"(${cost:.2f}) | {int(discount*100)}% off | phase={fav.get('phase','?')}"
        )
        return {
            "status":           "dry_run",
            "ticker":           fav["ticker"],
            "client_order_id":  f"brad-{strategy}-dryrun-{uuid4()}",
            "stink_price_c":    stink_c,
            "favorite_price_c": int(fav["favorite_price"] * 100),
            "favorite_side":    fav["favorite_side"],
            "contracts":        contracts,
            "cost":             cost,
            "strategy":         strategy,
            "_fav":             fav,
        }
    elif paper:
        return place_paper_trade(fav, balance, strategy=strategy)
    else:
        return place_stink_bid(fav, balance, dry_run=False, strategy=strategy)


def run_brad_refresh(dry_run: bool = False, paper: bool = True):
    """
    Cancel and re-place all open Brad stink bids at updated prices.
    Called every 15 minutes to keep prices competitive.
    In paper mode: runs paper fill simulation only (no live orders to cancel/replace).
    """
    log.info("-" * 60)
    log.info(f"[Brad] Starting stink bid refresh [{'PAPER' if paper else 'LIVE'}]...")
    log.info("-" * 60)

    if paper or dry_run:
        # Paper mode: just run the fill simulation + expiration check
        check_paper_fills(dry_run=dry_run)
        check_paper_expirations(dry_run=dry_run)
        log.info("[Brad] Paper refresh complete: checked for simulated fills and expirations")
        return

    # Live mode: cancel and re-place real orders
    check_and_notify_fills(dry_run=dry_run)

    open_orders = get_open_brad_orders()
    if not open_orders:
        log.info("[Brad] No open Brad orders to refresh")
        return

    balance = get_balance()
    if balance <= 0:
        log.warning("[Brad] Zero balance — skipping refresh")
        return

    # ── Cricket second-inning cancellation ──────────────────────────────────
    try:
        espn_data_refresh = fetch_espn_scoreboards()
    except Exception:
        espn_data_refresh = {}

    cricket_cancelled = []
    for ticker, order in list(open_orders.items()):
        ticker_upper = ticker.upper()
        is_cricket_order = any(x in ticker_upper for x in ('IPL', 'PSL', 'BBL'))
        if not is_cricket_order:
            continue
        order_title = order.get('ticker', ticker)
        phase = get_game_phase(ticker, order_title, espn_data_refresh)
        if phase == 'second_half':
            order_id = order.get('order_id', '')
            log.info(f"[Cricket] Cancelling {ticker} — match in second inning/chase phase")
            if order_id and not dry_run:
                cancel_order(order_id)
            elif dry_run:
                log.info(f"[Cricket] [DRY] Would cancel {ticker} (second inning)")
            del open_orders[ticker]
            cricket_cancelled.append(ticker)

    if cricket_cancelled:
        log.info(f"[Brad] Cancelled {len(cricket_cancelled)} cricket orders in second inning: {cricket_cancelled}")

    if not open_orders:
        log.info("[Brad] No remaining open Brad orders after cricket cancellation")
        return

    # Shared capital cap check before re-placing
    brad_exposure = get_total_brad_exposure(open_orders)
    brad_cap      = balance * BRAD_MAX_BALANCE_PCT
    if brad_exposure >= brad_cap:
        log.info(
            f"[Brad] Brad capital cap reached during refresh: ${brad_exposure:.2f} >= ${brad_cap:.2f} "
            f"— cancelling orders without re-placing"
        )
        for ticker, order in open_orders.items():
            order_id = order.get("order_id", "")
            if order_id:
                cancel_order(order_id)
        return

    # Fetch markets with 30-day window to cover S3 refresh as well
    markets = get_sports_markets(max_days=MAX_DAYS_UNTIL_CLOSE_S3)

    refresh_stink_bids(open_orders, markets, balance, dry_run=dry_run)
    log.info(f"[Brad] Refresh complete: processed {len(open_orders)} orders")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────────

def run_scan(post=None, **kwargs):
    """Entry point for firm.py orchestrator. Uses BRAD_PAPER_MODE constant."""
    run_brad_scan(dry_run=False, paper=BRAD_PAPER_MODE)


def main():
    parser = argparse.ArgumentParser(description="Brad — Kalshi Sports Multi-Strategy Stink Bid Bot")
    parser.add_argument("--dry-run",   action="store_true", help="Stdout only — no orders, no Discord")
    parser.add_argument("--scan-once", action="store_true", help="Single scan + exit")
    parser.add_argument("--live",      action="store_true", help="Live trading mode (places real orders)")
    parser.add_argument("--paper",     action="store_true", help="Explicit paper mode (default if --live not passed)")
    args = parser.parse_args()

    dry_run   = args.dry_run
    scan_once = args.scan_once
    paper     = not args.live   # paper=True unless --live passed

    if dry_run:
        paper = False   # dry_run overrides both (no orders, no tracking)

    modes = []
    if dry_run:   modes.append("DRY-RUN")
    elif paper:   modes.append("PAPER")
    else:         modes.append("LIVE 🔴")
    if scan_once: modes.append("SCAN-ONCE")

    log.info(f"Brad starting [{' | '.join(modes)}]")
    log.info(
        f"Strategies: "
        f"S1 winners {int(STINK_BID_DISCOUNT_S1*100)}% off (>{int(MIN_FAVORITE_PRICE_S1*100)}¢, max {MAX_ACTIVE_BIDS_S1}) | "
        f"S2 spreads {int(STINK_BID_DISCOUNT_S2*100)}% off (>{int(MIN_FAVORITE_PRICE_S2*100)}¢, max {MAX_ACTIVE_BIDS_S2}) | "
        f"S3 tournaments {int(STINK_BID_DISCOUNT_S3*100)}% off (>{int(MIN_FAVORITE_PRICE_S3*100)}¢, max {MAX_ACTIVE_BIDS_S3})"
    )
    log.info(
        f"Capital caps: S1={int(STRATEGY_CAPS['s1']*100)}% | "
        f"S2={int(STRATEGY_CAPS['s2']*100)}% | S3={int(STRATEGY_CAPS['s3']*100)}% | "
        f"Total cap={int(BRAD_MAX_BALANCE_PCT*100)}%"
    )

    if paper and not dry_run:
        log.info(f"[Brad] Paper trades tracked at: {PAPER_TRADES_FILE}")

    if scan_once:
        run_brad_scan(dry_run=dry_run, paper=paper)
        return

    last_scan    = 0.0
    last_refresh = 0.0

    while True:
        now = time.time()

        if now - last_scan >= SCAN_INTERVAL_SEC:
            try:
                run_brad_scan(dry_run=dry_run, paper=paper)
            except Exception as e:
                log.error(f"[Brad] Scan error: {e}", exc_info=True)
            last_scan = time.time()

        if now - last_refresh >= REFRESH_INTERVAL_SEC:
            try:
                run_brad_refresh(dry_run=dry_run, paper=paper)
            except Exception as e:
                log.error(f"[Brad] Refresh error: {e}", exc_info=True)
            last_refresh = time.time()

        time.sleep(30)


if __name__ == "__main__":
    main()
