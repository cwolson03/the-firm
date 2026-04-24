#!/usr/bin/env python3
"""
RUGRAT — Congressional Trade Intelligence System
Track 18 members. Score their trades. Cross-reference Cody's book. Post actionable alerts.
The Firm | Stratton Oakmont Discord Intelligence System
"""

import os
import sys
import json
import argparse
import requests
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(__file__), '..', 'config', 'bot-tokens.env')
SEEN_FILE   = os.path.join(os.path.dirname(__file__), '..', 'config', 'rugrat_seen.json')
load_dotenv(TOKENS_FILE)

# ── Shared context (optional) ───────────────────────────────────────────────
try:
    from shared_context import write_agent_status as _write_status
except ImportError:
    def _write_status(name, d): pass


RUGRAT_TOKEN = os.getenv('RUGRAT_TOKEN')

CHANNEL_SENATOR_TRACKER = 1491199675183927467
CHANNEL_ACTIVE_PLAYS    = 1487189069803819231
CHANNEL_WATCHLIST       = 1487189076460437586

SENATE_URL = "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/aggregate/all_transactions.json"
HOUSE_URL  = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"

HEADERS = {"User-Agent": "RugratBot/2.0 (congressional-trade-tracker; contact via Discord)"}

# ── Tracked Members ─────────────────────────────────────────────────────────
WATCHED_MEMBERS = {
    # Senate
    "Tommy Tuberville":        {"chamber": "Senate", "party": "R", "committees": ["Armed Services", "Agriculture"],     "specialty": "commodities,defense"},
    "Ted Cruz":                {"chamber": "Senate", "party": "R", "committees": ["Commerce", "Foreign Relations"],     "specialty": "energy,tech"},
    "Markwayne Mullin":        {"chamber": "Senate", "party": "R", "committees": ["Armed Services", "Commerce"],        "specialty": "diversified"},
    "John Hickenlooper":       {"chamber": "Senate", "party": "D", "committees": ["Commerce", "Energy"],                "specialty": "tech,energy"},
    "Jerry Moran":             {"chamber": "Senate", "party": "R", "committees": ["Appropriations", "Commerce"],        "specialty": "defense,ag"},
    "Mark Kelly":              {"chamber": "Senate", "party": "D", "committees": ["Armed Services", "Commerce"],        "specialty": "defense,space"},
    "John Hoeven":             {"chamber": "Senate", "party": "R", "committees": ["Agriculture", "Appropriations"],     "specialty": "energy,ag"},
    "Susan Collins":           {"chamber": "Senate", "party": "R", "committees": ["Appropriations", "Intelligence"],    "specialty": "diversified"},
    "Brian Mast":              {"chamber": "House",  "party": "R", "committees": ["Foreign Affairs", "Armed Services"], "specialty": "defense"},
    # House
    "Nancy Pelosi":            {"chamber": "House",  "party": "D", "committees": ["Minority Leader"],                  "specialty": "tech,options"},
    "Dan Crenshaw":            {"chamber": "House",  "party": "R", "committees": ["Homeland Security", "Intelligence"],"specialty": "crypto,tech,energy"},
    "Marjorie Taylor Greene":  {"chamber": "House",  "party": "R", "committees": ["Oversight"],                        "specialty": "tech,meme"},
    "Josh Gottheimer":         {"chamber": "House",  "party": "D", "committees": ["Financial Services"],               "specialty": "finance,tech"},
    "Michael McCaul":          {"chamber": "House",  "party": "R", "committees": ["Foreign Affairs"],                  "specialty": "defense,tech"},
    "Ro Khanna":               {"chamber": "House",  "party": "D", "committees": ["Armed Services", "Oversight"],      "specialty": "tech,semiconductors"},
    "Pat Fallon":              {"chamber": "House",  "party": "R", "committees": ["Armed Services", "Science"],        "specialty": "high_volume"},
    "Kevin Hern":              {"chamber": "House",  "party": "R", "committees": ["Budget", "Ways and Means"],         "specialty": "diversified"},
    "Marie Gluesenkamp Perez": {"chamber": "House",  "party": "D", "committees": ["Science", "Veterans"],              "specialty": "emerging"},
}

# Member track record scores (0-30 scale based on documented returns)
MEMBER_SCORES = {
    "Nancy Pelosi":            28,
    "Michael McCaul":          26,
    "Ro Khanna":               25,
    "Josh Gottheimer":         24,
    "Dan Crenshaw":            22,
    "Mark Kelly":              22,
    "Pat Fallon":              20,
    "Ted Cruz":                18,
    "Tommy Tuberville":        18,
    "Kevin Hern":              17,
    "Brian Mast":              17,
    "Markwayne Mullin":        16,
    "John Hickenlooper":       16,
    "Jerry Moran":             15,
    "John Hoeven":             15,
    "Susan Collins":           14,
    "Marjorie Taylor Greene":  12,
    "Marie Gluesenkamp Perez": 10,
}

# ── Cody's Book ─────────────────────────────────────────────────────────────
CODY_POSITIONS = [
    "BRK.B", "C", "DVN", "FRCB", "GOOG", "GOOGL", "LYFT", "MCD",
    "MU", "NVDA", "OKLO", "PLTR", "PYPL", "SMR", "SOBO", "SPY",
    "SWPPX", "TRP", "TSM", "VFIAX", "VTSAX", "WWD"
]
CODY_WATCHLIST = ["RTX", "NVDA", "MBLY", "BTC", "SOL"]

# Sector mapping for cross-reference scoring
SECTOR_MAP = {
    "tech":          ["NVDA", "AMD", "INTC", "MSFT", "AAPL", "GOOGL", "GOOG", "META", "CRM", "SNOW", "PLTR", "TSLA", "TSM", "MU", "AVGO", "QCOM", "TXN"],
    "defense":       ["RTX", "LMT", "NOC", "BA", "GD", "L3Harris", "HII", "KTOS", "AXON"],
    "energy":        ["XOM", "CVX", "DVN", "COP", "OXY", "SLB", "HAL", "TRP", "ENB"],
    "finance":       ["JPM", "BAC", "C", "GS", "MS", "BLK", "V", "MA", "PYPL"],
    "semiconductors":["NVDA", "AMD", "INTC", "TSM", "MU", "AVGO", "QCOM", "AMAT", "LRCX"],
    "space":         ["SPCE", "RKLB", "ASTR", "MNTS", "BA", "NOC", "OKLO", "SMR"],
    "nuclear":       ["OKLO", "SMR", "CCJ", "UEC", "NLR"],
    "crypto":        ["BTC", "ETH", "COIN", "MSTR", "RIOT", "MARA", "SOL"],
    "ag":            ["DE", "ADM", "BG", "CF", "MOS", "NTR"],
    "commodities":   ["GLD", "SLV", "GDX", "FCX", "VALE", "RIO", "CF", "MOS"],
}

# Amount range parsing — upper bound for scoring
AMOUNT_UPPER = {
    "$1,001 - $15,000":       15000,
    "$15,001 - $50,000":      50000,
    "$50,001 - $100,000":     100000,
    "$100,001 - $250,000":    250000,
    "$250,001 - $500,000":    500000,
    "$500,001 - $1,000,000":  1000000,
    "Over $1,000,000":        1500000,
    # Alternate formats
    "$1,001-$15,000":         15000,
    "$15,001-$50,000":        50000,
    "$50,001-$100,000":       100000,
    "$100,001-$250,000":      250000,
    "$250,001-$500,000":      500000,
    "$500,001-$1,000,000":    1000000,
}

AMOUNT_EMOJI = {
    15000:   "💵",
    50000:   "💰",
    100000:  "💰💰",
    250000:  "💰💰💰",
    500000:  "🤑",
    1000000: "🤑🤑",
    1500000: "🤑🤑🤑",
}


# ── Persistence ─────────────────────────────────────────────────────────────
def load_seen() -> set:
    try:
        with open(SEEN_FILE, 'r') as f:
            data = json.load(f)
            return set(data.get('seen', []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    # Keep last 5000 IDs to avoid unbounded growth
    seen_list = list(seen)[-5000:]
    with open(SEEN_FILE, 'w') as f:
        json.dump({'seen': seen_list, 'updated': datetime.now(timezone.utc).isoformat()}, f, indent=2)


def make_trade_id(trade: dict) -> str:
    """Generate a stable dedup ID for a trade."""
    name  = trade.get('_name', '')
    tick  = (trade.get('ticker') or '').upper()
    tx_dt = trade.get('transaction_date', '')
    tx_tp = trade.get('type', trade.get('transaction_type', ''))
    amt   = trade.get('amount', '')
    return f"{name}|{tick}|{tx_dt}|{tx_tp}|{amt}"


# ── Discord ──────────────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, demo: bool = False) -> bool:
    if demo:
        print(f"\n{'='*70}")
        print(f"[DEMO] Would post to channel {channel_id}:")
        print(content)
        print('='*70)
        return True

    if not RUGRAT_TOKEN:
        print("[RUGRAT] ERROR: RUGRAT_TOKEN not loaded", file=sys.stderr)
        return False

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {RUGRAT_TOKEN}",
        "Content-Type": "application/json",
    }
    # Chunk at 1990 chars for Discord's 2000-char limit
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[RUGRAT] Discord error {r.status_code}: {r.text[:300]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[RUGRAT] Discord exception: {e}", file=sys.stderr)
            success = False
    return success


# ── Data Fetchers ────────────────────────────────────────────────────────────
def fetch_senate_trades() -> list:
    print("[RUGRAT] Fetching Senate trades...")
    try:
        r = requests.get(SENATE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        trades = []
        for senator in data:
            name = (senator.get('first_name', '') + ' ' + senator.get('last_name', '')).strip()
            for tx in senator.get('transactions', []):
                tx = dict(tx)
                tx['_name']    = name
                tx['_chamber'] = 'Senate'
                trades.append(tx)
        print(f"[RUGRAT] Fetched {len(trades)} Senate transactions")
        return trades
    except Exception as e:
        print(f"[RUGRAT] Senate fetch error: {e}", file=sys.stderr)
        return []


def fetch_house_trades() -> list:
    print("[RUGRAT] Fetching House trades...")
    try:
        r = requests.get(HOUSE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        trades = []
        for tx in data:
            tx = dict(tx)
            tx['_name']    = tx.get('representative', 'Unknown Rep')
            tx['_chamber'] = 'House'
            trades.append(tx)
        print(f"[RUGRAT] Fetched {len(trades)} House transactions")
        return trades
    except Exception as e:
        print(f"[RUGRAT] House fetch error: {e}", file=sys.stderr)
        return []


def fetch_all_trades() -> list:
    return fetch_senate_trades() + fetch_house_trades()


# ── Member Name Matching ─────────────────────────────────────────────────────
def normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    return name.lower().strip()


def match_member(raw_name: str) -> str | None:
    """Match a raw name from the API to a WATCHED_MEMBERS key."""
    raw_norm = normalize_name(raw_name)
    for watched in WATCHED_MEMBERS:
        w_norm = normalize_name(watched)
        # Exact match
        if raw_norm == w_norm:
            return watched
        # Both-direction partial: "pelosi" in "nancy pelosi"
        parts = w_norm.split()
        raw_parts = raw_norm.split()
        last_name = parts[-1] if parts else ''
        raw_last  = raw_parts[-1] if raw_parts else ''
        if last_name and raw_last and last_name == raw_last:
            return watched
        # One name contains the other
        if w_norm in raw_norm or raw_norm in w_norm:
            return watched
    return None


def filter_watched(trades: list) -> list:
    """Keep only trades from WATCHED_MEMBERS."""
    result = []
    for t in trades:
        matched = match_member(t.get('_name', ''))
        if matched:
            t = dict(t)
            t['_matched_name'] = matched
            result.append(t)
    return result


# ── Date Utilities ───────────────────────────────────────────────────────────
def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%B %d, %Y', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(date_str.strip()[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def filter_recent(trades: list, days: int = 7) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for t in trades:
        d = parse_date(t.get('disclosure_date') or t.get('transaction_date') or '')
        if d and d >= cutoff:
            result.append(t)
    return result


# ── Amount Parsing ───────────────────────────────────────────────────────────
def parse_amount_upper(amount_str: str) -> int:
    """Return the upper bound of a congressional disclosure amount range."""
    if not amount_str:
        return 0
    # Try direct lookup
    val = AMOUNT_UPPER.get(amount_str.strip())
    if val:
        return val
    # Regex fallback: pull largest number
    nums = re.findall(r'[\d,]+', amount_str.replace('$', ''))
    if nums:
        vals = [int(n.replace(',', '')) for n in nums]
        return max(vals)
    if 'over' in amount_str.lower() or '1,000,000' in amount_str:
        return 1500000
    return 0


def amount_to_score(upper: int) -> int:
    """Convert upper bound to position size score (0-20)."""
    if upper > 100000:
        return 20
    elif upper >= 50000:
        return 15
    elif upper >= 15000:
        return 10
    else:
        return 5


def get_amount_emoji(upper: int) -> str:
    for threshold in sorted(AMOUNT_EMOJI.keys()):
        if upper <= threshold:
            return AMOUNT_EMOJI[threshold]
    return "🤑🤑🤑"


# ── Committee / Sector Relevance ─────────────────────────────────────────────
COMMITTEE_SECTORS = {
    "Armed Services": ["defense", "space", "semiconductors"],
    "Agriculture":    ["ag", "commodities"],
    "Commerce":       ["tech", "energy", "finance"],
    "Foreign Relations": ["defense", "energy"],
    "Energy":         ["energy", "nuclear", "commodities"],
    "Appropriations": ["defense", "diversified"],
    "Intelligence":   ["defense", "tech", "crypto"],
    "Minority Leader": ["tech", "finance", "diversified"],
    "Homeland Security": ["tech", "defense", "crypto"],
    "Oversight":      ["tech", "diversified"],
    "Financial Services": ["finance", "crypto", "tech"],
    "Foreign Affairs": ["defense", "tech"],
    "Science":        ["tech", "space", "semiconductors"],
    "Budget":         ["diversified", "finance"],
    "Ways and Means": ["finance", "diversified"],
    "Veterans":       ["defense", "emerging"],
}

TICKER_SECTORS = {
    "NVDA": ["tech", "semiconductors"], "AMD": ["tech", "semiconductors"],
    "INTC": ["tech", "semiconductors"], "TSM": ["tech", "semiconductors"],
    "MU": ["tech", "semiconductors"], "AVGO": ["tech", "semiconductors"],
    "GOOGL": ["tech"], "GOOG": ["tech"], "MSFT": ["tech"],
    "AAPL": ["tech"], "META": ["tech"], "PLTR": ["tech", "defense"],
    "RTX": ["defense"], "LMT": ["defense"], "NOC": ["defense"],
    "BA": ["defense", "space"], "GD": ["defense"],
    "RKLB": ["space"], "OKLO": ["nuclear", "space"], "SMR": ["nuclear"],
    "XOM": ["energy"], "CVX": ["energy"], "DVN": ["energy"],
    "COP": ["energy"], "TRP": ["energy"], "ENB": ["energy"],
    "COIN": ["crypto", "finance"], "MSTR": ["crypto"],
    "JPM": ["finance"], "BAC": ["finance"], "C": ["finance"],
    "GS": ["finance"], "PYPL": ["finance", "tech"],
    "BTC": ["crypto"], "ETH": ["crypto"], "SOL": ["crypto"],
    "SPY": ["diversified"], "QQQ": ["tech"], "BRK.B": ["diversified"],
    "DE": ["ag"], "ADM": ["ag", "commodities"],
}


def get_ticker_sectors(ticker: str) -> list:
    """Get sector tags for a ticker."""
    return TICKER_SECTORS.get(ticker.upper(), [])


def score_committee_relevance(member_name: str, ticker: str) -> int:
    """Score committee relevance (0-20). Does their committee give info edge?"""
    info = WATCHED_MEMBERS.get(member_name, {})
    committees = info.get('committees', [])
    specialty  = info.get('specialty', '').split(',')
    ticker_sectors = get_ticker_sectors(ticker)

    max_score = 0
    for committee in committees:
        committee_sectors = COMMITTEE_SECTORS.get(committee, [])
        for csector in committee_sectors:
            if csector in ticker_sectors:
                max_score = max(max_score, 20)
            elif csector in [s.strip() for s in specialty]:
                max_score = max(max_score, 15)

    # Specialty alignment even without committee match
    if max_score == 0:
        for spec in specialty:
            spec = spec.strip()
            if spec in ticker_sectors:
                max_score = max(max_score, 10)

    return max_score


def score_portfolio_overlap(ticker: str) -> tuple[int, str, str]:
    """Returns (score, portfolio_overlap_str, watchlist_match_str)."""
    t = ticker.upper()
    portfolio_str  = "None"
    watchlist_str  = "No"
    score = 0

    if t in [x.upper() for x in CODY_WATCHLIST]:
        score = 15
        watchlist_str = "YES — high conviction name"
        if t in [x.upper() for x in CODY_POSITIONS]:
            portfolio_str = f"**{t}** already in Cody's portfolio"
    elif t in [x.upper() for x in CODY_POSITIONS]:
        score = 10
        portfolio_str = f"**{t}** already in Cody's portfolio"
    else:
        # Check sector overlap
        t_sectors = get_ticker_sectors(t)
        for pos in CODY_POSITIONS:
            pos_sectors = get_ticker_sectors(pos.upper())
            if any(s in t_sectors for s in pos_sectors if s != 'diversified'):
                score = max(score, 5)
                portfolio_str = f"Related sector to {pos}"
                break

    return score, portfolio_str, watchlist_str


def score_macro_regime(ticker: str, transaction_type: str) -> int:
    """Simple macro regime alignment score (0-15). Based on current market context."""
    # In a tariff/trade-war environment (April 2026):
    # - Tech/semis under pressure but still high conviction for dip buys
    # - Defense elevated with geopolitical tensions
    # - Energy mixed
    # - Domestic plays getting premium
    t = ticker.upper()
    t_sectors = get_ticker_sectors(t)
    is_buy  = 'purchase' in transaction_type.lower()
    is_sell = 'sale' in transaction_type.lower()

    if 'defense' in t_sectors:
        return 15 if is_buy else 5
    elif 'semiconductors' in t_sectors or 'tech' in t_sectors:
        return 12 if is_buy else 8
    elif 'energy' in t_sectors:
        return 10
    elif 'nuclear' in t_sectors or 'space' in t_sectors:
        return 13 if is_buy else 5
    elif 'crypto' in t_sectors:
        return 10 if is_buy else 7
    else:
        return 8


# ── Trade Scoring ─────────────────────────────────────────────────────────────
def score_trade(member_name: str, ticker: str, transaction_type: str, amount_str: str) -> dict:
    """
    Score a trade 0-100 and return scoring breakdown.
    Components: member track record (30) + position size (20) + committee relevance (20) +
                portfolio overlap (15) + macro regime (15)
    """
    # 1. Member track record (0-30)
    track_record = MEMBER_SCORES.get(member_name, 10)

    # 2. Position size (0-20)
    upper = parse_amount_upper(amount_str)
    size_score = amount_to_score(upper)

    # 3. Committee relevance (0-20)
    committee_score = score_committee_relevance(member_name, ticker)

    # 4. Portfolio overlap (0-15)
    overlap_score, portfolio_str, watchlist_str = score_portfolio_overlap(ticker)

    # 5. Macro regime (0-15)
    macro_score = score_macro_regime(ticker, transaction_type)

    total = track_record + size_score + committee_score + overlap_score + macro_score
    total = min(total, 100)

    return {
        'total':            total,
        'track_record':     track_record,
        'size_score':       size_score,
        'committee_score':  committee_score,
        'overlap_score':    overlap_score,
        'macro_score':      macro_score,
        'portfolio_str':    portfolio_str,
        'watchlist_str':    watchlist_str,
        'amount_upper':     upper,
    }


def get_tier(score: int) -> str:
    if score >= 75:
        return "HIGH_CONVICTION"
    elif score >= 50:
        return "WATCH"
    elif score >= 25:
        return "NOTE"
    else:
        return "IGNORE"


# ── Yahoo Finance ─────────────────────────────────────────────────────────────
def fetch_stock_data(ticker: str) -> dict:
    """Pull current price, 5-day change, basic technicals via Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json()
        result = data.get('chart', {}).get('result', [])
        if not result:
            return {}

        meta   = result[0].get('meta', {})
        closes = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])

        current_price = meta.get('regularMarketPrice', 0)
        prev_close    = meta.get('previousClose', 0)
        currency      = meta.get('currency', 'USD')

        pct_change_1d = 0.0
        if prev_close and prev_close != 0:
            pct_change_1d = ((current_price - prev_close) / prev_close) * 100

        pct_change_5d = 0.0
        valid_closes = [c for c in closes if c is not None]
        if len(valid_closes) >= 2:
            oldest = valid_closes[0]
            newest = valid_closes[-1]
            if oldest and oldest != 0:
                pct_change_5d = ((newest - oldest) / oldest) * 100

        # Rough 50-day MA check — compare current to 52-week range midpoint as proxy
        fifty_two_low  = meta.get('fiftyTwoWeekLow', 0)
        fifty_two_high = meta.get('fiftyTwoWeekHigh', 0)
        ma_status = "N/A"
        if fifty_two_low and fifty_two_high:
            midpoint = (fifty_two_low + fifty_two_high) / 2
            if current_price > midpoint:
                ma_status = "Above mid-range"
            else:
                ma_status = "Below mid-range"

        return {
            'price':        current_price,
            'currency':     currency,
            'pct_1d':       round(pct_change_1d, 2),
            'pct_5d':       round(pct_change_5d, 2),
            'ma_status':    ma_status,
            '52w_high':     fifty_two_high,
            '52w_low':      fifty_two_low,
        }
    except Exception as e:
        print(f"[RUGRAT] Yahoo Finance error for {ticker}: {e}", file=sys.stderr)
        return {}


def fetch_news_headline(ticker: str) -> str:
    """Pull most recent news headline from Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&quotesCount=0&newsCount=3"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return "No recent news found"
        data = r.json()
        news = data.get('news', [])
        if news:
            headline = news[0].get('title', 'No headline')
            return headline[:120]
        return "No recent news found"
    except Exception:
        return "News fetch failed"


# ── Alert Formatting ──────────────────────────────────────────────────────────
def format_high_conviction_alert(trade: dict, scored: dict, stock_data: dict, news: str) -> str:
    """Build the full RUGRAT ALERT post for HIGH CONVICTION trades."""
    member_name  = trade.get('_matched_name', trade.get('_name', 'Unknown'))
    info         = WATCHED_MEMBERS.get(member_name, {})
    party        = info.get('party', '?')
    committees   = ', '.join(info.get('committees', ['Unknown']))
    specialty    = info.get('specialty', '').replace(',', '/')

    ticker       = (trade.get('ticker') or 'N/A').upper()
    tx_type      = trade.get('type', trade.get('transaction_type', 'Unknown'))
    amount       = trade.get('amount', 'Unknown')
    filed        = trade.get('disclosure_date', 'N/A')
    tx_date      = trade.get('transaction_date', 'N/A')

    # Calculate filing lag
    lag_str = ""
    fd = parse_date(filed)
    td = parse_date(tx_date)
    if fd and td:
        lag_days = (fd - td).days
        lag_str  = f" ({lag_days}-day lag)"

    # Transaction direction
    is_buy = 'purchase' in tx_type.lower()
    action = "BOUGHT" if is_buy else "SOLD"

    # Stock context
    price_str = "N/A"
    change_str = ""
    ma_str = ""
    if stock_data:
        price_str  = f"${stock_data.get('price', 0):.2f}"
        pct_1d     = stock_data.get('pct_1d', 0)
        pct_5d     = stock_data.get('pct_5d', 0)
        sign_1d    = "+" if pct_1d >= 0 else ""
        sign_5d    = "+" if pct_5d >= 0 else ""
        change_str = f"{sign_1d}{pct_1d}% today | {sign_5d}{pct_5d}% 5d"
        ma_str     = stock_data.get('ma_status', '')

    score      = scored['total']
    tier_emoji = "🔴" if score >= 75 else "🟡"

    # Sell signals get inverted framing
    sell_note = ""
    if not is_buy and member_name in ["Nancy Pelosi", "Michael McCaul", "Ro Khanna"]:
        sell_note = f"\n⚠️ WARNING: {member_name.split()[-1]} SELLING is a bearish signal — consider reducing exposure."

    # Build RUGRAT's take
    take = build_rugrats_take(member_name, ticker, tx_type, amount, scored, stock_data)

    # Overlap / watchlist
    portfolio_str = scored.get('portfolio_str', 'None')
    watchlist_str = scored.get('watchlist_str', 'No')

    lines = [
        f"🏛️ **RUGRAT ALERT** — Congressional Trade Detected",
        f"",
        f"👤 **Member:** {member_name} ({party})",
        f"🏢 **Committee:** {committees} | **Specialty:** {specialty}",
        f"📊 **Trade:** {action} ${ticker} {amount}",
        f"📅 **Filed:** {filed} | **Trade Date:** {tx_date}{lag_str}",
        f"",
        f"💼 **Portfolio Overlap:** {portfolio_str}",
        f"📋 **Watchlist Match:** {watchlist_str}",
        f"🎯 **Committee Edge:** {scored['committee_score']}/20 pts",
        f"",
        f"📈 **{ticker} Context:** {price_str} | {change_str}",
        f"📊 **52w Range Position:** {ma_str}",
        f"📰 **Recent News:** {news}",
        f"",
        f"⚡ **SCORE: {score}/100** — {tier_emoji} {'HIGH CONVICTION FOLLOW' if score >= 75 else 'WATCH'}",
        sell_note,
        f"",
        f"💡 **RUGRAT'S TAKE:**",
        take,
        f"",
        f"🔗 **Source:** congress.gov disclosure | {'Senate' if trade.get('_chamber') == 'Senate' else 'House'} Stock Watcher",
    ]
    return "\n".join(l for l in lines if l is not None)


def format_watch_alert(trade: dict, scored: dict) -> str:
    """Compact format for WATCH tier (50-74)."""
    member_name = trade.get('_matched_name', trade.get('_name', 'Unknown'))
    info        = WATCHED_MEMBERS.get(member_name, {})
    party       = info.get('party', '?')
    ticker      = (trade.get('ticker') or 'N/A').upper()
    tx_type     = trade.get('type', trade.get('transaction_type', 'Unknown'))
    amount      = trade.get('amount', 'Unknown')
    filed       = trade.get('disclosure_date', 'N/A')
    tx_date     = trade.get('transaction_date', 'N/A')
    is_buy      = 'purchase' in tx_type.lower()
    action      = "BUY" if is_buy else "SELL"

    return (
        f"🟡 **RUGRAT MONITOR** — {member_name} ({party}) | "
        f"${ticker} | {action} | {amount} | "
        f"Filed: {filed} | Score: {scored['total']}/100"
    )


def build_rugrats_take(member_name: str, ticker: str, tx_type: str, amount: str, scored: dict, stock_data: dict) -> str:
    """Generate a punchy, Stratton-voice analysis of the trade."""
    info    = WATCHED_MEMBERS.get(member_name, {})
    party   = info.get('party', '?')
    is_buy  = 'purchase' in tx_type.lower()
    upper   = scored.get('amount_upper', 0)
    t       = ticker.upper()

    # Price context
    price_context = ""
    if stock_data:
        price = stock_data.get('price', 0)
        pct   = stock_data.get('pct_1d', 0)
        if price:
            price_context = f" at ${price:.2f} ({'+' if pct >= 0 else ''}{pct}% today)"

    # Build the take based on member + sector
    t_sectors = get_ticker_sectors(t)
    specialty = info.get('specialty', '').split(',')

    # Overlap note
    overlap_note = ""
    t_up = t.upper()
    if t_up in [x.upper() for x in CODY_WATCHLIST]:
        overlap_note = f"{t} is already on Cody's watchlist — this is confirmation. "
    elif t_up in [x.upper() for x in CODY_POSITIONS]:
        overlap_note = f"Cody already holds {t} — this congressional conviction adds to the thesis. "

    conviction = "HIGH" if scored['total'] >= 75 else "MODERATE"
    action_word = "buying" if is_buy else "dumping"

    # Member-specific commentary
    member_context = {
        "Nancy Pelosi":            f"Pelosi {action_word} is always newsworthy — she's the GOAT of congressional trading.",
        "Michael McCaul":          f"McCaul's portfolio is massive and his defense/tech calls have been consistently sharp.",
        "Ro Khanna":               f"Khanna has one of the best tech track records in Congress — Silicon Valley insider knowledge.",
        "Josh Gottheimer":         f"Most active trader in Congress. This many filings means he's paying close attention.",
        "Dan Crenshaw":            f"Crenshaw has a solid crypto/tech read — Intelligence Committee access doesn't hurt.",
        "Mark Kelly":              f"Kelly's defense/space committee gives him real edge on {t}.",
        "Nancy Pelosi":            f"This is the big one — Pelosi signal.",
        "Tommy Tuberville":        f"Tuberville trades high volume, mostly commodities. Size matters here.",
        "Ted Cruz":                f"Cruz has energy/tech committee access — worth noting the sector alignment.",
        "Pat Fallon":              f"Fallon moves a lot of paper. When the volume spikes on one name, pay attention.",
    }.get(member_name, f"{member_name} ({party}) moving size into {t}.")

    size_note = ""
    if upper >= 500000:
        size_note = f" ${upper:,.0f}+ is serious conviction — not a casual trade."
    elif upper >= 100000:
        size_note = f" ${upper:,.0f} position is meaningful size."

    take_lines = [
        f"{member_context}{size_note}",
        f"{overlap_note}{conviction} conviction trade{price_context}.",
    ]

    if not is_buy:
        take_lines.append(f"SELL signal — consider whether to reduce {t} exposure if you hold it.")

    return " ".join(take_lines)


# ── Main Processing Pipeline ──────────────────────────────────────────────────
def process_trades(trades: list, days: int = 7, demo: bool = False, force_new: bool = False) -> list:
    """
    Filter, score, and post alerts for watched member trades.
    Returns list of processed trade dicts with scores.
    """
    # Filter to watched members only
    watched = filter_watched(trades)
    # Filter to recent
    recent  = filter_recent(watched, days=days)

    print(f"[RUGRAT] {len(recent)} trades from watched members in last {days} days")

    # Load seen IDs
    seen = load_seen() if not force_new else set()
    new_seen = set(seen)
    processed = []

    for trade in recent:
        trade_id = make_trade_id(trade)
        member_name = trade.get('_matched_name', trade.get('_name', ''))
        ticker      = (trade.get('ticker') or '').upper()
        tx_type     = trade.get('type', trade.get('transaction_type', ''))
        amount      = trade.get('amount', '')

        if not ticker or ticker in ('--', 'N/A', ''):
            continue

        # Skip already-seen (unless force_new)
        if trade_id in seen and not force_new:
            continue

        # Score it
        scored = score_trade(member_name, ticker, tx_type, amount)
        tier   = get_tier(scored['total'])

        if tier == "IGNORE":
            new_seen.add(trade_id)
            continue

        print(f"[RUGRAT] {member_name} | {ticker} | {tx_type} | Score: {scored['total']} | Tier: {tier}")

        # Fetch stock data and news for WATCH and above
        stock_data = {}
        news       = "N/A"
        if tier in ("HIGH_CONVICTION", "WATCH") and not demo:
            stock_data = fetch_stock_data(ticker)
            news       = fetch_news_headline(ticker)
        elif demo:
            stock_data = {'price': 123.45, 'pct_1d': 2.3, 'pct_5d': -1.2, 'ma_status': 'Above mid-range', '52w_high': 175.0, '52w_low': 80.0}
            news       = "[Demo news headline for " + ticker + "]"

        trade['_score']      = scored
        trade['_tier']       = tier
        trade['_stock_data'] = stock_data
        trade['_news']       = news
        processed.append(trade)

        # Post based on tier
        if tier == "HIGH_CONVICTION":
            msg = format_high_conviction_alert(trade, scored, stock_data, news)
            post_discord(CHANNEL_SENATOR_TRACKER, msg, demo=demo)
            post_discord(CHANNEL_ACTIVE_PLAYS,    msg, demo=demo)

        elif tier == "WATCH":
            msg = format_watch_alert(trade, scored)
            post_discord(CHANNEL_SENATOR_TRACKER, msg, demo=demo)

        # NOTE tier is batched in summary — mark seen but don't post individually
        new_seen.add(trade_id)

    # Batch-post NOTE tier trades if any
    note_trades = [t for t in processed if t.get('_tier') == 'NOTE']
    if note_trades:
        lines = [f"📋 **RUGRAT — Notable Trades (NOTE tier, {len(note_trades)} trades):**"]
        for t in note_trades[:10]:
            mn  = t.get('_matched_name', t.get('_name', ''))
            tk  = (t.get('ticker') or '').upper()
            typ = t.get('type', t.get('transaction_type', ''))
            amt = t.get('amount', '')
            sc  = t.get('_score', {}).get('total', 0)
            lines.append(f"  • {mn} | ${tk} | {typ} | {amt} | Score: {sc}")
        post_discord(CHANNEL_SENATOR_TRACKER, "\n".join(lines), demo=demo)

    # Save updated seen IDs
    if not demo:
        save_seen(new_seen)

    return processed


# ── Run Modes ─────────────────────────────────────────────────────────────────
def run_scan(demo: bool = False):
    """Full 7-day scan of all watched members."""
    print("[RUGRAT] === FULL SCAN — Last 7 Days ===")
    trades    = fetch_all_trades()
    processed = process_trades(trades, days=7, demo=demo)
    print(f"[RUGRAT] Scan complete. {len(processed)} new trades processed.")
    if not processed:
        msg = "🏛️ **RUGRAT** — Scan complete. No new trades from watched members in last 7 days."
        post_discord(CHANNEL_SENATOR_TRACKER, msg, demo=demo)


def run_recent(demo: bool = False, post: bool = True):
    """24-hour scan."""
    print("[RUGRAT] === RECENT SCAN — Last 24h ===")
    trades    = fetch_all_trades()
    processed = process_trades(trades, days=1, demo=demo)
    print(f"[RUGRAT] Recent scan complete. {len(processed)} new trades processed.")
    if not processed:
        print("[RUGRAT] No new trades from watched members in last 24h.")
    _write_status('rugrat', {
        'mode': 'recent_scan',
        'trades_processed': len(processed),
    })


def run_member(member_query: str, demo: bool = False):
    """Show all recent trades for a specific member."""
    print(f"[RUGRAT] === MEMBER SCAN: {member_query} ===")
    trades = fetch_all_trades()

    # Find matching member key
    matched_key = None
    for key in WATCHED_MEMBERS:
        if member_query.lower() in key.lower():
            matched_key = key
            break

    if not matched_key:
        print(f"[RUGRAT] No watched member matching '{member_query}'")
        print(f"[RUGRAT] Watched members: {', '.join(WATCHED_MEMBERS.keys())}")
        return

    # Filter to this member
    member_trades = []
    for t in trades:
        if match_member(t.get('_name', '')) == matched_key:
            t = dict(t)
            t['_matched_name'] = matched_key
            member_trades.append(t)

    # Get last 30 days for member view
    recent = filter_recent(member_trades, days=30)
    print(f"[RUGRAT] Found {len(recent)} trades for {matched_key} in last 30 days")

    if not recent:
        print(f"[RUGRAT] No recent trades found for {matched_key}")
        return

    # Sort by date desc
    def sort_key(t):
        d = parse_date(t.get('disclosure_date') or t.get('transaction_date') or '')
        return d or datetime.min.replace(tzinfo=timezone.utc)

    recent_sorted = sorted(recent, key=sort_key, reverse=True)[:20]

    info    = WATCHED_MEMBERS[matched_key]
    party   = info.get('party', '?')
    chamber = info.get('chamber', '')
    committees = ', '.join(info.get('committees', []))

    lines = [
        f"🏛️ **RUGRAT — {matched_key} Trade History**",
        f"Chamber: {chamber} | Party: {party} | Committees: {committees}",
        f"Track Record Score: {MEMBER_SCORES.get(matched_key, 10)}/30",
        f"Last 30 days: {len(recent)} trades | Showing top 20",
        "",
    ]

    for t in recent_sorted:
        ticker  = (t.get('ticker') or 'N/A').upper()
        tx_type = t.get('type', t.get('transaction_type', 'Unknown'))
        amount  = t.get('amount', 'Unknown')
        filed   = t.get('disclosure_date', 'N/A')
        tx_date = t.get('transaction_date', 'N/A')
        is_buy  = 'purchase' in tx_type.lower()
        emoji   = "🟢" if is_buy else "🔴"

        scored = score_trade(matched_key, ticker, tx_type, amount)
        lines.append(f"{emoji} ${ticker} | {tx_type} | {amount} | Filed: {filed} | Trade: {tx_date} | Score: {scored['total']}/100")

    output = "\n".join(lines)
    print(output)
    if demo:
        post_discord(CHANNEL_SENATOR_TRACKER, output, demo=True)


def run_ticker(ticker: str, demo: bool = False):
    """Find all congressional trades in a specific ticker."""
    ticker = ticker.upper()
    print(f"[RUGRAT] === TICKER SCAN: ${ticker} ===")
    trades = fetch_all_trades()

    # Filter by ticker
    ticker_trades = [t for t in trades if (t.get('ticker') or '').upper() == ticker]
    recent        = filter_recent(ticker_trades, days=30)

    print(f"[RUGRAT] Found {len(recent)} trades in ${ticker} in last 30 days (all members)")

    # Also check watched members specifically
    watched_ticker = [t for t in recent if match_member(t.get('_name', '')) is not None]

    lines = [
        f"🎯 **RUGRAT — Congressional Activity: ${ticker}**",
        f"Last 30 days: {len(recent)} total trades | {len(watched_ticker)} from watched members",
        "",
    ]

    # Sort by date
    def sort_key(t):
        d = parse_date(t.get('disclosure_date') or t.get('transaction_date') or '')
        return d or datetime.min.replace(tzinfo=timezone.utc)

    for t in sorted(recent, key=sort_key, reverse=True)[:20]:
        name    = t.get('_name', 'Unknown')
        matched = match_member(name)
        tx_type = t.get('type', t.get('transaction_type', 'Unknown'))
        amount  = t.get('amount', 'Unknown')
        filed   = t.get('disclosure_date', 'N/A')
        is_buy  = 'purchase' in tx_type.lower()
        emoji   = "🟢" if is_buy else "🔴"
        star    = "⭐" if matched else ""

        lines.append(f"{emoji}{star} {name} | {tx_type} | {amount} | Filed: {filed}")

    if not recent:
        lines.append("No congressional trades found in this ticker for last 30 days.")

    output = "\n".join(lines)
    print(output)

    if demo or watched_ticker:
        post_discord(CHANNEL_SENATOR_TRACKER, output, demo=demo)
        if watched_ticker and not demo:
            post_discord(CHANNEL_ACTIVE_PLAYS, f"🎯 Congressional signal on **${ticker}** — {len(watched_ticker)} trades from watched members. Check #senator-tracker.", demo=False)


def run_summary(demo: bool = False):
    """Post weekly summary of top trades to senator-tracker."""
    print("[RUGRAT] === WEEKLY SUMMARY ===")
    trades  = fetch_all_trades()
    watched = filter_watched(trades)
    recent  = filter_recent(watched, days=7)

    print(f"[RUGRAT] {len(recent)} trades from watched members in last 7 days")

    # Score all of them
    scored_trades = []
    for t in recent:
        member_name = t.get('_matched_name', t.get('_name', ''))
        ticker      = (t.get('ticker') or '').upper()
        tx_type     = t.get('type', t.get('transaction_type', ''))
        amount      = t.get('amount', '')

        if not ticker or ticker in ('--', 'N/A', ''):
            continue

        scored = score_trade(member_name, ticker, tx_type, amount)
        t = dict(t)
        t['_score'] = scored
        scored_trades.append(t)

    # Sort by score desc
    scored_trades.sort(key=lambda x: x.get('_score', {}).get('total', 0), reverse=True)

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [
        f"📊 **RUGRAT WEEKLY INTELLIGENCE REPORT — Week of {now}**",
        f"Watched members: {len(WATCHED_MEMBERS)} | Trades analyzed: {len(scored_trades)}",
        "",
        "**🔝 TOP TRADES THIS WEEK:**",
        "",
    ]

    for i, t in enumerate(scored_trades[:10], 1):
        mn     = t.get('_matched_name', t.get('_name', ''))
        tk     = (t.get('ticker') or 'N/A').upper()
        typ    = t.get('type', t.get('transaction_type', ''))
        amt    = t.get('amount', '')
        sc     = t.get('_score', {}).get('total', 0)
        tier   = get_tier(sc)
        is_buy = 'purchase' in typ.lower()
        emoji  = "🟢" if is_buy else "🔴"
        tier_e = "🔴" if tier == "HIGH_CONVICTION" else "🟡" if tier == "WATCH" else "⚪"

        lines.append(f"{i}. {emoji} **{mn}** | ${tk} | {typ} | {amt}")
        lines.append(f"   Score: {sc}/100 {tier_e} {tier.replace('_', ' ')}")
        lines.append("")

    # Summary stats
    high_conv = sum(1 for t in scored_trades if get_tier(t.get('_score', {}).get('total', 0)) == "HIGH_CONVICTION")
    watch     = sum(1 for t in scored_trades if get_tier(t.get('_score', {}).get('total', 0)) == "WATCH")
    lines.append(f"**TOTALS:** 🔴 {high_conv} High Conviction | 🟡 {watch} Watch")

    output = "\n".join(lines)
    post_discord(CHANNEL_SENATOR_TRACKER, output, demo=demo)
    if not demo:
        print(output)


def run_demo():
    """Demo mode — generate sample output without hitting Discord or external APIs."""
    print("[RUGRAT] === DEMO MODE — No Discord posts ===")
    print("[RUGRAT] Generating synthetic trade alerts...\n")

    sample_trades = [
        {
            '_name':             'Nancy Pelosi',
            '_chamber':          'House',
            '_matched_name':     'Nancy Pelosi',
            'ticker':            'NVDA',
            'type':              'Purchase',
            'amount':            '$250,001 - $500,000',
            'disclosure_date':   datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'transaction_date':  (datetime.now(timezone.utc) - timedelta(days=6)).strftime('%Y-%m-%d'),
            'asset_description': 'NVIDIA Corporation — Call Options',
        },
        {
            '_name':             'Dan Crenshaw',
            '_chamber':          'House',
            '_matched_name':     'Dan Crenshaw',
            'ticker':            'PLTR',
            'type':              'Purchase',
            'amount':            '$50,001 - $100,000',
            'disclosure_date':   datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'transaction_date':  (datetime.now(timezone.utc) - timedelta(days=4)).strftime('%Y-%m-%d'),
            'asset_description': 'Palantir Technologies Inc',
        },
        {
            '_name':             'Tommy Tuberville',
            '_chamber':          'Senate',
            '_matched_name':     'Tommy Tuberville',
            'ticker':            'SPY',
            'type':              'Sale (Full)',
            'amount':            '$100,001 - $250,000',
            'disclosure_date':   datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'transaction_date':  (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%d'),
            'asset_description': 'SPDR S&P 500 ETF Trust',
        },
        {
            '_name':             'Ro Khanna',
            '_chamber':          'House',
            '_matched_name':     'Ro Khanna',
            'ticker':            'TSM',
            'type':              'Purchase',
            'amount':            '$50,001 - $100,000',
            'disclosure_date':   datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'transaction_date':  (datetime.now(timezone.utc) - timedelta(days=3)).strftime('%Y-%m-%d'),
            'asset_description': 'Taiwan Semiconductor Manufacturing',
        },
    ]

    for trade in sample_trades:
        member_name = trade['_matched_name']
        ticker      = trade['ticker']
        tx_type     = trade['type']
        amount      = trade['amount']

        scored = score_trade(member_name, ticker, tx_type, amount)
        tier   = get_tier(scored['total'])

        stock_data = {
            'price':     145.82,
            'pct_1d':    2.3,
            'pct_5d':   -4.1,
            'ma_status': 'Above mid-range',
            '52w_high':  175.0,
            '52w_low':   80.0,
        }
        news = f"[Demo] {ticker} rallies on strong earnings guidance, analysts raise targets"

        print(f"\n{'─'*70}")
        print(f"TRADE: {member_name} | {ticker} | {tx_type} | {amount}")
        print(f"SCORE: {scored['total']}/100 | TIER: {tier}")
        print(f"  Track Record: {scored['track_record']}/30")
        print(f"  Size Score:   {scored['size_score']}/20")
        print(f"  Committee:    {scored['committee_score']}/20")
        print(f"  Overlap:      {scored['overlap_score']}/15")
        print(f"  Macro:        {scored['macro_score']}/15")

        if tier == "HIGH_CONVICTION":
            msg = format_high_conviction_alert(trade, scored, stock_data, news)
            print(f"\n[DEMO OUTPUT — Would post to #senator-tracker AND #active-plays]:")
            print(msg)
        elif tier == "WATCH":
            msg = format_watch_alert(trade, scored)
            print(f"\n[DEMO OUTPUT — Would post to #senator-tracker]:")
            print(msg)
        else:
            print(f"[DEMO] Tier: {tier} — would batch in daily summary")

    print(f"\n{'─'*70}")
    print("[RUGRAT] Demo complete. Run with --scan to process live data.")


# ── Entry Point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='RUGRAT — Congressional Trade Intelligence System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python rugrat.py --scan               Full 7-day scan, post alerts
  python rugrat.py --recent             Last 24 hours only
  python rugrat.py --member "Pelosi"    All recent trades for one member
  python rugrat.py --ticker NVDA        Congressional activity in NVDA
  python rugrat.py --summary            Weekly summary post
  python rugrat.py --demo               Demo mode, no Discord or external APIs
        """
    )
    parser.add_argument('--scan',    action='store_true',  help='Full 7-day scan, post alerts')
    parser.add_argument('--recent',  action='store_true',  help='Last 24h scan')
    parser.add_argument('--member',  type=str,             help='Show all recent trades for one member')
    parser.add_argument('--ticker',  type=str,             help='Find all congressional trades in a ticker')
    parser.add_argument('--summary', action='store_true',  help='Post weekly summary')
    parser.add_argument('--demo',    action='store_true',  help='Demo mode (no Discord posting)')
    parser.add_argument('--force',   action='store_true',  help='Ignore seen cache, reprocess all')

    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.scan:
        run_scan(demo=False)
    elif args.recent:
        run_recent(demo=False)
    elif args.member:
        run_member(args.member, demo=False)
    elif args.ticker:
        run_ticker(args.ticker, demo=False)
    elif args.summary:
        run_summary(demo=False)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == '__main__':
    main()
