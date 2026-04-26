#!/usr/bin/env python3
"""
shared_context.py — Shared agent state for The Firm
Provides write_agent_status() for all bots to log their last run + metrics.
State persisted atomically to /home/cody/stratton/data/shared_state.json
"""

import os
import json
import time
import fcntl

SHARED_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'shared_state.json')


def _load_state() -> dict:
    try:
        if os.path.exists(SHARED_STATE_FILE):
            with open(SHARED_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"agent_status": {}}


def _save_state(state: dict):
    """Atomic write: write to .tmp then rename."""
    try:
        os.makedirs(os.path.dirname(SHARED_STATE_FILE), exist_ok=True)
        tmp_path = SHARED_STATE_FILE + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(state, f, indent=2)
        os.rename(tmp_path, SHARED_STATE_FILE)
    except Exception as e:
        import sys
        print(f"[SHARED_CONTEXT] Save error: {e}", file=sys.stderr)


def write_agent_status(agent_name: str, status_dict: dict):
    """Thread-safe atomic write via flock + rename."""
    lock_path = SHARED_STATE_FILE + '.lock'
    try:
        os.makedirs(os.path.dirname(SHARED_STATE_FILE), exist_ok=True)
        with open(lock_path, 'w') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)  # exclusive lock
            state = _load_state()
            if 'agent_status' not in state:
                state['agent_status'] = {}

            now = time.time()
            entry = {
                'last_run': _iso_now(),
                'last_run_ts': now,
            }
            entry.update(status_dict)
            state['agent_status'][agent_name] = entry
            _save_state(state)
            # lock released when 'with' block exits
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'[shared_context] write_agent_status failed for {agent_name}: {e}')


def read_agent_status(agent_name: str = None) -> dict:
    """Read status for one agent, or all agents if agent_name is None."""
    state = _load_state()
    statuses = state.get('agent_status', {})
    if agent_name:
        return statuses.get(agent_name, {})
    return statuses


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


if __name__ == '__main__':
    # Quick smoke test
    write_agent_status('test_agent', {'test': True, 'value': 42})
    print(read_agent_status())
