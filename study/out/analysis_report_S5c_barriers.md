# S5c-BARRIERS — 0.5/0.3/6h barrier re-measure of the S5c fires

_**Pre-registered outcome re-measure on the EXACT S5c episode sets (no re-detection); multiplicity +4 cells -> program counter 462.** Entry = fire-bar close; LONG TP +0.5% / SL -0.3%, SHORT mirrored; S1 path-walker conventions verbatim (inclusive touch, bar-start horizon test, one bar spanning BOTH barriers -> SL, flagged). 6h cap; UNRESOLVED excluded from win rate; end-of-data excluded, counted. **Trades are simulated independently and MAY OVERLAP in time** — unlike the S5c episode windows, this is a per-fire outcome measure, not a sequential book. References for every table: geometric null **37.5%** (0.3/0.8); fee-adjusted breakeven **50.0%** at taker 0.10% RT (net win +0.40% vs net loss -0.40%). Expectancy: gross = p x 0.5 - (1-p) x 0.3; net = gross - 0.10._

## LOCKED vs UNLOCKED — summary

| cell | n | TP | SL | unres | eod | ambig | TP% (res) | vs null 37.5 | vs BE 50.0 | gross E/trade | net E/trade | gross sum | net sum | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LOCKED-long | 16 | 5 | 11 | 0 | 0 | 0 | 31.2% | -6.2 | -18.8 | -0.050% | -0.150% | -0.80% | -2.40% | UNDERPOWERED |
| LOCKED-short | 7 | 2 | 5 | 0 | 0 | 0 | 28.6% | -8.9 | -21.4 | -0.071% | -0.171% | -0.50% | -1.20% | UNDERPOWERED |
| UNLOCKED-long | 34 | 12 | 22 | 0 | 0 | 0 | 35.3% | -2.2 | -14.7 | -0.018% | -0.118% | -0.60% | -4.00% | powered |
| UNLOCKED-short | 15 | 8 | 7 | 0 | 0 | 0 | 53.3% | +15.8 | +3.3 | +0.127% | +0.027% | +1.90% | +0.40% | UNDERPOWERED |

## LOCKED-long — 16 trades (UNDERPOWERED: counts only, no verdict language)

TP 5 / SL 11 / unresolved 0 / end-of-data 0; ambiguous-bar SLs: 0. TP rate of resolved: 31.2% (references: null 37.5%, fee breakeven 50.0%). Resolution time med 11.1 / p90 53.9 min; resolved inside the original 30-min window: 12, beyond: 4.

Expectancy/trade: gross -0.050%, net -0.150%. Sums over the 16 resolved trades: gross -0.80%, net -2.40%.

| ts (UTC) | bucket | entry | outcome | min to res | ambig |
|---|---|---|---|---|---|
| 06-28 17:41 | 10329 | 71.22 | TP | 13.6 |  |
| 06-29 00:36 | 11107 | 70.75 | SL | 0.7 |  |
| 06-29 16:40 | 12688 | 73.50 | TP | 7.6 |  |
| 06-29 17:31 | 12831 | 75.99 | SL | 1.0 |  |
| 06-30 04:14 | 13569 | 74.14 | SL | 2.2 |  |
| 06-30 08:49 | 14009 | 73.44 | TP | 60.2 |  |
| 06-30 14:45 | 14873 | 73.53 | SL | 0.7 |  |
| 06-30 22:45 | 15371 | 73.40 | TP | 89.2 |  |
| 07-01 06:32 | 16574 | 74.74 | SL | 0.4 |  |
| 07-01 14:31 | 17516 | 76.71 | SL | 2.3 |  |
| 07-02 00:54 | 18332 | 77.19 | TP | 17.7 |  |
| 07-02 04:51 | 18645 | 78.34 | SL | 9.2 |  |
| 07-02 12:55 | 19954 | 81.31 | SL | 33.3 |  |
| 07-03 03:20 | 20976 | 80.79 | SL | 47.6 |  |
| 07-03 11:57 | 21730 | 81.55 | SL | 19.4 |  |
| 07-03 13:47 | 21870 | 81.56 | SL | 13.0 |  |

## LOCKED-short — 7 trades (UNDERPOWERED: counts only, no verdict language)

TP 2 / SL 5 / unresolved 0 / end-of-data 0; ambiguous-bar SLs: 0. TP rate of resolved: 28.6% (references: null 37.5%, fee breakeven 50.0%). Resolution time med 4.1 / p90 28.6 min; resolved inside the original 30-min window: 6, beyond: 1.

Expectancy/trade: gross -0.071%, net -0.171%. Sums over the 7 resolved trades: gross -0.50%, net -1.20%.

| ts (UTC) | bucket | entry | outcome | min to res | ambig |
|---|---|---|---|---|---|
| 06-28 17:15 | 10274 | 71.59 | TP | 19.7 |  |
| 06-29 15:06 | 12584 | 72.84 | SL | 1.9 |  |
| 06-29 15:47 | 12640 | 73.82 | SL | 2.7 |  |
| 07-01 13:31 | 17227 | 74.99 | SL | 0.8 |  |
| 07-03 08:01 | 21281 | 81.17 | SL | 42.1 |  |
| 07-03 14:34 | 21977 | 81.19 | SL | 5.7 |  |
| 07-03 15:12 | 22071 | 81.76 | TP | 4.1 |  |

## UNLOCKED-long — 34 trades

TP 12 / SL 22 / unresolved 0 / end-of-data 0; ambiguous-bar SLs: 0. TP rate of resolved: 35.3% (references: null 37.5%, fee breakeven 50.0%). Resolution time med 11.3 / p90 45.0 min; resolved inside the original 30-min window: 26, beyond: 8.

Expectancy/trade: gross -0.018%, net -0.118%. Sums over the 34 resolved trades: gross -0.60%, net -4.00%.

| ts (UTC) | bucket | entry | outcome | min to res | ambig |
|---|---|---|---|---|---|
| 06-28 17:41 | 10329 | 71.22 | TP | 13.6 |  |
| 06-29 00:15 | 11053 | 71.40 | SL | 1.1 |  |
| 06-29 12:00 | 12046 | 72.40 | SL | 0.4 |  |
| 06-29 13:34 | 12392 | 73.99 | SL | 0.5 |  |
| 06-29 16:37 | 12685 | 73.20 | TP | 2.2 |  |
| 06-29 17:30 | 12829 | 76.01 | SL | 1.9 |  |
| 06-29 18:19 | 12874 | 75.54 | SL | 7.4 |  |
| 06-30 00:33 | 13220 | 74.55 | SL | 2.2 |  |
| 06-30 01:21 | 13340 | 74.26 | TP | 6.8 |  |
| 06-30 04:14 | 13569 | 74.14 | SL | 2.2 |  |
| 06-30 06:31 | 13742 | 74.10 | SL | 39.0 |  |
| 06-30 08:49 | 14009 | 73.44 | TP | 60.2 |  |
| 06-30 14:45 | 14873 | 73.53 | SL | 0.7 |  |
| 06-30 21:28 | 15296 | 73.29 | TP | 34.4 |  |
| 06-30 22:45 | 15371 | 73.40 | TP | 89.2 |  |
| 07-01 06:32 | 16574 | 74.74 | SL | 0.4 |  |
| 07-01 11:11 | 16994 | 74.99 | SL | 38.4 |  |
| 07-01 14:31 | 17516 | 76.71 | SL | 2.3 |  |
| 07-01 23:27 | 18205 | 77.61 | SL | 21.4 |  |
| 07-02 00:08 | 18292 | 77.35 | SL | 9.6 |  |
| 07-02 00:54 | 18332 | 77.19 | TP | 17.7 |  |
| 07-02 02:30 | 18449 | 78.09 | TP | 8.0 |  |
| 07-02 03:29 | 18526 | 78.38 | TP | 21.2 |  |
| 07-02 04:51 | 18645 | 78.34 | SL | 9.2 |  |
| 07-02 07:02 | 18800 | 77.92 | SL | 2.5 |  |
| 07-02 11:08 | 19622 | 81.87 | TP | 3.1 |  |
| 07-02 11:58 | 19823 | 82.22 | SL | 19.9 |  |
| 07-02 12:55 | 19954 | 81.31 | SL | 33.3 |  |
| 07-03 03:20 | 20976 | 80.79 | SL | 47.6 |  |
| 07-03 04:28 | 21038 | 80.57 | TP | 64.5 |  |
| 07-03 11:11 | 21671 | 81.50 | SL | 28.1 |  |
| 07-03 11:57 | 21730 | 81.55 | SL | 19.4 |  |
| 07-03 13:47 | 21870 | 81.56 | SL | 13.0 |  |
| 07-03 14:19 | 21938 | 81.03 | TP | 20.6 |  |

## UNLOCKED-short — 15 trades (UNDERPOWERED: counts only, no verdict language)

TP 8 / SL 7 / unresolved 0 / end-of-data 0; ambiguous-bar SLs: 0. TP rate of resolved: 53.3% (references: null 37.5%, fee breakeven 50.0%). Resolution time med 16.8 / p90 40.8 min; resolved inside the original 30-min window: 12, beyond: 3.

Expectancy/trade: gross +0.127%, net +0.027%. Sums over the 15 resolved trades: gross +1.90%, net +0.40%.

| ts (UTC) | bucket | entry | outcome | min to res | ambig |
|---|---|---|---|---|---|
| 06-28 15:43 | 10086 | 71.80 | SL | 16.8 |  |
| 06-28 17:15 | 10274 | 71.59 | TP | 19.7 |  |
| 06-28 23:40 | 10990 | 71.60 | TP | 19.6 |  |
| 06-29 01:08 | 11164 | 71.45 | TP | 9.2 |  |
| 06-29 15:06 | 12584 | 72.84 | SL | 1.9 |  |
| 06-29 15:47 | 12640 | 73.82 | SL | 2.7 |  |
| 06-30 14:05 | 14775 | 73.30 | SL | 17.5 |  |
| 07-01 10:21 | 16932 | 75.46 | TP | 16.6 |  |
| 07-01 11:34 | 17021 | 74.82 | TP | 38.8 |  |
| 07-01 13:31 | 17227 | 74.99 | SL | 0.8 |  |
| 07-03 06:15 | 21147 | 81.10 | TP | 23.5 |  |
| 07-03 08:01 | 21281 | 81.17 | SL | 42.1 |  |
| 07-03 13:29 | 21847 | 81.56 | TP | 44.4 |  |
| 07-03 14:34 | 21975 | 81.19 | SL | 5.7 |  |
| 07-03 15:12 | 22071 | 81.76 | TP | 4.1 |  |

## Honest flags
- Same 5.26-day tape as S5c; the barrier geometry (0.5/0.3/6h) is the program's frozen original — nothing tuned, no alternative barriers run.
- Overlapping trades share tape segments; counts are per-fire, not portfolio-independent.
- Three of four cells are under the n=20 bar: counts only, judged on forward snapshots.

## HARD STOP
Judged once. No threshold variants, no alternative barriers.
