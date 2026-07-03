# S5f — Limit-at-POC entry (LOCKED, corrected pullback context)

_**Pre-registered; +4 cells (side x arm) -> counter 506. Corrected leg 5 = ALL-form pullback (C below the zone's MIN open for longs) — the EXISTS form is retired; ties excluded (long 82 / short 92 in-universe). Fires re-detected (locked legs 1'-4 + corrected leg 5), each simulated independently — resting limits may overlap in time. Touch-fill OPTIMISM flag: a bar-low touch is treated as a maker fill; real queues fill later or never. Limit = the terminal POC baseline (`_bucket_row` 5%/95% POC EMA, carried verbatim, full-tape seed). Fees: net at maker/taker 0.065% RT and taker/taker 0.10% RT._

## 0. Post-mortem — idx 21340 (permanent leg-5 regression anchor)

C(21340) = 81.43. S5d's EXISTS form was satisfied by N = 73: open(b-N+1) = open(idx 21268) = 81.44 > close — ONE old open above the close was enough, even though the zone's MINIMUM open is 81.11, i.e. the zone as a whole did NOT read net-down. Corrected ALL form: C = 81.43 >= min open 81.11 -> **long-ineligible. Rejected.**

Sweep check: locked legs 1'-4 fired at 21340 and S5d's leg 5w passed it (it became an S5d locked episode); the corrected leg kills it. Asserted in code — any future change to leg 5 must keep rejecting this bar.

## 1. Funnel (re-detected fires vs S5d-locked)

| side | S5d fires (EXISTS) | corrected fires (ALL) | killed | new | degenerate | cancelled | FILLED |
|---|---|---|---|---|---|---|---|
| long | 65 | 14 | 51 | 0 | 3 | 7 | 4 |
| short | 15 | 3 | 12 | 0 | 0 | 0 | 3 |

## 2. Time-to-fill (filled fires)

- long: n=4, median 1.9 min, p90 3.0 min.
- short: n=3, median 0.0 min, p90 0.4 min.

## 3. Per side x arm (entry = limit price)

| cell | n | W/L | avgW | avgL | exp gross | net 0.065 | net 0.10 | med hold (min) |
|---|---|---|---|---|---|---|---|---|
| long-FIXED _(under)_ | 4 | 0/4 | +0.000 | -0.300 | -0.300% | -0.365% | -0.400% | 30.1 |
| long-SIGNAL _(under)_ | 4 | 0/4 | +0.000 | -0.112 | -0.112% | -0.177% | -0.212% | 15.5 |
| short-FIXED _(under)_ | 3 | 1/2 | +0.500 | -0.300 | -0.033% | -0.098% | -0.133% | 41.6 |
| short-SIGNAL _(under)_ | 3 | 3/0 | +0.223 | +0.000 | +0.223% | +0.158% | +0.123% | 8.4 |

## 4. Unfilled counterfactual (30-min from fire close)

- long cancelled n=7: the missed move — median MFE 0.603% / median MAE -0.042%.
- short cancelled n=0: the missed move — median MFE -% / median MAE -%.

## 5. Head-to-head vs the locked market-entry cells (net at taker/taker 0.10)
_Imperfect comparison — S5d/S5e ran on the EXISTS-form fire set; this study's set is the corrected strict subset._

| side | S5f FIXED | S5f SIGNAL | S5d grid TP0.5/SL0.3 | S5e signal-death |
|---|---|---|---|---|
| long | -0.400% | -0.212% | -0.031% | +0.030% |
| short | -0.133% | +0.123% | -0.200% | -0.109% |

## Honest flags
- Touch-fill optimism: every fill here assumes the resting order trades on a touch.
- The corrected context is strict; small n everywhere -> counts only below n=20.
- Arm A keeps S1 cap semantics (unresolved excluded), arm B keeps S5e's (cap close realized) — per their reference studies; stated.
- Spent tape; forward snapshots are the judge.

## HARD STOP
No re-tuning; one limit rule, two pre-registered arms.
