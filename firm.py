#!/usr/bin/env python3
"""
FIRM.PY — The Master Coordinator
Stratton Oakmont Discord Intelligence System

All bots. One command. python3 firm.py

"The most important thing is to have fun. The second most important
thing is to have fun." — Mark Hanna
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
import threading
import importlib.util
import requests
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR = os.environ.get("FIRM_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(BASE_DIR, "agents")
TOOLS_DIR  = os.path.join(BASE_DIR, "tools")
LOGS_DIR   = os.path.join(BASE_DIR, "logs")

os.makedirs(LOGS_DIR, exist_ok=True)

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            os.path.join(LOGS_DIR, 'firm.log'),
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
        ),
    ]
)
log = logging.getLogger('FIRM')

# ── Load environment ─────────────────────────────────────────────────────────
load_dotenv(os.path.join(BASE_DIR, '.env'))

STRATTON_TOKEN   = os.getenv('STRATTON_TOKEN', '')
DONNIE_TOKEN     = os.getenv('DONNIE_TOKEN', '')
RUGRAT_TOKEN     = os.getenv('RUGRAT_TOKEN', '')
CHESTER_TOKEN    = os.getenv('CHESTER_TOKEN', '')
JORDAN_TOKEN     = os.getenv('JORDAN_TOKEN', '')
BRAD_TOKEN       = os.getenv('BRAD_TOKEN', '')
MARK_HANNA_TOKEN = os.getenv('MARK_HANNA_TOKEN', '')

# ── Channel IDs ─────────────────────────────────────────────────────────────
CHANNELS = {
    'kalshi':            1491861941361180924,
    'polymarket':        1491861941361180924,
    'sports-betting':    1491861968355590242,
    'promo-tracker':     1491861971635540108,
    'crypto-stack':      1491861963016507472,
    'whale-watch':       1491861959396561166,
    'senator-tracker':   1491861949615702076,
    'options-education': 1491861977214222366,
    'active-plays':      1491861990312906773,
    'watchlist':         1491861949615702076,
    'macro-context':     1491861985162432634,
    'deep-dives':        1491861982209511526,
    'the-crucible':      1491861982209511526,
    'daily-brief':       1491185593668079787,
    'bot-logs':          1491861993022554284,
    'general':           1491861935354810453,
}

# ── Scanner schedule (minutes between runs) ─────────────────────────────────
SCHEDULE = {
    'kalshi':     120,    # Donnie: every 2 hours
    'sports':     30,     # Brad: every 30 min
    'congress':   240,    # Rugrat: every 4 hours
    'whale':      30,     # Chester: every 30 min
    'options':    60,     # Jordan: hourly position check
    'research':   10080,  # Mark Hanna: weekly (7 days in minutes)
    'weather':    5,      # Weather bot: every 5 min
    'supervisor': 30,     # Supervisor heartbeat: every 30 min
}

# Track last run timestamps
_last_run = {k: 0 for k in SCHEDULE}
_running  = True
_stop_event = threading.Event()

# ── Discord post helper ─────────────────────────────────────────────────────
def post_discord(channel_id: int, content: str, token: str) -> bool:
    if not token:
        log.error(f"post_discord: no token provided for channel {channel_id}")
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                log.error(f"Discord error {r.status_code} → channel {channel_id}: {r.text[:200]}")
                return False
        except Exception as e:
            log.error(f"Discord post exception → channel {channel_id}: {e}")
            return False
    return True


def log_to_discord(message: str):
    """Log errors/events to #bot-logs."""
    try:
        post_discord(CHANNELS['bot-logs'], message, STRATTON_TOKEN or DONNIE_TOKEN or RUGRAT_TOKEN)
    except Exception:
        pass


# ── Dynamic module loader ────────────────────────────────────────────────────
def load_module(name: str, path: str):
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        log.info(f"Loaded module: {name}")
        return module
    except Exception as e:
        log.error(f"Failed to load {name}: {e}")
        return None


# ── Scanner runners ──────────────────────────────────────────────────────────
def run_kalshi_scanner():
    log.info("[DONNIE] Running Kalshi scan...")
    try:
        donnie_path = os.path.join(AGENTS_DIR, 'donnie.py')
        mod = load_module('donnie', donnie_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning("[DONNIE] donnie.py has no run_scan() function")
    except Exception as e:
        err = f"❌ **DONNIE (Kalshi) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_sports_scanner():
    log.info("[BRAD] Running sports scan...")
    try:
        sports_path = os.path.join(AGENTS_DIR, 'brad.py')
        mod = load_module('brad', sports_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning("[BRAD] brad.py has no run_scan() function")
    except Exception as e:
        err = f"❌ **BRAD (Sports) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_weather_scanner():
    log.info('[WEATHER] Running weather scan...')
    try:
        weather_path = os.path.join(AGENTS_DIR, 'weather.py')
        mod = load_module('weather', weather_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning('[WEATHER] weather.py has no run_scan() function')
    except Exception as e:
        err = f'❌ **WEATHER FAILED**: {e}'
        log.error(err)


def run_congress_scanner():
    log.info("[RUGRAT] Running congressional scan...")
    try:
        rugrat_path = os.path.join(AGENTS_DIR, 'rugrat.py')
        mod = load_module('rugrat', rugrat_path)
        if mod and hasattr(mod, 'run_recent'):
            mod.run_recent(post=False)  # SILENT MODE
        else:
            log.warning("[RUGRAT] rugrat.py has no run_recent() function")
    except Exception as e:
        err = f"❌ **RUGRAT (Congress) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_whale_scanner():
    log.info("[CHESTER] Running crypto whale scan...")
    try:
        chester_path = os.path.join(AGENTS_DIR, 'chester.py')
        mod = load_module('chester', chester_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=False)  # SILENT MODE
        else:
            log.warning("[CHESTER] chester.py has no run_scan() function")
    except Exception as e:
        err = f"❌ **CHESTER (Whale) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_options_check():
    log.info("[JORDAN] Running options position check...")
    try:
        jordan_path = os.path.join(AGENTS_DIR, 'jordan.py')
        mod = load_module('jordan', jordan_path)
        if mod and hasattr(mod, 'run_position_check'):
            mod.run_position_check(post=False)  # SILENT MODE
        else:
            log.warning("[JORDAN] jordan.py has no run_position_check() function")
    except Exception as e:
        err = f"❌ **JORDAN (Options) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_weekly_research():
    log.info("[MARK HANNA] Running weekly deep dive...")
    try:
        mark_path = os.path.join(AGENTS_DIR, 'mark_hanna.py')
        mod = load_module('mark_hanna', mark_path)
        if mod and hasattr(mod, 'run_weekly'):
            mod.run_weekly(post=False)  # SILENT MODE
        else:
            log.warning("[MARK HANNA] mark_hanna.py has no run_weekly() function")
    except Exception as e:
        err = f"❌ **MARK HANNA (Research) FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_supervisor():
    log.info('[SUPERVISOR] Running heartbeat check...')
    try:
        sup_path = os.path.join(TOOLS_DIR, 'supervisor.py')
        mod = load_module('supervisor', sup_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan()
        else:
            log.warning('[SUPERVISOR] supervisor.py has no run_scan() function')
    except Exception as e:
        err = f'❌ **SUPERVISOR FAILED**: {e}'
        log.error(err)
        log_to_discord(err)


# Per-scanner locks to prevent concurrent duplicate runs
_scanner_locks = {}
_locks_lock = threading.Lock()

def get_scanner_lock(name: str):
    with _locks_lock:
        if name not in _scanner_locks:
            _scanner_locks[name] = threading.Lock()
        return _scanner_locks[name]

# Map scanner names to functions
SCANNERS = {
    'kalshi':     run_kalshi_scanner,
    'sports':     run_sports_scanner,
    'congress':   run_congress_scanner,
    'whale':      run_whale_scanner,
    'options':    run_options_check,
    'research':   run_weekly_research,
    'weather':    run_weather_scanner,
    'supervisor': run_supervisor,
}


# ── Status command ───────────────────────────────────────────────────────────
def get_status() -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "**🏦 THE FIRM — STATUS REPORT**",
        f"*{now.strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "**Agents:**",
        "  🎰 Donnie — Kalshi prediction market execution",
        "  🌤️ Weather — Daily high temperature markets",
        "  🏈 Brad — Sports stink-bid strategy",
        "  🏛️ Rugrat — Congressional & insider tracker",
        "  🐋 Chester — Crypto whale tracker",
        "  📈 Jordan — Options position monitor",
        "  🎩 Mark Hanna — Macro research & deep dives",
        "",
        "**Scanner Status:**",
    ]
    for name, interval in SCHEDULE.items():
        last = _last_run.get(name, 0)
        if last == 0:
            status = "⏳ Not yet run"
        else:
            last_dt = datetime.fromtimestamp(last, tz=timezone.utc)
            ago = int((now.timestamp() - last) / 60)
            next_in = max(0, interval - ago)
            status = f"✅ Last: {ago}m ago | Next: ~{next_in}m"
        lines.append(f"  {name.capitalize()}: {status}")

    lines.extend([
        "",
        "**Commands:**",
        "  `/scan kalshi` `/scan sports` `/scan congress` `/scan weather`",
        "  `/scan whale` `/scan options` `/scan research` `/status`",
    ])
    return "\n".join(lines)


# ── Command handler ───────────────────────────────────────────────────────────
def handle_command(command: str, args: list = None) -> str:
    """Handle slash commands — called from Discord bot or direct invocation."""
    args = args or []
    command = command.lower().strip('/')

    if command in ('status',):
        return get_status()

    elif command == 'scan' and args:
        target = args[0].lower()
        if target in SCANNERS:
            SCANNERS[target]()
            return f"✅ Kicked off **{target}** scan."
        return f"❌ Unknown scan target: {target}. Options: {', '.join(SCANNERS.keys())}"

    elif command == 'research' and args and args[0].lower() == 'weekly':
        threading.Thread(target=run_weekly_research, daemon=True).start()
        return "✅ Mark Hanna is deep-diving..."

    elif command == 'research' and args and args[0].lower() == 'challenge':
        if len(args) > 1:
            ticker = args[1].upper()
            try:
                mark_path = os.path.join(AGENTS_DIR, 'mark_hanna.py')
                mod = load_module('mark_hanna', mark_path)
                if mod:
                    return mod.run_challenge(ticker, post=True)
            except Exception as e:
                return f"❌ Mark Hanna error: {e}"
        return "❌ Usage: /research challenge TICKER"

    return f"❌ Unknown command: {command}"


# ── Scheduler loop ───────────────────────────────────────────────────────────
def scheduler_loop():
    global _running
    log.info("[FIRM] Scheduler started.")
    while _running and not _stop_event.is_set():
        now = time.time()
        for name, interval_minutes in SCHEDULE.items():
            interval_seconds = interval_minutes * 60
            if now - _last_run[name] >= interval_seconds:
                _last_run[name] = now
                scanner_fn = SCANNERS.get(name)
                if scanner_fn:
                    lock = get_scanner_lock(name)
                    if lock.locked():
                        log.debug(f"[FIRM] {name} scanner still running — skipping this tick")
                    else:
                        def _run_with_lock(fn=scanner_fn, lk=lock, nm=name):
                            with lk:
                                fn()
                        t = threading.Thread(target=_run_with_lock, daemon=True, name=f"scanner-{name}")
                        t.start()
        time.sleep(60)  # check every minute


# ── Startup announcement ──────────────────────────────────────────────────────
def announce_startup():
    """Post startup message to #general."""
    stratton_tok = STRATTON_TOKEN
    if not stratton_tok:
        log.error("[FIRM] No Stratton token — skipping startup announcement")
        return

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    message = (
        f"🏦 **THE FIRM IS OPERATIONAL** — {now}\n\n"
        f"All agents are live. Intelligence is flowing.\n\n"
        f"**The Roster:**\n"
        f"🎰 **Donnie** — Kalshi scanner + execution engine. Every 2 hours.\n"
        f"🌤️ **Weather** — Daily high temp markets, dual-source forecasts. Every 5 min.\n"
        f"🏈 **Brad** — Sports stink-bid strategy. Every 30 min.\n"
        f"🏛️ **Rugrat** — Congressional trades, Senate Stock Watcher. Every 4 hours.\n"
        f"🐋 **Chester** — Crypto whale tracker, BTC mempool. Every 30 min.\n"
        f"📈 **Jordan** — Options position monitor. Every hour.\n"
        f"🎩 **Mark Hanna** — Weekly unconventional alpha deep-dives.\n\n"
        f"**Commands:** `/status` | `/scan [kalshi|sports|congress|whale|weather|options]` | `/research weekly`\n\n"
        f"_We don't slow down. We don't look back. We find the money._\n"
        f"— Stratton"
    )
    post_discord(CHANNELS['general'], message, stratton_tok)
    log.info("[FIRM] Startup announcement posted to #general")


# ── Signal handling ──────────────────────────────────────────────────────────
def shutdown(sig, frame):
    global _running
    log.info("[FIRM] Shutdown signal received. Stopping...")
    _running = False
    _stop_event.set()
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='The Firm — Master Agent Coordinator')
    parser.add_argument('--no-announce', action='store_true', help='Skip startup Discord announcement')
    parser.add_argument('--scan',        type=str,            help='Run a specific scanner immediately')
    parser.add_argument('--status',      action='store_true', help='Print status and exit')
    parser.add_argument('--command',     type=str,            help='Run a slash command (e.g. "scan kalshi")')
    parser.add_argument('--bot',         type=str,            help='Run a single agent standalone')
    parser.add_argument('--once',        action='store_true', help='Run all bots once then exit (no loop)')
    args = parser.parse_args()

    if args.status:
        print(get_status())
        return

    if args.command:
        parts = args.command.split()
        result = handle_command(parts[0], parts[1:])
        print(result)
        return

    if args.bot:
        bot_map = {
            'donnie':  ('kalshi',   'Donnie — Kalshi scanner'),
            'weather': ('weather',  'Weather — Temperature markets'),
            'rugrat':  ('congress', 'Rugrat — Congressional tracker'),
            'chester': ('whale',    'Chester — Crypto whale tracker'),
            'jordan':  ('options',  'Jordan — Options coach'),
            'brad':    ('sports',   'Brad — Sports & promo scanner'),
            'mark':    ('research', 'Mark Hanna — Research'),
        }
        key = args.bot.lower()
        if key in bot_map:
            scanner_key, label = bot_map[key]
            print(f"\n🚀 Running {label} standalone...\n")
            fn = SCANNERS.get(scanner_key)
            if fn:
                fn()
        else:
            print(f"Unknown bot: {args.bot}. Options: {', '.join(bot_map.keys())}")
        return

    if args.scan:
        fn = SCANNERS.get(args.scan.lower())
        if fn:
            fn()
        else:
            print(f"Unknown scanner: {args.scan}. Options: {', '.join(SCANNERS.keys())}")
        return

    if args.once:
        log.info("[FIRM] --once mode: running all scanners once then exiting")
        for name in ['kalshi', 'weather', 'sports', 'congress', 'whale', 'options']:
            try:
                log.info(f"[FIRM] Running {name}...")
                SCANNERS[name]()
            except Exception as e:
                log.error(f"{name} scan failed: {e}")
        log.info("[FIRM] All scanners complete. Exiting.")
        return

    # Full startup
    log.info("=" * 60)
    log.info("  THE FIRM — Starting up")
    log.info(f"  BASE_DIR: {BASE_DIR}")
    log.info("=" * 60)

    # Verify tokens
    token_check = {
        'DONNIE':      DONNIE_TOKEN,
        'RUGRAT':      RUGRAT_TOKEN,
        'CHESTER':     CHESTER_TOKEN,
        'JORDAN':      JORDAN_TOKEN,
        'BRAD':        BRAD_TOKEN,
        'MARK_HANNA':  MARK_HANNA_TOKEN,
    }
    for name, tok in token_check.items():
        if tok:
            log.info(f"  ✅ {name} token loaded")
        else:
            log.warning(f"  ⚠️ {name} token MISSING — bot will be limited")

    # Startup announcement
    if not args.no_announce:
        announce_startup()

    # Pre-stamp timestamps to prevent immediate double-fire
    now_ts = time.time()
    for name in list(SCHEDULE.keys()):
        _last_run[name] = now_ts

    # Run initial scans
    log.info("[FIRM] Running initial scans on startup...")
    for name in ['kalshi', 'weather']:
        try:
            log.info(f"[FIRM] Initial scan: {name}")
            SCANNERS[name]()
        except Exception as e:
            log.error(f"Initial {name} scan failed: {e}")

    # Start scheduler
    log.info("[FIRM] Starting scheduler loop...")
    scheduler_loop()


if __name__ == '__main__':
    main()
