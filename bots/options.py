#!/usr/bin/env python3
"""
OPTIONS — SPY 0DTE Options Desk
Posts to #options-education, #active-plays
The Firm

SPY options only. 0DTE or same-day contracts.
Discord group sends CALL/PUT + price target → Cody executes → Options desk monitors.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ── Shared context (optional — graceful fallback if not present) ────────────
try:
    _BOTS_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _BOTS_DIR)
    from shared_context import write_agent_status as _write_status
except ImportError:
    def _write_status(name, d): pass

# ── Config ─────────────────────────────────────────────────────────────────
TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'bot-tokens.env')
if not os.path.exists(TOKENS_FILE):
    TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(TOKENS_FILE)

JORDAN_TOKEN = os.getenv('JORDAN_TOKEN')

# ── SPY-only enforcement ────────────────────────────────────────────────────
TICKER = "SPY"

CHANNEL_OPTIONS_EDUCATION = 1491861977214222366   # #options-desk
CHANNEL_ACTIVE_PLAYS      = 1491861990312906773   # #play-alerts
CHANNEL_BOT_LOGS          = 1491861993022554284   # #bot-logs

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'jordan_positions.json')
STATE_FILE     = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'jordan_state.json')


# ── Timezone helper ─────────────────────────────────────────────────────────
def _get_et():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        from datetime import timezone as tz
        # EDT offset (UTC-4); close enough for market hours
        return tz(timedelta(hours=-4))


def now_et() -> datetime:
    return datetime.now(_get_et())


def today_str() -> str:
    """Today's date in ET as YYYY-MM-DD."""
    return now_et().strftime('%Y-%m-%d')


def time_to_close_str() -> str:
    """Human-readable time remaining until 4:00 PM ET."""
    et = now_et()
    close = et.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = close - et
    if delta.total_seconds() <= 0:
        return "market closed"
    total_min = int(delta.total_seconds() // 60)
    hours = total_min // 60
    mins  = total_min % 60
    if hours > 0:
        return f"{hours}h {mins}m to close"
    return f"{mins}m to close"


# ── State management ────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[OPTIONS] State save error: {e}", file=sys.stderr)


# ── Discord helper ──────────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str = None) -> bool:
    tok = token or JORDAN_TOKEN
    if not tok:
        print(f"[OPTIONS] ERROR: No token", file=sys.stderr)
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {tok}", "Content-Type": "application/json"}
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    success = True
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[OPTIONS] Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                success = False
        except Exception as e:
            print(f"[OPTIONS] Post exception: {e}", file=sys.stderr)
            success = False
    return success


# ── Positions management ────────────────────────────────────────────────────
def load_positions() -> list:
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and 'positions' in data:
                    return data['positions']
    except Exception:
        pass
    return []


def save_positions(positions: list):
    try:
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"[OPTIONS] Positions save error: {e}", file=sys.stderr)


def add_position(option_type: str, strike: float, entry_price: float,
                 target_price: float = None, source: str = "manual",
                 notes: str = "") -> dict:
    """Add a new SPY 0DTE options position. Expiry always = today."""
    expiry = today_str()
    position = {
        "id": f"SPY_{option_type.upper()}_{strike}_{expiry}",
        "ticker": TICKER,
        "type": option_type.upper(),        # CALL or PUT
        "position_type": "option",
        "source": source,                   # "discord_group" | "manual" | "personal"
        "strike": strike,
        "expiry": expiry,                   # always today for 0DTE
        "entry_price": entry_price,
        "target_price": target_price,
        "entry_date": expiry,
        "notes": notes,
        "status": "open",
        "target_alerted": False,
        "approaching_alerted": False,
    }
    positions = load_positions()
    # Replace if same ID already exists (re-add)
    positions = [p for p in positions if p['id'] != position['id']]
    positions.append(position)
    save_positions(positions)
    return position


# ── Market data ─────────────────────────────────────────────────────────────
def get_spy_price() -> float:
    """Fetch current SPY spot price from Yahoo Finance."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=1d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            result = data.get('chart', {}).get('result', [])
            if result:
                meta = result[0].get('meta', {})
                price = meta.get('regularMarketPrice', 0)
                if price:
                    return float(price)
    except Exception as e:
        print(f"[OPTIONS] SPY price fetch error: {e}", file=sys.stderr)
    return 0.0


def is_market_hours() -> bool:
    """Return True if current time is within market hours (9:30–16:00 ET, Mon–Fri)."""
    et = now_et()
    if et.weekday() >= 5:
        return False
    market_open  = et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= et <= market_close


def is_post_market() -> bool:
    """Return True after 4:15 PM ET (EOD sweep window)."""
    et = now_et()
    if et.weekday() >= 5:
        return False
    eod = et.replace(hour=16, minute=15, second=0, microsecond=0)
    return et >= eod


# ── Time-based alert helpers ────────────────────────────────────────────────
def _alert_fired(state: dict, key: str) -> bool:
    """Check if a dated alert has already fired today."""
    return state.get(key) == today_str()


def _mark_alert(state: dict, key: str):
    """Mark a dated alert as fired today."""
    state[key] = today_str()


def _format_open_positions(open_positions: list, spy_price: float) -> str:
    """One-liner per open position for inclusion in broadcast alerts."""
    if not open_positions:
        return "  (none)"
    lines = []
    for p in open_positions:
        target = p.get('target_price')
        entry  = p.get('entry_price', '?')
        opt    = p.get('type', '?')
        strike = p.get('strike', '?')
        source_tag = f" [{p.get('source', 'manual')}]" if p.get('source', 'manual') != 'manual' else ""
        if target and spy_price:
            move_needed = float(target) - spy_price
            direction   = "↑" if opt == 'CALL' else "↓"
            lines.append(
                f"  • SPY ${strike} {opt}{source_tag} | target ${target:.2f} "
                f"({direction}${abs(move_needed):.2f} away) | entry ${entry}"
            )
        else:
            lines.append(f"  • SPY ${strike} {opt}{source_tag} | entry ${entry}")
    return "\n".join(lines)


# ── EOD sweep ───────────────────────────────────────────────────────────────
def run_eod_sweep(post: bool = True) -> dict:
    """
    After 4:15 PM ET — mark today's 0DTE positions as expired.
    Post EOD summary to #active-plays.
    """
    state     = load_state()
    today     = today_str()
    sweep_key = f"eod_sweep_{today}"

    if _alert_fired(state, sweep_key):
        print("[OPTIONS] EOD sweep already done today")
        return {"skipped": True}

    positions = load_positions()
    updated   = False
    hits      = []
    misses    = []

    for pos in positions:
        if pos.get('status') != 'open':
            continue
        if pos.get('expiry') != today:
            continue

        # Mark as expired
        pos['status'] = 'expired'
        updated = True

        opt    = pos.get('type', '?')
        strike = pos.get('strike', '?')
        target = pos.get('target_price')
        entry  = pos.get('entry_price', 0)

        if pos.get('target_alerted'):
            hits.append(f"  ✅ SPY ${strike} {opt} — TARGET HIT | entry ${entry}")
        else:
            miss_str = f"  ❌ SPY ${strike} {opt} — missed target"
            if target:
                miss_str += f" (${target:.2f})"
            miss_str += f" | entry ${entry}"
            misses.append(miss_str)

    if updated:
        save_positions(positions)

    if hits or misses:
        summary_lines = [
            f"📋 **EOD Summary — SPY 0DTE | {today}**",
            "",
        ]
        if hits:
            summary_lines.append("**Targets Hit:**")
            summary_lines.extend(hits)
        if misses:
            summary_lines.append("**Targets Missed:**")
            summary_lines.extend(misses)
        summary_lines.append("\n_All 0DTE positions marked expired. Clean slate tomorrow._")
        summary = "\n".join(summary_lines)
        print(f"[OPTIONS] EOD Summary:\n{summary}")
        if post:
            post_discord(CHANNEL_ACTIVE_PLAYS, summary)
    else:
        print("[OPTIONS] EOD sweep: no open 0DTE positions to close")

    _mark_alert(state, sweep_key)
    save_state(state)
    return {"hits": len(hits), "misses": len(misses)}


# ── Price Monitor ───────────────────────────────────────────────────────────
def run_price_monitor(post: bool = True) -> dict:
    """
    SPY 0DTE price monitor. Run every 5 min during market hours via firm.py.

    Flow:
      1. Skip if outside market hours
      2. Fetch SPY spot price
      3. Post morning brief if first run of day (after 9:30 ET)
      4. Check time-based alerts (2pm, 3pm, 3:45pm)
      5. For each open position: check target, check approaching, check expired
      6. Write agent status
    """
    # ── EOD sweep check ──────────────────────────────────────────────────────
    if is_post_market():
        return run_eod_sweep(post=post)

    if not is_market_hours():
        print("[OPTIONS] Outside market hours — price monitor skipped")
        return {"skipped": True, "reason": "outside_market_hours"}

    spy_price = get_spy_price()
    if not spy_price:
        print("[OPTIONS] Could not fetch SPY price", file=sys.stderr)
        return {"error": "price_fetch_failed"}

    state     = load_state()
    today     = today_str()
    et        = now_et()
    positions = load_positions()
    open_pos  = [p for p in positions if p.get('status') == 'open']

    hits        = []
    approaching = []
    updated     = False

    # ── Morning brief ────────────────────────────────────────────────────────
    morning_key = f"morning_brief_{today}"
    if not _alert_fired(state, morning_key) and et.hour >= 9 and (et.hour > 9 or et.minute >= 30):
        pos_summary = _format_open_positions(open_pos, spy_price)
        brief = (
            f"☀️ **Morning Brief — SPY 0DTE Desk | {today}**\n"
            f"SPY: **${spy_price:.2f}**\n\n"
            f"**Open Positions:**\n{pos_summary}\n\n"
            f"_0DTE — theta burns all day. Stay sharp._"
        )
        print(f"[OPTIONS] Morning brief:\n{brief}")
        if post:
            post_discord(CHANNEL_OPTIONS_EDUCATION, brief)
        _mark_alert(state, morning_key)
        updated = True

    # ── Time-based warnings ──────────────────────────────────────────────────
    hour   = et.hour
    minute = et.minute

    # 2:00 PM ET alert
    alert_2pm_key = f"alert_2pm_{today}"
    if hour == 14 and minute < 15 and not _alert_fired(state, alert_2pm_key):
        pos_summary = _format_open_positions(open_pos, spy_price)
        msg = (
            f"⏰ **2PM WARNING — 90 min to close**\n"
            f"SPY: **${spy_price:.2f}** | {time_to_close_str()}\n\n"
            f"**Open positions:**\n{pos_summary}\n\n"
            f"Theta burning fast. Plan your exit."
        )
        if post:
            post_discord(CHANNEL_ACTIVE_PLAYS, msg)
        _mark_alert(state, alert_2pm_key)
        updated = True

    # 3:00 PM ET alert
    alert_3pm_key = f"alert_3pm_{today}"
    if hour == 15 and minute < 10 and not _alert_fired(state, alert_3pm_key):
        pos_summary = _format_open_positions(open_pos, spy_price)
        msg = (
            f"🔴 **FINAL HOUR — 60 min to close**\n"
            f"SPY: **${spy_price:.2f}** | {time_to_close_str()}\n\n"
            f"**Open positions:**\n{pos_summary}\n\n"
            f"Exit or let expire — decide now."
        )
        if post:
            post_discord(CHANNEL_ACTIVE_PLAYS, msg)
        _mark_alert(state, alert_3pm_key)
        updated = True

    # 3:45 PM ET alert
    alert_345pm_key = f"alert_345pm_{today}"
    if hour == 15 and 45 <= minute < 55 and not _alert_fired(state, alert_345pm_key):
        pos_summary = _format_open_positions(open_pos, spy_price)
        msg = (
            f"🚨 **15 MIN TO CLOSE**\n"
            f"SPY: **${spy_price:.2f}** | {time_to_close_str()}\n\n"
            f"**Open positions:**\n{pos_summary}\n\n"
            f"Last chance to exit for value."
        )
        if post:
            post_discord(CHANNEL_ACTIVE_PLAYS, msg)
        _mark_alert(state, alert_345pm_key)
        updated = True

    # ── Per-position checks ──────────────────────────────────────────────────
    for pos in open_pos:
        if not pos.get('target_price'):
            continue

        target    = float(pos['target_price'])
        opt_type  = pos.get('type', 'CALL').upper()
        entry     = pos.get('entry_price', 0)
        strike    = pos.get('strike', '?')
        source    = pos.get('source', 'manual')
        source_tag = f" [{source}]" if source != 'manual' else ""

        is_call = (opt_type == 'CALL')
        is_put  = (opt_type == 'PUT')

        # Distance calculations
        move_needed   = target - spy_price
        pct_to_target = abs(move_needed) / spy_price * 100

        # HIT check
        target_hit = (is_call and spy_price >= target) or (is_put and spy_price <= target)

        # APPROACHING: within 0.5% for SPY (tighter than generic 1%)
        approaching_now = pct_to_target <= 0.5 and not target_hit

        if target_hit and not pos.get('target_alerted'):
            direction = "📈 TARGET HIT" if is_call else "📉 TARGET HIT"
            overshoot = spy_price - target if is_call else target - spy_price
            msg = (
                f"🎯 **{direction}** — SPY ${strike} {opt_type}{source_tag}\n"
                f"SPY: **${spy_price:.2f}** | Target: **${target:.2f}** | "
                f"Entry: ${entry} | Overshoot: +${overshoot:.2f}\n"
                f"⏱ {time_to_close_str()}\n"
                f"**Book it or trail a stop.**"
            )
            # LLM exit analysis when target hit
            try:
                import sys as _sys_jrd
                if '/home/cody/stratton/bots' not in _sys_jrd.path:
                    _sys_jrd.path.insert(0, '/home/cody/stratton/bots')
                from llm_client import llm_reason, options_setup_prompt
                _et_now    = now_et()
                _close_dt  = _et_now.replace(hour=16, minute=0, second=0, microsecond=0)
                _mins_left = max(0, int((_close_dt - _et_now).total_seconds() / 60))
                _jrd_prompt = options_setup_prompt(
                    ticker            = "SPY",
                    option_type       = opt_type,
                    current_price     = spy_price,
                    target            = target,
                    time_to_close_mins= _mins_left,
                )
                _jrd_result = llm_reason(_jrd_prompt, primary="grok")
                if _jrd_result.get("reasoning"):
                    msg += "\n🧠 **Options Take:** " + _jrd_result.get("reasoning", "")[:300]
            except Exception as _jrd_err:
                pass  # LLM failure never blocks alerts
            hits.append(msg)
            for p in positions:
                if p.get('id') == pos.get('id'):
                    p['target_alerted'] = True
            updated = True

        elif approaching_now and not pos.get('approaching_alerted'):
            direction_arrow = "⬆️" if is_call else "⬇️"
            move_dir = "needs" if (is_call and move_needed > 0) or (is_put and move_needed < 0) else "past"
            msg = (
                f"⚡ **APPROACHING TARGET** {direction_arrow} — SPY ${strike} {opt_type}{source_tag}\n"
                f"SPY: **${spy_price:.2f}** | Target: **${target:.2f}** | {pct_to_target:.2f}% away\n"
                f"SPY {move_dir} ${abs(move_needed):.2f} more | Entry: ${entry} | {time_to_close_str()}\n"
                f"Get ready."
            )
            approaching.append(msg)
            for p in positions:
                if p.get('id') == pos.get('id'):
                    p['approaching_alerted'] = True
            updated = True

    if updated:
        save_positions(positions)
        save_state(state)

    # Post alerts to #active-plays
    if post:
        for msg in hits:
            post_discord(CHANNEL_ACTIVE_PLAYS, msg)
        for msg in approaching:
            post_discord(CHANNEL_ACTIVE_PLAYS, msg)

    result = {
        "spy_price": spy_price,
        "monitored": len(open_pos),
        "hits": len(hits),
        "approaching": len(approaching),
    }
    print(f"[OPTIONS] Price monitor: {result}")
    _write_status('options', {
        'mode': 'price_monitor',
        'spy_price': spy_price,
        'positions_monitored': result['monitored'],
        'target_hits': result['hits'],
        'approaching_alerts': result['approaching'],
    })
    return result


# ── Position check ──────────────────────────────────────────────────────────
def run_position_check(post: bool = True) -> str:
    """List all open SPY 0DTE positions with current status."""
    positions = load_positions()
    open_pos  = [p for p in positions if p.get('status') == 'open']

    spy_price = get_spy_price()
    now_str   = now_et().strftime('%Y-%m-%d %H:%M ET')

    lines = [f"**📊 OPTIONS — SPY 0DTE DESK** | {now_str}\n"]

    if not open_pos:
        lines.append("No open positions. Add with `--add CALL|PUT STRIKE ENTRY`")
    else:
        lines.append(f"SPY: **${spy_price:.2f}**\n")
        for pos in open_pos:
            opt    = pos.get('type', '?')
            strike = pos.get('strike', '?')
            entry  = pos.get('entry_price', '?')
            target = pos.get('target_price')
            source = pos.get('source', 'manual')
            source_tag = f" [{source}]" if source != 'manual' else ""

            target_str = ""
            if target and spy_price:
                move = float(target) - spy_price
                arrow = "↑" if opt == 'CALL' else "↓"
                pct   = abs(move) / spy_price * 100
                target_str = f" | target **${target:.2f}** ({arrow}${abs(move):.2f}, {pct:.2f}% away)"

            alerted_str = " ✅ TARGET HIT" if pos.get('target_alerted') else ""

            lines.append(
                f"• SPY **${strike} {opt}**{source_tag} | 0DTE {pos.get('expiry','')}\n"
                f"  Entry: ${entry} | SPY: ${spy_price:.2f}{target_str}{alerted_str}\n"
                f"  {time_to_close_str()}"
            )

    report = "\n".join(lines)
    if post:
        post_discord(CHANNEL_OPTIONS_EDUCATION, report)
    open_count = len(open_pos)
    _write_status('options', {
        'mode': 'position_check',
        'open_positions': open_count,
        'spy_price': spy_price,
    })
    return report


# ── Discord alert analysis ──────────────────────────────────────────────────
def analyze_discord_alert(alert_type: str, price_target: float) -> str:
    """Analyze a Discord group alert for SPY — CALL or PUT + price target."""
    alert_type = alert_type.upper()
    spy_price  = get_spy_price()

    if not spy_price:
        return "❌ Options: Can't pull SPY data right now. Try again."

    move_needed  = price_target - spy_price
    move_pct     = abs(move_needed) / spy_price * 100
    direction    = "up" if alert_type == 'CALL' else "down"
    time_left    = time_to_close_str()

    lines = [
        f"**🎯 OPTIONS — SPY 0DTE Alert Analysis**",
        f"Alert: SPY **{alert_type}** target **${price_target:.2f}**",
        f"SPY now: **${spy_price:.2f}** | Move needed: {direction} **${abs(move_needed):.2f}** ({move_pct:.1f}%)",
        f"⏱ {time_left}",
        "",
    ]

    # Go/No-Go
    verdict = ""
    reasons = []

    if move_pct < 0.3:
        reasons.append("Target too close to spot — basically ATM or in the money. Premium will be high.")
        verdict = "⚠️ TIGHT — check premium vs. reward"
    elif move_pct > 3.0:
        reasons.append("Large move required for 0DTE. Low probability, high leverage play.")
        verdict = "⚠️ LOTTERY TICKET — size tiny"
    else:
        reasons.append(f"Reasonable {move_pct:.1f}% move required. Viable 0DTE setup if catalyst exists.")
        verdict = "✅ VIABLE"

    # Time of day context
    et = now_et()
    if et.hour < 10:
        reasons.append("Early session — wide spreads, high IV. Wait for 10am open settle before entry.")
    elif et.hour >= 15:
        reasons.append("Late session — theta decay is brutal on 0DTE. Smaller size, fast exit plan.")

    if (alert_type == 'CALL' and move_needed < 0) or (alert_type == 'PUT' and move_needed > 0):
        reasons.append("⚠️ SPY is ALREADY past the target — this alert may be stale or wrong direction.")
        verdict = "🚨 CHECK THE ALERT"

    lines.extend([
        f"**Options Verdict: {verdict}**",
        "",
        "**Reasoning:**",
    ])
    for r in reasons:
        lines.append(f"  • {r}")

    lines.extend([
        "",
        f"**If you go:**",
        f"  Strike: Look for ${round(price_target / 5) * 5:.0f} or ATM ${round(spy_price / 5) * 5:.0f}",
        f"  Expiry: Today only (0DTE)",
        f"  Size: Small. Max 2% of account on Discord group alerts.",
        f"  Stop: Out at -40% premium. 0DTE has no recovery time.",
        "",
        f"_Log it with: `python3 bots/options.py --add {alert_type} <STRIKE> <ENTRY> --target {price_target:.2f} --source discord_group`_",
        f"*Options | The Firm*",
    ])

    return "\n".join(lines)


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Options — SPY 0DTE Options Desk')
    parser.add_argument('--position', action='store_true',
                        help='Check open positions')
    parser.add_argument('--monitor',  action='store_true',
                        help='Run price monitor (market hours only)')
    parser.add_argument('--add', nargs=3, metavar=('TYPE', 'STRIKE', 'ENTRY'),
                        help='Add SPY 0DTE position: CALL 502 8.50')
    parser.add_argument('--target',   type=float,
                        help='Price target for --add (e.g. 505.00)')
    parser.add_argument('--source',   type=str, default='manual',
                        help='Source: discord_group | manual | personal')
    parser.add_argument('--alert',    nargs=2, metavar=('TYPE', 'TARGET'),
                        help='Analyze Discord alert: CALL 505')
    parser.add_argument('--eod',      action='store_true',
                        help='Run EOD sweep manually')
    parser.add_argument('--no-post',  action='store_true',
                        help='Print only — do not post to Discord')
    args = parser.parse_args()

    post = not args.no_post

    if args.monitor:
        result = run_price_monitor(post=post)
        if args.no_post:
            print(f"[OPTIONS] Monitor result: {result}")

    elif args.add:
        opt_type, strike, entry = args.add
        pos = add_position(
            opt_type, float(strike), float(entry),
            target_price=args.target,
            source=args.source,
        )
        print(f"[OPTIONS] Position added: {pos['id']}")
        if args.target:
            print(f"[OPTIONS] Target price: ${args.target:.2f}")
        spy_price = get_spy_price()
        if spy_price and args.target:
            move = abs(float(args.target) - spy_price)
            pct  = move / spy_price * 100
            print(f"[OPTIONS] SPY: ${spy_price:.2f} | Move needed: ${move:.2f} ({pct:.1f}%) | {time_to_close_str()}")

    elif args.position:
        report = run_position_check(post=post)
        if args.no_post:
            print(report)

    elif args.alert:
        alert_type, target = args.alert
        report = analyze_discord_alert(alert_type, float(target))
        if post:
            post_discord(CHANNEL_OPTIONS_EDUCATION, report)
        else:
            print(report)

    elif args.eod:
        result = run_eod_sweep(post=post)
        print(f"[OPTIONS] EOD sweep: {result}")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
