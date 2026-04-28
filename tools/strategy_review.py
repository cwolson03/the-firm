#!/usr/bin/env python3
"""
Stratton Oakmont — Weather Strategy Experiment Review
Usage: python3 strategy_review.py

Reads /home/cody/stratton/data/weather_experiments.json
Outputs per-strategy win rate, avg edge, P&L summary.
"""
import json, os
from collections import defaultdict

FILE = '/home/cody/stratton/data/weather_experiments.json'

if not os.path.exists(FILE):
    print("No experiments file yet.")
    exit()

with open(FILE) as f:
    data = json.load(f)

print(f"Total signals logged: {len(data)}")
print(f"Open: {sum(1 for d in data if d['status']=='OPEN')}")
print(f"Resolved: {sum(1 for d in data if d['status'] in ('WIN','LOSS'))}")
print()

strategies = defaultdict(lambda: {'signals': 0, 'open': 0, 'wins': 0, 'losses': 0,
                                   'total_edge': 0.0, 'cities': set()})

for entry in data:
    for s in entry.get('strategies', []):
        st = strategies[s]
        st['signals'] += 1
        st['total_edge'] += entry['edge']
        st['cities'].add(entry['city_name'])
        if entry['status'] == 'OPEN':
            st['open'] += 1
        elif entry['status'] == 'WIN':
            st['wins'] += 1
        elif entry['status'] == 'LOSS':
            st['losses'] += 1

print(f"{'Strategy':<28} {'Signals':>7} {'Open':>6} {'Wins':>6} {'Losses':>7} {'Hit%':>6} {'AvgEdge':>8} {'Cities':>7}")
print("-" * 85)

for name in sorted(strategies.keys()):
    st = strategies[name]
    resolved = st['wins'] + st['losses']
    hit_pct = (st['wins'] / resolved * 100) if resolved > 0 else 0.0
    avg_edge = st['total_edge'] / st['signals'] if st['signals'] > 0 else 0.0
    print(f"{name:<28} {st['signals']:>7} {st['open']:>6} {st['wins']:>6} {st['losses']:>7} "
          f"{hit_pct:>5.1f}% {avg_edge*100:>7.1f}¢ {len(st['cities']):>7}")

print()
print("Strategy definitions:")
print("  A_conservative_15c  — 15c+ edge, all 19 cities (matches live threshold)")
print("  B_aggressive_10c    — 10c+ edge, all cities (lower bar, more signals)")
print("  C_thin_city_15c     — 15c+, OKC/NOLA/MIN/SATX/SEA/ATL only")
print("  D_high_conviction_25c — 25c+, any city (ultra-selective)")
print("  E_time_gated_15c    — 15c+, within 90min of model update (00:30/06:30/12:30/18:30 UTC)")
