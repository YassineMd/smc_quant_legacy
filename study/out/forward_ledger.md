# FORWARD LEDGER — frozen configurations under test on forward tape

Freeze discipline: entries here are NEVER re-tuned. Each new snapshot re-runs them unchanged and
appends results; a config graduates or dies on accumulated forward sample only (n >= 20 per side).

| id | registered | fires (frozen) | entry (frozen) | exits (frozen) | fee basis | status |
|---|---|---|---|---|---|---|
| S5E-SIGDEATH | 2026-07-03 (2ec478a) | S5b locked+unlocked legs 1'-4 + EXISTS-form range context (S5d) | market at fire close | signal-death (newest marker against + own-side share < 50, AND) with hard SL -0.3, 6h cap | taker/taker 0.10 | 3/4 cells net-positive on spent tape; awaiting forward n |
| S5H-CONDROUTER | 2026-07-04 (this commit) | locked legs 1'-4 + ALL-form pullback leg 5 + cluster rule (anchor 21340 must stay rejected) | ROUTER: close beyond baseline -> MARKET at close; else WAIT 30 min for moving-baseline touch (taker) | arm A fixed TP+0.5/SL-0.3; arm B signal-death locked | taker/taker 0.10 | registered; ~1 setup/day; underpowered until forward n >= 20 |

Re-run recipe per new snapshot: study/s5e_signal_exit.py (needs the S5b/S5d chain) and
study/s5h_conditional.py (self-contained on the merged tape + sweep parquet).
