# MM×Skew 1h Strategy — FROZEN SPEC

**v1.1 — FROZEN 2026-07-19.** In-sample only (28.4 days). Forward-test pending. Do not edit the rules below
unless explicitly told. Reference backtest: `study/mm_skew_poc.py`.

**Changelog**
- **v1.1** (2026-07-19): added the LONG **volume-delta filter** (`delta < +15%`) — buy-exhaustion cut,
  the only OOS-validated feature filter of delta/Mov.Magnitude/skew-mag/velocity. Short side left unfiltered
  (its delta filter looked good in-sample but failed out-of-sample — H2-only mirage).
- **v1.0**: base rule (direction + skew + panel-2 spread ±35 + POC-baseline), no delta filter.

## Timeframe
1h constant-volume buckets (SOLUSDT). All features are CAUSAL (known at the signal candle's close).

## Feature definitions
- **Direction** — bull = close>open, bear = close<open (the *Mov.Magnitude* colour: green/red).
- **Skew** — volume-weighted skewness of the bucket's volume-by-price profile, profile-read sign
  (`app.footprint_panel.profile_skewness`): >0 = volume mass at HIGHER prices, <0 = at LOWER prices.
  (>0 ⇒ *Skew* green/cyan ⇒ *MM×Skew* green; <0 ⇒ red/magenta.)
- **Panel-2 non-locked spread** — first-print eff-agg lean, `(2·app.pivot_detect.eff_causal_share − 1)·100`,
  range [−100,+100]; + = bullish eff-agg, − = bearish. (NON-locked = causal; the LOCKED/centered value is
  look-ahead — never use it for entry.)
- **POC baseline** — 5% EMA of `poc_price`: `base[k] = poc[k]·0.05 + base[k−1]·0.95` (pivot_detect L177-180).
- **Volume delta** — `(buy_vol − sell_vol) / curr_vol · 100` (%). + = net buying, − = net selling.

## Entry (at the signal candle's CLOSE)
**LONG** — ALL of:
1. bull (close > open)
2. skew > 0
3. panel-2 non-locked spread ≥ **+35**
4. close > POC baseline
5. **volume delta < +15%**  *(v1.1 — reject over-bought/blown-off longs; buy-exhaustion cut)*

**SHORT** — ALL of (mirror):
1. bear (close < open)
2. skew < 0
3. panel-2 non-locked spread ≤ **−35**
4. close < POC baseline
   *(no delta filter — the short-side delta cut failed out-of-sample, see caveats)*

*(No close-on-the-wick / close-position rule — tested, added nothing.)*

## Exit — FIXED, no trailing, no breakeven
- **SL**: 0.1% beyond the signal candle's extreme — `low·0.999` (long) / `high·1.001` (short).
- **TP**: distance = **RR × SL distance**. Frozen RR choices: **1:1.0** (most robust) and **1:1.5** (best in-sample).
- First barrier touched on later bars' high/low wins (SL-first on a bar that spans both). Every trade = win (TP) or loss (SL).

## Sizing (baseline)
Balance $200k; margin = 10% of balance × 10× leverage ⇒ **notional = full balance**; one position at a time,
compounding. (A volatility-scaled variant is under study — NOT part of the frozen baseline.)

## In-sample (28.4 days, 177 v1.0 signals: 79 long / 98 short) — SL-first, equity net @0.08%
| RR | v1.0 LONG win | v1.0 ALL net | **v1.1 LONG win** | **v1.1 ALL net** |
|----|-----------|-----------|----------|-------------------|
| 1:1.0 | 58.2% | −3.3% | **65.6%** | **+3.4%** |
| 1:1.5 | 48.1% | +5.4% | **54.8%** | **+8.8%** |

The v1.1 delta filter (`long delta < 15%`) drops ~14 over-bought longs, lifts LONG win ~+10pp, flips the 1:1.0
config net-positive, improves 1:1.5, and CUTS drawdown (1:1.0 8%→5%, 1:1.5 10%→6%). It holds split-half —
long win H1/H2 goes 59/47% (v1.0) → **65/67% (v1.1)**, repairing the recent-half decay. Every v1.0 cell beat
break-even; v1.1 is the same rule with the long-side exhaustion cut.

## Caveats (why this is a forward-test candidate, not a proven edge)
- **Not statistically significant**: n=177, no individual side/RR cell clears p<0.05 (best long 1:0.7 p=0.085).
  The evidence is *consistency* (longs beat BE at all 4 RRs by +7–9pp), not any single test.
- **Equity-fragile at 1:1.5**: dropping the 5 best of 79 long trades collapses +8.5% → ~0 (compounding luck).
- **Regime-suspect**: adding the POC baseline flipped the edge from short-dominant to long-dominant — likely
  SOL uptrend over the window. Forward tape decides whether it's the filter's merit or the regime.
- The panel-2 spread is causally clean (truncation-tested: spread[i] independent of all future bars).
