#!/usr/bin/env python3
"""
CHESTER — Crypto Whale Tracker
Posts to #crypto-stack, #whale-watch
The Firm | Stratton Oakmont Discord Intelligence System
"""

import os
import sys
import json
import argparse
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(__file__), '..', 'config', 'bot-tokens.env')
load_dotenv(TOKENS_FILE)

CHESTER_TOKEN = os.getenv('CHESTER_TOKEN')

CHANNEL_WHALE_WATCH  = 1491199678317072626
CHANNEL_CRYPTO_STACK = 1487189084693725215
CHANNEL_BOT_LOGS     = 1487189090817282139

# Cody's stack (for context alerts)
CODY_ETH = 2.224
CODY_BTC = 0.0334

# Thresholds
ETH_WHALE_THRESHOLD = 100   # ETH
BTC_WHALE_THRESHOLD = 10    # BTC (satoshi: 10 * 1e8 = 1_000_000_000)
BTC_SATOSHI_WHALE   = 1_000_000_000

# Known exchange addresses (partial list for flagging)
KNOWN_EXCHANGES = {
    "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE": "Binance",
    "0xd551234Ae421e3BCBA99A0Da6d736074f22192FF": "Binance",
    "0xa7224c1c88E02d33A3a7D7eFD56F0E31E88e8Be2": "Binance",
    "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3": "Coinbase",
    "0x503828976D22510aad0201ac7EC88293211D23Da": "Coinbase",
}

# ── Discord helper ──────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str = None) -> bool:
    tok = token or CHESTER_TOKEN
    if not tok:
        print(f"[CHESTER] ERROR: No token", file=sys.stderr)
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {tok}",
        "Content-Type": "application/json",
    }
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[CHESTER] Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[CHESTER] Discord post exception: {e}", file=sys.stderr)
            success = False
    return success


# ── Data fetchers ───────────────────────────────────────────────────────────
def fetch_whale_alert_rss() -> list:
    """Parse Whale Alert RSS feed for large crypto transactions."""
    results = []
    try:
        feed = feedparser.parse("https://whale-alert.io/rss")
        for entry in feed.entries[:20]:
            results.append({
                'source': 'whale-alert',
                'title': entry.get('title', ''),
                'summary': entry.get('summary', ''),
                'published': entry.get('published', ''),
                'link': entry.get('link', ''),
            })
    except Exception as e:
        print(f"[CHESTER] Whale Alert RSS error: {e}", file=sys.stderr)
    return results


def fetch_eth_large_transfers(min_eth: int = 100) -> list:
    """Fetch large ETH transfers via Etherscan free API (no key for basic)."""
    results = []
    try:
        # Use etherscan API — free tier allows basic queries
        # Get latest block first
        url = "https://api.etherscan.io/api"
        params = {
            "module": "proxy",
            "action": "eth_blockNumber",
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            latest_block = int(data.get('result', '0x0'), 16)

            # Get recent large transfers using token transfer endpoint (free)
            # Alternative: use public mempool API
            params2 = {
                "module": "account",
                "action": "txlist",
                "address": "0x0000000000000000000000000000000000000000",  # genesis
                "startblock": max(0, latest_block - 100),
                "endblock": latest_block,
                "sort": "desc",
                "apikey": "YourApiKeyToken"  # free tier works without key for limited calls
            }
            # Skip genesis — instead use a different approach via mempool
    except Exception as e:
        print(f"[CHESTER] Etherscan error: {e}", file=sys.stderr)

    # Use public Ethereum data via beaconchain or similar
    try:
        r = requests.get(
            "https://api.etherscan.io/api?module=stats&action=ethprice",
            timeout=10
        )
        if r.status_code == 200:
            eth_data = r.json().get('result', {})
            eth_price = float(eth_data.get('ethusd', 0))
            if eth_price:
                results.append({'_eth_price': eth_price})
    except Exception as e:
        print(f"[CHESTER] ETH price fetch error: {e}", file=sys.stderr)

    return results


def fetch_btc_large_txs(min_btc: float = 10.0) -> list:
    """Fetch large BTC transactions from mempool.space (free, no auth)."""
    results = []
    try:
        # Get recent mempool transactions
        r = requests.get("https://mempool.space/api/mempool/recent", timeout=15)
        if r.status_code == 200:
            txs = r.json()
            for tx in txs:
                # value is in satoshis
                value_btc = tx.get('value', 0) / 1e8
                if value_btc >= min_btc:
                    results.append({
                        'source': 'mempool.space',
                        'txid': tx.get('txid', ''),
                        'value_btc': round(value_btc, 4),
                        'fee': tx.get('fee', 0),
                        'timestamp': tx.get('time', 0),
                    })
    except Exception as e:
        print(f"[CHESTER] mempool.space error: {e}", file=sys.stderr)

    # Also check confirmed blocks
    try:
        r = requests.get("https://mempool.space/api/blocks", timeout=15)
        if r.status_code == 200:
            blocks = r.json()
            if blocks:
                latest = blocks[0]
                results.append({
                    'source': 'mempool.space-block',
                    'block_height': latest.get('height'),
                    'tx_count': latest.get('tx_count', 0),
                    'total_fees': round(latest.get('extras', {}).get('totalFees', 0) / 1e8, 6),
                    'timestamp': latest.get('timestamp', 0),
                })
    except Exception as e:
        print(f"[CHESTER] mempool block error: {e}", file=sys.stderr)

    return results


def fetch_crypto_prices() -> dict:
    """Get current BTC/ETH prices from CoinGecko (free, no auth)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return {
                'btc_price': data.get('bitcoin', {}).get('usd', 0),
                'btc_change': data.get('bitcoin', {}).get('usd_24h_change', 0),
                'eth_price': data.get('ethereum', {}).get('usd', 0),
                'eth_change': data.get('ethereum', {}).get('usd_24h_change', 0),
            }
    except Exception as e:
        print(f"[CHESTER] Price fetch error: {e}", file=sys.stderr)
    return {}


# ── Report builders ─────────────────────────────────────────────────────────
def build_whale_report(whale_alerts: list, btc_txs: list, prices: dict) -> str:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [f"🐋 **CHESTER — WHALE WATCH REPORT** — {now}\n"]

    # Price context
    if prices:
        btc_p = prices.get('btc_price', 0)
        eth_p = prices.get('eth_price', 0)
        btc_c = prices.get('btc_change', 0)
        eth_c = prices.get('eth_change', 0)
        btc_dir = "📈" if btc_c >= 0 else "📉"
        eth_dir = "📈" if eth_c >= 0 else "📉"
        lines.append(f"**Market Prices:**")
        lines.append(f"  {btc_dir} BTC: ${btc_p:,.0f} ({btc_c:+.1f}% 24h)")
        lines.append(f"  {eth_dir} ETH: ${eth_p:,.2f} ({eth_c:+.1f}% 24h)")
        lines.append("")

        # Cody's stack value
        if btc_p and eth_p:
            cody_btc_val = CODY_BTC * btc_p
            cody_eth_val = CODY_ETH * eth_p
            cody_total   = cody_btc_val + cody_eth_val
            lines.append(f"**📊 Portfolio Context (Cody's Stack):**")
            lines.append(f"  BTC: {CODY_BTC} BTC = ${cody_btc_val:,.2f}")
            lines.append(f"  ETH: {CODY_ETH} ETH = ${cody_eth_val:,.2f}")
            lines.append(f"  Total: **${cody_total:,.2f}**")
            lines.append("")

    # Whale Alert RSS
    if whale_alerts:
        lines.append(f"**🚨 Whale Alerts ({len(whale_alerts)} recent):**")
        for alert in whale_alerts[:8]:
            title = alert.get('title', '')
            pub   = alert.get('published', '')
            if title:
                lines.append(f"  • {title}")
        lines.append("")

    # BTC large transactions
    large_btc = [t for t in btc_txs if isinstance(t.get('value_btc'), float) and t['value_btc'] >= 10]
    if large_btc:
        lines.append(f"**₿ Large BTC Transactions (≥10 BTC, from mempool):**")
        for tx in large_btc[:5]:
            val = tx['value_btc']
            txid = tx.get('txid', '')[:16] + '...'
            lines.append(f"  • {val:.2f} BTC | txid: {txid}")
        lines.append("")

    # Block summary
    block_data = next((t for t in btc_txs if t.get('source') == 'mempool.space-block'), None)
    if block_data:
        lines.append(f"**📦 Latest Block #{block_data.get('block_height')}:**")
        lines.append(f"  TXs: {block_data.get('tx_count')} | Total Fees: {block_data.get('total_fees')} BTC")
        lines.append("")

    if len(lines) <= 3:
        lines.append("No major whale activity detected this scan.")

    return "\n".join(lines)


# ── Run modes ───────────────────────────────────────────────────────────────
def run_scan(post: bool = True) -> str:
    print("[CHESTER] Running full crypto whale scan...")
    whale_alerts = fetch_whale_alert_rss()
    btc_txs      = fetch_btc_large_txs()
    prices       = fetch_crypto_prices()
    report       = build_whale_report(whale_alerts, btc_txs, prices)
    if post:
        post_discord(CHANNEL_WHALE_WATCH, report)
        # Summary to crypto-stack
        if prices:
            btc_p = prices.get('btc_price', 0)
            eth_p = prices.get('eth_price', 0)
            post_discord(CHANNEL_CRYPTO_STACK,
                f"📊 **Chester Market Update** | BTC: ${btc_p:,.0f} | ETH: ${eth_p:,.2f} | Full report in #whale-watch")
    return report


def run_whale_only(post: bool = True) -> str:
    print("[CHESTER] Scanning for large transactions only...")
    btc_txs = fetch_btc_large_txs(min_btc=50)  # Higher threshold for whale-only
    whale_alerts = fetch_whale_alert_rss()
    prices  = fetch_crypto_prices()
    report  = build_whale_report(whale_alerts, btc_txs, prices)
    if post:
        post_discord(CHANNEL_WHALE_WATCH, report)
    return report


def run_demo() -> str:
    print("[CHESTER] Demo mode...")
    prices = {'btc_price': 84500, 'btc_change': -2.3, 'eth_price': 1987, 'eth_change': -3.1}
    whale_alerts = [
        {'title': '🚨 2,500 #BTC (211,250,000 USD) transferred from unknown wallet to Binance', 'published': 'Today'},
        {'title': '🚨 15,000 #ETH (29,805,000 USD) transferred from Coinbase to unknown wallet', 'published': 'Today'},
        {'title': '⚠️ 500 #BTC (42,250,000 USD) transferred between unknown wallets', 'published': '2 hours ago'},
    ]
    btc_txs = [
        {'source': 'mempool.space', 'txid': 'abc123def456789012345678', 'value_btc': 245.33, 'fee': 12000, 'timestamp': 0},
        {'source': 'mempool.space', 'txid': 'xyz789abc123456789012345', 'value_btc': 88.12, 'fee': 8500, 'timestamp': 0},
        {'source': 'mempool.space-block', 'block_height': 893421, 'tx_count': 2847, 'total_fees': 0.4521, 'timestamp': 0},
    ]
    report = build_whale_report(whale_alerts, btc_txs, prices)
    print(report)
    return report


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Chester — Crypto Whale Tracker')
    parser.add_argument('--scan',    action='store_true', help='Full whale scan')
    parser.add_argument('--whale',   action='store_true', help='Large txs only')
    parser.add_argument('--demo',    action='store_true', help='Demo mode')
    parser.add_argument('--no-post', action='store_true', help='Print only')
    args = parser.parse_args()

    post = not args.no_post

    if args.demo:
        run_demo()
    elif args.scan:
        report = run_scan(post=post)
        if args.no_post:
            print(report)
    elif args.whale:
        report = run_whale_only(post=post)
        if args.no_post:
            print(report)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
