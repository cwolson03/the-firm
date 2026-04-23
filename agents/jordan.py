#!/usr/bin/env python3
"""
JORDAN — Options Tracker & Coach
Posts to #options-education, #active-plays
The Firm | Stratton Oakmont Discord Intelligence System

Level 2 options. No more blind Discord alerts.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(TOKENS_FILE)

JORDAN_TOKEN = os.getenv('JORDAN_TOKEN')

CHANNEL_OPTIONS_EDUCATION = 1487189082546376756
CHANNEL_ACTIVE_PLAYS      = 1487189069803819231
CHANNEL_BOT_LOGS          = 1487189090817282139

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'jordan_positions.json')

# ── Discord helper ──────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str = None) -> bool:
    tok = token or JORDAN_TOKEN
    if not tok:
        print(f"[JORDAN] ERROR: No token", file=sys.stderr)
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {tok}", "Content-Type": "application/json"}
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[JORDAN] Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[JORDAN] Post exception: {e}", file=sys.stderr)
            success = False
    return success


# ── Positions management ────────────────────────────────────────────────────
def load_positions() -> list:
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_positions(positions: list):
    try:
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"[JORDAN] Positions save error: {e}", file=sys.stderr)


def add_position(ticker: str, option_type: str, strike: float, expiry: str,
                 contracts: int, entry_price: float, notes: str = "") -> dict:
    """Add a new options position."""
    position = {
        "id": f"{ticker}_{option_type}_{strike}_{expiry}",
        "ticker": ticker.upper(),
        "type": option_type.upper(),  # CALL or PUT
        "strike": strike,
        "expiry": expiry,  # YYYY-MM-DD
        "contracts": contracts,
        "entry_price": entry_price,
        "entry_date": datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        "notes": notes,
        "status": "open",
    }
    positions = load_positions()
    # Replace if exists
    positions = [p for p in positions if p['id'] != position['id']]
    positions.append(position)
    save_positions(positions)
    return position


# ── Market data ─────────────────────────────────────────────────────────────
def get_stock_price(ticker: str) -> dict:
    """Get current stock price from Yahoo Finance (free)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            result = data.get('chart', {}).get('result', [])
            if result:
                meta = result[0].get('meta', {})
                return {
                    'ticker': ticker.upper(),
                    'price': meta.get('regularMarketPrice', 0),
                    'prev_close': meta.get('previousClose', 0),
                    'change_pct': meta.get('regularMarketChangePercent', 0),
                    'volume': meta.get('regularMarketVolume', 0),
                    '52w_high': meta.get('fiftyTwoWeekHigh', 0),
                    '52w_low': meta.get('fiftyTwoWeekLow', 0),
                    'name': meta.get('longName', ticker),
                }
    except Exception as e:
        print(f"[JORDAN] Price fetch error for {ticker}: {e}", file=sys.stderr)
    return {'ticker': ticker.upper(), 'price': 0, 'error': True}


def calculate_days_to_expiry(expiry_str: str) -> int:
    try:
        expiry = datetime.strptime(expiry_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (expiry - now).days
    except Exception:
        return -1


def estimate_delta(option_type: str, stock_price: float, strike: float, dte: int) -> float:
    """Rough delta estimate (not Black-Scholes — ballpark only)."""
    if stock_price <= 0 or strike <= 0:
        return 0.0
    moneyness = stock_price / strike
    if option_type.upper() == 'CALL':
        if moneyness > 1.05:   return 0.75   # deep ITM
        if moneyness > 1.01:   return 0.60   # slightly ITM
        if moneyness > 0.99:   return 0.50   # ATM
        if moneyness > 0.95:   return 0.35   # slightly OTM
        return 0.20                            # OTM
    else:  # PUT
        if moneyness < 0.95:   return -0.75
        if moneyness < 0.99:   return -0.60
        if moneyness < 1.01:   return -0.50
        if moneyness < 1.05:   return -0.35
        return -0.20


def calculate_roll_scenarios(position: dict, current_price: float) -> str:
    """Generate roll scenarios for a position approaching expiry."""
    ticker  = position['ticker']
    strike  = position['strike']
    opt_type = position['type']
    expiry  = position['expiry']
    dte     = calculate_days_to_expiry(expiry)
    entry   = position['entry_price']
    contracts = position['contracts']

    lines = [
        f"**📋 Roll Calculator — {ticker} ${strike} {opt_type} ({expiry})**",
        f"DTE: {dte} | Entry: ${entry:.2f} | Stock: ${current_price:.2f}",
        "",
        "**Roll Scenarios:**",
        "",
    ]

    # Same strike, next month
    try:
        next_expiry = (datetime.strptime(expiry, '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d')
        lines.append(f"**Option A — Roll Out (same strike, +30 days):**")
        lines.append(f"  Close {expiry} ${strike} {opt_type}, open {next_expiry} ${strike} {opt_type}")
        lines.append(f"  Purpose: Buy time if thesis still intact, IV crush check required")
        lines.append("")

        # Strike adjustment
        if opt_type == 'CALL':
            new_strike = round(current_price * 1.05 / 5) * 5  # 5% OTM, round to $5
        else:
            new_strike = round(current_price * 0.95 / 5) * 5

        lines.append(f"**Option B — Roll Up/Down + Out:**")
        lines.append(f"  Close {expiry} ${strike} {opt_type}, open {next_expiry} ${new_strike} {opt_type}")
        lines.append(f"  New strike: ${new_strike} | Lower cost, less intrinsic value risk")
        lines.append("")

        lines.append(f"**Option C — Take Profit / Cut Loss:**")
        lines.append(f"  Close position. P/L is what it is.")
        lines.append(f"  Rule: If you're down >50% and DTE < 7, close it. Don't let it go to zero.")
        lines.append("")

        lines.append(f"**Jordan's Take:**")
        if dte <= 7:
            lines.append(f"  🔴 CRITICAL — {dte} DTE. Either roll NOW or close. Time decay is exponential from here.")
        elif dte <= 21:
            lines.append(f"  ⚠️ WARNING — {dte} DTE. Theta burn accelerating. Plan your exit or roll this week.")
        else:
            lines.append(f"  ✅ OK — {dte} DTE. Monitor but no emergency action yet.")
    except Exception as e:
        lines.append(f"  Error calculating scenarios: {e}")

    return "\n".join(lines)


# ── Analysis engine ─────────────────────────────────────────────────────────
def analyze_discord_alert(ticker: str, price_target: float, current_price: float = None) -> str:
    """Independent analysis of a Discord group alert."""
    ticker = ticker.upper()
    price_data = get_stock_price(ticker)
    spot = current_price or price_data.get('price', 0)

    if not spot:
        return f"❌ Jordan: Can't pull data for {ticker}. Check ticker and try again."

    upside_pct = ((price_target - spot) / spot * 100) if spot else 0
    change_pct = price_data.get('change_pct', 0)
    week_high = price_data.get('52w_high', 0)
    week_low = price_data.get('52w_low', 0)

    lines = [
        f"**🎯 JORDAN — Alert Analysis: ${ticker}**",
        f"Alert Target: ${price_target:.2f} | Current: ${spot:.2f} | Implied Upside: {upside_pct:+.1f}%",
        "",
        f"**Market Context:**",
        f"  Today: {change_pct:+.1f}% | 52W Range: ${week_low:.2f} — ${week_high:.2f}",
        "",
    ]

    # Go/No-Go logic
    verdict = ""
    reasons = []

    if upside_pct < 5:
        reasons.append("Target too close to spot — risk/reward poor for options")
        verdict = "❌ NO-GO"
    elif upside_pct > 100:
        reasons.append("Target is a moon shot — lottery ticket probability")
        verdict = "⚠️ RISKY"
    elif change_pct < -5:
        reasons.append("Stock already down hard today — catching a falling knife?")
        verdict = "⚠️ CAUTION"
    elif week_high and spot > week_high * 0.95:
        reasons.append("Near 52W highs — limited upside, asymmetric downside")
        verdict = "⚠️ CAUTION"
    else:
        reasons.append("Setup appears reasonable — verify catalyst before sizing up")
        verdict = "✅ CONDITIONAL GO"

    # Options recommendation
    days_suggestion = 45 if upside_pct < 20 else 60
    atm_strike = round(spot / 5) * 5  # round to nearest $5
    otm_strike = round(price_target * 0.9 / 5) * 5

    lines.extend([
        f"**Jordan's Verdict: {verdict}**",
        "",
        "**Reasoning:**",
    ])
    for r in reasons:
        lines.append(f"  • {r}")

    lines.extend([
        "",
        f"**If you go:**",
        f"  Strike suggestion: ${atm_strike} ATM call or ${otm_strike} slightly OTM",
        f"  Expiry suggestion: {days_suggestion}+ DTE — don't buy weeklies on Discord alerts",
        f"  Position size: 2-3% of portfolio MAX on unverified alerts",
        f"  Stop: Close at -50% of option premium. No exceptions.",
        "",
        f"**Do NOT follow this alert blindly.** What's the catalyst? Who sent it? Do your own check.",
        f"*Jordan | The Firm*",
    ])

    return "\n".join(lines)


def run_position_check(post: bool = True) -> str:
    """Check all open positions for expiry warnings."""
    positions = load_positions()
    if not positions:
        return "No positions tracked. Add with --position TICKER TYPE STRIKE EXPIRY CONTRACTS ENTRY"

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [f"**📊 JORDAN — POSITION MONITOR** — {now}\n"]

    alerts = []

    for pos in positions:
        if pos.get('status') != 'open':
            continue

        ticker  = pos['ticker']
        dte     = calculate_days_to_expiry(pos['expiry'])
        price_data = get_stock_price(ticker)
        spot    = price_data.get('price', 0)
        delta   = estimate_delta(pos['type'], spot, pos['strike'], dte)
        pl_pct  = 0  # Would need current option price to calculate

        status_emoji = "✅"
        if dte <= 7:
            status_emoji = "🔴"
            alerts.append(f"🔴 CRITICAL: {ticker} ${pos['strike']} {pos['type']} expires in {dte} DAYS")
        elif dte <= 21:
            status_emoji = "⚠️"
            alerts.append(f"⚠️ WARNING: {ticker} ${pos['strike']} {pos['type']} — {dte} DTE, plan your move")

        lines.append(
            f"{status_emoji} **{ticker}** ${pos['strike']} {pos['type']} | Exp: {pos['expiry']} ({dte}d)\n"
            f"   Contracts: {pos['contracts']} | Entry: ${pos['entry_price']:.2f} | Stock: ${spot:.2f}\n"
            f"   Est. Delta: {delta:+.2f} | Notes: {pos.get('notes', 'none')}\n"
        )

    if alerts:
        alert_text = "**🚨 ACTION REQUIRED:**\n" + "\n".join(alerts)
        if post:
            post_discord(CHANNEL_ACTIVE_PLAYS, alert_text)

    report = "\n".join(lines)
    if post:
        post_discord(CHANNEL_OPTIONS_EDUCATION, report)
    return report


def run_analyze(ticker: str, post: bool = True) -> str:
    """Quick options analysis for a ticker."""
    ticker = ticker.upper()
    print(f"[JORDAN] Analyzing {ticker}...")
    price_data = get_stock_price(ticker)
    spot = price_data.get('price', 0)

    if not spot:
        return f"Cannot pull data for {ticker}"

    change = price_data.get('change_pct', 0)
    w52h = price_data.get('52w_high', 0)
    w52l = price_data.get('52w_low', 0)
    name = price_data.get('name', ticker)

    # Suggest strikes
    atm    = round(spot / 5) * 5
    otm5   = round(spot * 1.05 / 5) * 5
    otm10  = round(spot * 1.10 / 5) * 5
    deep   = round(spot * 0.90 / 5) * 5

    lines = [
        f"**📊 JORDAN — OPTIONS ANALYSIS: ${ticker}**",
        f"*{name}*",
        "",
        f"**Price:** ${spot:.2f} ({change:+.1f}% today)",
        f"**52W Range:** ${w52l:.2f} — ${w52h:.2f}",
        "",
        f"**Suggested Call Strikes:**",
        f"  ATM: ${atm} | 5% OTM: ${otm5} | 10% OTM: ${otm10}",
        "",
        f"**Suggested Put Strikes (downside hedge):**",
        f"  10% OTM: ${deep}",
        "",
        f"**Jordan's Framework for ${ticker}:**",
        f"  • Use 45-60 DTE for directional plays (sweet spot for theta vs time)",
        f"  • ATM if you have high conviction; OTM if you want leverage with smaller premium",
        f"  • Never risk more than 2-5% of account on a single options position",
        f"  • Set alerts at -30% and -50% on premium paid",
        "",
        f"**Education Corner:**",
        f"  Delta 0.50 (ATM) = option moves $0.50 for every $1 stock move",
        f"  Theta decay accelerates inside 21 DTE — respect this number",
        f"  IV crush post-earnings can wipe gains even if direction is right",
        "",
        f"*Jordan | The Firm | Not financial advice — you pull the trigger, not me*",
    ]

    report = "\n".join(lines)
    if post:
        post_discord(CHANNEL_OPTIONS_EDUCATION, report)
    return report


def run_demo() -> str:
    print("[JORDAN] Demo mode...")
    # Add sample positions
    add_position("NVDA", "CALL", 900.0, "2026-06-20", 2, 15.50, "Earnings play")
    add_position("SPY", "PUT", 480.0, "2026-05-17", 1, 8.25, "Hedge against market dump")

    report = run_position_check(post=False)
    print(report)
    print("\n--- Alert Analysis Demo ---\n")
    analysis = analyze_discord_alert("TSLA", 320.0)
    print(analysis)
    return report


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Jordan — Options Tracker & Coach')
    parser.add_argument('--position', action='store_true', help='Check open positions')
    parser.add_argument('--add', nargs=6, metavar=('TICKER','TYPE','STRIKE','EXPIRY','CONTRACTS','ENTRY'),
                        help='Add position: NVDA CALL 900 2026-06-20 2 15.50')
    parser.add_argument('--analyze', type=str,  help='Analyze options for ticker')
    parser.add_argument('--alert', nargs=2, metavar=('TICKER', 'TARGET'), help='Analyze Discord alert')
    parser.add_argument('--roll', type=str,     help='Calculate roll scenarios for position ID')
    parser.add_argument('--demo', action='store_true', help='Demo mode')
    parser.add_argument('--no-post', action='store_true', help='Print only')
    args = parser.parse_args()

    post = not args.no_post

    if args.demo:
        run_demo()
    elif args.add:
        ticker, opt_type, strike, expiry, contracts, entry = args.add
        pos = add_position(ticker, opt_type, float(strike), expiry, int(contracts), float(entry))
        print(f"[JORDAN] Position added: {pos['id']}")
    elif args.position:
        report = run_position_check(post=post)
        if args.no_post:
            print(report)
    elif args.analyze:
        report = run_analyze(args.analyze, post=post)
        if args.no_post:
            print(report)
    elif args.alert:
        ticker, target = args.alert
        report = analyze_discord_alert(ticker, float(target))
        if post:
            post_discord(CHANNEL_OPTIONS_EDUCATION, report)
        else:
            print(report)
    elif args.roll:
        positions = load_positions()
        pos = next((p for p in positions if p['id'] == args.roll or p['ticker'] == args.roll.upper()), None)
        if pos:
            price_data = get_stock_price(pos['ticker'])
            spot = price_data.get('price', 0)
            report = calculate_roll_scenarios(pos, spot)
            if post:
                post_discord(CHANNEL_OPTIONS_EDUCATION, report)
            else:
                print(report)
        else:
            print(f"[JORDAN] Position not found: {args.roll}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
