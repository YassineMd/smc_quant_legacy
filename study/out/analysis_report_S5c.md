# S5c — Context-50 Confluence (locked + unlocked variants, 1m merged span)

_**Pre-registered extension of S5b/S5b-r — NOT a tuning pass. New leg 5 from the live logic; multiplicity +4 cells (long/short x locked/unlocked) -> program counter 458.** Leg 5 (frozen, 50 not tunable this run): composite 50-bar bar over [b-49, b]; LONG eligible only after a net 50-bar DECLINE (O* > C*, tick-exact at $0.01), SHORT after a net rise — bull momentum in fade context and vice versa. Legs 1'-4 per variant: V-LOCKED = S5b-r (badge spread >= 65); V-UNLOCKED = original S5b pre-correction (share >= 65); legs 1'/3/4 identical between variants. Machinery identical to S5b (30-min windows, per-cell non-overlap, seed-13 200-draw control, Jun-30 regime split, underpowered rule n < 20). Excursions are an information measure; gross unless stated._

## Data & universe
Same merged tape as S5b: 12575 bars, 06-28 11:04 -> 07-03 17:25 UTC (5.26 days). **Universe change: idx >= 50 -> 12525 evaluable rows (S5b: idx >= 16, 12,559 rows; 34 rows lost to the longer lookback).** 50-bar flat composites (O* == C* tick-exact): 103 bars -> neither side, excluded.

## 1. Attrition — momentum fires vs the 50-bar context

| cell | 4-leg fires | + leg 5 (context) | kept | episodes | locked-skip / eod |
|---|---|---|---|---|---|
| LOCKED-long | 107 | 30 | 28% | 16 | 14 / 0 |
| LOCKED-short | 29 | 10 | 34% | 7 | 3 / 0 |
| UNLOCKED-long | 191 | 77 | 40% | 34 | 43 / 0 |
| UNLOCKED-short | 68 | 26 | 38% | 15 | 11 / 0 |

Reading: 'kept' = the share of momentum fires that happened in FADE context (against the 50-bar drift); the rest fired with the trend and are excluded by leg 5.

## 2-3. Per-cell results

### LOCKED-long — 16 episodes (3.04 fires/day)

**UNDERPOWERED (n = 16 < 20): counts only, per protocol — no distributions, no control, no if-taken line. Episodes in the CSV; forward tape is the judge.**

### LOCKED-short — 7 episodes (1.33 fires/day)

**UNDERPOWERED (n = 7 < 20): counts only, per protocol — no distributions, no control, no if-taken line. Episodes in the CSV; forward tape is the judge.**

### UNLOCKED-long — 34 episodes (6.46 fires/day)

| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |
|---|---|---|---|---|---|---|---|
| 06-28 17:41 | 10329 | 71.22 | 0.674 | -0.056 | +0.323 | 21.8 | 1.2 |
| 06-29 00:15 | 11053 | 71.40 | 0.070 | -1.527 | -0.504 | 0.6 | 16.9 |
| 06-29 12:00 | 12046 | 72.40 | 2.500 | -0.428 | +2.113 | 11.9 | 0.5 |
| 06-29 13:34 | 12392 | 73.99 | 0.068 | -1.716 | -0.243 | 0.5 | 15.6 |
| 06-29 16:37 | 12685 | 73.20 | 2.281 | -0.014 | +2.090 | 28.6 | 1.1 |
| 06-29 17:30 | 12829 | 76.01 | 0.210 | -1.118 | -1.079 | 0.9 | 29.7 |
| 06-29 18:19 | 12874 | 75.54 | 0.265 | -0.543 | -0.026 | 23.1 | 11.3 |
| 06-30 00:33 | 13220 | 74.55 | 0.094 | -0.537 | -0.456 | 16.4 | 7.1 |
| 06-30 01:21 | 13340 | 74.26 | 0.512 | -0.135 | +0.135 | 7.4 | 19.5 |
| 06-30 04:14 | 13569 | 74.14 | 0.040 | -0.674 | -0.229 | 0.9 | 10.1 |
| 06-30 06:31 | 13742 | 74.10 | 0.121 | -0.135 | -0.013 | 1.8 | 5.3 |
| 06-30 08:49 | 14009 | 73.44 | 0.490 | -0.204 | +0.313 | 25.5 | 1.5 |
| 06-30 14:45 | 14873 | 73.53 | 0.014 | -1.306 | -0.707 | 0.7 | 21.0 |
| 06-30 21:28 | 15296 | 73.29 | 0.259 | -0.055 | +0.164 | 9.0 | 1.5 |
| 06-30 22:45 | 15371 | 73.40 | 0.068 | -0.150 | -0.109 | 22.4 | 19.5 |
| 07-01 06:32 | 16574 | 74.74 | 0.013 | -1.111 | -0.736 | 0.3 | 6.0 |
| 07-01 11:11 | 16994 | 74.99 | 0.400 | -0.293 | -0.147 | 8.7 | 22.3 |
| 07-01 14:31 | 17516 | 76.71 | 0.548 | -0.521 | +0.183 | 20.9 | 3.1 |
| 07-01 23:27 | 18205 | 77.61 | 0.193 | -0.515 | -0.232 | 4.1 | 22.1 |
| 07-02 00:08 | 18292 | 77.35 | 0.090 | -0.491 | -0.440 | 1.1 | 27.6 |
| 07-02 00:54 | 18332 | 77.19 | 0.803 | -0.052 | +0.713 | 24.6 | 4.1 |
| 07-02 02:30 | 18449 | 78.09 | 0.602 | 0.000 | +0.410 | 17.1 | 0.8 |
| 07-02 03:29 | 18526 | 78.38 | 0.740 | -0.217 | +0.357 | 22.7 | 2.6 |
| 07-02 04:51 | 18645 | 78.34 | 0.064 | -0.370 | -0.179 | 0.0 | 11.3 |
| 07-02 07:02 | 18800 | 77.92 | 0.013 | -0.501 | -0.257 | 0.8 | 17.3 |
| 07-02 11:08 | 19622 | 81.87 | 1.124 | -0.244 | +0.770 | 18.4 | 1.4 |
| 07-02 11:58 | 19823 | 82.22 | 0.304 | -0.718 | -0.523 | 0.8 | 21.8 |
| 07-02 12:55 | 19954 | 81.31 | 0.418 | -0.221 | +0.246 | 18.0 | 22.3 |
| 07-03 03:20 | 20976 | 80.79 | 0.186 | -0.297 | -0.062 | 4.7 | 21.4 |
| 07-03 04:28 | 21038 | 80.57 | 0.261 | -0.161 | -0.012 | 12.9 | 29.2 |
| 07-03 11:11 | 21671 | 81.50 | 0.147 | -0.356 | -0.245 | 5.3 | 29.0 |
| 07-03 11:57 | 21730 | 81.55 | 0.221 | -0.478 | -0.331 | 4.6 | 23.3 |
| 07-03 13:47 | 21870 | 81.56 | 0.294 | -0.809 | -0.539 | 5.5 | 27.0 |
| 07-03 14:19 | 21938 | 81.03 | 0.753 | -0.222 | +0.518 | 25.7 | 1.7 |

| metric | mean | median | p25 | p75 | p90 | max |
|---|---|---|---|---|---|---|
| MFE % | 0.436 | 0.260 | 0.091 | 0.539 | 0.788 | 2.500 |
| \|MAE\| % | 0.476 | 0.363 | 0.172 | 0.541 | 1.116 | 1.716 |

Ratio: med MFE 0.260 vs med \|MAE\| 0.363; MFE > \|MAE\| 44.1%; end mean +0.037% / med -0.085%.

Control (n=34 windows, 200 draws, seed 13): med MFE 0.390±0.076 | med \|MAE\| 0.326±0.063 | win 54.2±8.1 | mean end +0.117±0.093

Regime split: pre (chop) n=7 med end -0.026% | post (bull) n=27 med end -0.109%

If every setup were taken (window-end, long side):
- GROSS: W/L/F 13/21/0 | sum +1.26% | mean +0.037%/trade | avg win +0.641% vs avg loss -0.337%
- NET taker 0.10% RT: W/L/F 13/21/0 | sum -2.14% | mean -0.063%/trade | avg win +0.541% vs avg loss -0.437%

### UNLOCKED-short — 15 episodes (2.85 fires/day)

**UNDERPOWERED (n = 15 < 20): counts only, per protocol — no distributions, no control, no if-taken line. Episodes in the CSV; forward tape is the judge.**

## 4. LOCKED vs UNLOCKED — side by side

| cell | 4-leg | +leg5 | episodes | status | med MFE | med \|MAE\| | win% | med end |
|---|---|---|---|---|---|---|---|---|
| LOCKED-long | 107 | 30 | 16 | UNDERPOWERED | - | - | - | - |
| LOCKED-short | 29 | 10 | 7 | UNDERPOWERED | - | - | - | - |
| UNLOCKED-long | 191 | 77 | 34 | powered | 0.260 | 0.363 | 44.1 | -0.085 |
| UNLOCKED-short | 68 | 26 | 15 | UNDERPOWERED | - | - | - | - |

## Honest flags
- Thresholds frozen: 50 bars, 65/30-spread, 15pp — nothing tuned this run.
- Fee line is taker 0.10% round-trip on window-end only — no slippage, no stop logic; it is an accounting view, not a backtest.
- 5.26-day tape, one bull phase + ~1.5 days chop; 1m spent for mining.

## VERDICT
1/4 cells powered; underpowered cells defer to forward snapshots.

## HARD STOP
Judged once; no variants beyond the two pre-registered; forward snapshots are the judge.
