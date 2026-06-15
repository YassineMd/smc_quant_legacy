# Phase-5 test fixtures — FROZEN raw-tick tape

`aggtrade_tape.jsonl` is the **immutable** raw-tick fixture the Phase-5 (aggTrade)
gates replay. Freeze discipline is the same as `data/history.db.before-fixes`:
**never overwrite it.** Every gate below depends on it being present and unchanged,
so its reproducibility is the point.

- **Captured by** `scripts/capture_aggtrade.py` (the 19.0 throwaway recorder),
  SOLUSDT USD-M perps, 2026-06-15, a 300 s window.
- **Format:** one JSON object per line — `{"type": ..., "recv": <local epoch s>,
  "data": {<raw exchange payload>}}`; `type` ∈ `aggTrade` | `kline` | `oi`.
- **Contents:** 904 aggTrades · 2710 klines (542 × 5 tf) · 54 OI polls (≈1.14 MB).
  Quiet session (mean 3.2 tr/s, peak 11); OI moved both ways (20 up / 31 down).

**Drives:** 19.1 (trade→args), 19.2-normal (OI bleed conservation), 19.3 (hot-path
latency replay, accelerated), 19.6 (kline-vs-aggTrade side-by-side). The stress
cases — sustained OI expansion for the cap/lag (19.2) and a dense trade burst for
latency (19.3) — are **synthetic stressors dialed past worst case**, not this tape.

**To regenerate** a *new* tape, run the recorder (it writes a timestamped file under
`data/`, gitignored) — do not clobber this frozen one.

## `aggtrade_tape_active.jsonl` — active-flow companion (for the 19.6 sign-off)

Same format + freeze discipline, captured 2026-06-15 during a **moving** market
(SOL ~73.9, ~1% range over the surrounding 30 min): **980 aggTrades · 2556 klines ·
43 OI polls** over ~4 min, price traveling **73.89 → 73.65 across 25 distinct
tick-levels** (vs the quiet tape's 22), mean ~4 tr/s / peak 17. The quiet tape
proves correctness; this one shows the fidelity gain where price *travels within
candles* — the dramatic before/after for 19.6 (kline-smeared-onto-close vs aggTrade
true-price levels). **Frozen — do not overwrite.**
