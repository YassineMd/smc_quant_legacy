# RADAR RUNNER — CANONICAL HONEST TEST HARNESS

**Status: THE ONLY trusted way to evaluate Radar Runner tradeability. Locked 2026-08-22.**

Earlier pipelines showed **~100% prop pass**; this harness shows **0%** on the same strategy.
The difference is real money. Any Radar Runner number that was NOT produced by this harness
(or an extension of it that passes the same validation gates) is **not to be trusted for
trading or prop decisions.**

## The canonical code

`study/radarrun_30mbkt_live_full.py`  (commit 830af73; validation: `radarrun_30mbkt_live_validate.py`)

```
python study/radarrun_30mbkt_live_full.py
```

Reference result (30m bucket, Jan-2025 → 2026-06-19, as-badge scale-out bracket):
n taken 5,119 · win 82.7% · avg −0.038%/trade · equity bleeds −84% @0.4% risk · **prop first-attempt 0.0%**.

## Why the old numbers were wrong (the three failure modes, all confirmed)

1. **Batch detection repaints.** `radar_breakout_detect.detect()` over full history merges radar
   visits when later bars re-enter the radar, silently DROPPING badges the terminal actually fired
   (~⅓ of them). Fewer, cleaner signals → inflated win rate → the old ~100% pass tables.
2. **The terminal persists MORE than close-bar fires.** `app/terminal.py` ~7783: at every bar close
   it re-runs a full-history detect and freezes EVERY signal end_time not already recorded — including
   signals that appear on earlier bars as runs reshape. The true badge set is the UNION over all
   bar-closes, frozen at first appearance. Only-close-bar simulation undercounts too.
3. **Bar-level TP/SL resolution manufactures wrong outcomes.** A 30m bar spanning both levels needs
   1-minute first-touch resolution (with ties broken AGAINST the trade).

## The five gates any extension must pass (other tf / source / exit)

1. **Union persist semantics** — incremental replay, at each bar close k detect over history-to-k;
   accept every NEW (bar, side); freeze entry/SL at first appearance. Never one-shot batch.
2. **Window-stability check** — trailing window W must agree with a much larger window on a random
   bar sample (30m bucket: W=2000 ≡ W=10000, 120/120).
3. **Terminal-record reproduction** — the terminal's own `data/radarrun_fired.json` fires for that
   tf/source must come out as a SUBSET, entry/SL to the cent (30m bucket: **171/171**).
4. **1m first-touch resolution** with conservative ties; fees 0.04% RT + 0.03% slip per taker leg.
5. **Non-overlap `taken()` accounting** + day-block MC for prop FIRST-ATTEMPT pass (R0.4 sizing,
   HyroTrader 10% target / 6% trailing / 3–4% daily).

## Superseded (do NOT quote for tradeability)

- All batch-detect tables & loser lists: `radarrun_30m_losers*.py`, `radarrun_15m_losers.py`,
  `radarrun_30m_native.py` census, `radarrun_hyro_prop.py` runs on batch signals, the docx tables.
- Sparse fired-record-only stats (`radarrun_15m_losers_persisted.py`, `radarrun_honest_doc.py`) —
  faithful badges but a lumpy convenience sample of replayed months (it showed +0.067%/trade where
  the full set shows −0.038%).

Detection cache: `study/out/rr30mbkt_live_fires_union.json` (delete to re-detect, ~30 min).
