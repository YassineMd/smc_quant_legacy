# HONEST STRATEGY TEST PROMPT (paste at the start of ANY strategy test)

Born 2026-08-22 after the Radar Runner 30m-bucket tables showed 90%+ win / ~100% prop pass
where the honest answer was 82.7% / 0%. See RADARRUN_CANONICAL_TEST.md for the autopsy.

HONEST STRATEGY TEST — apply ALL gates, refuse to report numbers that skip any:

1. SIGNALS = exactly what the terminal shows. Replay detection bar-by-bar with only the
   history known at each close, and keep every signal that EVER appears (union persist
   semantics). Never one-shot batch detection. Validate: the terminal's own persisted
   record for that tf/source must reproduce as a subset, entry/SL to the cent.
2. FULL PERIOD, both years, reported separately (2025 / 2026). No convenience samples.
   Print month-density of signals so coverage is visible.
3. RESOLVE at 1-minute first-touch. Ambiguous bars resolve AGAINST the trade.
   Fees + slippage on every taker leg. Limit entries: no same-bar TP credit.
4. NON-OVERLAP taken() accounting only — one account, one trade at a time.
5. CAUSAL: every filter/feature uses only data known at entry. State the look-ahead check run.
6. REPORT: n total / n winners / n losers / win% / avg trade net / avg R / historical DD at
   the user's sizing / prop FIRST-ATTEMPT pass% (HyroTrader $200k, R0.4, day-block MC).
   Classify W / BE / L on NET.
7. TOO-GOOD ALARM: any win% > 90% or prop pass > 95% is a BUG until proven otherwise.
   Stop, find the flaw, show the proof before reporting it.
8. Name the harness/script that produced every number. If a result contradicts an older
   one, the older one is presumed wrong until re-run under these gates.
9. Verdict in one line: TRADEABLE / NOT — and what would change it.

Reference implementation of gates 1-4, 6: study/radarrun_30mbkt_live_full.py (+ _validate.py).
