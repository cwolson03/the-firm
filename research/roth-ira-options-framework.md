# Roth IRA Options Framework
**Author:** Stratton  
**Date:** April 7, 2026  
**Target:** Cody — E*TRADE Roth IRA  
**Purpose:** Systematic approach to maximizing tax-free compounding via options

---

## WHY THE ROTH IRA IS YOUR BIGGEST EDGE

Every dollar of premium you collect, every spread you close for profit, every long call that 10x's — **zero federal tax. Ever.** That's the actual edge. Not the options strategy. The wrapper.

The math: $10,000 in a taxable account compounding at 20%/year for 30 years = ~$2.37M. Then you pay long-term cap gains on most of it. Same $10,000 in a Roth at 20%/year = $2.37M, **all yours**. No tax drag on reinvested premium, no wash sale rules forcing you to hold losers, no harvesting games.

The Roth is your best account. Treat it like it is.

---

## ROTH IRA CONSTRAINTS (E*TRADE SPECIFIC)

Before the framework, know the rules:

**What's allowed in most Roth IRAs:**
- Long calls and puts (level 2 options)
- Covered calls (level 1)
- Cash-secured puts (level 1-2)
- Spreads: verticals, calendars, diagonals (level 3 at E*TRADE — requires approval)
- Iron condors (level 3)

**What's NOT allowed:**
- Naked puts/calls on individual stocks (margin required — IRAs can't hold debit margin)
- Selling calls without owning underlying (unless spread defined)
- Portfolio margin (IRAs only get cash/reg-T)
- Borrowing / leverage beyond the cash in the account

**Get Level 3 options approval at E*TRADE.** It unlocks spreads. Do it now if you haven't. You'll need to demonstrate knowledge of options strategies. Without spreads, your Roth options toolkit is cut in half.

---

## STRATEGY SELECTION CRITERIA

### The Roth Suitability Test

Ask three questions before any play:

1. **Is this defined risk?** — If you can lose more than you put in, you can't do it in an IRA
2. **Does it generate premium or capture a high-probability move?** — The Roth compounds best when you're collecting premium or making directional bets with asymmetric upside
3. **Is the underlying liquid enough?** — Illiquid options = wide spreads = you're donating money to market makers

### Tier 1: High-Priority Roth Strategies

**Cash-Secured Puts (CSPs)**
- Sell a put at a strike you'd *want* to own the stock at
- Collect premium upfront (all tax-free in Roth)
- If assigned: you own the stock at your target price
- **Best use:** On pullbacks of quality names (NVDA, RTX, AAPL) when IV is elevated
- **Current opportunity:** Tariff selloff elevated IV — collect premium on dips

**Covered Calls (CC)**
- Own 100 shares, sell a call above current price
- Collect premium month after month — all tax-free
- **Best use:** On positions you already hold in the Roth that are range-bound or at resistance
- **Current opportunity:** If you hold BTC-correlated names or defense stocks near highs

**Vertical Spreads (Bull Put, Bear Call, Bull Call, Bear Put)**
- Defined risk, defined reward
- Perfect for directional bets without the capital requirement of 100 shares
- **Bull put spread:** Sell put + buy lower put. Credit received. Profit if stock stays above your short strike.
- **Bull call spread:** Buy call + sell higher call. Debit paid. Profit if stock rallies to your long strike.
- **Best use:** When you have a directional thesis but IV is high (use credit spreads to be a vol seller) or you want limited capital deployment

**Poor Man's Covered Call (PMCC) / Diagonal**
- Buy a deep ITM LEAPS call (6-12 months out)
- Sell near-term OTM calls against it each month
- Synthetic stock position for much less capital
- **Best use:** High-priced stocks you can't afford 100 shares of in the Roth (NVDA, etc.)
- **Current opportunity:** NVDA LEAPS + monthly CC is the play on the AI thesis with limited capital

**Iron Condors**
- Sell OTM call spread + sell OTM put spread
- Collect premium for the stock staying range-bound
- **Best use:** On earnings you expect to be boring, or on high-IV names in sideways tape
- **Current opportunity:** SPY/QQQ iron condors during FOMC no-event periods

### Tier 2: Situational Strategies

**Long Calls (Directional)**
- Pay debit, all you can lose is the premium
- Use sparingly — pure premium decay plays against you
- **When to use:** High-conviction directional catalyst plays (earnings, FDA, data releases)
- **Sizing:** Never more than 3-5% of Roth on a single long option

**Long Puts (Hedging)**
- Buy downside protection on your largest Roth positions
- Especially useful before major macro events
- **Current opportunity:** SPY puts as a hedge going into April 10 CPI

**LEAPS (Long-dated calls, 12-24 months)**
- Time is your friend in the Roth — LEAPS benefit from compounding
- Buy LEAPS on your highest conviction multi-year theses
- Roll them annually, capturing appreciation tax-free

---

## POSITION SIZING RULES

### The Roth Size Matrix

| Strategy | Max % of Roth Per Trade | Notes |
|----------|------------------------|-------|
| CSP (cash-secured) | 15-20% | Capped by cash you'd need for assignment |
| Covered Call | N/A (covers existing position) | Just don't sell too many |
| Bull/Bear Spread | 5-8% | Defined risk, can size up |
| Iron Condor | 8-12% | Never let max loss be > 10% |
| Long Call / Put | 2-4% | Pure lottery ticket — small size |
| LEAPS | 10-15% | Long time horizon, size accordingly |
| PMCC Diagonal | 10-20% | Treat like owning stock |

### The "Never Risk the Account" Rule
- No single position should be able to blow up > 15% of your Roth
- Keep 20-30% in cash/short-term bonds for assignment risk on CSPs and new opportunities
- If you're ever doing math on "what if this goes to zero" and the answer is more than 20% of your account, you're too big

### Kelly-ish Sizing for Defined Risk
For spreads: `Size = (Account * 0.05) / Max_Loss_Per_Contract`
- Example: $20,000 Roth, bear call spread with $200 max loss → size = ($20,000 * 0.05) / $200 = 5 contracts

---

## ENTRY CRITERIA

**Don't enter without checking all five:**

1. **Implied Volatility Rank (IVR) > 30** for premium-selling strategies (CSPs, CC, condors)
   - High IV = fat premium = you're getting paid to take risk
   - Low IVR = cheap options = buy premium, don't sell it

2. **Defined thesis** — you know *why* this trade makes money. If the answer is "I dunno, it looks good," don't enter.

3. **Minimum 30 DTE** for premium-selling strategies — time decay accelerates inside 30 days, but you want buffer to manage

4. **Bid/ask spread on the option < 10% of mid** — if the spread is wider than 10%, liquidity is garbage and you're paying the market maker to trade

5. **Underlying is liquid** — 1M+ average daily shares, options OI > 1,000 on your strike

**Best entry conditions:**
- Market just sold off (elevated IV, stocks at support)
- Pre-earnings on a quality name you want to own anyway (CSP)
- VIX spike (great for selling spreads)
- Clear technical level for the short strike (don't sell into thin air)

---

## EXIT CRITERIA

**The rules:**

**For premium sellers (CSPs, CCs, condors):**
- Take profit at **50-60% of max premium collected** — don't wait for full expiration
  - Example: sold a put for $2.00 → buy it back at $0.80-$1.00
  - Why: 50% of max profit for 50% of the time = way better risk-adjusted return
- Stop loss: if position goes against you and you're at **2x the premium received**, close it
  - Sold for $2.00 → close if it costs $4.00 to buy back
  - Don't let losers run in a Roth — capital preservation matters

**For long options / spreads (directional):**
- Take profit at **100-150% gain** on long options — resist greed
- Stop loss at **-50%** on debit paid — cut losers fast
- Exit **7-10 days before expiration** on spreads — pin risk and assignment risk spike in the last week

**The cardinal rule:** There is no "waiting for it to come back" in the Roth. You're not paying taxes on losses anyway, so there's zero reason to hold a loser. Cut it, redeploy the capital.

---

## ROLLING RULES

Rolling = closing the current position and opening a new one at a better strike/date.

**Roll when:**
- Your short strike is being tested (within 5-7% of price on a CSP or CC)
- You want to extend duration to collect more premium
- You want to take a profit early and re-open at a new strike

**Rolling CSPs (down and out — when threatened):**
- If price falls and your short put is ITM: roll down (lower strike) AND out in time (add 30 days)
- Only roll for a credit or break-even — never pay a debit to roll (you're buying yourself time, not digging a hole)
- If you can't roll for a credit, consider taking assignment and then selling covered calls

**Rolling CCs (up and out — when stock runs past your strike):**
- If stock blows through your short call: roll up (higher strike) AND out in time
- Take the small loss on the call, reopen at a strike that gives you upside participation
- Don't cap your winners in a Roth — you're not paying taxes, so let quality names run

**Never roll into:**
- Earnings (assignment risk + IV crush can destroy you)
- Expiration week (no room to manage)
- A losing position just to "avoid taking a loss" — that's how small losers become account blowups

---

## WHAT TO AVOID AND WHY

| What | Why You Don't Do It |
|------|---------------------|
| **Naked short puts/calls** | Margin required — not allowed in IRAs |
| **Weekly expiration plays (0-7 DTE)** | Gambling, not trading. Delta hedging + assignment risk in a Roth is brutal |
| **Earnings long straddles on cheap stocks** | IV crush is your enemy. Unless IV is historically depressed pre-earnings |
| **Chasing high premium on junk stocks** | Biotech, penny stocks, meme names — the "premium" is there because the market is pricing real blow-up risk |
| **Overlapping positions in same sector** | If you have 3 CSPs in semiconductors and sector gets hit, you're getting assigned 3x |
| **Selling spreads with < $1 credit on $5 wide** | 20% max gain, 80% max loss = terrible risk/reward |
| **Ignoring earnings dates** | Know when earnings are before you sell any short option. Non-negotiable. |
| **Assignment on dividend-paying stocks just before ex-div** | Early assignment risk spikes — close before ex-dividend date or risk it |

---

## CONCRETE EXAMPLE PLAYS (April 2026 Context)

### Play 1: NVDA CSP — Selling the Tariff Fear
**Setup:** NVDA has sold off on broad tariff panic. IV is elevated (IVR elevated on selloff).
- Sell the NVDA $750 put, expiring ~45 days out (mid-May 2026)
- Collect ~$20-25 premium per contract
- Breakeven at ~$725-730 — that's NVDA at a 15-20% further discount from current levels
- Max profit: keep the premium if NVDA stays above $750
- Assignment: you own NVDA at $725-730, which is fine because your thesis is AI infrastructure

**Why the Roth is perfect:** You collect the premium tax-free. If assigned, you own NVDA tax-free and start selling covered calls.

---

### Play 2: RTX Bull Put Spread — Defense on the Dip
**Setup:** RTX pulled back with the market despite strong defense fundamentals.
- Sell RTX $115 put / Buy RTX $110 put, expiring 30-45 DTE
- Collect ~$1.50-2.00 credit on a $5 spread ($150-200 per contract)
- Max risk: $300-350 per contract (the difference minus premium)
- Win if RTX stays above $115

**Why the spread:** Defined risk, lower capital requirement than a CSP. Perfect for Roth where you can't margin.

---

### Play 3: BTC LEAPS via MSTR or IBIT
**Setup:** BTC at $68-70K, cycle not done, but no BTC options in most Roth IRAs.
- Buy MSTR (MicroStrategy) or IBIT (BlackRock Bitcoin ETF) LEAPS calls, Jan 2027 expiration
- Deep ITM (80-85 delta) — acts like owning shares at a discount
- Capture BTC's potential move to $100-120K over 6-9 months, all tax-free

**Why LEAPS:** No theta decay from owning LEAPS deep ITM. You're buying time and leverage on the BTC thesis.

---

### Play 4: SPY Iron Condor — The FOMC Vol Collector
**Setup:** FOMC on April 30, expected to hold. IV elevated into the meeting.
- Sell SPY $490 call / Buy $500 call
- Sell SPY $450 put / Buy $440 put
- 10 DTE around FOMC date (open 2-3 weeks before)
- Collect ~$2.50-4.00 total premium
- Win if SPY stays in the $450-$490 range through FOMC

**Why now:** Fed holds = muted reaction. IV crush after the non-event = you pocket premium. Classic vol-selling setup around "nothing happens" FOMC meetings.

---

### Play 5: PMCC on NVDA — Long AI Thesis, Monthly Income
**Setup:** NVDA at ~$850.
- Buy the NVDA Jan 2027 $700 call (deep ITM, ~85 delta) for ~$250 premium
- Each month, sell the 30-45 DTE OTM call at ~$950-1000 strike for ~$25-35
- Monthly income covers the LEAPS decay, net cost basis reduces over time

**Why the Roth:** Over 12 months of selling calls tax-free, you've recovered $300-400 in premium on a $250 investment. The Roth compounds this without tax drag.

---

## HOW TO MAXIMIZE TAX-FREE COMPOUNDING

### The Roth Compounding Playbook:

1. **Reinvest every dollar of premium immediately** — premium sitting in cash earns nothing. Use it to fund the next position within a week. The Roth compounds fastest when capital is always working.

2. **Prioritize high-IVR environments** — in low-vol markets, don't sell premium. Buy LEAPS instead. Let the market come to you. When vol spikes (tariff news, FOMC surprises), that's when you sell.

3. **Layer positions across time** — don't put everything in the same expiration. 30 DTE, 45 DTE, and 60 DTE positions mean you're always rolling something and always collecting something.

4. **Let winners ride on LEAPS** — if your LEAPS double, consider holding. There's no short-term cap gains on LEAPS in the Roth. Let the AI or BTC thesis play out without the tax clock forcing your hand.

5. **Use "wheel strategy" tactically** — on quality stocks you want to own:
   - Sell CSP → get assigned → sell covered calls → get called away → sell CSP again
   - Each loop generates premium that stays in the Roth tax-free
   - Works best on stocks in a range with high IV (like NVDA or RTX post-pullback)

6. **Annual contribution maximization** — 2026 contribution limit: $7,000 ($8,000 if 35+). Contribute in January, not December. Every month of compounding counts.

7. **Never take cash out of the Roth** — the penalty kills the compounding. Treat it as untouchable until 59½.

8. **Track cost basis on spreads** — E*TRADE's Roth tracking can be confusing on spreads. Document your trades. Know what you put in vs. what you're getting back for tax reporting on rollovers/conversions.

---

## THE ONE-PAGE CHEAT SHEET

```
ROTH IRA OPTIONS — QUICK RULES

IV HIGH (>50th percentile): SELL premium (CSPs, CCs, spreads, condors)
IV LOW (<30th percentile): BUY premium (LEAPS, long calls on catalysts)

ENTRY: 30-45 DTE | IVR >30 | Defined risk | Liquid underlier
EXIT: 50-60% profit on credit trades | -50% on debits | No expiration hold

SIZE: Never >15% on one trade | Keep 20-30% cash | Kelly-ish on spreads

ROLL: For a credit or don't | Never into earnings | Never expiration week

AVOID: Weeklies | Junk stock premium | Overlapping sector risk | Margin

COMPOUNDING: Reinvest premium immediately | Wheel quality names | Let LEAPS run
```

---

*Framework version 1.0 — Review after completing first month of actual Roth options trading*
