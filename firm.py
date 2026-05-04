#!/usr/bin/env python3
"""
FIRM.PY — The Master Coordinator
The Firm

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
from dotenv import load_dotenv

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), '..', 'config', 'firm.log')),
    ]
)
log = logging.getLogger('FIRM')

# ── Config ─────────────────────────────────────────────────────────────────
BOT_DIR    = os.path.dirname(os.path.abspath(__file__))
TOKENS_FILE = os.path.join(BOT_DIR, '..', 'config', 'bot-tokens.env')
load_dotenv(TOKENS_FILE)

STRATTON_TOKEN  = os.getenv('STRATTON_TOKEN', '')
DONNIE_TOKEN    = os.getenv('DONNIE_TOKEN', '')
RUGRAT_TOKEN    = os.getenv('RUGRAT_TOKEN', '')
CHESTER_TOKEN   = os.getenv('CHESTER_TOKEN', '')
JORDAN_TOKEN    = os.getenv('JORDAN_TOKEN', '')
BRAD_TOKEN      = os.getenv('BRAD_TOKEN', '')
MARK_HANNA_TOKEN = os.getenv('MARK_HANNA_TOKEN', '')

# ── Channel IDs ─────────────────────────────────────────────────────────────
CHANNELS = {
    'kalshi':           1491861941361180924,
    'polymarket':       1491861941361180924,
    'sports-betting':   1491861968355590242,
    'promo-tracker':    1491861971635540108,
    'crypto-stack':     1491861963016507472,
    'whale-watch':      1491861959396561166,
    'senator-tracker':  1491861949615702076,
    'options-education':1491861977214222366,
    'active-plays':     1491861990312906773,
    'watchlist':        1491861949615702076,
    'macro-context':    1491861985162432634,
    'deep-dives':       1491861982209511526,
    'the-crucible':     1491861982209511526,
    'daily-brief':      1491185593668079787,
    'bot-logs':         1491861993022554284,
    'general':          1491861935354810453,
}

# ── Scanner schedule (minutes between runs) ─────────────────────────────────
SCHEDULE = {
    'kalshi':    30,    # Economics: every 30 min — commodity/crypto markets need fresh discovery
    'sports':    15,    # Sports: every 15 min (paper, Discord muted)
    'congress':  240,      # Congressional: every 4 hours
    'whale':     999999,   # Crypto: every 30 min
    'options':   999999,    # Options: DISABLED until SPY positions loaded (was 15 min)
    'research':  999999, # Weather Intel: weekly (7 days in minutes)
    'weather':   3,      # Weather bot: every 3 minutes
    'supervisor': 30,   # Supervisor heartbeat: every 30 min
    'eval': 10080,      # Eval Framework: weekly (7 days in minutes)
    'btc_watch': 5,     # BTC price watcher: every 5 min
}

# Track last run timestamps
_last_run = {k: 0 for k in SCHEDULE}
_running  = True

# Options full position-check cadence (separate from fast price-monitor loop)
_last_full_check = 0   # unix timestamp of last run_position_check() call

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
# Module cache — each bot loaded once and reused to prevent file descriptor leak.
# Weather runs every 3 min; without caching, load_module() accumulates open
# file handles until hitting OS limit (Errno 24 Too many open files).
_module_cache: dict = {}

def load_module(name: str, path: str):
    """Load module from path, using cache to prevent file descriptor accumulation."""
    if name in _module_cache:
        return _module_cache[name]
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module_cache[name] = module
        log.info(f"Loaded and cached module: {name}")
        return module
    except Exception as e:
        log.error(f"Failed to load {name}: {e}")
        return None

def reload_module(name: str, path: str):
    """Force reload a module, clearing the cache entry first."""
    _module_cache.pop(name, None)
    return load_module(name, path)


# ── Scanner runners ──────────────────────────────────────────────────────────
def run_kalshi_scanner():
    log.info("[ECONOMICS] Running Kalshi scan...")
    try:
        donnie_path = os.path.join(BOT_DIR, 'economics.py')
        mod = load_module("economics", donnie_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning("[ECONOMICS] economics.py has no run_scan() function")
    except Exception as e:
        err = f"❌ **ECONOMICS FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_sports_scanner():
    log.info("[SPORTS] Running sports scan...")
    try:
        sports_path = os.path.join(BOT_DIR, 'sports.py')
        mod = load_module("sports", sports_path)
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning("[SPORTS] sports.py has no run_scan() function")
    except Exception as e:
        err = f"❌ **SPORTS FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_weather_scanner():
    log.info('[WEATHER] Running weather scan...')
    try:
        weather_path = os.path.join(BOT_DIR, 'weather.py')
        mod = reload_module('weather', weather_path)  # reload each time — weather has internal state
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=True)
        else:
            log.warning('[WEATHER] weather.py has no run_scan() function')
    except Exception as e:
        err = f'❌ **WEATHER FAILED**: {e}'
        log.error(err)


def run_congress_scanner():
    log.info("[CONGRESSIONAL] Running congressional scan...")
    try:
        mod = load_module('congressional', os.path.join(BOT_DIR, 'congressional.py'))
        if mod and hasattr(mod, 'run_recent'):
            mod.run_recent(post=False)
        else:
            log.warning('[CONGRESSIONAL] congressional.py has no run_recent() function')
    except Exception as e:
        err = f"❌ **CONGRESSIONAL FAILED**: {e}"
        log.error(err)
        log_to_discord(err)

    # RAG incremental update after each Congressional run
    try:
        import subprocess
        subprocess.Popen(
            ['python3', os.path.join(BOT_DIR, 'rag_ingest.py'), '--update'],
            cwd=os.path.dirname(BOT_DIR)
        )
        log.info('[CONGRESSIONAL] RAG incremental update triggered')
    except Exception as e:
        log.warning(f'[CONGRESSIONAL] RAG update trigger failed: {e}')


def run_whale_scanner():
    log.info("[CRYPTO] Running crypto whale scan...")
    try:
        mod = load_module('crypto', os.path.join(BOT_DIR, 'crypto.py'))
        if mod and hasattr(mod, 'run_scan'):
            mod.run_scan(post=False)
        else:
            log.warning('[CRYPTO] crypto.py has no run_scan() function')
    except Exception as e:
        err = f"❌ **CRYPTO FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_options_check():
    global _last_full_check
    log.info("[OPTIONS] Running options price monitor...")
    try:
        mod = load_module('options', os.path.join(BOT_DIR, 'options.py'))
        if mod:
            # PRIMARY: price monitor every 15 min (market hours only)
            if hasattr(mod, 'run_price_monitor'):
                mod.run_price_monitor(post=True)

            # SECONDARY: full position check every 60 min
            now_ts = time.time()
            if now_ts - _last_full_check >= 3600 and hasattr(mod, 'run_position_check'):
                log.info("[OPTIONS] Running full position check (60-min cadence)...")
                mod.run_position_check(post=True)
                _last_full_check = now_ts
        else:
            log.warning('[OPTIONS] options.py failed to load')
    except Exception as e:
        err = f"❌ **OPTIONS FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_weekly_research():
    log.info("[WEATHER_INTEL] Running weekly deep dive...")
    try:
        mod = load_module('weather_intel', os.path.join(BOT_DIR, 'weather_intel.py'))
        if mod and hasattr(mod, 'run_weekly'):
            mod.run_weekly(post=False)
        else:
            log.warning('[WEATHER_INTEL] weather_intel.py has no run_weekly() function')
    except Exception as e:
        err = f"❌ **WEATHER_INTEL FAILED**: {e}"
        log.error(err)
        log_to_discord(err)


def run_supervisor():
    log.info('[SUPERVISOR] Running heartbeat check...')
    try:
        sup_path = os.path.join(BOT_DIR, 'supervisor.py')
        mod = reload_module('supervisor', sup_path)  # always reload — supervisor is lightweight
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

def get_scanner_lock(name: str):
    if name not in _scanner_locks:
        import threading
        _scanner_locks[name] = threading.Lock()
    return _scanner_locks[name]

def run_eval_report():
    log.info('[EVAL] Generating weekly performance report...')
    try:
        mod = load_module('eval_framework', os.path.join(BOT_DIR, 'eval_framework.py'))
        if mod and hasattr(mod, 'generate_health_report'):
            mod.generate_health_report(post_to_discord=True)
        else:
            log.warning('[EVAL] eval_framework.py missing generate_health_report()')
    except Exception:
        log.exception('[EVAL] Weekly report failed')



def run_btc_watch_scanner():
    """Lightweight BTC price monitor. Triggers crypto scan on momentum conditions."""
    log.info("[BTC_WATCH] Running BTC price check...")
    try:
        donnie_path = os.path.join(BOT_DIR, 'economics.py')
        mod = load_module("economics", donnie_path)
        if mod and hasattr(mod, 'update_btc_price_history'):
            result = mod.update_btc_price_history()
            if result.get('triggered'):
                log.info("[BTC_WATCH] MOMENTUM SIGNAL: %s — triggering immediate Kalshi scan",
                         result.get('reason', ''))
                # Trigger the kalshi scanner immediately
                run_kalshi_scanner()
        else:
            log.warning("[BTC_WATCH] update_btc_price_history not available")
    except Exception as e:
        log.error("[BTC_WATCH] Failed: %s", e)


# Map scanner names to functions
SCANNERS = {
    'kalshi':   run_kalshi_scanner,
    'sports':   run_sports_scanner,
    'congress': run_congress_scanner,
    'whale':    run_whale_scanner,
    'options':  run_options_check,
    'research': run_weekly_research,
    'weather':  run_weather_scanner,
    'supervisor': run_supervisor,
    'eval': run_eval_report,
    'btc_watch': run_btc_watch_scanner,
}


# ── Status command ───────────────────────────────────────────────────────────
def get_status() -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "**🏦 THE FIRM — STATUS REPORT**",
        f"*{now.strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "**Bots:**",
        "  🎰 Economics — Kalshi/Polymarket scanner",
        "  🏛️ Congressional — Congressional & insider tracker",
        "  🐋 Crypto — Crypto whale tracker",
        "  📈 Options — Options coach & position monitor",
        "  🏈 Sports — Sports betting & promo tracker",
        "  🎩 Weather Intel — Weather market alpha research",
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
        "**Slash Commands:**",
        "  `/scan kalshi` `/scan sports` `/scan congress`",
        "  `/scan whale` `/promos` `/research weekly` `/status`",
    ])
    return "\n".join(lines)


# ── Simple HTTP command server (for slash command integration) ───────────────
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

    elif command == 'promos':
        run_sports_scanner()
        return "✅ Sports is scanning promos..."

    elif command == 'research' and args and args[0].lower() == 'weekly':
        threading.Thread(target=run_weekly_research, daemon=True).start()
        return "✅ Weather Intel is analyzing..."

    elif command == 'research' and args and args[0].lower() == 'challenge':
        if len(args) > 1:
            ticker = args[1].upper()
            try:
                sys.path.insert(0, BOT_DIR)
                import weather_intel
                importlib.reload(weather_intel)
                return weather_intel.run_challenge(ticker, post=True)
            except Exception as e:
                return f"❌ Weather Intel error: {e}"
        return "❌ Usage: /research challenge TICKER"

    return f"❌ Unknown command: {command}"


# ── Scheduler loop ───────────────────────────────────────────────────────────
# Fixed clock slots for kalshi economics scanner (UTC)
# Runs at :15 and :45 of every hour — always 15min before/after hour-close markets
KALSHI_FIXED_MINUTES = {15, 45}

def _kalshi_due(now_dt) -> bool:
    """Return True if current UTC minute is a kalshi scheduled slot and not yet run this slot."""
    slot_key = "%s-%02d" % (now_dt.strftime("%Y-%m-%dT%H"), (now_dt.minute // 30) * 30)
    if now_dt.minute not in KALSHI_FIXED_MINUTES:
        return False
    # Only fire once per slot (track by slot key in _last_run)
    last = _last_run.get("kalshi_slot", "")
    return last != slot_key

def scheduler_loop():
    global _running
    log.info("[FIRM] Scheduler started.")
    while _running:
        now = time.time()
        from datetime import datetime as _dt, timezone as _tz
        now_dt = _dt.fromtimestamp(now, tz=_tz.utc)

        for name, interval_minutes in SCHEDULE.items():
            # Economics/kalshi: use fixed clock slots (:15 and :45 UTC)
            if name == "kalshi":
                if _kalshi_due(now_dt):
                    slot_key = "%s-%02d" % (now_dt.strftime("%Y-%m-%dT%H"), (now_dt.minute // 30) * 30)
                    _last_run["kalshi_slot"] = slot_key
                    _last_run[name] = now
                    log.info("[FIRM] [KALSHI] Fixed-slot scan triggered at %s UTC" % now_dt.strftime("%H:%M"))
                    scanner_fn = SCANNERS.get(name)
                    if scanner_fn:
                        lock = get_scanner_lock(name)
                        if lock.locked():
                            log.debug("[FIRM] kalshi scanner still running — skipping slot")
                        else:
                            def _run_kalshi(fn=scanner_fn, lk=lock):
                                with lk:
                                    fn()
                            t = threading.Thread(target=_run_kalshi, daemon=True, name="scanner-kalshi")
                            t.start()
                continue

            # All other scanners: use existing relative interval logic
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
        time.sleep(30)  # check every 30s for better slot precision


# ── Discord gateway (lightweight polling — no discord.py required) ───────────
def discord_set_presence(token: str, status: str, activity: str):
    """Set bot presence via Discord REST (limited without gateway)."""
    # Note: True presence requires Gateway connection (discord.py)
    # This is a lightweight placeholder that logs intent
    log.info(f"[FIRM] Presence set → {activity} ({status})")


def announce_startup():
    """Post startup message to #general."""
    # Load Stratton token from openclaw.json
    stratton_tok = STRATTON_TOKEN
    if not stratton_tok:
        try:
            oc_path = os.path.expanduser('~/.openclaw/openclaw.json')
            with open(oc_path) as f:
                oc = json.load(f)
            stratton_tok = oc.get('channels', {}).get('discord', {}).get('token', '')
        except Exception as e:
            log.error(f"Could not load Stratton token: {e}")

    if not stratton_tok:
        log.error("[FIRM] No Stratton token — skipping startup announcement")
        return

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    message = (
        f"🏦 **THE FIRM IS OPERATIONAL** — {now}\n\n"
        f"All bots are live. Intelligence is flowing.\n\n"
        f"**The Roster:**\n"
        f"🎰 **Economics** — Kalshi scanner + arb + weather markets + regime detector. Every 2 hours.\n"
        f"🏛️ **Congressional** — Congressional trades, Senate Stock Watcher, SEC Form 4 filings. Every 4 hours.\n"
        f"🐋 **Crypto** — Crypto whale tracker. BTC mempool + Whale Alert RSS. Every 30 min.\n"
        f"📈 **Options** — Options position monitor & Discord alert analyzer. Every hour.\n"
        f"🏈 **Sports** — Sports betting lines, promo tracker. Every 2 hours.\n"
        f"🎩 **Weather Intel** — Weekly unconventional alpha deep-dives. Devil's advocate mode available.\n\n"
        f"**Commands:** `/status` | `/scan [kalshi|sports|congress|whale]` | `/promos` | `/research weekly`\n\n"
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
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='The Firm — Master Bot Coordinator')
    parser.add_argument('--no-announce', action='store_true', help='Skip startup Discord announcement')
    parser.add_argument('--scan',        type=str,            help='Run a specific scanner immediately: kalshi|sports|congress|whale|options|research')
    parser.add_argument('--status',      action='store_true', help='Print status and exit')
    parser.add_argument('--command',     type=str,            help='Run a slash command (e.g. "scan kalshi")')
    parser.add_argument('--bot',         type=str,            help='Run a single bot standalone: economics|congressional|crypto|options|sports|research')
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
            'economics': ('kalshi', 'Economics — Kalshi scanner'),
            'congressional': ('congress', 'Congressional — Congressional tracker'),
            'crypto': ('whale', 'Crypto — Crypto whale tracker'),
            'options': ('options', 'Options — Options coach'),
            'sports': ('sports', 'Sports — Sports & promo scanner'),
            'mark': ('research', 'Weather Intel — Research'),
        }
        key = args.bot.lower()
        if key in bot_map:
            scanner_key, label = bot_map[key]
            print(f"\n🚀 Running {label} standalone...\n")
            fn = SCANNERS.get(scanner_key)
            if fn:
                fn()
            else:
                print(f"Scanner '{scanner_key}' not implemented yet.")
        else:
            print(f"Unknown bot: {args.bot}")
            print(f"Options: {', '.join(bot_map.keys())}")
        return

    if args.scan:
        fn = SCANNERS.get(args.scan.lower())
        if fn:
            fn()
        else:
            print(f"Unknown scanner: {args.scan}. Options: {', '.join(SCANNERS.keys())}")
        return

    if args.once:
        log.info("[FIRM] --once mode: running all scheduled scanners (excluding weekly research)")
        for name in ['kalshi', 'sports', 'weather', 'congress', 'whale', 'options', 'supervisor']:
            try:
                log.info(f"[FIRM] Running {name}...")
                SCANNERS[name]()
            except Exception:
                log.exception(f"[FIRM] {name} scan failed")
        log.info("[FIRM] --once complete. Exiting.")
        return

    # Full startup
    log.info("=" * 60)
    log.info("  THE FIRM — Starting up")
    log.info("=" * 60)

    # Verify tokens
    token_check = {
        'DONNIE': DONNIE_TOKEN,
        'RUGRAT': RUGRAT_TOKEN,
        'CHESTER': CHESTER_TOKEN,
        'JORDAN': JORDAN_TOKEN,
        'BRAD': BRAD_TOKEN,
        'MARK_HANNA': MARK_HANNA_TOKEN,
    }
    for name, tok in token_check.items():
        if tok:
            log.info(f"  ✅ {name} token loaded")
        else:
            log.warning(f"  ⚠️ {name} token MISSING — bot will be limited")

    # Startup announcement
    if not args.no_announce:
        announce_startup()

    # Run initial scans — set timestamps FIRST to prevent scheduler double-fire
    log.info("[FIRM] Running initial scans on startup...")
    now_ts = time.time()
    for name in list(SCHEDULE.keys()):
        _last_run[name] = now_ts  # pre-stamp all to prevent immediate re-fire
    for name in ['kalshi', 'sports', 'weather']:
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
