"""
Stratton Intelligence Dashboard — FastAPI Backend
Serves data from The Firm multi-agent trading system running on Atlas.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

log = logging.getLogger("stratton-api")

# Paths
DATA_DIR = Path("/home/cody/stratton/data")
CONFIG_DIR = Path("/home/cody/stratton/config")
SHARED_STATE = DATA_DIR / "shared_state.json"
WEATHER_TRADES = DATA_DIR / "weather_paper_trades.json"
BRAD_TRADES = DATA_DIR / "brad_paper_trades.json"
EVAL_STORE = DATA_DIR / "eval_store.json"
FIRM_LOG = CONFIG_DIR / "firm.log"
BOT_TOKENS = CONFIG_DIR / "bot-tokens.env"

# Portfolio average costs
AVG_COSTS = {
    "BRK-B": 349.65, "C": 41.38, "DVN": 65.05, "FRCB": 13.905,
    "GOOG": 15.7036, "GOOGL": 15.7953, "LYFT": 57.99, "MCD": 259.125,
    "MU": 101.8758, "NVDA": 41.517, "OKLO": 97.8137, "PLTR": 162.20,
    "PYPL": 74.965, "SMR": 19.852, "SOBO": 16.9128, "SPY": 449.16,
    "SWPPX": 13.8159, "TRP": 34.3974, "TSM": 196.8807,
    "VFIAX": 439.0897, "VTSAX": 108.7681, "WWD": 24.116,
}

# Display names for each ticker
TICKER_NAMES = {
    "BRK-B": "Berkshire Hathaway", "C": "Citigroup", "DVN": "Devon Energy",
    "FRCB": "First Republic Bank", "GOOG": "Alphabet (GOOG)", "GOOGL": "Alphabet (GOOGL)",
    "LYFT": "Lyft", "MCD": "McDonald's", "MU": "Micron Technology",
    "NVDA": "NVIDIA", "OKLO": "Oklo Inc", "PLTR": "Palantir", "PYPL": "PayPal",
    "SMR": "NuScale Power", "SOBO": "South Bow Corp", "SPY": "S&P 500 ETF",
    "SWPPX": "Schwab S&P 500 Index", "TRP": "TC Energy",
    "TSM": "Taiwan Semiconductor", "VFIAX": "Vanguard 500 Index Admiral",
    "VTSAX": "Vanguard Total Market", "WWD": "Woodward Inc",
}

app = FastAPI(title="Stratton Intelligence Dashboard API", docs_url="/docs")

from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()

# ── Donnie module cache (fixes [Errno 24] Too many open files) ───────────

_donnie_module = None

def _get_donnie():
    global _donnie_module
    if _donnie_module is None:
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("economics", str(Path("/home/cody/stratton/bots/economics.py")))
            _donnie_module = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_donnie_module)
            log.info("Donnie module cached")
        except Exception as e:
            log.warning(f"Donnie module load failed: {e}")
    return _donnie_module

# ── Market title cache ───────────────────────────────────────────────────

_market_title_cache = {}

def _get_market_title(ticker: str) -> str:
    if ticker in _market_title_cache:
        return _market_title_cache[ticker]
    try:
        donnie = _get_donnie()
        if donnie:
            data = donnie.kalshi_get(f"/markets/{ticker}")
            title = data.get("market", {}).get("title", "") or data.get("title", "")
            if title:
                _market_title_cache[ticker] = title
                return title
    except Exception:
        pass
    # Parse ticker into readable name as fallback
    readable = ticker.replace("KX","").replace("-26APR30"," Apr 30").replace("-26JUN"," Jun").replace("-27"," 2027").replace("GDP","GDP Q1").replace("T2.0"," > 2%").replace("T2.5"," > 2.5%").replace("T3.0"," > 3%").replace("T1.0"," > 1%").replace("T1.5"," > 1.5%").replace("HORMUZNORM","Hormuz Normal").replace("USAIRANAGREEMENT","US-Iran Deal").replace("ALIENS","Alien Disclosure").replace("LAYOFFSYINFO","Tech Layoffs (494K)").replace("DOTPLOT","Fed Dot Plot 3.4%").replace("FEDMEET","Emergency Fed Meeting").replace("FEDDECISION","Fed Rate Decision")
    _market_title_cache[ticker] = readable
    return readable


@app.on_event("startup")
async def startup():
    from dotenv import load_dotenv
    load_dotenv(str(BOT_TOKENS))
    sys.path.insert(0, "/home/cody/stratton/bots")
    # Pre-warm donnie module cache
    _get_donnie()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_json(path: Path):
    with open(path) as f:
        return json.load(f)


def _safe(func):
    """Decorator: catch all exceptions and return JSON error."""
    from functools import wraps
    @wraps(func)
    async def wrapper(*a, **kw):
        try:
            return await func(*a, **kw)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    return wrapper


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
@_safe
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
@_safe
async def status():
    # Service check
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "stratton-firm"],
            capture_output=True, text=True, timeout=5,
        )
        service_running = r.stdout.strip() == "active"
    except Exception:
        service_running = False

    # Shared state
    state = _read_json(SHARED_STATE)
    agent_status = state.get("agent_status", {})
    agents = {}
    for key, val in agent_status.items():
        if isinstance(val, dict) and "last_run" in val:
            agents[key] = val

    # Augment agent status from firm.log (catches agents that don't write to shared_state)
    try:
        log_agent_map = {
            # weather scanner
            "weather-bot": "weather",
            # economics engine (was donnie)
            "economics": "economics",
            "ECONOMICS": "economics",
            "donnie-v3": "economics",
            "donnie": "economics",
            "DONNIE": "economics",
            "DONNIE V2": "economics",
            # sports engine (was brad)
            "sports": "sports",
            "SPORTS": "sports",
            "brad": "sports",
            "BRAD": "sports",
            # congressional tracker (was rugrat)
            "congressional": "congressional",
            "CONGRESSIONAL": "congressional",
            "rugrat": "congressional",
            "RUGRAT": "congressional",
            # options desk (was jordan)
            "options": "options",
            "OPTIONS": "options",
            "jordan": "options",
            "JORDAN": "options",
            # system monitor
            "supervisor": "supervisor",
            "SUPERVISOR": "supervisor",
            # weather intelligence
            "weather_intel": "weather_intel",
            "WEATHER_INTEL": "weather_intel",
            # crypto tracker
            "crypto": "crypto",
            "CRYPTO": "crypto",
        }
        log_last_seen = {}
        if FIRM_LOG.exists():
            lines = FIRM_LOG.read_text(errors="replace").splitlines()
            for line in reversed(lines[-2000:]):
                # Parse: [2026-04-25 03:26:07,123] INFO [weather-bot] message
                import re
                m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not m:
                    continue
                ts_str = m.group(1)
                for log_name, agent_key in log_agent_map.items():
                    if f"[{log_name}]" in line and agent_key not in log_last_seen:
                        log_last_seen[agent_key] = ts_str
                if len(log_last_seen) >= len(set(log_agent_map.values())):
                    break
        # Merge log-based times into agents dict (don't overwrite shared_state entries)
        for agent_key, ts_str in log_last_seen.items():
            iso_ts = ts_str.replace(" ", "T") + "Z"
            # also compute numeric ts for frontend comparisons
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                numeric_ts = dt.replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                numeric_ts = 0
            if agent_key not in agents:
                agents[agent_key] = {"last_run": iso_ts, "last_run_ts": numeric_ts, "source": "log"}
            elif "last_run" not in agents[agent_key]:
                agents[agent_key]["last_run"] = iso_ts
                agents[agent_key]["last_run_ts"] = numeric_ts
    except Exception as e:
        log.warning(f"Log-based agent health augmentation failed: {e}")

    # Kalshi positions via cached donnie
    kalshi_position_count = 0
    try:
        donnie = _get_donnie()
        if donnie:
            _positions = donnie.get_open_positions() or []
            kalshi_position_count = len(_positions)
    except Exception:
        pass
    kalshi = state.get("kalshi_portfolio", {})
    positions = kalshi.get("open_positions", [])

    # RAG stats
    rag_stats = {}
    try:
        from rag_store import store_stats, init_store
        init_store()
        rag_stats = store_stats()
    except Exception as e:
        rag_stats = {"error": str(e)}

    # Eval count
    try:
        evals = _read_json(EVAL_STORE)
        eval_trades = len(evals) if isinstance(evals, list) else len(evals.get("evaluations", evals.get("trades", [])))
    except Exception:
        eval_trades = 0

    # Weather win rate (post-tuning only: Apr 19+)
    try:
        trades = _read_json(WEATHER_TRADES)
        if isinstance(trades, dict):
            trades = trades.get("trades", [])
        # Filter to post-tuning trades only
        trades = [t for t in trades if t.get("date", "")[:10] >= "2026-04-19"]
        resolved = [t for t in trades if t.get("status") in ("WIN", "LOSS")]
        wins = [t for t in resolved if t.get("status") == "WIN"]
        weather_win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else 0
    except Exception:
        weather_win_rate = 0

    return {
        "service_running": service_running,
        "uptime_seconds": int(time.time() - START_TIME),
        "agents": agents,
        "kalshi_positions": kalshi_position_count or len(positions),
        "rag_stats": rag_stats,
        "eval_trades": eval_trades,
        "weather_win_rate": weather_win_rate,
    }


@app.get("/api/activity")
@_safe
async def activity(n: int = Query(100, le=500), agent: str = Query("")):
    pattern = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\] (\w+) \[([^\]]+)\] (.*)"
    )
    try:
        result = subprocess.run(
            ["tail", "-n", "3000", str(FIRM_LOG)],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
    except Exception:
        lines = []

    # Map inner tag names to professional agent names
    inner_tag_map = {
        "ECONOMICS": "economics", "DONNIE": "economics", "DONNIE V2": "economics",
        "SPORTS": "sports", "BRAD": "sports",
        "CONGRESSIONAL": "congressional", "RUGRAT": "congressional",
        "OPTIONS": "options", "JORDAN": "options",
        "WEATHER": "weather", "weather-bot": "weather",
        "WEATHER_INTEL": "weather_intel",
        "CRYPTO": "crypto", "CHESTER": "crypto",
        "SUPERVISOR": "supervisor",
        "EVAL": "eval", "EVAL FRAMEWORK": "eval",
    }

    entries = []
    for line in lines:
        m = pattern.match(line)
        if m:
            ts, level, ag, msg = m.groups()
            # If outer tag is FIRM, extract inner tag from message: [WEATHER] actual message
            real_agent = ag
            real_msg = msg
            if ag == "FIRM" and msg.startswith("["):
                inner_end = msg.find("]")
                if inner_end > 0:
                    inner_tag = msg[1:inner_end]
                    real_msg = msg[inner_end+1:].strip()
                    real_agent = inner_tag_map.get(inner_tag, inner_tag.lower())
            else:
                # Map outer tag to professional name
                real_agent = inner_tag_map.get(ag, ag.lower())

            if agent and real_agent.lower() != agent.lower():
                continue
            entries.append({
                "timestamp": ts,
                "level": level,
                "agent": real_agent,
                "message": real_msg,
                "raw": line,
            })

    entries.reverse()
    return entries[:n]


@app.get("/api/positions")
@_safe
async def positions():
    """Fetch live Kalshi positions directly from the Kalshi API (cached donnie module)."""
    try:
        donnie = _get_donnie()
        if not donnie:
            return []
        raw = donnie.kalshi_get("/portfolio/positions")
        market_positions = raw.get("market_positions", [])
        result = []
        for p in market_positions:
            ticker = p.get("ticker", "")
            position = p.get("position", 0) or 0
            exposure = float(p.get("market_exposure_dollars", 0) or 0)
            avg_price = float(p.get("average_traded_yes_price", p.get("average_price", 0)) or 0)
            realized_pnl = float(p.get("realized_pnl_dollars", 0) or 0)
            if ticker and (exposure > 0 or position != 0):
                title = _get_market_title(ticker)
                result.append({
                    "ticker": ticker,
                    "title": title,
                    "side": "YES" if position > 0 else "NO",
                    "contracts": abs(int(position)),
                    "avg_price_cents": round(avg_price * 100, 1) if avg_price <= 1 else round(avg_price, 1),
                    "exposure": round(exposure, 2),
                    "realized_pnl": round(realized_pnl, 2),
                    "max_payout": 0,
                    "potential_profit": 0,
                    "current_price_cents": 0,
                })
                # max payout at ENTRY: abs(position_fp)/100 contracts ×  each
                # position_fp is in centesimal units (e.g. -2000 = 20 contracts)
                try:
                    _pos_fp = float(p.get("position_fp", 0) or 0)
                    _contracts = abs(_pos_fp)  # position_fp is already whole contract count
                    if _contracts > 0 and result:
                        _entry_price = exposure / _contracts  # what we paid per contract
                        result[-1]["max_payout"] = round(_contracts, 2)
                        result[-1]["potential_profit"] = round(_contracts - exposure, 2)
                        result[-1]["entry_price_cents"] = round(_entry_price * 100, 1)
                except Exception:
                    pass
        return result
    except Exception as e:
        log.warning(f"Kalshi positions live fetch failed: {e}")
        return []


@app.get("/api/kalshi/balance")
@_safe
async def kalshi_balance():
    donnie = _get_donnie()
    if donnie:
        bal = donnie.get_balance()
        return {"balance": round(float(bal), 2) if bal else 0}
    return {"balance": 0}


@app.get("/api/kalshi/history")
@_safe
async def kalshi_history():
    evals = _read_json(EVAL_STORE) or []
    trades_file = DATA_DIR / "trades" / "resolved.json"
    if trades_file.exists():
        resolved = _read_json(trades_file) or []
        return resolved + evals
    return evals


@app.get("/api/weather")
@_safe
async def weather():
    trades = _read_json(WEATHER_TRADES)
    if isinstance(trades, dict):
        trades = trades.get("trades", [])

    # Return all trades for the frontend but compute stats on post-tuning only
    all_trades = trades
    # Filter to post-tuning trades only (Apr 16-18 were pre-parameter-tuning, excluded)
    trades = [t for t in trades if t.get("date", "")[:10] >= "2026-04-19"]

    total = len(trades)
    resolved = [t for t in trades if t.get("status") in ("WIN", "LOSS")]
    wins = [t for t in resolved if t.get("status") == "WIN"]
    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else 0

    # By city
    by_city = defaultdict(lambda: {"total": 0, "wins": 0, "edges": []})
    for t in trades:
        city = t.get("city_name", t.get("city", t.get("location", "unknown")))
        by_city[city]["total"] += 1
        if t.get("status") == "WIN":
            by_city[city]["wins"] += 1
        edge = t.get("edge")
        if edge is not None:
            by_city[city]["edges"].append(edge)

    city_stats = {}
    for city, d in by_city.items():
        city_stats[city] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] else 0,
            "edge_avg": round(sum(d["edges"]) / len(d["edges"]), 2) if d["edges"] else None,
        }

    # By source
    by_source = defaultdict(lambda: {"total": 0, "wins": 0})
    for t in trades:
        src = t.get("forecast_source") or "legacy"
        by_source[src]["total"] += 1
        if t.get("status") == "WIN":
            by_source[src]["wins"] += 1
    source_stats = {}
    for src, d in by_source.items():
        source_stats[src] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] else 0,
        }

    # Open positions (from post-tuning trades)
    open_trades = [t for t in trades if t.get("status") == "OPEN"]

    # Recent resolved (last 20)
    recent_resolved = [t for t in trades if t.get("status") in ("WIN", "LOSS")][-20:][::-1]

    # live Kalshi weather positions (real money)
    live_positions = []
    try:
        donnie = _get_donnie()
        if donnie:
            all_pos = donnie.get_open_positions()
            for ticker, exposure in all_pos.items():
                if 'HIGH' not in ticker:
                    continue
                # parse city and condition from ticker
                import re as _re
                parts = ticker.split('-')
                series = parts[0] if parts else ticker
                city_map = {
                    'KXHIGHNY': 'New York', 'KXHIGHLAX': 'Los Angeles', 'KXHIGHCHI': 'Chicago',
                    'KXHIGHHOU': 'Houston', 'KXHIGHPHI': 'Philadelphia', 'KXHIGHPHX': 'Phoenix',
                    'KXHIGHSF': 'San Francisco', 'KXHIGHTLV': 'Las Vegas', 'KXHIGHMIA': 'Miami',
                    'KXHIGHNYC': 'New York', 'KXHIGHBOS': 'Boston', 'KXHIGHTBOS': 'Boston',
                    'KXHIGHTATL': 'Atlanta', 'KXHIGHTCHI': 'Chicago', 'KXHIGHTHOU': 'Houston',
                    'KXHIGHTLAX': 'Los Angeles', 'KXHIGHTMIA': 'Miami', 'KXHIGHTMIN': 'Minneapolis',
                    'KXHIGHTNY': 'New York', 'KXHIGHTNOLA': 'New Orleans', 'KXHIGHTNOLA': 'New Orleans',
                    'KXHIGHTOKC': 'Oklahoma City', 'KXHIGHTPHX': 'Phoenix', 'KXHIGHTSATX': 'San Antonio',
                    'KXHIGHTSEA': 'Seattle', 'KXHIGHTSFO': 'San Francisco', 'KXHIGHTDC': 'Washington DC',
                    'KXHIGHAUS': 'Austin', 'KXHIGHAUSTIN': 'Austin', 'KXHIGHTPHIL': 'Philadelphia',
                    'KXHIGHPHIL': 'Philadelphia',
                }
                city = city_map.get(series, series.replace('KXHIGH', '').replace('KXHIGHT', ''))
                # get current market price for payout
                try:
                    mr = donnie.kalshi_get(f"/markets/{ticker}")
                    md = mr.get("market", mr)
                    no_ask = float(md.get("no_ask_dollars", 0) or 0)
                    close_time = md.get("close_time", "")
                    title = md.get("title", ticker)
                except Exception:
                    no_ask = 0; close_time = ""; title = ticker
                # get position_fp from portfolio positions for correct payout
                _pos_fp = 0
                try:
                    _raw_pos = donnie.kalshi_get("/portfolio/positions")
                    for _pp in _raw_pos.get("market_positions", []):
                        if _pp.get("ticker") == ticker:
                            _pos_fp = abs(float(_pp.get("position_fp", 0) or 0))
                            break
                except Exception:
                    pass
                # max_payout = contracts ×  (entry-based, not current price)
                _max_payout = _pos_fp if _pos_fp > 0 else (round(float(exposure) / 0.25, 2) if float(exposure) > 0 else 0)
                live_positions.append({
                    "ticker": ticker,
                    "city": city,
                    "title": title,
                    "side": "NO",
                    "exposure": round(float(exposure), 2),
                    "current_no_price_cents": round(no_ask * 100, 1),
                    "max_payout": round(_max_payout, 2),
                    "potential_profit": round(_max_payout - float(exposure), 2),
                    "close_time": close_time,
                })
    except Exception as _le:
        log.warning(f"Live weather positions fetch failed: {_le}")

    # baseline stats (Apr 19-26, pre-live calibration period)
    baseline = [t for t in trades if "2026-04-19" <= t.get("date", "")[:10] <= "2026-04-26"]
    baseline_resolved = [t for t in baseline if t.get("status") in ("WIN", "LOSS")]
    baseline_wins = [t for t in baseline_resolved if t.get("status") == "WIN"]

    return {
        "total": total,
        "resolved": len(resolved),
        "wins": len(wins),
        "win_rate": win_rate,
        "by_city": city_stats,
        "by_source": source_stats,
        "recent": recent_resolved,
        "open": open_trades,
        "live_positions": live_positions,
        "baseline": {
            "total": len(baseline),
            "resolved": len(baseline_resolved),
            "wins": len(baseline_wins),
            "win_rate": round(len(baseline_wins) / len(baseline_resolved) * 100, 1) if baseline_resolved else 0,
            "period": "Apr 19 – Apr 26",
        },
    }


@app.get("/api/brad")
@_safe
async def brad():
    trades = _read_json(BRAD_TRADES)
    if isinstance(trades, dict):
        trades = trades.get("trades", [])

    total = len(trades)
    resolved = [t for t in trades if t.get("status") in ("expired_win", "expired_loss")]
    wins = [t for t in resolved if t.get("status") == "expired_win"]
    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else 0

    by_strategy = defaultdict(lambda: {"total": 0, "wins": 0})
    for t in trades:
        strat = t.get("strategy", t.get("id", "unknown").split("-")[0] if t.get("id") else "unknown")
        by_strategy[strat]["total"] += 1
        if t.get("status") == "expired_win":
            by_strategy[strat]["wins"] += 1

    strat_stats = {}
    for s, d in by_strategy.items():
        strat_stats[s] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] else 0,
        }

    # Open positions
    open_trades = [t for t in trades if t.get("status") in ("open", "filled")]

    # Recent resolved
    recent_resolved = [t for t in trades if t.get("status") in ("expired_win", "expired_loss")][-20:][::-1]

    return {
        "total": total,
        "resolved": len(resolved),
        "wins": len(wins),
        "win_rate": win_rate,
        "by_strategy": strat_stats,
        "recent": recent_resolved,
        "open": open_trades,
    }


@app.get("/api/eval")
@_safe
async def eval_data():
    data = _read_json(EVAL_STORE)
    return data


@app.get("/api/rag-demo")
@_safe
async def rag_demo(
    member: str = Query("Nancy Pelosi"),
    ticker: str = Query("NVDA"),
    trade_type: str = Query("Purchase"),
):
    import importlib
    os.chdir("/home/cody/stratton")
    from dotenv import load_dotenv
    load_dotenv(str(BOT_TOKENS))

    from rag_store import search, init_store
    from llm_client import llm_reason, congressional_brief_prompt

    init_store()
    t0 = time.time()
    rag = search(member, ticker, trade_type, n_results=5)
    prompt = congressional_brief_prompt(
        member, ticker, trade_type,
        "$50K-$100K", 25, "Armed Services", "tech,semiconductors",
    )
    if rag.get("prior_disclosures"):
        prompt += "\n\nRetrieved context:\n" + "\n".join(rag["prior_disclosures"][:3])

    llm = llm_reason(prompt, primary="grok")
    latency = int((time.time() - t0) * 1000)

    return {
        "query": {"member": member, "ticker": ticker, "trade_type": trade_type},
        "retrieved_context": [
            {"text": d, "index": i}
            for i, d in enumerate(rag.get("prior_disclosures", []))
        ],
        "member_profile": rag.get("member_profile", ""),
        "market_context": rag.get("market_context", []),
        "llm_model": "grok",
        "llm_reasoning": llm.get("reasoning", ""),
        "llm_confidence": llm.get("confidence", ""),
        "go": llm.get("go", True),
        "risks": llm.get("risks", []),
        "latency_ms": latency,
    }


@app.get("/api/portfolio")
@_safe
async def portfolio():
    import requests

    tickers = list(AVG_COSTS.keys())
    results = []

    for ticker in tickers:
        try:
            # mutual funds need different interval
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            data = r.json()
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
            change_pct = round((price - prev) / prev * 100, 2) if prev else 0
            gain_pct = round((price - AVG_COSTS[ticker]) / AVG_COSTS[ticker] * 100, 1)
            results.append({
                "ticker": ticker,
                "price": round(price, 2),
                "change_pct": change_pct,
                "gain_pct": gain_pct,
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "price": None,
                "change_pct": None,
                "gain_pct": None,
                "error": str(e),
            })

    results.sort(key=lambda x: x.get("gain_pct") or -9999, reverse=True)
    return results


@app.get("/api/files")
@_safe
async def list_files():
    """List all bot files with metadata."""
    BOTS_DIR = Path("/home/cody/stratton/bots")
    files = []
    for f in sorted(BOTS_DIR.glob("*.py")):
        if f.name.startswith("_") or f.name == "__init__.py":
            continue
        stat = f.stat()
        content = f.read_text(errors="replace")
        lines = len(content.splitlines())
        files.append({
            "name": f.name,
            "lines": lines,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return files


@app.get("/api/file")
@_safe
async def get_file(path: str = Query(...)):
    """Serve bot file contents for the dashboard file viewer."""
    ALLOWED_BASE = Path("/home/cody/stratton/bots")
    requested = (ALLOWED_BASE / path).resolve()
    if not str(requested).startswith(str(ALLOWED_BASE)):
        return {"error": "Access denied"}
    if not requested.exists() or not requested.is_file():
        return {"error": "File not found"}
    content = requested.read_text(errors="replace")
    lines = len(content.splitlines())
    size_kb = round(len(content.encode()) / 1024, 1)
    return {
        "path": path,
        "content": content,
        "lines": lines,
        "size_kb": size_kb,
        "modified": datetime.fromtimestamp(requested.stat().st_mtime, tz=timezone.utc).isoformat()
    }
