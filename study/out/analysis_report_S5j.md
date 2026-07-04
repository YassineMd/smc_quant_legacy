# S5j-r2 — Fully-Locked Confluence (legs 1'' + 2 corrected) + EXISTS leg 5

_**S5j-r2: the second operator correction. Leg 1'' (from 14873): two most recent LOCKED markers on-side AND the newest is the on-side EXTREME cross — settling dots never count. Leg 2 (from 14876): the LOCKED BADGE SPREAD >= 65 points (share >= 82.5%), the terminal alert rule — the S5i 'share >= 65%' wording was a second mistranslation. Regression anchors asserted in code: 20977 PASSES all legs; 14873 FAILS leg 1''; 14876 FAILS leg 2 (spread +61.9). Multiplicity +2 -> counter 520.** Legs 3/4 = locked phase row; leg 5 = S5d leg5w EXISTS verbatim; 1h windows + fire-search blackout (37 fires absorbed); moving-baseline router (no self-touch); taker 0.10% net; fixed TP+0.5/SL-0.3 exits (S1, * = ambiguous). References: 37.5% null / 50.0% breakeven. Underpowered: n < 20 -> counts only._

## 1. Funnel (deltas vs S5j-r1 and S5d-locked)

| side | fire bars | vs S5j-r1 | vs S5d-locked | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 48 | +0 / -35 (n=83) | +0 / -17 (n=65) | 20 | 1 | 19 | 19 | 0 |
| short | 14 | +0 / -25 (n=39) | +0 / -1 (n=15) | 5 | 1 | 4 | 4 | 0 |

## 2. Economics per side (fixed exit; filled entries)

| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 20 | 5/15/0 | 25.0% | -12.5 | -25.0 | -0.100% | -0.200% | 13.9 | 8.1/29.1 |
| short _(under)_ | 5 | 2/3/0 | 40.0% | +2.5 | -10.0 | +0.020% | -0.080% | 20.3 | 4.6/29.1 |

## 3. 1h excursions from fire close (raw extremes), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 1 | MKT | +0.812 | +0.812/+0.812 | -0.787 | -0.787/-0.787 |
| long | 19 | TOUCH | +0.603 | +0.258/+1.464 | -0.382 | -0.623/-0.205 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 1 | MKT | +3.027 | +3.027/+3.027 | -0.133 | -0.133/-0.133 |
| short | 4 | TOUCH | +0.327 | +0.111/+0.914 | -0.296 | -0.581/-0.106 |
| short | 0 | CANCELLED | - | - | - | - |

## Honest flags
- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the program — the fire set is broad by design.
- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees are the honesty floor.
- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
