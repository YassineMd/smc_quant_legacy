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
  **BUILT** (`6c03ce8` + `427f1ab`) — gathers instances 24/7; revisit at ~20–50 distinct events/pattern to ground +
  prove discrimination, then build only the winners.

**CHOP/ROTATION/NEUTRAL audit — catch-alls are CORRECT; states are DESCRIPTIVE, not PREDICTIVE (no code change).**
Read-only instrumentation on 3600 real VM 1m buckets, BOTH per-bucket and selection. Verdict: the classifier is
sound as a flow descriptor — **do NOT loosen the catch-alls** (don't re-litigate without new evidence).
- *Catch-all rate*: 72% per-bucket (NEUTRAL 33.7% / CHOP 20.7% / ROTATION 17.6%), STRONG 16%. High but correct —
  OI-confirmed conviction is genuinely rare.
- *The "misses"*: 31% of catch-all buckets (799) had a decisive directional bar (body≥.5 & |delta|≥.10 &
  range≥.8 ATR) called no-edge. Gated **83.7% by `freshOI`** (no OI confirmation): per-bucket OI is structurally
  sparse — 32% of buckets have ~zero 4-vector, 56% below the `opL/vol ≥ 0.10` floor (median oi_build STRONG 0.288
  vs catch-all 0.047). **NOT a bug**: the selection E/R-saturation fix's `translate` factor gates only **0.1%**
  per-bucket; `freshOI` is correctly scaled.
- *DECISIVE test — forward returns*: the 799 "missed" moves continue **~50%** (K=1/3/5 = 47/50/49%), IDENTICAL to
  STRONG (49/52/49%); selection L=12 agrees (STRONG 51% / catch-all+move 52%). So the misses are **not tradeable**
  — loosening `freshOI` would add 799 coin-flip signals (the worse "cry-trade-on-noise" failure).
- *KEY FINDING*: the states are **descriptive of current flow, not predictive of the next move** on 1m SOL — even
  STRONG forward-wins only 49–54% (coin flip). Read labels as "what's happening now," not "what happens next."
- *Two parked caveats*: (1) this is ONE ~few-day regime — re-run on a trending stretch could differ; (2)
  **BEAR TRAP** showed 70% forward win at K=5 (n=60) — the one possibly-predictive thread, parked for later.
- **Classifier UNCHANGED** — confirmed working as a flow descriptor.

**Stats boxes — `y` toggles STATE/debug (hidden default) + dominant Sell|Buy colour (`cda7af6`).** Both the
per-bucket stats box (hover + live forming candle, via `_hover_context`) and the Mode-10 selection box:
- STATE verdict + its `DBG`/`why` calibration lines are **HIDDEN by default**; **`y`** toggles them, re-rendering
  both boxes immediately (`_toggle_states` → `_refresh_parked_hover` + `_refresh_selection_stats`); the classifier
  is skipped entirely while hidden.
- The FLOW `Sell | Buy` line colours **only the dominant side** (sell>buy → Sell red, buy>sell → Buy green;
  lesser side gray), matching the 4-vector top-2 dominance style.
- *Exe rebuilt at `cda7af6`* (supersedes the `e272b71` build) — `dist/OrderFlowTerminal.exe`, smoke-tested.

**OB-break study + candidate accumulator BUILT (`6c03ce8` refactor + `427f1ab`).** Studied which bucket-candle
characteristics predict follow-through when price breaks an order block (break = the OB engine's own death: a
bucket whose CLOSE clears the far edge — bullish/demand `close≤bottom`=down, bearish/supply `close≥top`=up).
- *Feasibility*: 1x = **49 breaks**, 5x = **15** (too few to split). *Finding — NO edge on current data*: 1m
  breaks continue **~45–49%** (coin flip, slight **reversal** lean by K=5, med −0.28 ATR); NO characteristic
  (OI-in-dir / body / aggression / velocity / penetration / VPIN / 12-state) discriminates continuation from
  fakeout at n≈24 (splits within noise, inconsistent across K; the OI split runs *backwards* — hinting breaks
  are **exhaustion, not ignition**). Did NOT fish 49 events for a setup (multiple-comparisons trap).
- *Refactor* (`6c03ce8`): extracted pure `app/region_state.py` (exhaustion mults + synth-bucket + selection-
  state) from the Qt-coupled terminal so the headless job shares the EXACT classifier math; n=1=hover 270/270.
- *Accumulator* (`427f1ab`, `scripts/pattern_accumulator.py`): read-only periodic scan of `history.db` that
  banks every OB break (candle characteristics + forward K=1/3/5 in ATRs) + loose rare-pattern windows (factors
  + `state_12`) to `data/*.jsonl`. Passive, idempotent (dedup by `ob_id` / `tf+L+epoch`), logs only breaks with
  a COMPLETE forward window. Verified on real data: 49(1m)+13(5m) breaks banked; re-run adds 0.
- *Deploy — PENDING (operator runs it; NOT auto-deployed)*: push `app/region_state.py`, `app/config.py`,
  `scripts/pattern_accumulator.py` to the VM + a ~hourly cron running the script from the project root; then
  `scp data/ob_breaks.jsonl` down periodically. **Revisit at n≥200** to test the OB-break **fade** edge
  rigorously (where a real edge separates from coin-flip and a chance pattern doesn't survive).

**Per-price 4-vector / positioning — DEFINITIVELY CLOSED, NO WIPE EVER (investigation, no change).** Checked
whether wiping the DB to capture per-price-resolved positioning (4-vector/OI/E-R by price level) is feasible
+ worth it. **Feasibility:** OI is ONE market-wide number, ~5s REST poll (`feeds.py:149` `openInterest`); the
4-vector is an ATTRIBUTION — the `OiAttributor` bleeds each global OI delta across trades by arrival timing
(`aggtrade.py`: "OI cannot be made per-trade exact"), and `process_tick` builds opL/opS/clL/clS from (trade
side, bled share), NOT price. So a captured per-price 4-vector is a TIMING ARTIFACT, not a measurement — the
source can't answer "where did OI open." **Free check (read-only) instead:** the approximated per-price split
is volume-proportional by construction (`opL_P = opL·buy_P/Σbuy`), so per-price positioning *concentration ≡
the volume ladder's shape*. Tested whether level concentration discriminates forward returns:
- *Test A* (n≈1800/side): CONCENTRATED vs SPREAD forward continuation = 44/47/48% vs 48/50/50% at K=1/3/5 —
  both **coin-flip**, and concentration leans toward **REVERSAL**, not continuation (opposite of the
  "strong level → follow-through" hypothesis).
- *Test B* (the OB-break-at-band case, n=49): break-dir volume concentrated AT the OB band continues **less**
  (44% vs 54% K3) — same reverse lean, noise at this n but directionally consistent with A.
- **Closure:** per-price positioning discriminates NOTHING, and the captured version (global-OI-sprayed-by-
  timing) could only add noise on top — **it cannot beat this clean approximation**, so no wipe is ever
  warranted. Per-price granularity is NOT what's missing; the forward-return ceiling is a market-efficiency
  property of 1m SOL. The one faint signal that persists across ALL probes (states, OB breaks, concentration)
  is the same **mean-reversion / OB-break fade** lean the accumulator is already banking to test at honest n.

**Selection-box flow readouts — E/R colouring + trajectory sparklines + balance-flip detector (`ab2da2b`).**
One concern on the Mode-10 selection box (+ hover box where noted). All DESCRIPTIVE (no predictive dressing).
- **Buyer/Seller E/R dominant-colouring** (hover + selection): only the stronger side coloured (green buyer /
  red seller), weaker greys out — same rule as `Sell | Buy`.
- **Three TRAJECTORY sparklines** (unicode block chars in the QLabel) in a bottom `FLOW TRAJECTORY →` section:
  **E/R** (`buyer_er−seller_er`, green/red), **Op L/S** (`opL−opS`, green/red), **Cl L/S** (`clL−clS`,
  purple/blue). RAW signed diff — NOT normalised (normalising E/R collapses to the delta fraction already shown;
  operator caught it). Per-side auto-scale, zero pinned to a gray baseline block (colour-flip + baseline ARE the
  crossover); `n≥SPARK_MIN`, vol-weighted downsample to `SPARK_WIDTH`. Op/Cl unsmoothed (5s-OI noise, shown honestly).
- **Direction-aware TWO-SIDED sustained BALANCE-FLIP** (`region_state.balance_flip`): on the E/R series, marks
  where the move's dominant side LOST CONTROL and STAYED lost. Net (`last_close−first_open`/range) sets direction
  (down→S→B, up→B→S, flat→any +`·AMBIG`). A real switch needs the OLD side to have HELD `≥FLIP_SUSTAIN_MIN` over
  `≥FLIP_MIN_REMAINDER` buckets BEFORE the cross AND the NEW side to HOLD after — the pre-run kills the `@+1`
  edge-graze a post-only gate let through. Headline = **SUSTAIN** (`held X%`); `·messy` = choppy settle
  (absorption). Dashed yellow vline + label, suppressed on `no_flip`. **Confluence REJECTED** by the discrimination
  test (OpL/OpS agrees with noise 64% vs real 66%; ClL/ClS = chance) = fake confidence.
- *Validated on real VM 1m*: `@+1`→no flip; A-noise→no flip; absorption→kept (`S→B 73% ·messy`); clean→100% held;
  one-way/end-edge→no flip; net-up→lands on the peak.
- **HONEST WALL** (operator-confirmed): the flip is a **HINDSIGHT locator** — confirms only after the new side
  sustains (lags the turn) and does NOT predict price. Where the balance switched + held, not a real-time top.
  The **FORMING marker** below is the honest response to "can it fire earlier?".

**FORMING balance-flip marker — tentative WATCH heads-up, one event / two maturities (`e9e0ada`).** Shows the SAME
crossing EARLIER, before it confirms, flagged `unconfirmed` — never a signal/forecast (walls hold). `balance_flip`
returns ONE of three off the SAME pre-gate (`pre_ok` = relevant-dir crossing where the OLD side held: `k≥REM`,
pre-run `≥SUSTAIN_MIN`):
- **CONFIRMED** = `pre_ok` + post matured (`n−k≥REM`) + new side held; `min()`. Byte-identical to the old gate (582/582, 0 mismatches).
- **FORMING** = `pre_ok` but post too short to judge (`n−k<REM`); `max()` = most recent. `sustain`=held-SO-FAR,
  `post_n`/`need`=maturity (`2/4`). The `p/N` + `unconfirmed` are the honesty guard (`100% · 1/4` = preliminary).
- **VANISH** automatic: `pre_ok` with `n−k≥REM` but `post<SUSTAIN_MIN` → NEITHER list → no marker (reverted). Most
  forming markers vanish — most early crossings ARE the noise the confirmed gate filters; shown, flagged, not hidden.
- *Render* (`terminal.py`): one mutually-exclusive treatment at the same `x` — CONFIRMED solid dashed bright yellow
  (z86/87) / FORMING dim dotted amber `(241,196,15,110)` thin (z84/85) + italic `#b8932f` `⋯ FORMING dir nn% · p/N
  · unconfirmed`. Separate `_forming_line/_label`; `_hide_flip` clears both; box mirrors (`Forming → …`). Solidifies
  into the confirmed line if it holds once REM accrue, or vanishes. **ZERO new thresholds** — reuses
  `FLIP_MIN_REMAINDER`/`FLIP_SUSTAIN_MIN`; the forming↔confirmed boundary IS `FLIP_MIN_REMAINDER`.
- *Validated on REAL 1m VM data* (5000 buckets, truncated to simulate the selection growing to the live edge):
  forms→SOLIDIFIES (`1/4→2/4→3/4`→CONFIRMED `4/4`); forms→VANISHES (post reverts → gone); never-forms `@+1` BY
  CONSTRUCTION (5288 forming + 4020 confirmed, 0 pre-gate violations); confirmed UNCHANGED (582/582). Eyeball-confirmed.
- **v1 LIMITATION** (accepted): keyed to the selection's `net_dir` → catches the MAIN turn earlier; will NOT catch a
  SECOND opposite turn near the edge after an already-confirmed flip — **v2**.
- **EXE NOW STALE**: forming is NOT in `dist/OrderFlowTerminal.exe` (last built `ab2da2b`); operator rebuilds next batch.

**"Explain the flip" synthesis — INVESTIGATED + DROPPED (read-only, no code).** Idea: at a flip, draw which
existing mechanisms were PRESENT (absorption / liquidation / exhaustion / vol-velocity) as the "why". Tested
base-rate guarded on real 1m data (140 flips vs 500 random buckets, `[flip−3,flip+1]` window). **DROPPED —
flips on 1m SOL mostly have NO clean detectable cause:**
- **3 of 4 mechanisms AMBIENT** (present at random buckets as much as flips): exhaustion 86% vs **96%** base
  (**1.02×**), vol/vel 67% vs 64% (**1.05×**), absorption 34% vs 32% (**1.07×**). Showing them = fabricated causes.
- **Only LIQUIDATION elevated** — 52% vs 36% = **1.46×** — but WEAK (a third of random buckets too); calling it
  a "cause" makes a weak correlation look causal.
- **No COMBINATION rescues it** (untuned, pre-specified, no fishing): ≥2 mech 1.08×, ≥3 1.30×; best pair
  LIQ&EXHAUST **1.46×** = liq alone; ABSORB&EXHAUST **1.01×** ambient. **No synergy.** The strict-def 2–3.5×
  lift was the **curve-fit trap** — appeared only after tuning, on 2–9% of flips, tiny-N (~5–10 buckets). Failed
  the no-tuning guard.
- **TIGHT-FOOTPRINT test — "maybe the loose window drowned a localized force": TESTED + FALSE.** Base-rate
  guarded on the flip's OWN footprint (`[f,f+REM]` maturation buckets + their high-low band; span median 0.150
  = random 0.150, no confound) vs 500 random same-size boxes, untuned, incl. the never-tested POSITIONING
  vectors. Ambient up close: pos-init **0.60×** (less than random), pos-close flat (0.305 vs 0.314), absorb
  0.95×, exhaust 1.02×, churn 0.89×. Sharpest: **liquidation's 1.46× loose lift COLLAPSES to 1.02×** at the
  tight footprint — the weak signal was the broad turny vicinity, not the flip's own ticks. No localized force
  to drown; the footprint looks like a random box.
- **Verdict:** dead at EVERY scale — loose window (only liq 1.46×), combinations (no synergy; 2–3.5× only
  under tuning = curve-fit), tight footprint + positioning (ambient, liq→1.02×, positioning ≤ random). The
  trigger isn't in anything we instrument. Consistent with **descriptive-not-predictive**. Closed at every
  scale. Not re-litigated.

**Abnormal-velocity bucket flag — BUILT (`59e01af`).** Descriptive VISUAL STUDY marker (watch where fast
buckets cluster; NOT a signal). Velocity = `curr_vol/duration`; flag when ≥ `VEL_ABN_RATIO` × the
**trailing-30-MEAN** velocity — SAME `buckets[idx-30:idx]` basis as the stats box's `30b BER/SER` (operator
wanted consistency, not a parallel z-score). Calibrated on 5610 real buckets (fat tail: median 363, max
25871); operator picked **`5.0×`** (~6% fire). Cues on a flagged Mode-10 candle: **2px border** (flow
colour kept) **always on** (per-frame pen list copied — #3 cache never mutated); **diamond** above (`v`
toggles): neon green=buyer / red=seller / **gold on divergence** (buy-led closed down / sell-led closed up),
alpha+size by ratio capped at `VEL_ABN_CAP=10×`. Hover: `30b VEL n.n×` line under VEL (gold ≥ cutoff). Keys:
`v` toggles diamonds (border always on); drawing-cancel moved `V`→`Escape`. Knobs `VEL_ABN_WINDOW=30` /
`VEL_ABN_RATIO=5.0` / `VEL_ABN_CAP=10.0`. **EXE REBUILT** 2026-06-23 00:36 through `9a1fa6a` (forming +
velocity flag, smoke-tested, copied to the operator's other machine — needs gcloud authed to `yass-chart`).

**UI: `h` toggles the Mode-10 selection stats box** (`show_sel_stats`; box only — flip line / absorption /
velocity overlays keep rendering). Built after the 00:36 exe rebuild (not yet in the exe).

**Drawing persistence on Mode 10 — CONSIDERED + DROPPED.** Time-space drawings already persist
(`drawings.json`); Mode-10 drawings are INDEX-space and the Zero Point resets to `now−24h` every launch
(`hamburger.py:271`) so ordinals shift — saving as-is restores them wrong. Honest fix = epoch-anchoring
(store `start_time`+price, re-map `ts→ordinal` each render). Operator decided not worth the complexity —
dropped, not built.

**E/R-exhaustion candle border + footprint imbalance vs 30b E/R + Mode-10 UI defaults (`79c1ace`).**
DESCRIPTIVE Mode-10 visual+controls batch; keyed off the trailing-30 E/R baseline (`EXH_WINDOW=30` = the
stats box's `30b BER/SER`).
- **E/R border** (`_bucket_row`, `ER_BORDER_EXH_PCT=50`): a side's E/R exhaustion-% (`(mult−1)×100`, the
  `[+N%]` bracket) ≥ cutoff overrides the wick/border — **3px** if both sides elevated else **2px**; colour
  = neon ORANGE (buy-led closed down) / BLUE (sell-led closed up) on a divergent close, else neon GREEN
  (buyer) / RED (seller) by dominant E/R side. Velocity flag now at-least-2px (won't shrink a 3px border).
- **Footprint imbalance** — replaced same-level ratio (`FOOTPRINT_IMBALANCE_RATIO` gone) with: level buy/
  sell ≥ `FOOTPRINT_IMB_ER_MULT` (=1.0) × the bucket's 30b BER/SER. Cues: NUMBER black-on-neon (gated by
  footprint toggle) + a candle-width horizontal neon line AT the level's exact price (price-anchored, no
  zoom drift; blue=buyer/orange=seller; both→split) — the LINE is ALWAYS on (two `PlotCurveItem`s,
  independent of the footprint toggle).
- **UI**: POC Dot OFF by default; Vector Drawing toolbar ON by default. **Alt+wheel = Y-zoom**
  (Shift+wheel = X-zoom already).
- **EXE NOT rebuilt** (operator's call) — stale by h-toggle + this batch since the 00:36/`9a1fa6a` build.

**BULL/BEAR absorption stack — per-bucket measure + selection aggregation + adaptive zones (`c5c219d`).**
One concern, bottom-up: per-bucket primitive → selection aggregation → drawn zones. All DESCRIPTIVE.
Eyeballed on a defended selection + a trend.
- **Per-bucket** (`region_state.absorption_vol`, `ABSORP_VOL_WINDOW=50`): BULL/BEAR absorption in VOLUME =
  aggressive volume on the side that FAILED to move price, scaled by suppression `s = clamp(1 −
  (|disp|/curr_vol)/k, 0, 1)` vs the trailing-50 norm `k = Σ|close−open|/Σcurr_vol`. **GROSS + directional**
  — only the heavier failed aggressor gets credit (`bull = sell_vol·s` if sellers dominated, else 0; mirror
  for bear). Shown in the hover box (`Bull/Bear Absorp`) + summed over the Mode-10 box with the bull:bear
  lean. Validated: held-despite-selling → high bull/0 bear; selling-worked → 0; clean move → 0 both;
  discriminates at equal sell volume.
- **Adaptive zones** (`zones_from_series`, `ABSORP_ZONE_MIN_RUN=3`, `ABSORP_ZONE_FLOOR_S=0.60`): a zone = a
  run of **≥3 consecutive same-side** buckets that are directional AND `s ≥ threshold` → green/red price×time
  band, labelled with absorbed volume. **Floorless slider** on `s`; **auto-default = the selection's MEDIAN
  nonzero-s** (defended→high, quiet→low; re-seeds each fresh selection, a drag pins it until the selection
  id changes — `id(drawer._selection)`). **Yellow dot at s=0.60 = the validated-strength boundary, a
  GRADIENT not a real/fake cliff** (suppression rises gradually). **Rightward projection**: real run solid
  (15%), projection lighter (6%) + dashed mid-line to the selection's right edge. Shared one-pass helpers
  `absorption_series`/`absorption_default_s`/`zones_from_series` (over the validated `absorption_vol`);
  `AbsorptionZoneSlider`+`_FloorSlider` (stats_overlay), `AbsorptionZoneLayer.update_zones` (chart_widgets);
  wired in `_refresh_selection_stats` (slider under the box, hidden on every early-return / `h`-hide).
- *Validated on live 1m VM data*: auto-default (s=0.56) drew a genuine 71.00-floor zone (88.8k vol); the
  slider loosened below the dot (`0.90/0.60`→1 strong, `0.40`→+1 weaker, `0.00`→7 fragments); a CLEAN TREND
  (+1.39/60 buckets) drew **nothing even at s=0.00** (no sustained same-side run — floorless can't
  manufacture on a trend). **Fixed top-quartile N=3 version was built then REJECTED for this adaptive one.**

**E/R border colour → by CLOSE direction (`dbfaaf1`, SEPARATE commit).** Non-divergent border now neon GREEN
if `cl ≥ op` else RED (was `_bm ≥ _sm`, dominant E/R side); divergence ORANGE/BLUE unchanged + still take
precedence. Split out per one-concern-per-commit (unrelated tweak in the same file as the absorption stack).

**EXE still stale** — neither `c5c219d` nor `dbfaaf1` is in the 00:36/`9a1fa6a` build; rebuild next batch.

**Direction-matched absorption at flips — INVESTIGATED + AMBIENT (read-only). 5th cause-test to die.** Does
the SIDE absorbing match the flip — BULL absorption before an S→B up-flip, BEAR before a B→S down-flip (the
directional angle the presence-test never checked)? Real VM 1m data, 453 sustained flips (218 up / 235
down), run-up = W buckets before the cross, absorbing = shipped `s ≥ 0.60` floor, flip gate from config,
untuned, stable W=6/8/10.
- **Unconditional looked like a find:** bull-before-up 0.858 (**1.39×** vs all-random), bear-before-down
  0.830 (**1.49×**), opposite side suppressed ~0.5× → clean ~2.5× matched/opposite split (sharper than the
  presence-test's 1.07×).
- **Conditioned on the SAME dominant side → collapses to ~1.0×** (up-bull **1.05×**, down-bear **1.02×**).
  TAUTOLOGY: an up-flip run-up is seller-dominant BY DEFINITION and `bull = sell_vol·s` fires whenever
  sellers dominate volume, so bull-before-up is mechanically built in. `P(bull | random seller-dom window)
  = 0.818` ≈ `P(bull | up-flip run-up) = 0.858`. Opposite-side "suppression" = the same artifact inverted.
- **METHOD LESSON (reusable):** a clean matched-vs-opposite directional split must be tested against a
  **SAME-DOMINANT-SIDE base rate, not all-random** — else a mechanical coupling (signal shares a DEFINITIONAL
  ingredient with the event's precondition) reads as a discovery. Unconditional 1.4× = false find; conditioned
  ~1.0× = truth. All-random is the wrong null when the event's precondition correlates with the signal's
  ingredient.
- **CLOSES** "does the bull/bear absorption measure give a flip edge": descriptively AMBIENT, directionality
  a tautology. Joins iceberg-presence 1.07× + the 4 explain-the-flip mechanisms. Measure still describes a
  single bucket's defense (why zones ship); just no flip relationship. No build.

**EFFECTIVE-AGGRESSION zones — the validated MIRROR of absorption, BUILT (`51bd635`).** Absorption = heavy
volume that FAILED to move price (`V*s`); eff-agg = the dominant aggressor's volume that MOVED price ITS way
(`V*(1-s)`), gated on direction; reuses the EXACT same s. Eyeballed. DESCRIPTIVE.
- **Measure** (`region_state.effective_aggression`): `eff_agg_bull = buy_vol*(1-s)` if buy-dom & close>open;
  `eff_agg_bear = sell_vol*(1-s)` if sell-dom & close<open. Proven: in-dir bucket `absorption+eff_agg == V`
  (split the dominant volume by s; corr −0.45, top-decile overlap 0.5% — near-disjoint mirror). Distinct from
  light drift (volume weight = force gate) and absorption (the s split).
- **Zones** (`eff_agg_series`/`eff_agg_default_f`/`eff_zones_from_series`): ≥3 same-side forceful runs →
  **NEON green (0,255,128) / red (255,0,96)** bands (distinct from absorption's muted green/red), projection +
  vol label. OWN slider (`EffAggZoneSlider`, neon accent, stacked under the absorption slider) riding force
  `f = eff_agg/vol_norm` (vol_norm = trailing-50 mean curr_vol; self-calibrated relative force ~[0,1]).
  Auto-default = selection's median nonzero-f, re-seeded per selection.
- **The dot (the one honest difference):** eff-agg has NO validated-strength cliff (it's common, ~55%; ~75% of
  directional buckets are ordinary directional volume). Dot GROUNDED at `EFF_AGG_ZONE_DOT_F=0.75` = ~p75 of
  directional force on real 1m: above = top-quartile distinctively forceful, below = ordinary directional
  volume. (f=1.0 rejected — 98th pct, ~0 runs.) A force gradient, not real/fake.
- *Validated*: forceful selection auto-default (f=0.88) drew a genuine forceful zone; the dot kept 2 forceful
  zones while below-dot surfaced ordinary runs; an ordinary light-directional region (51 dir buckets, median-f
  0.52) drew NOTHING at the dot (force gate works). Parity: hover `Bull/Bear Eff` + box `EFF-AGG · VOL`.
  Slider refactored to a shared `_ZoneThresholdSlider` base (absorption behaviour unchanged); `AbsorptionZone
  Layer` parametrised with colours. **EXE still stale.**

**DAEMON FIX — closes weren't broadcasting at the cap (`3384a6b`, DEPLOYED).** Bug: `feeds.py` fired the
per-close `ObPacket` only `if len(engine.closed_buckets) > last_count`, but the engine caps that list at
`CLOSED_BUCKETS_CAP`(10k) (append+pop(0)) → at the cap the length never grows → closes stopped broadcasting →
client scanner history froze (restart = fresh catch-up, "fixed" then re-froze). Symptom: "live candles leave
a gap, only restart fixes it." FIX: monotonic `engine.total_closed` (at the append); `feeds.py` ships
`closed_buckets[-delta:]` when the counter advances. Byte-identical below the cap. PROVEN live: 8 closes in
160s, tail advanced. SOLE instance of the length-growth pattern (grep-checked). Found by revert-and-check +
a live-edge monitor; two earlier wrong guesses (cloud, cloud-perf) corrected by empirical proof. Deployed via
`deploy.ps1` + `systemctl restart orderflow` (10-day history preserved).

**Mode-10 SELECTION EXHAUSTION lines — gated, fresh-from-selection (`fe0a714`).** Two smoothed lines (blue=bull/
red=bear) of each side's exhaustion across the selected buckets, in a PANEL BELOW the box (outside it, off the
candles), 0/50/100% scale, gold diamonds at crossovers. `'1'` toggles; hover = RAW per-bucket %.
- **Audits first:** raw effort-z (`b_mult`/`s_mult`) is NOT saturated (centered ~0.94; the 96%-at-flips was a
  loose binary). GATED (`geomean(absorb, drain/cover, Δsoft)` = effort hot AND OI draining) is the TRUE worn-out
  signal, separating worn-out from strong-continuation by **36×** but sparse (~6-11%). Chart-wide Keltner cloud
  of it was built then SCRAPPED (operator wanted selection-scoped).
- **MEASURE = GATED** (`config.EXH_MEASURE="gated"|"raw"`, gated default; `region_state.selection_exhaustion`).
  Shown side-by-side on real data: raw = smooth weave but mislabels a WINNING side as exhausted (sellers
  driving down read "95% exhausted"); gated stays 0% there (OI building) — honest.
- **BASELINE = FRESH FROM SELECTION START:** z vs ONLY the prior buckets in the selection (expanding,
  `EXH_SEL_MIN_WINDOW`=2; bucket 0 neutral; `_exh_z_mult` got a `min_window` param). Selection-relative; a
  uniform trend self-normalizes to ~0 (fires on a bucket abnormal vs its neighbors, not "the move went down").
- **INTERP (told to operator):** bull = buyers worn out pushing UP (tops); bear = sellers at bottoms. Down-move:
  BULL 0% correct (buyers not the pushing side; bull only scores on up-close). Both 0% = "trend has fuel, no
  reversal warning." Bull-spike + bear-0 in a downtrend = "bounce out of buyers, sellers fresh → trend resumes"
  (NOT a bottom; bottom = mirror: bear spiking, bull fresh).
- **DISPLAY:** raw → SYMMETRIC envelope (`envelope_symmetric` = max forward+backward, crossover at the true
  shift) → panel-mapped. Panel `EXH_STRIP_GAP` below the box, height `EXH_STRIP_FRAC` of the selection range, NO
  backing. Crossovers gated by `EXH_CROSS_PERSIST`. `ExhaustionStripLayer` (chart_widgets). The candle-relative
  mapping (lines off each close ±MAX_DIST×ATR) was tried + REJECTED for the panel. **EXE still stale.**
- **Follow-up (`4b52c91`):** selection-box ABSORPTION·VOL / EFF-AGG·VOL lean ratio → **2 decimals** (`:.2f`,
  e.g. `1.40× bear`) instead of `:.0f` ("1×"). bull-only/bear-only cases unchanged.

**Mode-10 selection panels (eff-agg + E/R) + panel-aware hover + UX defaults — BUILT (`5fc6221`).**
- **EFF-AGG EVOLUTION panel (`'2'`):** per-bucket bull/bear eff-agg as two NEON green/red lines stacked below the
  exhaustion strip. Reuses the eff-agg-ZONES arrays → `envelope_symmetric(EFF_STRIP_RELEASE)` → **shared-max**
  (bull-vs-bear magnitudes compare). One-sided per bucket → shows the bull→bear handoff. No diamonds. Validated.
- **EFFORT/RESULT panel (`'3'`):** buyer/seller E/R as two green/red lines; TWO-sided → continuous curves.
  **Promoted out of FLOW TRAJECTORY** — `E/R` sparkline row + `spark_er` REMOVED (Op/Cl L/S stay; flip detector
  keeps its own `er_seq`). Outliers moderate (max/median ~4×); shared-max, p90-norm is the fallback.
- **Reuse:** both panels share the parametrised `ExhaustionStripLayer` (now takes `rgb_bull/rgb_bear/rgb_cross`,
  like `AbsorptionZoneLayer` serves both zone kinds). Knobs `EFF_STRIP_*` / `ER_STRIP_*` mirror `EXH_STRIP_*`.
- **PANEL-AWARE HOVER (fix):** old `_hover_exhaustion` showed the exhaustion % anywhere (never checked cursor Y).
  Now `_hover_panels` registers each VISIBLE panel's y-band + label + RAW values per refresh and shows ONE
  labelled tooltip for ONLY the panel under the cursor (`EXHAUSTION %` / `EFF-AGG K` / `E/R BUY·SELL K`); candles/
  box/gaps → nothing. `exh_tooltip`→`panel_tooltip`, `_exh_hover`→`_panel_hovers`.
- **GAP-COLLAPSE STACKING:** panels were fixed slots (hiding one left a gap); now only VISIBLE panels take a slot
  → hiding `'1'`/`'2'`/`'3'` slides the lower ones UP (simulated across all combos).
- **UX defaults:** OB + Absorption/Iceberg **default OFF** (`m10_obs`/`m10_icebergs`); **`'o'`** toggles both
  (`_toggle_ob_iceberg`). **VPIN sub-pane collapsed by default** (`_collapse_vpin_pane`, `setSizes([10_000,0])`
  on both linked splitters via `singleShot(0)`; drag up to reveal). **Drawing toolbar shown on launch**
  (`self.drawbar.show()` — it self-`hide()`s in its ctor and the default-checked menu signal isn't wired at
  build). **EXE still stale.**

**Mode-10 LEAN panels — absorption panel + share lines + separators + E/R zoom — BUILT (`a2efc72`).**
- **NEW ABSORPTION panel + reshuffle:** top→bottom `1` ABSORPTION, `2` EFF-AGG, `3` E/R, `4` EXHAUSTION
  (exhaustion off `'1'`). Absorption = **NEON green / NEON purple** (`_RGB_ABS_*`).
- **LEAN as SHARE lines:** absorption/eff-agg/E/R now plot each side's **share of the pair** (two lines → 100%,
  cross at the **50% even midline**), not raw vol. **ROLLING** centered window (`region_state.rolling_share`,
  `config.LEAN_WINDOW_FRAC=0.25`/`LEAN_WINDOW_MIN=5`) → tracks the LOCAL lean + shifts. Cumulative tried first,
  REJECTED (flattens to the right edge). One-sided-per-bucket (abs/eff) ⇒ needs the window; E/R two-sided.
- **SELECTION-PURE:** abs + eff panels recomputed on the SLICE (proven panel==sliced; was 21/60 & 12/60 peeking).
  ZONES keep the full-history norm (panel ≠ zones, by request). E/R + exhaustion already pure.
- **E/R ZOOM:** `config.ER_LEAN_GAIN=3.0` multiplies E/R's deviation from 50% (display only, clamps; hover = true
  share) — E/R hugs the midline otherwise (two-sided).
- **SEPARATORS + clean panels:** hairline dividers per inter-panel gap (`PanelSeparatorLayer`, centre-fading via
  SOLID constant-alpha SEGMENTS — a gradient *cosmetic pen* renders nothing, the bug). **Removed the dotted
  0/50/100% internal guides** from every panel (operator pref); "even" reads from the share-line crossing.
- **Stats Box default OFF** (`m10_stats=False`, `'s'` toggles).
- **`OrderFlowTerminal.spec` → ONE-FILE exe** (binaries+datas in `EXE`, `runtime_tmpdir=None`, no `COLLECT`) →
  single portable `dist/OrderFlowTerminal.exe` for the operator's other PC. **EXE REBUILT this batch** through
  `a2efc72` — first fresh build since `9a1fa6a` (06-23); all the session's panel work is now in the exe.

**Spread badges + live PHASE tables (`a987dc3`).**
- **Spread badges:** per lean panel, the dominant side's lead (`|bull%-bear%|`), black on NEON green (bull/buy
  strongest) / NEON red (bear/sell). absorption strongest = LOWEST share, eff/E-R = highest.
- **Phase tables:** **UP (green) + DOWN (red) side by side**, right of the panels; classify the selection as
  before/start/during/end. Confidence = **live naive-Bayes posterior** `P(phase | selection's with-move
  spreads)` over `config.PHASE_STATS` (mean/std from R=30% moves), normalized, leading phase highlighted, live.
  `_phase_table_html` + `phase_tbl` TextItem. **EXE now stale again** (not in the `a2efc72` build).

**Phase PANELS + START/DURING merge + EMA-confidence opacity, selection-WARMED (`2766668`).**
- **3 phase panels** (`'5'` BEFORE / `'6'` START/DURING / `'7'` END), two lines each (UP green / DOWN red) =
  that phase's smoothed confidence; mirror the table rows, gap-collapse under panels 1-4. `bc_phase` dict.
- **Start+During MERGED** into one `START/DURING` (table + panels): summed at the posterior level (`_phase_post`
  returns `[p0, p1+p2, p3]`), `self._PHASES=("BEFORE","START/DURING","END")`; `PHASE_STATS` stays the 4-way
  classifier — only the *display* is 3.
- **Opacity = the live posterior smoothed by an EMA** (`op = λ·op + (1-λ)·posterior%`, `PHASE_EMA_LAMBDA=0.8`),
  replacing the old +1%-per-fire accumulation. `_phase_opacity_traj`→`_phase_conf_traj`. Conserved at 100, rows sum 100.
- **Selection scope (operator-chosen after 3 tries):** (a) EMA, (b) pure posterior no-EMA, (c) EMA strictly
  inside the selection — operator kept the EMA **WARMED through the `_lw` (~15) buckets just before `lo`** so the
  left edge is settled, not cold-starting. Only that pre-roll reaches outside `[lo,hi]`; the displayed trajectory/
  table/panels are the `[lo,hi]` slice, inputs otherwise selection-pure (PROVEN: slice==isolated-copy; differs
  from the leaky full-list on the early buckets). **EXE still stale** (none of this is in the `a2efc72` build).

**Mode-10 selection panels 8 & 9 + default reshuffle (`6bbd79a`, 2026-06-27).**
- **Panel 8 — Net Flow / OI-Δ (slot 8):** per-bucket Net Position Flow `(opL+clS)-(opS+clL)` emerald>0/crimson<0
  about a dashed zero baseline + Net OI Δ `(opL+opS)-(clL+clS)` yellow-dashed on the SAME band scale (Flow/OI
  divergence at a glance). `'8'` toggles; hover FLOW/OIΔ. `_selection_flow_curves` (L12-separated: raw
  opL/opS/clL/clS never reach the widget). [Iterated through a 9-state verdict engine that was BUILT then
  REMOVED on operator call — net result is just the two lines.]
- **Panel 9 — Thermal Divergence Oscillator (slot 9, BOTTOM):** `(Z_buyer-Z_seller)·vol_mult` from the RAW Step-5
  adaptive z's (`selection_exhaustion('raw')`, independent of Panel-4's GATED measure so they complement). ONE
  sign-split line, NEON CYAN (buyer-exh into a ceiling) / HOT MAGENTA (seller-exh into a floor) about a zero
  baseline; **SIGNED-LOG compressed** before band-mapping so a single velocity BURST can't flatten the rest of
  the line. `'9'` toggles. [Went line→cumulative-delta→bar-histogram→line over the session.]
- Both: slot/stack with the lean panels (slide up as others hide), Rule-0.6 empty guard + full clear on every
  redraw, items reused via `setData` (no per-frame alloc / no ghost), wired into `_selection_signature` + the
  3 teardown branches. **EXE stale** (not rebuilt this batch).
- **Default reshuffle (operator):** panels **1-4 ON**, phases **5-7 OFF**, **stats box HIDDEN** (`'h'`). REVERSES
  Fix-0's perf default (1-4 off). Panel 4 (gated `selection_exhaustion`, the 127ms@800 fn) is ON by default again
  — but it's signature-gated (Fix 1) so it recomputes only on a selection change, not per frame.
- **⚠️ STRATEGY VERDICT — P9 reversal is DESCRIPTIVE, not a predictor (settled; both sides, both metrics).** The
  operator's "down-move (≥20 buckets, 30% retrace) → P9 seller-exhaustion SPIKE → P1 absorption green → P2/P3
  ignition → market-buy" — with a RELATIVE threshold (spike ≥ f·max|ΔZ| of the move), a buyer-exhaustion VETO,
  0.3% SL / 1:1.5 TP, last-5-days SOL 5m — was backtested CAUSALLY. **Long 0–18%, Short 20–33% win-rate — both
  WELL below the 40% breakeven**, on BOTH raw ΔZ AND the velocity-weighted Panel-9 line. Maximal-confluence
  entries (P1/P2 ±100%) lost as often as marginal ones → no "optimum spread." The ignition confirmation enters
  LATE (top of the bounce) and the stop is tighter than the target → structural stop-outs. Extends the prior
  INVESTIGATION VERDICT to include P9. Data note: raw `ΔZ·vol_mult` carries ±1000s outliers (`vel_ratio` blows
  up when `avg_velocity`→0) — hence Panel 9's signed-log display; a faithful re-test would threshold on the
  log-compressed line. Only 21 (full)/16 (5-day) qualifying down-moves exist anyway — too few to fit thresholds.

**Mode-10 rework: liq-wave panel 8 + FIXED-WINDOW lean panels; panels 8/9 DROPPED (`9111fa1`, 2026-06-28).**
- **DROPPED panels 8 (Net Flow/OI-Δ) + 9 (Thermal):** operator doesn't use them. All `bc_flow_*`/`bc_exh9_*`
  items, `show_flow_panel`/`show_exh9`, `_toggle_*`/`_clear_*`, `_selection_flow_curves`, the `'8'`/`'9'` keys, and
  the signature + 3 teardown branches removed (grep-clean).
- **NEW panel 8 — Liquidation Pressure WAVE (key `'8'`, default ON):** fixed `LIQ_WAVE_WINDOW=10` trailing rolling
  **SUM** of net forced flow `liq_short - liq_long` per bucket, **signed-log** compressed, ONE sign-split line —
  **CYAN up** (forced BUYS = shorts liquidated = squeeze) / **MAGENTA down** (forced SELLS = longs liquidated =
  flush) about a zero baseline. Read: line **steepening** = cascade building (surf it), **curling back to the
  baseline** = wave exhausting. Validated on the real `buckets_5m_liq` tape — the biggest cascade (a long flush)
  shows the wave deepening −33k→−44k→−60k then leveling (−60021→−59459 = fading). Per the bt11/bt12 verdict it's a
  **descriptive / confirmation** read (liquidations = weak continuation but cost-fragile, +0.08% < ~0.1% fee), NOT
  a standalone trigger.
- **FIXED-WINDOW lean panels (`LIVE_PANEL_WINDOW=15`) — root-cause fix for "values change when I move the
  selection START."** Panels 1/2/3 share + panel-4 exhaustion baseline now read a FIXED trailing window anchored
  at each bucket, NOT a selection-relative window (`LEAN_WINDOW_FRAC`, now legacy) / expanding-from-start
  z-baseline. New `region_state.trailing_exhaustion` (fixed-window twin of `selection_exhaustion`; z-baseline =
  `buckets[max(0,k-window):k]`). Pre-roll `_pre0 = min(lo, LIVE_PANEL_WINDOW+ABSORP_VOL_WINDOW)` so the left edge
  reaches real history; compute over `_extp`, slice `[_pre0:]`. PROVEN: live-edge P1 share = **0.5736 identical**
  across selection starts 1900/1850/1700/1500 (OLD swung 0.25–0.45) → a stable live read, not a moving ruler.
- **Panel 4:** gold dashed **50% midline** (`bc_exh_mid`, `#ffd700`), hidden wherever `bc_exh_strip` hides.
- **`'T'` shows the phase table WITHOUT a phase panel (5/6/7) on** (`show_phase_table`; the phase-block gate is
  `any(show_phase) or show_phase_table`, hides the `phase_tbl` only when both are off).
- **Key fix:** liq wave is `'8'` **not `'l'` — `'L'`/`'l'` is the Liquidity-HEATMAP layer**; a duplicate `'L'`
  `QShortcut` was an *ambiguous shortcut overload* that silently disabled BOTH (caught on operator eyeball, then
  re-keyed to `'8'` per operator). Keys now stack: `1`-`4` lean, `5`-`7` phase, `8` liq-wave, `T` table.
- **EXE stale** (not rebuilt this batch).

**Panel-9 bull/bear/sum study + ABSOLUTE bucket index + bucket-chart UI (`8e8ddd2`, 2026-06-29). DAEMON REDEPLOYED.**
- **Panel 9 reworked — single composite → TWO trend lines + a sum (no flip dependence).** `bull = (lean +
  seller-exh)/4`, `bear = (lean − buyer-exh)/4`, where `lean = absorption + eff-agg + E/R` signed spreads
  (positive=bullish, SHARED). Each line carries ITS OWN side's gated exhaustion forward independently (`trailing_exhaustion`).
  Why two lines: the lean is symmetric (one line); only exhaustion is two-sided, so each trend's line diverges
  by its own capitulation signal. **BULL** green >0 / muted-grey <0; **BEAR** red <0 / muted-grey >0 (`_split_curve_by_sign`).
  **NEON-BLUE sum** = bull+bear → the exhaustion cancels = `lean/2` (pure lean confluence); thin 1.3.
  Gold dashed **±50%** refs (`PANEL9_SCALE=100`, custom dash pattern), dim zero baseline. THREE right-edge boxes:
  bull(green/grey) over bear(red/grey) stacked at `_badge_x`; the **sum box centred well right** (where the
  operator pointed), bg green/red **by sign** (line stays blue). Hover (`_panel_hovers` gained an optional 3rd
  `extra` slot) → BULL/BEAR/SUM. Items: `bc_p9_{zero,gold_hi,gold_lo,bull_g,bull_x,bear_r,bear_x,sum}` (`self._bc_p9_items`).
- **ABSOLUTE per-tf bucket index — on-screen `Idx` is now DB-anchored + stable.** `engine.total_closed` is a
  per-tf monotonic close counter, **persisted in the `meta` table (`total_closed_<tf>`) and RESTORED on rehydrate**
  (bootstrap = retained row count), so it survives restarts + 10k pruning and never drifts. It is **NOT**
  `closed_buckets.id` — that autoincrement is shared across all 5 tfs, so per-tf it's gapped (×8 for 5m, verified
  on the VM). Shipped via `total_closed` on CatchupStart/Ob/Catchup packets → `pipe_client` → terminal
  `_global_idx_offset = total_closed − len(window) + 1 + anchor_idx`; `Idx = offset + local_idx`, dot-formatted
  (`_fmt_idx`: `20.000`). **Resolution recipe (read it before fetching a cited range) → memory `bucket-index-resolution`.**
  Verified live on `smc-quant-eu`: meta carries `total_closed_{1m..4h}`, 1m=10001>cap(10000) (monotonic ✓).
- **Bucket-chart UI:** minimalist spot pill (white bg, centred bold price over fill%, no Price/$/Fill/Base; price
  line thinner 0.8 + light-grey); **Keltner Channel** overlay (`_keltner_bands`, EMA(close,`KELTNER_LENGTH=20`)
  ± `KELTNER_ATR_MULT=2.25`·Wilder-ATR) light-grey bands, **EMA mid HIDDEN, the POC-center baseline KEPT** (operator
  corrected an initial swap); `Elapsed` formats by magnitude (`_fmt_elapsed`: `45.0s`→`1m15s`→`1h35`).
- **EXE stale.** Daemon side (persistence/feeds/protocol/pipe_client) was **deployed + `orderflow` restarted** by the operator.

**Panel 0 + per-panel lock dividers + orange midlines + panel-state PERSISTENCE + UI polish (`3b23333`, 2026-06-29). Terminal-only.**
- **Shared helper `_draw_lean_lines`** — Panel 9's bull/bear/sum draw (refs, lock divider, sign-split, sum, badges,
  hover) was factored out so panels 9 + 0 stay in lock-step. Params: `show_lock`, `sum_only`, `clip_lock`, `tail_item`.
- **PANEL 0 (`'0'`, smoothed twin of P9, default on, very bottom):** each line = `(current + locked-7-back)/2`
  (`bull_line[max(0,k-7)]` avg). Shows ONLY the neon-blue SUM (`sum_only`): the LOCKED region (≤ hi-7) solid blue,
  the non-locked settling tail (`clip_lock`) drawn on a separate light-grey dashed `bc_p0_sum_tail` (overlaps the
  join); bull/bear lines + badges hidden; lock divider kept; badge = the last locked value. Full P9-clone item set
  `bc_p0_*` + badges `PANEL0_{BULL,BEAR,SUM}`.
- **LOCK-IN dividers (vertical light-gray dashed)** on panels 1/2/3/4 + 9: mark where each value is fully formed
  (left = locked). 1/2/3/9 = `LIVE_PANEL_WINDOW//2`=7; panel 4 = symmetric-envelope tail `ceil(log .1/log EXH_RELEASE)`≈5.
  `_draw_panel_lock` (1-4) and inline (9/0).
- **ORANGE 50% midlines:** panels 1/2/3 gain a 50% even midline (`bc_{abs,eff,er}_mid`); panel-4 `bc_exh_mid` + panel-9
  `±50%` recoloured gold → orange `#ff9800`.
- **PANEL-STATE PERSISTENCE:** toggles (1-9 + T) `_save_ui_state()` on every flip → `data/terminal_ui.json`, restored
  by `_load_ui_state()` in `__init__` (overrides code defaults; missing keys keep default = forward-compatible). So a
  reopened session keeps the layout — no more "toggle X by default" requests. **Default-hide panels 1/3/4/8** (keep 2/9/0).
- **Selection arrows:** Right/Left move the Magic-Selection RIGHT edge only (+1/-1 bucket), left edge fixed, clamped
  to ≥1 bucket (`drawing_tools.extend_selection`).
- **Stats box:** `Elapsed` formats by magnitude (`_fmt_elapsed`); Absorption + Eff-agg colour ONLY the dominant side
  (mute the other); Panel 4 gained a dominant-exhaustion badge. (A curr-(locked) badge format was added then removed.)
- **EXE stale.** No daemon change — terminal-only; just relaunch.

**Mode-10 selection PERF pass — 5 correctness-preserving fixes (`3b176e6`→`a89a2fd`).** Profiled first on real
data (N=200/400/800); the theory was REVISED: `rolling_share`'s O(N²) is real but small (~12% of the phase
block); the bigger costs are the trailing-50 norm re-sums and the per-bucket exp/log posteriors (`conf_traj`,
~26ms@800, UNoptimised); the single worst fn (gated `selection_exhaustion`, 127ms@800) is a MEASURE panel.
- **Fix 0 (`3b176e6`):** the 4 MEASURE panels default OFF (phase panels stay ON) — removes the exhaustion
  classifier from the default session. No value change.
- **Fix 1 (`b626526`):** change-detection gate on `_refresh_selection_stats` (`_selection_signature`, a pure
  staticmethod). Skips the heavy recompute when nothing affecting the output changed; only the cheap box
  reposition still runs. Live edge is in the key ONLY when the selection touches it OR the adaptive-VPIN
  baseline is active — so a static selection away from the edge is skipped EXACTLY (proven: outputs bit-
  identical across a live tick). **Static big selection: 117ms×20Hz → ~0.**
- **Fix 4 (`a9c2669`):** O(N) PREFIX SUMS for the trailing-50 norm (`absorption_series`/`eff_agg_series`) and
  `rolling_share` (was O(N·50)/O(N²)). `_absorption_core` is the shared O(1) tail (so `absorption_vol` stays
  bit-exact for the hover path). PROVEN negligible epsilon: max abs diff 2.4e-10 on volumes (~1 ULP), share
  diff 2.7e-15, zero/branch preserved, **displayed share % 0/4278 mismatches** (pixel-identical). 4.5×/4.7×/3×.
- **Fix 3 (`a814a4a`):** `eff_agg_from_absorption` reuses the absorption `s` instead of re-deriving it (the 3
  absorption passes have DIFFERENT norms — full/pure/ext — so they can't merge; the s-reuse is the safe win).
  BIT-IDENTICAL (max diff 0.0).
- **Fix 2 (`a89a2fd`):** the phase block is gated on `any(show_phase)` — turning the phase panels off now does
  ZERO phase work (the table follows the panels). Block body unchanged → default session byte-identical.
- **Result:** static selection ~0; per-recompute (live-edge/drag) ~1.7–1.9× (27→16 / 49→29 / 117→61ms). The
  `conf_traj` exp/log posteriors are the largest remaining residual (O(N), not quadratic). **EXE still stale.**
  Suite 9/9 (2 pre-existing fails: stale `_exh_z_mult` import in test_step5, removed `recalibrate` API in
  test_step19_4 — both fail at HEAD, unrelated).

**🟢 BOOKMAP HEATMAP — Phase 1: whole-book depth capture + trade tape (`9d048c4`). FOUNDATION — depth-over-time
now EXISTS for the Phase-2 heatmap.** The daemon already maintained the full resting book live
(`pulse_state.local_ob`, ~4Hz) but DISCARDED it; Phase 1 persists it as a bounded 6h rolling window in a
**SEPARATE `data/depth.db`** (own connection/sync/prune — deletable without touching `history.db`).
- **`app/depth_store.py`** (new) — `DepthStore`, 3 tables: `depth_snapshots` (full-book anchors every
  `DEPTH_SNAPSHOT_SECS=30` + one per diff-stream reconnect, so the delta chain never has an un-anchored gap),
  `depth_deltas` (LOSSLESS per-diff level changes — nothing sampled), `trade_tape` (every aggTrade for real
  bubbles). Packed binary `(int32 price-ticks, float32 qty)`, bids/asks side-implicit sub-arrays, qty==0 kept.
  Binance diffs are ABSOLUTE quantities → idempotent → exact replay. `reconstruct_at_u()` = nearest anchor +
  replay deltas (Phase 2 render + the proof).
- **HYBRID (snapshots + deltas)** + whole-book (`DEPTH_BAND_PCT=0.0` = no truncation; knob kept for banding).
- **6h HARD prune** every sync (`DEPTH_RETENTION_HOURS=6`): drop deltas/trades < cutoff; keep the ONE anchor
  at/just-before cutoff so the oldest retained delta still has a snapshot. Row counts hard-bounded to ~6h.
- **ISOLATION (critical — the close-broadcast fix stays safe):** O(1) capture tees only — `_capture_depth_diff`
  AFTER `public_stream` applies the diff; `_capture_trade` in `aggtrade_stream` AROUND (never inside)
  `_process_aggtrade`; bounded drop-oldest buffers drained OFF-LOOP by `DepthStore.sync_loop` (own depth.db
  txn). `_process_aggtrade` + the close/`ObPacket` path are BYTE-UNCHANGED. All gated by `DEPTH_CAPTURE_ENABLED`.
- **VALIDATED live (175s):** lossless reconstruction = reconstructed book == live book at update-id u
  (1013=1013 bids / 1006=1006 asks, 0 missing/extra/qty-mismatch); size TRUE 0.479MB/173s → **~40-45MB/6h** at
  30s snapshots (the 193MB mid-run was un-checkpointed WAL transient), well under the 500MB budget; prune holds;
  25 close-broadcasts fired DURING capture; suite 9/9. Deploy was additive-only (gated, separate db created
  fresh on the VM; existing feeds/buckets/close-broadcast untouched).
- **✅ DEPLOYED + VERIFIED LIVE on `smc-quant-eu` (2026-06-26).** Restart clean (REHYDRATE 22078 buckets →
  LISTENING). depth.db accumulating: deltas ~3.84/s, trades ~3.95/s, snapshots every ~30s, 42 changes/diff;
  TRUE compacted size projects **~44-53MB/6h** (the 124-178MB raw was un-checkpointed WAL — confirmed via a
  PASSIVE checkpoint), well under the 500MB budget. Closes still firing post-deploy (`closed_buckets` grew,
  `engine_state` written ~10s ago); no errors/regression. **The 6h depth+trade window is now LIVE and
  accumulating — the foundation for Phase 2.** (Pre-existing note: the OLD process took a hard SIGKILL on
  restart — `stop-sigterm` timed out — so its clean-shutdown flush was skipped; the 10s periodic sync + SQLite
  WAL recovery meant zero consequential loss. Graceful restart = a future systemd `KillSignal`/`TimeoutStopSec`
  look, NOT a Phase-1 issue.)
**🟢 HEATMAP Phase 2a — the `depth_window` daemon endpoint (`1b339ab`).** The terminal can't read the VM's
depth.db directly (the tunnel carries only IPC frames), so the heatmap needs the daemon to SERVE depth
windows. `protocol.py`: request `{"action":"depth_window", t0,t1, cols:W, ylo,yhi, ybins:H}` (+ `depth_window_stop`)
→ `DepthWindowPacket` (W×H base64 float32 grid of RAW resting size [terminal applies log+LUT+cutoffs locally,
sliders never hit the daemon] + per-col BBO); `DepthColumnPacket` = one live column at the pulse cadence.
`depth_store.build_window` = ONE forward pass O(deltas), `mode=ro`, run in an executor (OFF-LOOP — never blocks
feeds/capture/close-broadcast); `bin_live_book` bins the in-RAM `local_ob` for the live edge. `daemon._handle_control`
routes it off-loop + `depth_live_loop` pushes live columns to `client.heatmap` subscribers.
- **⚠️ CORRECTNESS LESSON (record — relevant to ANYTHING that replays the depth deltas): the delta chain DRIFTS
  from the snapshots.** Binance `@depth` drops messages and the daemon has NO gap-recovery, so walking deltas
  alone from a single anchor produces a progressively-WRONG book (validation caught it: mid-window columns
  200/500/999 mismatched + BBO off by a tick). The periodic snapshots are the GROUND-TRUTH RESETS they were
  designed to be — so `build_window` is a true HYBRID that RE-ANCHORS at every intermediate snapshot (merge-walk
  snapshots+deltas by (ts_ms,u)). After the fix: build_window == independent reconstruct, **max rel 4.2e-08
  (float32 grid), BBO EXACT** every column. NOTE: `reconstruct_at_u` (Phase 1, single u-prefix) does NOT
  re-anchor — fine for the live edge / one point, but a multi-point/window replay MUST re-anchor at snapshots.
- **PERF (accepted):** O(deltas), pure-Python floor ~357ms/35min, ~3.6s/6h. OFF-LOOP so it never lags the
  daemon; 2b LAZY-LOADS a recent window (~0.35-0.7s) + fetches older columns on scroll, so the 6h cold build is
  never paid up front. Live column 0.22ms. NOT made faster via C (won't add a VM build dep for a one-time cold
  open). Isolation: read-only (db mtime unchanged), suite 9/9.
- **✅ DEPLOYED + VERIFIED LIVE on `smc-quant-eu` (2026-06-26).** Clean restart (REHYDRATE 22116 → LISTENING).
  The DEPLOYED `build_window` is BIT-FAITHFUL on the VM's real depth.db: == independent reconstruct, **max rel
  diff 0.0, BBO exact** every column — the snapshot-reanchor holds in production. Read-only confirmed (db mtime
  unchanged by a build); capture/closes still flowing (`engine_state` written ~4s ago, depth deltas growing,
  last delta ~5s ago); no regression. The `depth_window` endpoint is LIVE serving correct windows. **VM perf:
  ~1085ms/0.95h, ~7s for a 6h cold build (e2-standard-2 ~2× my local) — OFF-LOOP + 2b lazy-loads a recent
  window, so the 6h cold build is rarely paid.**
- **Phase 2b is BUILT and the live-price latency bug it surfaced is DEPLOYED — two entries below. Next: Phase 3 bubbles.**

**🟢 HEATMAP Phase 2b — terminal render (`69a376c`, BUILT + EYEBALLED on SOLUSDT).** Scanner-mode-gated
("Liquidity Heatmap (Bookmap)"); the ~MB grid rides a SEPARATE `pipe_client` delivery buffer
(`depth_heatmap_state`), NEVER the 20Hz `snapshot()` — zero cost when the mode is closed.
- **`app/heatmap.py`** (NEW, pure / no-Qt, headlessly tested): base64 float32 decode; `HeatmapCache` =
  contiguous time-ordered columns with O(1) live append, lazy CONTIGUOUS prepend on scroll-back, FREE re-slice
  on a pan inside the loaded range, time-span trim; percentile contrast; neon diverging LUT + side-sign.
- **Render** (`_scan_depth_heatmap`/`_hm_render`): `ImageItem(col-major)` over a SIGNED pre-log grid
  (±log10(size+1), signed by side); adaptive contrast (percentile cutoffs + 60s renorm) via the
  `HeatmapContrastBar` sliders (default **p98.9 / p99.4**). Smooth 20Hz view-follow decoupled from the rebuild.
- **NEON palette by SIDE (Deepdom):** bids/buy = green, asks/sell = purple, intensity by size, TRANSPARENT below
  the cutoff (dark canvas shows, no blue wash). Greyscale 'g'.
- **BBO:** solid FORMED trace to the live edge + DASHED current bid/ask projecting forward (Bookmap LLT) +
  Y-axis price tags; crosshair = time + price.
- **Scan-Start HARD floor** (no back-fill before the chosen start); DATA window never extends past 'now' (blank
  to the right is empty view — no carry-forward flat lines, no force-back). **Tick-aligned ybins** (1 bin/tick).
- **DOM ladder (`cob_panel`):** default-ON in heatmap mode, neon-recolored, price-bucket AGGREGATION tracking the
  heatmap row height (coarsens out / refines to 1 tick in), crosshair-driven price+size readout.
- **Validated:** compile/import/suite 9/9 + headless (decode/axis-order, cache stitch/ring/contiguity, LUT,
  signed-cache, cob aggregation/mark); eyeballed live across several iterations. **Next: Phase 3 = trade bubbles.**

**🟢 LIVE-PRICE LATENCY FIX — recompute starving the broadcast loop (`ad4d467`, ✅ DEPLOYED + VERIFIED on
`smc-quant-eu` 2026-06-26).** Found during the 2b eyeball: live price "a few seconds behind, bursty" in BOTH
heatmap AND bucket modes. PROVEN on the VM (probe of the daemon output): a fresh 1m subscriber got 1 frame in
10s while the daemon burned ~70% of a core; the single asyncio loop blocked 3-4s every ~5s (`RECOMPUTE_SECS`).
Cause: `recompute_loop` + the 19.3b live-edge refresh rescanned OBs AND serialized the forming footprint / OB
matrix for ALL 5 tfs every cycle (`to_line()` runs BEFORE `broadcast_tf` filters by sub), and `calc_quant_obs`
is O(obs×buckets) (~3s on a ~10k-bucket 1m engine, the mitigation loop).
- **Fix:** (1) **subscription-gating** — per-tf work (recompute, live-edge footprint, kline tick, per-close
  packet) only for SUBSCRIBED tfs (`daemon.tf_has_subscribers` → `core._tf_subbed`); 1m terminal does 1m work,
  not 5×. (2) **skip-if-unchanged** — recompute only when `engine.total_closed` moved; the OB set is a pure
  function of the closed buckets (change only on a close, ~once/45s for 1m), so 4/5 rescans were identical.
  LOSSLESS; `catchup_start` still recomputes on connect.
- **Verified after deploy:** 3-4s/~5s → **~0.68s/~10s**.
- **⚠️→✅ Was PARTIAL here; STRUCTURALLY SOLVED by Step A (`5495a51`, below).** ad4d467 left the heavy
  recompute on the single loop (just ~5× less often) → would still hitch on 4h / fast markets. Step A moved it
  OFF the loop. Lone residual now = the ~0.68s/10s depth-flush PACKING hitch (`DEPTH_SYNC_SECS=10`) — Step A.2.

**🟢 LATENCY STRUCTURALLY SOLVED — process-pool offload of the OB recompute (Step A, `5495a51`, ✅ DEPLOYED +
VERIFIED on `smc-quant-eu` 2026-06-26).** `calc_quant_obs` is a PURE function of the closed buckets, so it now
runs on a SPAWN worker PROCESS (2nd core) — the broadcast loop NEVER blocks during recompute, any tf, any speed.
- **Design:** lazy `ProcessPoolExecutor(max_workers=1, spawn)`; `recompute_loop` snapshots `list(closed_buckets)`
  on-loop (immutable) then `await run_in_executor(pool, _recompute_ob_line, …)`; skip-if-unchanged in front;
  warm-up at boot.
- **FALLBACK (tested, non-negotiable):** any pool failure → tear down (reaps worker, no leak) → on-loop this
  cycle → recreate next → permanently latch on-loop after 3. Degrades to EXACTLY the pre-offload freeze, stable —
  never down, never thrash. Proven vs SUSTAINED failure (correct every cycle, latched, ≤3 pools, all shut down;
  real worker spawned+reaped).
- **BIT-IDENTICAL (pre-deploy):** on-loop == pool byte-for-byte on REAL engines (VM rehydrate, all 5 tfs, 22293
  buckets: 1m 145757B / 5m 32011B / 15m 55640B / 1h 51268B / 4h 50815B). Moves WHERE, not WHAT.
  `scripts/validate_ob_pool.py` (+ `_fallback.py`); run with `OB_VALIDATE_DB=<.backup snapshot>` on the VM.
- **Verified LIVE:** 1m max gap 0.68s (depth-flush only — recompute freezes GONE); **4h test** — a fresh 4h
  subscriber's 50831B rescan ran with NO loop gap (where Fix 1+2 froze seconds); worker on 2nd core ~7% CPU;
  daemon main-loop CPU **~70% → ~14%**.
- **✅ Step A.2 DONE (`572a7a5`, deployed+verified):** the ~0.8s/10s residual was NOT the depth-flush packing
  (measured 4ms — red herring, caught by MEASURING). REAL cause: the HISTORY sync's `prepare()` ran
  `calc_quant_obs` ×5 ON the loop every `SYNC_INTERVAL_SECS=10` to populate the **write-only** `order_blocks`
  table (never SELECTed; rehydrate recomputes via catchup). Fix: `obs=[]`. Proven write-only + rehydrate
  byte-identical (`scripts/validate_rehydrate_no_obs.py`); verified live: 0.8s/10s gap GONE, max loop gap now
  **~0.35s**. Recompute fully off-loop in BOTH places (recompute_loop pool + history-sync dropped).
- **✅ OI poll off-loop (`5cba32d`):** `fetch_oi_loop` does the `requests.get` via `run_in_executor` (network I/O
  releases the GIL) → the ~0.28s/5s hitch GONE.
- **✅ OB-pool graceful shutdown (`cea7b79`):** `shutdown_ob_pool()` (bounded daemon thread, can't block SIGTERM)
  cleanly reaps the worker — `OB POOL shut down cleanly` prints (clean join vs the old abrupt death).
- **✅ LATENCY DONE — real-time.** 3-4s/5s → Step A (pool) → A.2 (history rescan dropped) → OI off-loop →
  **~0.37s max, real-time; structurally solved (4h-proven), recompute off-loop in BOTH places.**
- **TWO consciously-ACCEPTED benign residuals (decisions, NOT gaps):**
  1. **~0.2s/10s footprint re-serialize** (history sync `json.dumps` ~1500 nodes ×5 tfs, mostly static). NOT
     fixed — the skip-unchanged lever has a RACE caveat (footprints mutate live); a real race risk for 0.2s of
     imperceptible gain on a 24/7 daemon = bad trade.
  2. **resource-tracker shutdown noise** — spawn `ProcessPoolExecutor` emits benign `leaked semaphore` /
     `sem_unlink FileNotFoundError` at interpreter exit. PROVEN benign (FileNotFoundError = already-unlinked;
     "leaked" = stale bookkeeping; every restart rehydrates fine — ZERO leak/data-loss). NOT chased — inherent
     CPython spawn-pool artifact (3 standalone repros, incl. worker importing app.feeds, could NOT reproduce;
     only VM-testable). Cosmetic, no functional cost = bad trade.
- **Next: Step B = Phase 3 trade bubbles** on the clean daemon — ✅ DONE (`40d2a69`).

**🟢 HEATMAP Phase 3 — executed-trade bubbles + iceberg overlay + UX polish (`40d2a69`, BUILT + EYEBALLED on
SOLUSDT).** Same isolation discipline as `depth_window` — the single event loop is never blocked; terminal
polish needs NO redeploy, the daemon trades endpoint deploys with `deploy.ps1`+restart.
- **Daemon/transport — PROVEN zero new loop-blocking (gap probe max 0.26s vs 0.37s baseline); lossless
  bit-identical on real VM data (7780 trades).** `depth_store.trades_window` (`mode=ro`, ts_ms index) →
  `_send_trades_window` off-loop (`run_in_executor`) + `trades_live_loop` batched push/pulse (O(1) capture-tee).
  `protocol`: `TradesWindowPacket`/`TradeBatchPacket` = 4 b64 arrays (ts i8, price/qty **f8 bit-identical**,
  side u1). `pipe_client` `trades_state` buffer — NEVER on the 20Hz `snapshot()`.
- **Bubbles (`heatmap.py` `TradeBubbleCache` + `_hm_render_bubbles`):** numpy (col×bin) aggregate → size by
  total qty / color by net side; two `pxMode` scatters green=buy/purple=sell, area∝qty (√r+clamp), `b` toggles,
  min-qty declutter. Hover pill (`sigHovered`, black-on-neon); `tip=None` kills the built-in x/y/data box.
- **Iceberg:** cells on an active absorption level recolor ELECTRIC BLUE (buy) / ORANGE (sell), from
  `snap['absorptions']` by price band (per-tf — same standing levels the bucket-chart `AbsorptionLayer` draws).
- **Polish:** fine **per-pulse BBO trace** (lines follow the live price, not the binned grid) · **shift+hover**
  resting-liquidity readout (`raw_at`, neighbour-search) · DOM `cob.autoscale_x` (in-view) · follow **15% lead
  gutter** + double-click re-lock + PAN-detaches-instantly/zoom-keeps · Contrast panel → extreme top-left ·
  menu **"Heatmap"**, 2nd in list · heatmap-open auto-cancels the draw tool (`cancel()` un-highlights toolbar).
- **Heatmap overlay is now FEATURE-COMPLETE (2b line + 3 bubbles).** Standalone exe rebuilt (`OrderFlowTerminal.spec`).

**⚠️ SUITE ROT (pre-existing, NOT from Phase 3 — found 2026-06-26):** `test_step5_exhaustion`
(imports `_exh_z_mult` from `app.terminal` — moved to `app.region_state`) and `test_step19_4_recompute`
(calls `QuantEngine.recalibrate` — removed) both fail at import/harness-helper, NOT on the logic they test
(19.4's real assertions still PASS). Both symbols are absent in `HEAD` too; an earlier refactor moved them
without updating the tests. 8/10 green. Un-rot when convenient: repoint test_step5's import to `region_state`;
19.4 needs the current calibrate entry point.

**⚠️ VERDICT — the balance-of-power SCORE/strategy is DESCRIPTIVE, not predictive (settled; don't rebuild as a
signal).** Hypothesis "move begins when absorption low + eff-agg/E-R high" tested exhaustively, all CAUSAL +
base-rate-guarded: direction not predictable (eff-agg only *describes* the move ≈ tautology; abs/E-R ~chance;
combined score / alignment / every coefficient ≈52% = chance; optimum collapses to eff-agg, absorption ≈ 0).
MOVES defined by RETRACEMENT (ZigZag, R≈30% — a leg is a MOVE once it retraces ≥R%); score over move K → move
K+1 dir = ~24% = `1−descriptive` (no forward info). E/R weakly ~ move SIZE (+0.22). Absorption-rises-into-end
(46%→65%) is real RETROSPECTIVELY but the **causal forward test killed it** (high against-absorption does NOT
warn of reversal — mildly opposite). So: great real-time DESCRIPTIVE read, not a forecaster. Phase table is
honest-descriptive (low posteriors shown). Don't re-attempt a predictive score without NEW data/symbol.

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
map), confirm the suite (§6) is green (8/10 `exit 0`; 2 pre-existing rotted — see the SUITE ROT note in §1.5), then pick the next
phase WITH the operator (Phase 2 → 3 → 4 → Mode-10 tool). §7/§8 are the Phase-5
record; the standing rules (§3) + the verification pattern (§4 — esp. FULL-suite-at-
every-commit, propose-then-approve, one-step-one-commit, hold-before-commit) govern
every future step.
