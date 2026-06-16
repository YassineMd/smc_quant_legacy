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

### Group A — "Mode 10 as the primary surface" (the next build — ONE coherent step)
- **Make Mode 10 first** in the scanner-mode list **AND the default chart** on terminal
  open (today it opens on the time chart / "Off").
- ~~**Open-zoom frames the candles.**~~ **REMOVED from Group A — reclassified as a
  deliberate VIEW-FOLLOW design step (below).** Attempted as A0 (exclude the forecast
  cloud from the Y-fit + a footprint-scale reorder) and **REVERTED 2026-06-16**: it is
  *not* a simple Y-fit — it exposed a latent frozen-view bug. Root cause + the real fix
  are in "Mode 10 view-follow" below.
- **Per-overlay ON/OFF toggles, each independent:** POC, footprint ladder, icebergs,
  buy/sell imbalance gaps, liquidation marks, order blocks.
- **Upgrade the Mode 10 stats/hover box** to match the time-chart's richness, PLUS
  bucket-relevant fields: **bucket elapsed time, bucket size in volume.**
- **Remove the redundant gold POC ring/box** (the Stage-1 ladder highlight) — keep ONLY
  the gold POC dot (Stage 0).
- **Cursor shows the Y-axis value (price)** in all modes.

### Mode 10 view-follow — DELIBERATE DESIGN STEP (was Group A "open-zoom"; reclassified 2026-06-16)
**Not a quick polish item — its own deliberate step, done later, NOT now.** A0 tried to fix
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