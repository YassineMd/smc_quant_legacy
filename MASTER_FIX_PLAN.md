# Master Execution Plan — Order Flow Terminal Data Integrity

**Role split:** You are the operator. I (this plan) am the architect. Claude Code is the developer.
**Goal:** Make every scalar that your 11 scanner modes and the future selection tool read either *accurate* or *honestly labeled as an estimate*, before any new feature is built.
**Standard:** True quantitative math. Kill rigid absolute constants. Keep proportional, adaptive logic. Never fabricate data the source doesn't contain.

---

## The capstone this plan serves — Mode 10 is the map, not "scanner #10"

**Reframing (operator's core realization, 2026-06-16).** Mode 10 — the volume-bucket
canvas — is **not** the tenth scanner. It is the **primary mapping surface** and the
**capstone** this entire project is building toward. Every other piece — the 11
scanners, the time-candle, all of Phases 1–4 — is **infrastructure whose only job is to
make Mode 10 trustworthy.** The data-integrity work in this plan is the foundation under
the map, not an end in itself.

- **The time-candle becomes VESTIGIAL once Mode 10 carries the map.** A time candle
  misrepresents the auction: a 50-volume minute and a 50,000-volume minute draw the
  **same width**, so equal screen space encodes wildly unequal participation. The
  **volume bucket is the native, truthful unit** — width *is* activity. This is the
  realization driving the project; the time-chart is scaffolding we keep only until the
  bucket canvas carries everything.
- **TradingView STAYS — it is the operator's deliberate external decision/drawing
  surface.** We do **not** rebuild a drawing/execution tool. Division of labor is
  explicit: **scanners = sensing**, **Mode 10 = the level map** (the artifact that exists
  *nowhere else on the market*), **TradingView = marking & execution.** Do not
  scope-creep Mode 10 toward a charting package.
- **Sequencing to the capstone (overlays migrate onto Mode 10 ONLY AFTER their logic is
  fixed — never before):**
  1. **Cheap validation NOW:** confirm the footprint + true POC render correctly on the
     Mode 10 bucket canvas — low-cost, high-information; prove the surface before we
     invest overlays in it.
  2. **Make every overlay CORRECT:** Phase 2 (OB fidelity, Steps 6–8) + Phase 3 (visual
     layer, Steps 9–14). No overlay is allowed onto the map until its math is honest.
  3. **Capstone:** consolidate all finished overlays onto Mode 10 and build the selection
     tool against the corrected scalars (see "After the pipeline is solid", below).

This reframing changes no step's *math* — it sets the **destination** every step serves
and the **rule** that no overlay is consolidated onto Mode 10 before its logic is fixed.

---

## Committed endgame order (locked 2026-06-16)

What remains to a trustworthy daily-driver Mode 10, in order. **GROUP A is DONE** (`0fc31d5 →
3426713` — Mode 10 is the primary surface + default-on-open); the Group B/C/D buckets and the Phases
still hold their content — this orders what's left and why.

> **CURRENT STATE (2026-06-17) — the terminal is functionally complete + HONEST.** Group A is done (Mode 10
> is home: default-on-open, view-follow, overlay toggles, 12-line stats + the A3b state engine). The canvas
> no longer lies — the **churn gate** kills false-conviction colors (no rounding-error vector paints
> green/cyan), the **doji flat-line** kills phantom ranges (zero-range buckets render a flat neutral line,
> not a forced colored body), POC is true per-bucket, the footprint ladder is correct. What remains is
> **state-engine calibration** (feeling the engine against live market — the big next thing) plus deferred
> beauty/cleanup (Phase 3 churn opacity, parallel-mode color lie, adaptive churn threshold, time-chart
> removal, Phase 2 OB fidelity, Phase 3 visual). **Nothing left is a correctness lie.**

1. ✅ **GROUP A COMPLETE (2026-06-17) — Mode 10 is home.** The move-in is done: Mode 10 is the primary
   surface + default-on-open, the time-candle demoted to a fallback. Shipped: view-follow (per-axis lock
   model `b2e1735` + A0 candle-framing) and A5 (`3426713`, list reorder + open-on-Mode-10). Full arc
   below ("Group A — ✅ COMPLETE").
2. ✅ **Churn-color CORRECTNESS sliver DONE (2026-06-17).** `_neon_v2_brush` gates churn on NET positioning
   `(main-opp)/curr_vol < CHURN_VOL_FRAC` (0.05) → muted slate `CHURN_RGBA`, returned BEFORE the palette +
   neon override; conviction opacity still rides the old `dom` (real conviction candles unchanged). The
   VOLUME denominator (not the pair sum) kills the rounding-error lie — clS=6.4 on 2.9K vol = 0.2% → churn,
   where the old dom inflated it to ~1.0 → cyan; NET (not max) also catches the balanced bucket
   (opL≈opS → net 0). Mode-10 only. Two deferrals (adaptive threshold; parallel-mode same-lie) recorded
   under the "Mode 10 color/churn fidelity" trace section below. Beauty (the deliberate churn identity)
   waits for Phase 3.
3. ✅ **Doji / zero-range CORRECTNESS DONE (2026-06-17, `7fbe58c`).** A bucket where all volume traded at
   one tick (`high==low`) rendered a forced `TICK/2` colored body — a phantom range. Both candle renderers
   (`BucketCandleItem` Mode 10 + `CandlestickItem` time chart) now draw a flat neutral `#888888` line at the
   single price (POC dot at center), no body/fill. Ranged dojis (open==close, high≠low) keep their sliver +
   wicks. Closes the gray-column **correctness** half; the tall 45%-transparent *ranged* churn bucket stays
   the Phase-3 churn-opacity beauty item.
4. **State-engine live calibration** *(IN PROGRESS — days of real market)*. Tune `app/bucket_state.py`
   constants against live verdicts; top priority = any confident/starred verdict that's wrong. See
   `scripts/STATE_ENGINE_TUNING.md`. Live findings:
   - ✅ **PREREQUISITE — engine integrity: the space_left guard (2026-06-17, `424438f`, VERIFIED live).**
     Calibration was blocked by impossible NEGATIVE vectors: `process_tick` subtracted a negative chunk whenever
     `target_vol` recalibrated BELOW the active bucket's curr_vol (`space_left < 0`), driving opS/sell_vol negative
     (conservation held; non-negativity broke). Guard: `space_left <= 0` → close the over-full bucket (grandfathered)
     and restart, never add a negative chunk. Shared bucketing logic, so it poisoned BOTH old history and new buckets
     (the stale 06-14 `dist\` daemon exe + the removed kline-delta path were red herrings — the real cause was this).
     `scripts/test_space_left_guard.py` reproduces→resolves; live proof = 2,983 post-wipe buckets, 0 negatives,
     0 conservation failures, 142 recalibrate-down events all grandfathered clean.
   - ✅ **COMMIT A — NEUTRAL-STATES DIRECTIONAL GUARD (the `notCommitted` factor) — BUILT + live-verified, 2026-06-17.**
     The neutral states (CHOP, ROTATION) confidently claimed buckets where something clearly DID happen — they read
     only velocity/delta/liq fractions and were blind to EFFORT, OI BUILDING, and PRICE MOVEMENT. THREE live instances
     of one structural gap:
       • **592 — absorption:** 8.7K vol, +85%/+50% E/R → ★ CHOP 82. Effort was being ABSORBED, not "nothing happening."
       • **327 — building:** ★ CHOP 96% on `opL ~17%` + OI building = accumulation (STRONG zeroed by `delta −17%`).
       • **608 — movement:** ★ CHOP 94% on a decisive churn-driven MARKDOWN (`|result|=0.85`, 83% churn, no fresh OI).
     FIX = CHOP gains ONE core factor `notCommitted = 1 − max(absorption, building, moved)` — "nothing happened" means
     NONE of the ways something could happen fired: `absorption = ramp(max(b_mult,s_mult); ABSORB 1.20/2.00)`,
     `building = ramp(doi_frac>0; BUILD 0.08/0.18)` (signed — a balanced unwind isn't commitment),
     `moved = ramp(|result|; MOVED 0.55/0.80)`. ROTATION gets the same coverage with NO new factors: `oiNeutral`
     tightened (`OI_NEUTRAL_HI 0.35→0.22`) for building + `moved` folded into `calm` (`1 − max(delta-aggr, moved)`) for
     movement. **WHY one max()-factor, not three parallel (hard-won):** the geomean's n-th root means each EXTRA ~1.0
     factor RAISES the score (dilution) — a separate `notBuilding=1.0` on the absorbed 592 flipped it back to ★CHOP 64;
     only checking BOTH 327 AND 592 caught it (prove-don't-assume). So every guard collapses into one max() per state.
     RESULT (live-verified): 592 → BULL EXHAUSTION 60 (CHOP 57), 327 → ROTATION 55 (CHOP 41, honest low-confidence
     "leaning rotation"), 608 → NEUTRAL 20 (honest "moved but fits no pattern"). Suite 11/11 (synthetic: oi-building →
     NEUTRAL, decisive-move → NEUTRAL, genuine CHOP/ROTATION green). SUBSUMES the earlier separately-held `notAbsorbed`
     (now the absorption arm) AND resolves the ROTATION `oiNeutral` leniency watch-item (now tightened to 0.22).
     `MOVED_LO>0.50` on purpose: 327 is a half-trend-bar (`|result|=0.50`) and STAYS ROTATION; only a one-way bar
     (>0.55) demotes. Endpoints are starting guesses; live-tune. Further-tune trigger: if buckets with `opL >20–25%`
     still read ROTATION not STRONG, tighten `OI_NEUTRAL_HI`/`OPEN_OI` more. One bucket isn't calibration; do NOT tune
     against outcomes (rotation coils before breakouts — not a target).
   - **DEFERRED — absorption arm for ROTATION.** ROTATION now has building (`oiNeutral`) + movement (`calm`) coverage,
     but still reads no `b_mult`/E-R — so a high-energy ABSORBED churny bucket can still misread ROTATION. Lower
     priority than CHOP was. Fold an absorption arm into a ROTATION factor (via max(), NOT a parallel factor) eventually.
   - **DEFERRED CANDIDATE (don't build; watch first) — a DIRECTIONAL "CHURN MARKDOWN / DISTRIBUTION" new state.**
     A real phenomenon the engine has NO positive verdict for: price moves DECISIVELY via position TRANSFER (high
     churn), with low fresh-OI and no/mild absorption — fitting no directional state (STRONG needs fresh OI;
     EXHAUSTION needs absorption; these buckets have neither, so both correctly gate to 0). TWO instances logged:
       • **608 — NEUTRAL markdown** (no delta lean): O 72.22→C 71.89, `|result|=0.85`, 83% churn, ~0 fresh OI,
         delta ~0. The `moved` arm fully fires (0.85 > HI) → CHOP 94→0 → **NEUTRAL 20**. Honest, label fits.
       • **611 — BEARISH markdown** (heavy delta lean): O 71.83→C 71.72, `|result|=0.69`, 76% churn, opS 14%
         fresh, **delta −43%** (Δaggr 0.72), seller E/R +10% (mild friction, BELOW the 1.20 absorb floor). STRONG
         BEAR scores 45 — the engine SEES the bearish character — but is gated by `freshOI=0.10` (only 14% fresh
         shorts = distribution, not initiation). `moved` only PARTIALLY fires (0.69 < HI → 0.55) so CHOP lands at
         **59**. The 59/45/44 near-tie is honestly "nothing cleanly fits", but the CHOP *label* undersells a
         decisive red bar. (Irony: the MORE-bearish 611 reads CHOP 59 while the less-bearish 608 reads NEUTRAL 20 —
         purely because 611's `|result|` is lower, partially vs fully firing `moved`. Not the bearish character.)
     THE REFINEMENT (from 611): the candidate must be **DIRECTIONAL** — lean bull/bear from the delta sign, not a
     flat NEUTRAL. 611 should read "DISTRIBUTION (bearish) ~55-60", capturing the lean NEUTRAL can't.
     SIGNATURE: high churn + decisive `|result|` + DIRECTIONAL delta (bull/bear) + low fresh-OI + no/mild absorption.
     THE TRAP (do NOT take it): loosening `freshOI` or tightening `MOVED_HI` would flip 611 into STRONG BEAR — but
     that mislabels transfer-driven distribution as "fresh conviction", re-breaking the initiation/transfer line
     `freshOI` exists to draw. The honest home is THIS state, not a weak STRONG BEAR. Open question is FREQUENCY
     (2 instances: 608 neutral, 611 bearish) — keep COUNTING before deciding to build; a new state needs its own
     signature test + validation. DECIDE AFTER MORE LIVE WATCHING — do not build pre-emptively.
   - **DEFERRED CANDIDATE — "ABSORPTION / STALL" in the DIRECTIONAL states (HIGHER PRIORITY than the churn-markdown
     candidate above — wrong-way risk, not just understatement; watch first).** The MIRROR of churn-markdown: there
     price MOVED via churn (neutral states were blind to HIGH `|result|`); here price does NOT move despite heavy
     directional effort (directional states blind to LOW `|result|` = absorption). Result-awareness is HALF-WIRED —
     the `moved` guard (Commit A) fixed the neutral half; the directional half (effort-without-movement = absorption
     ≠ strong) is UNBUILT.
       • **682 — instance #1 (bearish absorption).** O 71.75→C 71.73 (`|result|=0.33`, only 2 ticks on a 6-tick
         range), Sell 13.5K / Buy 4.3K (delta −52%, Δaggr 0.93), opS 18% fresh shorts, **Seller E/R +40%**
         (`s_mult=1.40` = heavy absorption), OI **building** (+2.5K) → reads **STRONG BEAR 54**. THE DAMNING FACT:
         the absorption barely registered — it only dropped `translate` to 0.86 (the 1.30/2.00 ramp is too lenient)
         — and STRONG has NO `|result|` check, so the ONLY thing capping 682 is modest fresh OI (`freshOI=0.20`
         binds). A SAME-absorbed bucket with ~40% fresh shorts would read STRONG BEAR **~76% (near-★)** despite full
         absorption. EXHAUSTION can't catch it either: its `absorb` arm fired (0.25) but the `drain/cover` GATE is 0
         (OI building + opening flow → EXHAUSTION = absorption of CLOSING/draining flow; 682 = absorption of OPENING
         flow, no route).
     WRONG-WAY RISK (why higher priority): 611's CHOP merely UNDERSOLD a real bearish move (right direction); 682's
     "STRONG BEAR" can point the OPPOSITE way — absorbed fresh shorts often precede a SQUEEZE (bounce), so a
     continuation read leans into reversal-risk. An INVERTED signal, not just understated.
     TWO FRAMINGS (decide which after watching):
       (a) RESULT FLOOR (a GUARD, the symmetric mirror of `moved`): STRONG BULL/BEAR demote when `|result|` is LOW
           and E/R is HIGH — effort that didn't translate to price isn't "strong". Fixes the lie by demoting (→
           NEUTRAL / low-confidence), same shape as the neutral-states `moved` guard.
       (b) ABSORPTION / STALL (a new STATE): fresh directional effort + high E/R + low `|result|` + OI building → a
           positive "bears/bulls being absorbed" verdict — and as a bonus flags the squeeze-risk DIRECTLY.
     SIGNATURE: directional delta + fresh OI + high E/R (`s_mult`/`b_mult` ≥ ~1.30) + low `|result|` + OI building.
     Watch-first: COUNT how often absorbed-stall buckets appear (1 instance: 682). DECIDE AFTER MORE WATCHING — do
     not build pre-emptively.
   - **DEFERRED (only if needed) — tighten CHOP velocity.** If busy-but-NOT-absorbed buckets (high `vol_mult`,
     normal E/R) still misfire as CHOP after the E/R guard, promote the soft `quiet` to a CORE factor with a
     tighter `CHOP_VEL_HI` (~1.40 vs today's lenient 2.20). Second step only — NOT stacked with the E/R guard.
   - **LATENT WATCH-ITEM — two velocity sources can disagree on neon.** The candle brush (`_bucket_vel_ratios`,
     terminal.py:1507) uses `(buy+sell)/duration` over a **mean** of the last-20 buckets; the stats VEL line + the
     state engine's `vel_ratio` use `(target_vol/duration)/median(rolling_velocity)` (quant_engine.py:337) — different
     numerator AND different baseline stat (mean vs median) over noisy 1m durations. On bucket 327 they agreed "slow"
     (brush 0.59 vs engine 0.95), but a future bucket could cross the neon `2.5` threshold on the brush while the
     engine reads slow → candle neon while VEL says slow (a real candle-vs-stats inconsistency). Eventually unify onto
     one velocity source (likely the engine's `vel_ratio`, so the candle matches the states/stats). Not now.
5. **LATER (only after living on Mode 10 as default + trusting it):** time-chart full removal (+ the
   dead Technical-Layers-menu cleanup) · Phase 2 OB fidelity · Phase 3 visual (churn beauty + the
   cyan/magenta color cleanup).

---

## CURRENT STATE (2026-06-19) — Mode-10 overlay layers: OB ✅ · TRAP ✅ · ABSORPTION ✅

The long arc since the 2026-06-17 state above: the Mode-10 **overlay layers** got built/reworked.
Committed vs in-progress vs deferred, so the map stays clean after a long iceberg dive.

### ✅ OB layer rework — COMPLETE (committed)
The order-block overlay is done end-to-end on the bucket canvas — it subsumes Phase 2 (OB fidelity),
the Group D gray-OB item, and the OB-detection trace findings (Steps 6 & 8).
- **Lifespan BOXES on exact bucket epochs:** every OB renders as a lifespan box (formation→live-edge if
  alive, formation→consumption if dead), on `start_epoch`/`confirm_epoch`/`end_epoch` — killed the
  minute-resolution mapping class.
- **b0 anchor fix:** `start_epoch = b0.start_time` (the absorption candle where the zone is, NOT b1) —
  fixed the floating/detached OB.
- **Progressive CLOSE-based erosion** replaced binary first-touch death (Step 8) — erodes on a decisive
  close-through, not a 1-tick wick graze.
- Toggle defaults (m10_obs ON, m10_dead_obs ON "Dead OBs").
- Commits: `d2a99a8`, `73b01c5`, `0c455a5`, `3728952`.

### ✅ TRAP states (B) — COMMITTED
TRAP states gate on the **trapped side OPENING** (`trappedOpen` core factor) — the corrected
"effort-trapped, not strength". Commit `3333226`.

### ✅ ABSORPTION / "iceberg" layer — COMPLETE (committed 2026-06-19)
Replaced the old over-firing per-bucket iceberg heuristic (Step 11's "sea of icebergs") with an honest
whale-absorption detector. The arc:
1. **Rigorous C1–C4 definition** (HEAVY κ·median-vol · SUSTAINED · ONE-SIDED · HELD) + empirical
   firing-rate validation BEFORE building — "a handful of whales per session, not a forest."
2. **Lifecycle** (stateless replay, like `calc_quant_obs`): form → persist (peak-$ tier) → die on a
   decisive close-through (0.10% buffer, never a wick).
3. **Broadcast + consumer** wired (`absorptions` on ObPacket/Catchup, no schema bump): committed
   `b03b2f7`, `d284e62`; then plumbed through **pipe_client** (the field was parsed but dropped before
   the render read it → bands didn't appear until pipe_client mirrored it into the snapshot) + a redraw-
   gate fix (the sig didn't track absorption state, so a death didn't repaint).
4. **BUCKET-NATIVE PORT (the root fix):** the detector ran on 1m TIME candles while Mode 10 renders VOLUME
   buckets — the time-vs-bucket axis mismatch (`ts_to_idx` slippage, same class as the OB floaters)
   caused floating marks, wrong-place/wick-looking caps, and missing early-bucket coverage.
   `calc_absorption` now reads `engine.closed_buckets` (anchors on `bucket.start_time`, dies on the bucket
   `close_price`), so detection + display share ONE volume-bucket axis. Closed buckets always carry a
   close → the forward-only-close gap is gone; full early-bucket coverage.
5. **LINES redesign:** an iceberg is a price LEVEL, not a zone → a horizontal line at the cluster **POC**
   (heaviest-absorbed level), **thickness = κ** (continuous — replaces the discrete $-tiers), color =
   side, label = `$peak (κ)`. **Cell-boundary span** `[birth−0.5 → death+0.5 | live-edge]` fixes the
   half-bucket offset and the `+1` death overshoot (candles are centered at index i, half-width 0.5).
   Same-price merging = **Option A** (sequential defenses stay separate; concurrent already merges; $ =
   peak).
6. **κ FLOOR — DOLLAR-ANCHORED, LOCKED (2026-06-19):** the κ number changed meaning across units (1m
   median 14,648 → bucket median ~9,900 SOL), so the floor is set in DOLLARS. **κ=0.80 on buckets** =
   the 1m unit's ~$270k all-whales floor (min $272k, zero sub-$250k), PLUS a hard **$250k** filter so the
   whale line holds regardless of market drift. ~26/8h, kept readable by thickness-triage (weak = thin).
- **RESOLVED render bugs (2026-06-19, diagnosed READ-ONLY → confirmed LIVE):** (a) **stray vertical
  end-cap — FIXED** (removed; on a LINE the ending IS the death, no cap). (b) **2 "floating" line-starts
  — DISPROVEN, no code change.** Two independent code analyses (this session + the architect)
  convergently fingered a **bisect-tie** in the SHARED `_ts_to_idx` (`bisect_right(start_times, birth)−1`
  resolving a non-unique `start_time` to the RIGHTMOST tied bucket). Read-only LIVE instrumentation
  (`data/absorption_live.log` — per-mark birth→idx with TIE + CLAMPED flags) showed **`TIE=False` on
  every mark, every frame.** The real cause was the **viewport clamp** `max(x0, vx0)` on births off the
  LEFT edge (correct — one floater was an ACTIVE whale still defending off-screen, so clamping beats
  hiding; it also matches the OB boxes' `max(confirm_x, vx0)`). **DECISION: leave as-is — anchoring was
  never broken.** The prove-it-LIVE discipline prevented a wrong change to the SHARED mapper (OB-
  regression risk) for a bug that does not exist on screen.
- **⚠️ LATENT HAZARD (real, not yet triggered):** the bisect-tie itself. Non-unique `start_time`
  (`terminal.py` already flags it) means `_ts_to_idx` WOULD mis-anchor a mark born into a genuine
  sub-second tie-cluster — it just didn't fire here (this session's buckets were 60–70s apart). In a
  fast/bursty market it will. Known issue for future hardening of the SHARED mapper (disambiguate ties —
  resolve to the FIRST tied bucket, or carry a stable bucket ordinal); affects OB too, so verify no OB
  regression when fixed. Do NOT lose this finding.
- **COMMITTED (2026-06-19)** — the bucket-native port + LINES + κ-floor + the Bug-1 cap removal shipped
  in two commits (DETECTOR: `quant_engine.py` · `feeds.py`; RENDER+CONSUMER: `chart_widgets.py` ·
  `pipe_client.py` · `terminal.py` · `hamburger.py`), preceded by this docs commit. The layer is DONE.

### SESSION 2026-06-20 — forecast tool DROPPED + Mode-10 / drawing-tools UI batch + standalone exe

**Forecast price-target tool — EXPLORED then DROPPED (reverted).** Built (read-only) a Phase-A backtest
harness + the collection prerequisites (book-desync re-seed fix, L2 book-snapshot collection, 1m
`target_vol` pin, `COLLECTION_LOCK` schema guard) toward a "where could price reach + how confident"
Mode-10 overlay. **Phase-A verdict: Layer 1 (volatility → reachability) VALIDATED; Layers 2–3
(structure/strength targeting) GATED behind weeks of multi-regime data + an L2 book stream.** The
operator then **dropped the tool entirely** — all 7 forecast commits reverted via `git reset --hard
5f07b7b` (harness + collection code are GONE; recoverable from reflog for weeks). *If revisited: the
reachability layer was the proven piece; targeting needs the multi-regime collection + the
magnet-by-strength refinement.*

**Mode-10 / drawing-tools UI batch — SHIPPED (on `pipeline-integrity`, atop `5f07b7b`):**
- `ba6531a` removed the Mode-10 **kinetic forecast cloud** (green/red `bull_fc`/`bear_fc`); KEPT the gray
  baseline (smoothed-POC EMA). Mode 4 untouched.
- `ed493ac` **stats overlay z-order** — stays BELOW an open hamburger menu (`StatsOverlay.keep_under`).
- `2707557` + `09c7449` **keyboard toggles** (flip the menu checkboxes via `QShortcut`): `s` Stats ·
  `d` Drawing toolbar · `p` POC dot · `l` Liquidations · `f` Footprint.
- `458fda7` **position-bracket labels** — centered in the box, black bold value on a white bg, SL always
  −% / TP always +% (risk vs reward), top label = the R:R ratio only.
- `f465f65` **footprint numbers** — gate tightened (≤40 → ≤20 visible buckets), text bigger (9 → 11px) +
  neon green/red `(0,255,127)`/`(255,7,58)`.
- `977f980` **footprint 300% imbalance** — a level's number flips to BLACK text on a neon bg when its
  same-level buy-vs-sell imbalance ≥ `config.FOOTPRINT_IMBALANCE_RATIO` (3.0). `TextPool` gained an
  optional per-cell background brush (5th spec element → `TextItem.fill`).
- `d30ce3b` **Scan Start** default anchor 2h → **24h** before the host clock.
- `1404290` **voice alerts (Audio Feed)** — the Audio Feed sub-widget (default OFF) now arms
  `QtTextToSpeech` in `AudioEngine.speak()`; the terminal diffs each snapshot for new OB `ob_id`s /
  iceberg `id`s (seeds silently on first data + tf-change so the history backlog is never read out) and
  speaks live events only: iceberg → "{scale} {Buy|Sell} Iceberg", OB → "{scale} {Long|Short} Order
  Block". `a` toggles it. Did NOT re-enable the alerts ledger/chime (separate severed feature).
- `ba1dfed` **hamburger menu UX** — (1) closes only on an OUTSIDE click now (deleted the cursor-leave
  `leaveEvent`; an app-wide mouse-press filter, installed only while open, ignores presses inside the
  panel / on the `[☰]` button / while a combo/calendar popup is up); (2) all controls live in a
  transparent `QScrollArea` so a short window SCROLLS them instead of cramming them unclickable (the
  panel is pinned to the full window height).
- `0e41f9a` **🔄 refresh button** (left of the bell) — un-freezes a stalled feed without restarting the
  window. `PipeClientWorker.refresh()` force-drops the live socket (`shutdown()` to unblock a stuck
  `recv`) and reconnects with NO 2s backoff, re-requesting the catch-up — fixes a half-open socket a
  net blip left behind that TCP never reported dead (the dominant freeze). `_refresh()` also relaunches
  the gcloud tunnel if its port died (safe no-op if live; `_TUNNEL` module global set in `main()`) and
  invalidates the render sigs so whichever scanner mode is active repaints from the fresh data
  (mode-agnostic — operates at the data layer all modes draw from).

**Standalone exe.** `python build.py --terminal-only` → `dist/OrderFlowTerminal.exe` (onefile ~88 MB),
PROVEN to render + spawn the gcloud tunnel (window + `cmd→gcloud compute ssh→putty` confirmed). 2nd
machine = install gcloud + `gcloud auth login` + accept the VM host key once, then double-click.
**Rebuild after a UI batch** to refresh it (the current exe predates this batch).

---

### SESSION 2026-06-21 — bucket-sizing overhaul + honest scale labels + storage audit

**Median-anchored bucket sizing — SHIPPED + DEPLOYED (`83651f3`).** Proved on real VM data that the old
variance-maximizing optimizer was DEGENERATE — it hit its `0.5 × avg-vol` search floor ~85% of the time
(18/19 windows on 1m) and lurched 50–77% chasing volume bursts, while 15m/1h/4h stayed pinned at 5000
(a flat 2h window never catches ≥10 high-tf candles, ever). Replaced with ONE mechanism anchored on the
data-rich 1m engine: `target_vol[1m] = BUCKET_MEDIAN_CANDLES × median(in-RAM 1m candle volume)`;
`target_vol[tf] = target_vol[1m] × (tf_seconds/60)`. Median is burst-immune (1m vol is ~2.04× right-
skewed — the exact distortion the old mean chased). The window IS `FOOTPRINT_MEM_CAP` (reads only the
in-RAM footprints dict → can never read pruned data; no new magic constant). All 5 tfs sized atomically
off one anchor each recompute sweep (`MarketDataCore._resize_engines`, hoisted out of the per-engine
path); removed `recalibrate` + `optimize_bucket_size` (single call site). `BUCKET_MEDIAN_CANDLES = 1.0`
knob ("one bucket ≈ one median 1m candle"). Validated within 1–5% of the optimizer where it was
reliable; **uniform ~2.2 buckets per candle-period on every tf** (vs the old ~852 per 4h period).
**Deployed live to the VM** — rehydrated 9628 buckets (NO wipe; schema version untouched), 15m/1h/4h off
the 5k floor with exact ratio integrity off the ~7.7K anchor; eyeballed on the live chart (fat buckets
at the live edge, by-design seam against the frozen old-size history).

**Honest bucket-scale labels — SHIPPED (`3376ba4`).** The Bucket Scale selector + window title now show
`N× (~vol)` instead of the dishonest `1m/5m/15m/1h/4h` time labels: `N×` = exact structural multiple
(`1×/5×/15×/60×/240×`), `~vol` = live per-bucket target at **1 significant figure** (`~8K/~40K/~100K/
~500K/~2M`) — honest precision matching the drifting median, not false digits. Display-only via the
`scanner_combo` pattern (`addItem(label, tf_key)` + `currentData()`) → daemon still gets `"1m"`/`"5m"`/…;
`TIMEFRAMES`/`TF_SECONDS`/`request_timeframe` untouched. All 5 ~vols derive client-side from the one
anchor the terminal holds (`target_vol[tf] = anchor × tf_seconds/60`), refreshed each tick, flicker-free
(re-render only on a rounded-value change); `request_timeframe` zeroes `target_vol` so labels skip the
mid-switch stale value. No daemon change. Verified live (ladder shows, switching loads, title updates,
no flicker, `×` renders clean).

**Storage-safety audit — NO LANDMINE, nothing to build.** Proved from the code + the live DB that ALL
daemon tables are bounded: `closed_buckets` IS pruned to `CLOSED_BUCKETS_CAP=10000`/tf every sync
(persistence `DELETE … id NOT IN (… ORDER BY id DESC LIMIT 10000)`), `footprints` to `FOOTPRINT_CAP=
10000`/tf, `order_blocks` rewritten each sync, `engine_state` 5 rows, `meta` 1 row; WAL auto-checkpoints
(default ~4 MB). No separate liquidations/CVD table (embedded in bucket/footprint JSON). Measured
bytes/row (720 B bucket, 866 B footprint) → **steady-state ceiling ~100–110 MB** (~2% of the 5.3 GB
free), reached over months then flat forever (`1mo ~60 MB, 6mo ~80 MB`). No reactive disk-full monitor,
but not needed (caps prevent fill); a write failure is caught + retried (`HISTORY FLUSH ERROR`), never a
crash. Opposite of the desync: suspected a landmine, the caps were already there.

**Iceberg break-range band — SHIPPED (`ee9711c`).** The absorption POC line now has a faint filled
`plo→phi` band behind it (the break boundary the engine already tracks for death-by-close-through),
side-colored at **additive 8% alpha** (`_ABSORB_BAND_ALPHA`). Two-pass render (ALL bands first, then ALL
POC lines on top → lines stay crisp however many bands stack); overlaps darken ONLY where price-bands
cluster (the "whales stacking here" cue, self-limited to real overlap, not global mush). Single-tick
clusters (`phi <= plo`) skip the band → line only (pinpoint vs zone defense — a free signal).
Rendering-only — `plo/phi` already ride every mark through the protocol, no engine change. Eyeballed
clean with multiple stacked icebergs.

**Drawing tools — fill + white default + edit handles + rect expand — SHIPPED (`330924d`).** (1) Rect/
ellipse interior FILL (`fill_color` + `fill_opacity` on `DrawnShape`, brush when opacity>0; Fill presets
+ opacity slider in the edit panel, rect/ellipse only; default 0 = outline-only). (2) Default border
colour → **white `#ffffff`** — visible on the dark `#141414` canvas (the old `#000000` was near-invisible);
saved shapes keep their stored colour, `PositionBracket` untouched. (3) Selection **HANDLES**
(`ShapeHandles`, `pg.TargetItem` dots): click a shape with the select tool to resize/move — rect = 4
corners + 4 edge-mids, ellipse = center (move) + corner (resize), trend/ray/measure = 2 endpoints,
h/v-line = 1; drag live, persist on release, cleared on deselect / tool-switch / erase / teardown.
(4) Rectangle **Expand ◀/▶** buttons — each click grows that side by ~half its width (view-independent),
handles re-sync. Rendering/UI only, no engine change.

**Title scan-time + Ctrl-wheel anchor nudge — SHIPPED (`7b7d702`).** The window title now appends the
**Scan Start (Zero Point)** date/time (`· Scan yyyy-MM-dd HH:mm`), rebuilt flicker-free each tick so it
scrubs live. **Ctrl + mouse-wheel** over the chart nudges that anchor **±1 min** (up=+1, down=−1),
consumed so it doesn't also zoom (plain wheel still zooms); the menu's Scan Start picker tracks it.
Debounced (`_scan_nudge_timer`, 90 ms): rapid notches scrub the title live but coalesce into ONE chart
redraw, so spinning never thrashes the heavy `_on_scan_time_changed` teardown+redraw per notch.
**Shift + wheel zooms the X axis only** (`2fda524`) — `_vb_wheel` routes Shift to the native
`wheelEvent(axis=0)`; plain wheel still zooms both axes.

**Drawing polish + audio default + per-mode scan window — SHIPPED.** (`626a300`) drawing tools: **W=0**
gives rect/ellipse NO border (`rebuild` uses `Qt.NoPen`; Qt's width-0 draws a 1px hairline — lines stay
min 1); the **rectangle tool defaults** to a borderless white **10% fill**; the Long/Short risk/reward
zones drop to **~10% opacity** (alpha 70→26). (`24acc25`) **Audio Feed ON by default** — `AudioEngine`
arms + the menu toggle starts checked (set before its connect); the silent-seed still prevents a
startup backlog flood. (`b6e39ee`) **Per-mode Scan Start window** — `_set_scanner` anchors **Mode 10 at
24h** and every **metric scanner at 1h** (signal-blocked so the switch redraw uses the new anchor).

**Depth Wall Min default 50% → 90% (`ccff906`).** `_calibrate_depth_slider`'s one-shot default now
shows only walls ≥ **90%** of the largest current resting wall (just the dominant ones). Slider stays
absolute-SOL + draggable; a manual drag still overrides.

**Mode-10 drawings persist across scan-time changes + mode switches (`da25329`).** `flush_index_drawings`
lived in `clear_scanner_canvas` (runs on EVERY scan-time change AND mode switch), so changing the time or
switching scanners wiped all Mode-10 drawings. Now they're kept in memory for the whole session: the
flush is removed from `clear_scanner_canvas`; `_set_scanner` **shows** them on Mode 10 / **hides** them on
the metric scanners (price-anchored shapes are off-axis there) / restores on return. New helpers
`PositionBracket.set_visible` + `DrawingController.set_index_visible`. Drawings now survive scan-time
changes, Ctrl-wheel nudges, and mode switches.

**Magic Selection tool — Mode-10 region stats, PHASE 1 (`f36ba1a`).** The Mode-10 capstone (phase 1 of
2). A 🪄 drawing-bar tool: drag a **1px white dashed** rect (no fill) on the bucket canvas → aggregated
order-flow stats for what's inside. **4-corner resize** (reuses `ShapeHandles`, corners-only); transient
(not saved); one at a time; cleared on leaving Mode 10; deletable via the **eraser** (click inside) or
**trash**. **Live**: `_refresh_selection_stats` runs each frame, so a selection reaching the forming edge
updates as buckets form.
- *Aggregation* (`_aggregate_selection`): **FLOW** stats are **price-band-filtered from each bucket's
  `levels` ladder** (truly "in the box": Volume / Sell|Buy / Delta / OI-Δ / CVD / POC-of-selection);
  **POSITIONING / EFFORT / READ** are **span-level whole-bucket** aggregates (4-vector, liqs, buyer/seller
  E/R, VEL, VPIN — no per-price data exists for these). **VERIFIED**: the levels invariant
  (`Σlevels.b == buy_vol`, `Σ(b+s) == curr_vol`, levels-POC == `poc_price`) was proven on real VM buckets
  FIRST, so a single-bucket full-band selection reproduces that bucket's own scalars to the decimal;
  two-bucket = per-key sums; the price-band filter is precise.
- *Stats box*: mirrors the forming-bucket hover box — same `O H L C` header, FLOW/POSITIONING/EFFORT/READ
  sections, same colours (4-vector **top-2 dominance**, VEL **gold**). Own `StatsOverlay` instance with
  smart **8-candidate placement** (beside / above / below, corner-aligned) so it stays on-screen + never
  covers the selection.
- *Phase 2 (next)*: the **state detectors** — a filtered honest subset (~13 of 35) into the READ section
  (where STATE goes), thresholds GROUNDED in the real aggregated numbers this tool now surfaces (avoid the
  kinetic-line trap: observe values first, don't guess). Phase-2 step 1 = use the live selection to
  observe real aggregates across many selections.

**Magic Selection STATE line — region read by the 12-state engine (`02f41fa`).** The selection box now shows
a **STATE** line (+ the `DBG`/`why` calibration lines) in its READ section, classifying the selected REGION
with the **SAME `bucket_state` engine the per-bucket hover box uses** — no forked state logic, just an
adapter. `_synth_bucket` collapses the selected buckets into one synthetic *aggregate bucket* shaped like a
real bucket dict; `_selection_state` drops it after the **real pre-selection buckets** and calls
`bucket_state.classify_bucket`, so the classifier's rolling windows (sweep 10, exhaustion 30) read the true
pre-selection context. A **ONE-bucket selection reduces to the per-bucket classifier EXACTLY** (proven 60/60
on state + confidence + debug lines) → selection STATE matches hover STATE.
- *Aggregation honesty*: extensive scalars (volumes, 4-vector, churn, liqs) **SUM**; OHLC = first-open /
  max-high / min-low / last-close; POC = argmax of the merged ladder. **INTENSIVE per-bucket rates**
  (`vol_mult`, `buyer_er`/`seller_er`) are the **VOLUME-WEIGHTED MEAN** of the buckets' own values, NOT
  recomputed from span totals.
- *The E/R-aggregation fix* (found by **instrumenting real VM data**, not guessed): the first cut recomputed
  `buyer_er = Σbuy / dispersion(merged_levels)`, which grows **~n× the single-bucket scale** (Σbuy sums;
  merged dispersion grows sub-linearly) → **saturated the exhaustion z-mults** → collapsed STRONG's
  `translate` factor to ~0 → trending regions fell to NEUTRAL/ROTATION. (vol_mult was NOT the cause — it
  aggregated fine.) Volume-weighted-mean E/R keeps it on single-bucket scale, preserves buy/sell asymmetry,
  reduces to the bucket at n=1. **Proof (real SOL 1m):** clean STRONG BULL **6/6**, clean STRONG BEAR
  **12/13** now read STRONG (was collapsing); `s_mult` on a saturated region **1.94 → 1.08**.
- *Semantics*: STATE is a **SPAN** concept (positioning has no per-price split) — the price band refines only
  FLOW. Genuinely **MIXED spans** (e.g. a bull→bear reversal whose per-bucket deltas flip sign) **correctly**
  read ROTATION/NEUTRAL — net aggression over a reversing span really is ~0. Clean → STRONG, mixed → ROTATION
  is honest. *(Optional future tuning: shift the STRONG↔ROTATION boundary for spans — **DEFERRED** until the
  fixed version is used in anger.)*
- *Next*: **Problem 2 — adaptive VPIN.** The fixed `0.8/0.85` VPIN threshold (per-bucket + selection) is a
  magic constant; live engine VPIN reads ~0.28, so it may rarely trigger. Replace with a self-calibrating
  measure (percentile-in-rolling-window / z-score / median-band) grounded in SOL's real VPIN distribution —
  same discipline as the bucket-sizing fix. *(Investigate + propose; then the Phase-2 state detectors.)*

**Adaptive VPIN tiering — self-calibrating toxic/warn (`e272b71`).** The fixed **0.85** 'toxic' VPIN line
was **100% dead** on SOL: instrumented on 800 live 1m buckets, the rolling-50 VPIN **never exceeds ~0.57**
(p90=0.49), so the crimson tier NEVER fired and the gold 0.50 fired only ~8%. Replaced the magic constant
with a **rolling-window PERCENTILE** (`app/vpin_adaptive.py`): toxic = VPIN in the top decile of the recent
distribution, warn = top quartile — toxicity judged **relative to recent conditions**, self-calibrating to
whatever range SOL's VPIN occupies. **Percentile** chosen (not z-score / median-band) because it's
shape-agnostic: fits both the tight near-symmetric rolling-50 VPIN and the right-skewed per-bucket/selection
VPIN with one rule.
- *ONE shared helper, all FOUR display sites* (so 'toxic' means the same everywhere): Mode-6 VPIN view
  (bars + tracker + risk line), bucket-canvas VPIN heatmap (brush + risk line), hover readout (HFT-Trap /
  Accumulation / Normal labels), selection box VPIN (**was ungated → now coloured**). The 3 rolling-50 sites
  share **identical** cutpoints (same series → byte-identical tiers, verified max abs diff **0.0**); the
  selection ranks against **same-length windows** (apples-to-apples regardless of size) via the same helper.
- The static `0.85` `InfiniteLine`s became **LIVE lines at the current toxic cutpoint** (self-adjusting,
  inside the data range). The heatmap brush is **no longer cached** per closed bucket (render-time now, since
  cutpoints shift as buckets arrive) — `vbrush` dropped from the `_bucket_row` cache.
- *Config knobs*: `VPIN_ADAPT_WINDOW=240`, `VPIN_WARN_PCTL=75`, `VPIN_TOXIC_PCTL=90`, `VPIN_ADAPT_MIN=30`
  (warm-up: < MIN samples → NORMAL, no false toxic). Dead `VPIN_ALERT_BASELINE` removed.
- **DISPLAY-ONLY**: VPIN does NOT feed the state classifier (`bucket_state.py` has zero `vpin` refs), so this
  is risk-free for the states. **Verified (real SOL 1m):** toxic now fires **~10%** by construction (was 0%);
  risk line at p90=**0.52** inside [0.12, 0.57]. This is the **prerequisite** for the Phase-2 VPIN-using
  states (Whale Wars "low VPIN", any HFT-churn state) to mean "low/high *for the current regime*".

**EXE STALE** — terminal changed across `02f41fa` (STATE line) + `e272b71` (adaptive VPIN); rebuild batched
AFTER Phase-2 state extensions so we don't rebuild twice.

**Phase 2 — the 4 state extensions (next, on the verified aggregation + adaptive VPIN):** Hidden Bullish
Accumulation (CVD deeply −, price flat/up, opL dominant), Hidden Bearish Distribution (mirror), Whale Wars /
Strategic Locking (big opL AND opS + ~0 net price + LOW VPIN-for-regime), Passive Floor/Ceiling Iceberg
(**only if** it reuses the existing iceberg/absorption marks — drop if it doesn't connect cleanly). Extend
the ONE classifier (12 + these), thresholds grounded in observed numbers.

---

### Deferred queue — current order (operator's call, 2026-06-19)
1. ✅ **Time-chart removal — DONE (all phases: A/B/menu/relabel/C/D).** Completed after the absorption
   dive — Mode 10 (`BucketCandleItem`) is the sole candle surface. Full record in the "⚠️ TIME-CHART
   REMOVAL" block below. **Active queue head is now the `target_vol` clamp / item 3 selection tool —
   item 2 (OB polish) is DONE.**
2. ✅ **OB polish (A) — DONE.** (a) OB toggle bug — RESOLVED by MERGING the two toggles into one
   "Order Blocks" (`55fb663`), not the originally-planned independent `show_live`/`show_dead` flags.
   (b) Min-render-height — SHIPPED (`4435c1d`): thin OB zones floor to a 7px DRAWN band at wide zoom
   (see the Mode-10 UI refinements record below). (c) Duplicate-timestamp / bisect-tie — assessed and
   deliberately SKIPPED: rare exact-ms collision, ≤1-bucket error, data-ambiguous (the OB carries only
   the timestamp) — not worth a fix; `bisect_left` determinism is the only available tweak and adds no
   correctness.
3. **Mode-10 selection tool (D)** — the capstone, built against the corrected scalars once the overlays
   are all clean (see "After the pipeline is solid", below).
- **✅ Mode-10 UI refinements — DONE (2026-06-19).** Operator-driven stats-box / toggle cleanup:
  (1) **OB toggle bug fixed** — "Order Blocks" + "Dead OBs" MERGED into one "Order Blocks" toggle
  (alive solid + dead faded together; OFF hides both), `55fb663`. (2) **State-debug calib in the stats
  box by default** — the "State Debug (calib)" toggle dropped; the STATE readout always shows the
  top-3-states + winner-factors block, `71131ad`. (3) **Forming-candle stats always on** — the live
  (right-most, not-yet-closed) candle's readout shows by default (no hover), pinned to its low,
  `8da2412`. The hover readout (cursor-anchored, a specific bucket) is unchanged. (4) **Candle midline
  removed** — the wick is drawn OUTSIDE the body (upper + lower segments) instead of one line through
  it, clearing the center line that showed through the semi-transparent body fill; wicks unchanged,
  `710e742`. (5) **Wick + body-border flow-coloring** — the candle border + wick are colored by
  buy/sell dominance: green when buy leads, red when sell leads, gray when even; a >50% lead (1.5x)
  goes NEON green/red and slightly thicker (0.7 vs 0.3 px). DIVERGENCE overrides at width 1: buy-led
  but closed DOWN → neon orange (255,128,0); sell-led but closed UP → neon blue (0,153,255). Body fill
  (neon-engine 4-vector brush) unchanged; pen cached in the #3 compute path. `4c8fc2b`. (6) **OB
  min-render-height** — thin order-block zones (a few ticks around the POC) floor to a 7px DRAWN band
  at wide zoom (expanded symmetrically around the zone center; true top/bottom unchanged), so they no
  longer collapse to a sub-pixel sliver, `4435c1d`.
- **DOM book-mid line (idea from the Phase-A DOM port) — DEFERRED, NOT Phase A.** The Mode-10 spot line
  is the last TRADE (`closes[-1]`); on a depth ladder the book MID (between best-bid/best-ask) is often
  the more useful reference. Idea: keep the last-trade line, ADD a thin mid line (or best-bid/ask markers)
  so the spread + the last-trade-vs-book position are visible. NOT a bug (the COB is correctly aligned —
  diagnosed read-only); an enhancement, consider after the time-chart removal.
- **Alerts re-wire (surfaced during Phase B) — DEFERRED.** `alerts.feed` (OB/liq notifications) fired
  ONLY in the old Off-branch that Phase B severed — so alerts already didn't fire in Mode 10 (the default)
  and now never do. Re-wire `alerts.feed` into the scanner / Mode-10 path as a later follow-up. Not lost.
- **✅ TIME-CHART REMOVAL — COMPLETE (all phases).** A (DOM port) + B (sever "Off") + menu cleanup
  (Technical-Layers section removed) + relabel (timeframe selector → "Bucket Scale") + C (time-chart
  scene items deleted) + D (orphaned classes deleted: `CandlestickItem`/`SessionLayer`/`LiquidationLayer`
  in `chart_widgets.py`; `FootprintLayer`/`ImbalanceLayer`/`IcebergLayer` in `footprint_layers.py`). The
  CRITICAL Phase-C-step-1 was handled correctly: `visible_filter` was relocated OFF the now-deleted
  `ob_item` onto `bc_obs` (defined in `chart_widgets.py`, wired in `terminal.py` via
  `multiplierChanged → setattr(bc_obs, "visible_filter")`); `bc_obs.update_data_indexed` reads it, so
  Min-Mult still filters Mode-10 OBs. `_hover_stats`/`_stats_enabled` deleted. Mode 10
  (`BucketCandleItem`) is the sole candle surface. [Commits: A 332b8bf · B babba79 · menu 954dc80 ·
  relabel 3ff0404 · C 2bb2c71 · D 753a08d]
- **🔴 HIGH-PRIORITY (not urgent) — `optimize_bucket_size` balloons `target_vol` for higher tfs.
  DEFERRED to its own turn (don't interrupt the time-chart removal).** Root cause: `max_test_v =
  avg_node_vol*15` + UNCLAMPED `best_v` (quant_engine.py:403) → higher-tf footprint nodes (whole-candle
  volume) push `target_vol` to 648K/1.47M SOL (15m/1h); 4h stuck at the 5K default. NOT PC-off
  corruption (WAL crash-safe — it's a legit-computed bad value); it persists because rehydrate trusts
  `engine_state.target_vol` verbatim. FIX = CLAMP (not rebuild — rebuild re-runs the same logic):
  (a) §0.6 scale-free sanity-clamp in `optimize_bucket_size`; (b) validate/clamp on rehydrate. **1m
  UNAFFECTED** (stable ~4,419) and 1m is the live default → high-priority but not workflow-blocking.
- **💡 FEATURE (potential) — MULTI-TIMEFRAME OB CONFLUENCE.** OBs from several timeframes at once (spec
  §7.2.2; the old web app's strongest original idea — cross-tf OB confluence scoring). The native rewrite
  dropped it (daemon is single-tf-per-client). The dead "OB Overlay Timeframes" checklist (REMOVED in the
  hamburger cleanup — `obTfsChanged` had zero subscribers) was its last UI trace. To revisit: needs daemon
  work (per-client multi-tf streaming OR a merged cross-tf OB feed), THEN re-add the checklist. Genuinely
  valuable — don't lose the idea.
- **✅ TERMINAL PERFORMANCE — DONE (2026-06-19).** Profile-first with a live per-frame probe (TEMP,
  since removed). Before: **~2 FPS lurching** — `period` spiked to **525ms / p95 1.6s** loading history
  then panning at N≈2800. After all four fixes: **steady ~16 FPS (~63ms), NO spikes**, even with footprint
  + OBs + icebergs + COB on and during pan, across 4–6 windows on the i7-8565U. The probe overturned THREE
  wrong guesses (DOM-compute, candles-as-the-only-cost, walls-as-the-floor) and pinpointed each real fix —
  *trust the re-measure, not the instinct* (the footprint redirect: instinct said "compute", it measured as
  text/paint).
  - **#1 candle viewport-cull (`cbb1a55`)** — `BucketCandleItem` paints only the visible candles (was all N,
    no cull); `set_view` re-culls on pan/zoom. Isolated pan: paint **408ms → ~90ms (~78%)**.
  - **#3 static closed-bucket compute cache (`ff131a7`)** — VPIN(O50N)/vel-ratios(O20N)/OHLC/EMA computed
    ONCE on close, only the live edge recomputed (trailing-window-final proof). Equivalence-tested. draw ~1ms.
  - **footprint fix (`551cb36`)** — TextPool skip-unchanged + numbers gated to ≤40 buckets, else top-3 bubbles
    (≤200), else none; per-bucket POC rides the same detail gate. items +340 / paint +230ms → negligible.
  - **#2 wall gate (`9462c63`)** — `DepthWallLayer.update_data` gated on (drawn-walls, viewport, threshold)
    signature; quiet view skips. `dirty_main` ~120 → 14–16 idle.
  - **THE FLOOR IS THE OS TIMER, not our code:** ~63ms = Windows 15.6ms granularity rounding the 50ms
    `GUI_TIMER_MS` up to ~62.5ms. 16 FPS is the deliberate target for a low-power multi-window laptop — the
    fixes removed the *work that stretched the period under load* (the spikes), not the floor.
  - **Deferred perf follow-ups (all LOW priority — recorded, not needed now):**
    1. **COB gate** — the COB panel, when open, has the SAME ungated-every-frame pattern the walls had
       (`dirty_cob` ~100–150/frame + `dom` ~6–7ms). Not period-critical (fits the window). Cheap fix: gate
       `cob.update_depth` on the same depth signature as the walls (`DepthWallLayer._sig` pattern).
    2. **OB/iceberg re-loop** — `OrderBlockLayer`/`AbsorptionLayer` already viewport-CULL the draw, but still
       LOOP all obs/marks each frame (the bisect). Negligible now (measured ~baseline); only matters if counts
       grow into the thousands. Cheap fix then: skip off-screen IN the loop, not just the draw.
    3. **Higher-FPS lever (optional, machine-dependent)** — per-frame work is now cheap enough to run faster:
       `timeBeginPeriod(1)` (Windows timer res) + lower `GUI_TIMER_MS` → 30–60 FPS. NOT wanted on the current
       laptop/4–6-window setup (would multiply CPU 4–6×); recorded for a stronger machine / fewer windows.
- **State-engine calibration — still DEFERRED to LIVE trading** (operator's call): feel the engine against
  the real market over days; the item-4 "State-engine live calibration" arc above still holds.

---

## Mode 10 primary-surface punch list (operator, from live canvas use — 2026-06-16)

Observed by the operator trading off the live Mode 10 canvas (after the Stage-0 true-POC
marker and the Stage-1 per-bucket footprint ladder landed). Bucketed by **WHEN**, so the
sequencing stays deliberate and nothing is lost. **Execution order: Group C first (it
degrades every mode), then Group A (the next coherent build); B and D fold into their
phases.**

### Group C — all-modes cursor redraw bug (DO FIRST, before Group A)
- **Cursor leaves trails/smears** when moved and clears slowly on zoom — a redraw/
  clearing defect that degrades *every* mode, not just Mode 10. Fix before building more
  onto the surface. *(Operator-greenlit as the first execution item.)*

### Group A — "Mode 10 as the primary surface" — ✅ COMPLETE (2026-06-17)
**THE MOVE-IN IS DONE — Mode 10 is home.** The time-candle is demoted to a fallback; the volume-bucket
canvas is the primary surface + default-on-open. The arc: **A1** (POC box → dot) · **A2** (cursor price)
· **A3** (12-line readout + state engine) · **A4** (overlay toggles) · **view-follow** (per-axis lock
model + A0 candle-framing) · **A5** (move-in: first-in-list + default-on-open). Commits `0fc31d5 →
3426713`. The original punch-list items below are all addressed.
- **Make Mode 10 first** in the scanner-mode list **AND the default chart** on terminal
  open (today it opens on the time chart / "Off").
- ~~**Open-zoom frames the candles.**~~ **REMOVED from Group A — reclassified as a
  deliberate VIEW-FOLLOW design step (below).** Attempted as A0 (exclude the forecast
  cloud from the Y-fit + a footprint-scale reorder) and **REVERTED 2026-06-16**: it is
  *not* a simple Y-fit — it exposed a latent frozen-view bug. Root cause + the real fix
  are in "Mode 10 view-follow" below.
- **Per-overlay ON/OFF toggles, each independent:** POC, footprint ladder, icebergs,
  buy/sell imbalance gaps, liquidation marks, order blocks, **the stats/hover box
  itself.** *(The whole readout is one more independently-toggleable element — added
  2026-06-16. Do NOT build a one-off toggle; fold it into this A4 framework.)*
  - **A4 uses distinct `m10_` menu keys** (not the shared time-chart layer keys) — so
    greying a Phase-3 overlay on Mode 10 never greys its working time-chart twin, and
    Mode 10's toggle system has **zero dependency on time-chart code**. Forward note
    (2026-06-16): **when the time chart is removed (A5+), its "Technical Layers" menu
    section becomes dead UI — toggles for a chart that no longer exists. Remove that
    menu section as part of the time-chart removal step.** Mode 10's `m10_` toggles are
    unaffected by that removal — the distinct keys make it clean decoupling.
- **Upgrade the Mode 10 stats/hover box** to match the time-chart's richness, PLUS
  bucket-relevant fields: **bucket elapsed time, bucket size in volume.**
  - **A3a — readout — ✅ DONE (2026-06-16, commit `0fc31d5`):** 12-line order-flow box
    (Volume · Sell|Buy · Delta · OI Δ · OpL|OpS · ClS|ClL · Buyer/Seller E/R + anomaly% ·
    30-bucket rolling E/R · VEL · STATE) in four separated sections (Flow / Positioning /
    Effort / Read). **Live-breathe:** re-pulls the forming bucket every redraw frame while
    the cursor is parked, signature-gated on the live-edge volume so idle frames are free.
    Anomaly% = the exact Step-5/Mode-3 `_exhaustion_mults` multiplier as % off 1.0.
    **Dominant-vector coloring:** only the two largest of opL/opS/clS/clL light up (a zero
    never lights), so the box shows *why* the candle is its color. STATE is a `—`
    placeholder pending A3b.
  - **A3b — STATE ENGINE — ✅ DONE (2026-06-16, commit `892382f`)** — new pure module
    `app/bucket_state.py`, wired to Mode 10 line 12; synthetic test `scripts/test_a3b_state_engine.py`
    (13 verdicts + squeeze-floor gradient) folded into the suite. Geomean scoring → `(state,
    confidence)`, verdict = highest-scoring state, confidence = winning score. Liq prerequisite
    shipped first as **A3b-pre** (commit `7014d31`, activated the dormant liq feed +
    `liq_short`/`liq_long` on the wire — no schema bump, no wipe). All the proposal's open items
    were resolved: trap = **aggression × failed-result × swept-level + POC-position soft factor**,
    with `result`-against-the-aggressor the hard gate (the corrected "effort absorbed, not
    strength"); EXHAUSTION tied to the Step-5 z (`_exhaustion_mults`); **ROTATION/CHURN + CHOP +
    NEUTRAL floor** added; rigid precedence replaced by **best-score-wins** + a
    **gradient-preserving squeeze floor** (≥0.80, rising with liq intensity); per-bucket liq
    volume + the rolling sweep test both confirmed available. Every threshold is a named constant
    at the top of the module (tuned live).
    - **STATE CONFIDENCE — core to the verdict, NOT cosmetic (2026-06-16):** the engine must
      output **`(state, confidence)`**, never a bare label. Line 12 renders the state **with a
      confidence level 1–100%**; the state's **background-color opacity scales to confidence**;
      **a star (★) marks 80%+.** Confidence is computed from how cleanly the state's conditions
      are met (margin over threshold) and is a first-class return of the verdict function.
      **Rationale — a wrong verdict is worse than no verdict:** a calibrated "SHORT SQUEEZE 35%"
      tells the operator to distrust a marginal read, where a misleadingly binary label does not.
    - **Context-aware / sequence states — DEFERRED to the selection-tool build (2026-06-16):**
      the hover engine stays **per-bucket**. Cross-bucket logic — churn-collapse over N buckets,
      state run-length, the regime-shift signal — belongs with the **Mode 10 selection tool**
      (it reads across a span of buckets; that's the natural home), **NOT a separate v2 of the
      hover engine.** See "After the pipeline is solid → the selection tool" below.
    - **State engine live calibration + OHLC-usage audit — DEFERRED until the terminal is
      complete.** Engine is built + synthetically proven (`scripts/test_a3b_state_engine.py`),
      constants ready to tune (see `scripts/STATE_ENGINE_TUNING.md`). Calibrating means opening
      every mode, marking positions on TradingView, and feeling the market over days — that needs
      the finished tool, and doing it now means redoing it later. **When done:** a math /
      data-distribution + degenerate-input pass on real buckets; an **OHLC-usage audit per state**
      (esp. close-position / wick handling in STRONG); then the operator calibrates verdicts over
      days. **Top priority: any confident / starred verdict that's wrong.**
- **Remove the redundant gold POC ring/box** (the Stage-1 ladder highlight) — keep ONLY
  the gold POC dot (Stage 0).
- **Cursor shows the Y-axis value (price)** in all modes.

### Mode 10 view-follow — ✅ DONE (2026-06-17) — per-axis lock model (was Group A "open-zoom")
**SHIPPED** — `12a0d1e` (auto-follow roll + candle-framing) + `b2e1735` (per-axis lock model). The
TradingView per-axis lock: opens following (both axes locked, live edge tracked); a manual pan/zoom
unlocks the axis that actually MOVED (compare-to-previous-range — pyqtgraph's `sigRangeChangedManually`
can't be trusted for the axis: its payload carries the axis for wheel-zoom only, nothing for the three
drag gestures); re-lock is a double-click (X axis → lock X, Y axis → lock Y, plot body → lock both).
**A0 candle-framing folded in + DONE:** the Y fit uses candle lows/highs with the forecast cloud
EXCLUDED, so candles aren't squished and extreme post-fit buckets no longer overflow full-height. The
frozen one-shot `_fit_scanner_y` is replaced by a per-frame roll **for Mode 10 only** (the 9 other
scanner modes keep `_fit_scanner_y`). **End-key / on-canvas-button snap was DROPPED** — the double-click
subsumes it. Tunable constants: `FOLLOW_WINDOW/MARGIN/PAD_FRAC/AXIS_TOL_FRAC` + `FOLLOW_X/Y_PER_TICK`
(refit cadence). The original design analysis is kept below as the record.

**Original analysis (the reverted A0 + the root cause that motivated the real fix):** A0 tried to fix
"candles open zoomed-out" with a one-line Y-fit change (exclude the forecast cloud so the
candles aren't blown tiny) plus a footprint-scale reorder. It was **reverted** — it traded a
minor annoyance for a real regression: live buckets fell off the frozen view and their
footprints went blank.
- **Diagnosed root cause:** the Mode 10 view does a **ONE-SHOT frozen fit** (`_fit_scanner_y`
  runs only on mode / tf / Zero-Point change — *never* as new buckets form). The oversized
  **forecast-cloud Y-range was accidentally the cushion** that kept new buckets on-screen.
  Tightening the fit to the candles (A0) removed that cushion and **exposed the latent
  frozen-view bug**: new buckets form at X ordinals past the frozen right edge (and Y prices
  past the now-tight range) and render off-screen — baked correctly but blank. (The footprint
  bake is fine; this is a view-*follow* problem, not a scale problem.)
- **The REAL fix = a "view follows the live edge" feature**, of which candle-framing is only
  one part. **Decide the follow semantics first:** auto-follow (track the right edge + roll Y
  with live price) · hold-with-snap-back (stay put; snap to the live edge on a key/button) ·
  hybrid (follow unless the user has manually panned). **Fold the forecast-cloud exclusion in
  at that point** so candle-framing and live-follow land *together*, not separately.
- **Full-height overflow consequence (record 2026-06-16):** the frozen one-shot fit also lets an
  EXTREME post-fit bucket (a fast one-sided rip whose range exceeds the frozen `[lo,hi]`) **overflow
  the pane as a full-height candle** — read at first as mysterious "gray columns" (actually the gray
  `#888888` candle border of a faint churn body running off both edges, [chart_widgets.py:459](app/chart_widgets.py:459)).
  The view-follow fix should **bound / roll the Y range** so these don't render as full-height artifacts.

### Mode 10 footprint ladder rendering — ✅ DONE (2026-06-16) — DO NOT RE-OPEN
Closed a four-round saga (footprint apparently "vanishing" / blank on new buckets / a numbers-vs-
bubbles split across the chart). **Root cause (confirmed by a live-instrumented zoom log):**
`BucketFootprintItem` did **NO viewport culling** (unlike `FootprintLayer`), so the 600-label
budget (`_FP_TEXT_CAP`) was spent on the **OLDEST off-screen buckets → the live edge was starved
of numbers**, and any level beyond the cap drew **nothing → blank.** It was never a clearing/scale/
A0 bug — those were symptoms. **Fix (commit `4dcf3ee`):** (1) cull to the visible X window using
the `x0/x1` pattern FootprintLayer already used; (2) fill the label budget **newest-first**; (3)
**bubble fallback** for any beyond-cap/short-row level so no visible bucket is ever blank. The 12px
legibility gate was **correct, not mistuned** — the missing piece was culling. A per-frame re-bake
throttle is deferred (culling makes each bake cheap; revisit only if it feels heavy). *(Distinct
from the A0 candle-framing/view-follow step above, which stays deferred.)*

### Group B — order-book DOM on the canvas (its OWN Phase-3 step — bigger, own plumbing)
- Order-book **DOM ladder** + green/red **depth-wall horizontal lines**; default depth
  **50%** (not the current 20%); **hovering a wall line shows its volume.**

### Group D — defer into existing phases (do NOT pull forward)
- **Gray OB band renders ugly:** correctly diagnosed as a mitigated order block, so it
  STAYS — but "properly design it" is an OB rendering-quality task → **fold into Phase 2
  (OB fidelity).**
- **Y-axis price-fill / base labels** on the dashed live-price line sit *centered* on the
  line; they should sit **ABOVE** it (small render tweak) → **Phase 3.**
- **Drawing-tools polish pass:** the measurement tool shows price **% change** (item 11);
  the long/short position tool drops the redundant on-position value labels (the
  Entry/SL/TP dashed lines already show them), adds price **% change**, keeps **RR**
  (item 12) → drawing-tools polish.

---

## 0. Read this first — the rules that govern every step

### 0.1 The constant rule (so we don't over-correct)
- **KILL** absolute hardcoded thresholds that don't move with the market: `ICEBERG_VOL_SHARE = 0.04`, `er > 300 / er > 150`, the `churn / 2` split, fixed multiplier ladders like `1.8 / 1.3 / 0.8`.
- **KEEP** multipliers applied to a *rolling baseline* — these are already adaptive: `vel_ratio > 1.2 × avg_velocity`, `seller_er > 1.5 × avg_seller_er`. A multiple of a moving average scales with flow; it is not a rigid constant. Do not "fix" these.
- When in doubt: a number is bad if it means the same thing in a dead market and a liquidation cascade. It is fine if it is a ratio against something the market itself sets.

### 0.2 The "verbatim port" guardrail is retired
The legacy math is wrong in the specific places below. Several steps edit `quant_engine.py` on purpose. Every such edit must carry an inline comment: `# DIVERGES FROM LEGACY: <reason>`. The guardrail is replaced by "documented, tested deviation."

### 0.3 How to drive Claude Code (give it these standing instructions once, then per-step)
- **One step, one branch-commit, minimal diff.** Never batch steps. Commit between each so a bad change is bisectable.
- **No drive-by refactors, no reformatting, no renaming** outside the step's scope.
- **Restate the live invariants in every prompt** (see 0.4).
- **Every step ends at a verification gate.** Claude Code must produce the check output and you must eyeball it *before* committing. No green check, no commit.
- **Schema changes are atomic — and there are TWO independent schemas, not one.** The wire schema (`BucketSnapshot` in `protocol.py`, produced on demand by `QuantBucket._assemble` / `live_snapshot`) and the **persistence schema** (`persistence._bucket_to_dict` / `_bucket_from_dict`, over `QuantBucket` attributes) are separate, hand-maintained serializers. Any step that adds/changes a bucket field must, in the *same* commit, update **both** plus every `terminal.py` consumer. Specific traps confirmed in the code: (a) if a new wire field derives from a new `QuantBucket` attribute that isn't added to the persistence serializer, after a reboot that attribute silently resets to its `__init__` default; (b) `_bucket_from_dict` uses `d.get(key, default)` with **no schema-version guard**, so a *meaning* change deserializes old append-only rows under the new meaning with no error — silent mixed-semantics corruption. Mitigation: bump a schema version in `engine_state` on any bucket-field change, and rely on the Phase 0c `history.db` wipe to avoid mixed-semantics rows. A half-applied schema change corrupts rehydrated history without raising.

### 0.4 Invariants Claude Code must respect (paste into every prompt)
- `quant_engine.py` math is shared byte-for-byte between daemon and terminal; edits must be intentional and commented.
- `uTime` (candle open time) is the footprint DB key, `latest_utime`, and `candle["time"]`. **Never** repurpose it.
- `bucket.start_time` is **not unique** (many buckets share one busy minute). Never key on it alone.
- `pipe_client.snapshot()` is copy-on-write; consumers treat returned lists as **read-only**.
- `persistence.HistoryStore` rehydrates the `rolling_velocity` and `vpin_queue` deques and all closed buckets *with* `levels`. Any field/semantics change must round-trip through it.
- Liquidations do **not** flow through `process_tick` (separate stream). Don't wire them in.

### 0.5 DO NOT IMPLEMENT — Gemini's invalid items (tell Claude Code to skip these)
- **`_scan_vpin((self` "syntax error"** — fabricated. The file parses clean. Nothing to fix.
- **Inverting the sell-imbalance diagonal** — current `sell` vs `b_above` is the correct Sierra/ATAS convention. Inverting it breaks a working signal.
- **"Snowball" cluster-drift fix** — the running-mean clustering is self-limiting; the premise is backwards.
- **Volume-weighted Otsu as a "bug fix"** — it's a design preference on a wrong worked example. (It is offered as an *optional* experiment in Step 7, not a fix.)
- **"Distribute spillover across DOM levels"** — would invent prices kline data doesn't have. The honest answer is Phase 5 (aggTrade).
- **Replacing E/R range denominator because it's "a bug"** — it's standard E/R. We *upgrade* it in Step 4 deliberately, not because it's broken.

### 0.6 The degenerate-input contract (MANDATORY for every adaptive/statistical computation)
Every dynamic metric in this plan divides by something the market produces (dispersion, volume, standard deviation, velocity). Each of those can legitimately be **zero or near-zero** in real conditions (single-price absorption bucket, dead-flat session, cold start after boot). An unguarded division there produces an astronomical outlier that then **poisons the very rolling baseline** it feeds. So every adaptive computation MUST define its behavior on three degenerate inputs, and Claude Code may not ship one that doesn't:
1. **Zero/near-zero denominator** → floor it at a *physically meaningful, scale-free* value, never an arbitrary tiny epsilon. For tick-denominated effort, the floor is **1.0 tick** ("it took at least one tick"). For a standard deviation, floor at a *fraction of the mean* (a coefficient-of-variation floor), never a fixed absolute number — an absolute floor would reintroduce exactly the rigid constant we're killing.
2. **Cold start / under-filled window** → require `len(window) >= MIN_WINDOW` before the metric is allowed to fire (booleans return "no signal"; continuous metrics return the neutral value, e.g. multiplier = 1). No z-score may be computed against a window shorter than its minimum.
3. **Outlier ingestion** → the baseline (mean/percentile) that an adaptive gate reads must itself be resistant to a single spike: prefer a median or a pre-capped sample so one degenerate bucket can't permanently warp the window.
This rule supersedes any looser wording in an individual step. Steps 1, 4, 5, 8, and 11 each carry the specific instantiation.

### 0.7 How to actually verify an engine-math fix (the rehydration trap)
The Phase-0 baseline exposed a methodology trap: **re-reading rehydrated buckets does NOT re-run the new math.** Closed buckets in `history.db` store the *output scalars* (opL/opS/vel_mult/buyer_er/…) computed by whatever math was live when they closed; `rehydrate` restores those stored values verbatim. So after a fix, the 0b diagnostic over old buckets still shows **old-math numbers** — it does not validate the new code. There is also no raw-tick store to replay from (footprints hold accumulated levels, not the frame deltas `process_tick` consumes). Therefore every engine-math step (1, 2, 3, 4, 6, 8) is verified in this order:
1. **Synthetic unit test FIRST (authoritative, instant, deterministic):** construct a known tick/bucket sequence in a `scripts/` test, run the new function, assert the exact expected output (e.g. Step 1: feed event-times `t, t+0.5, t+2.0` → assert the two durations are `0.5` and `1.5`, not floored to 1.0). This is the real proof the math changed correctly — it doesn't depend on history at all.
2. **Live-accumulation sanity check (confirms it behaves on real flow):** run the daemon with the fix, let *new* buckets form, point the 0b diagnostic at the **new** buckets, compare their distribution to the baseline. 1m accumulates in minutes; 1h/4h take hours — so lean on the synthetic test for correctness and use live accumulation only as a behavioral sanity pass.
3. **`history.db.before-fixes` is the frozen "before" reference**, never overwritten. The live daemon's working `history.db` will drift (and now contains mixed old/new-math buckets) — that's expected; the frozen copy is the immutable baseline the synthetic tests and characterization compare against.

---

## PHASE 0 — Safety net (do before any fix)

### Step 0a — Branch + commit discipline
**Directive:** Create a working branch `pipeline-integrity`. Confirm the tree is clean and the app boots (daemon + one terminal, offscreen ok) before touching anything.
**Verify:** Clean boot, port 9999 served, one terminal connects and renders. Commit nothing yet.

### Step 0b — Build a reusable bucket-invariant checker
**Directive:** Create a small offscreen diagnostic (its own file under a `tools/` or `scripts/` dir, not imported by the app) that connects to the running daemon, pulls a `snapshot()` for a given timeframe, and reports, over the closed-bucket array: (1) duration distribution `end_time - start_time`; (2) `vol_mult` distribution; (3) volume-conservation residual `(opL+opS+clL+clS [+churn]) - curr_vol` as a fraction of `curr_vol`; (4) count of buckets where any single vector exceeds `curr_vol`; (5) VPIN recomputed from buckets vs the engine scalar. This is the regression harness reused by every later step.
**Verify:** It runs and prints all five against live data. This snapshot *is* your "before" baseline — save the output.

### Step 0c — Decide `history.db`
**Directive (your call, not Claude Code's):** After Phase 1 the stored `vol_mult`/durations/vectors in `history.db` (~2,872 buckets) are computed by the *old* math; only new buckets will be correct. Either (A) wipe `history.db` after Phase 1 for a clean slate, or (B) keep it and accept a transition window. Recommendation: **wipe after Phase 1**, because mixed-math history is exactly the kind of silent distortion you're trying to eliminate. Note the choice in the branch README.

---

## PHASE 1 — Foundational scalar correctness
*These fix the numbers every mode and the tool consume. Highest priority.*

### Step 1 — Velocity clock (feeds boundary, blocking everything)
**Problem:** `_process_kline` passes `tick_time = int(uTime)` (candle **open** time, constant for the whole candle) into `process_tick`. Buckets that open and close inside one candle get `start_time == end_time` → `duration = max(1.0, 0)` → velocity, `vel_ratio`, `vol_mult` are quantized to candle boundaries, not real fill speed. This gates OB ignition and feeds Mode 4 (kinetic) and Mode 10 (neon). **Confirmed by the Phase-0 baseline:** 1h `vol_mult` median is *exactly* 1.000 (degenerate → OB ignition can't fire on 1h/4h), and the data also contains **negative durations** (`end_time < start_time`, down to −27 h) because the clock currently mixes two incoherent sources — wall-clock `time.time()` (at `QuantEngine.__init__` and in `live_snapshot`) vs candle-open `tick_time` everywhere else — so buckets straddling a daemon restart inherit start/end from different clocks.
**Directive:**
- In `dynamic_stream`, read the event time `payload["E"]` (epoch ms) and pass it into `_process_kline`.
- In `_process_kline`, leave `uTime` untouched for the DB key / `latest_utime` / `candle["time"]`. Change **only** the value handed to `process_tick` as `tick_time`: use `payload["E"] / 1000.0` (float seconds). Fall back to `time.time()` if `E` is absent.
- **Make the bucket clock coherent (new, from the baseline):** seed `QuantBucket.__init__`'s `start_time` from the same event-time source, not `time.time()` — pass the engine's last-seen event time, or lazily set `start_time` on the first tick rather than at construction. Audit `live_snapshot`'s `now=time.time()` similarly: the active-bucket proxy may keep wall-clock, but a *closed* bucket's `end_time` must only ever be an event-time `tick_time`.
- In `quant_engine._close_active_bucket`, replace the rigid `duration = max(1.0, ...)` floor. Instant fills are *high* velocity, not low — a 1.0s floor currently inverts reality during bursts. But do **not** floor the duration at a tiny epsilon: `target_vol / epsilon` explodes and poisons `rolling_velocity` (same failure class as Step 4 — see 0.6). **Guard the degenerate cases explicitly (the floor currently masks them):** if `end - start <= 0` (the negative/zero-duration buckets the baseline found), do not feed that bucket's velocity into `rolling_velocity` at all (skip it) or treat it as a flagged outlier — never let it produce a negative or astronomically clamped velocity. For genuine sub-second fills, floor the duration at the **event-clock resolution (1 ms = 0.001 s)** — a physical floor — so it reads as high-but-bounded velocity. Then, per 0.6(3), make `avg_velocity` outlier-resistant (median, or cap each `vel` sample at a multiple of the running median before it enters the deque) so one burst bucket can't permanently warp the baseline, and apply the warm-up gate 0.6(2) before trusting `vel_ratio`. `# DIVERGES FROM LEGACY`.
**Invariants:** engine signature already accepts `tick_time`; feeds change + the `__init__`/`live_snapshot` clock seeds + the `_close_active_bucket` floor/guard.
**Verify (synthetic test FIRST, then harness — see 0.7):** a synthetic tick sequence with known event-time deltas must produce the exact expected durations and velocities (instant, deterministic). Then on live-accumulated buckets: same-minute closes show varied sub-minute durations, no negative durations, `vol_mult` no longer pinned at 1.000 (especially on 1h), bursts read high not low.

### Step 2 — ΔOI conservation clamp (feeds boundary)
**Problem:** OI is polled every 5s but klines push ~1/s, so one push absorbs up to 5s of OI change against a single push's volume. When `|delta_oi| > deltaVol`, the 4-vector ratios exceed 1 and `opL+opS+clL+clS > curr_vol` — inflating Modes 1/2/3/7/8 and the tool's dominance matrix.
**Directive:** Immediately before the `engine.process_tick(...)` call in `_process_kline`, clamp `delta_oi` to `[-deltaVol, +deltaVol]`. Rationale: an OI change cannot physically exceed the volume that produced it; the overflow is a sampling artifact. Keep the engine math untouched — the clamp lives at the feeds boundary. **Same family (per CC's audit):** also clamp `taker_buy` into `[0, deltaVol]` before the call, so `b_ratio = taker_buy/vol` and `s_ratio` stay inside `[0, 1]`. The existing `max(0.0, deltaBuy)` only guards the lower bound; a non-monotonic Binance frame where `deltaBuy > deltaVol` would push `b_ratio > 1` / `s_ratio < 0` and corrupt every downstream vector. One clamp closes both ends.
**Verify (0b harness):** conservation residual `< 1e-6 × curr_vol` for all buckets; zero buckets with a vector exceeding `curr_vol`.

### Step 3 — Honest churn decomposition + pulse (rate) rendering (engine; schema change — atomic)
**Problem:** The `churn / 2` split fabricates open-vs-close positioning out of volume that *did not change OI*. There is no L1 information to attribute it; the 50/50 is pure invention, and it makes your position curves look busy in ranging markets where nothing structural is happening — a **false pulse precisely in the coil before a move**, where reading the real one matters most. Separately: Modes 1/2/7/8 render *cumulative* vectors (an ever-climbing ledger), but the trader reads **steepness first** (the per-bucket rate = the heartbeat) and height second — so the cumulative line buries the signal they actually use.
**Directive:**
- Redefine the 4 vectors to carry **only the OI-confirmed portion**: when `delta_oi > 0`, split `delta_oi` into `opL/opS` by taker ratio; when `delta_oi < 0`, split `|delta_oi|` into `clL/clS` by taker ratio. Remove the `churn/2` terms entirely. `# DIVERGES FROM LEGACY`. **Also remove the duplicated 50/50 split in `stats_overlay.py`** (CC flagged it — keep engine and hover consistent).
- Track `churn = curr_vol - |delta_oi|` as a **new explicit bucket field** (`churn`), carried through `_assemble`, `BucketSnapshot`, persistence, and exposed to the modes/tool as "unattributed transfer volume."
- New conservation law: `opL + opS + clL + clS + churn == curr_vol`.
- **Add a per-bucket RATE (pulse) view to Modes 1/2/7/8**, since steepness is what the trader reads: alongside (or toggleable with) the cumulative curve, render each vector's **per-bucket flow** — opening flow breathing up, closing flow breathing down — so the heartbeat is drawn directly instead of inferred from slope. Cumulative stays as the "how far it climbed" context, checked second. Churn renders as a neutral band/rate.
- Update Mode 3's derived `delta_oi` (`(opL+opS) - (clL+clS)`) — it now equals true OI delta exactly.
**Invariants:** atomic schema update across protocol/engine/persistence/terminal (0.3) — remember the wire schema and the persistence serializer are separate (0.3); bump the schema version.
**Verify:** (1) synthetic test — 5-component conservation `opL+opS+clL+clS+churn == curr_vol` holds exactly. (2) **Visual sign-off (this is a MEANING change — the trader decides by eye, §0.7 can't):** accumulate fresh buckets spanning BOTH a ranging and a trending stretch, render old-50/50 vs new-confirmed **side by side**, and confirm the old curves climbed through the chop while the new ones went quiet and only built on real OI expansion. Show the rate view too. Do not commit until the trader confirms the quieter, truer picture reads right and picks the churn rendering (explicit band vs hover-only).

### Step 4 — Adaptive Effort/Result (engine)
**Problem:** `result = |high - low|` (range) is a valid but blunt displacement proxy — a single wick inflates it, and it can't tell a clean run from a chop with the same range.
**Directive:** Replace the range denominator with a **volume-weighted price dispersion** computed at close from `b.levels`: `vwap = Σ(vol_i · price_i)/Σvol_i`; `dispersion = sqrt(Σ vol_i·(price_i - vwap)² / Σ vol_i)`; then `ticks = max(1.0, dispersion / TICK_SIZE)` — **floor at 1.0 tick, NOT epsilon**. A bucket whose entire volume prints at one price (pure absorption — `dispersion = 0`) must read as "1 tick of effort," giving `er = vol`, a large but bounded value. Flooring at epsilon instead makes `er` explode to ~1e7 and permanently poisons the Step 5 rolling baseline (per 0.6(1)). `buyer_er = buy_vol / ticks`, `seller_er = sell_vol / ticks`. The benefit over legacy range is **wick-robustness in multi-level buckets** (a single outlier wick no longer inflates the denominator); the degenerate single-price case is handled identically to legacy. `# DIVERGES FROM LEGACY`. Apply the same formula and floor in `live_snapshot`.
**Caution:** This rescales E/R magnitudes, which feeds Step 5 (exhaustion thresholds) and OB ignition. Do Step 4 and Step 5 back to back.
**Verify (0b harness):** E/R values for a known absorption bucket (high volume, tiny dispersion) spike sharply vs a clean-run bucket of equal range — confirming it now discriminates absorption from displacement.

### Step 5 — Adaptive exhaustion (Mode 3, terminal)
**Problem:** `_scan_exhaustion` uses absolute `er > 300 / > 150` and a fixed multiplier ladder `1.8/1.3/0.8`, `1.5/0.7`. All rigid; all break when E/R is rescaled (Step 4) or when regime shifts.
**Directive:** Replace absolute E/R gates with the **z-score of each bucket's E/R against a rolling window** of recent buckets. Replace the step-function multiplier ladder with a **smooth monotonic function of that z-score** (e.g. a normalized ramp or logistic), so exhaustion intensity scales continuously with how anomalous the effort is, not in three rigid tiers. Keep the OI-direction term, but derive it from the now-correct `delta_oi` (Step 3).
**Degenerate-input guards (per 0.6 — non-optional):** the z-score denominator must be `max(std, c · |mean|)` with a small coefficient-of-variation `c` (scale-free — **not** a fixed absolute variance floor), so a dead-flat window can't divide by zero or manufacture huge z-spikes from noise. Until the window reaches `MIN_WINDOW` buckets, bypass the z-score entirely and emit the neutral multiplier (= 1), not a computed value.
**Verify:** Exhaustion output reacts to *relative* effort extremes and no longer flatlines or saturates when the market's overall E/R scale changes. Confirm it tracks across a quiet→volatile transition.

---

## PHASE 2 — Order-block fidelity (Mode 10 + canvas)

### Step 6 — Fix the band over-extension (engine: `calculate_dynamic_band`)
**Problem:** The `t_otsu` floor is checked **only in the inflection (`else`) branch**, never in the `delta_v > 0` *sliding* branch — and this is true identically in both the upward and downward expansion loops (so it is **not** an up/down asymmetry; both directions share the same gap). On a smooth monotonic decay away from the POC there is no inflection to trigger the check, so the band walks down the entire slope well below the institutional floor, stopping only when volume flattens near zero. Bands bloat past the real shelf — the opposite of the razor-sharp OBs you want.
**Directive:** Add the `t_otsu` floor to the **sliding branch in both loops**: stop expanding the moment the level being stepped onto falls below `t_otsu`, regardless of whether volume is still monotonically decreasing. Compare the *next* level's volume to `t_otsu` consistently (remove the curr/next off-by-one). `# DIVERGES FROM LEGACY`. (This is a deliberate design choice toward tighter bands aligned with the goal, not a symmetry fix — the code is already symmetric.)
**Verify:** On a bucket with a clear high-volume core and a long thin tail, the band now terminates at the shelf edge, not at the tail. Compare top/bottom before/after on a few real OBs.

### Step 7 — (OPTIONAL EXPERIMENT, not a fix) Otsu weighting
**Directive:** Only if you want to A/B it: add a flag to compute Otsu class weights by *summed volume* rather than row count, and compare resulting `t_otsu` and band tightness against Step 6 on real data. Keep it behind a flag; do not adopt blindly. Skip entirely if Step 6 already gives tight bands.

### Step 8 — Proportional OB mitigation, not binary death (engine: `calc_quant_obs`)
**Problem:** An OB is flipped fully dead the instant a candle's low touches its near edge (`b.low <= ob["top"]` for bullish). Touching the near edge is price *entering to test* — you lose the zone exactly when it becomes tradable. Binary alive/dead also hides partial consumption.
**Directive:** Replace the binary kill with a **volume-based mitigation score** (the units must match — you cannot subtract SOL from the dimensionless `power_score`; that goes negative and the opacity breaks). Two new OB fields (schema change — atomic, per 0.3): at ignition set `ob["initial_volume"]` and `ob["remaining_volume"]` to the **absorbed volume that formed the zone** — the summed `b0.levels` volume inside `[bottom, top]` (fall back to `b0.curr_vol` if the band sum is unavailable). As later buckets trade *inside* the OB range, subtract their in-range executed volume from `remaining_volume` (floored at 0). The renderer fades opacity as `remaining_volume / initial_volume`. `power_score` stays untouched as the ranking metric. Mark `active = False` only when the structure is truly broken — bullish: a low pierces below `ob["bottom"]`; bearish: a high breaks above `ob["top"]` — **or** when `remaining_volume` hits 0 (fully consumed). `# DIVERGES FROM LEGACY`. Apply the analogous (but vertical) consumption model to imbalance gaps in Step 14.
**Mandatory: this must be INCREMENTAL, not recomputed from scratch (per 0.6 spirit + Phase-4 concern).** Do **not** rescan all historical buckets × their `levels` for every OB on every recompute — that is O(obs × buckets × levels) on the daemon's event loop and will stall the feed during exactly the volatility you care about (note: this runs on the *daemon*, not the UI thread — the symptom is stale charts across all 3 screens, not a frozen window). Instead, keep `remaining_volume` as **persistent per-OB state keyed by `ob_id`** (a `{ob_id: remaining_volume}` map on the engine): when a single new bucket closes, update only the active OBs whose `[bottom, top]` it overlaps, using only that one bucket's in-range volume — O(active_obs) per close. On rehydrate, backfill the map once from history (a one-time O(history) cost at boot is fine). Persist the map or recompute it on boot — your call, but the per-close path touches only the newest bucket.
**Verify:** A retest that wicks into a zone and bounces leaves the OB alive with reduced strength; only a clean break-through kills it. Confirm on a real retest sequence.

### OB detection fidelity — trace findings (2026-06-16) — extends Steps 6 & 8
*(Not numbered — Step 9 belongs to Phase 3; these are detection-side findings for this phase.)*
A read-only trace of `calc_quant_obs` / `calculate_dynamic_band` surfaced the fix agenda
below. Cross-referenced to Steps 6 & 8 so nothing is built twice:
- **Mitigation is too crude — binary first-touch.** `b.low <= ob["top"]` (bullish) flips
  the zone fully dead on the first graze: no buffer, no body-close, no partial-fill, so a
  one-tick wick marks a live zone gray ([quant_engine.py:601](app/quant_engine.py:601)).
  **Step 8 already** replaces binary death with a proportional volume-consumption score
  (alive-with-reduced-strength vs broken) — that's the weak-vs-strong continuum. **Add to
  Step 8:** also gate the *touch test itself* with a **buffer or body-close confirmation**
  (a 1-tick wick into the edge ≠ a close-through entry) so a zone isn't marked spent on a graze.
- **The two `× 1.5` absorption thresholds are HARDCODED magic numbers**
  ([quant_engine.py:554](app/quant_engine.py:554) / [578](app/quant_engine.py:578)). Promote
  both to **named, tunable constants** in config — live-calibratable, like the state-engine
  constants in `scripts/STATE_ENGINE_TUNING.md`.
- **`calc_quant_obs` is a VERBATIM LEGACY PORT** (`main.py:490`), never redesigned for the
  aggTrade / 4-vector world it now runs in — it *reads* `opL/opS`/`vel_ratio` but the detection
  logic predates them. **Phase 2 decision (don't leave it undecided):** redesign detection
  4-vector-native, or confirm the ported logic is fine as-is.
- **OB conviction gap — the bullish gate is `opL > opS` ONLY** ([quant_engine.py:555](app/quant_engine.py:555)
  / [579](app/quant_engine.py:579)), never `opL` vs `clS`/`clL`. So a bucket whose DOMINANT flow is
  CLOSING (clS short-covering / clL long-puking) can fire an OB on a minority opening sliver — an
  **unwinding-driven OB, not new conviction**. (A purely-closing bucket with `opL=opS=0` fails the
  gate; a closing-*dominant* one with a small buy-skewed opening slice passes.) Invisible today —
  neither the color (encodes velocity) nor `power_score` distinguishes it. **FIX = mark + rank, NOT
  a silent filter** (operator's call, 2026-06-16): tag `ignition_type` (opening-conviction vs
  closing-unwinding); render closing-ignition OBs distinctly (dimmer / dashed / marked); **down-rank
  them in `power_score`** so the Min-Mult slider drops the weak ones first. Conviction OBs stay bright
  + rank high. Rationale: show everything, communicate **quality** (like the state engine's
  confidence), never a hidden include/exclude.
- **Band geometry is `b0`-only** — `calculate_dynamic_band` shapes the zone purely from the
  absorption bucket `b0`'s Otsu-thresholded volume wall; the ignition bucket `b1` only *gates*
  detection, never shapes the band. **Flagged for review** (may be correct — the zone *is* the
  absorption wall — but confirm alongside Step 6).

---

## PHASE 3 — Visual-layer correctness (so all 11 screens are honest)

### Step 9 — Stacked-imbalance run flush (`footprint_layers.ImbalanceLayer`)
**Problem:** A valid buy run is discarded when the *next* row is an opposing sell imbalance (the `elif` zeroes `run_buy` without flushing it). Real stacked zones go missing.
**Directive:** Before zeroing a run on a direction flip, flush it to `gaps` if it meets `STACKED_IMBALANCE_MIN`. Mirror for sell→buy. Leave the diagonal comparison itself **unchanged** (it is correct — see 0.5).
**Verify:** A buy run capped immediately by a sell imbalance now produces a gap zone. Construct a synthetic level ladder to confirm.

### Step 10 — Iceberg wick mitigation (`footprint_layers.IcebergLayer`)
**Problem:** Mitigation tests `close` (`c[3]`), so a wick that sweeps the level but closes back leaves a ghost line.
**Directive:** Mitigate on the candle's true extremes: buy iceberg dies when a later `low (c[2]) < price`; sell iceberg dies when a later `high (c[1]) > price`.
**Verify:** A wick-through sweep now ends the track at that candle; a candle that merely closes past without wicking through behaves correctly too.

### Step 11 — Adaptive iceberg detection (`footprint_layers.IcebergLayer`)
**Problem:** `share >= 0.04` and `skew >= 0.65` are rigid. In a thin forming candle every level clears 4%, flooding the screen ("sea of icebergs"); in a cascade real icebergs hide.
**Directive:** Replace the absolute share gate with a **statistical anomaly test on the candle's own level-volume distribution**: flag a level whose volume z-score (within that candle's levels, ideally smoothed over a short rolling window of recent candles) exceeds ~2.5σ — a scale-free standard-deviation multiple, not a fixed volume. Keep `skew` (it is already a 0–1 ratio) but consider gating it relative to the recent skew distribution rather than a flat 0.65.
**Degenerate-input guards (per 0.6 — non-optional):** a candle with very few levels has a tiny/zero σ, so trivial noise would score as 2.5σ. Require a **minimum level count** before the test may fire (booleans return "not an iceberg" below it), and floor the σ denominator at a fraction of the mean level-volume (scale-free), never an absolute number. This also fixes the forming-candle flood (early candles have too few levels to qualify).
**Verify:** Iceberg count drops to a sane handful on normal candles, still fires on genuine single-level concentrations, and no longer floods early in a forming candle.

### Step 12 — Per-side DOM normalization (`footprint_layers.DepthWallLayer`) — *tradeoff, your call*
**Problem:** Shared `max_q` across both sides lets a one-sided spoof wall compress the other side's opacity.
**Directive (decide first):** Option A — normalize bids against `max_bid_q` and asks against `max_ask_q` (kills spoof-blindness, but hides genuine cross-side magnitude asymmetry). Option B — keep shared scale (shows asymmetry, vulnerable to spoof). If you trade off relative wall structure per side, choose A. Tell Claude Code which; don't let it guess.
**Verify:** Under a simulated 200k one-sided wall, the opposite side's walls retain readable contrast (Option A) — and you confirm you're comfortable losing the cross-side size comparison.

### Step 13 — Footprint text-cap ordering (`footprint_layers.FootprintLayer`)
**Problem:** The 600-label cap fills oldest-first, so live-edge candles render blank when many rows are in view.
**Directive:** Allocate the label budget newest-first (iterate the in-viewport footprints from the right edge inward) so the live edge always gets numbers; older candles drop off the cap gracefully.
**Verify:** Zoom to a state with >600 visible rows; the rightmost candles keep their buy/sell numbers.

### Step 14 — Imbalance gaps: progressive vertical fill, not a static rectangle (`footprint_layers.ImbalanceLayer`)
**Problem:** Two distortions. (1) A mitigated gap is `continue`d — erased from history entirely. (2) Worse: treating a gap as a single all-or-nothing rectangle that only dies on a far-edge pierce **over-draws "ghost" support over levels already swept clean.** A buy gap spanning 140–145 that price retraces into down to 142 and bounces has had its **142–145 portion consumed**, yet a binary model keeps painting the full 140–145 band as live support.
**Directive:** Model the gap as a **price void that fills progressively from the near edge**, tracking how deep price has penetrated:
- For a **buy** gap `[bottom, top]` (support, entered from above): track `deepest = min(bottom, min subsequent candle low clamped into the band)`. The **unfilled remainder is `[bottom, deepest]`** — render only that, projecting right. The consumed `[deepest, top]` slice is gone (optionally keep it as a faded, time-frozen outline for historical context, not as live support).
- For a **sell** gap (resistance, entered from below): mirror — track the highest penetration up from `top`; unfilled remainder is `[penetration, top]`.
- **Full fill** (penetration reaches the far edge) → the live remainder vanishes; freeze the consumed outline at the fill candle's timestamp so history is reviewable (fixes distortion 1).
**Note:** this is a *vertical* consumption model (price eats the void), deliberately distinct from Step 8's *volume* fade for OBs (liquidity consumed) — each matches its structure's meaning. Precision here is still bounded by the kline-derived level data (Phase 5).
**Verify:** A retrace that fills the top half of a buy gap and bounces leaves only the lower unfilled half drawn as live support; the swept half is no longer painted; fully-filled gaps freeze in history rather than vanishing.

### Mode 10 color/churn fidelity — trace findings (2026-06-16)
Surfaced tracing `_neon_v2_brush` + the OB renderer. Visual-layer items for this phase:
- **✅ FIXED (2026-06-17) — Zero/rounding-error-vector bucket rendered a CONVICTION color.** Now gated:
  `_neon_v2_brush` returns muted `CHURN_RGBA` when NET `(main-opp)/curr_vol < CHURN_VOL_FRAC` (0.05),
  before the palette + neon override; conviction opacity unchanged (still `dom`). Original trace, kept
  for context: `_neon_v2_brush` ([terminal.py:1726](app/terminal.py:1726)) does `max(vectors, key=...)`; when all
  four vectors are 0 (pure churn / no net OI), `max` returns the **first dict key = `opL`** (an
  arbitrary insertion-order tiebreak), which the palette maps to **green** — and if the bucket was fast
  (`vel_ratio ≥ 2.5`) the neon override paints it **bright neon green at full alpha**. Observed: a bucket
  with Sell 2.6K / Buy 0, closed RED, all vectors 0 → shown bright green (a 100%-sell red bar lying as
  bullish). The brush has **no churn branch** — it always picks a vector. Same root as the "gray column"
  finding: a no-net-positioning bucket renders **faint-green-looks-gray** (non-neon) or **bright-green-lie**
  (neon) — neither correct.
- **✅ FIXED (2026-06-17, `7fbe58c`) — Zero-range (high==low) bucket drew a phantom colored body.** Both
  candle renderers (`BucketCandleItem` Mode 10 + `CandlestickItem` time chart) now draw a flat neutral
  `#888888` line at the single price (no forced `TICK/2` body); ranged dojis keep their sliver + wicks. The
  honest §0.6 degenerate rendering — all volume at one tick is a flat line, not a range. Closes the
  **correctness** half of the "gray column"; the tall transparent *ranged* churn bucket is the separate
  Phase-3 churn-opacity (beauty) item below.
- **Color naming + collision.** `RGB_GREEN_NEON` / `RGB_RED_NEON` are **misnamed** — their values are
  pure **cyan `(0,255,255)`** / **magenta `(255,0,255)`** ([config.py:197](app/config.py:197)), so a fast
  bullish OB renders cyan and a fast bearish OB magenta. Worse, OB-cyan **collides** with candle-cyan
  (clS short-covering, [terminal.py:1738](app/terminal.py:1738)) — two different meanings sharing a color.
  Rename the constants to their true colors AND resolve so no two meanings share one.
- **Churn bucket visual identity (design, after view-follow).** Churn buckets (one-sided volume,
  dominance ≈ 0) need a **deliberate, legible, beautiful** identity — muted-but-intentional color /
  pattern / hollow treatment, distinct from conviction green/red, attractive on the dark canvas. This is
  the single correct answer to "how to color a no-net-positioning bucket" that replaces BOTH the
  gray-column and the bright-green-lie behaviors above.
- **DEFERRED (post-calibration) — the churn threshold may need to be ADAPTIVE.** `CHURN_VOL_FRAC = 0.05`
  is a FIXED net-fraction in a relative world (same class of mistake as a fixed `px_per_y`): in a churny
  stretch 5% net positioning may be notable; in a trend it's noise. The honest version gates conviction on
  net-fraction **relative to the recent buckets' distribution** — the same rolling-baseline / z-score
  machinery as the Step-5 exhaustion (`_exhaustion_mults`) — not a magic number. Decide after watching real
  buckets across regimes whether the constant holds or becomes adaptive (constant-only if it stays; logic
  change if it goes adaptive). The fixed constant was shipped deliberately to stop the egregious lie and
  unblock calibration — a rounding-error vector is churn under *any* threshold.
- **DEFERRED — the parallel "Micro Bucket Open/Close Intent" modes carry the SAME lie.**
  `_scan_bucket_open_pos` / `_scan_bucket_close_pos` ([terminal.py:1548](app/terminal.py:1548) /
  [1603](app/terminal.py:1603)) color with their OWN logic — they do **not** call `_neon_v2_brush`, so the
  net/volume gate does NOT reach them; a rounding-error vector still prints cyan there. The same fix
  (net-positioning / volume gate) is needed when those modes get attention. Deferred — Mode 10 is primary.

---

## PHASE 4 — Performance (3 screens × 11 modes × 20 Hz)

### Step 15 — Move `recalibrate` off the hot path (engine/daemon)
> **PULLED FORWARD into Phase 5 as sub-step 19.4** (operator-approved with the aggTrade plan, 2026-06-15). aggTrade runs the non-close per-message path orders of magnitude more often than 1s kline, so a synchronous optimizer on the event loop becomes an acute stall risk during the volume bursts that matter most. Step 15 is now executed *inside* Phase 5 (19.4) — after the wiring (19.3), before the schema cutover (19.5); its verify gate folds into 19.3/19.4's per-message latency measurement. It is no longer a standalone Phase-4 item, so Phase 4 is now Steps 16–18.
**Problem:** `optimize_bucket_size` (a 20-step × 2h-window optimizer) runs synchronously on **every bucket close**, on the asyncio loop, during exactly the bursts when the daemon must drain the socket.
**Directive:** Decouple it — run the optimizer on a periodic interval (e.g. every few minutes) or in a thread executor, not inside `_close_active_bucket`. The 2h window barely changes between consecutive closes, so per-close recompute is wasted work. Keep the per-close VPIN/velocity bookkeeping inline; only the optimizer moves.
**Verify:** Under a synthetic burst of rapid closes, the daemon's per-tick latency stays flat; `target_vol` still adapts on its slower cadence.

### Step 16 — Cluster DOM once per frame (`footprint_layers.DepthWallLayer`)
**Problem:** `_cluster` runs 4× per frame on the same depth payload.
**Directive:** Cluster each side once at the top of `update_data` into locals; reuse for both the max computation and the draw loop.
**Verify:** Identical visual output; cluster call count drops to 2/frame (one per side).

### Step 17 — Cache imbalance/iceberg zones on bucket-close, not 20 Hz
**Problem:** `ImbalanceLayer` and `IcebergLayer` rebuild all zones from the full footprint cache every render frame. Historical zones don't change between bucket closes.
**Directive:** Compute the zone set once when a new bucket finalizes (signature-gated on the closed-bucket version counter already in `pipe_client`), cache it, and have the 20 Hz paint path only re-pin/redraw the cached geometry. Mirror the COW/version pattern already used for `closed_buckets`/`order_blocks`.
**Verify:** Pan/zoom on a busy chart stays smooth; zone set updates exactly on bucket close, not per frame.

### Step 18 — OB mitigation loop efficiency (engine: `calc_quant_obs`)
**Problem:** `for ob in obs: for b in buckets:` with a `parse_ts` (strptime) inside the inner loop, over up to 10k buckets — re-scanned from scratch every recompute.
**Directive:** If Step 8's incremental `{ob_id: remaining_volume}` design is in place, the per-close mitigation already costs O(active_obs) and this step is mostly done — verify no full-history rescan remains on the hot path. For any residual from-scratch scan (e.g. OB *detection*), hoist `parse_ts` out of inner loops (parse each confirm time once) and exploit time-ordering to break early. Don't change mitigation *semantics* (Step 8 owns that).
**Verify:** OB recompute time is flat as bucket count grows toward the cap; no per-close operation is O(history).

---

## PHASE 5 — The architectural decision (the fidelity ceiling) — **PROMOTED: next milestone after Steps 3–4**

> **Re-prioritized (trader's call):** the operator reads these charts as a live *pulse* and has stated the 1-second kline cadence sits right at the threshold their brain can consciously count — i.e. their edge needs sub-second, order-by-order flow to *feel* rather than *count*. That makes aggTrade not a "someday polish" but the real product target. **Sequencing decision:** still do it AFTER Steps 3–4 (the pulse-critical math on the existing source), NOT before — swapping the data source while the consuming math is still being corrected would confound every verification (was it the fix or the new source?). Once Steps 3–4 are clean and the rate/pulse rendering is built and validated against the frozen baseline, aggTrade becomes a clean source-swap that flows sub-second tape into an already-trusted pulse. Phases 3–4 (visual/perf) can run before OR after this, the operator's choice; but aggTrade comes right after Step 4.

### Step 19 — kline → aggTrade for true order-by-order fidelity (SCOPED — next major milestone)
**The honest truth:** Steps 1–4 make the system *internally correct and honest given 1-second kline data* — but every "tick" is still a 1-second delta with its volume smeared onto a single close price. Footprints, imbalances, icebergs, per-level POC, OB bands, AND the per-bucket pulse rate are all reconstructed from that. They remain *1-second approximations of the tape* no matter how clean the math is. The **scalar** modes (volume, CVD, VPIN, OI-based vectors) run on accurate aggregate data; only the **level-distribution and sub-second pulse** signals need this — which, for this operator, is the core of the edge.
**Directive (its own mini-project, own plan, own before/after):** Add an `@aggTrade` (or `@trade`) websocket in `feeds.py`; route each trade (true price + qty + buyer-maker flag) into the footprint levels and `process_tick`, with klines retained only for OHLC/candle framing and OI alignment. This raises message volume by orders of magnitude — the per-tick hot path must stay allocation-light. Validate by comparing an aggTrade-built footprint against the kline-built one for the same window. **Note:** the event-time clock (Step 1) and conservation clamps (Step 2) already make the per-trade timestamps and ratios correct, so aggTrade slots into a clock and a decomposition that are already right.
**Recommendation:** Do Phases 1–4 first and trade on the now-honest system. Schedule Phase 5 deliberately once you've decided the level-fidelity signals are worth the rewrite. Do **not** let it block the foundational fixes.

**Approved staging (operator-signed, `pipeline-integrity`, 2026-06-15):** 19.0 capture harness (throwaway recorder → real raw-tick tape) · 19.1 pure trade→`process_tick`-args mapper · 19.2 OI pending-balance attributor (scale-free cap + per-trade Step-2 clamp) · 19.3 wire `@aggTrade` into `feeds`, demote klines to framing, dedicated 150 ms live-edge throttle (gate **measures** per-message latency on a tape replay, not just invariants) · **19.4 = Step 15 pulled forward** (recalibrate/OB off the hot path) · 19.5 schema bump 2→3 + `history.db` wipe (deliberate fidelity cutover, not a field change) · 19.6 kline-vs-aggTrade side-by-side visual sign-off. Per-sub-step detail + the OI cap/decay semantics live in `scripts/HANDOFF.md` §8.

---

## PHASE 6 — Hygiene

### Step 20 — Remove or wire the dead VPIN scalar
**Directive:** `engine.vpin` (the `target_vol`-polluted scalar) is never displayed — every visible VPIN recomputes correctly. Either delete it and its serialization, or, if kept for any consumer, recompute it the same way the display does (`Σ|buy−sell| / Σ(curr_vol)` over the window) so engine and display can never disagree.
**Verify:** No mode reads a different VPIN than Mode 6 produces.

### Step 21 — Final full-harness pass + clean history
**Directive:** Re-run the 0b harness across all 5 timeframes. If you chose 0c Option A, wipe `history.db` now and let it re-accumulate under the corrected math. Capture an "after" baseline next to your Phase 0 "before."
**Verify:** All invariants green on freshly accumulated buckets across every timeframe.

---

## Mode → fix dependency map (which screens each fix makes trustworthy)

| Mode | Trustworthy after |
|------|-------------------|
| 1 open_pos / 2 close_pos | Steps 2, 3 |
| 3 exhaustion | Steps 2, 3, 4, 5 |
| 4 kinetic | Step 1 |
| 5 volume | already accurate (kline taker data) |
| 6 vpin | already correct (just Step 20 for hygiene) |
| 7 / 8 bucket open/close pos | Steps 2, 3 |
| 9 effort_result | Step 4 |
| 10 bucket_canvas (candles/OB/vpin) | Steps 1, 2, 4, 6, 8 (+ Step 19 for true level fidelity) |

---

## After the pipeline is solid → the selection tool
**This section IS the capstone's final step (see "Mode 10 is the map", top of this plan): overlays consolidate onto Mode 10 ONLY after Phases 2–3 make each one correct — never before — then we build the tool.**
Once Phases 1–4 are green, we design the Mode 10 selection tool against the corrected scalars:
- It reads bucket **scalars only** (`BucketSnapshot`), never per-level `levels` (those aren't in the snapshot). Its absorption/iceberg-style states must lean on `buyer_er/seller_er` (now the robust dispersion-based version) — if that proves too blunt for an area classifier, we add a derived scalar to the bucket (e.g. a POC concentration ratio) rather than shipping `levels` to the client.
- Its VPIN aggregation uses the correct `Σ|b−s| / Σ vol` formula over the selection.
- Its velocity profile reads `start_time/end_time` (now real, post-Step 1), not stored `vol_mult`.
- Gemini's 50-state taxonomy becomes the *classifier spec* at that point — and we'll audit it the same way we audited the flaw list, because several states assume data the kline source can't actually deliver.

**Start here:** Phase 0 (0a → 0b → decide 0c), then Step 1. Don't move past a step until its verification gate is green and you've eyeballed the harness output.