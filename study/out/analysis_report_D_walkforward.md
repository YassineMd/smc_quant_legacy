# Walk-Forward Selection Exam — Report D

_Generated 2026-07-02 15:23 UTC · holdout UNSEALED once, no re-runs. Fees R=0.3%, TP=1.667R, breakeven≈48.8%._


## Split
discovery 6869 buckets · embargo 525 · holdout 2590 (post-embargo).

**v1 5-item checklist: DROPPED** (superseded by the weighted score; ride-along = the 12 conditions only).


## W-ALL — selection vs baselines

| window | n | sel TP%% | always-L | always-S | better | Δ vs better | 90%% CI (sel) | gap CI | whip%% | E[R] sel | E[R] better | blk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 6869 | 41.1 | 35.7 | 40.1 | short 40.1 | +1.0 | [37.9, 44.5] | [0.3, 1.8] | 24.2 | -0.20 | -0.23 | 23 |
| HOLDOUT | 2565 | 27.3 | 50.9 | 27.2 | long 50.9 | -23.6 | [17.1, 42.6] | [-42.1, 5.1] | 21.9 | -0.57 | +0.06 | 8 |

**HEADLINE VERDICT (holdout, W-ALL): FAIL** — sel TP% 27.3 vs better-fixed 50.9 (long), gap 90% CI [-42.1, 5.1], n=2565, ~8 blocks.


### Holdout calibration — pred%% decile vs ACTUAL TP%% (does the ordering survive?)

- **long**: D1 53%(n258) · D2 51%(n258) · D3 60%(n258) · D4 52%(n258) · D5 52%(n258) · D6 52%(n257) · D7 47%(n258) · D8 45%(n258) · D9 47%(n258) · D10 49%(n258)

- **short**: D1 19%(n257) · D2 30%(n256) · D3 37%(n257) · D4 29%(n256) · D5 28%(n257) · D6 22%(n256) · D7 26%(n256) · D8 20%(n257) · D9 38%(n256) · D10 23%(n257)


### Holdout gap-decile — selected TP%% by |pred gap| decile (bigger gap = better trade?)
| dec | n | sel TP%% |
|---|---|---|
| 1 | 225 | 20.0 |
| 2 | 392 | 31.9 |
| 3 | 297 | 32.7 |
| 4 | 296 | 26.0 |
| 5 | 347 | 25.4 |
| 6 | 342 | 22.2 |
| 7 | 383 | 29.2 |
| 8 | 209 | 35.4 |
| 9 | 73 | 8.2 |
| 10 | 1 | 0.0 |
_monotonic-ish: no_


### Holdout selected-trade excursions (median)

- TP trades O.06 beyond-TP %: 0.505 (n700) · SL trades O.12 beyond-SL %: 2.411 · O.24 near-win: 0.267 · O.18 tease %: 0.133 · O.23 time-in-profit s: 82 (n1865)


## W-STAT — selection vs baselines

| window | n | sel TP%% | always-L | always-S | better | Δ vs better | 90%% CI (sel) | gap CI | whip%% | E[R] sel | E[R] better | blk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 6869 | 40.1 | 35.7 | 40.1 | short 40.1 | +0.0 | [37.0, 43.4] | [0.0, 0.0] | 24.2 | -0.23 | -0.23 | 23 |
| HOLDOUT | 2565 | 27.2 | 50.9 | 27.2 | long 50.9 | -23.7 | [16.9, 42.6] | [-42.3, 5.1] | 21.9 | -0.58 | +0.06 | 8 |

**HEADLINE VERDICT (holdout, W-STAT): FAIL** — sel TP% 27.2 vs better-fixed 50.9 (long), gap 90% CI [-42.3, 5.1], n=2565, ~8 blocks.


### Holdout calibration — pred%% decile vs ACTUAL TP%% (does the ordering survive?)

- **long**: D1 48%(n258) · D2 48%(n258) · D3 53%(n258) · D4 51%(n258) · D5 58%(n258) · D6 50%(n257) · D7 51%(n258) · D8 53%(n258) · D9 50%(n258) · D10 43%(n258)

- **short**: D1 26%(n257) · D2 26%(n256) · D3 25%(n257) · D4 29%(n256) · D5 22%(n257) · D6 25%(n256) · D7 27%(n256) · D8 33%(n257) · D9 32%(n256) · D10 29%(n257)


### Holdout gap-decile — selected TP%% by |pred gap| decile (bigger gap = better trade?)
| dec | n | sel TP%% |
|---|---|---|
| 1 | 332 | 27.7 |
| 2 | 459 | 26.1 |
| 3 | 422 | 24.2 |
| 4 | 420 | 23.6 |
| 5 | 303 | 29.7 |
| 6 | 241 | 30.3 |
| 7 | 214 | 34.1 |
| 8 | 130 | 30.8 |
| 9 | 40 | 17.5 |
| 10 | 4 | 25.0 |
_monotonic-ish: no_


### Holdout selected-trade excursions (median)

- TP trades O.06 beyond-TP %: 0.499 (n697) · SL trades O.12 beyond-SL %: 2.417 · O.24 near-win: 0.267 · O.18 tease %: 0.133 · O.23 time-in-profit s: 82 (n1868)


## Ride-along — 12 conditions (holdout vs discovery), mechanical verdict

| id | dir | disc eff | hold n_true | hold TP_t | hold TP_f | hold eff | verdict |
|---|---|---|---|---|---|---|---|
| L1 | long | +6.0 | 218 | 44.0 | 51.2 | -7.2 | FAIL |
| L2 | long | +4.2 | 1279 | 52.4 | 48.8 | +3.5 | PASS |
| L3 | long | +5.1 | 1440 | 51.1 | 50.0 | +1.2 | PARTIAL |
| L4 | long | +3.3 | 46 | 52.2 | 50.6 | +1.6 | PARTIAL |
| L5 | long | +2.7 | 1462 | 52.5 | 48.2 | +4.3 | PASS |
| L6 | long | +4.7 | 544 | 48.5 | 51.2 | -2.6 | FAIL |
| X1 | short | +24.5 | 37 | 35.1 | 27.1 | +8.1 | PARTIAL |
| X2 | short | +15.0 | 66 | 30.3 | 27.1 | +3.2 | PARTIAL |
| X3 | short | +5.5 | 1132 | 29.3 | 25.5 | +3.9 | PASS |
| X4 | short | +5.6 | 62 | 22.6 | 27.3 | -4.7 | FAIL |
| X5 | short | +4.2 | 892 | 27.0 | 27.3 | -0.2 | FAIL |
| X6 | short | +6.9 | 47 | 23.4 | 27.2 | -3.8 | FAIL |

ride-along tally: {'FAIL': 5, 'PASS': 3, 'PARTIAL': 4}
