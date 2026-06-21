# Pipeline-Integrity Handoff — read this first

You are continuing a multi-step data-integrity rework of a native order-flow
trading terminal for **SOLUSDT perps**. This note orients a fresh session without
re-deriving anything. **Phases 1 and 5 are COMPLETE — the terminal is aggTrade-native and LIVE on the
real v3 `history.db`. Phases 2–4 are now done/subsumed (the OB-layer rework subsumed Phase 2; the
absorption layer replaced the Phase-3 iceberg steps and the rest of Phase 3's layers were deleted with
the time chart; the perf work covered Phase 4), and the time-chart removal is COMPLETE. Next is the
deferred queue (§1.5): target_vol clamp → the Mode-10 selection tool (OB polish is done).**

---

## 0. What this project is building — Mode 10 is the capstone (read first)

**Reframing (operator's core realization, 2026-06-16) — the destination behind every
phase.** Mode 10, the volume-bucket canvas, is **NOT** scanner mode #10. It is the
**PRIMARY MAPPING SURFACE** and the **CAPSTONE** the whole project builds toward.
Everything else — the scanners, the time-candle, Phases 1–4 — is **infrastructure that
exists to make Mode 10 trustworthy.**

- **Time-candle → VESTIGIAL once Mode 10 carries the map.** Time candles misrepresent
  the auction: a 50-vol minute and a 50,000-vol minute draw the **same width**. The
  **volume bucket is the native, truthful unit** (width = activity). The time-chart is
  scaffolding kept only until the bucket canvas carries everything.
- **TradingView STAYS — deliberate external decision/drawing surface.** We do NOT
  rebuild a drawing/execution tool. Division of labor: **scanners = sensing · Mode 10 =
  the level map** (exists nowhere else on the market) **· TradingView = marking +
  execution.** Don't scope-creep Mode 10 into a charting package.
- **Sequencing to the capstone (overlays migrate onto Mode 10 ONLY AFTER their logic is
  fixed, never before):** (1) **cheap validation NOW** — footprint + true POC render
  correctly on the Mode 10 bucket canvas; (2) **Phase 2 (OB fidelity) + Phase 3 (visual
  layer)** make every overlay CORRECT; (3) **capstone** — consolidate all finished
  overlays onto Mode 10 + build the selection tool (MASTER_FIX_PLAN "After the pipeline
  is solid").

Changes no step's math — it sets the destination (§2 "what's next" feeds it) and the
rule: **no overlay onto Mode 10 before its logic is corrected.**

---

## 1. Where things stand

- **Branch:** `pipeline-integrity` (off `main`/`master` baseline `6ddfc5e`). All work lands here.
- **Commit list (Phase 0 + Phase 1, in order):**
  ```
  6ddfc5e  baseline: pre-integrity-fixes (verbatim legacy state)   <- rollback point
  84eda62  phase-0: baseline diagnostic + 1m/1h before-fix reports
  f63b969  phase-0: baseline reports 5m/15m/4h before-fix
  7895eb1  docs: add master fix plan (source of truth)
  345676a  step-1: event-time velocity clock + degenerate-duration guards (engine/feeds)
  7eab798  step-2: delta-OI + taker-buy conservation clamp at feeds boundary
  95a4503  docs: update plan — Step 3 rate view + Phase 5 promotion
  6faec23  step-3a: honest churn decomposition (OI-confirmed vectors + explicit churn field)
  98801ba  step-3b: per-bucket pulse rate view + secondary-axis churn (modes 1/2/7/8)
  149f946  scripts: step-3 decomposition comparison tools
  e038528  step-4: adaptive effort/result via volume-weighted dispersion (engine)
  93286a0  step-5: adaptive exhaustion via E/R z-score + smooth multipliers (mode 3)
  ```
- **`data/history.db.before-fixes` is the FROZEN pre-fix baseline. NEVER write to it.**
  It is the immutable "before" reference; gitignored (the whole `data/` dir is).
- **The working `data/history.db` is now LIVE at schema v3 (aggTrade-built).** The 19.5
  cutover wiped the old kline buckets (footprints kept); the daemon re-accumulates
  aggTrade history. **The build-time "test on an ISOLATED empty DB" caution (§4) is
  LIFTED** — it existed only to protect the working db BEFORE the cutover, which is
  done; run/test against the real db now. (`before-fixes` stays frozen regardless.)
- **Run it live (how to test):** `python -m app.daemon` (headless core, binds
  `127.0.0.1:9999`) then `python -m app.terminal` (GUI; `Ctrl+N` spawns more windows).
  The daemon now boots clean on the v3 db (no re-wipe — v3 == v3). A running daemon is
  read by `scripts/baseline_diag.py <tf>` for the 5 invariant distributions.

## 1.5 — CURRENT DEEP-DIVE (2026-06-19): Mode-10 overlay layers (OB ✅ · TRAP ✅ · ABSORPTION ✅)

Since the Phase-1 commits above, all work has been the **Mode-10 overlay layers**. **START HERE.**

### ✅ ABSORPTION / "iceberg" layer — COMMITTED (the just-finished deep-dive)

**WHAT IT IS:** a whale-absorption detector. Heavy, sustained, one-sided aggression at a price level
that **HOLDS** (price fails to break it) = a hidden iceberg defending the level. Replaced the old
over-firing per-bucket iceberg heuristic ("sea of icebergs").

**THE DEFINITION (validated on real data):** trigger = C1 ∧ C2 ∧ C3 ∧ C4 —
- C1 HEAVY: dominant-side absorbed ≥ `κ × median bucket vol`.
- C2 SUSTAINED: ≥ 4 hits (`nmin`), no single bucket > 60% (`rho`) of the absorbed.
- C3 ONE-SIDED: dominant side ≥ 75% (`sigma`).
- C4 HELD: vol beyond the level ≤ 15% (`beta`).
Buy-aggression that holds → a SELL mark (a sell wall absorbed buyers); sell-aggression → a BUY mark.

**BUCKET-NATIVE (THE KEY ARCHITECTURE — the root fix):** `calc_absorption(buckets, …)` computes on
`engine.closed_buckets` (NOT 1m footprints), anchors marks on `bucket.start_time`, dies on the bucket
`close_price` close-through (close-rule + 0.10% buffer). WHY: the original ran on 1m TIME candles while
Mode 10 renders VOLUME buckets — the time-vs-bucket axis mismatch (`ts_to_idx` slippage, same class as
the OB floaters) caused floating marks, wrong-place/wick-looking deaths, and missing early-bucket
coverage. Bucket-native fixed all of it: ONE shared axis; closed buckets always carry a close (no
forward-only-close gap); full early coverage (levels on every bucket).

**THE FLOOR (LOCKED, dollar-anchored):** **κ=0.80 + a hard $250k filter** (`t1_usd`). The κ number
changed meaning across units (1m median 14,648 → bucket median ~9,900 SOL), so the floor is set in
DOLLARS: κ=0.80 reproduces the 1m unit's ~$270k all-whales floor (min $272k, zero sub-$250k); the hard
$250k holds the line regardless of market drift. Validated: 107 marks, 26.7/8h, min $272k.

**LIFECYCLE (stateless replay, like `calc_quant_obs`):** marks are STANDING LEVELS, born at a bucket,
die on a **decisive close-through** (a bucket close past the level by the 0.10% buffer — NOT a wick).
`$ = PEAK window absorbed`, paired with κ (both taken at the peak-$ window — do NOT switch to cumulative).

**RENDER — LINES not boxes** (an iceberg IS a price level, not a zone). `AbsorptionLayer` in
`chart_widgets.py`: a horizontal line at the cluster **POC** (`price` field = heaviest-absorbed level),
color = side (blue buy `(41,121,255)` / orange sell `(255,145,0)`), **thickness = κ continuous**
(`_absorb_thickness`: κ0.80→1px … κ4.6→6px), label = `$X (κN.N)`. **Cell-boundary span: `x0 = birth−0.5,
x1 = death+0.5`** (the half-bucket fix — candles are centered at index i with half-width 0.5; the old
`x0=birth / x1=death+1` caused the offset + the overshoot). Merging = **Option A** (sequential same-price
= separate defenses; concurrent already merges via the detector `hit` logic; $ = peak).

**KEY FILES/FUNCTIONS:**
- `quant_engine.py` — `calc_absorption(buckets, kappa=0.80, …, t1_usd=250_000.0)` → marks
  `{id, plo, phi, price, side, absorbed_usd, tier, kappa, birth, end, active}`; `_absorption_clusters`
  returns `(plo, phi, side, absorbed, kappa, poc)`.
- `feeds.py` — `_absorption_marks(tf)` passes `engine.closed_buckets`; `absorptions=` on the
  recompute_loop ObPacket, CatchupStart, CatchupPacket.
- `chart_widgets.py` — `AbsorptionLayer.update_data_indexed` (lines render); `_absorb_thickness`,
  `RGB_ABSORB_*`, `_ABSORB_CAP_H`.
- `pipe_client.py` — `self.absorptions` mirrored on the ObPacket **ELSE branch only** (full-recompute;
  NOT the close-piggyback `if pkt.new_buckets:` — that ships `absorptions=[]` and would wipe them) +
  `snapshot()["absorptions"]`.
- `terminal.py` — `self.bc_absorption`; the `m10_icebergs` draw block (gated, zValue −6) calling
  `bc_absorption.update_data_indexed(snap["absorptions"], x[-1], _ts_to_idx, view)`; redraw-gate
  `abs_sig = (id, active, end, round(kappa,2), price)` in `_draw_scanner` (so a death/thickness/POC change
  repaints — without it, a death didn't repaint in a quiet market).
- `hamburger.py` — `m10_icebergs` toggle "Absorption", default ON.

**COMMITTED — the absorption layer is DONE.** Shipped across `d284e62` (original 1m detector), `b03b2f7`
(broadcast on ObPacket), then the **bucket-native completion (2026-06-19)** in two commits after a docs
commit:
- **DETECTOR (daemon):** `quant_engine.py` — bucket-native `calc_absorption` (reads `engine.closed_buckets`)
  + the POC in `_absorption_clusters` + the **κ=0.80 / $250k (`t1_usd`) floor**; `feeds.py` —
  `_absorption_marks` → `engine.closed_buckets` (was the 1m `footprints_db`).
- **RENDER+CONSUMER (terminal):** `chart_widgets.py` — the **LINES render** (`AbsorptionLayer`) + the
  cell-boundary span (x0=birth−0.5, x1=death+0.5) + the `_absorb_thickness` κ→px map + **the Bug-1 cap
  removal**; `pipe_client.py` — consumer cache (`self.absorptions` + `snapshot()`, ELSE-branch store);
  `terminal.py` — wiring (`bc_absorption` + the m10_icebergs draw block) + the redraw-gate `abs_sig`;
  `hamburger.py` — the `m10_icebergs` **"Absorption" toggle** (default ON).

**RESOLVED BUGS (the deep-dive's closing work — diagnosed READ-ONLY, then confirmed LIVE):**
- (a) **Stray vertical end-cap — FIXED (removed).** On a LINE the ending itself IS the death signal, so the
  inherited box-era cap block (`drawLine(QLineF(x1_death, price±_ABSORB_CAP_H …))`) was deleted from
  `AbsorptionLayer.update_data_indexed` (the `_ABSORB_CAP_H` constant stays — still boundingRect y-padding).
- (b) **2 "floating" line-starts — DISPROVEN (no bug, no code change). The clearest case yet for the
  prove-it-LIVE discipline.** TWO independent analyses (this session AND the architect) convergently
  diagnosed a **bisect-tie** from code: `_ts_to_idx`'s `bisect_right(start_times, birth)−1` resolves a
  non-unique `start_time` to the RIGHTMOST tied bucket, so a mark born into a tie-cluster would overshoot
  its true birth cell. Clean, convincing, and **WRONG.** Read-only LIVE instrumentation
  (`data/absorption_live.log`: per-mark birth→idx + a TIE flag + a CLAMPED flag) showed **`TIE=False` on
  every mark, every frame.** The real cause was the **viewport CLAMP** `x0c = max(x0, vx0)`: a mark born
  off the LEFT edge correctly starts at the screen edge (which lands mid-cell when the pan/zoom leaves the
  edge off a half-integer). The 2 floaters were exactly the 2 `CLAMPED=True` marks — one an ACTIVE whale
  still defending its level off-screen, so clamping (not hiding) is correct, and it matches the OB boxes'
  `max(confirm_x, vx0)`. **DECISION: leave as-is — the anchoring was never broken.** Had we "fixed" the
  convergent hypothesis we'd have changed the SHARED `_ts_to_idx` (risking OB regression) for a bug that
  does not exist on screen. Instrumenting instead of fixing is what caught it.
- **⚠️ LATENT HAZARD (real, not yet triggered) — the bisect-tie itself.** Non-unique `start_time`
  (`terminal.py:~1033` already flags it) means `_ts_to_idx` WOULD mis-anchor a mark born into a genuine
  sub-second tie-cluster — it just didn't fire in this session's data (buckets 60–70s apart). In a
  fast/bursty market it will. Known issue for future hardening of the SHARED mapper (disambiguate ties —
  resolve to the FIRST tied bucket, or carry a stable bucket ordinal); affects OB too, so verify no OB
  regression when fixed. Do NOT lose this finding.

### ✅ OB LAYER — COMPLETE (committed)
Exact-epoch lifespan boxes (`start_epoch`/`confirm_epoch`/`end_epoch`), b0 anchor (`start_epoch =
b0.start_time`), close-based progressive erosion mitigation. Commits `d2a99a8`, `73b01c5`, `0c455a5`,
`3728952`. Subsumes Phase 2 (OB fidelity) + the Group-D gray-OB item.

### ✅ TRAP (B) — COMMITTED
TRAP states gate on the trapped side OPENING (`trappedOpen` factor). Commit `3333226`. Live-verify during
state calibration.

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

**EXE REBUILT at `e272b71`** (through adaptive VPIN) — `dist/OrderFlowTerminal.exe` (88 MB), smoke-tested
(launches clean; bundles Magic Selection + STATE line + E/R fix + adaptive VPIN). Clean refresh point since
Phase-2 building is on hold.

**Phase 2 — the 4 state extensions: DEFERRED behind a candidate accumulator (NOT abandoned).** Scoped +
instrumented on real SOL data; ALL FOUR fail the grounding-with-discrimination gate *now*:
- *#4 Passive Floor/Ceiling Iceberg*: marks ARE reachable (`snap["absorptions"]`: price-band + time + tier,
  time→index mapper at `terminal.py:2442`) but it's a snapshot **OVERLAY**, not a per-bucket scalar → a
  context badge, not an argmax state. **No-go as a state.**
- *#1 Hidden Bullish Accumulation, #2 Hidden Bearish Distribution, #3 Whale Wars*: REAL patterns (genuine
  instances are legit) but structurally RARE on SOL (~74% churn median → OI-directional signal is only ~26%
  of volume) — only **~1–2 distinct events in 3600 buckets**, and they **vanish at selection sizes ≥40**. Too
  few to ground without confirmation bias. Tight-vs-loose tradeoff is inherent (tight=faithful=rare,
  loose=steals from the 12). **No-destabilization confirmed**: tight defs fire only on windows the 12 call
  ROTATION/NEUTRAL — never STRONG/traps; single buckets untouched → n=1=hover holds.
- *Path*: a **passive candidate accumulator** (VM cron, read-only on `history.db`; logs loose-pattern windows
  + factor vector + the `state_12` overlap signal to `data/pattern_candidates.jsonl`; needs a small pure
  `app/region_state.py` refactor extracting `_synth_bucket`/`_exhaustion_mults` from the GUI module).
  **PROPOSED, not built** — gathers instances 24/7; revisit at ~20–50 distinct events/pattern to ground +
  prove discrimination, then build only the winners.

---

### DEFERRED QUEUE (reordered 2026-06-19)
1. ✅ **Time-chart removal — DONE (all phases A/B/menu/relabel/C/D).** Completed after the absorption
   dive; Mode 10 (`BucketCandleItem`) is the sole candle surface (full record in the "⚠️ TIME-CHART
   REMOVAL" block below). **Active queue head is now the `target_vol` clamp / item 3 selection tool —
   item 2 (OB polish) is DONE.**
2. ✅ **OB polish (A) — DONE.** (a) toggle bug RESOLVED by MERGING the two toggles into one "Order
   Blocks" (`55fb663`), not the originally-planned independent `show_live`/`show_dead`. (b) min-render-
   height SHIPPED (`4435c1d`: thin OB zones floor to a 7px band at wide zoom, see refinements record).
   (c) duplicate-timestamp/bisect-tie assessed + deliberately SKIPPED (rare exact-ms collision,
   ≤1-bucket, data-ambiguous — not worth it; `bisect_left` is the only tweak and adds no correctness).
3. **Mode-10 selection tool (D)** — the capstone (see §2 / the plan's "After the pipeline is solid").
- **✅ Mode-10 UI refinements — DONE (2026-06-19).** Operator-driven stats-box / toggle cleanup:
  (1) **OB toggle bug fixed** — "Order Blocks" + "Dead OBs" MERGED into one "Order Blocks" toggle
  (alive solid + dead faded together; OFF hides both), `55fb663`. (2) **State-debug calib in the stats
  box by default** — "State Debug (calib)" toggle dropped; the STATE readout always shows the
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
  min-render-height** — thin order-block zones floor to a 7px DRAWN band at wide zoom (expanded
  symmetrically around the zone center; true top/bottom unchanged), no more sub-pixel slivers, `4435c1d`.
- **DOM book-mid line (idea, surfaced during the Phase-A DOM port) — DEFERRED, not Phase A.** The
  Mode-10 spot line is the last TRADE (`closes[-1]`); on a depth ladder the more useful reference is
  often the book MID (between best-bid/best-ask), so the reference sits in the spread between the COB
  bands. Idea: KEEP the last-trade line (honest "last print") and ADD a thin mid line (or best-bid/
  best-ask markers) so the spread + where the last trade sits vs the live book are both visible. NOT a
  bug (the COB is correctly aligned; this is an enhancement). Consider after the time-chart removal.
- **Alerts re-wire (surfaced during Phase B) — DEFERRED.** `alerts.feed` (OB/liq notifications) fired
  ONLY in the old Off-branch, which Phase B severed — so alerts already didn't fire in Mode 10 (the
  default) and now never do. Re-wire `alerts.feed` into the scanner / Mode-10 path as a later
  follow-up (after the structural removal work). Not lost.
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
- **🔴 HIGH-PRIORITY (not urgent) — `optimize_bucket_size` produces absurd/unstable `target_vol` for
  higher tfs. DEFERRED to its own focused turn (do NOT interrupt the time-chart removal).** Root cause:
  `optimize_bucket_size` (quant_engine.py) sets `max_test_v = avg_node_vol*15` and assigns the
  variance-max `best_v` UNCLAMPED → higher-tf footprint nodes (whole-candle volume) balloon `target_vol`
  to 648K/1.47M SOL (15m/1h), while 4h sits stuck at the 5K default. NOT PC-off corruption (SQLite WAL
  is crash-safe; it's a legitimately-computed bad value). It PERSISTS across restarts because rehydrate
  trusts `engine_state.target_vol` verbatim (no clamp, no replay). **FIX = the CLAMP, not rebuild:**
  (a) §0.6 sanity-clamp inside `optimize_bucket_size` (bound `target_vol` to a scale-free range — a sane
  multiple of `DEFAULT_TARGET_VOL` or the cross-tf median, outlier-resistant); (b) validate/clamp
  `target_vol` on rehydrate (reject absurd persisted values). Rebuild-from-footprints is NOT the fix —
  it re-runs the same unclamped logic. **1m is UNAFFECTED** (stable ~4,419, 14% spread) — and 1m is the
  live-trading default — so this is high-priority but does NOT block the current workflow.
- **💡 FEATURE (potential, worth revisiting) — MULTI-TIMEFRAME OB CONFLUENCE.** Showing order blocks
  from several timeframes at once (was spec §7.2.2; the old web app's STRONGEST original idea —
  cross-tf OB confluence scoring). The native rewrite DROPPED it (the daemon went single-tf-per-client
  via `set_tf`). The dead "OB Overlay Timeframes" checklist (REMOVED in the hamburger cleanup — it was a
  no-op stub: `obTfsChanged` had zero subscribers) was its last UI trace. To revisit: needs real daemon
  work (per-client multi-tf streaming OR a merged cross-tf OB feed), THEN re-add the checklist (trivial
  next to the daemon work). The cross-tf OB confluence was genuinely valuable — don't lose the idea.
- **✅ TERMINAL PERFORMANCE — DONE (2026-06-19).** Profile-first with a live per-frame probe (TEMP, removed
  after). Before: **~2 FPS lurching**, `period` spiked to **525ms / p95 1.6s** on load-history-then-pan at
  N≈2800. After: **steady ~16 FPS (~63ms), NO spikes** with footprint+OBs+icebergs+COB on and during pan,
  across 4–6 windows on the i7-8565U. The probe overturned THREE wrong guesses (DOM-compute, candles-as-only-
  cost, walls-as-floor) — the discipline: *trust the re-measure, not the instinct* (footprint redirect: instinct
  "compute", measured text/paint). Four commits, one concern each, re-measured after each:
  - **#1 candle viewport-cull `cbb1a55`** — paint only visible candles + `set_view` re-cull on pan; isolated
    pan paint **408→~90ms**.
  - **#3 static closed-bucket compute cache `ff131a7`** — VPIN/vel/OHLC/EMA computed once on close, only the
    live edge recomputed (trailing-window-final, equivalence-tested); draw ~1ms.
  - **footprint `551cb36`** — TextPool skip-unchanged + numbers≤40 / top-3 bubbles≤200 / none, POC on the
    detail gate; items +340 paint +230ms → negligible.
  - **#2 wall gate `9462c63`** — `DepthWallLayer.update_data` gated on (drawn-walls, viewport, threshold);
    `dirty_main` 120→14-16 idle.
  - **The ~63ms floor is the OS timer** (Windows 15.6ms granularity rounds the 50ms `GUI_TIMER_MS` to ~62.5ms),
    NOT our code. 16 FPS is the deliberate target for the low-power multi-window laptop; the fixes removed the
    *work that stretched the period under load* (the spikes), not the floor.
  - **Deferred follow-ups (LOW priority, recorded — not needed now):** (1) **COB gate** — COB panel when open
    repaints ungated every frame (`dirty_cob`~100-150, `dom`~6-7ms), same shape as the walls; gate
    `cob.update_depth` on the depth sig. (2) **OB/iceberg re-loop** — already culls the DRAW but loops all
    obs/marks each frame; negligible now, only matters at thousands; skip off-screen in the loop then.
    (3) **higher-FPS lever** — `timeBeginPeriod(1)` + lower `GUI_TIMER_MS` → 30-60 FPS; NOT wanted on the
    current laptop/multi-window setup (4–6× CPU), recorded for a stronger machine.
- **State-engine calibration — DEFERRED to LIVE trading** (feel it over days; see the plan's item-4 arc).

### THE WORKING DISCIPLINE (critical — preserve it)
- **⚠️ THE #1 TRAP — YOU WILL HIT IT ON THE 2 RENDER BUGS: when a HEADLESS test passes but the LIVE
  SCREEN still shows the bug, TRUST THE SCREEN. Instrument the LIVE running process — do NOT re-prove
  headless.** This recurred MANY times in the absorption dive: the **geometry raster** test, the
  **`PipeClientWorker` store** test, AND the **redraw-gate-sig** test ALL passed headless while the
  operator's screen stayed broken — each time the real bug was a level deeper that ONLY live
  instrumentation found (the pipe_client field-drop, the gate skipping a repaint, the time-vs-bucket axis
  mismatch). **For the 2 open render bugs (stray end-cap, floating starts): instrument the LIVE
  `AbsorptionLayer.update_data_indexed` / the live render — log what each mark RECEIVES (active/end/birth/
  price) AND how it actually DRAWS (x0/x1/skip/cap), exactly like the `data/absorption_live.log`
  instrumentation used during the dive. Do NOT spin up another headless harness and call it proven.** The
  operator's "look at the screenshot, it's still wrong" caught EVERY foundational bug — defer to the screen.
- **One step, one commit. Propose before build. HOLD for live sign-off before committing.**
- **The volume bucket is the honest unit; time candles are "a big lie"** (equal width ≠ equal activity) —
  why bucket-native won and why the time-chart removal is promoted.

### IMMEDIATE NEXT STEPS (a fresh session starts exactly here)
1. **Absorption layer — DONE & COMMITTED (2026-06-19), 3 commits: docs, detector, render.** Bug-1 fixed,
   Bug-2 disproven (see RESOLVED BUGS above), live-verified on screen before commit.
2. ✅ **Time-chart removal — DONE** (all phases; see the "⚠️ TIME-CHART REMOVAL" record in §1.5).
   **Active next item:** the target_vol clamp, then
   the Mode-10 selection capstone.

---

## 2. Source of truth + what's next

- **`MASTER_FIX_PLAN.md`** (project root) is the authoritative plan. Read it. Every
  step references it. It is committed (`7895eb1`, updated `95a4503`).
- **Phase 1 (Steps 1–5) is DONE.** The corrected scalars every scanner mode reads
  are now accurate/honest.
- **Phase 5 (Step 19, kline → aggTrade) is DONE — the terminal is aggTrade-native
  and LIVE.** All sub-steps committed (19.0–19.6), the working `history.db` cut over
  to v3 (kline buckets retired, aggTrade history accumulating), suite 9/9. The data
  path is now true order-by-order: event-time sub-second clock, exact per-trade taker
  split, OI pending-balance attribution, recalibrate/OB off the close hot path,
  150 ms live edge, true-price footprint levels. 19.6 confirmed the gain by eye
  (aggTrade relocates the POC to the real volume peak on travel; converges with kline
  when quiet). Full Phase-5 record + commit list = **§8**.
- **NEXT — the deferred queue (§1.5), not a phase pick.** The Phase 2/3/4 structure is SUBSUMED —
  kept here for the dependency map only:
  - **Cheap validation — ✅ DONE:** footprint ladder + true POC render correctly on Mode 10.
  - **Phase 2 — OB fidelity (Steps 6–8) — ✅ DONE/subsumed** by the OB-layer rework (Step 8 =
    progressive close-based erosion; the band fix folded in). Step 7 (Otsu) was always an optional
    experiment.
  - **Phase 3 — visual layer (Steps 9–14) — MOSTLY OBSOLETE:** Steps 9/14 (`ImbalanceLayer`), 10/11
    (`IcebergLayer`), and 13 (time-chart `FootprintLayer`) targeted classes DELETED with the time
    chart; the absorption layer replaced the iceberg heuristic; the Mode-10 footprint text-cap was
    handled in the perf work; the Off-mode candle bug is moot (Off mode is gone). Only **Step 12**
    (DOM per-side normalization — `DepthWallLayer` survives) remains, as an OPTIONAL "your-call"
    tradeoff.
  - **Phase 4 — perf (Steps 15–18) — ✅ DONE:** Step 15 shipped as 19.4; 16/17/18 = the four perf
    commits; residual = the LOW perf follow-ups recorded in §1.5.
  - **Then the capstone** — the Mode-10 selection tool, built against the corrected scalars on the
    now-clean overlays (MASTER_FIX_PLAN "After the pipeline is solid").
- **Where trustworthiness landed (post Phase-1 + aggTrade):**
  - **HIGH-CONFIDENCE now:** Modes 1/2 (open/close pos), 3 (exhaustion), 4 (kinetic),
    5 (volume), 6 (VPIN), 7/8 (bucket pos), 9 (effort/result) — honest Phase-1
    scalars now flowing from true order-by-order aggTrade; footprint LEVELS (POC,
    dispersion E/R) are true-price. (Mode 4 carries the vol_mult watch-item.)
  - **PENDING Phase 2:** Mode-10 ORDER BLOCKS — band width (6) + mitigation (8) are
    still legacy; the OB *inputs* are true-price now, but the band/lifecycle math is
    unfixed.
  - **PENDING Phase 3:** footprint-LAYER renderings (imbalances, icebergs, DOM walls,
    footprint text, imbalance gaps) — fed true-price input by aggTrade, but the layer
    logic (9–14) + the Off-mode candle bug are unfixed.
- **WATCH-ITEM carried forward:** vol_mult burst tail (possible cap for OB
  `power_score` / neon intensity) — §8. Watch live before deciding.

## 3. Standing rules that govern EVERY step (from MASTER_FIX_PLAN §0)

- **§0.3 — Atomic schema change = TWO serializers + a version bump.** A bucket field
  lives in BOTH (a) the **wire** schema (`protocol.BucketSnapshot`, produced by
  `QuantBucket._assemble`/`live_snapshot`) and (b) the **persistence** schema
  (`persistence._bucket_to_dict`/`_bucket_from_dict`). They are SEPARATE, hand-kept
  serializers. Any field add/meaning-change must update **both**, every `terminal.py`
  consumer, **and bump `persistence.BUCKET_SCHEMA_VERSION`** (currently **3** — v3 =
  the 19.5 aggTrade fidelity cutover), in ONE commit. The boot-time schema guard in `persistence.rehydrate_engines` clears
  stale-version bucket/OB/engine tables (footprints kept) so old-meaning rows never
  silently rehydrate. `_bucket_from_dict` uses `d.get(...)` with no per-field guard,
  so a half-applied schema change corrupts history without raising.
- **§0.6 — Degenerate-input contract (mandatory for any divide-by-something).**
  (1) Zero/near-zero denominator → floor at a *physically meaningful, scale-free*
  value (1.0 tick for tick-effort; a coefficient-of-variation `c·|mean|` for a std),
  NEVER an arbitrary epsilon or fixed absolute. (2) Cold start / under-filled window
  → require `len >= MIN_WINDOW` before firing; below it, neutral (multiplier = 1).
  (3) Outlier ingestion → the rolling baseline must be outlier-resistant (median or
  pre-capped/tanh-bounded) so one spike can't poison the window.
- **§0.7 — How to verify engine-math fixes (the rehydration trap).** Rehydrated
  buckets store the OUTPUT scalars (opL/opS/vel_mult/buyer_er/…) computed by whatever
  math was live when they closed. So **re-reading `history.db` does NOT test new
  math** — it shows old-math numbers. There is no raw-tick store to replay. Verify in
  this order: **(1) synthetic unit test FIRST** (authoritative — construct a known
  tick/bucket sequence, assert the EXACT expected output, history-independent), then
  **(2) live-accumulation sanity** on a fresh daemon's NEW buckets. `before-fixes` is
  the frozen reference, never overwritten.

## 4. The verification pattern we've used every step (follow it)

- **One step → one synthetic test → user eyeballs → one commit.** Minimal diff, no
  drive-by refactors. Every engine edit carries a `# DIVERGES FROM LEGACY: <reason>`
  comment (the legacy "verbatim port" guardrail is retired — see §0.2).
- **Synthetic test FIRST**, in `scripts/test_stepN_*.py`, asserting the EXACT
  discriminating property (not just "runs"). This is the regression suite.
- **Run the FULL suite at every commit, not just the new step's test.** A later
  step can silently break an earlier step's assertion — Step 3's 4→5-component
  conservation change broke the Step-2 test and it went unnoticed until all five
  were re-run together.
- **Live checks use an ISOLATED empty-DB daemon** *(build-time pattern; post-19.5
  cutover the working db is LIVE v3 — isolated DB is now only for a guaranteed-clean
  cold-start test, not a protection requirement)*: a throwaway
  launcher in the OS temp dir that monkeypatches `config.DATA_DIR`/`HISTORY_DB`/
  `FOOTPRINTS_FILE` to an empty temp path, so the daemon cold-starts and produces
  ONLY new-math buckets (no rehydrate of old-math, no legacy-json migration). Pattern:
  ```python
  from app import config as cfg
  cfg.DATA_DIR = TMP; cfg.HISTORY_DB = TMP+"/iso.db"; cfg.FOOTPRINTS_FILE = TMP+"/absent.json"
  from app import daemon; daemon.main()
  ```
  Then point `scripts/baseline_diag.py <tf>` at it (reads via the real CATCHUP path).
- Hold for the operator's confirmation before every commit. They trade live off this;
  correctness is paramount. They review diffs line-by-line.

## 5. Architecture / key facts a fresh session won't know

- **Two processes:** `python -m app.daemon` (headless asyncio core: feeds + 5 per-tf
  `QuantEngine`s + SQLite `HistoryStore`; TCP loopback server on `127.0.0.1:9999`)
  and `python -m app.terminal` (PySide6/PyQtGraph GUI, 20 Hz, thread-safe
  `PipeClientWorker` cache; multi-window). The terminal auto-reuses a live port (no
  SSH tunnel when a local daemon is up). Cloud/GCP deploy is deferred.
- **Data source = Binance kline (combined 5-tf websocket), ~1 message/sec.** A "tick"
  fed to the engine is the DELTA between two kline frames of the same candle; its
  whole volume is attributed to ONE price (the close at frame time). This 1s/
  single-price reconstruction is the fidelity ceiling Phase 5 (aggTrade) lifts.
- **`tick_time` is now EVENT time** (`payload["E"]/1000`, Step 1), not candle-open
  time. `uTime` (candle open) is STILL the footprint-DB key / `latest_utime` /
  `candle["time"]` — never repurpose it. Bucket `start_time` is event-time, lazily
  seeded on the first tick (may be `None` before then). `bucket.start_time` is NOT
  unique (many buckets share one busy minute) — never key on it alone.
- **Conservation law (Step 2 + 3): `opL + opS + clL + clS + churn == curr_vol`**
  exactly. The 4 position vectors carry only OI-confirmed flow (split by taker ratio);
  `churn` = OI-neutral/unattributed transfer volume (a real bucket field, in both
  serializers). `delta_oi` and `taker_buy` are clamped at the feeds boundary so the
  vectors can't exceed volume and ratios stay in [0,1].
- **`engine.vpin` is DEAD** — computed/persisted/shipped but never displayed. Every
  visible VPIN (Mode 6, hover, Mode 10 lower pane) recomputes as
  `Σ|buy−sell| / Σ curr_vol` over the trailing window. (Step 20 hygiene: delete it or
  recompute it the display way.)
- **E/R (Step 4):** denominator is now volume-weighted price dispersion from
  `b.levels` (`_effort_ticks`), floored at 1.0 tick — wick-robust; absorption reads
  `er = vol` (bounded). **Exhaustion (Step 5, Mode 3):** smooth z-score multipliers
  (`_exh_z_mult`/`_exhaustion_mults` in `terminal.py`), no rigid 150/300 tiers.
- **OPEN Phase-3/4 bug (diagnosed, NOT fixed):** in "Off" (time-chart) mode candles
  render extremely zoomed-out / invisible. Candle data and the Step-1 clock are
  CLEAN (verified: 100 contiguous bars, correct times). Cause: the empty, origin-
  anchored `scanner_bars` BarGraphItem is added **without** `ignoreBounds=True`
  (grep `self.plot.addItem(self.scanner_bars)` — its comment reads "must drive Y
  fit"), so it pollutes the one-shot
  `autoRange` X-extent from ≈0 to the candle epoch (~1.78e9). **Fix = `ignoreBounds=
  True` on `scanner_bars` in Off mode (or empty-safe bounds).** Slot into Phase 3/4.

## 6. Regression suite (`scripts/`) — how to run

All synthetic tests are history-independent and deterministic; **exit 0 = pass**.
Run the FULL suite at every commit (§4): a later step has TWICE broken an earlier
step's test — Step 3 silently changed test_step2's conservation law (caught + fixed
at `258ed1c`), and 19.3 deleted the kline tick-birth path test_step2 drove.
**test_step2 is RETIRED as of 19.3** — its feeds-boundary Step-2 clamp was relocated
into the per-trade path; the invariant is now covered by 19.1 (taker in {0,vol}) +
19.2 (|delta_oi| <= vol) + 19.3 (conservation / no overflow on real buckets).
Run from the project root:
```
python scripts/test_step1_velocity_clock.py   # event-time clock, degenerate-duration guards
python scripts/test_step3_churn_decomp.py      # opL+opS+clL+clS+churn==curr_vol; schema round-trip + guard
python scripts/test_step4_effort_result.py     # dispersion E/R: absorption vs run, wick-robust
python scripts/test_step5_exhaustion.py        # z-score exhaustion: scale-invariance, degenerate-safe
python scripts/test_step19_1_trade_mapper.py   # 19.1 aggTrade->args: exact m->side + T/1000 clock (tape replay)
python scripts/test_step19_2_oi_attributor.py  # 19.2 OI pending-balance: identity, K*Vw cap-and-hold, lag<=K, dead-vol floor
python scripts/test_step19_3_wiring.py         # 19.3 aggTrade wired into feeds: clock coherence, oi_open framing, steady-latency flat, invariants
python scripts/test_step19_4_recompute.py      # 19.4 recalibrate/OB off the close hot path: per-engine close flat, target_vol still adapts
python scripts/test_step19_3b_live_edge.py     # 19.3b 150ms live-edge: timer-driven (not per-trade), carries the forming bucket
python scripts/test_a3b_state_engine.py        # A3b Mode 10 state engine: 13 synthetic verdicts + gradient-preserving squeeze floor
```
- `test_step5` imports `app.terminal` (pulls in Qt); it sets `QT_QPA_PLATFORM=offscreen`
  itself, so it runs standalone.
- **Live tools (need a running daemon):** `scripts/baseline_diag.py <tf>` prints the
  5 invariant distributions (duration, vol_mult, conservation residual, vector
  overflow, engine-vs-recomputed VPIN) — this IS the Phase-0 "before" baseline; the
  committed `scripts/baseline_<tf>_20260615.txt` are those before-fix snapshots.
- **Step-3 comparison tools:** `scripts/compare_step3_decomp.py <tf>` (live daemon)
  and `scripts/compare_step3_from_history.py {scan|<tf> [s] [e]}` (reads a COPY of
  `history.db.before-fixes`) reconstruct OLD-50/50 vs NEW-confirmed for visual
  before/after. Re-prove Step 3 with these if needed.
- **19.6 fidelity tool:** `scripts/compare_19_6_footprint.py` (read-only) renders the
  kline-vs-aggTrade footprint side-by-side (POC-displacement headline) from the frozen
  tapes `scripts/fixtures/aggtrade_tape.jsonl` (quiet) + `aggtrade_tape_active.jsonl`
  (moving). Output PNGs are gitignored (regenerable).

## 7. Phase 5 (aggTrade) discipline + churn-as-signal (operator-established)

1. **aggTrade MUST be proposed-then-approved BEFORE any code.** Do NOT start the
   source-swap cold. First DESIGN and PRESENT the approach, then wait for the
   operator's explicit approval — same discipline as the Step-3 comparison
   (propose → operator approves → build). The proposal must cover:
   - how each aggTrade (true price + qty + buyer-maker flag) routes into the
     footprint **levels** and into `process_tick`;
   - what the **klines are retained for** (OHLC/candle framing + OI alignment only);
   - the **message-volume / hot-path concern** — aggTrade is orders of magnitude
     more messages than 1s kline, so the per-tick path must stay allocation-light.

2. **aggTrade validation needs a side-by-side before/after (like Step 3 had):** an
   aggTrade-built footprint vs the kline-built one over the SAME window, so the
   operator can judge the fidelity gain by eye. Build that comparison artifact —
   don't just assert the improvement.

3. **CHURN IS A SIGNAL, not just removed noise (operator's trading insight —
   record and honor it).** Churn collapsing toward the baseline while the
   opens/closes pulse bars spike = rotation converting into real positioning, the
   market "starting to breathe." That regime-change moment is **central to how the
   operator reads the pulse view** (Modes 7/8 with the secondary-axis churn line)
   and must inform the future **Mode-10 selection-tool** design. Treat churn's
   *dynamics* (its drop) as information, not merely as the leftover after the
   OI-confirmed split.

## 8. Phase 5 (aggTrade) — APPROVED DESIGN + STAGING (operator-signed 2026-06-15)

**STATUS: PHASE 5 DONE (2026-06-16) — aggTrade-native + live, suite 9/9, working db
cut over to v3.** Commit list:
`d89253e` 19.0 capture harness + frozen tape · `6c52ba7` 19.1 trade→args mapper ·
`eefdd11` 19.2 OI pending-balance attributor · `918199c` 19.3 wire aggTrade (retire
the kline tick-source + the subsumed step-2 test) · `40dd6e3` 19.4 recalibrate/OB
off the close hot path (= old Step 15) · `1c39fab` 19.3b 150 ms live edge · `a00cf7e`
19.5 schema v3 cutover · `1a610f9` 19.6 footprint fidelity sign-off. (Plus `e95f4c1`
test-harden, `94d2704` active companion tape, `4b0585a`/`6fe3e3f`/this docs.) The v3
cutover was EXECUTED on the real working db (guard wiped 2954 kline buckets,
footprints survived, aggTrade buckets persisted at v3; `history.db.before-fixes`
byte-identical throughout). 19.6 signed off by eye: aggTrade relocates the POC to
the true volume peak on travel (4-tick correction), converges with kline when quiet
(1 tick) — it corrects exactly where price travels.

**OPEN downstream-tuning item (from the 19.5 dry-run; NOT a cutover blocker):** with
aggTrade, sub-second BURST buckets (~5000 vol in ~6 ms) give a `vol_mult` (velocity
ratio) tail into the thousands (observed max ~3414) — aggTrade revealing real bursts
that kline's 1 s floor hid. The Step-1 MEDIAN `avg_velocity` correctly resists
baseline warp (normal buckets stay ~1.0, so the velocity gate still discriminates) —
nothing is corrupted. BUT downstream consumers see extreme values on bursts: OB
`power_score` (multiplies by `vel_ratio`) and the neon-velocity intensity. Operator
wants to SEE it live before deciding signal vs noise; candidate fix = a `vol_mult`
cap for `power_score`/neon. Do NOT fix pre-emptively — watch live first.
**Visual tell (Mode-10 trace, 2026-06-16):** the burst tail manifests on the Mode 10
neon brush as **cyan-saturation** — `clS`-dominant buckets at the forming edge trip the
`vel_ratio >= 2.5` neon override (`_neon_v2_brush`); the day EVERY fast bucket goes cyan
and neon stops discriminating is the live signal the `vol_mult` cap for neon intensity
has earned its place.

Architecture approved. Core insight: **aggTrade is a better source for the same
five `process_tick` args** (price, vol, taker_buy, delta_oi, tick_time) — Steps 1–4
already made all five correct, so this is a source-swap into trusted math, not new
math. The taker split becomes EXACT per-trade (the `m` buyer-maker flag), so the
Step-2 taker clamp can no longer fire (kept as a zero-cost guard). A "tick" becomes
ONE real aggTrade (true price/qty/side), not a 1s batched delta.

**The 5 settled decisions:**
1. **OI = pending-balance bleed** (OI stays a 5s REST poll; NOT per-trade-exact).
   ONE global signed `pending_oi`; on each poll `pending_oi += (oi - last_oi)`; on
   each trade of size q, `share = clamp(pending_oi, -q, +q)`, feed `delta_oi=share`
   to ALL 5 engines, then `pending_oi -= share`. The Step-2 clamp is now literally
   per-trade — its most important home. Strictly MORE honest than today's kline path
   (which clamps the OI overflow AWAY and loses it; the balance carries the residual
   forward until volume absorbs it).
2. **OI cap/decay is MANDATORY, scale-free (§0.6).** Bound `|pending_oi| <= C = K·Vw`
   where `Vw` = EWMA of trade volume per OI-poll interval (recent volume — adaptive,
   market-set; NOT a fixed constant), K≈3. **Cap-and-hold** (clamp to ±C, DROP the
   excess + count it in a diagnostic) — NOT decay: the excess is OI that no volume
   could carry, so dropping it is honest; decay would bleed stale OI onto unrelated
   later trades. This provably **bounds the attribution lag to ≤ K poll-intervals**
   (pending ≤ K·Vw, drained at the volume rate ⇒ ≤ K windows). **On `@aggTrade`
   (re)connect: resync `last_oi = current_oi`, `pending_oi = 0`** — the missed-gap
   trades can't be reconstructed, so don't dump the gap's OI onto resumed trades.
   This is the trend/desync robustness the operator demanded as non-optional.
3. **Live-edge refresh = dedicated ~150 ms throttle**, decoupled from trade rate. Do
   NOT overload the 0.4s pulse loop — keep concerns separate. NEVER broadcast per
   trade. Bucket-close `ObPacket`s stay event-driven (volume-paced = the true pulse);
   only the forming-bucket refresh is throttled.
4. **Dedicated `aggtrade_stream` asyncio task** (isolates backpressure), parallel to
   `dynamic_stream`/`liquidations_stream` in `feeds.start_tasks`.
5. **Schema bump `BUCKET_SCHEMA_VERSION` 2→3 + `history.db` wipe at cutover.** NO
   bucket FIELD changes (both serializers untouched); the bump is a DELIBERATE
   fidelity reset so kline-smeared and aggTrade-true `levels` never share a rolling
   window. Footprint node shape unchanged (survives the wipe by design). Note it in
   the README like the Phase-1 wipe.

**Hot-path fact (reassuring):** bucket closes are VOLUME-paced, not message-paced —
aggTrade delivers the same volume in more messages, so closes/sec and the expensive
per-close work (POC / E-R / VPIN / recalibrate) do NOT scale up. Only the per-message
path scales (5 `process_tick` calls/trade). 19.3's gate MEASURES per-message latency
on a real-tape burst replay — prove no stall, don't assume it.

**Staging (each = one branch-commit, minimal diff, FULL suite green at every commit):**
| Sub-step | Scope | Gate |
|---|---|---|
| 19.0 | throwaway `@aggTrade`+5 klines+OI capture recorder → real raw-tick JSONL tape. NO app code. | tape parses; trade/kline/OI counts; peak trades/s; buy/sell + price-level variety. |
| 19.1 | pure trade→`(price,vol,taker_buy,tick_time)` mapper, exact `m`→side. No wiring. | DETERMINISTIC REPLAY of the 19.0 tape: known trades → exact args; side correct; `tick_time=T/1000`. |
| 19.2 | pure OI pending-balance attributor (decisions 1+2). No wiring. | tape replay (normal): Σshares=ΔOI, nothing lost, neutral when flat. + SYNTHETIC OI-expansion burst: `pending_oi` stays ≤ C AND lag ≤ K windows. |
| 19.3 | wire `aggtrade_stream`; route trade→5 engines+levels; DELETE kline tick-birth; klines=framing; 150 ms throttle. | isolated empty-DB daemon: invariants green (`baseline_diag`) AND MEASURED per-message latency flat under a dense tape-replay burst (p50/p99/max, no backlog growth). |
| 19.4 | **= Step 15**: recalibrate/OB off `_close_active_bucket` → periodic/executor. | synthetic burst of rapid closes → per-tick latency flat; `target_vol` still adapts. |
| 19.5 | bump schema 2→3; guard wipes `history.db`; re-accumulate; README note. | version bump triggers the clear path (extend `test_step3`'s guard test). |
| 19.6 | kline-built vs aggTrade-built footprint over the SAME tape window, side by side. | NOT pass/fail — operator VISUAL sign-off (like Step 3): aggTrade POC at true price, sharper dispersion-E/R. |

**Tape-as-fixture:** the 19.0 tape is the project's FIRST real raw-tick fixture, so
19.1 / 19.2-normal / 19.3 / 19.6 are deterministic replays of it (not hand-built
sequences). 19.2's runaway/cap assertions still need a SYNTHETIC burst (real tape
won't reliably contain a desync). So capture LONG/VARIED enough: target several
minutes, a few k trades, OI both rising+falling, a high-trades/sec burst for 19.3.

**Discipline reminders (still in force, §7):** propose-then-approve before code in any
sub-step that warrants it (§7.1); side-by-side before/after for the fidelity claim
(§7.2 = 19.6); churn's DROP is a signal, not just removed noise (§7.3).

**Tools:** `scripts/capture_aggtrade.py` is the 19.0 recorder (standalone; reads only
public market data, writes one JSONL tape under `data/`; imports `app.config` for
URLs/constants only — touches no app state).

---
**Start here:** Phases 1 + 5 are DONE — the terminal is aggTrade-native and live on
the real v3 `history.db`. Read **§0** (the Mode-10 capstone reframing) + **§2** (current state · what's next · trustworthiness
map), confirm the suite (§6) is green (10 tests, all `exit 0`), then pick the next
phase WITH the operator (Phase 2 → 3 → 4 → Mode-10 tool). §7/§8 are the Phase-5
record; the standing rules (§3) + the verification pattern (§4 — esp. FULL-suite-at-
every-commit, propose-then-approve, one-step-one-commit, hold-before-commit) govern
every future step.
