"""Tier-0 shared constants.

Single source of truth imported by BOTH the daemon and the terminal. Nothing
here imports asyncio, PySide6, or pandas — it must stay cheap so every module
can pull from it without dragging in heavy dependencies.

All numeric parameters are transcribed directly from the legacy `main.py` and
the master specification (draft_instructions.md). Where the spec and the legacy
code disagree, the divergence is noted inline.
"""

from __future__ import annotations

import bisect
import os
import sys

# ---------------------------------------------------------------------------
# Instrument / contract
# ---------------------------------------------------------------------------
SYMBOL = "SOLUSDT"
TICK_SIZE = 0.01          # spec §3.1.2 — fixed SOLUSDT tick
PRICE_DECIMALS = 2        # f"{price:.2f}" footprint keys (spec §10.2.2) + HUD (§6.2.1)

# ---------------------------------------------------------------------------
# Quant engine parameters (legacy main.py + spec §3)
# ---------------------------------------------------------------------------
DEFAULT_TARGET_VOL = 5000.0     # V_target default (main.py:42, spec §3.1.1)
BUCKET_MEDIAN_CANDLES = 1.0     # bucket-sizing knob: target_vol[1m] = this many MEDIAN 1m-candle
                                # volumes; higher tfs scale by candle-duration ratio. Median is
                                # burst-immune (1m vol is ~2x right-skewed, so the old mean-based
                                # optimizer chased bursts). 1.0 = "one bucket ~ one median 1m candle"
                                # (matches the old level -> no disruption); raise=coarser, lower=finer.
CLOSED_BUCKETS_CAP = 10000      # TERMINAL-side in-RAM scrollback cap (the client PC has RAM to spare)
DAEMON_BUCKETS_CAP = 4000       # DAEMON retention — buckets/tf kept in RAM + DB + catch-up. Split from the terminal
#                                 cap 2026-08-24 (e2-small RAM headroom): the engines' 10k full-footprint buckets/tf
#                                 were the largest RAM sink (~300-500MB) and made full catch-ups 2.5x heavier. Deep
#                                 history is safe elsewhere: GCS cold archive (6h cron; prune only deletes rows the
#                                 archive already holds) + the terminal's local pkl bucket caches.
RECALIB_WINDOW_SECS = 7200      # main.py:132 — 2-hour sliding recalibration frame
RECOMPUTE_SECS = 5              # 19.4 — periodic recalibrate + OB rescan cadence (off the per-close hot path)
VELOCITY_LOOKBACK = 20          # main.py:46 — rolling_velocity deque maxlen
ER_LOOKBACK = 20                # main.py:502 — effort/result baseline window
OB_MIN_BUCKETS = 20             # main.py:493 — calc_quant_obs needs >=20 buckets

# Otsu + calculus expansion (calculate_dynamic_band, main.py:392)
OTSU_ITERATIONS = 50            # 50-step between-class variance maximization
EXPANSION_MAX_TICKS = 100       # hard spatial expansion limit per direction

# VPIN — spec §3.4 mandates this; legacy main.py ships vel_ratio instead.
# DECISION: keep vel_ratio (OB engine depends on it) AND add VPIN alongside.
VPIN_WINDOW = 50                # N=50 normalized micro-buckets (spec §3.4.1)
# Adaptive VPIN tiering (app.vpin_adaptive) — replaces the dead fixed 0.85 'toxic' line.
# 'toxic'/'warn' are PERCENTILES of the recent VPIN distribution, so they self-calibrate to
# SOL's real range (the rolling-50 VPIN never exceeds ~0.57 live, so a fixed 0.85 never fired).
# Shared by every VPIN display site so 'toxic' means the same thing everywhere.
VPIN_ADAPT_WINDOW = 240         # rolling baseline: percentiles taken over the last N buckets
VPIN_WARN_PCTL = 75             # VPIN >= this pct of the recent window -> WARN (gold)
VPIN_TOXIC_PCTL = 90            # VPIN >= this pct -> TOXIC (crimson); ~top-decile by construction
VPIN_ADAPT_MIN = 30             # need >= this many samples before adaptive tiers engage (else NORMAL)

# ── Pattern accumulator (scripts/pattern_accumulator.py) — passive candidate logger ──
# Read-only periodic scan of history.db that BANKS candidate events (OB breaks + rare-pattern
# windows) with their characteristics + forward outcomes, so a real setup can be tested at honest
# sample size later (n>=200) instead of curve-fitting the handful of events in the current data.
ACCUM_TFS = ("1m", "5m")           # scales scanned (1x / 5x)
ACCUM_LIMIT = 6000                 # most-recent closed buckets/tf to scan per run
ACCUM_FWD_KS = (1, 3, 5)           # forward-return horizons (buckets) for OB-break outcomes
ACCUM_PATTERN_WINDOWS = (12, 20)   # selection sizes for the rare-pattern candidate scan
# loose rare-pattern cutoffs — GENEROUS recall (tightened at grounding time; over-logging is cheap)
ACCUM_HBA_CVD = -0.10              # HiddenBullAccum: cvd <= this, disp >= 0, opL = argmax
ACCUM_HBD_CVD = 0.10               # HiddenBearDist:  cvd >= this, disp <= 0, opS = argmax
ACCUM_WW_BOTH = 0.15               # WhaleWars: min(opL,opS)/vec >= this, |disp| <= 0.20, churn < 0.65

# ── Mode-10 selection E/R trajectory sparkline (descriptive — where buyer/seller E/R balance shifts) ──
SPARK_MIN = 5                      # min selected buckets to show the sparkline (fewer = noise, omit)
SPARK_WIDTH = 40                   # max chars; longer selections downsample to this many vol-weighted bins
SPARK_ZERO_BAND = 0.12             # |scaled balance| below this -> flat gray ▄ baseline (visible zero band)
FLIP_AMBIG_BAND = 0.33             # |net move| / range below this -> selection isn't cleanly directional
                                   # (balance-flip detector falls back to best crossing + ·AMBIG flag)
FLIP_SUSTAIN_MIN = 0.60            # TWO-SIDED: old side must have HELD >= this fraction BEFORE the cross
                                   # AND new side HOLD >= this AFTER, for a real 'held-then-lost' switch
                                   # (post-only let an edge graze with no prior control through = @+1 noise)
FLIP_MIN_REMAINDER = 4             # need >= this many buckets on EACH side of a crossing to confirm it
                                   # (a start- or end-of-selection graze can't be confirmed sustained)
FLIP_MESSY_CLARITY = 0.40          # crossing cleanness (min 1/N, local-persistence, separation) below
                                   # this -> '·messy' texture tag (choppy settle, e.g. absorption)

# ── Abnormal-velocity visual flag (descriptive — buckets far above their recent velocity baseline) ──
VEL_ABN_WINDOW = 30                # trailing buckets for the velocity baseline — MATCHES the stats-box
                                   # 30b BER/SER window (EXH_WINDOW): mean of vel over buckets[i-30:i]
VEL_ABN_RATIO = 5.0                # flag a bucket when velocity >= this x its trailing-30-mean (vel =
                                   # curr_vol/duration); operator-picked (~6% of buckets fire). NOT a signal
VEL_ABN_CAP = 10.0                 # ratio at which the marker hits FULL intensity; the fat tail (up to
                                   # ~76x) just maxes out (z=cutoff faint -> >=cap bright)

# ── E/R exhaustion candle border (descriptive) — neon 2px border when a side's E/R is strongly elevated ──
ER_BORDER_EXH_PCT = 50             # a side's E/R exhaustion-% (the stats-box [+N%] bracket = (mult-1)*100,
                                   # E/R z vs the trailing-30 window) >= this -> neon 2px border (green =
                                   # buyer elevated / red = seller). ~25% of candles at 50. NOT a signal.

# ---------------------------------------------------------------------------
# Phase 5 — OI pending-balance attributor (aggTrade; app.aggtrade.OiAttributor)
# ---------------------------------------------------------------------------
# OI is a 5s REST poll while aggTrades are sub-second, so a polled OI delta is bled
# across the trades that follow it under ONE global signed balance, clamped per-trade
# to +/-q (the Step-2 clamp). A scale-free cap K*Vw (Vw = EWMA volume-per-poll-
# interval) bounds that balance via cap-and-hold, hence bounds the attribution lag.
# All three are easy-to-tune knobs (revisit K once watching live).
OI_CAP_K = 3                    # cap = K * Vw  ->  attribution lag bounded to <= K OI-poll intervals
OI_VW_EWMA_N = 12               # EWMA memory (in poll-intervals) for the Vw volume baseline
OI_VW_FLOOR_FRAC = 0.1          # floor Vw at this fraction of the MEDIAN engine target_vol (scale-free,
                                # rule 0.6/1) so a dead-volume patch can't collapse the cap to 0

# ---------------------------------------------------------------------------
# Timeframes (30m added 2026-08-15 for the Radar Runner — see study/RADARRUN_30M_LIVE_PLAN.md)
# ---------------------------------------------------------------------------
TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h"]
DEFAULT_TF = "1h"   # cold-start timeframe. A saved terminal_ui.json "tf" overrides this (see _load_ui_state).

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
}

# pandas date_range freq aliases (main.py:388)
TF_PANDAS_FREQ = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
}

# ---------------------------------------------------------------------------
# IPC — raw TCP loopback (spec §1.3)
# ---------------------------------------------------------------------------
IPC_HOST = "127.0.0.1"
IPC_PORT = 9999
RECONNECT_SECS = 2              # client retry cadence (spec §1.3.1)
SOCKET_QUEUE_MAX = 256          # per-client outbound backlog before frame-drop

# ---------------------------------------------------------------------------
# Data / persistence (spec §9.1, §10.2.2)
# ---------------------------------------------------------------------------
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
# When frozen by PyInstaller, persist user data next to the executable rather
# than inside the temporary extraction dir (which is wiped on exit).
if getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(sys.executable)
else:
    PROJECT_DIR = os.path.dirname(ROOT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "data")
FOOTPRINTS_FILE = os.path.join(DATA_DIR, "server_footprints.json")  # legacy JSON (migration source)
HISTORY_DB = os.path.join(DATA_DIR, "history.db")  # SQLite state store (instant rehydration)

# --- Bookmap-style depth/trade capture (Phase 1) — a SEPARATE, ephemeral rolling store ----------------
# Lives in its own depth.db (own connection/sync/prune), fully decoupled from the durable bucket history.
DEPTH_DB = os.path.join(DATA_DIR, "depth.db")
DEPTH_CAPTURE_ENABLED = True    # master off-switch for the whole depth/trade capture subsystem
DEPTH_BAND_PCT = 0.0            # capture band as ±% of mid; <=0 = WHOLE BOOK (no truncation, real fidelity)
DEPTH_SNAPSHOT_SECS = 30        # full-book anchor cadence (+ one on every diff-stream reconnect)
DEPTH_SYNC_SECS = 10            # off-loop executor write cadence (drain buffers -> depth.db)
DEPTH_RETENTION_HOURS = 72      # HARD time-based prune (governs depth_deltas + trade_tape + snapshots alike).
                                # 2026-07-05: 6 -> 72 after the mem PLATEAU gate (725.6MB @84.5h < 734.7 @23.5h),
                                # so the Pull detector has real forward depth+tape history to test against.
                                # Disk: depth.db ~101MB@6h -> ~1.2GB@72h projected (linear); / has 5.1GB free.
DEPTH_BUFFER_CAP = 200000       # max buffered records per stream (drop-oldest) so a stalled write can't grow RAM
# Phase 2b — terminal heatmap render (the daemon serves raw sizes; these shape the request + local contrast)
HEATMAP_YBINS = 400             # price bins (Y resolution) requested per window
HEATMAP_BAND_PCT = 2.0          # price band shown = +- this % of mid (the visible Y range)
HEATMAP_RECENT_MINS = 45        # cold-open window = the last N minutes (fast first paint, ~0.4-0.7s)
HEATMAP_MAX_COLS = 1400         # cap on requested time-columns (≈ viewport pixel width)
HEATMAP_RENORM_SECS = 60        # re-sample the loaded grid's contrast this often (stable -> no flicker)
HEATMAP_LO_PCT = 98.9           # lower cutoff percentile: default VERY HIGH so only the strongest liquidity
#                                 shows first (a clean view); drag the slider down to reveal more.
HEATMAP_HI_PCT = 99.4           # upper cutoff percentile (sizes >= this -> max color; caps the $60-wall washout)
HEATMAP_BUBBLE_MIN_QTY = 0.0    # Phase 3: min aggregated-cell qty (SOL) to draw a trade bubble (0 = show all)
HEATMAP_BUBBLE_MIN_PX = 4       # smallest bubble diameter (px)
HEATMAP_BUBBLE_MAX_PX = 34      # largest bubble diameter (px)

FOOTPRINT_CAP = 10000           # main.py:291 — retention threshold per timeframe (on disk)
FOOTPRINT_MEM_CAP = 300         # per-tf footprint nodes kept in RAM (>=2h for recalibrate)
TIME_ENGINE_CAP = 800           # per-tf CLOCK-candle buckets kept in RAM by the clock engines (full-fidelity time chart)
# CLOCK-candle RECORDING depth (2026-08-30): the persistent footprint store per tf — decoupled from the small
# engine RAM ring above, because the store is what survives when NO terminal is connected. 3d/7d/14d/28d/45d/90d.
# Sizing: avg wire JSON 1m~1.5KB .. 1h~6.4KB (measured on the clock archive) -> full store ~40MB gz / ~150MB RAM.
TIME_STORE_CAP = {"1m": 4320, "5m": 2016, "15m": 1344, "30m": 1344, "1h": 1080, "4h": 1500}
#                 4h raised 540->1500 (250d) 2026-08-31: the VM store ALREADY held 834 4h candles (~139d);
#                 a 540 cap would have trimmed ~49 days of recorded history on the first fold.
TIME_SERVE_CAP = 2000           # newest clock candles shipped per get_time_candles serve (frame-size guard)
# FOOTPRINT BACKFILL from aggTrades REST (2026-08-31, app/aggtrade_backfill): heal stored clock candles that
# have OHLC but no footprint (the pre-recording-fix backlog + any future daemon-downtime gap). Newest-first,
# hard request budget per hourly pass -> converges over days without hammering (weight 20/req, ~1 req/s pace
# = ~1200 weight/min vs the 2400/min futures limit shared with klines/OI/depth).
TC_BACKFILL_ENABLED = True
TC_BACKFILL_REQ_PER_PASS = 600  # aggTrades requests per hourly pass (each <= 1000 trades)
TC_BACKFILL_PACE_S = 1.0        # sleep between requests

# ꕻ Big Bar (m10_bigbar): a Time candle is BIG when its size (high-low) is STRICTLY above this percentile of
# the candle sizes across the last 4 finished EMA-trend segments (rank-based -> outlier-immune; the first
# top-third-of-range rule printed ~1% of candles because one monster stretched the range). P90 ~= 33-41
# marks per 336-bar screen on 5m/15m/30m clock (measured 2026-08-31). Raise for fewer, lower for more.
BIGBAR_SIZE_PCTL = 90.0
# 5m CLOCK Radar Runner filter: only fire breakouts whose absorption-R at the breakout bar is >= this. OOS-validated
# on 5m time candles (study/radarrun_absorpR_band_oos.py + radarrun_15m_absorpR_prop.py): cuts maxDD 21%->6% and flips
# the 5m prop verdict marginal->PASS (99/95/89% @R0.5/0.75/1.0), keeping ~1/3 of signals (4.7 trd/day). Applied ONLY to
# 5m in TIME mode; bucket-scale RR and other timeframes are unchanged.
RR_ABSORPR_MIN = -0.25
# Radar Runner tradeable bracket + OPTIMAL (risk-based) position sizing. Validated on the HyroTrader 1-Step $200k
# prop eval (study/radarrun_hyro_two_rules.py + radarrun_hyro_manual_capture.py): risk a FLAT 0.4% of the account per
# trade, sizing the position so hitting the candle-capped SL loses exactly that (loss capped regardless of stop width).
# 100% pass under Hyro's 6%-max/3-4%-daily TRAILING limits. Margin = notional/leverage = (risk$/stop-dist)/lev.
# TP = 0.25% chosen for MANUAL trading: highest practical win rate (91.5%) + LOWEST drawdown (p99 3.1%), clears in
# ~1-1.5 months. (0.3% is faster ~26d but slightly higher DD/lower win; 0.2% wins 93.4% but too slow to catch by hand.)
RR_TP_FRAC = 0.0025             # fixed take-profit distance of the RR bracket (was 0.5%; 0.25% = best for manual trading)
# TWO-TARGET SCALE-OUT (badge click): 50% off at TP1, 50% at TP2 (GROSS price fractions; maker RT ~0.04% -> nets 0.2%/0.4%).
# Backtest (study/radarrun_scaleout.py, 30c+30bkt notional): stop->BE after TP1 = 100% pass, ~13d, DDp99 3.9% (best notional cfg).
RR_TP1_FRAC = 0.0024            # TP1 gross (nets ~0.20% after 0.04% maker RT) — take 50%
RR_TP2_FRAC = 0.0044            # TP2 gross (nets ~0.40%) — take the other 50%; move stop to BE after TP1
RR_MAKER_RT = 0.0004            # maker round-trip (0.02%+0.02%) used only to show NET on the TP labels
RR_ACCOUNT_BALANCE = 200000.0   # prop account size; risk$ = this * RR_RISK_FRAC. Bump once FUNDED to compound risk.
RR_RISK_FRAC = 0.004            # risk per trade = this * RR_ACCOUNT_BALANCE (0.4% = $800 on $200k) — the OPTIMAL flat risk
RR_LEVERAGE = 10.0              # exchange leverage; sets margin = notional/leverage. Does NOT change the $ risked.
REHYDRATE_LIMIT = 1440          # main.py:248 — last 24h of entries per tf (legacy replay)
SAVE_INTERVAL_SECS = 15         # main.py:286 — periodic footprint flush (legacy JSON)
SYNC_INTERVAL_SECS = 10         # async SQLite upsert cadence (replaces JSON flush)
CATCHUP_CHUNK_SIZE = 1000       # closed buckets per CATCHUP_CHUNK frame
BUCKET_CACHE_SAVE_SECS = 120    # worker persists its tf's base window (bucket_cache) at most this often
BASELINE_CANDLES = 100          # spec §9.1.3 — REST baseline pull
TS_FORMAT = "%Y-%m-%d %H:%M:00"  # spec §10.2.2 — temporal slicing key

# ---------------------------------------------------------------------------
# Binance endpoints (daemon-only)
# ---------------------------------------------------------------------------
REST_KLINES = "https://fapi.binance.com/fapi/v1/klines"
REST_OPEN_INTEREST = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={SYMBOL}"
REST_DEPTH = f"https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit=1000"
WS_KLINE = "wss://fstream.binance.com/market/stream?streams=solusdt@kline_{tf}"
WS_DEPTH = "wss://fstream.binance.com/ws/solusdt@depth"
WS_LIQUIDATIONS = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
WS_AGGTRADE = "wss://fstream.binance.com/market/ws/solusdt@aggTrade"   # Step 19.3 — order-by-order tape
DEPTH_HIST_URL = (
    "https://data.binance.vision/data/futures/um/daily/klines/"
    "SOLUSDT/{tf}/SOLUSDT-{tf}-{date}.zip"
)
OI_POLL_SECS = 5                # main.py:629
PULSE_BROADCAST_SECS = 0.4      # main.py:877 — DOM/OI pulse cadence
LIVE_EDGE_SECS = 0.15           # 19.3b — sub-second forming-bucket live-edge refresh (decoupled from trade rate)
DOM_LEVELS = 200                # main.py:881 — sorted depth levels per side

# ---------------------------------------------------------------------------
# GUI timing (spec §1.4.2, §9.2.3)
# ---------------------------------------------------------------------------
GUI_TIMER_MS = 50               # 20Hz master redraw loop
# Multi-window CPU relief: a chart window that is NOT the focused one repaints only every Nth frame (data threads keep
# running; only the paint throttles). With several charts open (e.g. 1m/30m bucket + 15m/30m clock) rendering them all
# at 20Hz saturates CPU. N=3 -> background windows paint ~6.7Hz (still smooth to glance at); raise for more relief, set
# 1 to disable. The FOCUSED window is always full 20Hz.
GUI_BG_FRAME_SKIP = 3
SESSION_PERF = True             # terminal-side session profiler: ~10s CSV row to data/session_perf.log
SESSION_PERF_SECS = 10.0        # profiler flush cadence (progressive-lag instrumentation; negligible overhead)
# tracemalloc LEAK HUNT -> data/session_memtrace.log. DIAGNOSTIC and DEFAULT OFF: with millions of live JSON objects
# in the worker, take_snapshot() on the GUI thread froze the terminal. Opt in ONLY for a short capture with SMC_MEMTRACE=1.
SESSION_MEMTRACE = os.environ.get("SMC_MEMTRACE", "0") != "0"
SESSION_MEMTRACE_SECS = 30.0    # tracemalloc snapshot cadence (leak deltas ~ this * leak-rate; 30s ≈ 13MB steps)
CHART_CACHE_CAP = 10000         # max candles per viewport (spec §1.1.2)
# Replay mode. On entering replay (or moving the Start Date) the chart loads the PER-TF minimum window ENDING at the
# replay cursor (terminal._replay_span_secs = -_default_scan_secs: 7d on 1h/4h, 5d on 15m, 3d on 5m, 24h on 1m) — the
# same days it would show live. REPLAY_MIN_BUCKETS floors the load when the window is unusually quiet so the
# pivot/VPIN/HM lookback stays valid; REPLAY_WINDOW caps it for perf. REPLAY_LOOKBACK_SECS = how far the cold-archive
# is asked to reach so a replay of OLD data still has context before the cursor (then trimmed to the per-tf span).
REPLAY_SPAN_SECS = 24 * 3600    # the 1m default; higher tfs override per-tf via terminal._replay_span_secs
REPLAY_MIN_BUCKETS = 300
REPLAY_WINDOW = 3600    # max bars kept in a replay frame — sized to hold the densest per-tf target (a BUSY 1m 24h
#                         runs ~3.5k buckets; higher tfs top out ~1.7k) so no per-tf window is silently clipped. Only
#                         1m ever approaches this; the fixed-left window GROWS as you step up to the cap, then the
#                         oldest bar slides off (a perf ceiling — heavier only when stepping far on 1m)
REPLAY_LOOKBACK_SECS = 2 * 24 * 3600
REPLAY_AUTOPLAY_MS = 250   # Ctrl+Right auto-play cadence: reveal one candle every this-many ms (stops on Left/Right)
# Cold-archive GCS bucket (must match study/pull_archive.ps1 $GCS and ops/archive_buckets.py GCS_DEFAULT). When the
# terminal scrolls/replays before the local mirror's coverage, it rsyncs missing chunks from here ON DEMAND and caches
# them under study/archive_data — so any date on GCS is reachable without a manual pull. ARCHIVE_FETCH_COOLDOWN_S
# throttles retries when a range genuinely isn't on GCS yet (never a tight loop).
ARCHIVE_GCS = "gs://smc-quant-archive/solusdt"
ARCHIVE_FETCH_COOLDOWN_S = 45.0

# ---------------------------------------------------------------------------
# Hamburger control ranges (spec §7)
# ---------------------------------------------------------------------------
MULT_FILTER_MIN = 0.0
MULT_FILTER_MAX = 10.0
MULT_FILTER_STEP = 0.1          # spec §7.2.3
CHART_FILTER_MIN = 100
CHART_FILTER_MAX = 25000
CHART_FILTER_STEP = 100         # spec §7.3.3 / §8.2
DOM_BIN_STEP = 0.01             # spec §8.1 — depth aggregation bin

# Analytics thresholds (spec §4)
FOOTPRINT_UNPACK_PX = 35        # vertical grid spacing to unpack side-by-side rows (§4.1.1)
IMBALANCE_RATIO = 2.0           # diagonal imbalance multiplier (§4.1.1)
FP_CANDLE_MIN_PX = 6.0           # 'W' footprint-candle mode: below this on-screen candle width (px) the view
#                                  falls back to normal candles (the per-level bars would smear thinner than a pixel)
FOOTPRINT_IMB_ER_MULT = 1.0      # Mode-10 footprint imbalance: a price level's buy (or sell) volume
                                 # >= this x the bucket's 30b buyer (or seller) E/R baseline (trailing-30
                                 # mean) -> imbalance. Cues: the footprint NUMBER inverts to black-on-neon
                                 # (green buy / red sell) + a candle-WIDTH horizontal line just below the
                                 # number (wick-thin; neon blue = buyer / neon orange = seller). NOT a signal.
# 4h abnormal-order overlay (the 'B' button next to V/Z): a price level in the 4h bucket's ladder is flagged when
# its buy (or sell) volume >= this x the 4h bucket's AVERAGE per-level buy (or sell) volume. Higher than the 1m mult
# because a 4h ladder spans many more levels, so only the genuine STANDOUT orders should light up.
FOOTPRINT_IMB_ER_MULT_4H = 3.0
IMBALANCE_OPACITY = (0.35, 0.95)  # min/max highlight opacity (§4.1.1)
STACKED_IMBALANCE_MIN = 3       # consecutive rows to form a channel (§4.1.2)

# ── Per-bucket BULL/BEAR absorption (VOLUME, descriptive — aggressive volume that didn't move price) ──
ABSORP_VOL_WINDOW = 50           # trailing buckets for the volume->displacement norm k = Σ|close-open| /
                                 # Σ curr_vol; suppression s = clamp(1 - (|disp|/vol)/k, 0, 1); GROSS,
                                 # directional: bull = sell_vol*s if sell-dominant, bear = buy_vol*s if
                                 # buy-dominant. NOT a signal — read the bull:bear ratio over a selection.
ABSORP_ZONE_MIN_RUN = 3          # absorption-zone band: a zone = >= this many CONSECUTIVE absorbing buckets
                                 # (directional AND suppression s >= the slider) on one side, at ANY slider
                                 # position. 90% of heavy buckets are isolated singles; N=3 keeps only
                                 # sustained runs (no single-bucket zones even at the loosest slider).
ABSORP_ZONE_FLOOR_S = 0.60       # yellow-dot 'validated-strength' floor on the zone slider: the validated
                                 # top-quartile-volume zones live at s >= ~0.6 (their p10). At/above = the
                                 # validated-strength regime; below = weaker-but-still-suppressed (a
                                 # gradient, not a real/fake cliff). A clean trend stays empty regardless.

# ── EFFECTIVE AGGRESSION (the validated MIRROR of absorption: heavy volume that DID move price its way) ──
EFF_AGG_FORCE_WINDOW = 50        # trailing buckets for the FORCE norm vol_norm = mean curr_vol; the slider
                                 # rides f = eff_agg / vol_norm — a self-calibrated RELATIVE force ratio
                                 # (~[0,1]; median ~0.60 on 1m). eff_agg reuses the SAME s as absorption.
EFF_AGG_ZONE_MIN_RUN = 3         # eff-agg zone = >= this many CONSECUTIVE forceful same-side buckets (mirror
                                 # of ABSORP_ZONE_MIN_RUN; no single-bucket zones at any slider position).
EFF_AGG_ZONE_DOT_F = 0.75        # 'forceful' dot on the eff-agg slider = ~p75 of directional force on real
                                 # 1m data: above = top-quartile DISTINCTIVELY forceful (heavy vol that
                                 # worked), below = ordinary directional volume (~what volume bars show).
                                 # Grounded, NOT a validated-strength cliff — eff-agg is common (the honest
                                 # difference from absorption's dot); forceful zones at the dot are rare.

# ── Mode-10 EXHAUSTION STRIP (SELECTION-scoped: bull/bear gated exhaustion, two lines in a bottom PANEL) ──
EXH_RELEASE = 0.60               # envelope decay for the SYMMETRIC smoother (max of a forward + a backward
                                 # envelope) that turns the spiky gated score into two readable lines.
                                 # Symmetric (non-causal) so they CROSS at the true balance shift, not at a
                                 # point dragged right by a causal decay-tail. Fine — a selection is a
                                 # descriptive look at a KNOWN region.
EXH_MEASURE = "gated"            # which measure feeds the lines: "gated" (TRUE worn-out: effort hot AND OI
                                 # draining; sparse/honest) or "raw" (effort-z normalized; smooth weave but
                                 # mislabels a winning side as exhausted). The operator accepted GATED.
EXH_SEL_MIN_WINDOW = 2           # FRESH-from-selection baseline: a bucket needs >= this many PRIOR selected
                                 # buckets to compute its z (else neutral). 2 = compute from the 2nd bucket
                                 # on (thin/noisy early, accepted); the baseline is the selection ONLY (no
                                 # reach-back before it), expanding as you go right.
EXH_STRIP_FRAC = 0.25            # panel height as a fraction of the selection's price range (y1 - y0).
EXH_STRIP_GAP = 0.05             # the panel's 100% line sits this fraction of the selection height BELOW the
                                 # box bottom — the panel is OUTSIDE the selection (under it), so it never
                                 # covers the candles. Raise to drop it further below; needs room beneath the
                                 # selection (draw the box with space below, or it falls off the bottom).
EXH_CROSS_PERSIST = 2            # mark a crossover (gold diamond) only if the new dominant side holds >= this
                                 # many buckets (so a choppy selection doesn't litter diamonds).

# ── Mode-10 EFF-AGG EVOLUTION STRIP (SELECTION-scoped: per-bucket eff-agg, NEON green bull / red bear, two
#    lines in a SECOND panel STACKED below the exhaustion strip; '2' toggles) ──
EFF_STRIP_FRAC = 0.25            # panel height as a fraction of the selection's price range (y1 - y0).
EFF_STRIP_GAP = 0.05             # gap below the previous SHOWN panel's 0% line — only VISIBLE panels take a
                                 # slot, so hiding one ('1'-'4') slides the ones below it up (no blank gap).
EFF_STRIP_RELEASE = 0.60         # envelope decay for the SYMMETRIC smoother that lifts the one-sided per-bucket
                                 # eff-agg spikes into two readable bands (matches the exhaustion strip's look).

# ── Mode-10 EFFORT/RESULT EVOLUTION STRIP (SELECTION-scoped: buyer vs seller E/R, green/red, two lines in a
#    THIRD panel STACKED below the eff-agg strip; '3' toggles. Promoted out of the FLOW TRAJECTORY sparkline) ──
ER_STRIP_FRAC = 0.25            # panel height as a fraction of the selection's price range (y1 - y0).
ER_STRIP_GAP = 0.05             # this panel's 100% line sits this fraction of the selection height BELOW the
                                 # eff-agg strip's 0% line — the three panels stack, each in a fixed slot.
ER_STRIP_RELEASE = 0.60         # envelope decay for the SYMMETRIC smoother (E/R is two-sided, so it's already
                                 # smoother than eff-agg; this matches the other two panels' look).

# ── Mode-10 ABSORPTION STRIP (SELECTION-scoped: per-bucket bull/bear absorption — volume that FAILED to move
#    price — green/red, two lines in the FIRST/TOP panel; '1' toggles) ──
ABS_STRIP_FRAC = 0.25           # panel height as a fraction of the selection's price range (y1 - y0).
ABS_STRIP_GAP = 0.05            # this panel's 100% line sits this fraction of the selection height BELOW the
                                 # box bottom — it's the top of the stack (1 abs, 2 eff, 3 er, 4 exh).
ABS_STRIP_RELEASE = 0.60        # envelope decay for the SYMMETRIC smoother (matches the other panels' look).

# ── Lean panels (absorption '1' / eff-agg '2' / E/R '3') — the two lines are each side's SHARE of the pair,
#    crossing at the 50% midline. ROLLING (not cumulative): the share is taken over a CENTERED window so the
#    lines track the LOCAL lean and shift across the selection (cumulative would converge to a flat line) ──
LEAN_WINDOW_FRAC = 0.25         # (legacy selection-relative mode) rolling-share window = this fraction of …
LEAN_WINDOW_MIN = 5            # … the selection's bucket count, floored here. Replaced by LIVE_PANEL_WINDOW.
LIVE_PANEL_WINDOW = 15         # FIXED trailing window for the lean panels (1/2/3 share + 4 exhaustion baseline)
                              # — selection-INDEPENDENT: every bar reads the same no matter where you draw the
                              # selection start (panels become a stable live read, not a moving ruler). Tunable.
LIQ_WAVE_WINDOW = 10          # fixed trailing window for the Liquidation Pressure panel's net-liq rolling sum
                              # (the "wave"): 10 = responsive (surf a building cascade), larger = smoother.
SCORE_SMOOTH_W = 1            # SCORE panel gap-line smoothing: CENTERED rolling mean over this many dots
                              # (odd; 1 = OFF — operator pref 2026-07-03: raw per-bucket line). DISPLAY-ONLY —
                              # per-bucket scores, hover, and the forward log are raw regardless.
PANEL9_SCALE = 100            # Panel-9 bull/bear lines: value (in points) mapped to the band edge; the gold
                              # dashed reference lines sit at +/-50 on this scale (0 = center).
ER_LEAN_GAIN = 3.0             # E/R hugs 50% (two-sided), so its panel ZOOMS the deviation from the midline by
                                 # this factor (display only — the hover still shows the true share). 1.0 = none.
# Keltner Channel overlay on the bucket-candle chart: EMA(close, LENGTH) basis ± ATR_MULT · ATR(LENGTH).
KELTNER_LENGTH = 20            # EMA basis + ATR period (in buckets)
KELTNER_ATR_MULT = 2.25       # band half-width = this × ATR
# Smooth-approx "effective timeframe" scale for the 1m KC + POC baseline (hamburger slider under Depth Wall).
# 1.0 = native (byte-identical to today); higher stretches the EMA+ATR period ×scale and widens the band
# ×sqrt(scale) so the 1m channel APPROXIMATES a higher-TF one (≈5m at 5×, ≈15m at 15×, ≈1h at 60×, ≈4h at 240×)
# WITHOUT re-aggregating — the POC baseline EMA period scales ×scale too, so its center line adapts in step.
KELTNER_SCALE_DEFAULT = 1.0
KELTNER_SCALE_MAX = 240.0     # slider ceiling (240× on a 1m base ≈ the 4h channel)
KELTNER_BASELINE_PERIOD = 39  # native POC-baseline EMA period (α=0.05 → 2/(39+1)); scaled ×scale for the slider

# ── Mode-10 PHASE TABLE (live, beside the panels): classify the selection as before/start/during/end of a
#    move. Per phase, the MEAN and STD of the signed with-move spread (% pts, + favors the move) for
#    absorption / E/R / eff-agg, profiled over R=30% retracement moves. The table's CONFIDENCE is computed
#    LIVE — a naive-Bayes posterior P(phase | the selection's CURRENT spreads), normalized across the 4
#    phases — so it shifts as price moves. (eff-agg's tight std dominates; absorption's wide std makes it
#    near-irrelevant — by design.) DESCRIPTIVE. Row: (NAME, (abs_mean,abs_std), (er_mean,er_std), (eff_mean,eff_std)).
PHASE_STATS = {
    'up': [
        ('BEFORE', (12.3, 76.2), (-7.5, 29.6), (-24.6, 61.7)),
        ('START',  (11.8, 75.6), (4.2, 26.2),  (22.8, 52.3)),
        ('DURING', (-33.4, 66.6), (20.7, 30.5), (57.8, 48.0)),
        ('END',    (-18.2, 71.0), (27.3, 29.1), (71.9, 36.0)),
    ],
    'down': [
        ('BEFORE', (19.0, 75.7), (-16.1, 29.8), (-35.8, 55.4)),
        ('START',  (-2.3, 74.0), (8.2, 31.3),  (21.8, 59.4)),
        ('DURING', (-45.6, 71.9), (30.5, 30.8), (68.4, 46.1)),
        ('END',    (-44.2, 60.2), (27.8, 27.4), (75.2, 33.4)),
    ],
}
# Phase-table/panel OPACITY = the live CONFIDENCE (posterior%, sums to 100) smoothed by an EMA:
# op = λ·op + (1-λ)·(posterior% of that phase). The EMA is WARMED through the _lw (~15) buckets just before the
# selection (terminal._refresh_selection_stats pre-roll) so the left edge is settled instead of cold-starting;
# only that warm-up reaches outside [lo,hi] — the displayed trajectory/table/panels are all the [lo,hi] portion.
# λ = responsiveness: low → snaps to the current lean, high → smoother memory; ~0.8 ≈ a ~5-bucket glide.
# Stays conserved at 100% (convex blend of vectors that each sum to 100).
PHASE_EMA_LAMBDA = 0.8
# Mode-10 PHASE PANELS ('5' BEFORE / '6' START/DURING / '7' END) — one per merged phase, two lines = that
# phase's live confidence for UP (green) / DOWN (red) across the selection (the table's rows as lines).
PHASE_PANEL_FRAC = 0.25        # panel height as a fraction of the selection's price range (same as the others).
PHASE_PANEL_GAP = 0.05         # gap below the previous shown panel (they stack under 1-4).
ICEBERG_VOL_SHARE = 0.04        # 4% candle volume (§4.2.1)
ICEBERG_SKEW = 0.65             # 65% absorption skew (§4.2.1)
VELOCITY_NEON_RATIO = 2.5       # HFT neon overload trigger (index.html:945, spec §10.2.3)
VELOCITY_TIER_HIGHLIGHT = 1.2   # OB ignition velocity gate (main.py:508)

# ---------------------------------------------------------------------------
# Trade-size classification (LARGE / SMALL market orders)
# ---------------------------------------------------------------------------
# FIXED log-spaced trade-size bins (CONTRACTS), ~4 per decade, spanning dust -> whale. Grounded in the
# measured 60-min SOL aggTrade qty distribution (median ~1.9, p90 ~90, p95 ~273, p99 ~896, p99.5 ~1400,
# max ~5000): the slider's meaningful range p40(0.75)->p99.5(1400) sits in the well-resolved middle, every
# default rung falls mid-spread. STORE-RAW: each closed bucket carries its per-side count+vol histogram over
# these bins (sz_cb/sz_cs/sz_vb/sz_vs); the terminal thresholds them LIVE (slider) with log-linear within-bin
# interpolation, so the cutoff is retroactive + instant — never baked into stored counts. Edges are static
# (never shipped per-bucket); both daemon (binning) and terminal (thresholding) import them. Implicit open
# underflow [0, edges[0]) = bin 0 and overflow [edges[-1], inf) = bin len(edges).
SIZE_HIST_EDGES = (
    0.1, 0.178, 0.316, 0.562, 1.0, 1.78, 3.16, 5.62, 10.0, 17.8,
    31.6, 56.2, 100.0, 178.0, 316.0, 562.0, 1000.0, 1780.0, 3160.0, 5620.0,
)
SIZE_HIST_NBINS = len(SIZE_HIST_EDGES) + 1     # 21 bins (19 interior + underflow + overflow)

# Daemon rolling 60-min trade-size percentile (the AUTO-DEFAULT slider position; shipped on the pulse as
# PulsePacket.size_thr = [p50,p90,p95,p99,p99.5] in contracts). Cheap: prune + sort ~7k floats every few
# seconds on the pulse loop (sub-ms). Cold-start guard: ship [] until the window has enough samples (~4 min
# at SOL's ~2 trades/s) so the terminal never thresholds against a half-warm distribution.
SIZE_PCTILE_WINDOW_MS = 3_600_000   # 60-min rolling window
SIZE_THR_MIN_SAMPLES = 500          # cold-start: below this, ship size_thr=[] (not warm yet)
SIZE_THR_RECOMPUTE_SECS = 3.0       # recompute cadence (distribution drifts slowly; pulse is 0.4s)
# Terminal cold-start fallbacks for the LARGE/SMALL panel + bubble cutoffs, used only until the daemon's
# size_thr warms (the measured 60-min p95 / p50 in contracts). Once size_thr arrives it supersedes these.
SIZE_DEFAULT_LARGE = 273.0          # large cutoff (contracts) ~ p95
SIZE_DEFAULT_SMALL = 1.9            # small cutoff (contracts) ~ p50

# ---------------------------------------------------------------------------
# Zone planner (ZONE_PLANNER_SPEC) — forward-excursion / first-passage engine.
# Constants are added per commit as each concern lands; commit 1 needs only the horizon.
# ---------------------------------------------------------------------------
H_DEFAULT = 20                      # forward horizon in BUCKETS (a volume clock, NOT seconds) — spec §S1/§13
# commit 2 — cohort matcher (NEW-detector params: set once by definition, FROZEN, never tuned vs a label)
KNN_K = 200                         # kNN cohort seed size (nearest by scaled-L2 over the feature vector)
MATCH_RADIUS_MULT = 2.0             # cohort = seed members within this × the adaptive radius (prunes far tail)
MATCH_RADIUS_FLOOR = 0.25           # z-units — floors the adaptive radius so identical pools never -> 0/NaN
COHORT_MIN_NONZERO = 5              # need >= this many NONZERO seed distances, else InsufficientSample
MIN_EFF_N = 8                       # gate: below this effN, propose_zones -> InsufficientSample (draw only a note)
# commit 3 — first-passage evaluator
STOP_EXEC = "touch"                 # stop order executes on TOUCH (default; level is close-anchored in commit 5). {touch, close}
AMB_WARN = 0.70                     # same-bucket ambiguous fraction above this -> warn + drop confidence (addendum A2)
# commit 4 — zone_planner + ENTRY box (NEW params: set once by first-principle, FROZEN, never tuned vs a label)
DEFAULT_SIZE = 1.0                  # position size (contracts) — slippage's sole consumer (commit 8); v1 placeholder (A5.2)
ENTRY_PULLBACK_LOOKBACK = 5         # buckets — pullback-depth window for the statistical fill band (§5, §13 ~5)
ENTRY_DEPTH_Q_LO = 0.40            # fill-band SHALLOW edge = P0 -/+ q40(pullback depth) (§5)
ENTRY_DEPTH_Q_HI = 0.60            # fill-band DEEP    edge = P0 -/+ q60(pullback depth) (§5)
SNAP_TOL_TICKS = 15                # ticks ($0.15 SOL) — magnet-snap / struct∩stat reconcile tolerance (§5, §7, §13)
THICK_DISP_MULT = 1.0             # thickness floor stat term = THICK_DISP_MULT * effort_ticks*TICK (=1 std of intrabar price) (§5)
WINNER_SHIFT_TOL = 0.05           # two-pass converges when |Δ winner set| / |winners| < this (§4)
MAX_REFINE = 6                     # winners->stop refinement cap. §4 estimated 2, but on 1m the map is a
                                   # CONTRACTING fixed point (winner selection tightens q85(D|winners)
                                   # incrementally) that settles in 3-5, not 2 — measured, monotone, no
                                   # limit cycle. 6 = observed-max(5) + headroom; loop also breaks on cycle.
STAT_ONLY_CONF = 0.6             # box confidence x this when it has NO structural anchor (§5 "no structural entry anchor")
G_AGREE_LAMBDA = 1.0             # g_agree = exp(-|struct-stat| / (LAMBDA * bandwidth)) — struct∩stat disagreement haircut (§10)
N0_EFFN = 40                       # g_effn = eff_n/(eff_n+N0_EFFN); eff_n = greedy disjoint count (R1). Conservatism
                                   # lives HERE, not in deflating the count. Calibrate against the 11b coverage pass.
# quantile levels — used by the commit-4 two-pass (provisional stop/TP1); STOP box (5) + TP boxes (6) reuse them
STOP_HEAT_Q = 0.85                # tight stop edge = f -/+ q85(winner MAE) — clears 85% of winner heat (§6, §13)
TP_QUANTILES = (0.50, 0.75, 0.90)  # TP1/2/3 levels = f +/- q(favourable excursion | filled) (§7, §13, A5.4)
# commit 5 — STOP box
WICK_BUFFER_Q = 0.75              # wide edge buffer = q75(overshoot | cohort members that HELD) — touch-honest (§6, §13)
STOP_WIDE_Q = 0.95               # statistical wide-edge fallback (no structural invalidation) = q95(winner MAE)
STOP_GRID_N = 13                 # stop-search resolution over [wide, tight] for the argmax-E[R] recommended line (§6/§9)
# commit 6 — TP boxes
TP_BW_DQ = 0.05                  # TP box thickness = local favourable-excursion width across [q-DQ, q+DQ] (§7)
# commit 7 — scale-out optimiser
W_MIN = 0.20                    # min scale-out weight per TP so a scalp always de-risks at TP1 (§9, §13)
WEIGHT_STEP = 0.05              # coarse simplex grid step for weight optimisation (K<=3 -> <=2 free weights) (§9)
BOOTSTRAP_N = 1000             # block-bootstrap resamples for the E[R] CI (block length ~ H buckets) — the
                                # LOAD-BEARING 1m uncertainty band (statistical, not path-ordering; addendum)
# descriptive excursion readout (recovered deliverable, NOT the shelved prescriptive layer)
EXC_THIN_EFFN = 30             # eff_n below this -> "THIN — tails unstable" caption (render but distrust the tails)
EXC_P95_MIN_EFFN = 20         # eff_n below this -> SUPPRESS p95 (it's 1-2 points = just the cohort max, misleads)


def size_bin(qty: float) -> int:
    """Index 0..len(SIZE_HIST_EDGES) of the log-spaced size bin holding ``qty`` (contracts).

    ``bisect_right`` so a qty exactly on an edge lands in the upper bin; qty < edges[0] -> 0 (underflow),
    qty >= edges[-1] -> len(edges) (overflow). O(log NBINS), called once per aggTrade on the daemon.
    """
    return bisect.bisect_right(SIZE_HIST_EDGES, qty)

# ---------------------------------------------------------------------------
# Color palette — pure light mode (spec §5.1)
# ---------------------------------------------------------------------------
COLOR_CANVAS = "#ffffff"
COLOR_GRID = "#eeeeee"
COLOR_AXIS_TEXT = "#000000"
COLOR_CROSSHAIR = "#000000"

# B&W candlesticks (§5.1.2)
COLOR_BULL_BODY = "#ffffff"
COLOR_BEAR_BODY = "#000000"
COLOR_CANDLE_BORDER = "#000000"

# Imbalances (§4.1.1)
COLOR_IMB_BUY = (57, 255, 20)    # neon green
COLOR_IMB_SELL = (255, 7, 58)    # neon red
RGBA_CHANNEL_BUY = (57, 255, 20, 0.15)
RGBA_CHANNEL_SELL = (255, 7, 58, 0.15)

# Icebergs (§4.2.2)
COLOR_ICEBERG_BUY = "#00ffff"    # cyan — absorbing sellers
COLOR_ICEBERG_SELL = "#ff00ff"   # magenta — absorbing buyers

# COB depth (§8.1 / §10.2.3)
RGBA_COB_ASK = (248, 81, 73, 0.4)
RGBA_COB_BID = (46, 160, 67, 0.4)

# Liquidation marks (§7.3 line "Liquidation Marks")
COLOR_LIQ_SHORT = "#00ffff"      # cyan — shorts liquidated (forced buys)
COLOR_LIQ_LONG = "#ff00ff"       # magenta — longs liquidated (forced sells)

# Alert ledger feeds (§8.4)
COLOR_ALERT_BULL_OB = "#27ae60"
COLOR_ALERT_BEAR_OB = "#e74c3c"
COLOR_ALERT_SHORT_LIQ = "#00ffff"
COLOR_ALERT_LONG_LIQ = "#ff00ff"
COLOR_BADGE = "#e74c3c"

# Bucket velocity visuals (getBucketVisuals, index.html:933)
RGB_GREEN_STD = (46, 204, 113)
RGB_GREEN_NEON = (0, 255, 255)
RGB_RED_STD = (231, 76, 60)
RGB_RED_NEON = (255, 0, 255)
RGB_BLUE_STD = (52, 152, 219)
RGB_PURPLE_STD = (155, 89, 182)
BUCKET_ALPHA_FLOOR = 0.15        # minimum opacity so it never vanishes


def ensure_data_dir() -> str:
    """Create the data directory if missing and return its path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR
