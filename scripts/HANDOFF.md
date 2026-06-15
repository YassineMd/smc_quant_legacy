# Pipeline-Integrity Handoff — read this first

You are continuing a multi-step data-integrity rework of a native order-flow
trading terminal for **SOLUSDT perps**. This note orients a fresh session without
re-deriving anything. **Phase 1 is complete; the next work is Phase 5 (aggTrade).**

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
  It is the immutable "before" reference for re-running the Phase-0 baseline. It is
  gitignored (the whole `data/` dir is). The live `data/history.db` is now new-math
  (the Step-3 schema guard wiped the old buckets on first Step-3 boot — expected).

## 2. Source of truth + what's next

- **`MASTER_FIX_PLAN.md`** (project root) is the authoritative plan. Read it. Every
  step references it. It is committed (`7895eb1`, updated `95a4503`).
- **Phase 1 (Steps 1–5) is DONE.** The corrected scalars every scanner mode reads
  are now accurate/honest.
- **NEXT = Phase 5, Step 19 (kline → aggTrade) — DESIGN APPROVED, IN PROGRESS at
  sub-step 19.0.** It was **PROMOTED** to come **right after Phase 1**, before
  Phases 2/3/4. Rationale (operator's call): they read the chart as a live *pulse*
  and need sub-second order-by-order flow; 1s kline is the fidelity ceiling.
  Sequencing: do it AFTER Steps 1–4 (done) so the source-swap doesn't confound the
  math verification — it's now a clean swap into already-trusted math. The full
  approved design + sub-step staging is **§8 below** (read it). **Step 15 was pulled
  out of Phase 4 into Phase 5 as sub-step 19.4**, so Phase 4 is now Steps 16–18.
  **Phases 2 (Steps 6–8), 3 (9–14), 4 (16–18) come AFTER aggTrade.**

## 3. Standing rules that govern EVERY step (from MASTER_FIX_PLAN §0)

- **§0.3 — Atomic schema change = TWO serializers + a version bump.** A bucket field
  lives in BOTH (a) the **wire** schema (`protocol.BucketSnapshot`, produced by
  `QuantBucket._assemble`/`live_snapshot`) and (b) the **persistence** schema
  (`persistence._bucket_to_dict`/`_bucket_from_dict`). They are SEPARATE, hand-kept
  serializers. Any field add/meaning-change must update **both**, every `terminal.py`
  consumer, **and bump `persistence.BUCKET_SCHEMA_VERSION`** (currently **2**), in
  ONE commit. The boot-time schema guard in `persistence.rehydrate_engines` clears
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
- **Live checks use an ISOLATED empty-DB daemon**, not the working db: a throwaway
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
**All five pass as of `258ed1c`** — note test_step2 had been silently red since
Step 3 (it asserted the superseded 4-component law) until that commit migrated it
to the 5-component law; this is exactly why §4 now mandates the full-suite run.
Run from the project root:
```
python scripts/test_step1_velocity_clock.py   # event-time clock, degenerate-duration guards
python scripts/test_step2_oi_clamp.py          # delta-OI/taker conservation clamp (boundary-spy + taker counterfactual)
python scripts/test_step3_churn_decomp.py      # opL+opS+clL+clS+churn==curr_vol; schema round-trip + guard
python scripts/test_step4_effort_result.py     # dispersion E/R: absorption vs run, wick-robust
python scripts/test_step5_exhaustion.py        # z-score exhaustion: scale-invariance, degenerate-safe
python scripts/test_step19_1_trade_mapper.py   # 19.1 aggTrade->args: exact m->side + T/1000 clock (tape replay)
python scripts/test_step19_2_oi_attributor.py  # 19.2 OI pending-balance: identity, K*Vw cap-and-hold, lag<=K, dead-vol floor
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
**Start here:** read `MASTER_FIX_PLAN.md` (esp. §0 + the Phase 5 PROMOTED note),
confirm the suite (§6) is green, then read **§8** for the approved Phase-5 design +
staging. Phase 5 is IN PROGRESS at sub-step 19.0. Do not move past a sub-step until
its gate is green and the operator has eyeballed it.
