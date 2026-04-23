#!/usr/bin/env python3
"""
MARK HANNA — Unconventional Alpha Research Bot
Posts to #the-crucible, #deep-dives, #macro-context
The Firm | Stratton Oakmont Discord Intelligence System

"Without me, you are nothing."
"""

import os
import sys
import json
import argparse
import requests
import hashlib
from datetime import datetime, timezone
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(TOKENS_FILE)

MARK_HANNA_TOKEN = os.getenv('MARK_HANNA_TOKEN')

CHANNEL_THE_CRUCIBLE = 1491199681064206346
CHANNEL_DEEP_DIVES   = 1487189078427439187
CHANNEL_MACRO        = 1487189080482648095
CHANNEL_BOT_LOGS     = 1487189090817282139

# State file to track which topic was last researched
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'mark_hanna_state.json')

# Research queue — rotate through these weekly
RESEARCH_QUEUE = [
    {
        "id": "royalty_streaming",
        "name": "Royalty Streaming Companies",
        "tickers": ["WPM", "RGLD", "OR", "SSL"],
        "description": "Metal streaming companies that provide upfront capital to miners in exchange for future production at fixed prices",
        "search_terms": ["silver wheaton WPM royalty", "Royal Gold RGLD royalty streaming 2026", "precious metals royalty company analysis"],
        "angle": "Leveraged exposure to precious metals with less operational risk than miners. Premium to NAV justified by predictable cash flow."
    },
    {
        "id": "litigation_finance",
        "name": "Litigation Finance",
        "tickers": ["LITI", "MFIN"],
        "description": "Companies that fund lawsuits in exchange for a share of the proceeds — uncorrelated to market cycles",
        "search_terms": ["litigation finance funds 2026", "Burford Capital lawsuit funding returns", "legal finance uncorrelated returns"],
        "angle": "Zero correlation to equities. Return is case outcome dependent. Major legal tech tailwind."
    },
    {
        "id": "catastrophe_bonds",
        "name": "Catastrophe Bonds",
        "tickers": ["SRLN", "FEMA cat bond"],
        "description": "Insurance-linked securities that pay out if disasters DON'T happen. Yield typically 6-15% above LIBOR.",
        "search_terms": ["catastrophe bonds cat bonds yield 2026", "ILS insurance linked securities returns", "Swiss Re cat bond market"],
        "angle": "Truly uncorrelated yield. Climate risk pricing is often wrong — that's the edge."
    },
    {
        "id": "carbon_markets",
        "name": "Carbon Credit Markets",
        "tickers": ["KRBN", "XCAR", "ARCA"],
        "description": "Voluntary and compliance carbon credit markets. California cap-and-trade, EU ETS, voluntary offset space.",
        "search_terms": ["carbon credits ETF KRBN 2026", "EU ETS carbon price forecast", "voluntary carbon market 2026"],
        "angle": "Regulatory tailwind + inflation hedge. Market structural oversupply being corrected by policy tightening."
    },
    {
        "id": "reinsurance",
        "name": "Reinsurance Plays",
        "tickers": ["RNR", "ACGL", "RE"],
        "description": "Companies that insure the insurers. Hard market conditions = fat premiums, disciplined underwriting.",
        "search_terms": ["reinsurance hard market 2026 RenaissanceRe", "ACGL Arch Capital reinsurance rates", "cat bond reinsurance opportunity"],
        "angle": "Hard market cycle post-COVID and climate claims. Rate firming not yet priced in by equity market."
    },
    {
        "id": "defi_yield",
        "name": "DeFi Yield Strategies",
        "tickers": ["AAVE", "CRV", "COMP"],
        "description": "Decentralized finance protocols generating real yield through lending, liquidity provision, and protocol fees",
        "search_terms": ["DeFi yield 2026 AAVE Compound", "stablecoin yield farming safe 2026", "real yield DeFi protocols"],
        "angle": "Cody has ETH sitting unstaked earning 0. Multiple yield strategies available at 4-12% with varying risk."
    },
    {
        "id": "royalty_music",
        "name": "Music & IP Royalty Investing",
        "tickers": ["SONG", "HMTV"],
        "description": "Direct investment in music catalog royalties, patent royalties, and IP streaming revenue streams",
        "search_terms": ["music royalty investing 2026", "ANote Music royalty platform", "Hipgnosis Songs Fund SONG royalties"],
        "angle": "Streaming growth compounds royalty income. Catalog acquisition prices have reset — entry point now."
    },
    {
        "id": "spac_arb",
        "name": "SPAC Arbitrage",
        "tickers": [],
        "description": "Risk-free (near) return on SPACs trading below NAV while awaiting deal or liquidation. Built-in floor.",
        "search_terms": ["SPAC arbitrage 2026 below NAV", "SPAC liquidation plays trust value", "risk-free SPAC arb current"],
        "angle": "SPACs below $10 NAV = nearly free money. Liquidation timeline known. Zero downside if you buy under NAV."
    },
]


# ── Discord helper ──────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str = None) -> bool:
    tok = token or MARK_HANNA_TOKEN
    if not tok:
        print(f"[MARK HANNA] ERROR: No token", file=sys.stderr)
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {tok}", "Content-Type": "application/json"}
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[MARK HANNA] Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[MARK HANNA] Post exception: {e}", file=sys.stderr)
            success = False
    return success


# ── State management ────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"last_topic_index": -1, "last_run": None}


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[MARK HANNA] State save error: {e}", file=sys.stderr)


# ── News fetcher ────────────────────────────────────────────────────────────
def search_news(query: str, limit: int = 3) -> list:
    """Fetch news via DuckDuckGo Instant Answer API (free, no key)."""
    results = []
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": "TheFirmBot/1.0"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            # RelatedTopics
            for item in data.get('RelatedTopics', [])[:limit]:
                if isinstance(item, dict) and item.get('Text'):
                    results.append({
                        'text': item['Text'][:300],
                        'url': item.get('FirstURL', '')
                    })
            # Abstract
            if data.get('AbstractText'):
                results.insert(0, {
                    'text': data['AbstractText'][:400],
                    'url': data.get('AbstractURL', '')
                })
    except Exception as e:
        print(f"[MARK HANNA] News fetch error: {e}", file=sys.stderr)
    return results[:limit]


def get_ticker_summary(ticker: str) -> str:
    """Get basic ticker info from Yahoo Finance (free)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
            price  = meta.get('regularMarketPrice', 0)
            change = meta.get('regularMarketChangePercent', 0)
            name   = meta.get('longName', ticker)
            return f"{name} ({ticker}): ${price:.2f} ({change:+.1f}% today)"
    except Exception:
        pass
    return f"{ticker}: price unavailable"


# ── Deep dive builder ───────────────────────────────────────────────────────
def build_deep_dive(topic: dict) -> str:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [
        f"# 🎩 MARK HANNA — WEEKLY DEEP DIVE",
        f"**Topic: {topic['name']}**",
        f"*{now} | The Firm Research Division*",
        "",
        "---",
        "",
        f"## 📋 WHAT IS IT?",
        topic['description'],
        "",
        f"## 🎯 THE ANGLE",
        topic['angle'],
        "",
    ]

    # Tickers
    if topic.get('tickers'):
        lines.append("## 📊 HOW TO ACCESS IT")
        lines.append("**Instruments:**")
        for ticker in topic['tickers']:
            summary = get_ticker_summary(ticker)
            lines.append(f"  • {summary}")
        lines.append("")

    # News
    all_news = []
    for term in topic.get('search_terms', [])[:2]:
        news = search_news(term, limit=2)
        all_news.extend(news)

    if all_news:
        lines.append("## 📰 CURRENT INTELLIGENCE")
        for item in all_news[:4]:
            text = item['text']
            url  = item.get('url', '')
            lines.append(f"  • {text}")
            if url:
                lines.append(f"    ↳ {url}")
        lines.append("")

    # Risk section
    lines.extend([
        "## ⚠️ THE RISKS",
        "Before you move: here's what kills this trade.",
        "",
        "**Macro:** Rate environment, dollar strength, liquidity crunch can crater even uncorrelated plays.",
        "**Execution:** Illiquidity, wide bid-ask spreads, limited retail access to some instruments.",
        "**Timing:** 'Early' and 'wrong' look identical for too long.",
        "",
        "## 🎲 RECOMMENDED PLAY",
        f"Study the space. Identify your entry vehicle from the list above.",
        f"Start with 2-5% of risk capital until you understand the mechanics.",
        f"This is an **educational brief** — not a trade recommendation. Do your own DD.",
        "",
        "---",
        f"*Mark Hanna | The Firm | Full archive in #deep-dives*",
    ])

    return "\n".join(lines)


# ── Bear case builder ───────────────────────────────────────────────────────
def build_bear_case(ticker: str) -> str:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    ticker = ticker.upper()

    # Get price
    price_info = get_ticker_summary(ticker)

    # Search for bearish news
    news = search_news(f"{ticker} bearish risk overvalued 2026", limit=3)
    news2 = search_news(f"{ticker} short thesis concerns", limit=2)
    all_news = news + news2

    lines = [
        f"# 😈 MARK HANNA — DEVIL'S ADVOCATE",
        f"**${ticker} — THE BEAR CASE**",
        f"*{now} | Why this blows up in your face*",
        "",
        "---",
        "",
        f"**Current price:** {price_info}",
        "",
        "## 🔴 THE THESIS AGAINST",
        "",
        "I'm going to tell you everything that's wrong with this trade.",
        "Not because I want you to miss it — because you NEED to hear this before you size up.",
        "",
    ]

    if all_news:
        lines.append("## 📰 BEARISH INTELLIGENCE")
        for item in all_news[:4]:
            lines.append(f"  • {item['text'][:250]}")
        lines.append("")

    lines.extend([
        "## 💀 KILL SCENARIOS",
        "",
        f"**Scenario 1 — Macro crush:** Rates stay higher longer, P/E compression hits growth names. ${ticker} re-rates down 20-30%.",
        f"**Scenario 2 — Narrative break:** Whatever the bull case is built on (earnings beat, catalyst, sector rotation) fails to materialize.",
        f"**Scenario 3 — Liquidity event:** Market-wide deleveraging hits everything. Correlation goes to 1. No hiding.",
        f"**Scenario 4 — Insider knowledge you don't have:** They know something. The tape is trying to tell you. Are you listening?",
        "",
        "## ⚖️ VERDICT",
        "",
        "Every trade has a price. If you can't answer *what would make me wrong*, you're gambling.",
        f"Know your stop. Know your max loss. Don't marry the thesis.",
        "",
        "---",
        f"*Mark Hanna | The Firm | Devil's Advocate mode — challenge any play with --challenge TICKER*",
    ])

    return "\n".join(lines)


# ── Run modes ───────────────────────────────────────────────────────────────
def run_weekly(post: bool = True) -> str:
    print("[MARK HANNA] Running weekly deep dive...")
    state = load_state()
    next_index = (state.get('last_topic_index', -1) + 1) % len(RESEARCH_QUEUE)
    topic = RESEARCH_QUEUE[next_index]

    print(f"[MARK HANNA] Topic: {topic['name']}")
    deep_dive = build_deep_dive(topic)

    if post:
        post_discord(CHANNEL_THE_CRUCIBLE, deep_dive)
        post_discord(CHANNEL_DEEP_DIVES, deep_dive)
        # Teaser to macro
        post_discord(CHANNEL_MACRO,
            f"🎩 **Mark Hanna** dropped a weekly deep dive on **{topic['name']}**.\n"
            f"Tickers: {', '.join(topic['tickers'][:4]) if topic['tickers'] else 'various'}\n"
            f"Full brief in #the-crucible and #deep-dives.")

    state['last_topic_index'] = next_index
    state['last_run'] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    return deep_dive


def run_challenge(ticker: str, post: bool = True) -> str:
    ticker = ticker.upper()
    print(f"[MARK HANNA] Building bear case for ${ticker}...")
    bear_case = build_bear_case(ticker)
    if post:
        post_discord(CHANNEL_THE_CRUCIBLE, bear_case)
    return bear_case


def run_demo() -> str:
    print("[MARK HANNA] Demo mode...")
    topic = RESEARCH_QUEUE[0]  # Royalty streaming
    deep_dive = build_deep_dive(topic)
    print(deep_dive)
    print("\n\n--- DEVIL'S ADVOCATE DEMO ---\n")
    bear = build_bear_case("WPM")
    print(bear)
    return deep_dive


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Mark Hanna — Unconventional Alpha Research')
    parser.add_argument('--weekly',   action='store_true', help='Generate weekly deep dive')
    parser.add_argument('--challenge', type=str,           help='Bear case for ticker')
    parser.add_argument('--demo',     action='store_true', help='Demo mode')
    parser.add_argument('--no-post',  action='store_true', help='Print only')
    args = parser.parse_args()

    post = not args.no_post

    if args.demo:
        run_demo()
    elif args.weekly:
        report = run_weekly(post=post)
        if args.no_post:
            print(report)
    elif args.challenge:
        report = run_challenge(args.challenge, post=post)
        if args.no_post:
            print(report)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
