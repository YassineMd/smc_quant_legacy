# Barrier Study — Analysis Part A: univariate discovery screen

_Generated 2026-07-02 13:19 UTC · DISCOVERY only · **HOLDOUT SEALED** (no statistic computed on it) · univariate, no models._


## Method

- Winners = outcome **TP**, losers = **SL** (UNRESOLVED dropped). Whipsaw buckets are SL on both directions (correct). Per direction, per entry-legal computed feature.

- **Effect size** = TP-rate gap, top-vs-bottom bin. Numeric → quartiles (Q4 vs Q1, directional); categorical/flag → best-vs-worst category (max−min spread, upward-biased for high cardinality).

- **Uncertainty** = block bootstrap over **3h TIME blocks** (1000 reps, 90% CI). Rows overlap heavily, so BLOCKS are resampled, never rows. Effective-block count reported with every CI.

- **Reference lines** (every table): direction discovery baseline · **37.5% geometric null** · **48.8% fee breakeven**.

- **Leakage guard:** features restricted to E*/G*/C.*/K.*/B-* by prefix whitelist; J-/X-/O.*/L.* never enter as features. The clean-reverse/whipsaw loser split uses L.08 only to *describe* the loss, never as a predictor.


## Data health

| item | value |
|---|---|
| total span | 4.00 days |
| discovery rows (≤70% time) | 13738 |
| holdout rows (SEALED, ≥cut+6h) | 5180 — not inspected |
| long discovery | TP 2451 / SL 4418 · baseline **35.7%** · 23 3h-blocks |
| short discovery | TP 2753 / SL 4116 · baseline **40.1%** · 23 3h-blocks |
| E52.01 sz-present rows (long/short) | 2300 / 2300 — ~1.3 discovery days |
| features screened | 262 (degenerate/low-n long 7, short 7) |

> **sz caveat:** E52.01 (and E50/E51) are trade-size features that began 2026-06-30; per the coverage rule they are screened ONLY on sz-present rows (~1.3 discovery days), so absence isn't confused with signal. They are under-powered here and get their real test in forward confirmation.


## LONG — top 15 by |effect size|

_baseline 35.7% · null 37.5% · breakeven 48.8%_

| # | feature | kind | top bin (TP%% , n) | bottom bin (TP%% , n) | effect pp | 90% CI | blk | flag |
|---|---|---|---|---|---|---|---|---|
| 1 | E31.01 | num | Q4 (44.9, 1699) | Q1 (20.1, 1700) | **24.8** | [15.1, 34.5] | 23 |  |
| 2 | B-P3.08 | cat | 1 (41.4, 394) | 2 (16.9, 59) | **24.4** | [6.7, 40.9] | 13 | cat×3 |
| 3 | B-S.02 | num | Q4 (25.9, 1717) | Q1 (45.5, 1718) | **-19.6** | [-29.9, -8.8] | 23 |  |
| 4 | C.08 | cat | NEUTRAL (37.7, 3639) | STRONG BULL (18.3, 82) | **19.4** | [4.6, 30.6] | 23 | cat×9 |
| 5 | E32.07 | num | Q3 (43.7, 3321) | Q1 (25.2, 1665) | **18.4** | [7.5, 28.2] | 23 |  |
| 6 | K.10 | num | Q4 (23.7, 1479) | Q1 (41.0, 1743) | **-17.4** | [-30.4, -7.6] | 16 | ⚠level  |
| 7 | E11.05 | num | Q4 (40.7, 1709) | Q1 (23.6, 1759) | **17.1** | [7.0, 29.1] | 23 |  |
| 8 | E09.01 | num | Q4 (24.7, 1716) | Q1 (40.3, 1725) | **-15.6** | [-28.3, -5.7] | 20 | ⚠level  |
| 9 | E04.01 | num | Q4 (24.8, 1702) | Q1 (40.4, 1727) | **-15.6** | [-28.2, -5.7] | 19 | ⚠level  |
| 10 | E04.08 | num | Q4 (24.8, 1702) | Q1 (40.4, 1727) | **-15.6** | [-28.2, -5.7] | 19 | ⚠level  |
| 11 | E04.09 | num | Q4 (24.8, 1702) | Q1 (40.4, 1727) | **-15.6** | [-28.2, -5.7] | 19 | ⚠level  |
| 12 | E11.09 | num | Q3 (40.6, 3433) | Q1 (25.2, 1717) | **15.4** | [4.6, 25.3] | 23 |  |
| 13 | E11.02 | num | Q4 (40.0, 1714) | Q1 (24.7, 1714) | **15.3** | [5.7, 24.1] | 23 |  |
| 14 | K.02 | num | Q4 (24.9, 1716) | Q1 (40.1, 1717) | **-15.1** | [-27.9, -5.6] | 18 | ⚠level  |
| 15 | K.01 | num | Q4 (25.2, 1716) | Q1 (40.1, 1717) | **-14.9** | [-27.7, -4.9] | 18 | ⚠level  |

## SHORT — top 15 by |effect size|

_baseline 40.1% · null 37.5% · breakeven 48.8%_

| # | feature | kind | top bin (TP%% , n) | bottom bin (TP%% , n) | effect pp | 90% CI | blk | flag |
|---|---|---|---|---|---|---|---|---|
| 1 | B-P3.08 | cat | 2 (64.4, 59) | 1 (32.7, 394) | **31.7** | [10.9, 54.6] | 13 | cat×3 |
| 2 | E31.01 | num | Q4 (30.8, 1699) | Q1 (53.5, 1700) | **-22.7** | [-34.4, -11.5] | 23 |  |
| 3 | C.08 | cat | STRONG BULL (54.9, 82) | NEUTRAL (36.8, 3639) | **18.1** | [5.2, 28.5] | 23 | cat×9 |
| 4 | E48.06 | num | Q4 (46.0, 1717) | Q1 (28.2, 1717) | **17.7** | [5.8, 31.1] | 23 |  |
| 5 | B-S.02 | num | Q4 (48.2, 1717) | Q1 (31.3, 1718) | **16.9** | [6.5, 29.1] | 23 |  |
| 6 | E11.05 | num | Q4 (34.4, 1709) | Q1 (48.9, 1759) | **-14.5** | [-24.8, -4.6] | 23 |  |
| 7 | E29.01 | num | Q4 (33.5, 1699) | Q1 (47.5, 1700) | **-13.9** | [-22.3, -5.9] | 23 |  |
| 8 | G08.1 | num | Q4 (33.5, 1699) | Q1 (47.5, 1700) | **-13.9** | [-22.3, -5.9] | 23 |  |
| 9 | B-S.13 | num | Q4 (32.4, 1717) | Q1 (46.2, 1718) | **-13.8** | [-25.5, -3.5] | 23 |  |
| 10 | E11.09 | num | Q3 (36.3, 3433) | Q1 (49.9, 1717) | **-13.6** | [-21.1, -3.9] | 23 |  |
| 11 | E04.01 | num | Q4 (51.5, 1702) | Q1 (38.3, 1727) | **13.3** | [2.4, 32.7] | 19 | ⚠level  |
| 12 | E04.08 | num | Q4 (51.5, 1702) | Q1 (38.3, 1727) | **13.3** | [2.4, 32.7] | 19 | ⚠level  |
| 13 | E04.09 | num | Q4 (51.5, 1702) | Q1 (38.3, 1727) | **13.3** | [2.4, 32.7] | 19 | ⚠level  |
| 14 | E09.01 | num | Q4 (50.9, 1716) | Q1 (38.3, 1725) | **12.7** | [2.1, 31.3] | 20 | ⚠level  |
| 15 | E03.01 | num | Q4 (46.1, 1717) | Q1 (34.2, 1718) | **11.9** | [4.0, 19.8] | 23 |  |

## Pre-registered trader priors (shown regardless of rank)


### absorption spread — `B-P1.03`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1718 | 39.2 | 20.3 |
| Q2 | 1717 | 39.8 | 22.9 |
| Q3 | 1717 | 31.0 | 25.1 |
| Q4 ◄top | 1717 | 32.7 | 28.7 |

**effect (top−bottom) = -6.6 pp**  ·  90% CI [-12.4, -1.1]  ·  eff-blocks 23  ·  rank 52/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1718 | 40.5 | 20.3 |
| Q2 | 1717 | 37.3 | 22.9 |
| Q3 | 1717 | 43.9 | 25.1 |
| Q4 ◄top | 1717 | 38.7 | 28.7 |

**effect (top−bottom) = -1.8 pp**  ·  90% CI [-8.3, 5.5]  ·  eff-blocks 23  ·  rank 171/262


### absorption dominant side — `B-P1.04`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| bear ◄bot | 3708 | 35.0 | 24.9 |
| bull ◄top | 3161 | 36.5 | 23.4 |

**effect (top−bottom) = 1.5 pp**  ·  90% CI [-4.1, 6.4]  ·  eff-blocks 23  ·  rank 187/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| bear ◄bot | 3708 | 40.1 | 24.9 |
| bull ◄top | 3161 | 40.1 | 23.4 |

**effect (top−bottom) = 0.0 pp**  ·  90% CI [-5.1, 5.6]  ·  eff-blocks 23  ·  rank 255/262


### eff-agg spread — `B-P2.03`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1719 | 35.3 | 24.6 |
| Q2 | 1716 | 36.2 | 24.8 |
| Q3 | 1717 | 36.3 | 22.7 |
| Q4 ◄top | 1717 | 34.9 | 24.9 |

**effect (top−bottom) = -0.4 pp**  ·  90% CI [-7.0, 6.0]  ·  eff-blocks 23  ·  rank 227/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1719 | 40.1 | 24.6 |
| Q2 | 1716 | 38.9 | 24.8 |
| Q3 | 1717 | 41.1 | 22.7 |
| Q4 ◄top | 1717 | 40.2 | 24.9 |

**effect (top−bottom) = 0.2 pp**  ·  90% CI [-7.5, 7.3]  ·  eff-blocks 23  ·  rank 250/262


### last-exhausted side — `B-P4.02`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| bear ◄top | 3758 | 36.2 | 25.6 |
| bull ◄bot | 3111 | 35.0 | 22.6 |

**effect (top−bottom) = 1.2 pp**  ·  90% CI [-2.8, 5.2]  ·  eff-blocks 23  ·  rank 197/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| bear ◄bot | 3758 | 38.2 | 25.6 |
| bull ◄top | 3111 | 42.4 | 22.6 |

**effect (top−bottom) = 4.2 pp**  ·  90% CI [-1.2, 9.6]  ·  eff-blocks 23  ·  rank 85/262


### 12-state verdict at entry — `E60.01`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| BEAR EXHAUSTION ◄bot | 211 | 32.2 | 22.3 |
| BEAR TRAP ◄top | 126 | 38.9 | 23.0 |
| BULL EXHAUSTION | 217 | 33.2 | 28.6 |
| BULL TRAP | 158 | 38.0 | 20.3 |
| CHOP | 1575 | 35.4 | 24.2 |
| LIQUIDITY COIL | 5 | 60.0 | 40.0 |
| LONG SQUEEZE | 11 | 36.4 | 18.2 |
| NEUTRAL | 2149 | 35.9 | 25.5 |
| ROTATION | 1551 | 36.5 | 23.2 |
| SHORT SQUEEZE | 12 | 25.0 | 16.7 |
| STRONG BEAR | 373 | 34.9 | 25.2 |
| STRONG BULL | 481 | 34.7 | 21.8 |

**effect (top−bottom) = 6.7 pp**  ·  90% CI [-0.9, 13.8]  ·  eff-blocks 23  ·  rank 49/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| BEAR EXHAUSTION ◄top | 211 | 45.5 | 22.3 |
| BEAR TRAP ◄bot | 126 | 38.1 | 23.0 |
| BULL EXHAUSTION | 217 | 38.2 | 28.6 |
| BULL TRAP | 158 | 41.8 | 20.3 |
| CHOP | 1575 | 40.4 | 24.2 |
| LIQUIDITY COIL | 5 | 0.0 | 40.0 |
| LONG SQUEEZE | 11 | 45.5 | 18.2 |
| NEUTRAL | 2149 | 38.6 | 25.5 |
| ROTATION | 1551 | 40.3 | 23.2 |
| SHORT SQUEEZE | 12 | 58.3 | 16.7 |
| STRONG BEAR | 373 | 39.9 | 25.2 |
| STRONG BULL | 481 | 43.5 | 21.8 |

**effect (top−bottom) = 7.4 pp**  ·  90% CI [-1.4, 16.6]  ·  eff-blocks 23  ·  rank 38/262


### 15-candle context direction — `C.01`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| -1 | 3419 | 34.6 | 24.6 |
| 0 ◄bot | 111 | 27.0 | 26.1 |
| 1 ◄top | 3339 | 37.1 | 23.8 |

**effect (top−bottom) = 10.0 pp**  ·  90% CI [0.5, 17.5]  ·  eff-blocks 23  ·  rank 33/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| -1 | 3419 | 40.8 | 24.6 |
| 0 ◄top | 111 | 46.8 | 26.1 |
| 1 ◄bot | 3339 | 39.1 | 23.8 |

**effect (top−bottom) = 7.8 pp**  ·  90% CI [-0.9, 15.8]  ·  eff-blocks 23  ·  rank 35/262


### 15-candle context %chg — `C.02`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1718 | 34.9 | 25.0 |
| Q2 | 1812 | 33.9 | 24.3 |
| Q3 | 1622 | 34.3 | 24.9 |
| Q4 ◄top | 1717 | 39.7 | 22.8 |

**effect (top−bottom) = 4.9 pp**  ·  90% CI [-3.0, 12.1]  ·  eff-blocks 23  ·  rank 74/262

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| Q1 ◄bot | 1718 | 40.2 | 25.0 |
| Q2 | 1812 | 41.8 | 24.3 |
| Q3 | 1622 | 40.8 | 24.9 |
| Q4 ◄top | 1717 | 37.4 | 22.8 |

**effect (top−bottom) = -2.7 pp**  ·  90% CI [-10.0, 5.1]  ·  eff-blocks 23  ·  rank 124/262


### large-order net side APPROX(fixed-273c) — `E52.01`

**long** (baseline 35.7% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| large-buy ◄top | 1156 | 42.9 | 22.4 |
| large-sell ◄bot | 1143 | 37.7 | 22.1 |
| none | 1 | 100.0 | 0.0 |

**effect (top−bottom) = 5.2 pp**  ·  90% CI [-0.7, 10.9]  ·  eff-blocks 7  ·  rank 65/262  ·  APPROX(fixed-273c)

**short** (baseline 40.1% · null 37.5% · breakeven 48.8%):

| bin | n | TP%% | whip%% |
|---|---|---|---|
| large-buy ◄bot | 1156 | 34.7 | 22.4 |
| large-sell ◄top | 1143 | 40.2 | 22.1 |
| none | 1 | 0.0 | 0.0 |

**effect (top−bottom) = 5.5 pp**  ·  90% CI [-2.2, 12.2]  ·  eff-blocks 7  ·  rank 59/262  ·  APPROX(fixed-273c)


## Caveats

1. **Wide CIs by construction** — only ~23–23 independent 3h blocks; treat point effects as screening signal, not proof. The holdout is the real test.

2. **Multiple comparisons** — 262 features screened; high-cardinality categoricals (e.g. E60.01 12-state) use a best-vs-worst spread that is upward-biased. Ranking is for shortlisting only.

3. **sz under-power** — E52.01/E50*/E51* rest on ~1.3 discovery days (post-2026-06-30).

4. **⚠ Non-stationary LEVEL features are regime confounds, not signals** — absolute price levels (E04.* close, E09.* POC, K.01/K.02/K.03/K.10 KC bands & rolling-POC) rank high only because price trended across the 4-day window, so 'price bin' proxies the calendar. They are tagged `⚠level` and should be **discounted** as entry features regardless of effect size. Relatedly, the market-**tempo** axis (E31 avg-velocity, B-S.02 duration, E29 vel, G08.1, E11 volume) flips sign long↔short — a directional-**regime** read, not a per-side edge; only the holdout can tell regime-fit from signal.

5. **Genuinely stationary candidates** to weigh for the shortlist: the flow/structure features that are ratios/shares/states/flags (B-P3.08 both-hot E/R, C.08 context-state fade, the absorption/eff-agg spreads, exhaustion side) — comparable across time by construction.

6. **Holdout sealed** — no statistic here touched the last 30%%; it unseals once, after the architect + Yassine register the shortlist.


**HARD STOP — discovery screen only. No holdout, no models, no multivariate fits.**
