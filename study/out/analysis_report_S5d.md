# S5d — Range-Context Confluence (N ∈ [50,100] zone; locked + unlocked)

_**Pre-registered variant of S5c's context leg (Yassine's design); multiplicity +4 cells -> program counter 466.** Leg 5w: LONG eligible iff C(b) is below the MAX open of the zone bars b-99..b-49 (equivalently EXISTS N in [50,100] with O(b-N+1) > C(b)); SHORT mirrored on the MIN open. Tick-exact cents, rolling max/min implementation. Both sides CAN be eligible on one bar (different N) — legal, the momentum legs pick the side (5482 such bars in-universe here). **Strictly LOOSER than S5c's fixed-50 — every S5c fire is an S5d fire (asserted on the universe).** Universe idx >= 100: 12475 evaluable rows (S5c: >= 50, 12,525; S5b: >= 16, 12,559). Legs 1'-4 identical to S5c per variant; machinery identical (per-cell non-overlap, seed-13 control, Jun-30 regime split, n<20 -> counts only). Barrier block: fires simulated INDEPENDENTLY and may overlap in time. Comparison-table completions measured fresh under this mandate: barriers on the two no-context fire sets and if-taken lines on committed sets — mechanical, not new hypothesis cells; the unlocked no-context stream is reproduced with S5b's original global-lockout semantics and asserted == the original run (55L/26S)._

## TOP — no context vs fixed-50 vs range, per variant x side

| variant | side | context | eps | fires/day | med MFE | med \|MAE\| | taken mean% (gross) | barrier TP% (res) |
|---|---|---|---|---|---|---|---|---|
| LOCKED | long | none | 35 | 6.65 | 0.418 | 0.370 | +0.082 | 37.1 |
| LOCKED | long | fixed-50 | 16 | 3.04 | 0.258 | 0.376 | +0.016 | 31.2 |
| LOCKED | long | range | 26 | 4.94 | 0.443 | 0.284 | +0.133 | 46.2 |
| LOCKED | short | none | 13 | 2.47 | 0.366 | 0.469 | -0.204 | 38.5 |
| LOCKED | short | fixed-50 | 7 | 1.33 | 0.488 | 0.345 | -0.251 | 28.6 |
| LOCKED | short | range | 12 | 2.28 | 0.351 | 0.396 | -0.314 | 25.0 |
| UNLOCKED | long | none | 55 | 10.45 | 0.261 | 0.433 | -0.026 | 32.7 |
| UNLOCKED | long | fixed-50 | 34 | 6.46 | 0.260 | 0.363 | +0.037 | 35.3 |
| UNLOCKED | long | range | 51 | 9.69 | 0.278 | 0.306 | +0.050 | 43.1 |
| UNLOCKED | short | none | 26 | 4.94 | 0.380 | 0.322 | -0.201 | 34.6 |
| UNLOCKED | short | fixed-50 | 15 | 2.85 | 0.254 | 0.345 | -0.048 | 53.3 |
| UNLOCKED | short | range | 24 | 4.56 | 0.315 | 0.319 | -0.133 | 41.7 |

## Attrition (universe idx >= 100)

leg 5w standalone eligibility: long 69.3% / short 74.6% of universe bars (loose by design).

| cell | 4-leg fires | + leg 5w | kept | episodes | locked-skip / eod |
|---|---|---|---|---|---|
| LOCKED-long | 107 | 65 | 61% | 26 | 39 / 0 |
| LOCKED-short | 29 | 15 | 52% | 12 | 3 / 0 |
| UNLOCKED-long | 191 | 133 | 70% | 51 | 82 / 0 |
| UNLOCKED-short | 68 | 40 | 59% | 24 | 16 / 0 |

## LOCKED-long — 26 episodes (4.94 fires/day)

**A. Excursions** — full episode table:

| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |
|---|---|---|---|---|---|---|---|
| 06-28 17:41 | 10329 | 71.22 | 0.674 | -0.056 | +0.323 | 21.8 | 1.2 |
| 06-29 00:36 | 11107 | 70.75 | 1.314 | -0.382 | +1.074 | 22.4 | 1.3 |
| 06-29 12:03 | 12070 | 72.96 | 1.713 | -0.206 | +1.329 | 8.6 | 0.4 |
| 06-29 16:40 | 12688 | 73.50 | 1.864 | -0.041 | +1.415 | 25.5 | 1.3 |
| 06-29 17:31 | 12831 | 75.99 | 0.132 | -1.158 | -1.119 | 1.0 | 29.7 |
| 06-30 04:14 | 13568 | 74.12 | 0.067 | -0.648 | -0.202 | 1.0 | 10.2 |
| 06-30 08:49 | 14009 | 73.44 | 0.490 | -0.204 | +0.313 | 25.5 | 1.5 |
| 06-30 13:07 | 14563 | 72.35 | 0.650 | -0.498 | -0.332 | 24.4 | 27.9 |
| 06-30 14:45 | 14873 | 73.53 | 0.014 | -1.306 | -0.707 | 0.7 | 21.0 |
| 06-30 16:54 | 15067 | 73.44 | 0.640 | -0.054 | -0.054 | 15.3 | 21.8 |
| 06-30 22:45 | 15371 | 73.40 | 0.068 | -0.150 | -0.109 | 22.4 | 19.5 |
| 07-01 02:13 | 16028 | 74.17 | 1.106 | -0.175 | +0.957 | 25.2 | 2.1 |
| 07-01 06:32 | 16574 | 74.74 | 0.013 | -1.111 | -0.736 | 0.3 | 6.0 |
| 07-01 13:38 | 17269 | 75.31 | 1.049 | -0.120 | +0.783 | 17.4 | 0.5 |
| 07-01 14:31 | 17516 | 76.71 | 0.548 | -0.521 | +0.183 | 20.9 | 3.1 |
| 07-01 20:30 | 17921 | 76.84 | 0.833 | -0.065 | +0.716 | 20.3 | 1.2 |
| 07-02 00:53 | 18331 | 77.28 | 0.686 | -0.168 | +0.453 | 25.7 | 5.2 |
| 07-02 04:51 | 18645 | 78.34 | 0.064 | -0.370 | -0.179 | 0.0 | 11.3 |
| 07-02 12:55 | 19954 | 81.31 | 0.418 | -0.221 | +0.246 | 18.0 | 22.3 |
| 07-02 15:35 | 20127 | 80.81 | 0.247 | -0.396 | +0.210 | 29.9 | 1.4 |
| 07-03 03:20 | 20976 | 80.79 | 0.186 | -0.297 | -0.062 | 4.7 | 21.4 |
| 07-03 07:41 | 21244 | 81.15 | 0.407 | -0.086 | +0.000 | 10.4 | 27.1 |
| 07-03 08:43 | 21340 | 81.43 | 0.086 | -0.860 | -0.626 | 0.0 | 17.7 |
| 07-03 11:57 | 21730 | 81.55 | 0.221 | -0.478 | -0.331 | 4.6 | 23.3 |
| 07-03 13:47 | 21870 | 81.56 | 0.294 | -0.809 | -0.539 | 5.5 | 27.0 |
| 07-03 14:31 | 21963 | 81.39 | 0.467 | -0.270 | +0.455 | 23.2 | 3.1 |

med MFE 0.443 vs med \|MAE\| 0.284; MFE>\|MAE\| 57.7%; end mean +0.133% / med +0.091%.
Control (n=26, 200 draws, seed 13): med MFE 0.394±0.102 | med \|MAE\| 0.329±0.073 | win 54.0±9.7 | mean end +0.116±0.118.
Regime: pre n=5 med end +1.074% | post n=21 med end -0.054%.

**B. Every setup taken (window end):**
- GROSS: W/L/F 13/12/1 | win 50.0% | sum +3.46% | mean +0.133% | avgW +0.651 / avgL -0.416
- NET 0.10% RT: W/L/F 13/13/0 | win 50.0% | sum +0.86% | mean +0.033%

**C. Barriers 0.5/0.3/6h** (refs: null 37.5%, fee breakeven 50.0%): TP 12 / SL 14 / unres 0 / eod 0 (ambig 0) -> TP 46.2% of resolved; expectancy gross +0.069% net -0.031%; sums gross +1.80% net -0.80%; res-time med 11.5 / p90 48.4 min.

## LOCKED-short — 12 episodes (2.28 fires/day)

**A. UNDERPOWERED (n = 12 < 20): counts only — no distributions/control; B and C below are counts, no verdict language.**

**B. Every setup taken (window end):**
- GROSS: W/L/F 5/7/0 | win 41.7% | sum -3.77% | mean -0.314% | avgW +0.436 / avgL -0.850
- NET 0.10% RT: W/L/F 5/7/0 | win 41.7% | sum -4.97% | mean -0.414%

**C. Barriers 0.5/0.3/6h** (refs: null 37.5%, fee breakeven 50.0%): TP 3 / SL 9 / unres 0 / eod 0 (ambig 0) -> TP 25.0% of resolved; expectancy gross -0.100% net -0.200%; sums gross -1.20% net -2.40%; res-time med 4.9 / p90 46.0 min.

## UNLOCKED-long — 51 episodes (9.69 fires/day)

**A. Excursions** — full episode table:

| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |
|---|---|---|---|---|---|---|---|
| 06-28 15:18 | 10048 | 71.90 | 0.278 | -0.306 | -0.250 | 15.0 | 19.3 |
| 06-28 15:52 | 10099 | 71.92 | 0.250 | -0.334 | +0.000 | 7.1 | 1.4 |
| 06-28 17:41 | 10329 | 71.22 | 0.674 | -0.056 | +0.323 | 21.8 | 1.2 |
| 06-28 22:56 | 10864 | 70.11 | 1.155 | -0.057 | +1.098 | 27.7 | 0.3 |
| 06-29 00:15 | 11053 | 71.40 | 0.070 | -1.527 | -0.504 | 0.6 | 16.9 |
| 06-29 00:55 | 11137 | 71.40 | 0.392 | -0.658 | -0.294 | 3.9 | 23.2 |
| 06-29 12:00 | 12046 | 72.40 | 2.500 | -0.428 | +2.113 | 11.9 | 0.5 |
| 06-29 13:34 | 12392 | 73.99 | 0.068 | -1.716 | -0.243 | 0.5 | 15.6 |
| 06-29 16:37 | 12685 | 73.20 | 2.281 | -0.014 | +2.090 | 28.6 | 1.1 |
| 06-29 17:30 | 12829 | 76.01 | 0.210 | -1.118 | -1.079 | 0.9 | 29.7 |
| 06-29 18:19 | 12874 | 75.54 | 0.265 | -0.543 | -0.026 | 23.1 | 11.3 |
| 06-30 00:33 | 13220 | 74.55 | 0.094 | -0.537 | -0.456 | 16.4 | 7.1 |
| 06-30 01:21 | 13340 | 74.26 | 0.512 | -0.135 | +0.135 | 7.4 | 19.5 |
| 06-30 04:14 | 13568 | 74.12 | 0.067 | -0.648 | -0.202 | 1.0 | 10.2 |
| 06-30 06:28 | 13737 | 74.15 | 0.108 | -0.202 | -0.067 | 1.3 | 7.7 |
| 06-30 08:49 | 14009 | 73.44 | 0.490 | -0.204 | +0.313 | 25.5 | 1.5 |
| 06-30 13:07 | 14563 | 72.35 | 0.650 | -0.498 | -0.332 | 24.4 | 27.9 |
| 06-30 14:45 | 14873 | 73.53 | 0.014 | -1.306 | -0.707 | 0.7 | 21.0 |
| 06-30 16:54 | 15067 | 73.44 | 0.640 | -0.054 | -0.054 | 15.3 | 21.8 |
| 06-30 21:28 | 15296 | 73.29 | 0.259 | -0.055 | +0.164 | 9.0 | 1.5 |
| 06-30 22:45 | 15371 | 73.40 | 0.068 | -0.150 | -0.109 | 22.4 | 19.5 |
| 07-01 02:13 | 16028 | 74.17 | 1.106 | -0.175 | +0.957 | 25.2 | 2.1 |
| 07-01 04:31 | 16434 | 75.65 | 0.264 | -0.410 | -0.066 | 15.4 | 5.0 |
| 07-01 06:32 | 16574 | 74.74 | 0.013 | -1.111 | -0.736 | 0.3 | 6.0 |
| 07-01 11:11 | 16994 | 74.99 | 0.400 | -0.293 | -0.147 | 8.7 | 22.3 |
| 07-01 13:38 | 17269 | 75.31 | 1.049 | -0.120 | +0.783 | 17.4 | 0.5 |
| 07-01 14:31 | 17516 | 76.71 | 0.548 | -0.521 | +0.183 | 20.9 | 3.1 |
| 07-01 20:30 | 17921 | 76.84 | 0.833 | -0.065 | +0.716 | 20.3 | 1.2 |
| 07-01 23:27 | 18205 | 77.61 | 0.193 | -0.515 | -0.232 | 4.1 | 22.1 |
| 07-02 00:08 | 18292 | 77.35 | 0.090 | -0.491 | -0.440 | 1.1 | 27.6 |
| 07-02 00:53 | 18331 | 77.28 | 0.686 | -0.168 | +0.453 | 25.7 | 5.2 |
| 07-02 02:30 | 18449 | 78.09 | 0.602 | 0.000 | +0.410 | 17.1 | 0.8 |
| 07-02 03:29 | 18526 | 78.38 | 0.740 | -0.217 | +0.357 | 22.7 | 2.6 |
| 07-02 04:51 | 18645 | 78.34 | 0.064 | -0.370 | -0.179 | 0.0 | 11.3 |
| 07-02 07:02 | 18800 | 77.92 | 0.013 | -0.501 | -0.257 | 0.8 | 17.3 |
| 07-02 11:08 | 19622 | 81.87 | 1.124 | -0.244 | +0.770 | 18.4 | 1.4 |
| 07-02 11:58 | 19823 | 82.22 | 0.304 | -0.718 | -0.523 | 0.8 | 21.8 |
| 07-02 12:55 | 19954 | 81.31 | 0.418 | -0.221 | +0.246 | 18.0 | 22.3 |
| 07-02 13:41 | 20030 | 81.55 | 0.515 | -0.674 | -0.638 | 6.8 | 29.9 |
| 07-02 15:25 | 20122 | 80.63 | 0.236 | -0.174 | +0.062 | 10.6 | 12.0 |
| 07-02 22:56 | 20461 | 80.81 | 0.012 | -0.433 | -0.210 | 0.8 | 19.6 |
| 07-03 03:20 | 20976 | 80.79 | 0.186 | -0.297 | -0.062 | 4.7 | 21.4 |
| 07-03 04:28 | 21038 | 80.57 | 0.261 | -0.161 | -0.012 | 12.9 | 29.2 |
| 07-03 05:18 | 21075 | 80.67 | 0.645 | -0.037 | +0.397 | 17.0 | 3.0 |
| 07-03 07:35 | 21238 | 81.21 | 0.332 | -0.172 | +0.037 | 15.5 | 1.6 |
| 07-03 08:43 | 21340 | 81.43 | 0.086 | -0.860 | -0.626 | 0.0 | 17.7 |
| 07-03 11:11 | 21671 | 81.50 | 0.147 | -0.356 | -0.245 | 5.3 | 29.0 |
| 07-03 11:57 | 21730 | 81.55 | 0.221 | -0.478 | -0.331 | 4.6 | 23.3 |
| 07-03 13:47 | 21870 | 81.56 | 0.294 | -0.809 | -0.539 | 5.5 | 27.0 |
| 07-03 14:19 | 21938 | 81.03 | 0.753 | -0.222 | +0.518 | 25.7 | 1.7 |
| 07-03 16:06 | 22167 | 81.30 | 0.098 | -0.246 | -0.025 | 18.8 | 8.7 |

med MFE 0.278 vs med \|MAE\| 0.306; MFE>\|MAE\| 47.1%; end mean +0.050% / med -0.062%.
Control (n=51, 200 draws, seed 13): med MFE 0.380±0.064 | med \|MAE\| 0.323±0.052 | win 53.6±7.0 | mean end +0.101±0.076.
Regime: pre n=11 med end -0.026% | post n=40 med end -0.064%.

**B. Every setup taken (window end):**
- GROSS: W/L/F 20/30/1 | win 39.2% | sum +2.53% | mean +0.050% | avgW +0.606 / avgL -0.320
- NET 0.10% RT: W/L/F 18/33/0 | win 35.3% | sum -2.57% | mean -0.050%

**C. Barriers 0.5/0.3/6h** (refs: null 37.5%, fee breakeven 50.0%): TP 22 / SL 29 / unres 0 / eod 0 (ambig 0) -> TP 43.1% of resolved; expectancy gross +0.045% net -0.055%; sums gross +2.30% net -2.80%; res-time med 13.0 / p90 47.6 min.

## UNLOCKED-short — 24 episodes (4.56 fires/day)

**A. Excursions** — full episode table:

| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |
|---|---|---|---|---|---|---|---|
| 06-28 15:43 | 10086 | 71.80 | 0.418 | -0.181 | +0.097 | 16.9 | 7.3 |
| 06-28 17:15 | 10274 | 71.59 | 0.084 | -0.964 | -0.475 | 2.7 | 20.2 |
| 06-28 19:08 | 10419 | 71.00 | 0.394 | -0.225 | -0.113 | 24.1 | 12.9 |
| 06-28 23:40 | 10990 | 71.60 | 0.154 | -1.006 | -0.698 | 6.6 | 27.3 |
| 06-29 01:08 | 11164 | 71.45 | 0.084 | -0.742 | -0.098 | 0.5 | 22.3 |
| 06-29 12:38 | 12249 | 73.74 | 0.366 | -0.963 | +0.081 | 25.8 | 14.1 |
| 06-29 13:38 | 12404 | 73.33 | 0.777 | -0.832 | -0.109 | 26.1 | 11.7 |
| 06-29 15:06 | 12584 | 72.84 | 2.142 | -0.124 | +1.991 | 25.5 | 5.7 |
| 06-29 15:47 | 12640 | 73.82 | 0.488 | -0.474 | -0.230 | 8.1 | 27.2 |
| 06-30 13:32 | 14642 | 72.06 | 2.068 | -0.153 | +2.068 | 30.0 | 5.8 |
| 06-30 14:05 | 14775 | 73.30 | 0.791 | -0.177 | +0.628 | 25.1 | 0.5 |
| 06-30 22:31 | 15350 | 73.27 | 0.259 | -0.055 | +0.109 | 9.7 | 0.7 |
| 07-01 01:46 | 15959 | 73.94 | 0.527 | -0.257 | +0.500 | 29.9 | 20.6 |
| 07-01 10:21 | 16932 | 75.46 | 0.013 | -0.649 | -0.451 | 2.1 | 21.5 |
| 07-01 11:34 | 17021 | 74.82 | 0.254 | -0.294 | -0.200 | 3.9 | 21.9 |
| 07-01 13:31 | 17227 | 74.99 | 1.480 | -0.133 | +1.120 | 24.7 | 0.8 |
| 07-01 23:37 | 18223 | 77.56 | 0.052 | -0.606 | -0.322 | 0.4 | 25.6 |
| 07-02 16:31 | 20159 | 80.45 | 0.336 | -0.447 | +0.099 | 22.1 | 8.3 |
| 07-03 06:15 | 21145 | 81.07 | 0.148 | -0.469 | -0.345 | 3.4 | 24.7 |
| 07-03 08:01 | 21281 | 81.17 | 0.160 | -0.345 | -0.333 | 5.9 | 29.7 |
| 07-03 12:22 | 21778 | 81.28 | 0.271 | -0.098 | +0.123 | 29.2 | 8.6 |
| 07-03 13:29 | 21847 | 81.56 | 0.294 | -0.208 | +0.074 | 24.0 | 8.0 |
| 07-03 14:34 | 21975 | 81.19 | 0.714 | -0.025 | +0.480 | 20.1 | 0.0 |
| 07-03 15:12 | 22071 | 81.76 | 0.000 | -0.795 | -0.795 | 0.6 | 28.9 |

med MFE 0.315 vs med \|MAE\| 0.319; MFE>\|MAE\| 50.0%; end mean +0.133% / med -0.012%.
Control (n=24, 200 draws, seed 13): med MFE 0.404±0.099 | med \|MAE\| 0.330±0.076 | win 55.0±9.5 | mean end +0.138±0.123.
Regime: pre n=9 med end -0.109% | post n=15 med end +0.099%.

**B. Every setup taken (window end):**
- GROSS: W/L/F 12/12/0 | win 50.0% | sum -3.20% | mean -0.133% | avgW +0.347 / avgL -0.614
- NET 0.10% RT: W/L/F 11/13/0 | win 45.8% | sum -5.60% | mean -0.233%

**C. Barriers 0.5/0.3/6h** (refs: null 37.5%, fee breakeven 50.0%): TP 10 / SL 14 / unres 0 / eod 0 (ambig 0) -> TP 41.7% of resolved; expectancy gross +0.033% net -0.067%; sums gross +0.80% net -1.60%; res-time med 18.5 / p90 43.7 min.

## Honest flags
- Leg 5w is looser by construction; its standalone pass rate (69-75%) means the momentum legs, not the context, do nearly all the filtering here.
- Same 5.26-day tape as S5b/S5c; 1m spent for mining; barrier fires overlap in time (independent sims).
- No other ranges, no threshold variants were run.

## HARD STOP
Judged once; forward snapshots are the judge.

## APPENDIX — barrier race + 1h post-TP / post-SL runs (S5d-BARRIERS+)

_Outcome columns appended to the committed S5d fire sets — no re-detection, no new cells (counter stays 466). Barrier race re-derived with the S1 walker and asserted equal to s5d_barrier_trades.csv row-for-row. Fires simulated INDEPENDENTLY — they may overlap in time. References: geometric null 37.5%, taker breakeven 50.0% (0.10% RT). 1h runs are right-censored when the extreme prints in the window's last bar (capped flag; also in the CSVs). missed_tp can be negative for winners that hit TP after the hour._

### LOCKED-long — 26 fires

TP 12 / SL 14 / unresolved 0 / eod 0 -> TP 46.2% of resolved (null 37.5%, breakeven 50.0%). Expectancy/trade gross +0.069%, net -0.031%. Resolution med 11.5 / p90 48.4 min; resolved inside 30 min: 20/26.

WINNERS (post-TP run within 1h of entry):

| ts (UTC) | entry | min to TP | mfe_1h % | missed_tp % | capped |
|---|---|---|---|---|---|
| 06-28 17:41 | 71.22 | 13.6 | 0.674 | +0.174 |  |
| 06-29 12:03 | 72.96 | 3.3 | 2.179 | +1.679 |  |
| 06-29 16:40 | 73.50 | 7.6 | 4.068 | +3.568 |  |
| 06-30 08:49 | 73.44 | 60.2 | 0.490 | -0.010 |  |
| 06-30 13:07 | 72.35 | 23.1 | 1.714 | +1.214 |  |
| 06-30 16:54 | 73.44 | 13.8 | 0.640 | +0.140 |  |
| 06-30 22:45 | 73.40 | 89.2 | 0.382 | -0.118 |  |
| 07-01 02:13 | 74.17 | 13.0 | 1.146 | +0.646 |  |
| 07-01 13:38 | 75.31 | 7.7 | 2.589 | +2.089 |  |
| 07-01 20:30 | 76.84 | 10.1 | 1.614 | +1.114 |  |
| 07-02 00:53 | 77.28 | 22.5 | 1.812 | +1.312 |  |
| 07-03 14:31 | 81.39 | 35.2 | 0.872 | +0.372 |  |

missed_tp median +0.880% / mean +1.015%; winners running >= +1.0% within the hour: 7/12 (58%).

LOSERS: median post-SL continuation (max adverse within 1h) 0.736% (n=14).

### LOCKED-short — 12 fires — **UNDERPOWERED (n<20): counts only, no verdict language**

TP 3 / SL 9 / unresolved 0 / eod 0 -> TP 25.0% of resolved (null 37.5%, breakeven 50.0%). Expectancy/trade gross -0.100%, net -0.200%. Resolution med 4.9 / p90 46.0 min; resolved inside 30 min: 9/12.

WINNERS (post-TP run within 1h of entry):

| ts (UTC) | entry | min to TP | mfe_1h % | missed_tp % | capped |
|---|---|---|---|---|---|
| 06-28 17:15 | 71.59 | 19.7 | 0.964 | +0.464 |  |
| 06-29 12:38 | 73.74 | 0.7 | 0.963 | +0.463 |  |
| 07-03 15:12 | 81.76 | 4.1 | 0.917 | +0.417 |  |

missed_tp median +0.463% / mean +0.448%; winners running >= +1.0% within the hour: 0/3 (0%).

LOSERS: median post-SL continuation (max adverse within 1h) 0.920% (n=9).

### UNLOCKED-long — 51 fires

TP 22 / SL 29 / unresolved 0 / eod 0 -> TP 43.1% of resolved (null 37.5%, breakeven 50.0%). Expectancy/trade gross +0.045%, net -0.055%. Resolution med 13.0 / p90 47.6 min; resolved inside 30 min: 40/51.

WINNERS (post-TP run within 1h of entry):

| ts (UTC) | entry | min to TP | mfe_1h % | missed_tp % | capped |
|---|---|---|---|---|---|
| 06-28 17:41 | 71.22 | 13.6 | 0.674 | +0.174 |  |
| 06-28 22:56 | 70.11 | 5.3 | 2.339 | +1.839 |  |
| 06-29 16:37 | 73.20 | 2.2 | 4.495 | +3.994 |  |
| 06-30 01:21 | 74.26 | 6.8 | 0.512 | +0.012 |  |
| 06-30 08:49 | 73.44 | 60.2 | 0.490 | -0.010 |  |
| 06-30 13:07 | 72.35 | 23.1 | 1.714 | +1.214 |  |
| 06-30 16:54 | 73.44 | 13.8 | 0.640 | +0.140 |  |
| 06-30 21:28 | 73.29 | 34.4 | 0.600 | +0.100 |  |
| 06-30 22:45 | 73.40 | 89.2 | 0.382 | -0.118 |  |
| 07-01 02:13 | 74.17 | 13.0 | 1.146 | +0.646 |  |
| 07-01 13:38 | 75.31 | 7.7 | 2.589 | +2.089 |  |
| 07-01 20:30 | 76.84 | 10.1 | 1.614 | +1.114 |  |
| 07-02 00:53 | 77.28 | 22.5 | 1.812 | +1.312 |  |
| 07-02 02:30 | 78.09 | 8.0 | 0.768 | +0.268 |  |
| 07-02 03:29 | 78.38 | 21.2 | 0.740 | +0.240 |  |
| 07-02 11:08 | 81.87 | 3.1 | 1.124 | +0.624 |  |
| 07-02 13:41 | 81.55 | 6.3 | 0.515 | +0.015 |  |
| 07-02 15:25 | 80.63 | 40.5 | 0.546 | +0.046 |  |
| 07-03 04:28 | 80.57 | 64.5 | 0.385 | -0.115 | Y |
| 07-03 05:18 | 80.67 | 16.8 | 0.855 | +0.355 |  |
| 07-03 14:19 | 81.03 | 20.6 | 1.321 | +0.821 |  |
| 07-03 16:06 | 81.30 | 63.7 | 0.381 | -0.119 | Y |

missed_tp median +0.254% / mean +0.665%; winners running >= +1.0% within the hour: 9/22 (41%).

LOSERS: median post-SL continuation (max adverse within 1h) 0.672% (n=29).

### UNLOCKED-short — 24 fires

TP 10 / SL 14 / unresolved 0 / eod 0 -> TP 41.7% of resolved (null 37.5%, breakeven 50.0%). Expectancy/trade gross +0.033%, net -0.067%. Resolution med 18.5 / p90 43.7 min; resolved inside 30 min: 18/24.

WINNERS (post-TP run within 1h of entry):

| ts (UTC) | entry | min to TP | mfe_1h % | missed_tp % | capped |
|---|---|---|---|---|---|
| 06-28 17:15 | 71.59 | 19.7 | 0.964 | +0.464 |  |
| 06-28 23:40 | 71.60 | 19.6 | 1.802 | +1.302 |  |
| 06-29 01:08 | 71.45 | 9.2 | 0.742 | +0.242 |  |
| 06-29 12:38 | 73.74 | 0.7 | 0.963 | +0.463 |  |
| 06-29 13:38 | 73.33 | 3.5 | 1.432 | +0.932 |  |
| 07-01 10:21 | 75.46 | 16.6 | 1.100 | +0.600 |  |
| 07-01 11:34 | 74.82 | 38.9 | 0.668 | +0.168 |  |
| 07-01 23:37 | 77.56 | 25.1 | 0.761 | +0.261 | Y |
| 07-03 13:29 | 81.56 | 44.4 | 0.871 | +0.370 |  |
| 07-03 15:12 | 81.76 | 4.1 | 0.917 | +0.417 |  |

missed_tp median +0.440% / mean +0.522%; winners running >= +1.0% within the hour: 3/10 (30%).

LOSERS: median post-SL continuation (max adverse within 1h) 0.648% (n=14).

