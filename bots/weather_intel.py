#!/usr/bin/env python3
"""
weather_intel.py — Weather Market Intelligence
Intraday weather signal analysis for The Firm.

Monitors NWS ASOS observations, tracks city-level temperature forecasts,
and identifies Kalshi daily high temperature market mispricings in real time.

Previously contained macro research functions — those have been removed.
Weather scanning is handled by weather.py; this module provides intelligence
analysis and signal context.
"""

import os
import sys
import requests
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(__file__), '..', 'config', 'bot-tokens.env')
load_dotenv(TOKENS_FILE)

MARK_HANNA_TOKEN = os.getenv('MARK_HANNA_TOKEN')

CHANNEL_THE_CRUCIBLE = 1491199681064206346
CHANNEL_DEEP_DIVES   = 1487189078427439187
CHANNEL_MACRO        = 1487189080482648095
CHANNEL_BOT_LOGS     = 1487189090817282139


# ── Discord helper ──────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str = None) -> bool:
    tok = token or MARK_HANNA_TOKEN
    if not tok:
        print(f"[WEATHER_INTEL] ERROR: No token", file=sys.stderr)
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {tok}", "Content-Type": "application/json"}
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[WEATHER_INTEL] Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[WEATHER_INTEL] Post exception: {e}", file=sys.stderr)
            success = False
    return success


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
            for item in data.get('RelatedTopics', [])[:limit]:
                if isinstance(item, dict) and item.get('Text'):
                    results.append({
                        'text': item['Text'][:300],
                        'url': item.get('FirstURL', '')
                    })
            if data.get('AbstractText'):
                results.insert(0, {
                    'text': data['AbstractText'][:400],
                    'url': data.get('AbstractURL', '')
                })
    except Exception as e:
        print(f"[WEATHER_INTEL] News fetch error: {e}", file=sys.stderr)
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


def get_status() -> dict:
    """Return current weather intelligence status."""
    return {
        "module": "weather_intel",
        "role": "Weather market intelligence and signal analysis",
        "status": "active"
    }


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Weather Intel — Weather Market Intelligence')
    parser.add_argument('--status', action='store_true', help='Print module status')
    args = parser.parse_args()

    if args.status:
        import json
        print(json.dumps(get_status(), indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
