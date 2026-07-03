# S5e — Signal-Death Exit on the S5d fires

_**Pre-registered exit variant; +4 cells -> program counter 502. No re-detection** — entries are the committed S5d fire sets. Exit: newest P0 cross marker AGAINST the position AND own-side eff-agg share < 50%, both on the fire's variant semantics (locked: X markers + locked share; unlocked: X + settling-dot markers + live-edge share), evaluated per bar close; hard SL -0.3% intrabar throughout (S1 conventions, SL precedes a same-bar close signal); 6h cap flagged. Hold times: SL = touch-bar start, SIGNAL/CAP = exit-bar end. Fires overlap in time (independent sims). _Cells with n < 20: counts only._

## 1. Per cell

| cell | n | SIGNAL/SL/CAP | win% | avgW | avgL | exp gross | exp net | med hold (min) | med giveback |
|---|---|---|---|---|---|---|---|---|---|
| LOCKED-long | 26 | 18/8/0 | 38.5 | +0.667 | -0.206 | +0.130% | +0.030% | 6.4 | 0.256 |
| UNLOCKED-long | 51 | 44/7/0 | 54.9 | +0.433 | -0.152 | +0.176% | +0.076% | 2.8 | 0.095 |
| LOCKED-short _(under)_ | 12 | 7/5/0 | 33.3 | +0.390 | -0.239 | -0.009% | -0.109% | 6.0 | 0.279 |
| UNLOCKED-short | 24 | 21/3/0 | 54.2 | +0.344 | -0.166 | +0.124% | +0.024% | 3.1 | 0.119 |

## 2. Head-to-head — expectancy net (same fires, taker 0.10% RT)

| cell | SIGNAL-DEATH | fixed TP0.5/SL0.3 (S5d grid) | TRAIL SL0.3 (S5d grid) |
|---|---|---|---|
| LOCKED-long | +0.030% | -0.031% | -0.046% |
| UNLOCKED-long | +0.076% | -0.055% | -0.076% |
| LOCKED-short | -0.109% | -0.200% | -0.177% |
| UNLOCKED-short | +0.024% | -0.067% | -0.065% |

## 3. Runner capture — the direct test
S5d-BARRIERS+ TP winners whose 1h max reached >= +1.0%: what the signal exit harvested:

| variant | side | bucket | 1h max % | signal-exit pnl % | reason | held (min) |
|---|---|---|---|---|---|---|
| locked | long | 12070 | 2.179 | +0.521 | SIGNAL | 6.7 |
| locked | long | 12688 | 4.068 | +3.238 | SIGNAL | 36.4 |
| locked | long | 14563 | 1.714 | +0.014 | SIGNAL | 5.5 |
| locked | long | 16028 | 1.146 | +0.148 | SIGNAL | 3.7 |
| locked | long | 17269 | 2.589 | +0.199 | SIGNAL | 6.1 |
| locked | long | 17921 | 1.614 | +0.508 | SIGNAL | 17.2 |
| locked | long | 18331 | 1.812 | +1.242 | SIGNAL | 41.4 |
| unlocked | long | 10864 | 2.339 | +0.485 | SIGNAL | 7.3 |
| unlocked | long | 12685 | 4.495 | +4.044 | SIGNAL | 38.7 |
| unlocked | long | 14563 | 1.714 | +0.138 | SIGNAL | 2.8 |
| unlocked | long | 16028 | 1.146 | +0.040 | SIGNAL | 2.8 |
| unlocked | long | 17269 | 2.589 | +0.252 | SIGNAL | 3.6 |
| unlocked | long | 17921 | 1.614 | +0.403 | SIGNAL | 10.9 |
| unlocked | long | 18331 | 1.812 | +1.333 | SIGNAL | 39.4 |
| unlocked | long | 19622 | 1.124 | +0.928 | SIGNAL | 5.2 |
| unlocked | long | 21938 | 1.321 | +0.000 | SIGNAL | 0.4 |
| unlocked | short | 10990 | 1.802 | +0.042 | SIGNAL | 5.2 |
| unlocked | short | 12404 | 1.432 | +0.450 | SIGNAL | 5.1 |
| unlocked | short | 16932 | 1.100 | +0.808 | SIGNAL | 41.7 |

Median harvest on the 19 runners: +0.450% (vs their median 1h max 1.714%).

## 4. Exit-leg context (bar closes evaluated during holds, pre-exit)

| cell | evals | cross-leg true | share-leg true | both (exit) |
|---|---|---|---|---|
| LOCKED-long | 504 | 22.8% | 5.4% | 3.6% |
| UNLOCKED-long | 617 | 29.0% | 12.3% | 7.1% |
| LOCKED-short | 185 | 29.2% | 3.8% | 3.8% |
| UNLOCKED-short | 308 | 36.4% | 14.3% | 6.8% |

## Honest flags
- Exit variant on spent tape; the S5d-GRID conclusion stands as reference — this adds ONE pre-registered exit family, not a search.
- SL hold-time uses the S1 touch convention while SIGNAL/CAP use bar-end: median holds mix the two.

## HARD STOP
Judged once; forward snapshots are the judge.
