# Pipeline-Integrity Handoff — read this first

You are continuing a multi-step data-integrity rework of a native order-flow
trading terminal for **SOLUSDT perps**. This note orients a fresh session without
re-deriving anything. **Phases 1 and 5 are COMPLETE — the terminal is aggTrade-native
and LIVE on the real v3 `history.db`. Next is the operator's choice of Phase 2 / 3 / 4,
then the Mode-10 selection tool (see §2).**

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

### DEFERRED QUEUE (reordered 2026-06-19)
1. **Time-chart removal — PROMOTED.** The time-candle entanglement caused the absorption-on-1m mismatch
   this whole dive fixed — cut it at the source (+ remove the dead Technical-Layers menu section).
2. **OB polish (A)** — min-render-height + duplicate-timestamp handling.
3. **Mode-10 selection tool (D)** — the capstone (see §2 / the plan's "After the pipeline is solid").
- **DOM book-mid line (idea, surfaced during the Phase-A DOM port) — DEFERRED, not Phase A.** The
  Mode-10 spot line is the last TRADE (`closes[-1]`); on a depth ladder the more useful reference is
  often the book MID (between best-bid/best-ask), so the reference sits in the spread between the COB
  bands. Idea: KEEP the last-trade line (honest "last print") and ADD a thin mid line (or best-bid/
  best-ask markers) so the spread + where the last trade sits vs the live book are both visible. NOT a
  bug (the COB is correctly aligned; this is an enhancement). Consider after the time-chart removal.
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
2. **NEXT — time-chart removal (PROMOTED).** The time-candle entanglement is what caused the whole
   absorption-on-1m mismatch this dive fixed. It is NOT just "delete the time-chart code" — it's "what
   SHOULD the pure-bucket surface be once the dishonest time-unit is gone?" Start with a FIRST-PRINCIPLES
   analysis (what the time-chart provides, what depends on it, what unifies once it's gone, the ideal
   bucket-native architecture) + a proposed removal plan with risk areas. PROPOSE before building. (Also
   remove the dead Technical-Layers menu section.)

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
- **NEXT — operator picks the phase (the CAPSTONE is Mode 10 itself — see §0; the
  Mode-10 selection tool is its final step, after the pipeline is fully solid):**
  - **Cheap validation — ✅ DONE:** footprint ladder + true POC render correctly on the
    Mode 10 bucket canvas (Stage-0 POC dot `6ec3578`, Stage-1 levels-on-wire `8fca531`,
    footprint viewport-cull/newest-first-cap/bubble-fallback fix `4dcf3ee`). Capstone
    surface proven. Root-cause record (the four-round saga: no viewport cull → 600-label
    budget spent on off-screen oldest buckets → live edge starved) = MASTER_FIX_PLAN
    "Mode 10 footprint ladder rendering — DONE". *(Distinct from time-chart Step 13, which
    is the same oldest-first cap bug in `FootprintLayer` and is still pending.)*
  - **Phase 2 — OB fidelity (Steps 6–8):** band over-extension fix (6), Otsu A/B
    (7, optional/skippable), proportional OB mitigation not binary death (8). Makes
    Mode-10 ORDER BLOCKS trustworthy.
  - **Phase 3 — visual layer (Steps 9–14):** stacked-imbalance flush (9), iceberg
    wick-mitigation (10) + adaptive detection (11), DOM per-side normalization (12),
    footprint text-cap newest-first (13), imbalance progressive vertical fill (14) —
    PLUS the diagnosed-not-fixed **Off-mode candle bug** (§5: `scanner_bars` wants
    `ignoreBounds=True`).
  - **Phase 4 — perf (Steps 16–18):** cluster DOM once/frame (16), cache zones on
    bucket-close (17), OB loop efficiency (18). (Step 15 already shipped as 19.4.)
  - **Then the capstone** — consolidate every finished overlay onto Mode 10 (ONLY after
    Phases 2–3 make each correct) + the Mode-10 selection tool vs the corrected scalars
    (MASTER_FIX_PLAN "After the pipeline is solid").
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
