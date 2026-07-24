# Flow Flip — forward-test candidate (1h)

Exploratory reversal. One ~23-day regime, mined, forward n=0. **Not frozen-significant** — a terminal highlight to judge forward.

## Setup (`app/flow_flip_detect.py`)
Two consecutive candles opposite, the 2nd large:
- **SHORT** = candle i-1 bullish, candle i bearish, `mov_magn(i) > 50` → enter i's close.
- **LONG** = mirror (bear → big bull).
- **Exit:** STRUCTURAL stop at candle i's opposite wick ±0.1% · fixed **0.5% TP**. Break-even ≈ **74%** (the stop is wide).

## DON'T-CHASE entry filter — FROZEN 2026-07-24 (both sides)
Only take the turn when the reversal candle has **not** yet closed past candle 1's far extreme (still room to the target):
**LONG `close2 < high1` · SHORT `close2 > low1`** (`pass_entry`). Terminal draws the sphere ONLY when this holds.
- Revived the otherwise-dead LONG side: **80.0%** (n=30, +4.3%) vs chasing **69.4%** (n=36, −3.8%).
- SHORT side indifferent (81% vs 83%). Overall +5.4pp, **p=0.33 — not significant**. Directionally right, underpowered.

## Whole-data (structural / 0.5% TP)
| side | n | win% | break-even | mean/tr |
|---|---|---|---|---|
| ALL | 111 | 77.5% | 74% | +0.062% |
| SHORT (working) | 45 | 82.2% | 73% | +0.143% |
| LONG (dead w/o filter) | 66 | 74.2% | 74% | +0.008% |

- **SHORT edge lives in the dark-dE third** (eff-agg c2−c1 most negative): 100% win (n=15) vs ~73% for the other two-thirds. Spheres are shaded by dE (dark = stronger).
- **Bracket-fixed null: p=0.063** (vs random big-bear shorts at the same bracket) — the wide stop already gives ~73% on any big-bear short, so FlowFlip's lift is thin.

## ⚠ The stop is LOAD-BEARING — do NOT tighten
Tested on the don't-chase set: the structural (wide) stop is the ONLY net-positive config. Every tighter fixed stop
turns it negative (the reversal dips against you before running, so a tight stop is noise-hit right before the winner):
| exit | win% | mean/tr | total |
|---|---|---|---|
| structural / 0.5 TP | 77.3% | +0.077% | **+5.0%** |
| SL 0.5 / TP 0.5 | 53.6% | −0.044% | −3.1% |
| SL 0.4 / TP 0.5 | 46.4% | −0.063% | −4.3% |
| SL 0.3 / TP 0.5 | 37.7% | −0.079% | −5.3% |
**To risk less, SIZE DOWN the position (smaller notional), do NOT tighten the stop.**

## Terminal
Sphere prints only on `pass_entry` (don't-chase), both sides; shaded by dE (dark full-colour = flow flipped harder,
pale = weak/unknown). Structural stop unchanged. Study forward; the freeze is the research basis, not a live gate.
