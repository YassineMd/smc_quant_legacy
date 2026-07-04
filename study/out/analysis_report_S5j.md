# S5j — Final Leg-5 (EXISTS = S5d leg5w) + S5i machinery (LOCKED)

_**Pre-registered; +2 cells -> counter 516. Supersedes S5i (its priority-scan encoding was an architect error). Leg 5 = S5d's leg5w EXISTS form, code reused verbatim (rolling zone max/min); both-eligible bars are legal, legs 1'-4 pick the side. Everything else identical to S5i: share-form leg 2 (>= 65%), 1h windows + fire-search blackout (126 fires absorbed), moving-baseline router (no self-touch), taker 0.10% net, fixed TP+0.5/SL-0.3 exits (S1, * = ambiguous). References: 37.5% null / 50.0% breakeven. Underpowered: n < 20 -> counts only._

## 1. Funnel (deltas vs S5i and S5d-locked)

| side | fire bars | vs S5i | vs S5d-locked | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 133 | +55 / -0 (n=78) | +68 / -0 (n=65) | 35 | 10 | 25 | 25 | 0 |
| short | 40 | +14 / -0 (n=26) | +25 / -0 (n=15) | 12 | 4 | 8 | 8 | 0 |

## 2. Economics per side (fixed exit; filled entries)

| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 35 | 13/22/0 | 37.1% | -0.4 | -12.9 | -0.003% | -0.103% | 11.0 | 3.1/18.6 |
| short _(under)_ | 12 | 7/5/0 | 58.3% | +20.8 | +8.3 | +0.167% | +0.067% | 20.0 | 2.6/8.5 |

## 3. 1h excursions from fire close (raw extremes), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 10 | MKT | +0.684 | +0.337/+1.249 | -0.392 | -0.516/-0.194 |
| long | 25 | TOUCH | +0.333 | +0.108/+0.640 | -0.446 | -0.734/-0.246 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 4 | MKT | +0.344 | +0.224/+1.053 | -0.985 | -1.110/-0.686 |
| short | 8 | TOUCH | +0.492 | +0.229/+1.055 | -0.352 | -0.519/-0.117 |
| short | 0 | CANCELLED | - | - | - | - |

## Honest flags
- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the program — the fire set is broad by design.
- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees are the honesty floor.
- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
