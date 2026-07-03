# S4-GEO Stage 3 — Equality Extension (tick-exact), all timeframes

_**FRAMING.** Equality is a first-class state, defined TICK-EXACT: prices compared after rounding to
the instrument tick, **derived from data = $0.01** (every O/C/H/L and every ladder price across all
five tfs is cent-aligned; gcd of scaled prices = 1 cent; zero misaligned prices in sampling). Per tf:
**tie-bar cells (T1 "=", T2, T4) are the FIRST ANALYSIS of previously-excluded bars; extended non-tie
cells (T1 >/<, T3) are re-cuts of mined data — characterization.** The 1m dataset is SPENT throughout.
Outcome = next-bar direction (tick-exact next-bar doji excluded, counted) — an information measure,
no barriers, no fees. Pipeline otherwise unchanged (shared `app.bar_quantiles` M/P, cutoff
2026-06-21 06:00 UTC where data extends earlier — the 1m table starts 06-29, so the cutoff does not
bind there)._

## Multiplicity
**This stage: 40 cells x 5 tfs = 200 screened. S4-GEO running total: 50 (stage 1) + 200 (stage 2) +
200 (stage 3) = 450 cells**, on top of ~35+ earlier trials against the spent 1m data.

## Data & recovered bars
| tf | span (UTC) | rows | next-doji | no-ladder | recovered (had a tie) | baseline up | disc/hold | day-blocks | min-n | flags |
|---|---|---|---|---|---|---|---|---|---|---|
| 1m | 06-28 11:03 -> 07-02 11:17 | 9192 | 692 | 65 | 6946 (75.6%) | 50.34% | 6337/2854 | 5 | 100 | SPENT |
| 5m | 06-21 06:00 -> 07-02 11:15 | 5372 | 177 | 4 | 2688 (50.0%) | 50.13% | 3730/1641 | 12 | 100 |  |
| 15m | 06-21 06:00 -> 07-02 11:14 | 1818 | 32 | 0 | 590 (32.5%) | 48.40% | 1264/553 | 12 | 50 |  |
| 1h | 06-21 06:49 -> 07-02 11:17 | 457 | 3 | 0 | 82 (17.9%) | 47.48% | 319/137 | 12 | 25 |  |
| 4h | 06-21 06:35 -> 07-02 11:14 | 112 | 1 | 0 | 12 (10.7%) | 46.43% | 78/33 | 12 | 14 | THIN |

Recovered = bars carrying at least one tick-exact equality among the six O/C/M/P pairs — exactly the
bars stages 1/2 excluded from one or more levels; every one of them now carries a group at every level.

## Survivors & sealed-holdout verdicts (judged ONCE)
Survivor: disc n >= min-n, |lift| >= 5pp, 90% day-block CI clear of 0. Holdout PASS: same sign,
>= 50% of the discovery effect, n >= 15. **16 survivors -> 4 PASS:**

| tf | cell | class | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|
| 1m | `T4:C>MP>O` | EQ | 616 | +5.71 | [1.68,9.74] | 295 | +4.75 | **PASS** |
| 5m | `T4:C>MP>O` | EQ | 271 | +5.54 | [1.85,9.41] | 109 | -7.70 | holdout-FAIL |
| 15m | `T1:M=P` | EQ | 225 | +8.69 | [3.6,13.72] | 82 | -0.81 | holdout-FAIL |
| 15m | `T3:M<=rest` | RECUT | 134 | +16.12 | [8.81,24.22] | 67 | +13.37 | **PASS** |
| 15m | `T3:P<=rest` | RECUT | 310 | +5.92 | [2.5,9.9] | 135 | +2.52 | holdout-FAIL |
| 1h | `T1:M=P` | EQ | 26 | +18.68 | [0.22,35.33] | 10 | -9.64 | holdout-FAIL |
| 1h | `T3:C>=rest` | RECUT | 113 | +5.50 | [0.12,11.33] | 47 | +5.68 | **PASS** |
| 1h | `T3:M>=rest` | RECUT | 25 | +13.29 | [2.12,20.52] | 15 | -2.97 | holdout-FAIL |
| 4h | `T1:C>M` | RECUT | 38 | -9.45 | [-18.07,-0.4] | 20 | -5.61 | **PASS** |
| 4h | `T1:C>P` | RECUT | 41 | -11.76 | [-22.02,-1.69] | 18 | -5.05 | holdout-FAIL |
| 4h | `T1:C<P` | RECUT | 35 | +13.26 | [1.43,23.23] | 15 | +6.06 | holdout-FAIL |
| 4h | `T1:M>P` | RECUT | 39 | -10.26 | [-20.43,-2.46] | 18 | +0.51 | holdout-FAIL |
| 4h | `T1:M<P` | RECUT | 34 | +8.97 | [0.33,16.03] | 15 | -0.61 | holdout-FAIL |
| 4h | `T3:P>=rest` | RECUT | 16 | +27.72 | [10.22,37.17] | 8 | +14.39 | holdout-FAIL |
| 4h | `T3:P<=rest` | RECUT | 24 | -16.03 | [-29.24,-3.92] | 7 | -3.46 | holdout-FAIL |

### Reading the four PASSes
- **`15m T3:M<=rest` -> UP (+16.1pp disc CI[8.8,24.2] n=134 / +13.4 hold n=67)** — the stage's
  strongest confirmed cell: when the volume-weighted median sits at (or tied with) the BOTTOM of the
  {O,C,M,P} stack, the next 15m bar leans up by double digits. Re-cut class — but the or-tied
  universe is 41% recovered bars, and the effect dwarfs the stage-2 15m family's magnitudes.
- **`1h T3:C>=rest` -> UP (+5.5/+5.7, n=113/47)** — the or-tied extension of stage 2's 1h
  `L1:high_C` PASS (+7.1/+3.7): the close-at-the-top continuation signal survives tie inclusion at
  the same magnitude. Second confirmation of the family on 1h data.
- **`1m T4:C>MP>O` -> UP (+5.7/+4.8, n=616/295) — EQ class but SPENT data**: on M=P bars, close
  above the collapsed volume node with open below it -> continuation up. Echoes stage-2's 5m
  `L3:C>P>M>O` PASS — but note the 5m T4 sibling here FLIPPED in holdout (+5.5 disc -> -7.7),
  so treat this strictly as a 1m characterization, not a confirmed edge.
- **`4h T1:C>M` -> DOWN (-9.5/-5.6, n=38/20) — THIN**: close above the volume median fades at 4h,
  opposite sign to the lower-tf continuation family. Consistent with 4h `T1:C>P`/`T1:M>P` survivor
  signs that missed holdout. Interesting inversion hypothesis; needs another 11-day sample before it
  means anything.

Notable holdout failures: `15m T1:M=P` (+8.7 -> -0.8): the M=P state ALONE carries no edge — the
pure-equality (EQ) cells broadly did not confirm on fresh tfs; the information stays in WHERE the
close sits relative to the stack, ties included.

## 1m — **1m SPENT: characterization only**
Baseline 50.34% up; discovery 49.64% / holdout 51.86%. Survivor min-n 100.

### T1 — three-state pairs (18)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T1:O>C | RECUT | 4274 | 47.85 | -2.49 | 2980 | -2.97 | [-3.87,-2.09] | 1293 | -1.35 |  |
| T1:O<C | RECUT | 4290 | 52.84 | 2.51 | 2921 | 3.15 | [2.59,3.61] | 1369 | 1.1 |  |
| T1:O=C | EQ | 628 | 50.16 | -0.18 | 436 | -0.79 | [-3.28,2.43] | 192 | 1.27 |  |
| T1:O>M | RECUT | 3869 | 48.36 | -1.98 | 2695 | -2.26 | [-3.46,-1.34] | 1173 | -1.3 |  |
| T1:O<M | RECUT | 3859 | 52.45 | 2.11 | 2605 | 2.52 | [1.84,3.3] | 1254 | 1.17 |  |
| T1:O=M | EQ | 1464 | 50.0 | -0.34 | 1037 | -0.46 | [-1.94,1.82] | 427 | 0.13 |  |
| T1:O>P | RECUT | 3732 | 48.66 | -1.68 | 2592 | -2.23 | [-3.85,-1.14] | 1140 | -0.37 |  |
| T1:O<P | RECUT | 3690 | 52.44 | 2.1 | 2481 | 2.47 | [1.81,3.19] | 1208 | 1.21 |  |
| T1:O=P | EQ | 1770 | 49.49 | -0.85 | 1264 | -0.28 | [-2.73,2.37] | 506 | -2.05 |  |
| T1:C>M | RECUT | 3932 | 52.98 | 2.64 | 2682 | 3.15 | [2.64,3.57] | 1250 | 1.5 |  |
| T1:C<M | RECUT | 3880 | 48.14 | -2.19 | 2688 | -2.7 | [-3.6,-2.22] | 1191 | -1.06 |  |
| T1:C=M | EQ | 1380 | 48.99 | -1.35 | 967 | -1.25 | [-3.07,0.66] | 413 | -1.49 |  |
| T1:C>P | RECUT | 3759 | 52.3 | 1.96 | 2574 | 2.3 | [1.58,3.03] | 1185 | 1.22 |  |
| T1:C<P | RECUT | 3662 | 48.23 | -2.11 | 2525 | -2.16 | [-3.74,-1.16] | 1136 | -2.03 |  |
| T1:C=P | EQ | 1771 | 50.54 | 0.2 | 1238 | -0.37 | [-1.51,1.13] | 533 | 1.61 |  |
| T1:M>P | RECUT | 2400 | 50.0 | -0.34 | 1659 | -0.34 | [-1.43,0.66] | 741 | -0.31 |  |
| T1:M<P | RECUT | 2274 | 49.47 | -0.86 | 1513 | -0.74 | [-1.45,0.01] | 760 | -1.33 |  |
| T1:M=P | EQ | 4518 | 50.95 | 0.61 | 3165 | 0.53 | [-0.14,1.41] | 1353 | 0.91 |  |

### T2 — multi-equalities (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T2:O=C=M | EQ | 217 | 51.61 | 1.28 | 149 | 0.02 | [-4.45,8.41] | 68 | 4.03 |  |
| T2:O=C=P | EQ | 253 | 50.2 | -0.14 | 170 | 0.36 | [-5.19,9.37] | 83 | -1.25 |  |
| T2:O=M=P | EQ | 961 | 50.36 | 0.03 | 700 | 0.36 | [-2.82,3.91] | 261 | -0.52 |  |
| T2:C=M=P | EQ | 937 | 48.67 | -1.67 | 668 | -1.89 | [-4.37,1.74] | 269 | -0.93 |  |
| T2:O=C&M=P | EQ | 380 | 51.58 | 1.24 | 273 | 0.9 | [-4.21,5.38] | 107 | 2.35 |  |
| T2:O=M&C=P | EQ | 271 | 52.4 | 2.06 | 191 | 2.71 | [-0.82,9.54] | 80 | 0.64 |  |
| T2:O=P&C=M | EQ | 248 | 50.81 | 0.47 | 168 | 1.55 | [-6.28,13.57] | 80 | -1.86 |  |
| T2:O=C=M=P | EQ | 175 | 50.86 | 0.52 | 123 | -0.05 | [-6.95,12.15] | 52 | 1.99 |  |

### T3 — or-tied extremes (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T3:O>=rest | RECUT | 4027 | 48.3 | -2.04 | 2827 | -2.53 | [-3.46,-1.78] | 1200 | -0.77 |  |
| T3:O<=rest | RECUT | 4022 | 53.13 | 2.8 | 2735 | 3.19 | [2.07,4.18] | 1287 | 1.91 |  |
| T3:C>=rest | RECUT | 4097 | 52.92 | 2.58 | 2809 | 3.11 | [2.01,4.45] | 1288 | 1.4 |  |
| T3:C<=rest | RECUT | 4055 | 47.55 | -2.79 | 2832 | -3.28 | [-3.95,-2.83] | 1222 | -1.61 |  |
| T3:M>=rest | RECUT | 1869 | 50.4 | 0.06 | 1304 | -0.03 | [-1.55,2.22] | 565 | 0.36 |  |
| T3:M<=rest | RECUT | 1819 | 50.36 | 0.02 | 1266 | 0.59 | [0.01,1.22] | 553 | -1.22 |  |
| T3:P>=rest | RECUT | 2868 | 49.9 | -0.44 | 1991 | 0.03 | [-1.43,1.27] | 876 | -1.51 |  |
| T3:P<=rest | RECUT | 2948 | 49.29 | -1.05 | 2085 | -1.25 | [-2.14,-0.32] | 863 | -0.41 |  |

### T4 — M=P-collapsed strict orderings (6; universe = M=P bars)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T4:C>MP>O | EQ | 911 | 55.76 | 5.43 | 616 | 5.71 | [1.68,9.74] | 295 | 4.75 | **PASS** |
| T4:C>O>MP | EQ | 193 | 52.85 | 2.51 | 139 | 4.31 | [1.58,6.16] | 54 | -1.86 |  |
| T4:MP>C>O | EQ | 167 | 56.29 | 5.95 | 117 | 5.06 | [-1.36,12.01] | 50 | 8.14 |  |
| T4:MP>O>C | EQ | 198 | 44.44 | -5.89 | 129 | -3.13 | [-10.3,3.85] | 69 | -11.28 |  |
| T4:O>MP>C | EQ | 929 | 49.3 | -1.04 | 639 | -2.07 | [-6.45,0.67] | 290 | 1.25 |  |
| T4:O>C>MP | EQ | 192 | 48.96 | -1.38 | 130 | -4.26 | [-7.65,0.2] | 62 | 4.59 |  |

## 5m
Baseline 50.13% up; discovery 49.81% / holdout 50.82%. Survivor min-n 100.

### T1 — three-state pairs (18)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T1:O>C | RECUT | 2586 | 47.95 | -2.18 | 1800 | -2.98 | [-3.71,-2.27] | 786 | -0.31 |  |
| T1:O<C | RECUT | 2611 | 52.05 | 1.92 | 1792 | 2.81 | [1.87,3.73] | 819 | -0.03 |  |
| T1:O=C | EQ | 175 | 53.71 | 3.58 | 138 | 2.36 | [-3.88,7.53] | 36 | 7.51 |  |
| T1:O>M | RECUT | 2498 | 48.52 | -1.61 | 1738 | -2.75 | [-3.85,-1.66] | 760 | 1.02 |  |
| T1:O<M | RECUT | 2464 | 51.54 | 1.41 | 1703 | 2.51 | [1.09,3.93] | 760 | -1.09 |  |
| T1:O=M | EQ | 410 | 51.46 | 1.33 | 289 | 1.74 | [-4.03,7.83] | 121 | 0.42 |  |
| T1:O>P | RECUT | 2416 | 48.47 | -1.66 | 1664 | -2.1 | [-3.1,-0.95] | 752 | -0.69 |  |
| T1:O<P | RECUT | 2389 | 51.03 | 0.9 | 1658 | 1.64 | [0.79,2.57] | 730 | -0.82 |  |
| T1:O=P | EQ | 567 | 53.44 | 3.31 | 408 | 1.9 | [-1.78,5.7] | 159 | 7.04 |  |
| T1:C>M | RECUT | 2493 | 51.78 | 1.65 | 1694 | 2.25 | [1.18,3.38] | 799 | 0.37 |  |
| T1:C<M | RECUT | 2480 | 48.35 | -1.78 | 1740 | -2.17 | [-3.47,-1.08] | 739 | -0.89 |  |
| T1:C=M | EQ | 399 | 50.88 | 0.75 | 296 | -0.15 | [-3.7,2.82] | 103 | 3.55 |  |
| T1:C>P | RECUT | 2411 | 52.09 | 1.96 | 1630 | 2.21 | [1.01,3.41] | 781 | 1.42 |  |
| T1:C<P | RECUT | 2413 | 48.53 | -1.6 | 1705 | -1.48 | [-2.75,-0.03] | 707 | -1.88 |  |
| T1:C=P | EQ | 548 | 48.54 | -1.59 | 395 | -2.72 | [-6.48,1.88] | 153 | 1.46 |  |
| T1:M>P | RECUT | 1949 | 50.54 | 0.41 | 1317 | 0.53 | [-1.5,2.44] | 632 | 0.13 |  |
| T1:M<P | RECUT | 1928 | 50.16 | 0.03 | 1343 | -0.89 | [-3.33,1.66] | 584 | 2.09 |  |
| T1:M=P | EQ | 1495 | 49.57 | -0.57 | 1070 | 0.47 | [-0.92,1.74] | 425 | -3.06 |  |

### T2 — multi-equalities (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T2:O=C=M | EQ | 38 | 57.89 | 7.76 | 30 | 13.52 | [-5.2,32.48] | 8 | -13.32 |  |
| T2:O=C=P | EQ | 47 | 55.32 | 5.19 | 38 | 5.45 | [-3.92,18.31] | 9 | 4.73 |  |
| T2:O=M=P | EQ | 186 | 54.84 | 4.71 | 134 | 6.16 | [-0.18,12.92] | 52 | 1.1 |  |
| T2:C=M=P | EQ | 174 | 51.15 | 1.02 | 129 | 2.13 | [-3.36,7.09] | 45 | -1.93 |  |
| T2:O=C&M=P | EQ | 79 | 46.84 | -3.29 | 67 | -3.54 | [-13.5,5.45] | 12 | -0.82 |  |
| T2:O=M&C=P | EQ | 45 | 55.56 | 5.43 | 36 | 5.74 | [-8.07,20.47] | 9 | 4.73 |  |
| T2:O=P&C=M | EQ | 54 | 55.56 | 5.43 | 43 | 8.33 | [-2.76,19.55] | 11 | -5.37 |  |
| T2:O=C=M=P | EQ | 27 | 59.26 | 9.13 | 22 | 13.82 | [-4.18,34.63] | 5 | -10.82 |  |

### T3 — or-tied extremes (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T3:O>=rest | RECUT | 2188 | 47.76 | -2.37 | 1543 | -3.34 | [-4.1,-2.53] | 645 | 0.03 |  |
| T3:O<=rest | RECUT | 2177 | 52.32 | 2.19 | 1522 | 3.08 | [1.56,4.6] | 654 | 0.09 |  |
| T3:C>=rest | RECUT | 2202 | 52.86 | 2.73 | 1503 | 3.28 | [2.44,4.12] | 699 | 1.54 |  |
| T3:C<=rest | RECUT | 2185 | 48.74 | -1.39 | 1538 | -1.83 | [-2.77,-1.01] | 646 | -0.36 |  |
| T3:M>=rest | RECUT | 710 | 51.27 | 1.14 | 513 | 1.65 | [-2.81,5.36] | 197 | -0.06 |  |
| T3:M<=rest | RECUT | 689 | 49.49 | -0.64 | 487 | -1.76 | [-4.66,1.41] | 202 | 2.15 |  |
| T3:P>=rest | RECUT | 1351 | 48.85 | -1.28 | 961 | -1.11 | [-2.55,0.37] | 389 | -1.72 |  |
| T3:P<=rest | RECUT | 1367 | 50.84 | 0.71 | 930 | 0.3 | [-2.27,2.69] | 437 | 1.58 |  |

### T4 — M=P-collapsed strict orderings (6; universe = M=P bars)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T4:C>MP>O | EQ | 380 | 51.84 | 1.71 | 271 | 5.54 | [1.85,9.41] | 109 | -7.7 | holdout-FAIL |
| T4:C>O>MP | EQ | 80 | 60.0 | 9.87 | 49 | 3.25 | [-5.93,10.89] | 31 | 20.15 |  |
| T4:MP>C>O | EQ | 87 | 54.02 | 3.89 | 61 | 9.2 | [-0.73,19.3] | 26 | -8.51 |  |
| T4:MP>O>C | EQ | 104 | 43.27 | -6.86 | 76 | -3.76 | [-10.65,3.09] | 28 | -15.11 |  |
| T4:O>MP>C | EQ | 361 | 44.88 | -5.25 | 261 | -4.98 | [-8.51,-1.41] | 100 | -5.82 |  |
| T4:O>C>MP | EQ | 98 | 46.94 | -3.19 | 66 | -5.87 | [-15.31,6.41] | 32 | 2.3 |  |

## 15m
Baseline 48.40% up; discovery 47.31% / holdout 50.81%. Survivor min-n 50.

### T1 — three-state pairs (18)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T1:O>C | RECUT | 926 | 46.44 | -1.97 | 654 | -1.9 | [-4.05,0.13] | 271 | -2.11 |  |
| T1:O<C | RECUT | 860 | 50.12 | 1.71 | 582 | 1.49 | [-0.64,3.24] | 278 | 2.06 |  |
| T1:O=C | EQ | 32 | 59.38 | 10.97 | 28 | 13.4 | [-3.25,37.54] | 4 | -0.81 |  |
| T1:O>M | RECUT | 887 | 47.69 | -0.72 | 625 | -0.27 | [-2.02,1.96] | 261 | -1.77 |  |
| T1:O<M | RECUT | 860 | 48.6 | 0.2 | 589 | 0.23 | [-2.88,2.48] | 271 | 0.11 |  |
| T1:O=M | EQ | 71 | 54.93 | 6.52 | 50 | 0.69 | [-9.92,11.55] | 21 | 20.61 |  |
| T1:O>P | RECUT | 874 | 47.83 | -0.58 | 615 | 0.17 | [-2.64,3.29] | 258 | -2.36 |  |
| T1:O<P | RECUT | 835 | 49.22 | 0.82 | 575 | 0.34 | [-2.73,2.78] | 260 | 1.88 |  |
| T1:O=P | EQ | 109 | 46.79 | -1.62 | 74 | -4.07 | [-8.8,1.59] | 35 | 3.47 |  |
| T1:C>M | RECUT | 862 | 52.78 | 4.38 | 595 | 4.45 | [2.97,5.83] | 266 | 4.07 |  |
| T1:C<M | RECUT | 877 | 44.01 | -4.39 | 613 | -4.41 | [-5.89,-2.73] | 264 | -4.22 |  |
| T1:C=M | EQ | 79 | 49.37 | 0.96 | 56 | 0.9 | [-5.96,8.19] | 23 | 1.36 |  |
| T1:C>P | RECUT | 850 | 51.06 | 2.65 | 589 | 2.77 | [1.95,3.62] | 260 | 2.26 |  |
| T1:C<P | RECUT | 863 | 45.42 | -2.98 | 601 | -3.38 | [-4.56,-2.18] | 262 | -1.96 |  |
| T1:C=P | EQ | 105 | 51.43 | 3.02 | 74 | 5.39 | [-1.37,11.0] | 31 | -2.43 |  |
| T1:M>P | RECUT | 743 | 47.38 | -1.03 | 518 | -0.59 | [-3.07,1.99] | 225 | -1.92 |  |
| T1:M<P | RECUT | 768 | 47.01 | -1.4 | 521 | -3.16 | [-4.88,-1.26] | 246 | 2.03 |  |
| T1:M=P | EQ | 307 | 54.4 | 5.99 | 225 | 8.69 | [3.6,13.72] | 82 | -0.81 | holdout-FAIL |

### T2 — multi-equalities (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T2:O=C=M | EQ | 4 | 75.0 | 26.6 | 4 | 27.69 | [,] | 0 |  |  |
| T2:O=C=P | EQ | 5 | 80.0 | 31.6 | 5 | 32.69 | [4.55,52.65] | 0 |  |  |
| T2:O=M=P | EQ | 21 | 57.14 | 8.74 | 14 | 9.83 | [-19.74,27.52] | 7 | 6.33 |  |
| T2:C=M=P | EQ | 23 | 60.87 | 12.46 | 17 | 17.4 | [4.25,30.24] | 6 | -0.81 |  |
| T2:O=C&M=P | EQ | 10 | 80.0 | 31.6 | 8 | 40.19 | [19.08,53.61] | 2 | -0.81 |  |
| T2:O=M&C=P | EQ | 6 | 83.33 | 34.93 | 5 | 32.69 | [3.52,53.53] | 1 | 49.19 |  |
| T2:O=P&C=M | EQ | 3 | 100.0 | 51.6 | 3 | 52.69 | [,] | 0 |  |  |
| T2:O=C=M=P | EQ | 2 | 100.0 | 51.6 | 2 | 52.69 | [,] | 0 |  |  |

### T3 — or-tied extremes (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T3:O>=rest | RECUT | 709 | 46.12 | -2.28 | 505 | -1.77 | [-4.94,1.46] | 203 | -3.52 |  |
| T3:O<=rest | RECUT | 666 | 49.55 | 1.14 | 454 | 0.71 | [-2.85,3.55] | 212 | 2.02 |  |
| T3:C>=rest | RECUT | 672 | 50.15 | 1.74 | 470 | 1.63 | [-1.11,4.08] | 202 | 2.16 |  |
| T3:C<=rest | RECUT | 715 | 44.06 | -4.35 | 513 | -4.43 | [-5.68,-3.41] | 202 | -3.78 |  |
| T3:M>=rest | RECUT | 175 | 49.14 | 0.74 | 122 | 4.33 | [-0.88,10.06] | 53 | -7.42 |  |
| T3:M<=rest | RECUT | 202 | 63.86 | 15.46 | 134 | 16.12 | [8.81,24.22] | 67 | 13.37 | **PASS** |
| T3:P>=rest | RECUT | 431 | 50.12 | 1.71 | 297 | 1.17 | [-2.16,4.46] | 134 | 2.92 |  |
| T3:P<=rest | RECUT | 445 | 53.26 | 4.85 | 310 | 5.92 | [2.5,9.9] | 135 | 2.52 | holdout-FAIL |

### T4 — M=P-collapsed strict orderings (6; universe = M=P bars)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T4:C>MP>O | EQ | 76 | 47.37 | -1.04 | 63 | -2.87 | [-12.32,6.43] | 13 | 10.72 |  |
| T4:C>O>MP | EQ | 29 | 79.31 | 30.91 | 23 | 35.3 | [29.31,42.43] | 6 | 15.85 |  |
| T4:MP>C>O | EQ | 22 | 54.55 | 6.14 | 14 | 16.98 | [-3.02,42.95] | 8 | -13.31 |  |
| T4:MP>O>C | EQ | 17 | 64.71 | 16.3 | 16 | 15.19 | [1.11,27.06] | 1 | 49.19 |  |
| T4:O>MP>C | EQ | 84 | 41.67 | -6.74 | 56 | -2.67 | [-15.81,10.49] | 28 | -15.1 |  |
| T4:O>C>MP | EQ | 29 | 68.97 | 20.56 | 18 | 24.91 | [14.0,37.75] | 11 | 12.82 |  |

## 1h
Baseline 47.48% up; discovery 46.71% / holdout 49.64%. Survivor min-n 25.

### T1 — three-state pairs (18)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T1:O>C | RECUT | 237 | 43.04 | -4.45 | 167 | -4.19 | [-9.12,0.91] | 70 | -5.35 |  |
| T1:O<C | RECUT | 217 | 52.07 | 4.59 | 149 | 4.3 | [-1.19,9.54] | 67 | 5.59 |  |
| T1:O=C | EQ | 3 | 66.67 | 19.18 | 3 | 19.96 | [,] | 0 |  |  |
| T1:O>M | RECUT | 229 | 43.67 | -3.82 | 169 | -3.51 | [-7.17,-0.3] | 60 | -4.64 |  |
| T1:O<M | RECUT | 215 | 51.16 | 3.68 | 140 | 3.29 | [-1.42,7.6] | 74 | 4.42 |  |
| T1:O=M | EQ | 13 | 53.85 | 6.36 | 10 | 13.29 | [-10.84,27.87] | 3 | -16.3 |  |
| T1:O>P | RECUT | 225 | 44.44 | -3.04 | 169 | -1.74 | [-6.97,3.52] | 56 | -6.78 |  |
| T1:O<P | RECUT | 219 | 50.23 | 2.74 | 139 | 1.49 | [-6.2,7.94] | 79 | 4.8 |  |
| T1:O=P | EQ | 13 | 53.85 | 6.36 | 11 | 7.84 | [-22.74,28.13] | 2 | 0.36 |  |
| T1:C>M | RECUT | 220 | 50.91 | 3.43 | 155 | 4.26 | [-0.9,9.55] | 64 | 1.93 |  |
| T1:C<M | RECUT | 225 | 43.56 | -3.93 | 155 | -4.77 | [-9.41,-0.73] | 70 | -2.49 |  |
| T1:C=M | EQ | 12 | 58.33 | 10.85 | 9 | 8.85 | [-18.34,30.77] | 3 | 17.03 |  |
| T1:C>P | RECUT | 220 | 48.18 | 0.7 | 161 | 1.12 | [-3.7,6.66] | 58 | 0.36 |  |
| T1:C<P | RECUT | 223 | 46.19 | -1.3 | 147 | -1.81 | [-8.66,3.93] | 76 | -0.95 |  |
| T1:C=P | EQ | 14 | 57.14 | 9.66 | 11 | 7.84 | [-9.13,26.02] | 3 | 17.03 |  |
| T1:M>P | RECUT | 208 | 43.27 | -4.21 | 153 | -2.92 | [-6.93,1.15] | 54 | -7.04 |  |
| T1:M<P | RECUT | 213 | 49.77 | 2.28 | 140 | -0.28 | [-5.95,5.26] | 73 | 6.53 |  |
| T1:M=P | EQ | 36 | 58.33 | 10.85 | 26 | 18.68 | [0.22,35.33] | 10 | -9.64 | holdout-FAIL |

### T2 — multi-equalities (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T2:O=C=M | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=C=P | EQ | 1 | 100.0 | 52.52 | 1 | 53.29 | [,] | 0 |  |  |
| T2:O=M=P | EQ | 2 | 100.0 | 52.52 | 2 | 53.29 | [,] | 0 |  |  |
| T2:C=M=P | EQ | 1 | 100.0 | 52.52 | 1 | 53.29 | [,] | 0 |  |  |
| T2:O=C&M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=M&C=P | EQ | 1 | 0.0 | -47.48 | 1 | -46.71 | [,] | 0 |  |  |
| T2:O=P&C=M | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=C=M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |

### T3 — or-tied extremes (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T3:O>=rest | RECUT | 180 | 42.22 | -5.26 | 137 | -5.1 | [-11.7,0.97] | 43 | -5.45 |  |
| T3:O<=rest | RECUT | 167 | 52.69 | 5.21 | 113 | 3.73 | [-1.89,8.87] | 53 | 8.86 |  |
| T3:C>=rest | RECUT | 161 | 52.8 | 5.31 | 113 | 5.5 | [0.12,11.33] | 47 | 5.68 | **PASS** |
| T3:C<=rest | RECUT | 168 | 44.05 | -3.44 | 112 | -3.85 | [-11.98,2.88] | 56 | -3.21 |  |
| T3:M>=rest | RECUT | 40 | 55.0 | 7.52 | 25 | 13.29 | [2.12,20.52] | 15 | -2.97 | holdout-FAIL |
| T3:M<=rest | RECUT | 41 | 56.1 | 8.61 | 30 | 9.96 | [-3.35,20.69] | 11 | 4.91 |  |
| T3:P>=rest | RECUT | 105 | 47.62 | 0.14 | 66 | -1.25 | [-14.32,9.3] | 39 | 1.65 |  |
| T3:P<=rest | RECUT | 106 | 46.23 | -1.26 | 84 | 3.29 | [-5.05,10.47] | 22 | -17.82 |  |

### T4 — M=P-collapsed strict orderings (6; universe = M=P bars)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T4:C>MP>O | EQ | 8 | 37.5 | -9.98 | 5 | -6.71 | [-19.15,3.98] | 3 | -16.3 |  |
| T4:C>O>MP | EQ | 2 | 50.0 | 2.52 | 1 | 53.29 | [,] | 1 | -49.64 |  |
| T4:MP>C>O | EQ | 3 | 66.67 | 19.18 | 2 | 53.29 | [,] | 1 | -49.64 |  |
| T4:MP>O>C | EQ | 7 | 71.43 | 23.94 | 4 | 28.29 | [,] | 3 | 17.03 |  |
| T4:O>MP>C | EQ | 10 | 40.0 | -7.48 | 8 | -9.21 | [-32.68,18.24] | 2 | 0.36 |  |
| T4:O>C>MP | EQ | 3 | 100.0 | 52.52 | 3 | 53.29 | [,] | 0 |  |  |

## 4h — **THIN**
Baseline 46.43% up; discovery 41.03% / holdout 60.61%. Survivor min-n 14.

### T1 — three-state pairs (18)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T1:O>C | RECUT | 60 | 45.0 | -1.43 | 46 | 0.28 | [-7.77,9.59] | 14 | -3.46 |  |
| T1:O<C | RECUT | 51 | 47.06 | 0.63 | 31 | -2.32 | [-14.15,9.76] | 19 | 2.55 |  |
| T1:O=C | EQ | 1 | 100.0 | 53.57 | 1 | 58.97 | [,] | 0 |  |  |
| T1:O>M | RECUT | 58 | 43.1 | -3.33 | 44 | -0.12 | [-11.1,10.11] | 14 | -10.61 |  |
| T1:O<M | RECUT | 53 | 50.94 | 4.51 | 33 | 1.4 | [-12.01,16.78] | 19 | 7.81 |  |
| T1:O=M | EQ | 1 | 0.0 | -46.43 | 1 | -41.03 | [,] | 0 |  |  |
| T1:O>P | RECUT | 60 | 40.0 | -6.43 | 46 | -4.07 | [-14.15,2.96] | 14 | -10.61 |  |
| T1:O<P | RECUT | 51 | 54.9 | 8.47 | 31 | 7.36 | [-3.56,17.74] | 19 | 7.81 |  |
| T1:O=P | EQ | 1 | 0.0 | -46.43 | 1 | -41.03 | [,] | 0 |  |  |
| T1:C>M | RECUT | 59 | 38.98 | -7.45 | 38 | -9.45 | [-18.07,-0.4] | 20 | -5.61 | **PASS** |
| T1:C<M | RECUT | 51 | 54.9 | 8.47 | 38 | 8.97 | [-0.87,17.44] | 13 | 8.62 |  |
| T1:C=M | EQ | 2 | 50.0 | 3.57 | 2 | 8.97 | [,] | 0 |  |  |
| T1:C>P | RECUT | 60 | 36.67 | -9.76 | 41 | -11.76 | [-22.02,-1.69] | 18 | -5.05 | holdout-FAIL |
| T1:C<P | RECUT | 50 | 58.0 | 11.57 | 35 | 13.26 | [1.43,23.23] | 15 | 6.06 | holdout-FAIL |
| T1:C=P | EQ | 2 | 50.0 | 3.57 | 2 | 8.97 | [,] | 0 |  |  |
| T1:M>P | RECUT | 57 | 40.35 | -6.08 | 39 | -10.26 | [-20.43,-2.46] | 18 | 0.51 | holdout-FAIL |
| T1:M<P | RECUT | 50 | 52.0 | 5.57 | 34 | 8.97 | [0.33,16.03] | 15 | -0.61 | holdout-FAIL |
| T1:M=P | EQ | 5 | 60.0 | 13.57 | 5 | 18.97 | [-22.39,56.25] | 0 |  |  |

### T2 — multi-equalities (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T2:O=C=M | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=C=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:C=M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=C&M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=M&C=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=P&C=M | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T2:O=C=M=P | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |

### T3 — or-tied extremes (8)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T3:O>=rest | RECUT | 46 | 39.13 | -7.3 | 35 | -3.88 | [-16.17,7.96] | 11 | -15.15 |  |
| T3:O<=rest | RECUT | 38 | 50.0 | 3.57 | 22 | -0.12 | [-13.75,14.88] | 15 | 6.06 |  |
| T3:C>=rest | RECUT | 37 | 40.54 | -5.89 | 23 | -10.59 | [-26.02,4.12] | 13 | 0.93 |  |
| T3:C<=rest | RECUT | 36 | 50.0 | 3.57 | 28 | 5.4 | [-4.86,14.88] | 8 | 1.89 |  |
| T3:M>=rest | RECUT | 7 | 57.14 | 10.71 | 6 | 8.97 | [-39.71,57.53] | 1 | 39.39 |  |
| T3:M<=rest | RECUT | 10 | 60.0 | 13.57 | 7 | 30.4 | [3.53,57.91] | 3 | -27.27 |  |
| T3:P>=rest | RECUT | 24 | 70.83 | 24.4 | 16 | 27.72 | [10.22,37.17] | 8 | 14.39 | holdout-FAIL |
| T3:P<=rest | RECUT | 31 | 32.26 | -14.17 | 24 | -16.03 | [-29.24,-3.92] | 7 | -3.46 | holdout-FAIL |

### T4 — M=P-collapsed strict orderings (6; universe = M=P bars)
| cell | class | full n | P(up) | lift | disc n | lift | 90% CI | hold n | lift | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| T4:C>MP>O | EQ | 1 | 0.0 | -46.43 | 1 | -41.03 | [,] | 0 |  |  |
| T4:C>O>MP | EQ | 1 | 100.0 | 53.57 | 1 | 58.97 | [,] | 0 |  |  |
| T4:MP>C>O | EQ | 2 | 100.0 | 53.57 | 2 | 58.97 | [,] | 0 |  |  |
| T4:MP>O>C | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |
| T4:O>MP>C | EQ | 1 | 0.0 | -46.43 | 1 | -41.03 | [,] | 0 |  |  |
| T4:O>C>MP | EQ | 0 |  |  | 0 |  | [,] | 0 |  |  |

## HARD STOP
Holdouts judged once; verdicts final for this snapshot. Next evidence = forward snapshots only.
