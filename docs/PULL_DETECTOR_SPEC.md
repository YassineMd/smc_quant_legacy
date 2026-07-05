# Liquidity PULL detector — design spec (Phase 3)

**Status: SPEC ONLY. No code until 72h of depth has accumulated in `depth.db`.** The retention bump
(6h → 72h, commit `ef85cac`) was deployed on 2026-07-05; the tape (`trade_tape`) and the book
(`depth_snapshots` + `depth_deltas`) must hold a continuous ≥72h window before any of this is built.
Building on a 6h store would blind the wall-stability and vanish tests. **Gate: `depth_store.stats()`
reports ≥ 72h span on both `depth_snapshots` and `trade_tape` → then build, one commit per stage.**

A **sweep** (Phase 1/2, already live) harvests resting stops with a wick. A **pull** is the other half
of the same manipulation grammar: a large resting wall is **cancelled** (not consumed) as price
approaches, so the path it was "defending" opens up and price runs. Detecting it needs the book *and*
the tape over time — exactly what the 72h depth store now provides and the sweep detector did not use.

## Data sources (all in `depth.db`, daemon-owned, read-only here)
- `depth_snapshots(ts_ms, u, mid, bids, asks)` — full-book anchors every `DEPTH_SNAPSHOT_SECS` (30s).
- `depth_deltas(u, ts_ms, changes)` — per-update level changes between anchors (rebuild exact book at t).
- `trade_tape(a, ts_ms, price, qty, side)` — every aggTrade, no sampling (the "printing" test's ground truth).
- Bucket history (`history.db`) for pivots (k=5) and the VP edges, same as the sweep detector.

## The five stages (all required for a Tier-A pull; a partial set = Tier-B decoy, as with sweeps)

**1. WALL TRACKING.** A *wall* = a resting level whose size is in the **top decile** of book size at that
time, **stable for ≥ 10 minutes** (present, near-constant size, across the snapshot+delta reconstruction),
and located **at or near a k=5 pivot or a volume-profile edge** (VAH/VAL/POC of the recent session). Track
each qualifying wall as a time series: `(price, side {bid/ask}, size(t), first_seen, last_seen)`.

**2. VANISH-WITHOUT-PRINTING.** The wall's size **drops sharply** (e.g. ≥ 70% of its tracked size removed
within one snapshot interval) **while `trade_tape` shows no matching executions at that price** over the
same window. Depth removed + no prints = **cancelled/pulled**, not filled. This is the core spoof
signature and the stage that was impossible pre-72h (needs the book delta AND the tape aligned in time).
Guardrail: a wall consumed by real trades (prints ≈ size removed) is NOT a pull — it's genuine absorption.

**3. THIN-AGGRESSION TRAVERSE.** Immediately after the pull, price **traverses the vacated level on thin
aggression** — the volume needed to move through it is a small fraction of that level's former wall size
(the thing that was "holding" is gone, so little effort moves price through). Measure: executed volume at/
through the level in the traverse window ÷ the pulled wall size < a small threshold (mirror the sweep's
10% vacuum cutoff; calibrate).

**4. FAR-SIDE FORCED-FLOW BURST.** The pull-driven move triggers a **forced-flow burst on the FAR side** —
the same `clS`/`clL` (or liq) z ≥ 2 signature the sweep uses, but here it is the *consequence* of the pull
(stops/liquidations run once the wall is gone), on the side the pull enabled. AND OI delta < 0 (deleveraging).

**5. ABSORPTION LANDING.** The move **lands on and is absorbed by** a genuine level — a far-side wall or a
VP node where real size steps in (prints ≈ depth held, the inverse of stage 2). This is where the mover's
actual interest sits: the pull cleared the path *to* this level.

## B/S intent mapping (unified with the sweep detector)
The label is the **bullish/bearish intent** — the direction the mover profits — not the mechanism:
- **B (bullish intent)** — mover wants price UP. Pull of **resistance** (an ASK wall above) clears the path
  upward → **B**. (Mirrors a downside *sweep*, which also gets **B**.)
- **S (bearish intent)** — mover wants price DOWN. Pull of **support** (a BID wall below) clears the path
  downward → **S**. (Mirrors an upside *sweep*.)

So across both detectors: **B = they want up, S = they want down**, regardless of sweep-vs-pull. Terminal
labels reuse the Phase-2 renderer: `"B L. Pull"` (green) / `"S L. Pull"` (red), same dashed leader.

## Output & tiering (mirror the sweep detector)
`study/out/liq_pulls.csv`: `ts, bucket_id, side_label (B/S), wall_price, wall_size, vanish_frac,
print_ratio, traverse_thinness, forced_z, oi_delta, absorption_price, tier`. **Tier-A** = all five stages.
**Tier-B** = wall + vanish-without-printing but a missing/weak traverse/burst/landing (the decoys). Same
blind CALIBRATION PACK protocol as Phase 1 (Tier-A + Tier-B decoys shuffled, tier hidden, Ctrl+F-jumpable
Idx list → measured precision). No outcome study at this stage — precision first.

## Build plan (once the 72h gate passes)
1. `app/depth_replay.py` — pure book reconstruction at time t from snapshot+deltas + a per-level size
   time series; a tape-window query. (Reuses `depth_store` read paths; no daemon change.)
2. `study/liq_pulls.py` — stages 1–5 on the stored window → `liq_pulls.csv` + calibration pack.
3. Terminal: feed pulls into the Phase-2 label renderer (already supports the `Pull` kind); **live pull
   detection needs forward depth in RAM/`depth.db`, so it runs on the daemon-served book, not offline only**.
4. Keep it **uncalibrated** in the tooltip until the blind grade produces a precision number, exactly as
   the sweep labels are today.

## Explicit non-goals for this phase
No profitability/outcome study, no live trading hook, no daemon changes, and **no code at all** until the
depth store demonstrably holds ≥ 72h. This document is the frozen contract to build against then.
