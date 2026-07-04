# S5j-r3 — Fully-Locked Confluence, operator leg updates (100-bar P0, two-sided phase, zone 60-100)

_**S5j-r3 updates (operator, 2026-07-04): leg 1'' now reads the P0 markers on a 100-BAR selection [b-99, b] (was 16) — same rule: two most recent LOCKED markers on-side, the newest must be the on-side EXTREME cross, dots never count. Leg 3 is TWO-SIDED: the own-side table's dominant phase == START/DURING AND the opposite table's dominant phase != START/DURING. Leg 5 zone narrowed to N = 60..100 (EXISTS form unchanged). Leg 2 stays the LOCKED BADGE SPREAD >= 65 (share >= 82.5%). Regression anchors asserted: 20977 passes legs 1-4; 14873 fails leg 1'' (its +50 cross was a dot); 14876 fails leg 2 (spread +61.9). Multiplicity +2 -> counter 522.** 1h windows + fire-search blackout (168 fires absorbed); moving-baseline router (no self-touch); taker 0.10% net; fixed TP+0.5/SL-0.3 exits (S1, * = ambiguous). References: 37.5% null / 50.0% breakeven. Underpowered: n < 20 -> counts only._

## 1. Funnel (deltas vs S5j-r2 and S5d-locked)

| side | fire bars | vs S5j-r2 | vs S5d-locked | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 147 | +100 / -1 (n=48) | +100 / -18 (n=65) | 30 | 1 | 29 | 29 | 0 |
| short | 75 | +62 / -1 (n=14) | +62 / -2 (n=15) | 24 | 1 | 23 | 22 | 1 |

## 2. Economics per side (fixed exit; filled entries)

| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 30 | 11/19/0 | 36.7% | -0.8 | -13.3 | -0.007% | -0.107% | 11.1 | 5.6/31.2 |
| short | 23 | 12/11/0 | 52.2% | +14.7 | +2.2 | +0.117% | +0.017% | 16.9 | 5.0/19.5 |

## 3. 1h excursions from fire close (raw extremes), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 1 | MKT | +0.812 | +0.812/+0.812 | -0.787 | -0.787/-0.787 |
| long | 29 | TOUCH | +0.490 | +0.198/+1.546 | -0.384 | -0.564/-0.175 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 1 | MKT | +0.376 | +0.376/+0.376 | -0.599 | -0.599/-0.599 |
| short | 22 | TOUCH | +0.430 | +0.221/+0.623 | -0.497 | -0.736/-0.141 |
| short | 1 | CANCELLED | +0.110 | +0.110/+0.110 | -1.167 | -1.167/-1.167 |

## Honest flags
- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the program — the fire set is broad by design.
- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees are the honesty floor.
- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
