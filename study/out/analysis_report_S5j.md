# S5j-r5 — Fully-Locked Confluence + color-qualified close-entries (corrected)

_**S5j-r5 (operator correction of r4's entry translation): the bar COLOR is the only close requirement — a bullish bar UNDER the baseline is a valid long entry, a bearish bar ABOVE it a valid short entry; close-vs-baseline does not matter (r4 wrongly required the close beyond the line). WAIT entry bar (long) = first bar touching/at the moving baseline (low <= line) that closes bullish; short mirrored. Every entry still executes AT THE BAR CLOSE; a MKT-route fire bar failing the color test falls through to the WAIT scan. w_max/w_min stay ENTRY-referenced (CANCELLED rows keep the fire-close counterfactual). Legs unchanged from r3 (100-bar leg 1'', spread-65 leg 2, two-sided leg 3, zone 60-100); anchors hold (20977 / 14873 / 14876). Multiplicity +2 -> counter 526.** 1h windows + fire-search blackout (168 fires absorbed); taker 0.10% net; fixed TP+0.5/SL-0.3 exits from the entry close (S1, * = ambiguous). References: 37.5% null / 50.0% breakeven. Underpowered: n < 20 -> counts only._

## 1. Funnel (deltas vs S5j-r2 and S5d-locked)

| side | fire bars | vs S5j-r2 | vs S5d-locked | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 147 | +100 / -1 (n=48) | +100 / -18 (n=65) | 30 | 0 | 30 | 30 | 0 |
| short | 75 | +62 / -1 (n=14) | +62 / -2 (n=15) | 24 | 0 | 24 | 23 | 1 |

## 2. Economics per side (fixed exit; filled entries)

| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 30 | 13/17/0 | 43.3% | +5.8 | -6.7 | +0.047% | -0.053% | 9.7 | 8.4/35.3 |
| short | 23 | 14/9/0 | 60.9% | +23.4 | +10.9 | +0.187% | +0.087% | 12.7 | 7.8/22.0 |

## 3. 1h excursions (ENTRY-referenced for filled rows; CANCELLED = fire-close counterfactual), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 0 | MKT | - | - | - | - |
| long | 30 | TOUCH | +0.393 | +0.233/+1.597 | -0.457 | -0.700/-0.162 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 0 | MKT | - | - | - | - |
| short | 23 | TOUCH | +0.306 | +0.178/+0.570 | -0.690 | -1.102/-0.192 |
| short | 1 | CANCELLED | +0.110 | +0.110/+0.110 | -1.167 | -1.167/-1.167 |

## 4. Leg-5 CONTEXT zone range (bars b-99..b-59 = N 60..100), % from fire close
_The reach of the context window that qualified each fire: highest high above / lowest low below the fire close. Split by episode outcome to see whether a bigger prior swing precedes a better result._

| side | outcome | n | med zone HIGH % | med zone LOW % | med zone RANGE % |
|---|---|---|---|---|---|
| long | TP | 13 | +0.309 | -0.186 | 0.558 |
| long | SL | 17 | +0.269 | -0.456 | 0.980 |
| long | all filled | 30 | +0.288 | -0.347 | 0.723 |
| short | TP | 14 | +0.310 | -0.309 | 0.648 |
| short | SL | 9 | +0.414 | -0.297 | 0.872 |
| short | all filled | 23 | +0.321 | -0.297 | 0.773 |

## Honest flags
- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the program — the fire set is broad by design.
- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees are the honesty floor.
- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
