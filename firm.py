#!/usr/bin/env python3
"""
firm.py — Entry point for The Firm.

The full orchestrator lives in bots/firm.py. This shim makes it runnable
from the project root as documented: python3 firm.py [args]
"""
import sys
import os

# Add bots/ to path so bots.firm imports work cleanly
bots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bots')
sys.path.insert(0, bots_dir)

from firm import main  # noqa: E402

if __name__ == '__main__':
    main()
