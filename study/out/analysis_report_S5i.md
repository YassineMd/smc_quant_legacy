# S5i — Corrected Confluence + Router Entry + Fixed Exit (LOCKED)

_**Pre-registered; +2 cells -> counter 514. Supersedes the S5f/g/h fire sets: leg 5 is now a PRIORITY SCAN (N = 50..100, first decisive composite wins, one side per bar). Leg 2 is the SHARE form as mandated (bull share >= 65% == locked badge spread >= 30 — looser than the S5b-r alert's spread >= 65; stated here so the translation is on the record). Legs 1/3/4 = the committed sweep columns (locked markers / phase / P6 spread). Fire windows are 1h with a fire-search BLACKOUT (69 fires absorbed); trades may outlive their window. Router: at-or-through the baseline -> MARKET at fire close, else WAIT the hour for a touch of the MOVING baseline (no fire-bar self-touch, per spec). All taker; net 0.10% RT. Exit fixed TP+0.5/SL-0.3 only (S1 conventions, * = ambiguous flag). Underpowered rule: n < 20/side -> counts only._

## 1. Funnel

| side | fire bars | vs S5d-locked (EXISTS) | vs S5f/g/h (ALL) | episodes | MKT | WAIT | touched | CANCELLED |
|---|---|---|---|---|---|---|---|---|
| long | 78 | +47 regained / -34 lost (n=65) | +64 / -0 (n=14) | 22 | 10 | 12 | 12 | 0 |
| short | 26 | +16 regained / -5 lost (n=15) | +23 / -0 (n=3) | 13 | 6 | 7 | 7 | 0 |

## 2. Economics per side (fixed exit; filled entries only)

| side | n | TP/SL/unres | TP rate (res) | avgW | avgL | exp gross | exp net | med hold | touch delay med/p90 |
|---|---|---|---|---|---|---|---|---|---|
| long | 22 | 8/14/0 | 36.4% | +0.500 | -0.300 | -0.009% | -0.109% | 11.4 | 0.2/3.0 |
| short _(under)_ | 13 | 8/5/0 | 61.5% | +0.500 | -0.300 | +0.192% | +0.092% | 17.5 | 0.5/2.2 |

## 3. 1h excursions from fire close (raw extremes), split by route

| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |
|---|---|---|---|---|---|---|
| long | 10 | MKT | +0.684 | +0.337/+1.249 | -0.392 | -0.516/-0.194 |
| long | 12 | TOUCH | +0.190 | +0.107/+0.420 | -0.494 | -0.735/-0.234 |
| long | 0 | CANCELLED | - | - | - | - |
| short | 6 | MKT | +0.294 | +0.189/+0.667 | -0.899 | -1.057/-0.774 |
| short | 7 | TOUCH | +0.407 | +0.204/+0.769 | -0.542 | -0.815/-0.315 |
| short | 0 | CANCELLED | - | - | - | - |

## Honest flags
- Leg 2 share-form is the mandated translation; it is looser than the terminal alert's spread-65 rule — the S5i fire set is NOT comparable 1:1 to S5b-r locked cells.
- Touch entries assume a fill on a bar-low touch of the moving line (taker at the line; no slippage).
- Spent tape; underpowered cells are counts only; the fire-search blackout makes episodes disjoint by construction but trades may overlap beyond the hour.

## HARD STOP
Judged once; forward snapshots are the judge.
