# Handoff Manifest — Pure Quant Order Flow Terminal

_Generated 2026-06-14 at the close of the `update_instructions.md` polish pass._
_Updated 2026-06-14: §5.1 confirmed in the wild (real OB on the index grid) and §5.2 resolved (spot/base badge merge). Terminal exe rebuild pending — see §5.2._
_Updated 2026-06-14: tracker engine rebuilt — finite right-docked TradingView-style axis badges (`_scanner_tracker` finite `PlotCurveItem` rules + bold 13px badges + per-frame `_redock_trackers`); see §2. Exe still one rebuild behind both this and §5.2._
_Updated 2026-06-14: cloud deployment SHIPPED + live (see §7); terminal auto-tunnel + exe gcloud-path fallback verified. **BACKEND IS NOW LOCKED** — next session = frontend visual/UI/charting/drawing work ONLY._

This document is the authoritative snapshot of system state for the next session. It complements (does not replace) the project memory and the inline `# §N` comments in the source.

---

## 1. System Overview

A decoupled, two-process native desktop trading terminal for **SOLUSDT** perpetuals:

- **`OrderFlowDaemon`** (`python -m app.daemon`) — headless asyncio core. One combined Binance websocket streams all 5 timeframes (1m/5m/15m/1h/4h); per-tf `QuantEngine` (Otsu-calculus order blocks, 4-vector flow decomposition, VPIN); TCP loopback server on `127.0.0.1:9999` with per-client timeframe subscription and fire-and-forget broadcast.
- **`OrderFlowTerminal`** (`python -m app.terminal`) — PySide6 + PyQtGraph GUI, 20Hz render loop off a thread-safe `PipeClientWorker` cache. Multi-window (Ctrl+N).

Both ship as standalone exes in `dist/` (built via `build.py --onedir`). Runtime: Python 3.10.10, PySide6 6.11.1, pyqtgraph 0.14.0 (pinned to PySide6 via `PYQTGRAPH_QT_LIB` in `app/__init__.py`).

### Module map (`app/`, 5,660 LOC)
| File | LOC | Role |
|------|-----|------|
| terminal.py | 1555 | GUI window, 20Hz loop, **all 10 scanner modes**, crosshair/HUD |
| quant_engine.py | 528 | QuantBucket/QuantEngine (verbatim port + VPIN), `BucketSnapshot` producers |
| drawing_tools.py | 616 | DrawingController (press-drag-release), DrawnShape, PositionBracket, DrawingToolbar, ShapeEditPanel |
| chart_widgets.py | 497 | LocalTimeAxis (mode-aware), CandlestickItem, OrderBlockLayer (+`update_data_indexed`), BucketCandleItem, TextPool, footprint/depth layers live in footprint_layers.py |
| feeds.py | 337 | MarketDataCore — 5-tf combined kline + depth/liq/OI streams |
| hamburger.py | 303 | FloatingOverlayMenu — all controls, 11 scanner modes, scan-time anchor |
| pipe_client.py | 257 | PipeClientWorker socket thread + snapshot cache |
| stats_overlay.py | 218 | 12-line hover stats + state-matrix verdict |
| protocol.py | 211 | IPC wire frames + `BucketSnapshot` TypedDict (17 fields) |
| config.py | 188 | all constants, frozen-aware data paths |
| persistence.py | ~310 | **SQLite `HistoryStore`** — instant rehydration + async upsert sync (replaced JSON tick-replay) |
| daemon.py / alerts.py / cob_panel.py / hud_overlay.py / __init__.py | — | support (daemon now streams chunked CATCHUP + runs the sync loop) |

---

## 2. The 10 Scanner Modes (functional state)

X-axis = **dimensionless integer bucket ordinal** (`LocalTimeAxis.set_scanner_active(True)` → `"Idx: N"` labels). Data source = `snap["closed_buckets"]` + live `snap["active_bucket"]` appended as the pulsing right edge. Built by `_build_scanner_buckets()` (signature-gated; filters by the Scan-Start-Time anchor; live-edge dedupe by `(start_time, curr_vol)`). Dispatched by `_draw_scanner()` → `self._scan_<mode>(filtered, x_indices)`.

| # | Key | Render | Scale | Trackers |
|---|-----|--------|-------|----------|
| 1 | `open_pos` | cumulative opL/opS lines (green/red) | auto-fit Y | dual (up/down), pair-% |
| 2 | `close_pos` | cumulative clS/clL lines (blue/purple) | auto-fit | dual, pair-% |
| 3 | `exhaustion` | CVD-extreme × OI × E/R lines (blue/red) | **clamp −5..105** | dual, native % |
| 4 | `kinetic` | energy histograms (main vb) + forecast cloud (secondary `vb_kinetic_price`) | dual-axis | energy up/down + 3 price forecast tags on secondary vb |
| 5 | `volume` | cumulative buy/sell lines (teal/red) | auto-fit | dual, pair-% |
| 6 | `vpin` | rolling N=50 color-shift bars + 0.85 line | **clamp 0..1.05** | single mid |
| 7 | `bucket_open_pos` | mirrored opL up / −opS down, neon brushes | auto-fit | dual up/down |
| 8 | `bucket_close_pos` | mirrored clS up / −clL down, neon brushes | auto-fit | dual up/down |
| 9 | `effort_result` | mirrored bER up / −sER down | auto-fit | dual up/down |
| 10 | `bucket_canvas` | **dual-pane**: neon-V2 candles + forecast + **order blocks** (upper) / VPIN heatmap (lower) | upper price-fit, lower clamp 0..1.05 | spot+fill badge + 3 forecast tags |

**Trackers** (`_scanner_tracker`) — **right-docked TradingView-style axis badges (rebuilt 2026-06-14):** each = a **finite** dashed `pg.PlotCurveItem` rule running from the live data point `x_data` → the viewport right edge `x_max` (it **never** extends left into history) + a **bold 13px** color-coded `pg.TextItem` badge pinned at `x_max`, hard against the right Y-axis. The Mode-10 live spot line is the sole **`span=True`** exception (stays a full-width `InfiniteLine`); tag-only trackers (Mode-4 forecasts, Mode-10 bull/bear) pass `line=False`. Directional anchors `_TRACK_ANCHORS` up=(1.02,1.0)/down=(1.02,0.0)/mid=(1.02,0.5) stack converging values; x=1.02 hugs the axis. `target_vb` routes onto the secondary kinetic vb. **`_redock_trackers()`** runs every frame (ungated, in `_on_timer`'s scanner branch) re-pinning badges + finite-rule right-ends to the live `x_max`, so they stay docked under pan/zoom even when the signature-gated metric recompute is skipped; `_scan_trackers` is the redock registry (reset in `clear_scanner_canvas` + `__init__`).

**One-shot autofit** (`_scanner_needs_autofit`): `_fit_scanner_y` fits once per mode/anchor change then yields — manual zoom/pan persists, no 20Hz snap-back. Set True in `_set_scanner`, `_on_scan_time_changed`, `_change_tf`.

**Dark theme** (`_apply_scanner_theme`): scanner canvas → `#141414` + `#dcdcdc` axes; restored to light on Off. Forecast baseline pen `(180,180,180,150)` for dark-bg contrast.

**Perf gate**: `_draw_scanner` skips the heavy recompute when `(len(closed), active.curr_vol, scan_start_unix, mode)` is unchanged → zero idle CPU.

---

## 3. Mouse Gesture State Machine (the brittle core)

**Engine:** `DrawingController._vb_drag(ev, axis=None)` **overrides** `vb.mouseDragEvent` (original captured as `self._orig_drag` in `__init__`).

```
_vb_drag(ev):
  try:
    if locked OR active_tool in (None, "select", "eraser") OR ev.button()!=LeftButton:
        return self._orig_drag(ev, axis)        # native pan/zoom — untouched
    ev.accept()
    p0 = mapSceneToView(ev.buttonDownScenePos()); p1 = mapSceneToView(ev.scenePos())
    if ev.isStart():  _begin_draw(p0)            # create live rubber-band DrawnShape
    _update_draw(p1)                              # rebuild live shape each drag tick
    if ev.isFinish(): _finish_draw(p0, p1)       # commit + auto-revert
  except Exception:
    return self._orig_drag(ev, axis)             # FALLBACK: a draw fault never breaks pan
```

- **Single-click** (`sigMouseClicked → _on_click`) handles **only** `select` + `eraser`. The legacy 2-click model, `_on_move`, `_pending`, `_preview` are deleted.
- **`_finish_draw`** commits the shape/bracket, then **auto-reverts**: `set_tool("select")` + `toolbar.select_tool("select")` (programmatic `setChecked` — no signal loop).
- **Yellow follow-spot** (`terminal.cursor_spot`, `pg.ScatterPlotItem` 10px `#ffeb3b`): shown in `_on_mouse_move` when `drawer.active_tool not in (None, "select")`, else hidden.
- **Position brackets** (`PositionBracket`): 3 horizontal movable price handles (entry/stop/target) **+ 2 vertical movable span handles** (`left_line`/`right_line`, muted gray dashed, `SizeHorCursor`) → `_recalc_span` updates `x0/x1` + fill. Live R:R recolors teal/orange/red.

---

## 4. Design Guardrails (do not violate)

1. **Index-space drawings are SESSION-ONLY.** On `bucket_canvas`, `drawer.index_mode=True` routes new shapes/brackets to `_idx_shapes`/`_idx_brackets`, which are **never** written to `drawings.json` and are **flushed** by `clear_scanner_canvas` (Mode-10 teardown) via `flush_index_drawings()`. Rationale: changing the Scan-Start-Time anchor shifts what data lives at any `Idx`, so index coordinates are not stable. Time-space `shapes`/`brackets` remain persisted. Select/erase/clear scan **both** lists.
2. **Drawing lock:** `drawer.locked = (scanning and mode != "bucket_canvas")` — drawing is enabled on the time chart and on Mode 10 only.
3. **`bucket.start_time` is NOT unique** — multiple buckets fill within one busy minute and share it. Never key/dedupe by start_time alone. Live-edge dedupe uses `(start_time, curr_vol)`.
4. **In-chart text must use `pg.TextItem`/`TextPool`**, never `painter.drawText` (the inverted price ViewBox flips/zoom-scales QPicture text). `TextPool.clear(plot)` is the leak guard for pool-managed labels (they bypass `active_scanner_items`).
5. **`_fit_scanner_y` sets both axes explicitly** (not `enableAutoRange`) — hidden time-based items would pollute auto-bounds.
6. **Mode-10 X-lock is a deterministic per-frame mirror** (`lower.setXRange(*main.viewRange()[0])`), NOT `setXLink` (double-control + unreliable offscreen propagation).
7. **`mouseDragEvent` override must always `try/except`-fallback** to `_orig_drag` — native pan is sacrosanct.
8. **Live-edge bucket metrics** (poc/ER/vol_mult/end_time) are computed on the fly in `QuantBucket.live_snapshot()` — they aren't finalized until close.
9. **Test-harness limits:** offscreen `QT_QPA_PLATFORM=offscreen` gives an empty `sceneBoundingRect` until `resize()`, and `_on_mouse_move` early-returns outside it → mouse-position branches need a contained point. Background daemons don't reliably die on Windows — always free port 9999 first: `Get-NetTCPConnection -LocalPort 9999 -State Listen | Stop-Process -Id $_.OwningProcess -Force`.
10. **Persistence threading:** `HistoryStore` serializes the flush payload ON the asyncio loop (`prepare`) and does SQLite I/O in a thread executor (`_write`) — the executor must NEVER read the live engines/footprints (race). Closed buckets persist WITH `levels` (OB-band fidelity). `quant_engine.py` carries NO DB logic. `closed_buckets` has no natural key — append-only via the per-tf identity cursor. `pipe_client.snapshot()` is copy-on-write — consumers must keep treating `closed_buckets`/`order_blocks` read-only.

---

## 5. Next Immediate Steps (open items to harden)

1. **✅ Live order-block on index grid — CONFIRMED IN THE WILD (2026-06-14).** Against the live daemon, a real bullish OB (`vol_mult` 3.84, confirm 14:36 / end 14:37, mitigated) mapped correctly onto the Mode-10 index grid at ordinals **8→11** (bbox w=3) with its tier label — closing the synthetic-fixture-only gap in `§6.1`. _Residual nuance:_ this run caught a *mitigated* block (normal x0/x1 case). A longer session would also exercise the *active* branch (`x1=x_right` live-edge projection) and the confirm-before-window clamp (`x0=0`) on real data — both proven synthetically and sharing the same code path.
2. **✅ Mode-10 spot vs. baseline tag overlap — RESOLVED (2026-06-14).** Folded the EMA-baseline readout into the `t_spot` badge as a third gray line (`Price $X / (Y% Fill) / Base $Z`) and dropped the separate `t_bc_base` tracker, so the two `direction="mid"` badges can no longer overlap when close ≈ baseline. The gray dashed baseline *curve* still shows its position. Live-verified offscreen: `t_bc_base_tag`/`_ln` gone, merged badge carries all three readouts, no orphan/leak on teardown. **⚠ `terminal.py` source is now one fix ahead of `dist/OrderFlowTerminal` — run `python build.py --onedir` to resync the packaged exe.**
3. **Kinetic Mode-4 cloud "Base" baseline pen on white?** Now light-gray for the dark scanner theme — confirm it still reads if the dark-theme decision is ever reverted.
4. **`vpin` mode now reports 1 `InfiniteLine` (the 0.85 threshold) + 1 finite `PlotCurveItem` tracker rule** (post-overhaul; the tracker rule was an `InfiniteLine` before). If a future "tracker count" assertion is added, account for the finite-vs-span split — only Mode-10 `t_spot` is `span=True`.
5. **Index-space drawing persistence** is intentionally session-only; if users later want to keep Mode-10 annotations, design a separate anchor-relative store (do NOT reuse `drawings.json`).
6. **Position-bracket label vs. span handles:** `update_view` pins the data label to the view right edge (time chart only, called in the non-scanner `_on_timer` path). Verify the label doesn't fight the new vertical span handles during simultaneous drags.
7. **Full visual QA pass on a real monitor** — all offscreen tests pass, but the yellow spot, neon palette contrast, tracker badge placement, and dual-pane splitter ratios warrant a human eyeball.
8. **Exe smoke test depth.** Final exe verification confirms clean boot; a deeper compiled-mode interaction pass (drawing, mode switching in the packaged app) would close the loop.

---

## 6. Build / Run quick reference

```bash
pip install -r requirements.txt
python -m app.daemon        # terminal 1 (headless core)
python -m app.terminal      # terminal 2+ (GUI; Ctrl+N for more windows)
python build.py --onedir    # rebuild dist/OrderFlowDaemon + dist/OrderFlowTerminal
```
Verify a code change offscreen: free port 9999 → start `python -m app.daemon` → `QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 python -c "..."` driving `_on_timer()` in a loop.

---

## 7. Cloud Persistence Layer — SQLite `HistoryStore` + chunked CATCHUP (2026-06-14)

Built for the 24/7 GCP migration (daemon → `e2-standard-2`; terminal stays local over an SSH `-L 9999:127.0.0.1:9999` tunnel — no TLS/auth). Replaces the JSON footprint tick-replay with `data/history.db` (WAL).

- **Boot:** `HistoryStore.rehydrate_engines` loads finalized state straight from SQLite — `target_vol`, `rolling_velocity`/`vpin_queue` deques, the active bucket, and ALL closed buckets **including `levels`** (so `calc_quant_obs` keeps producing true Otsu bands, never the degraded `poc±0.05` fallback) — with **zero tick replay**. Verified live: 2,872 buckets re-armed across 5 engines, sub-second. The legacy JSON replay survives ONLY as a one-time migration (empty DB + `server_footprints.json` present → replay once → first sync seeds SQLite → every later boot instant).
- **Sync (async, replaced the 15s JSON flush):** `sync_loop` every `SYNC_INTERVAL_SECS` (10s) — **serialize on the event loop** (`prepare`) then **write in a thread executor** (`_write`, pure SQL, no shared-state access, never blocks the loop). `closed_buckets` append-only via a per-tf identity cursor (surrogate autoincrement `id`; `start_time` non-unique + `curr_vol==target_vol` on every closed bucket ⇒ no natural key). Pruned to `CLOSED_BUCKETS_CAP` (**10,000**)/tf + `FOOTPRINT_CAP`/tf each sync; in-memory footprints trimmed to `FOOTPRINT_MEM_CAP` (300, ≥2h for `recalibrate`).
- **Chunked CATCHUP (`protocol.py`):** `CATCHUP_START` (clear + target_vol + OB set + recent footprints + total) → N× `CATCHUP_CHUNK` (`CATCHUP_CHUNK_SIZE`=1000 buckets/frame; daemon `await`s between frames) → `CATCHUP_END` (live `active_bucket` + vpin). Legacy monolithic `CATCHUP` dataclass kept but no longer emitted. Verified live: tf=4h streamed 1,942 buckets as 2 chunks.
- **`pipe_client` no-freeze (two independent fixes):** (1) chunk ingest builds heavy structures OUTSIDE the lock, takes it only for a pointer-swap / `extend`; (2) **copy-on-write `snapshot()`** via per-field version counters (`closed_buckets`/`order_blocks`) — heavy lists re-copied only on change, not every frame. A 10k-bucket history then costs one copy per change, not 20/sec. `snapshot()` now also carries `catchup_loading`.
- **`quant_engine.py`: UNTOUCHED** (pure-math guardrail) — `persistence.py` (de)serializes via public attributes; ±inf high/low stored as `None` sentinels.
- **Deployment SHIPPED + live (2026-06-14):** `DEPLOYMENT.md` (Debian-12 from-source runbook), `deploy.ps1` (Windows push wrapper: stop daemon + WAL-checkpoint + scp), `requirements-daemon.txt` (headless deps — no PySide6/pyqtgraph). The `orderflow.service` systemd unit uses `KillSignal=SIGINT` so `systemctl stop` triggers the daemon's final flush. The terminal **auto-manages the SSH tunnel** (`SSHTunnelManager` in `terminal.py` `main()`: port-check → invisible `gcloud ssh -N -L` `Popen` → whole-tree `taskkill /T` on `aboutToQuit`/`finally`; only the launcher kills it, so multi-terminal is safe). See project memory `smc-cloud-deployment`.
