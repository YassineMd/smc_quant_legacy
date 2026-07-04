# S5j-r4 — Fully-Locked Confluence + qualified close-entries (bar color rule)

_**S5j-r4 updates (operator, 2026-07-04): (1) w_max/w_min are now ENTRY-referenced — % from the entry price over 1h from the entry bar (CANCELLED rows keep the fire-close counterfactual). (2) Entry qualification: the entry bar must close BULLISH for longs / BEARISH for shorts, and every entry executes AT THE BAR CLOSE (translation on the record: the color is a close property, so the operator's 'enter the close' is applied to all entries; a MKT-route fire bar that fails the color test FALLS THROUGH to the WAIT scan rather than dying). WAIT entry bar (long) = touches the moving baseline AND closes above BOTH its open and the baseline — one condition covering the normal pullback and the reclaim exception (open below the line, close above); short mirrored. Legs unchanged from r3 (100-bar leg 1'', spread-65 leg 2, two-sided leg 3, zone 60-100); anchors hold (20977 / 14873 / 14876). Multiplicity +2 -> counter 524.** 1h windows + fire-search blackout (168 fires absorbed); taker 0.10% net; fixed TP+0.5/SL-0.3 exits from the entry close (S1, * = ambiguous). References: 37.5% null / 50.0% breakeven. Underpowered: n < 20 -> counts only._

## 1. Funnel (deltas vs S5j-r2 and S5d-locked)

| side | fire bars | vs S5j-r2 | vs S5d-locked | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 147 | +100 / -1 (n=48) | +100 / -18 (n=65) | 30 | 0 | 30 | 30 | 0 |
| short | 75 | +62 / -1 (n=14) | +62 / -2 (n=15) | 24 | 0 | 24 | 23 | 1 |

## 2. Economics per side (fixed exit; filled entries)

| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 30 | 13/17/0 | 43.3% | +5.8 | -6.7 | +0.047% | -0.053% | 12.3 | 17.5/36.4 |
| short | 23 | 13/10/0 | 56.5% | +19.0 | +6.5 | +0.152% | +0.052% | 12.7 | 9.4/29.0 |

## 3. 1h excursions (ENTRY-referenced for filled rows; CANCELLED = fire-close counterfactual), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 0 | MKT | - | - | - | - |
| long | 30 | TOUCH | +0.548 | +0.247/+1.543 | -0.447 | -0.673/-0.193 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 0 | MKT | - | - | - | - |
| short | 23 | TOUCH | +0.338 | +0.207/+0.543 | -0.601 | -1.062/-0.225 |
| short | 1 | CANCELLED | +0.110 | +0.110/+0.110 | -1.167 | -1.167/-1.167 |

## Honest flags
- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the program — the fire set is broad by design.
- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees are the honesty floor.
- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
