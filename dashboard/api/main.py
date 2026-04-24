"""
Stratton Intelligence Dashboard — FastAPI Backend
Serves data from The Firm multi-agent trading system running on Atlas.
"""

import json
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
    "BRK-B": 349.65, "C": 41.38, "DVN": 65.05, "GOOG": 15.70,
    "GOOGL": 15.80, "LYFT": 57.99, "MCD": 259.13, "MU": 101.88,
    "NVDA": 41.52, "OKLO": 97.81, "PLTR": 162.20, "PYPL": 74.97,
    "SMR": 19.85, "SPY": 449.16, "TRP": 34.40, "TSM": 196.88, "WWD": 24.12,
}

app = FastAPI(title="Stratton Intelligence Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()


@app.on_event("startup")
async def startup():
    from dotenv import load_dotenv
    load_dotenv(str(BOT_TOKENS))
    sys.path.insert(0, "/home/cody/stratton/bots")


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

    # Kalshi positions
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

    # Weather win rate
    try:
        trades = _read_json(WEATHER_TRADES)
        if isinstance(trades, dict):
            trades = trades.get("trades", [])
        resolved = [t for t in trades if t.get("status") in ("WIN", "LOSS")]
        wins = [t for t in resolved if t.get("status") == "WIN"]
        weather_win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else 0
    except Exception:
        weather_win_rate = 0

    return {
        "service_running": service_running,
        "uptime_seconds": int(time.time() - START_TIME),
        "agents": agents,
        "kalshi_positions": len(positions),
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
    # Read last 3000 lines
    try:
        result = subprocess.run(
            ["tail", "-n", "3000", str(FIRM_LOG)],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
    except Exception:
        lines = []

    entries = []
    for line in lines:
        m = pattern.match(line)
        if m:
            ts, level, ag, msg = m.groups()
            if agent and ag.lower() != agent.lower():
                continue
            entries.append({
                "timestamp": ts,
                "level": level,
                "agent": ag,
                "message": msg,
                "raw": line,
            })

    entries.reverse()
    return entries[:n]


@app.get("/api/positions")
@_safe
async def positions():
    state = _read_json(SHARED_STATE)
    return state.get("kalshi_portfolio", {}).get("open_positions", [])


@app.get("/api/weather")
@_safe
async def weather():
    trades = _read_json(WEATHER_TRADES)
    if isinstance(trades, dict):
        trades = trades.get("trades", [])

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
        src = t.get("forecast_source", "unknown")
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

    return {
        "total": total,
        "resolved": len(resolved),
        "wins": len(wins),
        "win_rate": win_rate,
        "by_city": city_stats,
        "by_source": source_stats,
        "recent": trades[-20:][::-1],
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

    return {
        "total": total,
        "resolved": len(resolved),
        "wins": len(wins),
        "win_rate": win_rate,
        "by_strategy": strat_stats,
        "recent": trades[-10:][::-1],
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
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d"
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
