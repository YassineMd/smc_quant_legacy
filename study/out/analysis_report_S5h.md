# S5h — Conditional Baseline Entry (the routing rule, locked pullback setups)

_**Pre-registered; +2 cells -> counter 512. NO resting limit anywhere.** Router at the fire bar vs B(fire): long WAITs for the moving-line touch only when the close is above the baseline, else enters MARKET at the fire close (short mirrored). WAIT leg = S5g semantics verbatim; all entries taker, net line 0.10% RT. Same armed-setup universe as S5g (cluster rule; anchor 21340 holds); ALL-MARKET and ALL-TOUCH recomputed and parity-asserted sid-for-sid against the committed s5g_episodes.csv. **~7 setups on this tape: machinery-correctness + forward-ledger registration, NOT a verdict.**_

## 1. Router split

| side | setups | MARKET | WAIT | WAIT->touched | WAIT->untriggered | WAIT->tie/degen |
|---|---|---|---|---|---|---|
| long | 5 | 2 | 3 | 3 | 0 | 0 |
| short | 2 | 0 | 2 | 2 | 0 | 0 |

## 2. Conditional economics per side x arm (all cells n < 20 -> counts only)

| cell | n | W/L | sum | mean | net 0.10 |
|---|---|---|---|---|---|
| long-FIXED | 5 | 0/5 | -1.50% | -0.300% | -0.400% |
| long-SIGNAL | 5 | 0/5 | -0.86% | -0.171% | -0.271% |
| short-FIXED | 2 | 1/1 | +0.20% | +0.100% | +0.000% |
| short-SIGNAL | 2 | 2/0 | +0.55% | +0.274% | +0.174% |

## 3. The comparison — same setups, three entry strategies (pnl % gross; * = ambig flag)

| sid | side | route | MKT fix | MKT sig | TOUCH fix | TOUCH sig | COND fix | COND sig |
|---|---|---|---|---|---|---|---|---|
| 1 | long | WAIT | +0.500 | +0.154 | -0.300 | -0.146 | -0.300 | -0.146 |
| 2 | long | WAIT | -0.300 | -0.300 | -0.300 | -0.300 | -0.300 | -0.300 |
| 3 | long | MARKET | -0.300 | -0.300 | DEGENER | DEGENER | -0.300 | -0.300 |
| 4 | long | WAIT | -0.300 | -0.300 | -0.300 | -0.062 | -0.300 | -0.062 |
| 5 | long | MARKET | -0.300 | -0.049 | -0.300 | -0.122 | -0.300 | -0.049 |
| 6 | short | WAIT | +0.500 | +0.419 | +0.500 | +0.434 | +0.500 | +0.434 |
| 7 | short | WAIT | -0.300 | +0.025 | -0.300 | +0.113 | -0.300 | +0.113 |
| **sum FIXED** | | | -0.50% (n=7) | | -1.00% (n=6) | | -1.30% (n=7) | |
| **sum SIGNAL** | | | -0.35% (n=7) | | -0.08% (n=6) | | -0.31% (n=7) | |

## 4-5. Registration
The CONDITIONAL router (this exact rule, both arms, no parameter changes ever) is now a frozen entry in the forward ledger beside S5e — see study/out/forward_ledger.md. Underpowered by construction on this tape; the ledger accumulates forward setups (~1/day) until n >= 20 per side.

## HARD STOP
Judged once; forward tape is the judge.
