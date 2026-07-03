# S5g — Baseline-Touch Entry (locked, corrected pullback; moving line)

_**Pre-registered correction of S5f's entry mistranslation — a touch TRIGGER on the MOVING baseline, taker fill at the line, not a resting maker limit frozen at fire. +4 cells -> counter 510.** Fire set identical to S5f (anchor 21340 asserted rejected); NEW cluster rule: fires inside an armed 30-min window collapse to one setup. Primary net line = taker/taker 0.10% RT (touch = market entry). Fire-bar self-touch is allowed per the operator rule and is OPTIMISTIC (the touch precedes the close-time fire) — flagged. Setups simulated independently._

## 1. Funnel

| side | raw fires | armed setups | touched | untriggered | tie/degenerate |
|---|---|---|---|---|---|
| long | 14 | 5 | 4 | 0 | 1 |
| short | 3 | 2 | 2 | 0 | 0 |

## 2. Touch delay
n=6 touched setups: median 0.2 min, p90 15.6 min, fire-bar self-touches: 3.

## 3. Economics per side x arm (touch entries; gross and net 0.10)

| cell | n | W/L | avgW | avgL | exp gross | exp net | med hold |
|---|---|---|---|---|---|---|---|
| long-FIXED _(under)_ | 4 | 0/4 | +0.000 | -0.300 | -0.300% | -0.400% | 23.1 |
| long-SIGNAL _(under)_ | 4 | 0/4 | +0.000 | -0.157 | -0.157% | -0.257% | 8.8 |
| short-FIXED _(under)_ | 2 | 1/1 | +0.500 | -0.300 | +0.100% | +0.000% | 31.4 |
| short-SIGNAL _(under)_ | 2 | 2/0 | +0.274 | +0.000 | +0.274% | +0.174% | 13.3 |

## 4. Untriggered counterfactual (30-min from fire close)

| sid | side | fire bucket | cf MFE % | cf MAE % |
|---|---|---|---|---|

## 5. Head-to-head — one fire set, three entry styles (net 0.10 taker/taker)
_S5f limit ran per RAW fire (uncollapsed) on the same corrected fire set — caveat._

| side | arm | S5f resting limit | S5g touch | market-at-fire |
|---|---|---|---|---|
| long | FIXED | -0.400% | -0.400% | -0.240% |
| long | SIGNAL | -0.212% | -0.257% | -0.259% |
| short | FIXED | -0.133% | +0.000% | +0.000% |
| short | SIGNAL | +0.123% | +0.174% | +0.122% |

## Honest flags
- All cells rest on single-digit setups -> counts only (n < 20) throughout; no verdict language anywhere in this study.
- Fire-bar self-touch entries are optimistic (touch precedes the fire's close-time confirmation); market-at-fire has no such issue (entry at the close).
- No slippage on the touch fill; the taker fee line is the honesty floor.
- Spent tape; the corrected-pullback fire set is ~1 armed setup/day — forward accumulation is slow by construction.

## HARD STOP
One trigger rule, two pre-registered arms, one comparison table.
