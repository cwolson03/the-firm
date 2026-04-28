#!/usr/bin/env python3
"""
SUPERVISOR.PY — The Firm Heartbeat Monitor
============================================
The Firm — system health monitor.

Runs every 30 minutes via firm.py. Detects anomalies and posts alerts
to Discord #general if something looks wrong.

Checks:
  1. stratton-firm.service health (systemctl)
  2. firm.log error rate (>5 ERRORs in 30 min)
  3. Kalshi balance drop (>$20 since last check)
  4. Unexpected live orders on Kalshi (sports-* should be paper)
  5. Paper mode integrity (sports.py, weather.py)
  6. GDP T2.0 stop-loss (KXGDP-26APR30-T2.0 NO price below 50¢)

State:
  /home/cody/stratton/data/supervisor_state.json

Usage:
    python3 supervisor.py --once     # single check + exit
    python3 supervisor.py            # continuous (30-min loop)

Requirements:
    pip install requests cryptography
"""

import os
import sys
import json
import time
import base64
import logging
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — path auto-detect (Atlas = /home/cody, local = /home/stratton)
# ─────────────────────────────────────────────────────────────────────────────

if os.path.exists("/home/cody/stratton"):
    PRIVATE_KEY_PATH = "/home/cody/stratton/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/cody/stratton/config/bot-tokens.env"
    LOG_PATH         = "/home/cody/stratton/logs/supervisor.log"
    FIRM_LOG_PATH    = "/home/cody/stratton/config/firm.log"
    STATE_FILE       = "/home/cody/stratton/data/supervisor_state.json"
    SPORTS_PY_PATH     = "/home/cody/stratton/bots/sports.py"
    WEATHER_PY_PATH  = "/home/cody/stratton/bots/weather.py"
else:
    PRIVATE_KEY_PATH = "/home/stratton/.openclaw/workspace/config/kalshi_private.pem"
    BOT_TOKENS_ENV   = "/home/stratton/.openclaw/workspace/config/bot-tokens.env"
    LOG_PATH         = "/home/stratton/.openclaw/workspace/logs/supervisor.log"
    FIRM_LOG_PATH    = "/home/stratton/.openclaw/workspace/config/firm.log"
    STATE_FILE       = "/home/stratton/.openclaw/workspace/data/supervisor_state.json"
    SPORTS_PY_PATH     = "/home/stratton/.openclaw/workspace/research/sports.py"
    WEATHER_PY_PATH  = "/home/stratton/.openclaw/workspace/research/weather.py"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID = os.getenv("KALSHI_KEY_ID", "")

GENERAL_CHANNEL = 1491861935354810453   # #general
ALERT_COOLDOWN_HOURS = 2                # don't repeat the same alert within 2 hours
BALANCE_DROP_THRESHOLD = 20.0           # alert if balance drops more than $20
ERROR_RATE_THRESHOLD = 5                # alert if more than 5 ERRORs in 30 min
GDP_STOP_LOSS_TICKER = "KXGDP-26APR30-T2.0"
GDP_STOP_LOSS_PRICE  = 0.50             # alert if NO price drops below 50¢

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

log = logging.getLogger("supervisor")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh  = logging.FileHandler(LOG_PATH)
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_fh)
log.addHandler(_sh)

# ─────────────────────────────────────────────────────────────────────────────
# AUTH — RSA-PSS signing for Kalshi API
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

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD — STRATTON TOKEN
# ─────────────────────────────────────────────────────────────────────────────

def _load_stratton_token() -> str:
    token = os.environ.get("STRATTON_TOKEN", "")
    if token:
        return token
    try:
        with open(BOT_TOKENS_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("STRATTON_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        log.error(f"Could not load STRATTON_TOKEN: {e}")
    return ""


def send_alert(message: str):
    """Post alert to #general via Stratton bot."""
    token = _load_stratton_token()
    if not token:
        log.error("STRATTON_TOKEN not set — cannot post Discord alert")
        return
    url     = f"https://discord.com/api/v10/channels/{GENERAL_CHANNEL}/messages"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    # Chunk if needed
    for chunk in [message[i:i+1990] for i in range(0, len(message), 1990)]:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                log.error(f"Discord alert failed: {r.status_code} {r.text[:200]}")
            else:
                log.info(f"Discord alert posted: {chunk[:80]}...")
        except Exception as e:
            log.error(f"Discord alert exception: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# STATE PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load supervisor state from JSON. Returns empty state if missing."""
    default = {
        "last_balance": None,
        "last_check":   None,
        "alerts_sent":  [],
    }
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                data = json.load(f)
            # Merge with defaults for any missing keys
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        log.error(f"Could not load state: {e}")
    return default


def save_state(state: dict):
    """Persist state to JSON."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"Could not save state: {e}")


def should_send_alert(state: dict, alert_key: str) -> bool:
    """
    Return True if we haven't sent this alert within ALERT_COOLDOWN_HOURS.
    alert_key is a short string identifying the alert type.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ALERT_COOLDOWN_HOURS)
    for entry in state.get("alerts_sent", []):
        if entry.get("key") == alert_key:
            try:
                sent_at = datetime.fromisoformat(entry["timestamp"])
                if sent_at > cutoff:
                    log.info(f"Alert '{alert_key}' suppressed (sent {sent_at.strftime('%H:%M')} UTC, cooldown active)")
                    return False
            except Exception:
                pass
    return True


def record_alert(state: dict, alert_key: str):
    """Record that an alert was sent."""
    now = datetime.now(timezone.utc).isoformat()
    # Remove old entries for the same key
    state["alerts_sent"] = [
        e for e in state.get("alerts_sent", [])
        if e.get("key") != alert_key
    ]
    state["alerts_sent"].append({"key": alert_key, "timestamp": now})
    # Prune entries older than 24 hours
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    state["alerts_sent"] = [
        e for e in state["alerts_sent"]
        if e.get("timestamp", "") > cutoff
    ]

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1: Firm service health
# ─────────────────────────────────────────────────────────────────────────────

def check_firm_service(state: dict) -> Optional[str]:
    """
    Check stratton-firm.service via systemctl.
    Returns alert message if down, else None.
    """
    log.info("[Check 1] Firm service health...")
    # Only run this check on Atlas where the service actually exists
    if not os.path.exists("/home/cody/stratton"):
        log.info("  Not on Atlas — skipping service check")
        return None
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'stratton-firm'],
            capture_output=True, text=True, timeout=10
        )
        status = result.stdout.strip()
        log.info(f"  stratton-firm.service: {status}")
        if status != 'active':
            key = "firm_service_down"
            if should_send_alert(state, key):
                return (key, f"🚨 FIRM DOWN: stratton-firm.service is **{status}** (not active)\n"
                             f"Action: `sudo systemctl restart stratton-firm` or check logs")
            return None
    except FileNotFoundError:
        # systemctl not available (local dev environment)
        log.info("  systemctl not found — skipping service check (local env)")
    except subprocess.TimeoutExpired:
        log.warning("  systemctl timed out")
        key = "systemctl_timeout"
        if should_send_alert(state, key):
            return (key, "⚠️ SUPERVISOR: systemctl timed out checking stratton-firm.service")
    except Exception as e:
        log.error(f"  Service check error: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2: Log error rate
# ─────────────────────────────────────────────────────────────────────────────

def check_log_error_rate(state: dict) -> Optional[tuple]:
    """
    Read last 200 lines of firm.log.
    Count ERROR lines in the last 30 minutes.
    Returns (key, alert_message) if > 5 errors, else None.
    """
    log.info("[Check 2] Log error rate...")
    try:
        if not os.path.exists(FIRM_LOG_PATH):
            log.info(f"  firm.log not found at {FIRM_LOG_PATH}")
            return None

        with open(FIRM_LOG_PATH) as f:
            lines = f.readlines()

        # Take last 200 lines
        recent_lines = lines[-200:]
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=30)

        errors_found = []
        for line in recent_lines:
            if ' ERROR ' not in line and ' ERROR\t' not in line:
                continue
            # Try to parse timestamp from log line: [2026-04-16 02:30:00] ERROR ...
            try:
                ts_part = line[1:20]  # "[2026-04-16 02:30:00]"
                line_time = datetime.strptime(ts_part, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if line_time >= cutoff:
                    errors_found.append(line.strip())
            except Exception:
                # If we can't parse the timestamp, include it anyway (conservative)
                errors_found.append(line.strip())

        log.info(f"  {len(errors_found)} ERROR lines in last 30 min")

        if len(errors_found) > ERROR_RATE_THRESHOLD:
            key = "log_error_rate"
            if should_send_alert(state, key):
                error_sample = "\n".join(errors_found[:5])
                return (key, f"⚠️ LOG ERROR SURGE: {len(errors_found)} errors in last 30 min\n"
                             f"```\n{error_sample}\n```")
    except Exception as e:
        log.error(f"  Log error rate check failed: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3: Balance check
# ─────────────────────────────────────────────────────────────────────────────

def check_balance(state: dict) -> Optional[tuple]:
    """
    Fetch Kalshi balance. Alert if dropped more than $20 since last check.
    Updates state["last_balance"].
    Returns (key, alert_message) if drop detected, else None.
    """
    log.info("[Check 3] Balance check...")
    try:
        data = kalshi_get("/portfolio/balance")
        if not data:
            log.warning("  Could not fetch balance")
            return None

        balance_cents = data.get("balance", 0)
        balance = float(balance_cents) / 100.0
        log.info(f"  Current balance: ${balance:.2f}")

        last_balance = state.get("last_balance")
        state["last_balance"] = balance   # always update

        if last_balance is not None:
            drop = last_balance - balance
            log.info(f"  Last balance: ${last_balance:.2f} | Drop: ${drop:.2f}")
            if drop > BALANCE_DROP_THRESHOLD:
                key = "balance_drop"
                if should_send_alert(state, key):
                    return (key, f"💸 BALANCE DROP ALERT: ${last_balance:.2f} → ${balance:.2f} "
                                 f"(−${drop:.2f} in last 30 min)\n"
                                 f"Review open positions and recent fills.")
        else:
            log.info("  No previous balance on record — establishing baseline")
    except Exception as e:
        log.error(f"  Balance check error: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4: Unexpected live orders
# ─────────────────────────────────────────────────────────────────────────────

def check_unexpected_orders(state: dict) -> Optional[tuple]:
    """
    Fetch resting orders. Flag:
    - sports-* prefix orders (Sports should be paper)
    - Unknown prefix (not donnie-* and not from known bots)
    Returns (key, alert_message) if suspicious orders found, else None.
    """
    log.info("[Check 4] Unexpected live orders...")
    try:
        data = kalshi_get("/portfolio/orders", params={"status": "resting", "limit": 100})
        orders = data.get("orders", [])
        log.info(f"  {len(orders)} resting orders on Kalshi")

        suspicious = []
        brad_live   = []

        for o in orders:
            client_id = str(o.get("client_order_id", "") or "")
            ticker    = o.get("ticker", "")
            side      = o.get("side", "")
            price     = o.get("yes_price") or o.get("no_price") or 0

            # Donnie orders are expected
            if client_id.startswith("donnie-"):
                continue

            # Sports orders should be paper (sports should not place real Kalshi orders)
            if client_id.startswith("brad-"):
                brad_live.append(f"  {ticker} BUY {side} @ {price}¢ (id={client_id[:20]})")
                continue

            # weather- or mark- prefix are also unexpected live
            if client_id.startswith(("weather-", "mark-", "brad-stink-")):
                suspicious.append(f"  {ticker} BUY {side} @ {price}¢ (id={client_id[:20]})")
                continue

            # Truly unknown order
            if client_id:
                suspicious.append(f"  {ticker} BUY {side} @ {price}¢ (id={client_id[:20]})")

        alerts = []
        if brad_live:
            key = "brad_live_orders"
            if should_send_alert(state, key):
                order_list = "\n".join(brad_live)
                alerts.append((key, f"🚨 SPORTS IS LIVE: {len(brad_live)} real resting order(s) found — "
                                    f"Sports should be in paper mode!\n```\n{order_list}\n```\n"
                                    f"Action: Cancel orders and check sports.py run_scan()"))

        if suspicious:
            key = "unknown_live_orders"
            if should_send_alert(state, key):
                order_list = "\n".join(suspicious)
                alerts.append((key, f"⚠️ UNKNOWN LIVE ORDERS: {len(suspicious)} unexpected resting order(s)\n"
                                    f"```\n{order_list}\n```\nReview immediately."))

        if alerts:
            # Combine into one message if multiple
            combined_key = alerts[0][0]
            combined_msg = "\n\n".join(msg for _, msg in alerts)
            return (combined_key, combined_msg)
    except Exception as e:
        log.error(f"  Unexpected orders check error: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 5: Paper mode integrity
# ─────────────────────────────────────────────────────────────────────────────

def check_paper_mode_integrity(state: dict) -> Optional[tuple]:
    """
    Read sports.py and weather.py source to verify paper mode is intact.
    Returns (key, alert_message) if paper mode is broken, else None.
    """
    log.info("[Check 5] Paper mode integrity...")
    issues = []

    # Sports check
    try:
        if os.path.exists(SPORTS_PY_PATH):
            with open(SPORTS_PY_PATH) as f:
                brad_content = f.read()

            if 'paper=False' in brad_content and 'paper=True' not in brad_content:
                issues.append("⚠️ PAPER MODE BROKEN: sports.py has paper=False but no paper=True anywhere")
            else:
                log.info("  sports.py paper mode: OK")
        else:
            log.info(f"  sports.py not found at {SPORTS_PY_PATH}")
    except Exception as e:
        log.error(f"  Sports paper mode check error: {e}")

    # Weather check
    try:
        if os.path.exists(WEATHER_PY_PATH):
            with open(WEATHER_PY_PATH) as f:
                weather_content = f.read()

            # Check weather.py live/paper mode status
            # Live mode (dry_run=False in run_scan) is intentional after approval
            # Only flag if something looks actively wrong (e.g. both True and False in conflict)
            has_dry_run_true  = 'dry_run=True' in weather_content
            has_dry_run_false = 'dry_run=False' in weather_content
            # Check for LIVE MODE approval comment to confirm intentional
            is_live_approved = 'LIVE MODE' in weather_content and 'approved' in weather_content.lower()

            if has_dry_run_false and not has_dry_run_true:
                if is_live_approved:
                    log.info("  weather.py: LIVE MODE (approved) — paper mode intentionally disabled")
                else:
                    issues.append("⚠️ PAPER MODE BROKEN: weather.py has dry_run=False but no approval comment")
            elif has_dry_run_true:
                log.info("  weather.py paper mode: OK (paper/dry-run active)")
            else:
                log.info("  weather.py mode: unknown")
        else:
            log.info(f"  weather.py not found at {WEATHER_PY_PATH}")
    except Exception as e:
        log.error(f"  Weather paper mode check error: {e}")

    if issues:
        key = "paper_mode_broken"
        if should_send_alert(state, key):
            return (key, "\n".join(issues))
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CHECK 6: GDP position stop-loss
# ─────────────────────────────────────────────────────────────────────────────

def check_gdp_stop_loss(state: dict) -> Optional[tuple]:
    """
    Fetch KXGDP-26APR30-T2.0 market price.
    If NO price < 50¢ → alert "review position".
    Returns (key, alert_message) if triggered, else None.
    """
    log.info("[Check 6] GDP T2.0 stop-loss...")
    try:
        data   = kalshi_get(f"/markets/{GDP_STOP_LOSS_TICKER}")
        market = data.get("market", {})

        if not market:
            log.warning(f"  Could not fetch {GDP_STOP_LOSS_TICKER} market data")
            return None

        yes_bid = float(market.get("yes_bid_dollars", 0.5) or 0.5)
        no_price = round(1.0 - yes_bid, 3)

        log.info(f"  {GDP_STOP_LOSS_TICKER}: YES bid={yes_bid:.2f} NO price≈{no_price:.2f} ({int(no_price*100)}¢)")

        if no_price < GDP_STOP_LOSS_PRICE:
            key = "gdp_stop_loss"
            if should_send_alert(state, key):
                return (key, f"⚠️ GDP T2.0 stop-loss: NO at {int(no_price*100)}¢ — review position\n"
                             f"Thesis was NO wins (GDP < 2.0%). If NO price drops here, "
                             f"market disagrees — check GDPNow and consider exiting.")
    except Exception as e:
        log.error(f"  GDP stop-loss check error: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SUPERVISOR CHECK
# ─────────────────────────────────────────────────────────────────────────────

def run_supervisor_check():
    """Run all 6 checks. Post alerts to Discord if anomalies detected."""
    now = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info(f"SUPERVISOR CHECK — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 60)

    state = load_state()
    alerts_fired = []
    checks_passed = []
    checks_failed = []

    # ── Run all checks ───────────────────────────────────────────────────────
    checks = [
        ("firm_service",    check_firm_service),
        ("log_error_rate",  check_log_error_rate),
        ("balance",         check_balance),
        ("unexpected_orders", check_unexpected_orders),
        ("paper_mode",      check_paper_mode_integrity),
        ("gdp_stop_loss",   check_gdp_stop_loss),
    ]

    for check_name, check_fn in checks:
        try:
            result = check_fn(state)
            if result:
                key, message = result
                log.warning(f"ALERT [{check_name}]: {message[:120]}")
                send_alert(message)
                record_alert(state, key)
                alerts_fired.append(check_name)
                checks_failed.append(check_name)
            else:
                checks_passed.append(check_name)
        except Exception as e:
            log.error(f"Check '{check_name}' crashed: {e}", exc_info=True)
            checks_failed.append(check_name)

    # ── Update state ─────────────────────────────────────────────────────────
    state["last_check"] = now.isoformat()
    save_state(state)

    # ── Summary ──────────────────────────────────────────────────────────────
    log.info("-" * 60)
    log.info(f"Supervisor check complete | Passed: {len(checks_passed)} | Alerts: {len(alerts_fired)}")
    if alerts_fired:
        log.warning(f"Alerts fired: {', '.join(alerts_fired)}")
    else:
        log.info("All checks clean — no anomalies detected")
    log.info("=" * 60)

    return {
        "passed": checks_passed,
        "alerted": alerts_fired,
        "failed": checks_failed,
    }

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────────

def run_scan(post=None, **kwargs):
    """Supervisor check — runs every 30 min via firm.py"""
    run_supervisor_check()
    try:
        import sys as _ss, os as _so
        _ss.path.insert(0, _so.path.dirname(_so.path.abspath(__file__)))
        from shared_context import write_agent_status
        write_agent_status('supervisor', {'status': 'ran'})
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Supervisor — The Firm heartbeat monitor")
    parser.add_argument("--once", action="store_true", help="Run one check cycle and exit")
    args = parser.parse_args()

    if args.once:
        result = run_supervisor_check()
        print("\n" + "=" * 60)
        print("SUPERVISOR SUMMARY")
        print("=" * 60)
        print(f"Checks passed: {result['passed']}")
        print(f"Alerts fired:  {result['alerted']}")
        if result['alerted']:
            print("⚠️  Alerts were posted to Discord #general")
        else:
            print("✅ All clear — no alerts fired")
        print("=" * 60)
        return

    import time as _time
    while True:
        run_supervisor_check()
        _time.sleep(1800)   # 30 minutes


if __name__ == "__main__":
    main()
