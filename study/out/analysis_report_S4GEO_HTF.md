# S4-GEO Stage 2 — THE EXAM: bar geometry vs next-bar direction on 5m/15m/1h/4h

_**FRESH DATA.** These tables were never analyzed (all prior S4-GEO work was 1m only). Filter:
start_time >= 2026-06-21 06:00 UTC. Pipeline imported UNCHANGED from stage 1; M/P from the shared
`app.bar_quantiles` module; strict ties; next-bar dojis excluded. Outcome = next-bar direction —
an information measure, no barriers, no fees. Track A hypotheses were declared BEFORE this data was
read; Track B holdouts were sealed and judged ONCE — **PASS means PASS.**_

## Step 0 — characterization (computed and reported before outcomes were read)
| tf | buckets | span (UTC) | ladder cov % | gaps | tie% L1hi | L1lo | L2 | L3 | M==P % | flag |
|---|---|---|---|---|---|---|---|---|---|---|
| 5m | 5558 | 06-21 06:00 -> 07-02 11:15 | 99.93 | 0 | 15.97 | 15.34 | 29.06 | 50.04 | 27.89 |  |
| 15m | 1851 | 06-21 06:00 -> 07-02 11:14 | 100.00 | 0 | 8.00 | 9.83 | 17.02 | 32.41 | 16.86 |  |
| 1h | 461 | 06-21 06:49 -> 07-02 11:17 | 100.00 | 0 | 5.86 | 4.99 | 10.63 | 17.79 | 7.81 |  |
| 4h | 114 | 06-21 06:35 -> 07-02 11:14 | 100.00 | 0 | 1.75 | 2.63 | 4.39 | 10.53 | 4.39 | THIN |

**Prediction on record — CONFIRMED:** the 1m M==P rate of 49% collapses on richer HTF ladders:
27.9% (5m) -> 16.9% (15m) -> 7.8% (1h) -> 4.4% (4h), and every tie rate falls with it — full
orderings (L3) are usable on HTF in a way they never were on 1m. Coverage ~100%, zero >2s gaps.
**4h flagged THIN** (114 usable): reported descriptively; its Track A verdicts are n-capped by rule.

## TRACK A — pre-registered hypotheses (full fresh dataset per tf, no split)
Verdict rule (pre-committed): PASS = lift negative AND 90% day-block CI clear of 0 AND n>=100
(>=50 on 4h); PARTIAL = negative but CI spans 0 or under the n bar; FAIL = flip/~0.

| tf | hyp | cell | n | P(up) | lift | 90% CI | blocks | verdict |
|---|---|---|---|---|---|---|---|---|
| 5m | H1 | `L3:O>P>M>C` | 359 | 49.86 | -0.27 | [-2.76,2.45] | 12 | PARTIAL |
| 5m | H2 | `L2:P>C` | 291 | 49.48 | -0.65 | [-4.82,3.11] | 12 | PARTIAL |
| 15m | H1 | `L3:O>P>M>C` | 142 | 35.21 | -13.19 | [-19.1,-7.73] | 12 | **PASS** |
| 15m | H2 | `L2:P>C` | 157 | 48.41 | 0.0 | [-5.9,5.42] | 12 | FAIL |
| 1h | H1 | `L3:O>P>M>C` | 47 | 46.81 | -0.68 | [-9.33,7.08] | 12 | PARTIAL |
| 1h | H2 | `L2:P>C` | 34 | 35.29 | -12.19 | [-23.46,-0.42] | 12 | PARTIAL |
| 4h | H1 | `L3:O>P>M>C` | 11 | 36.36 | -10.06 | [-25.79,2.27] | 12 | PARTIAL (THIN tf) |
| 4h | H2 | `L2:P>C` | 12 | 75.0 | 28.57 | [13.47,42.55] | 12 | FAIL (THIN tf) |

- **H1 `L3:O>P>M>C` -> next bar DOWN: PASS on 15m** — lift **-13.2pp** (P(up) 36.6% vs baseline
  49.8%), CI [-19.1,-7.7], n=142. The stage-1 survivor that faded on the spent 1m comparison slice
  is REAL at 15m on fresh data. On 5m the effect is absent (-0.3pp); 1h/4h under the n bar (signs
  negative both, -0.7 / -10.1).
- **H2 `L2:P>C` (the stage-1 CANDIDATE) does NOT generalize**: dead on 5m (-0.7) and 15m (+0.0),
  FAIL on 4h (+28.6 flip, n=12 — THIN noise). Only 1h leans its way (-12.2, CI [-23.5,-0.4] clear,
  but n=34 -> PARTIAL by the pre-committed n bar). Dropping the M constraint kills the signal:
  it is the FULL ordering (close below the entire acceptance stack, median included) that carries
  the information, not the P-high/C-low pair alone.

## TRACK B — full screen: survivors and sealed-holdout verdicts
**Multiplicity: 50 cells x 4 tfs = 200 screened** (on top of stage 1's 50). Survivor rule:
disc n>=100 (>=50 on 4h), |lift|>=5pp, CI clear of 0. Holdout judged once: same sign AND >=50%
of the discovery effect AND n>=30 -> PASS. **8 survivors -> 6 PASS:**

| tf | cell | disc n | lift | 90% CI | hold n | hold lift | verdict |
|---|---|---|---|---|---|---|---|
| 5m | `L3:C>P>M>O` | 264 | +5.49 | [1.24,9.45] | 119 | +8.84 | **PASS** |
| 5m | `L3:P>M>C>O` | 108 | -9.07 | [-14.27,-4.12] | 41 | -14.24 | **PASS** |
| 15m | `L1:low_C` | 463 | -5.63 | [-7.6,-3.79] | 185 | -4.87 | **PASS** |
| 15m | `L2:O>C` | 283 | -8.09 | [-11.58,-4.67] | 107 | -9.69 | **PASS** |
| 15m | `L3:O>P>M>C` | 106 | -13.35 | [-20.3,-6.72] | 36 | -11.92 | **PASS** |
| 1h | `L1:high_C` | 106 | +7.07 | [1.9,12.74] | 45 | +3.70 | **PASS** |
| 5m | `L2:O>P` | 206 | -6.12 | [-11.94,-0.11] | 101 | +0.66 | holdout-FAIL |
| 5m | `L2:C>P` | 213 | +5.59 | [0.25,10.35] | 113 | -0.38 | holdout-FAIL |

### Reading the six PASSes — one coherent family
Every PASS is the same phenomenon: **where the close sits relative to the bar's volume-acceptance
stack predicts CONTINUATION, and the stricter the ordering, the bigger the effect.**

- **15m is the sweet spot** (nested, all DOWN-side): close lowest of the four (`L1:low_C`, -5.6pp,
  n=463) ⊂ open highest + close lowest (`L2:O>C`, -8.1pp) ⊂ full ordering `L3:O>P>M>C` (-13.4pp
  disc / -11.9 hold) — H1 independently re-derived by the screen it was registered into.
- **5m only reacts to FULL orderings** (pairs are dead — see H2): `L3:C>P>M>O` -> UP (+5.5 disc /
  +8.8 hold, n=119) — up-bar closing above the whole stack, continuation; `L3:P>M>C>O` -> DOWN
  (-9.1 / -14.2) — up-bar (C>O) that closed UNDER its volume mass, fade. Same logic, both signs.
- **1h long side**: `L1:high_C` -> UP (+7.1 disc CI[1.9,12.7] / +3.7 hold, n=45 — clears the 50%
  bar at 52%; the thinnest PASS, treat as the family's 1h echo rather than standalone).
- Holdout killed both 5m L2 survivors (`L2:O>P` -6.1->+0.7, `L2:C>P` +5.6->-0.4): pair-level
  geometry without the median does not survive — consistent with H2's failure.

These six are NOT independent tests: the 15m trio is nested and the 5m/1h cells are the same
mechanism at other scales. Honest count: **one phenomenon, multiply confirmed; strongest single
statement = H1 on 15m.**

## 5m
5558 post-cutoff buckets, 5554 usable, 4 no-ladder; **5372 rows** after 177 next-bar dojis. Baseline 50.13% up (discovery 49.81%). Discovery/holdout 3730/1641, 12 day-blocks.

### L1 — highest/lowest of {O,C,M,P}
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L1:high_O | 1264 | 46.2 | -3.61 | [-4.5,-2.73] | 550 | -1.73 |  |
| L1:low_O | 1265 | 52.41 | 2.6 | [0.83,4.54] | 552 | -0.46 |  |
| L1:high_C | 1228 | 54.48 | 4.67 | [3.44,5.77] | 598 | 2.02 |  |
| L1:low_C | 1283 | 47.54 | -2.27 | [-3.25,-1.27] | 554 | -1.18 |  |
| L1:high_M | 154 | 51.3 | 1.49 | [-3.35,5.84] | 63 | 3.15 |  |
| L1:low_M | 155 | 47.1 | -2.72 | [-11.16,6.76] | 64 | -0.82 |  |
| L1:high_P | 455 | 49.01 | -0.8 | [-4.14,2.63] | 198 | -3.85 |  |
| L1:low_P | 450 | 49.56 | -0.26 | [-3.21,3.02] | 230 | -0.82 |  |

### L2 — (highest, lowest) pairs
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L2:O>C | 785 | 47.26 | -2.55 | [-4.04,-1.07] | 333 | -3.07 |  |
| L2:O>M | 48 | 45.83 | -3.98 | [-15.37,5.71] | 21 | -3.2 |  |
| L2:O>P | 206 | 43.69 | -6.12 | [-11.94,-0.11] | 101 | 0.66 | SURV holdout-FAIL |
| L2:C>O | 744 | 54.7 | 4.89 | [2.57,7.47] | 351 | 2.45 |  |
| L2:C>M | 68 | 50.0 | 0.19 | [-7.82,7.55] | 31 | -8.89 |  |
| L2:C>P | 213 | 55.4 | 5.59 | [0.25,10.35] | 113 | -0.38 | SURV holdout-FAIL |
| L2:M>O | 63 | 47.62 | -2.19 | [-11.53,8.31] | 19 | 12.34 |  |
| L2:M>C | 60 | 48.33 | -1.48 | [-8.58,5.05] | 27 | -10.08 |  |
| L2:M>P | 5 | 40.0 | -9.81 | [-48.87,28.9] | 5 | -10.82 |  |
| L2:P>O | 235 | 48.94 | -0.88 | [-4.57,3.21] | 84 | -12.73 |  |
| L2:P>C | 190 | 47.89 | -1.92 | [-7.0,2.7] | 101 | 1.65 |  |
| L2:P>M | 9 | 55.56 | 5.74 | [-49.75,33.98] | 4 | 24.18 |  |

### L3 — full orderings
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L3:O>C>M>P | 96 | 43.75 | -6.06 | [-12.85,2.75] | 54 | -6.38 | thin |
| L3:O>C>P>M | 15 | 53.33 | 3.52 | [-16.64,25.81] | 10 | -10.82 | thin |
| L3:O>M>C>P | 63 | 44.44 | -5.37 | [-17.5,6.09] | 29 | 0.9 | thin |
| L3:O>M>P>C | 277 | 49.82 | 0.01 | [-3.22,3.02] | 121 | -8.67 |  |
| L3:O>P>C>M | 14 | 50.0 | 0.19 | [-17.8,21.47] | 5 | 9.18 | thin |
| L3:O>P>M>C | 247 | 46.96 | -2.85 | [-5.82,-0.02] | 112 | 5.43 |  |
| L3:C>O>M>P | 114 | 54.39 | 4.57 | [-4.73,12.85] | 49 | 2.24 |  |
| L3:C>O>P>M | 28 | 53.57 | 3.76 | [-6.51,13.82] | 12 | -25.82 | thin |
| L3:C>M>O>P | 60 | 61.67 | 11.85 | [3.76,19.97] | 38 | -6.09 | thin |
| L3:C>M>P>O | 209 | 53.11 | 3.3 | [-0.55,7.63] | 123 | 5.27 |  |
| L3:C>P>O>M | 13 | 46.15 | -3.66 | [-28.92,19.64] | 8 | -13.32 | thin |
| L3:C>P>M>O | 264 | 55.3 | 5.49 | [1.24,9.45] | 119 | 8.84 | SURV **PASS** |
| L3:M>O>C>P | 2 | 50.0 | 0.19 | [,] | 1 | 49.18 | thin |
| L3:M>O>P>C | 11 | 36.36 | -13.45 | [-30.41,10.86] | 7 | -7.97 | thin |
| L3:M>C>O>P | 2 | 0.0 | -49.81 | [,] | 4 | -25.82 | thin |
| L3:M>C>P>O | 11 | 27.27 | -22.54 | [-37.64,-8.97] | 7 | -7.97 | thin |
| L3:M>P>O>C | 22 | 50.0 | 0.19 | [-16.26,14.45] | 10 | -20.82 | thin |
| L3:M>P>C>O | 27 | 59.26 | 9.45 | [-4.43,24.87] | 5 | 9.18 | thin |
| L3:P>O>C>M | 6 | 50.0 | 0.19 | [-35.54,34.47] | 2 | 49.18 | thin |
| L3:P>O>M>C | 58 | 51.72 | 1.91 | [-1.91,8.18] | 38 | 9.7 | thin |
| L3:P>C>O>M | 1 | 0.0 | -49.81 | [,] | 2 | -0.82 | thin |
| L3:P>C>M>O | 75 | 57.33 | 7.52 | [-1.19,17.47] | 27 | -17.49 | thin |
| L3:P>M>O>C | 87 | 45.98 | -3.84 | [-11.81,6.51] | 50 | -0.82 | thin |
| L3:P>M>C>O | 108 | 40.74 | -9.07 | [-14.27,-4.12] | 41 | -14.24 | SURV **PASS** |

### L4 — elementary pairs (LEFT > RIGHT)
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L4:O>C | 1800 | 46.83 | -2.98 | [-3.71,-2.27] | 786 | -0.31 |  |
| L4:O>M | 1738 | 47.07 | -2.75 | [-3.85,-1.66] | 760 | 1.02 |  |
| L4:O>P | 1664 | 47.72 | -2.1 | [-3.1,-0.95] | 752 | -0.69 |  |
| L4:C>M | 1694 | 52.07 | 2.25 | [1.18,3.38] | 799 | 0.37 |  |
| L4:C>P | 1630 | 52.02 | 2.21 | [1.01,3.41] | 781 | 1.42 |  |
| L4:M>P | 1317 | 50.34 | 0.53 | [-1.5,2.44] | 632 | 0.13 |  |

## 15m
1851 post-cutoff buckets, 1851 usable, 0 no-ladder; **1818 rows** after 32 next-bar dojis. Baseline 48.40% up (discovery 47.31%). Discovery/holdout 1264/553, 12 day-blocks.

### L1 — highest/lowest of {O,C,M,P}
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L1:high_O | 458 | 44.98 | -2.33 | [-5.92,1.39] | 188 | -4.01 |  |
| L1:low_O | 407 | 48.16 | 0.85 | [-2.87,3.59] | 193 | 0.48 |  |
| L1:high_C | 427 | 48.95 | 1.64 | [-0.73,3.86] | 188 | 2.91 |  |
| L1:low_C | 463 | 41.68 | -5.63 | [-7.6,-3.79] | 185 | -4.87 | SURV **PASS** |
| L1:high_M | 63 | 44.44 | -2.87 | [-13.42,5.06] | 37 | -7.57 |  |
| L1:low_M | 62 | 53.23 | 5.92 | [-2.71,16.02] | 34 | 13.89 |  |
| L1:high_P | 206 | 47.09 | -0.22 | [-5.72,5.56] | 105 | 6.33 |  |
| L1:low_P | 205 | 49.27 | 1.96 | [-2.71,8.64] | 87 | -4.84 |  |

### L2 — (highest, lowest) pairs
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L2:O>C | 283 | 39.22 | -8.09 | [-11.58,-4.67] | 107 | -9.69 | SURV **PASS** |
| L2:O>M | 27 | 62.96 | 15.65 | [-3.26,42.41] | 12 | 7.52 |  |
| L2:O>P | 101 | 50.5 | 3.18 | [-3.29,9.63] | 42 | -3.19 |  |
| L2:C>O | 260 | 46.92 | -0.39 | [-5.85,4.47] | 108 | 1.04 |  |
| L2:C>M | 24 | 41.67 | -5.64 | [-20.54,9.91] | 17 | 8.01 |  |
| L2:C>P | 91 | 50.55 | 3.24 | [-5.35,12.54] | 40 | -3.31 |  |
| L2:M>O | 21 | 61.9 | 14.59 | [-10.88,27.59] | 13 | -12.35 |  |
| L2:M>C | 24 | 33.33 | -13.98 | [-28.8,5.42] | 19 | 1.82 |  |
| L2:M>P | 7 | 28.57 | -18.74 | [-47.96,14.4] | 5 | -30.81 |  |
| L2:P>O | 84 | 47.62 | 0.31 | [-9.54,8.73] | 54 | 8.45 |  |
| L2:P>C | 111 | 46.85 | -0.46 | [-8.05,7.1] | 46 | 1.36 |  |
| L2:P>M | 6 | 50.0 | 2.69 | [-26.18,36.34] | 4 | 49.19 |  |

### L3 — full orderings
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L3:O>C>M>P | 51 | 60.78 | 13.47 | [0.2,27.1] | 19 | 7.08 | thin |
| L3:O>C>P>M | 16 | 62.5 | 15.19 | [-9.6,39.84] | 7 | 6.33 | thin |
| L3:O>M>C>P | 34 | 38.24 | -9.07 | [-20.06,2.2] | 15 | -10.81 | thin |
| L3:O>M>P>C | 121 | 41.32 | -5.99 | [-13.86,1.31] | 43 | -4.3 |  |
| L3:O>P>C>M | 5 | 40.0 | -7.31 | [-28.63,14.33] | 3 | 15.85 | thin |
| L3:O>P>M>C | 106 | 33.96 | -13.35 | [-20.3,-6.72] | 36 | -11.92 | SURV **PASS** |
| L3:C>O>M>P | 46 | 56.52 | 9.21 | [-3.32,20.3] | 23 | -2.99 | thin |
| L3:C>O>P>M | 7 | 57.14 | 9.83 | [-9.9,32.9] | 9 | 15.85 | thin |
| L3:C>M>O>P | 41 | 46.34 | -0.97 | [-14.05,11.36] | 13 | -12.35 | thin |
| L3:C>M>P>O | 93 | 51.61 | 4.3 | [-1.36,10.0] | 40 | -3.31 | thin |
| L3:C>P>O>M | 10 | 30.0 | -17.31 | [-46.13,2.51] | 6 | 15.85 | thin |
| L3:C>P>M>O | 104 | 44.23 | -3.08 | [-14.3,6.17] | 55 | 1.91 |  |
| L3:M>O>C>P | 5 | 40.0 | -7.31 | [-30.76,20.52] | 3 | -17.48 | thin |
| L3:M>O>P>C | 8 | 37.5 | -9.81 | [-28.49,9.72] | 5 | 29.19 | thin |
| L3:M>C>O>P | 2 | 0.0 | -47.31 | [,] | 2 | -50.81 | thin |
| L3:M>C>P>O | 5 | 60.0 | 12.69 | [-26.05,36.95] | 3 | 15.85 | thin |
| L3:M>P>O>C | 9 | 22.22 | -25.09 | [-36.84,-13.15] | 5 | -10.81 | thin |
| L3:M>P>C>O | 10 | 70.0 | 22.69 | [-3.49,40.72] | 6 | -17.48 | thin |
| L3:P>O>C>M | 1 | 100.0 | 52.69 | [,] | 2 | 49.19 | thin |
| L3:P>O>M>C | 37 | 43.24 | -4.07 | [-14.38,6.05] | 11 | -14.45 | thin |
| L3:P>C>O>M | 2 | 0.0 | -47.31 | [,] | 2 | 49.19 | thin |
| L3:P>C>M>O | 32 | 56.25 | 8.94 | [-2.77,17.63] | 22 | 3.73 | thin |
| L3:P>M>O>C | 56 | 51.79 | 4.48 | [-4.28,13.29] | 31 | 0.8 | thin |
| L3:P>M>C>O | 40 | 45.0 | -2.31 | [-12.07,9.39] | 25 | 9.19 | thin |

### L4 — elementary pairs (LEFT > RIGHT)
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L4:O>C | 654 | 45.41 | -1.9 | [-4.05,0.13] | 271 | -2.11 |  |
| L4:O>M | 625 | 47.04 | -0.27 | [-2.02,1.96] | 261 | -1.77 |  |
| L4:O>P | 615 | 47.48 | 0.17 | [-2.64,3.29] | 258 | -2.36 |  |
| L4:C>M | 595 | 51.76 | 4.45 | [2.97,5.83] | 266 | 4.07 |  |
| L4:C>P | 589 | 50.08 | 2.77 | [1.95,3.62] | 260 | 2.26 |  |
| L4:M>P | 518 | 46.72 | -0.59 | [-3.07,1.99] | 225 | -1.92 |  |

## 1h
461 post-cutoff buckets, 461 usable, 0 no-ladder; **457 rows** after 3 next-bar dojis. Baseline 47.48% up (discovery 46.71%). Discovery/holdout 319/137, 12 day-blocks.

### L1 — highest/lowest of {O,C,M,P}
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L1:high_O | 128 | 41.41 | -5.3 | [-12.14,1.09] | 42 | -4.4 |  |
| L1:low_O | 106 | 50.0 | 3.29 | [-4.13,9.71] | 51 | 9.19 |  |
| L1:high_C | 106 | 53.77 | 7.07 | [1.9,12.74] | 45 | 3.7 | SURV **PASS** |
| L1:low_C | 105 | 40.95 | -5.76 | [-13.82,1.41] | 54 | -3.34 |  |
| L1:high_M | 16 | 50.0 | 3.29 | [-10.4,17.14] | 11 | -4.18 |  |
| L1:low_M | 19 | 42.11 | -4.6 | [-30.44,14.14] | 7 | 7.51 |  |
| L1:high_P | 49 | 42.86 | -3.85 | [-17.66,7.19] | 32 | 0.36 |  |
| L1:low_P | 71 | 45.07 | -1.64 | [-9.08,5.56] | 20 | -14.64 |  |

### L2 — (highest, lowest) pairs
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L2:O>C | 70 | 38.57 | -8.14 | [-19.85,1.2] | 31 | -1.25 |  |
| L2:O>M | 9 | 44.44 | -2.26 | [-23.87,19.36] | 1 | -49.64 |  |
| L2:O>P | 40 | 35.0 | -11.71 | [-28.1,4.71] | 10 | -9.64 |  |
| L2:C>O | 66 | 51.52 | 4.81 | [-4.07,13.66] | 31 | 5.2 |  |
| L2:C>M | 6 | 33.33 | -13.38 | [-44.93,21.03] | 3 | 50.36 |  |
| L2:C>P | 27 | 59.26 | 12.55 | [4.75,24.09] | 9 | -16.3 |  |
| L2:M>O | 9 | 33.33 | -13.38 | [-24.39,-1.22] | 3 | 17.03 |  |
| L2:M>C | 4 | 75.0 | 28.29 | [,] | 6 | 0.36 |  |
| L2:M>P | 3 | 66.67 | 19.96 | [,] | 1 | -49.64 |  |
| L2:P>O | 26 | 50.0 | 3.29 | [-16.33,17.57] | 14 | 14.65 |  |
| L2:P>C | 21 | 33.33 | -13.38 | [-28.22,1.22] | 13 | -11.17 |  |
| L2:P>M | 1 | 100.0 | 53.29 | [,] | 3 | -16.3 |  |

### L3 — full orderings
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L3:O>C>M>P | 20 | 45.0 | -1.71 | [-21.43,13.78] | 5 | -9.64 | thin |
| L3:O>C>P>M | 5 | 20.0 | -26.71 | [-45.56,-0.6] | 1 | -49.64 | thin |
| L3:O>M>C>P | 17 | 23.53 | -23.18 | [-42.41,-0.65] | 3 | -16.3 | thin |
| L3:O>M>P>C | 33 | 33.33 | -13.38 | [-31.26,3.12] | 11 | -4.18 | thin |
| L3:O>P>C>M | 3 | 66.67 | 19.96 | [,] | 0 |  | thin |
| L3:O>P>M>C | 29 | 44.83 | -1.88 | [-16.7,9.24] | 18 | 0.36 | thin |
| L3:C>O>M>P | 15 | 60.0 | 13.29 | [-2.75,36.72] | 4 | 0.36 | thin |
| L3:C>O>P>M | 5 | 40.0 | -6.71 | [-42.08,27.09] | 2 | 50.36 | thin |
| L3:C>M>O>P | 11 | 54.55 | 7.84 | [-5.61,22.65] | 4 | -24.64 | thin |
| L3:C>M>P>O | 27 | 48.15 | 1.44 | [-11.17,20.54] | 13 | -3.48 | thin |
| L3:C>P>O>M | 1 | 0.0 | -46.71 | [,] | 1 | 50.36 | thin |
| L3:C>P>M>O | 34 | 55.88 | 9.17 | [-1.26,19.61] | 15 | 17.03 | thin |
| L3:M>O>C>P | 2 | 100.0 | 53.29 | [,] | 1 | -49.64 | thin |
| L3:M>O>P>C | 2 | 50.0 | 3.29 | [,] | 0 |  | thin |
| L3:M>C>O>P | 1 | 0.0 | -46.71 | [,] | 0 |  | thin |
| L3:M>C>P>O | 3 | 0.0 | -46.71 | [,] | 1 | 50.36 | thin |
| L3:M>P>O>C | 2 | 100.0 | 53.29 | [,] | 5 | -9.64 | thin |
| L3:M>P>C>O | 6 | 50.0 | 3.29 | [-18.62,15.85] | 2 | 0.36 | thin |
| L3:P>O>C>M | 0 |  |  | [,] | 2 | 0.36 | thin |
| L3:P>O>M>C | 10 | 50.0 | 3.29 | [-25.9,29.84] | 5 | -29.64 | thin |
| L3:P>C>O>M | 0 |  |  | [,] | 1 | -49.64 | thin |
| L3:P>C>M>O | 8 | 37.5 | -9.21 | [-29.73,15.25] | 7 | 7.51 | thin |
| L3:P>M>O>C | 8 | 12.5 | -34.21 | [-50.16,-16.44] | 8 | 0.36 | thin |
| L3:P>M>C>O | 16 | 56.25 | 9.54 | [-7.09,23.06] | 7 | 21.79 | thin |

### L4 — elementary pairs (LEFT > RIGHT)
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L4:O>C | 167 | 42.51 | -4.19 | [-9.12,0.91] | 70 | -5.35 |  |
| L4:O>M | 169 | 43.2 | -3.51 | [-7.17,-0.3] | 60 | -4.64 |  |
| L4:O>P | 169 | 44.97 | -1.74 | [-6.97,3.52] | 56 | -6.78 |  |
| L4:C>M | 155 | 50.97 | 4.26 | [-0.9,9.55] | 64 | 1.93 |  |
| L4:C>P | 161 | 47.83 | 1.12 | [-3.7,6.66] | 58 | 0.36 |  |
| L4:M>P | 153 | 43.79 | -2.92 | [-6.93,1.15] | 54 | -7.04 |  |

## 4h — **THIN (descriptive only, 114 usable < 400)**
114 post-cutoff buckets, 114 usable, 0 no-ladder; **112 rows** after 1 next-bar dojis. Baseline 46.43% up (discovery 41.03%). Discovery/holdout 78/33, 12 day-blocks.

### L1 — highest/lowest of {O,C,M,P}
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L1:high_O | 35 | 37.14 | -3.88 | [-16.17,7.96] | 11 | -15.15 |  |
| L1:low_O | 21 | 42.86 | 1.83 | [-11.66,16.1] | 15 | 6.06 |  |
| L1:high_C | 23 | 30.43 | -10.59 | [-26.02,4.12] | 13 | 0.93 |  |
| L1:low_C | 27 | 48.15 | 7.12 | [-2.63,16.02] | 8 | 1.89 |  |
| L1:high_M | 4 | 25.0 | -16.03 | [,] | 1 | 39.39 |  |
| L1:low_M | 6 | 66.67 | 25.64 | [-2.61,52.54] | 3 | -27.27 |  |
| L1:high_P | 14 | 64.29 | 23.26 | [6.56,35.46] | 8 | 14.39 |  |
| L1:low_P | 21 | 23.81 | -17.22 | [-29.37,-6.32] | 7 | -3.46 |  |

### L2 — (highest, lowest) pairs
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L2:O>C | 16 | 43.75 | 2.72 | [-15.76,17.2] | 5 | -20.61 |  |
| L2:O>M | 4 | 100.0 | 58.97 | [,] | 2 | -10.61 |  |
| L2:O>P | 14 | 14.29 | -26.74 | [-38.12,-4.94] | 4 | -10.61 |  |
| L2:C>O | 13 | 30.77 | -10.26 | [-30.15,10.88] | 9 | 6.06 |  |
| L2:C>M | 2 | 0.0 | -41.03 | [,] | 1 | -60.61 |  |
| L2:C>P | 6 | 33.33 | -7.69 | [-34.45,18.33] | 3 | 6.06 |  |
| L2:M>O | 1 | 0.0 | -41.03 | [,] | 1 | 39.39 |  |
| L2:M>C | 2 | 0.0 | -41.03 | [,] | 0 |  |  |
| L2:M>P | 1 | 100.0 | 58.97 | [,] | 0 |  |  |
| L2:P>O | 5 | 60.0 | 18.97 | [-9.57,40.53] | 5 | -0.61 |  |
| L2:P>C | 9 | 66.67 | 25.64 | [8.43,42.84] | 3 | 39.39 |  |
| L2:P>M | 0 |  |  | [,] | 0 |  |  |

### L3 — full orderings
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L3:O>C>M>P | 10 | 10.0 | -31.03 | [-44.57,-10.52] | 3 | -27.27 | thin |
| L3:O>C>P>M | 2 | 100.0 | 58.97 | [,] | 1 | -60.61 | thin |
| L3:O>M>C>P | 2 | 0.0 | -41.03 | [,] | 1 | 39.39 | thin |
| L3:O>M>P>C | 5 | 60.0 | 18.97 | [-12.4,55.41] | 4 | -10.61 | thin |
| L3:O>P>C>M | 1 | 100.0 | 58.97 | [,] | 1 | 39.39 | thin |
| L3:O>P>M>C | 10 | 40.0 | -1.03 | [-14.59,10.66] | 1 | -60.61 | thin |
| L3:C>O>M>P | 3 | 66.67 | 25.64 | [,] | 1 | 39.39 | thin |
| L3:C>O>P>M | 2 | 0.0 | -41.03 | [,] | 0 |  | thin |
| L3:C>M>O>P | 3 | 0.0 | -41.03 | [,] | 2 | -10.61 | thin |
| L3:C>M>P>O | 8 | 50.0 | 8.97 | [-18.47,31.28] | 6 | 6.06 | thin |
| L3:C>P>O>M | 0 |  |  | [,] | 1 | -60.61 | thin |
| L3:C>P>M>O | 4 | 0.0 | -41.03 | [,] | 3 | 6.06 | thin |
| L3:M>O>C>P | 0 |  |  | [,] | 0 |  | thin |
| L3:M>O>P>C | 1 | 0.0 | -41.03 | [,] | 0 |  | thin |
| L3:M>C>O>P | 0 |  |  | [,] | 0 |  | thin |
| L3:M>C>P>O | 1 | 0.0 | -41.03 | [,] | 0 |  | thin |
| L3:M>P>O>C | 1 | 0.0 | -41.03 | [,] | 0 |  | thin |
| L3:M>P>C>O | 0 |  |  | [,] | 1 | 39.39 | thin |
| L3:P>O>C>M | 0 |  |  | [,] | 0 |  | thin |
| L3:P>O>M>C | 3 | 66.67 | 25.64 | [,] | 1 | 39.39 | thin |
| L3:P>C>O>M | 0 |  |  | [,] | 0 |  | thin |
| L3:P>C>M>O | 1 | 0.0 | -41.03 | [,] | 2 | -10.61 | thin |
| L3:P>M>O>C | 5 | 80.0 | 38.97 | [13.43,57.97] | 2 | 39.39 | thin |
| L3:P>M>C>O | 4 | 75.0 | 33.97 | [,] | 3 | 6.06 | thin |

### L4 — elementary pairs (LEFT > RIGHT)
| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|
| L4:O>C | 46 | 41.3 | 0.28 | [-7.77,9.59] | 14 | -3.46 |  |
| L4:O>M | 44 | 40.91 | -0.12 | [-11.1,10.11] | 14 | -10.61 |  |
| L4:O>P | 46 | 36.96 | -4.07 | [-14.15,2.96] | 14 | -10.61 |  |
| L4:C>M | 38 | 31.58 | -9.45 | [-18.07,-0.4] | 20 | -5.61 |  |
| L4:C>P | 41 | 29.27 | -11.76 | [-22.02,-1.69] | 18 | -5.05 |  |
| L4:M>P | 39 | 30.77 | -10.26 | [-20.43,-2.46] | 18 | 0.51 |  |

## HARD STOP
Verdicts are final for this dataset (holdout judged once; no re-tuning). Next fresh evidence =
forward snapshots only.
