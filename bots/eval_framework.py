#!/usr/bin/env python3
"""
eval_framework.py — Post-resolution trade evaluation for The Firm
Scores decision quality, identifies patterns, generates system health reports.
Runs after market close; called from firm.py weekly or on-demand.

eval_store.json schema:
{
  "trade_id": str,
  "agent": str,              # economics / congressional / options / sports
  "market": str,
  "direction": str,
  "entry_date": str,
  "entry_edge_pct": float,
  "llm_confidence_at_entry": str,
  "outcome": str,            # WIN / LOSS / EXPIRED / PENDING
  "pnl_pct": float,
  "resolved_date": str,
  "llm_eval": {
    "process_score": int,    # 1-10: was reasoning sound regardless of outcome?
    "edge_quality": str,     # "strong" / "marginal" / "weak"
    "what_worked": str,
    "what_to_improve": str,
    "lesson": str,
    "avoid_next_time": str
  },
  "raw_thesis": str,
  "raw_llm_reason": str
}
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger('EVAL')

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_FILE   = os.path.join(BASE_DIR, 'data', 'eval_store.json')
TOKENS_FILE = os.path.join(BASE_DIR, 'config', 'bot-tokens.env')

try:
    from dotenv import load_dotenv
    load_dotenv(TOKENS_FILE)
except Exception:
    pass


# ── Storage ───────────────────────────────────────────────────────────────────

def load_evals() -> list:
    try:
        if os.path.exists(EVAL_FILE):
            with open(EVAL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_evals(evals: list):
    os.makedirs(os.path.dirname(EVAL_FILE), exist_ok=True)
    tmp = EVAL_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(evals, f, indent=2)
    os.replace(tmp, EVAL_FILE)


def log_trade_entry(trade_id: str, agent: str, market: str, direction: str,
                    entry_edge_pct: float, llm_confidence: str = '',
                    raw_thesis: str = '', raw_llm_reason: str = '') -> dict:
    """Log a trade at entry time. Returns the entry record."""
    record = {
        'trade_id': trade_id,
        'agent': agent,
        'market': market,
        'direction': direction,
        'entry_date': datetime.now(timezone.utc).isoformat(),
        'entry_edge_pct': entry_edge_pct,
        'llm_confidence_at_entry': llm_confidence,
        'outcome': 'PENDING',
        'pnl_pct': 0.0,
        'resolved_date': '',
        'llm_eval': {},
        'raw_thesis': raw_thesis[:500],
        'raw_llm_reason': raw_llm_reason[:500],
    }
    evals = load_evals()
    evals = [e for e in evals if e['trade_id'] != trade_id]  # replace if exists
    evals.append(record)
    save_evals(evals)
    return record


def resolve_trade(trade_id: str, outcome: str, pnl_pct: float) -> Optional[dict]:
    """Mark a trade as resolved and trigger LLM eval."""
    evals = load_evals()
    record = next((e for e in evals if e['trade_id'] == trade_id), None)
    if not record:
        log.warning(f'[EVAL] Trade {trade_id} not found in eval store')
        return None
    record['outcome'] = outcome.upper()
    record['pnl_pct'] = pnl_pct
    record['resolved_date'] = datetime.now(timezone.utc).isoformat()
    # Run LLM eval
    record['llm_eval'] = _run_llm_eval(record)
    save_evals(evals)
    return record


# ── LLM Evaluation ────────────────────────────────────────────────────────────

def _eval_prompt(record: dict) -> str:
    outcome_word = 'won' if record['outcome'] == 'WIN' else 'lost' if record['outcome'] == 'LOSS' else 'expired'
    return f"""You are evaluating the quality of a prediction market trade decision.

Trade: {record['market']}
Agent: {record['agent']}
Direction: {record['direction']}
Entry edge: {record['entry_edge_pct']:.1f}%
LLM confidence at entry: {record['llm_confidence_at_entry'] or 'not recorded'}
Outcome: {record['outcome']} ({outcome_word}, P&L: {record['pnl_pct']:+.1f}%)
Entry thesis: {record['raw_thesis'] or 'not recorded'}
LLM reasoning at entry: {record['raw_llm_reason'] or 'not recorded'}

Evaluate the PROCESS quality (1-10), not just the outcome. A good process can lose; a bad process can win.

Return JSON only:
{{
  "process_score": <1-10>,
  "edge_quality": "<strong|marginal|weak>",
  "what_worked": "<what was good about the reasoning>",
  "what_to_improve": "<specific improvement for next similar trade>",
  "lesson": "<one-line actionable lesson>",
  "avoid_next_time": "<specific pattern or condition to avoid>"
}}"""


def _run_llm_eval(record: dict) -> dict:
    """Run LLM evaluation on a resolved trade. Uses Claude if available, else Grok."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from llm_client import query_claude, query_grok
        import json as _json

        prompt = _eval_prompt(record)

        # Try Claude first (better structured reasoning)
        result = query_claude(prompt, system="You are a quantitative trading analyst. Return only valid JSON.")
        if not result.get('ok'):
            result = query_grok(prompt)

        content = result.get('content', '')

        # Parse JSON from response
        try:
            # Extract JSON block if wrapped in markdown
            if '```' in content:
                content = content.split('```')[1]
                if content.startswith('json'):
                    content = content[4:]
            return _json.loads(content.strip())
        except Exception:
            # Fallback: return raw content as lesson
            return {
                'process_score': 5,
                'edge_quality': 'unknown',
                'what_worked': '',
                'what_to_improve': '',
                'lesson': content[:200] if content else 'eval failed',
                'avoid_next_time': ''
            }
    except Exception as e:
        log.warning(f'[EVAL] LLM eval failed: {e}')
        return {'process_score': 0, 'lesson': f'eval error: {e}'}


# ── System Health Report ──────────────────────────────────────────────────────

def generate_health_report(post_to_discord: bool = False) -> str:
    """Weekly system health report — LLM synthesizes patterns across all resolved trades."""
    evals = load_evals()
    resolved = [e for e in evals if e['outcome'] in ('WIN', 'LOSS')]

    if not resolved:
        return "No resolved trades yet to evaluate."

    wins = [e for e in resolved if e['outcome'] == 'WIN']
    win_rate = len(wins) / len(resolved) * 100 if resolved else 0
    avg_pnl = sum(e['pnl_pct'] for e in resolved) / len(resolved) if resolved else 0
    avg_process = sum(e['llm_eval'].get('process_score', 5) for e in resolved) / len(resolved) if resolved else 0

    # Collect lessons
    lessons = [e['llm_eval'].get('lesson', '') for e in resolved if e['llm_eval'].get('lesson')]
    avoids = [e['llm_eval'].get('avoid_next_time', '') for e in resolved if e['llm_eval'].get('avoid_next_time')]

    # LLM synthesis of patterns
    pattern_analysis = ''
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from llm_client import query_claude, query_grok
        summary_prompt = f"""Analyze these trading lessons and identify the top 3 actionable patterns:

Lessons from {len(resolved)} resolved trades:
{chr(10).join(f'- {l}' for l in lessons[:20])}

Things to avoid:
{chr(10).join(f'- {a}' for a in avoids[:10])}

Identify: 1) The strongest systematic edge, 2) The most common failure mode, 3) One specific rule to add.
Keep response under 200 words."""
        result = query_claude(summary_prompt)
        if not result.get('ok'):
            result = query_grok(summary_prompt)
        pattern_analysis = result.get('content', '')[:400]
    except Exception as e:
        pattern_analysis = f'Pattern analysis unavailable: {e}'

    # Format report
    lines = [
        "**📊 THE FIRM — WEEKLY EVAL REPORT**",
        f"*{datetime.now(timezone.utc).strftime('%Y-%m-%d')}*",
        "",
        f"**Resolved Trades:** {len(resolved)} | **Win Rate:** {win_rate:.1f}%",
        f"**Avg P&L:** {avg_pnl:+.1f}% | **Avg Process Score:** {avg_process:.1f}/10",
        "",
        "**By Agent:**",
    ]
    for agent in sorted(set(e['agent'] for e in resolved)):
        agent_trades = [e for e in resolved if e['agent'] == agent]
        agent_wins = [e for e in agent_trades if e['outcome'] == 'WIN']
        wr = len(agent_wins) / len(agent_trades) * 100
        lines.append(f"  {agent}: {len(agent_trades)} trades, {wr:.0f}% win rate")
    lines.extend([
        "",
        "**🧠 Pattern Analysis:**",
        pattern_analysis,
        "",
        "**Recent Lessons:**",
    ])
    for lesson in lessons[-5:]:
        lines.append(f"  • {lesson}")
    lines.append("\n*Eval Framework | The Firm*")

    report = "\n".join(lines)

    if post_to_discord:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import requests
            from dotenv import load_dotenv
            import os as _os
            load_dotenv(TOKENS_FILE)
            token = _os.getenv('JORDAN_TOKEN', '') or _os.getenv('STRATTON_TOKEN', '')
            CHANNEL_BOT_LOGS = 1491861993022554284
            if token:
                requests.post(
                    f"https://discord.com/api/v10/channels/{CHANNEL_BOT_LOGS}/messages",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"content": report[:1990]},
                    timeout=10
                )
        except Exception as e:
            log.warning(f'[EVAL] Discord post failed: {e}')

    return report


# ── Pending trade sync (called by Donnie after resolution) ────────────────────

def sync_donnie_positions():
    """Pull resolved Kalshi positions and update eval store."""
    # Donnie calls resolve_trade() directly after position resolution
    pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Eval Framework — The Firm')
    parser.add_argument('--report', action='store_true', help='Generate weekly health report')
    parser.add_argument('--list', action='store_true', help='List all eval records')
    parser.add_argument('--resolve', nargs=3, metavar=('TRADE_ID', 'OUTCOME', 'PNL_PCT'),
                        help='Resolve a trade: TRADE_ID WIN 45.2')
    parser.add_argument('--log', nargs=4, metavar=('TRADE_ID', 'AGENT', 'MARKET', 'EDGE_PCT'),
                        help='Log a trade at entry')
    parser.add_argument('--post', action='store_true', help='Post report to Discord')
    args = parser.parse_args()

    if args.report:
        report = generate_health_report(post_to_discord=args.post)
        print(report)
    elif args.list:
        evals = load_evals()
        print(f"Total eval records: {len(evals)}")
        for e in evals[-10:]:
            print(f"  {e['trade_id']} | {e['agent']} | {e['outcome']} | {e['pnl_pct']:+.1f}% | process: {e['llm_eval'].get('process_score', '?')}/10")
    elif args.resolve:
        trade_id, outcome, pnl = args.resolve
        record = resolve_trade(trade_id, outcome, float(pnl))
        if record:
            print(f"Resolved: {trade_id} | {outcome} | {pnl}%")
            print(f"LLM Eval: {json.dumps(record['llm_eval'], indent=2)}")
        else:
            print(f"Trade {trade_id} not found")
    elif args.log:
        trade_id, agent, market, edge = args.log
        record = log_trade_entry(trade_id, agent, market, 'YES', float(edge))
        print(f"Logged: {record['trade_id']}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
