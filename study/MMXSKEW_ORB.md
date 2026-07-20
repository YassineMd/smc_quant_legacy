# MMXSKEW-ORB 1h Strategy — FROZEN SPEC (variant of MMXSKEW v1.1)

**FROZEN 2026-07-19.** In-sample only (28.4 days). Forward-test pending. Variant of `study/MMXSKEW.md` v1.1
that adds a New-York-session **opening-range / once-per-day** timing filter. Reference backtest:
`study/mm_skew_orb.py`.

## The idea
Take **one trade per day**, only during the NY session, and only the day's first v1.1 MM×Skew setup that is
also breaking the opening range. In practice the breakout gate rarely binds (a v1.1 signal after the open is
almost always already a range breakout — it requires close > POC baseline), so this is effectively a
**once-per-day, NY-session-timed** filter on v1.1.

## Session / timing (data is EDT = UTC−4 over the sample)
- **NY open** = 9:30 ET = **13:30 UTC**.
- **Opening range (OR)** = high/low of the constant-volume buckets whose start falls in **13:30–14:00 UTC**
  (the first 30 min). `OR_high`, `OR_low`; **"the open"** = the OPEN price of the first OR-window bucket.
- **Entry window** = **14:00–20:00 UTC** (10:00–16:00 ET) — the NY RTH session only, *after* the OR. A breakout
  outside the session (evening/overnight) does NOT qualify. (v1.0.1 fix: was unbounded to end-of-ET-day, which
  smeared entries across the evening — the ORB is a NY-session strategy.)

## Entry — one per day
The FIRST post-open bucket that is a **v1.1 MM×Skew signal** (see MMXSKEW.md — direction + skew + panel-2
spread ±35 + POC baseline + long delta<15) AND an **opening-range breakout** (long: close > OR_high; short:
close < OR_low). One trade/day; flat-to-flat (skip a day if a prior trade is still open).

## Exit — two SL modes (compared)
- **FROZEN SL** — 0.1% beyond the signal candle's extreme (as in MMXSKEW.md).
- **ORB SL** — 0.1% beyond the session **OPEN** (`open·0.999` long / `open·1.001` short).
- **TP** = RR × SL distance. Frozen RR choices: **1:1.0** and **1:1.5**.

## In-sample (28.4 days, 24 days-with-open, 10–13 trades) — SL-first, net @0.08% — NY-session-capped
| SL mode | RR | n | win% | net | maxDD |
|---------|----|---|------|-----|-------|
| frozen | 1:1.0 | 13 | 76.9% | +9.3% | 1.0% |
| frozen | 1:1.5 | 12 | 75.0% | +14.7% | 1.0% |
| ORB | 1:1.0 | 12 | 75.0% | +14.4% | 1.7% |
| **ORB** | **1:1.5** | **10** | **80.0%** | **+19.9%** | **2.2%** |

**Best config: ORB SL @ 1:1.5.** Higher win rate than full v1.1 (~70% vs ~52%), clears break-even
significantly even at n≈11 (p=0.027), tiny drawdown, and — unlike v1.1 at 1:1.5 — the return survives
dropping the best 2 trades (not luck-carried). ORB SL beats frozen SL on return; frozen beats ORB on DD.

## Caveats (the dominant one is SAMPLE SIZE)
- **n = 11–14 trades, one 28-day (likely uptrend) regime.** Win-rate CI ≈ [45%, 90%]; the +18% magnitude
  will NOT repeat cleanly. Treat the *direction* (once-per-day NY-session selection sharply lifts win rate and
  cuts drawdown) as the finding, not the number. Forward tape is the only real test.
- Session assumes EDT throughout (true for the Jun–Jul sample); a live version must handle EST/EDT.
