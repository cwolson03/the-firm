# DECISIONS.md — Engineering Decisions & Lessons

A running log of significant decisions, post-mortems, and hard lessons.
Each entry answers: what happened, what we changed, why.

---

## 2026-04-10 — CPI Shelter trade: the sizing miss

**What happened:** CPI shelter printed above 4.24% — Donnie had modeled 40% probability vs Kalshi's implied 15%. Edge was +25 points. The trade resolved YES, +275% return. The position was sized at ~$45.

**What was wrong:** At +25 points of edge on an ECONOMIC_DATA market, the position sizing formula warranted $100-150. We left significant PnL on the table.

**Decision:** Updated sizing notes. High-conviction ECONOMIC_DATA plays with ≥20pt edge should be sized at $100-150, not the conservative default. The quant model is calibrated — when it says +25 points, trust it.

---

## 2026-04-01 — BTC intraday loss: the "right direction, wrong timing" problem

**What happened:** Entered a NO position on a BTC daily range market 9 minutes before close, with spot price only $49 (0.05%) from the threshold. The model's directional call was correct — but the position resolved against us. Spot moved across the threshold in the final minutes.

**Root cause:** Two separate failures:
1. Insufficient time-to-close buffer — 9 minutes is too short for a crypto range market with high volatility
2. Spot too close to threshold — 0.05% buffer means any normal volatility prints across the line

**Decision:** Added two mandatory hard gates to `economics.py`:
```python
CRYPTO_MIN_MINUTES_TO_CLOSE = 30   # no crypto/commodity entry inside 30 min
CRYPTO_MIN_BUFFER_PCT = 0.005       # spot must be ≥0.5% from threshold
```
These aren't suggestions — they're execution blockers. The quant model can show positive edge, but if either condition fails, no trade.

**Lesson:** For range markets with binary resolution, proximity to threshold matters as much as direction. A correct model + bad timing = loss.

---

## 2026-04-18 — Weather scanner: pre/post tuning calibration

**What happened:** Analyzed paper trade results from Apr 16-18 (the first 3 days of running). Win rate: 5.6% on Apr 16, 21.8% on Apr 17, 29.5% on Apr 18. The bot was placing too many bids, edge thresholds were too loose, city-specific bias wasn't calibrated.

**Decision:** Raised edge threshold from initial setting to 28% minimum. Added per-city bias calibration (rolling 7-day correction from resolved trades, activates after ≥3 samples). Added multi-model consensus gate (LOW agreement = skip signal).

**Result:** Post-tuning win rate (Apr 19+): 45.2% on 529 resolved trades. The pre-tuning data is preserved in the dataset but excluded from reported win rates.

---

## 2026-04-25 — Module reload state loss: the fill detection bug

**What happened:** Senior engineer code review identified that `firm.py` was calling `load_module('donnie_v2', path)` every 2 hours, which re-executes the full module file and resets all globals — including `_known_resting` (the dict that tracks outstanding orders for fill detection). Fill detection relies on comparing current resting orders vs. previous scan. Resetting the dict every 2 hours means Donnie loses memory of what orders it placed.

**Decision:** Added `_save_donnie_state()` / `_load_donnie_state()` to persist `_known_resting` and `last_tier2_snapshot` to `data/donnie_state.json` at end of every scan, reload on module load.

**Secondary fix:** Added `_module_cache` dict to `firm.py` — modules are loaded once and reused, eliminating the reload entirely for stateless agents (weather, supervisor, etc.).

---

## 2026-04-25 — Concurrent write race condition in shared_context.py

**What happened:** Same code review identified that `write_agent_status()` was doing read-modify-write without any locking. Weather runs every 3 minutes, supervisor runs every 30 minutes — they will occasionally overlap. Last writer wins, silently discarding the other's update.

**Decision:** Added `fcntl.flock(LOCK_EX)` around the read-modify-write sequence. Lock acquired via a `.lock` file, automatically released when the `with` block exits. POSIX atomic rename already in place for the write itself.

---

## 2026-04-21 — Directional edge gate: the payout vs. model edge distinction

**Comment preserved in economics.py:**
```
# ── FIX (Apr 21): Use directional model edge, not payout edge ─────────────
# Payout edge ((1-price) or price) can be positive even when the model says
# the trade has negative directional edge.
```
Payout edge = "how much can I make if I'm right." Directional edge = "does my model think the true probability is higher or lower than the market price." Without this fix, any YES position priced under 82¢ would pass the 18¢ edge gate regardless of model direction. The fix enforces that the model's probability must be in the same direction as the trade.

---

*This file grows with the system. Every significant change gets an entry.*
