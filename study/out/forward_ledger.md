# FORWARD LEDGER — frozen configurations under test on forward tape

Freeze discipline: entries here are NEVER re-tuned. Each new snapshot re-runs them unchanged and
appends results; a config graduates or dies on accumulated forward sample only (n >= 20 per side).

| id | registered | fires (frozen) | entry (frozen) | exits (frozen) | fee basis | status |
|---|---|---|---|---|---|---|
| S5E-SIGDEATH | 2026-07-03 (2ec478a) | S5b locked+unlocked legs 1'-4 + EXISTS-form range context (S5d) | market at fire close | signal-death (newest marker against + own-side share < 50, AND) with hard SL -0.3, 6h cap | taker/taker 0.10 | 3/4 cells net-positive on spent tape; awaiting forward n |
| S5H-CONDROUTER | 2026-07-04 (this commit) | locked legs 1'-4 + ALL-form pullback leg 5 + cluster rule (anchor 21340 must stay rejected) | ROUTER: close beyond baseline -> MARKET at close; else WAIT 30 min for moving-baseline touch (taker) | arm A fixed TP+0.5/SL-0.3; arm B signal-death locked | taker/taker 0.10 | registered; ~1 setup/day; underpowered until forward n >= 20 |
| PIVOT-P2HELD | 2026-07-06 (this commit) | S5j-r5 confluence via app/pivot_detect (the SHIPPED indicator), INDEPENDENT per-side sequential walk | WAIT-baseline-touch, taken ONLY IF the ALIGNED live eff-agg (panel-2) spread @E > 0 AND its min over [D,E] > -50 (`p2_live_at_e`>0 & `p2_live_min_de`>-50 in the CSV) | fixed TP+0.5/SL-0.3, 6h cap | taker/taker 0.10 | in-sample (Jul2-5, n=116) KEEP 47 TP 63.8% net +0.117% vs 44.0% base, Fisher p=0.0003; the STRONG candidate; awaiting forward n>=20/side |
| PIVOT-ABSORB-E | 2026-07-06 (this commit) | S5j-r5 confluence via app/pivot_detect, INDEPENDENT per-side sequential walk | WAIT-baseline-touch, taken ONLY IF the entry bar is an ALIGNED absorption candle (long: sell-led `sv>bv` & closed up; short: buy-led & closed down; `entry_absorption`==1 in the CSV) | fixed TP+0.5/SL-0.3, 6h cap | taker/taker 0.10 | in-sample KEEP 41 TP 48.8% vs 44.0% base, Fisher p=0.56 (DIRECTIONAL only, underpowered); awaiting forward n |
| PIVOT-4HZONE | 2026-07-06 (this commit) | S5j-r5 via app/pivot_detect, independent per-side walk | WAIT-baseline-touch, taken ONLY IF the pivot's DETECTION price sits in the last-COMPLETED 240x/4h buy/sell zone: buy -> lower/buyer wick (price <= vq_lo), sell -> upper/seller wick (price >= vq_hi); zones = `bar_quantiles.vq()` of the tf='4h' stored buckets (== the live SSH 240x stream) | fixed TP+0.5/SL-0.3, 6h cap | taker/taker 0.10 | in-sample (Jun28-Jul5, n=116) IN-ZONE 40 TP 62.5% vs OUT 34.2%, **Fisher p=0.006, both-sided (L 60/34, S 65/34)**; INDEPENDENT source (4h vol profile, not 1m eff-agg) -> the strongest candidate besides P2-held; awaiting forward n>=20/side |
| PIVOT-E2-TIER | 2026-07-07 (this commit) | S5j-r5 confluence via app/pivot_detect, INDEPENDENT per-side walk | TIERED by D fill = aligned LIVE panel-2 spread at D (p2d): **cyan/orange** (p2d>80) -> **E2**; **green/red** (63<p2d<=80) -> **E2**; **hollow** (p2d<=63) -> **E if panel-2 HELD** (aligned live spread @E>0 AND min over [D,E]>-50) **ELSE E2**. E2 = flip-rescue: E greyed (@E<=0 or min[D,E]<=-50) then first bar within 1h of E whose aligned live spread re-confirms >= 30 | fixed TP+0.5/SL-0.3, 6h cap | taker/taker 0.10 | in-sample (Jul2-6, 1m, n=106) TP 66.0% net **+0.128%** vs raw -0.052%; **POST-HOC** (63/80 tiers + E2thr=30 + per-tier entry picks ALL fit on this tape) -> heavily optimistic; forward tape is the ONLY test; awaiting forward n>=20/side |

Re-run recipe per new snapshot: study/s5e_signal_exit.py (needs the S5b/S5d chain);
study/s5h_conditional.py (self-contained on the merged tape + sweep parquet);
study/pivot_backtest.py -> study/out/pivot_backtest_episodes.csv, then filter its rows: PIVOT-P2HELD =
`p2_live_at_e`>0 AND `p2_live_min_de`>-50; PIVOT-ABSORB-E = `entry_absorption`==1 (both vs the unfiltered
TP rate, over MKT/TOUCH rows only). pivot_backtest auto-globs study/data/history_snapshot_*.db so a fresh
pull is picked up with no edits. PIVOT-4HZONE = `python study/pivot_4hzone.py` (reads the tf='4h' buckets from
the newest snapshot for the 240x zones + the CSV for outcomes). PIVOT-E2-TIER = `python study/pivot_entry_timing.py`
(the STRATEGY line: cyan/orange->E2, green/red->E2, hollow->E-held+E2; reads the merged snapshot tape). NOTE the
1m aged June 22 off the 10k cap, so the forward window starts wherever the freshest snapshot's 1m begins.
