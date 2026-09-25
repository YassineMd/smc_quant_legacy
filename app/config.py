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
# THE AUCTION SNAPSHOT (layer 1 of the user's auction reading, 2026-09-24): what a Claude conversation reads -- the
# Claude app through its connector (android/auction_mcp.py, on the VM) and /auction-read (Claude Code). Today's and the
# multi-day value, the recent summary, the last cycles in the auction's words. A FIXED path under the project (the
# tablet engine runs on a temp DATA_DIR): on the VM /home/yassine_mdouari/smcflow/data/, here data/. The reading
# instructions that go with it: app/auction_read_prompt.md (ONE copy, read by the connector and by the skill).
AUCTION_SNAPSHOT_PATH = os.path.join(PROJECT_DIR, "data", "auction_snapshot.json")
AUCTION_SNAPSHOT_SECS = 20.0        # written at most this often
AUCTION_SNAPSHOT_CYCLES = 80        # the builder's default: the newest this many cycles
AUCTION_BP_MAX_PER_CYCLE = 20       # Big Player events listed per cycle (largest first; the count says if more)
AUCTION_FILE_CYCLES = 400           # ... and in the FILE the Claude connector reads (the whole feed window, capped)
AUCTION_ACTIVE_MIN = 1.0            # a side is ACTIVE at or above its own normal aggressive $/s
AUCTION_SUMMARY_N = 12              # the recent stretch the summary counts over (finished rated cycles)
# THE CYCLE HISTORY (user 2026-09-24: Claude should reach "anything before the engine's ~6 h window"): every SETTLED
# snapshot row is appended once to <dir>/YYYY-MM-DD.jsonl (UTC day of the cycle's start) and kept this many days.
# Settled = finished at least this long ago, so its wall columns (fetched 15 s after, final by 45 s) have landed.
AUCTION_HISTORY_DIR = os.path.join(PROJECT_DIR, "data", "auction_history")
AUCTION_HISTORY_DAYS = 30
AUCTION_HISTORY_SETTLE_SECS = 120.0
# ... an UNRATED row (no I x I reading: its wall or baseline is missing) waits this long in case it gets rated; and
# nothing is recorded until this long after the first snapshot, the wall grid covers the window and the HLH klines
# are in: MEASURED 2026-09-24, a restart's first writes were all unrated for ~4 min (the grid catching up)
AUCTION_HISTORY_UNRATED_SECS = 900.0
AUCTION_HISTORY_WARMUP_SECS = 300.0
FOOTPRINTS_FILE = os.path.join(DATA_DIR, "server_footprints.json")  # legacy JSON (migration source)
HISTORY_DB = os.path.join(DATA_DIR, "history.db")  # SQLite state store (instant rehydration)

# --- Bookmap-style depth/trade capture (Phase 1) — a SEPARATE, ephemeral rolling store ----------------
# Lives in its own depth.db (own connection/sync/prune), fully decoupled from the durable bucket history.
DEPTH_DB = os.path.join(DATA_DIR, "depth.db")
DEPTH_CAPTURE_ENABLED = True    # master off-switch for the whole depth/trade capture subsystem
DEPTH_BAND_PCT = 0.0            # capture band as ±% of mid; <=0 = WHOLE BOOK (no truncation, real fidelity)
DEPTH_SNAPSHOT_SECS = 30        # full-book anchor cadence (+ one on every diff-stream reconnect)
DEPTH_SYNC_SECS = 10            # off-loop executor write cadence (drain buffers -> depth.db)
DEPTH_VACUUM_SECS = 300.0       # how often the pruned pages are handed back (see DEPTH_VACUUM_PAGES)
DEPTH_VACUUM_PAGES = 2000       # pages per pass (~8 MB). Reclaiming the whole freelist in one call is a
                                # multi-second stall -- the very thing this exists to prevent.
DEPTH_RETENTION_HOURS = 72      # HARD time-based prune (governs depth_deltas + trade_tape + snapshots alike).
                                # 2026-07-05: 6 -> 72 after the mem PLATEAU gate (725.6MB @84.5h < 734.7 @23.5h),
                                # so the Pull detector has real forward depth+tape history to test against.
                                # Disk: depth.db ~101MB@6h -> ~1.2GB@72h projected (linear); / has 5.1GB free.
DEPTH_BUFFER_CAP = 200000       # max buffered records per stream (drop-oldest) so a stalled write can't grow RAM
# --- 'flow' scanner mode (user 2026-09-08): the tablet's Trades gauge as a chart -- taker buy $ / sell $ over time.
# Trades are accumulated ONCE into FLOW_BIN_SECS bins (app/flow_pane.FlowStore); every frame reads a rolling sum over
# those bins, so the per-frame cost is bounded by the DRAWN range, never by the tape.
FLOW_BIN_SECS = 1.0             # accumulation bin (the x resolution of the two lines)
FLOW_RETAIN_SECS = 259200       # 72 h of bins kept in RAM (2 x 259200 float64 = 4.1 MB) == the daemon's tape
#                                 badges read the same bins on the candle canvas, so they want a day of history
FLOW_WINDOW_SECS = 60           # default rolling window = the tablet gauge's 60 s
FLOW_WINDOW_CHOICES = (10, 30, 60, 300)
FLOW_BACKFILL_SECS = 3600       # history requested on entry (one trades_window, same shape as the Trades tape's)
# --- Cycle-start lines ON the flow chart (user 2026-09-10). The two lines CROSSING is the cycle boundary --
# read off the window above, the one the lines are actually drawn with. The cross only counts once the spread
# has REACHED MIN_SPREAD_PCT and HELD it for MIN_HOLD_SECS; the line is then drawn back at the exact cross.
FLOW_CROSS_ON = True
FLOW_CROSS_MIN_SPREAD_PCT = 10.0    # |buy-sell| / (buy+sell) of the SAME rolling window the lines are drawn with
FLOW_CROSS_MIN_HOLD_SECS = 20.0     # consecutive seconds it must stay there, after the cross
FLOW_CROSS_MAX = 400                # newest N kept on screen (a hard ceiling; see FLOW_CROSS_MIN_PX)
FLOW_CROSS_CONTEXT_SECS = 600.0     # tape read on EITHER side of the view. Backwards: two consecutive
                                    # same-colour lines are ONE cycle, so the leftmost line on screen has to know
                                    # about the one before it or a pan alone would make it appear. Forwards: the
                                    # last visible cycle needs its real END to measure its price move over.
FLOW_CROSS_MIN_PX = 7               # ... and no two DRAWN lines closer than this many pixels. Zoomed out to
                                    # 20 h the cap alone put a line every 4 px -- unreadable, and 140 ms/paint.
FLOW_CROSS_WIDTH = 1.2              # pen width, px (user 2026-09-10: 2.4 read as "tooo thick")
# Badge tiers: (minimum px between badges, pills per badge, rows to stagger over). The first tier that loses
# NO badge wins, so they degrade instead of vanishing when the user zooms out. Rows x pills = bands of strip.
# (minimum px between badges, rows to stagger over). The first tier that loses NO badge wins, so a badge row
# degrades instead of vanishing when the user zooms out; rows = bands of strip it needs.
# One ladder PER ROW, because each pane's pill holds different text and so crowds at a different width.
FLOW_TICK_BADGE_TIERS = ((30.0, 1), (16.0, 2))          # "+9t",         on the flow chart under zero
CYCLE_RATE_BADGE_TIERS = ((74.0, 1), (38.0, 2))         # "S -2.5/100k", on the Cycle pane
CVOL_BADGE_TIERS = ((40.0, 1), (22.0, 2))               # "1.7x",        on the Volume pane
LOB_BADGE_TIERS = ((64.0, 1), (34.0, 2))                # "1.02/0.97",   on the Book pane
# The badge rate is ticks of PRICE movement per this many dollars the studied side traded. 100k, not 1M: a
# per-million rate on a cycle that traded $164k printed "+55/M" next to a +9t move and read as nonsense.
FLOW_CROSS_BADGE_UNIT_USD = 100_000.0
FLOW_CROSS_BADGE_UNIT_TXT = "100k"
FLOW_CROSS_BADGE_BAND_PX = 16       # one pill plus its gap, in pixels; the strip is bands x this
# Cycle pane: ticks price moved per this many dollars each side has traded so far in the cycle. Same unit as
# the badge, on the same cycles -- both come from FlowStore.crosses().
CYCLE_RATE_MIN_USD = 20_000.0       # a cycle whose dominant side traded less than this gets no bar: dividing
                                    # a tick by a few thousand dollars is a spike, not a reading

# --- Cycle VOLUME pane: is this cycle's dominant-side volume low / normal / high for that side? ---------------
CVOL_PANE_ON = True
CVOL_BASE_N = 5                     # compared against the MEDIAN of that side's previous N cycles. Median, not
CVOL_MIN_N = 3                      # mean: at N=5 one outsized cycle would drag a mean around completely.
# Cuts measured on 20 h of live tape (n=676) -- the TERCILES of the ratio, so each band really is a third of
# cycles. A "sensible" 0.70-1.40 band would have called only 17.6% of them normal.
CVOL_LOW = 0.55
CVOL_HIGH = 1.90
CVOL_PER_SECOND = False             # ⚠ 52% of the ratio's variance is shared with how LONG the cycle ran, and
                                    # that is already the bar's WIDTH. True divides by duration ($/s) to take
                                    # it back out -- a different question, so it is the user's call.
CVOL_MIN_USD = 20_000.0             # below this the cycle has nothing to rate
CVOL_LOOKBACK_SECS = 3600.0         # how far BEFORE the view the baseline reaches: five same-side cycles is
                                    # further back than the drawn range, and a bar must not change on a pan
CVOL_LOW_COL = "#4d84c4"            # quiet / normal / heavy. Deliberately NOT green-red: this pane is about
CVOL_MID_COL = "#7a828e"            # how MUCH traded, and green/red already mean buy/sell everywhere else.
CVOL_HIGH_COL = "#d9a520"

# --- Book pane: is the RESTING book thick or thin this cycle, per side, vs the last N cycles? -----------------
# Measured on 8 h of live tape (n=237) at the +-100 radius before any cut was chosen:
#   bid p10 0.93  p33 0.98  median 1.00  p67 1.03  p90 1.08  p99 1.17   (ask within 0.01 of that)
# ⚠⚠ a 0.80-1.25 band calls 100% of cycles NORMAL and 0.90-1.11 calls 91%: the book at +-100 ticks barely moves
# over 80 seconds. The cuts below are the measured TERCILES, so "high" really does mean about +3%.
# ⚠ they are RADIUS-DEPENDENT: terciles are 0.95/1.06 at +-10 ticks and 0.99/1.01 at +-200. The pane follows the
# liquidity pane's radius, so pick +-10 there if you want this pane to have range.
LOB_PANE_ON = True
LOB_BASE_N = 5
LOB_MIN_N = 3
LOB_LOW = 0.98
LOB_HIGH = 1.03
LOB_MIN_COLS = 2                # the depth snapshots are ~30 s apart and a median cycle is ~82 s, so a cycle
                                # averages ~3 of them. Under this it is a SAMPLE, not an average -- no bar.
LOB_MIN_SPAN = 0.3219           # log2(1.25): the y range never shrinks below +-25%, so a 3% wiggle cannot be
                                # auto-fitted into looking like a signal.
LOB_CACHE_MAX = 4000            # per-cycle book means kept across windows, so the baseline survives a pan

# --- Speed pane: is price flat, drifting, normal or fast this cycle, for its own side? ------------------------
# |ticks per SECOND| over the cycle vs the MEDIAN of the same side's previous N. Measured on 20 h (n=598):
#   p10 0.27  p33 0.65  median 1.03  p67 1.50  p90 3.15  p99 7.27
# The cuts are those TERCILES, so each label is a real third; they move by <0.06 over N=3..8.
# Controls: only 8% shared with cycle DURATION (so this is not the bar's width restated, unlike the Volume
# pane's 45-52%), 42% with the |move| ratio, which is expected since speed has move in its numerator.
SPEED_PANE_ON = True
SPEED_BASE_N = 5
SPEED_MIN_N = 3
SPEED_SLOW = 0.65
SPEED_FAST = 1.50
SPEED_FLAT_TICKS = 1.0          # under a tick the DIRECTION is meaningless, so FLAT is its own class, not a
                                # slow one. 15% of finished cycles land there.
SPEED_FLAT_COL = "#5a616d"      # dimmer than the "normal" grey, so flat and normal do not read alike
SPEED_BADGE_TIERS = ((48.0, 1), (26.0, 2))      # "+fast" / "-slow" / "flat"

# --- Interpretation pane: name what happened in each cycle -----------------------------------------------
# The user's seven-state table, classified by the QUADRANT MAP at the top of that same picture: aggressive
# volume x price displacement. Measured on 20 h / n=670 before it was built:
#   * volume must be $ PER SECOND. Total $ against the previous cycles shares 49-53% of its variance with
#     cycle DURATION -- long cycles would read "heavy" on both sides at once. The rate shares 2-3%.
#   * splitting each axis at its own baseline (ratio > 1.0) fills the quadrants 28/22/21/29% and the seven
#     named states 9.5-14.4% each. Nothing degenerate, nothing forced.
#   * the median cycle sits 0.49 log2 units from the crosshair; 36% sit inside 0.35 and are drawn DIM.
# The BOOK and the per-side volumes are shown as EVIDENCE only: the table's full 5-cell signature scored
# 1.00/4 against ~1.33/4 for chance, so it is not used to classify.
INTERP_PANE_ON = True
INTERP_WIDTH = 440              # measured: the price-move line needs 352 px at 8 pt. On the SAME line as
                                # the state name it needed 555, past the panel's own 520 maximum, so it gets
                                # its own line and the panel gets the width that line actually needs.
                                # 404 not 380: 'SELLER ABSORBED' + 'drifting down' on the state line
                                # needs 376, and a 4 px margin is not a margin.
                                # 440 not 404: 'Limit Buyers +30%' / 'Limit Sellers -12%' needs 423.
                                # Dropping the second 'Limit' would fit 404 -- the user's wording won.
INTERP_MAX_ROWS = 240           # the feed is capped, not the history: older cycles still feed every baseline
# The cycle candles ACCUMULATE rather than covering a fixed span (user 2026-09-12: "forget the 8h idea ...
# as I pan/zoom the candles get generated, as it was set, but keep them on chart even if I zoom on 1 candle").
# The pane reads the VIEW like the other cycle panes -- so the four share one crosses() memo entry -- and
# keeps every cycle it has ever read, so zooming in discards nothing and zooming back out re-displays the rest
# without re-deriving it. MEASURED: a zoom down to a single candle and back out = 0 picture rebuilds.
#
# ⚠⚠ BOTH CAPS EXIST FOR THE FRAME, not for memory (a cached cycle is seven floats). MEASURED with a real
# grab(), candles on screen -> ms of paint EVERY FRAME: 300 -> 3.4 | 400 -> 5.8 | 800 -> 9.6 | 1200 -> 15.2 |
# 2400 -> 12-21, and one picture BUILD at 400 -> 6.3 ms, at 1200 -> 44.8, at 2400 -> 46.
PX_CACHE_MAX = 3600             # cycles retained, EVICTING WHAT IS FURTHEST FROM THE VIEW -- not the oldest,
                                # which would throw away the very candles a LEFT pan has just generated (see
                                # _px_cache_merge). The cap is FLOW_CROSS_MAX, the most cycles one crosses()
                                # read can return: the user can zoom out until the WHOLE cache is on screen,
                                # where every candle in it is legitimately visible and must be drawn, so
                                # holding more would let this feature make the worst frame worse than the one
                                # that already shipped. Cycles beyond it are not lost -- panning back to them
                                # regenerates them, which is the behaviour the user asked to keep. Raising it
                                # costs roughly 12 us of paint per extra candle, on every frame.
PX_DRAW_MAX = 3600               # candles in ONE picture: the picture is a WINDOW on the cache, padded either
                                # side by whatever is left of this cap, so an ordinary pan or zoom lands
                                # inside an already-drawn set and rebuilds nothing. Padded in CANDLES rather
                                # than seconds because that is the unit the cost is in. ⚠ when the VIEW alone
                                # holds more than this, the pad goes to zero and every visible candle is
                                # still drawn: the cap bounds the MARGIN, it never hides data. At PX_CACHE_MAX
                                # == this, the window is always the whole cache -- the window logic is what
                                # keeps the cost bounded if the cache cap is ever raised.
INTERP_SPAN_SECS = 6 * 3600.0   # The feed is anchored at the LIVE EDGE and spans this, INDEPENDENT of the
                                # chart's view: zooming or panning must not empty it (user 2026-09-11). That
                                # costs a second crosses() entry -- measured 0.88 ms cold at 4 h, 1.19 at 6 h,
                                # so 2 reads per 0.5 s tick instead of 1: +0.24% of one core.
INTERP_STALE_SECS = 600.0       # The feed's read ENDS at the store's own live edge, so "is the last cycle
                                # still forming?" is really "is the tape fresh?". A quiet market can go
                                # minutes without a print -- measured 189 s behind wall-clock on live tape --
                                # so a tight gate silently dropped the forming row. Past this the tape is
                                # stale (a dropped feed) and nothing is claimed to be forming.
INTERP_BASE_N = 5
INTERP_MIN_N = 3
INTERP_WEAK_BELOW = 0.35        # log2 distance from the crosshair, on the WEAKER of the two axes

# --- ABSORPTION as a REJECTION, not an open-to-close move (user 2026-09-12) -------------------------------
# Buyers can drive price 20 ticks up and hand every one back; open-to-close then reports ~0 -- the strongest
# possible absorption, invisible. So a BUYER ABSORBED cycle is measured from its HIGH to its close, and a
# SELLER ABSORBED one from its LOW.
#   MEASURED on live tape, absorbed cycles: giveback median 7 ticks, p90 19, max 41, while open-to-close
#   against the aggressing side had a median of MINUS 1 -- the two differ by a median of 8 ticks.
#   As a FRACTION of the push it runs p10 0.39, p50 0.84, p90 1.50, terciles 0.68 / 1.00; 38% of absorbed
#   cycles gave back the WHOLE push (a full reversal past the open).
#   ⭐ And it is new information: shared variance with the flow ratio 0%, speed 4%, |open-close| 1%,
#   duration 3%. Unlike the book columns, this is not something the pane already knew.
#   RE-MEASURED after the per-bin TRUE extremes landed (n=458 absorbed over 66 h, was n=40 over 6 h): the
#   giveback fraction is essentially unchanged -- terciles 0.71 / 1.00 against the old 0.68 / 1.00, and 38%
#   still hand back the whole push -- so ABSORB_REJECT_WEAK below needs no re-basing. The PUSH distribution
#   did move (p50 6 t, p90 19, max 141), which is what re-based the floor's rationale.
ABSORB_PUSH_MIN_TICKS = 4.0     # under this push the ratio manufactures a percentage rather than reporting
                                # one: at 2.0 a 2-tick push against a 31-tick giveback printed "1533% given
                                # back". RE-MEASURED on the TRUE per-bin extremes (n=458 absorbed / 66 h):
                                # fractions with |frac| > 3 are 3% in the 4-5 tick bucket and 0% above it, so
                                # the value stands -- but it is NOT the p10 any more (that is now 2 ticks) and
                                # it withholds the percentage from 32% of absorbed cycles, not ~10%. Withheld,
                                # never guessed: those rows print no "% given back" at all.
ABSORB_REJECT_WEAK = 0.68       # the measured LOWER tercile: gave back less than this and the absorption
                                # claim is thin however heavy the flow was

# --- Pane NAMES: one source of truth ---------------------------------------------------------------------
# The pane paints this top-left AND its hamburger toggle carries the same words (user 2026-09-11), so a
# toggle can never drift from the pane it opens. They had: the toggle for "CYCLE IMPACT" read "Cycle pane
# (dominance runs)". Defined here, after the N's they quote, so the numbers can never disagree either.
# The CYCLE LOOKBACK: how many previous cycles every rating in the family is measured against. One knob for
# CYCLE VOLUME, CYCLE BOOK, CYCLE SPEED and the Interpretation feed, changed from the feed's bottom-right
# corner (user 2026-09-11), so they can never drift apart.
#
# MEASURED across N=2..20 on 20 h / n=678 before the control was built, at the SHIPPED band cuts:
#   VOLUME  low/normal/high  32/33/35% at N=5, still 30/36/35% at N=10, 26/38/35% at N=20
#   SPEED   slow/normal/fast 37/27/36% at N=5, essentially flat to N=20 (34/29/37%)
#   BOOK    37/26/36% at N=5 -> 44/22/34% at N=20; its middle band was never a true third at this radius
#   FEED    breakout 27% at N=5 -> 31% at N=20 (its own split is at ratio > 1.0, so it self-normalises)
# So the cuts keep their meaning across the range; BOOK degrades the most and is the one to watch.
CYCLE_BASE_N = 5
CYCLE_BASE_N_MIN = 2
CYCLE_BASE_N_MAX = 200          # NOT a measurement artifact this time. The cold crosses() read is driven by
                                # the WINDOW, not by N -- measured on 20 h of live tape: 1 h 0.39 ms, 4 h
                                # 0.87, 10 h 2.35, 20 h 6.30. Scaling the window as N/5 made the cost track N,
                                # which was the only thing a cap was ever protecting. The window is bounded
                                # below instead, so the cost is flat in N and N can be whatever is useful.
                                # ⚠ The mix DOES shift with N, and that is real rather than a bug: over the
                                # same 20 h the feed reads BREAKOUT 27.5% at N=5, 29.5% at 20, 33.8% at 50 and
                                # 40.5% at 100. A longer baseline is smoother, so "heavier than usual" and
                                # "faster than usual" coincide more often. It is a different question, not a
                                # better answer.
CYCLE_LOOKBACK_MAX_SECS = 12 * 3600.0   # 2.35 ms cold, so ~0.9% of a core at two reads per 0.5 s tick.
                                # Past this the baseline uses however many cycles the window holds.


def pane_titles(n=None):
    """Every pane's name at lookback `n`. The pane paints it and its hamburger toggle carries the same words."""
    n = int(CYCLE_BASE_N if n is None else n)
    d = "  ·  "
    return {
        "liq": "LIMIT ORDERS" + d + "resting bid / ask $",
        "cyc": "CYCLE IMPACT" + d + "ticks per %s" % FLOW_CROSS_BADGE_UNIT_TXT,
        "cvol": "CYCLE VOLUME" + d + "dominant side vs last %d" % n,
        "lob": "CYCLE BOOK" + d + "bid / ask vs last %d" % n,
        "spd": "CYCLE SPEED" + d + "ticks/s vs last %d" % n,
        "interp": "INTERPRETATION" + d + "one row per cycle",
        "px": "PRICE" + d + "one candle per cycle",
        "lines": "BUY / SELL FLOW" + d + "taker $ per window (the pane)",
        "fratio": "FLOW RATIOS" + d + "$/s vs last %d" % n + d + "buy / sell",
        "iimp": "INTEREST × IMPACT" + d + "who leads vs last %d" % n,
        "cint": "LINES INTEREST" + d + "each side's aggressive $/s vs its own last %d" % n,
        "cimp": "LINES IMPACT" + d + "each side's reach (led) or push-back vs its own last %d" % n,
    }


# --- Flow ratios pane (user 2026-09-15): the interpretation feed's "buy 1.06  sell 0.35", as two step lines
# on the cycle clock -- one value per cycle held over the cycle's span. EXACTLY the feed's numbers: each
# side's aggressive $ PER SECOND over the MEDIAN of that side's rate in the previous N cycles (prev_ratio,
# include_open, so the forming cycle is rated from what it has so far and a half-formed cycle never enters a
# baseline). The feed's "flow" (both sides) is NOT drawn (user 2026-09-15: "remove the blue line"). Same
# crosses() arguments as the other cycle panes, so the read is a memo hit. Plotted in log2 (0.5x / 1x / 2x
# are equidistant); the dashed guide is 1.0x.
FRATIO_PANE_ON = True
FRATIO_BUY_COL = "#26a69a"      # buy -- the same teal as the buy $ line
FRATIO_SELL_COL = "#ef5350"     # sell -- the same red as the sell $ line
# the pane's top-right dropdown (user 2026-09-15). "None" = both sides, as the pane has always drawn them; otherwise
# ONE series over the whole cycle: Buyer Ratio, Seller Ratio, or Delta Ratio = the SIZE of the cycle's net
# aggressive $/s (buy minus sell) over the median size of the previous N cycles' net (prev_ratio, include_open, the
# same rule as the other two), coloured by its SIGN: teal = buyers were the net aggressors, red = sellers. Either
# colour can sit above 1.0x (a bigger imbalance than usual) or below it (a smaller one).
# ⚠ The first cut drew buyer ratio / seller ratio coloured by that quotient's own side of 1.0x, so teal was ALWAYS
# above and red ALWAYS below -- the user flagged it as "not normal" the same day. Persisted as fratio_mode.
FRATIO_MODES = ("None", "Buyer Ratio", "Seller Ratio", "Delta Ratio")
# Delta Ratio is DRAWN within 1/FRATIO_DELTA_CLIP .. FRATIO_DELTA_CLIP (the badge still prints the true value): a
# nearly balanced cycle has a net close to zero, so its ratio runs toward 0 -- the 2026-09-15 probe on real tape
# printed 0.00x (log2 about -9) -- and the axis fit stretched to reach it, flattening every other bar
FRATIO_DELTA_CLIP = 16.0
FRATIO_MODE = "None"

# --- INTEREST x IMPACT pane (user 2026-09-16: "its really hard to look at different panes at the same time ...
# one pane that does this for me"). ONE bar per FINISHED cycle, folding what needed four panes to read:
#   CLICK   a left click explains ONE bar in plain language and outlines it. The four labels (Height, Colour,
#           Fill, Dot) are a size and a shade above their own descriptions -- they used to be <b> like the numbers
#           beside them and did not separate. A WHY line closes the panel with the MECHANISM: the height only ever
#           sees the aggressive end of a trade, so when price disagrees with the aggressor the passive end is what
#           moved it (absorbed into standing size, or quotes pulled). ⚠ that passive read is worded as the
#           INFERENCE it is -- the pane sees the book at a cycle's OPEN, never the passive fills through it.
#   HEIGHT  log2 of the aggressive interest imbalance -- the buyers' taker $/s over the median of their previous N
#           cycles, divided by the sellers' same ratio. Above 1.0x the buyers are hotter than usual relative to
#           the sellers, below it the sellers are. Every multiple SHOWN is the LEADING side's, always >= 1.0x (it
#           printed buy / sell, so a red bar showed the reciprocal and had to be inverted by eye). The AXIS
#           is MIRRORED for that same reason: both halves read the leader's multiple, so a bar pointing DOWN
#           to 2.3x means the SELLERS led by 2.3x. Unmirrored it read 0.44x under a bar the panel called
#           2.3x -- the same reciprocal, one layer lower (user 2026-09-16: "is that normal??").
#   COLOUR  the side that leads (teal buyers, red sellers), ORANGE when price went the OTHER way.
#   FILL    SOLID when THAT SIDE's push REACHED at least what its own previous N cycles reached for that effort and
#           that time (IIMP_COEF_*), HOLLOW when it did not -- interest that did not convert. ⚠ the fill follows
#           the side the HEIGHT names, not the side that owns the cycle by the crossing: those differ on about one
#           cycle in four, and a bar that mixed them was describing two different sides at once.
#   DOT     the FAR side's resting $ at the cycle's open against the previous N cycles: filled above
#           IIMP_WALL_HIGH (pushed into a wall), hollow below IIMP_WALL_LOW (open road), nothing in between.
# The cuts are the measured TERCILES over 48 h / 1250 rated cycles (2026-09-16), the Volume and Speed panes' rule.
# ⚠⚠ The resting book is NOT blended into the height, though the user asked for both kinds of interest in one
# number: measured, giving the book real weight (x2) raised its share of the number to 15% but dropped the
# height's agreement with the cycle's own dominant side from 77% to 68%, and with the direction price actually
# took from 65% to 58%. The book's swing is far narrower than the tape's (p10..p90 0.73 vs 2.33 in log2), so any
# blend either ignores it or amplifies its noise. It carries its own mark instead.
# ⚠⚠ IIMP_COEF_* are NATURAL-log slopes and the residual they produce is in natural-log units, so the multiple
# of expected reach is e**resid. Displaying 2**resid squashed every reading toward 1.0x (a true 3.54x printed
# as 2.4x). Terciles and the fill are untouched by the base -- only the printed multiple was wrong.
# ⚠ IIMP_COEF_* are log-log slopes of reach on (own $ up to the extreme, seconds to the extreme, resting wall at
# the open), fitted per side on the same 48 h (buy R2 0.744 / sell 0.726). No intercept is needed: the score is a
# residual against the median of the previous N same-side cycles, which cancels it.
IIMP_PANE_ON = True
IIMP_MODES = ("None", "Buyer", "Seller", "Delta", "Lines Buyer/Seller")   # the pane's top-right dropdown
#                                 None = the leader's multiple; Buyer / Seller = that side's interest x impact
#                                 vs its own baseline; Delta = buyer over seller. All in log2 (user 2026-09-20).
#                                 Lines Buyer/Seller (user 2026-09-21) = the Buyer option and the Seller option
#                                 TOGETHER, as two lines instead of bars: the same numbers to the digit, one point
#                                 per cycle at its middle, both against the same 1x midline and INDEPENDENT of each
#                                 other -- each side against its OWN baseline, unlike None and Delta, which set one
#                                 side against the other.
IIMP_MODE = "None"
IIMP_LINES_MODE = "Lines Buyer/Seller"
# (Lines Interest and Lines Impact were options here for a few hours on 2026-09-22 and are now panes of their
# own -- see the LINES INTEREST / LINES IMPACT block below. Do not put them back in this dropdown.)
# THE SMOOTHING SLIDER on THIS pane drives its Lines Buyer/Seller option and nothing else (user 2026-09-22:
# "also on the interestximpact add the slider on Lines Buyer/seller dropdown option"). Same trailing geometric
# mean the two split panes use, over LINES_SMOOTH_MIN..MAX cycles, persisted as "iimp_smooth".
# ⚠ 5 since 2026-09-23, at the user's word ("make the smooth slider default to 5 / both on the tablet and
# terminal"). It shipped at 1 -- no smoothing, the chart this mode drew since e312e2c -- so that turning the
# slider on could never redraw a view nobody asked to change; they have now asked.
IIMP_SMOOTH_N = 5
IIMP_LINES_W = 1.8              # width of the two lines, px (the forming stretch is drawn at IIMP_FORM_PEN_A)

# ---------------------------------------------------------------------------------------------------------
# LINES INTEREST and LINES IMPACT -- two panes of their own (user 2026-09-22: "we gonna seperate them from
# interestximpact panel, so they wont be included in the dropdown anymore, instead each will have its own pane
# and toggle"). Both draw ONE LINE PER SIDE, teal buyers / red sellers, on a log2 axis against a 1x midline.
#
#   LINES INTEREST  each side's aggressive $ per second over the median of its own previous CYCLE_BASE_N
#                   cycles -- how HARD that side has been pushing against its own recent normal.
#   LINES IMPACT    each side's reach against what that side usually reaches for the same effort in the same
#                   time. ⚠ Impact exists only for the side that LED a cycle, so each line is the mean of its
#                   OWN last N cycles AS THE LEADER and HOLDS flat across the cycles it did not lead. The user
#                   was asked and chose that over counting a non-led cycle as 1x, which draws a sawtooth.
# ---------------------------------------------------------------------------------------------------------
CINT_PANE_ON = False            # both default OFF: a split that silently adds two panes to the stack would be
CIMP_PANE_ON = False            # a surprise, and the toggle is the point of the split
LINES_SMOOTH_N = 5              # DEFAULT trailing-mean window, in CYCLES, and its OWN knob -- deliberately not
                                # the cycle lookback. The lookback picks the BASELINE each cycle's reading is
                                # divided by; this picks how many of those are averaged for display. Tying them
                                # would move the smoothing every time the user changed the baseline, which are
                                # two different questions. Averaged in LOG space (a geometric mean), so 0.5x and
                                # 2x pull on it equally -- an arithmetic mean of ratios is biased upward.
                                # LIVE value: each pane's own slider, persisted as "cint_smooth"/"cimp_smooth".
CIMP_SMOOTH_N = 3               # LINES IMPACT's own default (user 2026-09-25: "make Lines impact ... smoothing 3 by
                                # default"); LINES INTEREST keeps LINES_SMOOTH_N. A saved slider value still wins.
                                # The two panes carry SEPARATE windows: once they are separate panes there is no
                                # reason a slow interest read and a fast impact read should not sit side by side.
LINES_SMOOTH_MIN = 1            # 1 = no smoothing at all, the raw per-cycle reading. ⚠ _lines_smooth() clamps
LINES_SMOOTH_MAX = 30           # its min_n to the window for exactly this: a window of 1 can never hold 3
                                # samples, and without the clamp the series comes back NaN and the pane blank.
LINES_W = 1.8                   # width of the two lines, px; the forming stretch uses IIMP_FORM_PEN_A's alpha
# --- LINES IMPACT: the DOMINANCE bands (user 2026-09-22: "mark the areas in red/green where one side impact
# grew x1 times more than the other", corrected to "not 1x rather 0.3x"). A tinted full-height band over every
# cycle where one side's smoothed impact stands at least this far above the other's.
# ⚠ A DIFFERENCE OF MULTIPLES, not a ratio -- the reading PX_IIB_MIN_SPREAD already carries for this family,
# from this user, on this exact wording. Buyers 1.8x vs sellers 0.7x is a spread of 1.1x and is marked; buyers
# 1.4x vs sellers 0.9x is 0.5x and is not, though its RATIO is 1.56x.
# ⚠ Compared on the TRUE multiples, never the clipped values the lines are DRAWN with -- the Takeover
# badge's rule, for the same reason: a clip is a drawing limit, not a reading.
LIMP_DOM_SPREAD = 0.3           # 0 switches the bands off entirely
# THE SECOND SHADE (user: "for area that gained 0.3x use a brighter green/red"). A gap that wide can open two
# ways and they are not the same event: the leading side CLIMBED, or the other side fell away under it. The
# bright shade is the first. Measured SINCE THE BAND STARTED -- the reference the user picked when asked --
# read from the cycle JUST BEFORE it opened, so the move that creates the band counts.
# ⚠ Reading from the band's own first cycle instead looked right and was wrong: a leader jumping 1.0x ->
# 1.4x, the clearest climb there is, measured a gain of zero and drew dim. Their own example is the dim case:
# both lines at 1x, green falls to 0.65x, red never moved, so the area is red and stays dim.
LIMP_DOM_GAIN = 0.3             # how far the LEADING side must have climbed inside the band to brighten it
LIMP_DOM_ALPHA = 38             # the tint, out of 255. Low on purpose: it sits UNDER the two lines and the
LIMP_DOM_ALPHA_HI = 95          # guides, and the pane's job is still the lines. HI is the gained-into band.
# The BRIGHT band gets its own pair of colours, picked by the user from two swatches (2026-09-23), rather
# than the pane's teal / red at a higher alpha. They are far more saturated than IIMP_BUY_COL / SELL_COL, so
# a gained-into band separates from an ordinary one by HUE as well as by weight.
# ⚠ THE SELLERS' BRIGHT BAND IS PURPLE, NOT RED (user 2026-09-23: "maybe its not really visible to me, so
# change it to this color instead bright purple"). Bright red #FF0000 against the dim band's #ef5350 was red
# on red -- the two shades differed only in saturation and weight, which is the one axis a 38-vs-95 alpha was
# already using. Purple separates them by HUE, the way #66FF00 already does on the buy side.
# ⚠ Both swatches arrived as lossy WebP: the purple samples #BD03FD but is LABELLED #BE03FD, and the label
# is used. (The first red swatch drifted the same way, #FF000D for pure red.) The green was exact.
# --- LINES IMPACT: the STEP marks (user 2026-09-23). A single move of at least LIMP_STEP between one cycle
# and the next is drawn LIMP_STEP_W wide: in the side's own colour when it ROSE, grey when it FELL -- a side
# losing its impact is not a signal FOR that side, and teal or red would read as one.
# ⚠ ONE STEP, not a run: the first cut marked the whole monotone climb and the user replaced it -- "it should
# be the increase/decrease just from 2 cycles so we will not color the whole increase/decrease".
# ⚠ A DIFFERENCE OF MULTIPLES, like every "Nx" in this family: 2**new - 2**old, never the log2 step the lines
# are drawn with. In log2, 2.0x -> 2.3x (+0.3x) would go unmarked and 0.20x -> 0.25x (+0.05x) would be marked;
# the tablet's first cut did exactly that and is corrected in the same commit as this block.
# ⚠ THE SMOOTHING SLIDER DECIDES HOW OFTEN THIS FIRES: the lines are a trailing mean, and a longer window
# flattens exactly the single-cycle jumps this looks for.
LIMP_STEP = 0.3
LIMP_STEP_W = 4.0               # px, against LINES_W 1.8
LIMP_LOSS_COL = "#7a828e"       # the grey of a FALL
LIMP_DOM_BRIGHT_BUY = "#66FF00"
LIMP_DOM_BRIGHT_SELL = "#BE03FD"
# --- BREAKOUT BADGE on the PRICE pane (user 2026-09-21: "add a badge on the breakout candles where the candle side is
# above x1 interestximpact and its opposite is below x1 -- for example we have a breakout buy candle and the
# interestximpact line buy is above x1 and interestximpact line sell is below x1"). A BREAKOUT BUY candle gets a GREEN
# badge when the buyers' I x I is above 1x AND the sellers' is below 1x; a BREAKOUT SELL candle a RED one on the
# mirror. The two numbers are the "Lines Buyer/Seller" lines themselves, and "breakout" is the candle's own state
# colour (the Interpretation feed's classifier), so the badge is a join of two things already on screen.
# THE BADGE IS A TRIANGLE (user, the same day: "add red/green triangle instead above/below, for above reverse the
# triangle"): GREEN and pointing UP under a breakout-buy candle's low, RED and pointing DOWN over a breakout-sell
# candle's high -- the Volume Burst badges' placement, the user's own choice of 2026-09-09. It cannot be taken for a
# Big Player bubble (a circle, at a print's price). The forming candle's badge is lighter -- its state and both
# lines still move until the cycle closes.
# ⚠ It exists only where the I x I pane has numbers: the newest FLOW_CROSS_MAX cycles of the view, rated ones only.
# ⚠ Reading by demand: while the PRICE pane shows, the I x I tick runs even with that pane toggled off.
# ⚠ DESCRIPTIVE: it names a cycle that has closed (or is closing); nothing here was tested as a signal.
# "TAKEOVER" is its name in the menu (Indicator > Cycle Chart): one side takes the tape over while the other lets go.
# PX_IIB_ON is only the toggle's DEFAULT -- the checkbox (persisted with every other layer, key "cyc_takeover") is
# what switches it, and with it off nothing is computed and nothing keeps the I x I tick alive.
PX_IIB_ON = True
# VACUUM candles too (user, the same day: "apply this also for vaccum buy/sell not just breakout buy/sell"). A vacuum
# is the quadrant map's LIGHT flow + BIG move, and like a breakout it names the way price went: buy = up, sell = down.
# ⚠ The PRICE pane draws vacuum candles NEUTRAL (only breakout and absorbed carry a colour), so their state is not in
# the candle cache: it is classified on the I x I pane's own read with the very quadrant rule the candles' colours and
# the Interpretation feed use (_px_quadrants), and only a candle the cache left neutral can be badged as a vacuum.
PX_IIB_VACUUM = True
# QUIET candles too (user, the same day: "lets apply it also for the Quiet"): the quadrant map's LIGHT flow + SMALL
# move. The feed gives a quiet cycle no side, so here it takes the way price went, like the other two: up = buy,
# down = sell, and a cycle that closed where it opened has no side and no badge.
PX_IIB_QUIET = True
# ... and the two LIGHT-flow states must have HELD their move (user: "regarding the vaccum and quiet they should have
# kept >= 70%"): kept = the candle's move in its own direction over how far it reached that way -- (close - open) /
# (high - open) for a buy -- the I x I pane's own "kept", read under the same rule: only once the push is at least
# IIMP_KEEP_MIN_TICKS long. A push too short to read has not shown it kept anything, so it gets no badge. Breakouts
# are NOT held to it. 0 switches it off.
PX_IIB_KEPT_MIN = 0.70
# THE GAIN FILTER (user, the same day: "for breakout buy signal we wanna see buyer gaining interestximpact and sellers
# loosing it, and for breakout sell signal we wanna see sellers gaining interestximpact and buyers loosing it"): on top
# of the 1x rule a breakout BUY needs the buyers' I x I ABOVE the previous bar's and the sellers' BELOW OR EQUAL TO the
# previous bar's (the user relaxed "below" to "lower or equal" the same day: the other side standing still is enough);
# a breakout SELL the mirror. "The previous bar" is the cycle right before it, and it must be one the I x I pane
# could rate: across a break in the lines there is nothing to have gained on, so there is no badge. The TRUE values
# are compared, not the 8x-clipped ones the lines are drawn with.
PX_IIB_REQUIRE_GAIN = True
# THE SPREAD (user, the same day: "lets add the spread, it should be at least 1x"): the gap between the two lines in
# the chart's own units -- the candle side's multiple MINUS the other side's -- must reach this much. Buyers at 1.8x
# with sellers at 0.7x is a spread of 1.1x and passes; buyers at 1.4x with sellers at 0.9x is 0.5x and does not.
# ⚠ A DIFFERENCE of multiples, not their ratio: under the 1x rule the ratio is above 1x by construction, so "at least
# 1x" could only ever have meant the difference. 0 switches it off.
PX_IIB_MIN_SPREAD = 1.0
PX_IIB_BUY_COL = "#00C853"      # the breakout-buy candle's own green ...
PX_IIB_SELL_COL = "#FF1F1F"     # ... and the breakout-sell candle's own red
PX_IIB_SIZE = 10                # the triangle, px
PX_IIB_OFFSET_PX = 9            # its centre sits this far beyond the wick's end
PX_IIB_FORM_A = 110             # alpha of the forming candle's badge (finished ones are opaque)
IIMP_LOW = 0.76                 # imbalance terciles: below = sellers lead, above IIMP_HIGH = buyers lead
IIMP_HIGH = 1.37
IIMP_WALL_RADIUS = 25           # the wall is read within +-this many ticks of mid (a radius on the daemon's ladder)
# THE CANONICAL WALL GRID (2026-09-23). The wall is read from columns aligned to absolute multiples of this many
# seconds, fetched for their own sake -- never from the Limit Orders pane's VIEW window, whose columns start at
# the view's left edge and are as wide as the view divided by the pane's pixels. The daemon fills a column with
# the latest depth snapshot at or before the column's END, so an aligned column has ONE value whichever request
# fetched it. ⚠ Before this, the same cycle read differently depending on where the user had panned (measured:
# 31/97 I x I and 40/91 LINES IMPACT cycles changed between a cold and a warm read of the same view).
IIMP_WALL_COL_SECS = 15         # the snapshots come every ~30 s; 15 s columns never skip one
# THE BREAKOUT GATE (user 2026-09-25: "the wall should be > 1x, the impact >= 1.5x and the kept >= 70%"), ADDED to heavy
# flow + fast price, read on the I x I pane's numbers for the cycle's leader (flow_interp.breakout_class). Updated the
# same day: "its either wall >=1x or opposite tape >=1x" -- the leader met a wall OR the other side's aggression.
BREAK_WALL_MIN = 1.0            # the wall in the leader's way, x its normal: at least ...
BREAK_OPP_TAPE_MIN = 1.0        # ... OR the OTHER side's tape (its aggressive $/s x its own normal): at least
BREAK_IMPACT_MIN = 1.5          # the leader's impact (reach x its usual): at least; a SHORT push quotes none
BREAK_KEPT_MIN = 0.70           # the share of that reach kept at the close: at least
# THE VACUUM GATE (user 2026-09-25: "if its buy interest the sell tape and wall should be <1x, if its sell interest the
# buy tape and wall should be < 1x"), ADDED to light flow + fast price, read on the I x I pane's numbers for the cycle's
# INTEREST LEADER (flow_interp.vacuum_ok): nothing stood in its way -- no wall, no aggression from the other side. The
# mirror of the breakout gate (wall >= 1x OR opposite tape >= 1x). The same day: "remove the overall flow filter from
# the vaccum because we already have the opposite tape and wall filter" -- ANY fast cycle, heavy or light (a heavy one
# is tried as a breakout, then as the absorbed case, first). A fast cycle that fails every gate is NORMAL.
VAC_WALL_MAX = 1.0              # the wall in the leader's way, x its normal: under ...
VAC_OPP_TAPE_MAX = 1.0          # ... AND the OTHER side's tape (its aggressive $/s x its own normal): under
# Its CANDLE: the breakout candle's own green / red ("use the same green and red on the breakout candle"), outline and
# wicks SOLID like a breakout's, the body at the low opacity ("keep the current low oppacity of the vaccum"). The tablet
# mirrors the alpha (ChartView.VAC_FILL_A).
VAC_CANDLE_FILL_A = 56          # the vacuum candle's body, alpha out of 255 (~22%)
# A CONFLICT BAR (user 2026-09-25: "conflict bars are where the tape of both side >=3x"): BOTH sides' tapes -- each
# side's aggressive $/s against its own last N, the card's two tape bars -- at or above this. The tablet boxes the candle
# in red, high to low ("the box should be RED color 2px width from the high to the low of the conflict bar").
CONFLICT_TAPE_MIN = 3.0
# ... and its REACH (user 2026-09-25): no longer the bar's own high / low. The LOW is the low of the closest PREVIOUS
# lime LINES IMPACT area whose low is below the bar's low, the HIGH the high of the closest previous purple area whose
# high is above the bar's high -- looking back no further than this. One found and not the other: the other side takes
# the same distance from the bar. Neither: the bar's own high / low.
CONFLICT_LOOKBACK_SECS = 24 * 3600.0
IIMP_WALL_CHUNK = 900           # columns per request (3.75 h; ~50 KB, the Limit Orders pane's own budget)
# THE LIVE EDGE (2026-09-23). A new cycle is only known ~20 s after it opened (FLOW_CROSS_MIN_HOLD_SECS), and its
# wall is the column that ENDS at or before its open -- so that column has been over for 20 s or more by then.
# Fetching it LIVE_LAG after its end puts the wall in hand BEFORE the cycle is known, and the forming point shows
# the moment the cycle does. ⚠ It was FINAL_LAG + a 30 s cadence before: the forming I x I bar and LINES IMPACT
# point came 30-75 s after the open, and a cycle shorter than that was never drawn while it formed (measured).
# The daemon writes its snapshots every DEPTH_SYNC_SECS (10 s) and that write can stall, so a column fetched
# before FINAL_LAG is PROVISIONAL: it is fetched once more when it turns final, and a changed value replaces it.
# The grid a live session ends with is therefore the grid a cold start reads.
IIMP_WALL_LIVE_LAG = 15.0       # a column is fetched this long after its end (past the daemon's 10 s write)
IIMP_WALL_FINAL_LAG = 45.0      # ... and is FINAL once its end is this far in the past -- every snapshot has landed
IIMP_WALL_BACKFILL_GAP = 5.0    # seconds between requests while filling history -- PACED (2026-09-23 outage, below)
# ⚠⚠ THE 2026-09-23 OUTAGE. The daemon's trade intake froze at 14:13:11 UTC during a sell-off burst, on a box already
# out of memory, after its slow liquidity-window requests had gone from 0-30/h (~1 s each) to 184/h (38-83 s max) --
# every start of every client backfilled ALL 72 h of walls at 1 s spacing. The grid now backfills ON DEMAND: this
# many hours at least, and back to the leftmost view edge seen this session (+ the lookback), never further.
IIMP_WALL_MIN_BACK_H = 6.0
IIMP_WALL_LIVE_GAP = 2.0        # the least seconds between live-edge requests (one is due per 15 s column anyway)
# WHAT HAS BEEN PAINTED STAYS (user 2026-09-23: "whatever have been loaded and calculated and painted should staaay
# no matter if i zoom in out or pane left right"). The I x I pane and the two LINES panes keep every FINISHED cycle
# they have computed, keyed by its start, and draw from that cache -- the PRICE pane's rule (PX_CACHE_MAX). Before,
# each drew only its latest read: what left the view was dropped, and what came into it was blank until the next
# read. Bounded, evicting what is FURTHEST FROM THE VIEW; ~72 h of cycles fits.
IIMP_CACHE_MAX = 6000
IIMP_FIT_MIN_N = 20             # the y fit LATCHES only once this many cycles were rated -- a boot-time read with a
                                # handful of cycles must not freeze a scale the rest of the data will not fit
IIMP_WALL_LOW = 0.94            # far-side resting orders vs the previous N cycles -- terciles again
IIMP_WALL_HIGH = 1.06
# REFIT 2026-09-24 on the RULE-B cycles (flow_pane 0e65ac3: one second belongs to one cycle), 72 h / 2271 rated cycles
# read off the daemon's own tape and book (scratchpad iimp_refit.py): buy R2 0.686 (1st half -> 2nd 0.678), sell 0.712
# (0.691). The old (0.090, 0.275, -0.235) / (0.089, 0.233, -0.231) fit these cycles at only 0.595 / 0.573, and 13% of
# fills changed with the refit. ⚠ The sell WALL slope is weak (SE 0.078) -- kept, noted. Re-check every week or two:
# the slopes moved a lot since mid-September, partly because the cycle boundary rule changed.
IIMP_COEF_BUY = (0.259, 0.172, -0.243)
IIMP_COEF_SELL = (0.285, 0.146, -0.107)
# THE OTHER SIDE'S PUSH-BACK (user 2026-09-24: "the buyers showed interest and realistically had an impact since only
# 24% of the bearish candle was kept"). On a cycle a side did NOT lead, its impact is how far it pushed price BACK
# from the leader's extreme to the close, against what its own $ and seconds after that extreme usually buy:
# ln(1 + ticks handed back) on ln(its $ after the extreme) and ln(seconds after the extreme), per PUSHING side, no
# wall (its slope was weak and the wrong sign). Same 72 h: buyers pushing back R2 0.467 (oos 0.373), sellers 0.460
# (0.417). It replaces the flat 1x the non-leader used to get in every per-side I x I value and in LINES IMPACT.
# Descriptive: none of reach / kept / push-back told the NEXT cycle anything (shift-null p 0.13-0.35) -- the goal is
# the market NOW.
IIMP_PB_COEF_BUY = (0.129, 0.211)       # BUYERS pushing back up from the low (a sell-led cycle)
IIMP_PB_COEF_SELL = (0.128, 0.257)      # SELLERS pushing back down from the high (a buy-led cycle)
IIMP_BUY_COL = "#26a69a"        # the same teal / red every other pane uses for the two sides
IIMP_SELL_COL = "#ef5350"
# ORANGE = the leading side is not the way price went, the cycle badges' own "contradicted" colour. Measured on
# 48 h: the aggressive tape leans against the cycle's price direction on 32% of cycles, and on 8% of the biggest
# up cycles the sellers were the heavier aggressors while price rose -- real, and it read as a contradiction until
# it had its own colour (user 2026-09-16: "we have a huge breakout bullish bar and the interestximpact shows a red
# bar below 1"). Price can rise on passive buying and withdrawn offers, which the tape never shows.
IIMP_CONTRA_COL = "#ff9f43"
IIMP_WALL_COL = "#dcdcdc"       # the wall dots are NEUTRAL: amber would read as the contradicted colour
# ⚠ there is NO per-cycle badge strip here (user 2026-09-16: "remove the 1.4x 0.38x badge ... a window should pop
# up when I click on the histogram bar"). One bar at a time is explained in words instead, on a LEFT CLICK, and the
# clicked bar is outlined. The strip was also what crowded the pane's own title at a dense zoom.
# The bar is DRAWN within 1/IIMP_CLIP .. IIMP_CLIP and the axis follows the 95th percentile: measured over 48 h the
# |imbalance| runs p50 1.6x / p90 3.1x / p95 4.0x / p99 7.6x with a max of 95x, so 5.1% of cycles pass 4x and 0.9%
# pass 8x -- fitting to the max (or to p99) let one cycle flatten every other bar in the first live render. The
# right-edge readout and the click panel keep printing the TRUE multiple.
IIMP_CLIP = 8.0
# The cycle STILL FORMING is drawn too (user 2026-09-16: "also add the live current forming one, the panel and the
# histogram should update live"), rated from what it has so far against the same baseline, on one lighter item of
# its own -- the ratios pane's convention, and its alphas. ⚠ Its numbers MOVE: every quantity is partial until the
# cycle closes, which is why it is drawn lighter and says so in the click panel. ⚠ It never enters any baseline
# (flow_interp._ratio_vec appends only finished cycles), so nothing it does can drag a later reading. ⚠ Only a LIVE
# read's last row is forming: crosses() marks the last row of ANY read unfinished, so a view panned back into
# history must not claim one -- the guard is INTERP_STALE_SECS against the view's right edge.
IIMP_FORM_FILL_A = 70           # forming bar: brush alpha when it has converted, pen alpha always
IIMP_FORM_PEN_A = 150
# --- RETENTION: what the push actually KEPT (user 2026-09-16: "we had a good impact 2.4x but it handed 17 tick out
# of the 27 initially traveled ... add a variable like efficiency"). The impact score is built on REACH alone, so a
# push that travels 27 ticks and hands back 17 scores the same as one that travels 27 and holds them.
#   kept = (the move in the LEADER's own direction) / reach, read only when reach >= IIMP_KEEP_MIN_TICKS.
# MEASURED over 48 h / 334 rated cycles (181 cleared the gate): terciles 0.375 / 0.750, median 0.571; 17.7% handed
# back EVERYTHING, 11.6% held nearly all. It is NOT the impact restated -- Spearman(kept, impact) +0.149, 2.2%
# shared variance (the earlier absorption work measured the same independence: 0% with flow, 4% with speed).
# The user's case is 10.8% of ALL rated cycles: impact >= 1.0x AND retention in the bottom third.
# ⚠ 46% of cycles never clear the 4-tick gate. Absence of a reading must NEVER downgrade a bar -- the panel says
#   "not read" and the cap is simply absent. (A 2-tick push once printed "1533% given back"; that is arithmetic,
#   not absorption -- the same reason ABSORB_PUSH_MIN_TICKS exists.)
# ⚠ Retention does NOT feed the fill or the score, on purpose. giveback = push - move, so scoring on both and then
#   validating against the move is CIRCULAR -- this project already produced one z=+7.6 result that way and
#   correctly never shipped it. Reach and hold stay two separate channels so you can see WHICH one failed.
# ⚠ DISPLAY (user 2026-09-24, "do the partial fill"): a bar that REACHED (solid) and went the leader's way is now drawn
#   solid only up to the share KEPT, from the 1x line out, and outlined beyond (terminal _iimp_kept_frac, tablet
#   ChartView). The VERDICT is unchanged -- `good` and the score are still reach only; orange bars and an unread kept
#   keep their whole fill.
# ⚠ DESCRIPTIVE only. As an entry filter retention tested null here (AUC 0.52-0.57, consistent in 2 of 6 setups).
IIMP_KEEP_MIN_TICKS = 4.0       # below this the fraction is arithmetic, not information
IIMP_KEEP_LOW = 0.375           # measured terciles of kept/reached over 48 h
IIMP_KEEP_HIGH = 0.750
IIMP_KEEP_MARK_ON = True        # the cap on bottom-tercile bars; False leaves the reading in the panel only
IIMP_KEEP_COL = "#3a4150"
# --- BUYER / SELLER SCORE (user 2026-09-16: "establish a buyer and seller score for each bar"). ONE number
# per side, 0..100 = that side's size-corrected aggressive $ per second, as a causal PERCENTILE within the last
# N cycles of BOTH SIDES pooled. Pooled, not each side against itself: the user's question is "how much they are
# interested COMPARED TO THE OTHER SIDE", and a self baseline answers the opposite one -- in a trend the winner's
# bar is already high so winning again ranks ~50, while the loser's small uptick ranks high. That is exactly the
# complaint that started the cut ("the price is going up and apparently sellers are behaving unusually strongly").
#
# ⚠⚠ IT USED TO HAVE FOUR COMPONENTS. Measured against the direction price took, over 44-48 h and replicated on
# four samples of 1080-1567 cycles, the four-part version scored 65-66% -- BELOW its own best part and below a
# one-line rule. Each part, alone:
#     agg  (aggression)  68.1-69.3%   the only clean carrier -- and identical to raw $ (68.1%)
#     conv (reach model) 62.2-63.0%   partly directional by construction; the iimp pane's FILL already shows it
#     pas  (resting $)   36.0-38.5%   INVERTED: the heavier book is the side price moves AGAINST
#     kept (retention)   100.0%       across sides it IS the sign of the move. Zero information.
#   So `kept` and `conv` went, and `pas` failed a held-out test: flipped it is a real effect (60.0% / 66.9% in
#   two independent halves, p 5e-06 and 1e-14) but ADDING it to aggression scored -4.8 pts on one half and
#   +1.7 on the other -- equal-weighting a 63% signal with a 68% one only dilutes. The book stays OUT of the
#   score; the inversion is kept as knowledge (see [[buyer-seller-score]] in memory).
# ⚠ What remains is therefore NOT a composite. It is the flow ratio on a bounded 0..100 scale -- easier to read
#   at a glance than the FLOW RATIOS pane's log axis, but the same information. Do not describe it as more.
# ⚠⚠ DESCRIPTIVE AND COINCIDENT. "The disagreements are informative" was tested and is NULL: after a divergence
#   the next cycle goes the flow's way 51.0% of the time (177/347, p=0.747), split-half 49.2% / 53.0%, and the
#   divergence adds -0.012 over simply knowing the cycle's own direction. No forward claim, here or anywhere.
SCORE_SIZE_EXP = 0.55           # swept: takes rho(aggression, cycle $) from +0.65 to -0.02 / +0.01
SCORE_LOW = 32.0                # measured terciles of the ONE-component score (a percentile, so near 33 / 67)
SCORE_HIGH = 70.0
SCORE_MIN_PARTS = 1             # one component: a side either has a reading or it has none
# ⚠ The score had a PANE of its own; it was DROPPED 2026-09-16 at the user's request ("I have a better
# idea"). What is left is the per-cycle Score line in the INTEREST x IMPACT click panel, which is the only
# consumer of _score_parts. If nothing ever needs it again, that line and _score_parts go together.
#                                 because a fixed light grey was invisible on the light canvas (#ffffff):
#                                 measured contrast 39 of 765, i.e. drawn but unseeable (2026-09-16)
# --- the Buy/Sell Flow ($) PANE itself gets a toggle (user 2026-09-15: "we dont have it", then "I want the
# whole chart to hide not just the lines"): in Flow mode the main chart IS that pane, so OFF hides the main
# chart widget and the stack closes up around it. Display only -- the bins, the crossings, every cycle pane
# and the feed keep reading the same store; leaving Flow mode always shows the chart again.
FLOW_PANE_ON = True


# --- PRICE pane, ABOVE the flow lines (user 2026-09-12) ---------------------------------------------------
PX_PANE_ON = True               # the price track over the same clock as the flow lines
PX_MAX_POINTS = 2400            # points after decimation. MIN/MAX per bucket, so this is 2 per pixel column
                                # at a 1200 px pane -- the envelope survives, which plain striding would eat.
COLOR_PRICE_LINE_DARK = "#e8eaed"   # the price line carries no side, so unlike the teal/red flow lines
COLOR_PRICE_LINE_BW = "#000000"     # it has to follow the ground: light on the dark canvas, black on Simple BW
PX_MKT_BTN_W = 78               # the PRICE pane's BUY / SELL pair (Flow mode): button width x height in px, its
PX_MKT_BTN_H = 26               # font and the gap between the two. Smaller than the candle chart's 122 x 42 /
PX_MKT_FONT_PX = 11             # 14 px (user 2026-09-21: "make the buy/sell buttons smaller on the price chart")
PX_MKT_GAP = 8                  # -- that pane is a fraction of the chart's height and the pair sat on its candles.
PX_CANDLE_FILL = 0.72           # body width as a fraction of the CYCLE's own duration (the rest is the gap,
                                # so back-to-back cycles still read as separate candles)
# ⚠ there is deliberately NO pixel floor on the body width. One was tried: at a 20 h view it made every
# body 663 s wide against a ~77 s median cycle, so 400 bodies OVERLAPPED into a solid smear -- which
# misrepresents the data far worse than a thin body does. A body narrower than a pixel still shows, because
# its border pen is cosmetic; and the wick carries the range either way.
PX_RECALC_SECS = 0.25   # live-tape rebuild cap. A candle QPicture over ~80 bodies costs ~0.8 ms against the
                        # price line's 0.03 ms, and at the 20 Hz tick rate that would be 1.5% of a core to
                        # redraw bodies that have not moved. A VIEW change (zoom, pan) bypasses this entirely,
                        # so interaction stays instant; only the live edge is paced.
PX_WICK_HILITE_W = 2.8          # stroke width for the highlighted REJECTION wick of an absorbed candle
                                # that closed against the absorbed side -- the wick IS the reading there, so it
                                # is drawn heavier than the 1.0 px the ordinary wicks use
PX_PAD_FRAC = 0.06              # y padding above and below the visible high/low
PX_REFIT_FRAC = 0.18            # dead-band: re-fit y only when the visible high/low moves by this much of the
                                # current range, so the axis does not wobble on every 20 Hz tick

PANE_TITLE_LIQ = "LIMIT ORDERS" + "  ·  " + "resting bid / ask $"
PANE_TITLE_CYC = "CYCLE IMPACT" + "  ·  " + "ticks per %s" % FLOW_CROSS_BADGE_UNIT_TXT
PANE_TITLE_CVOL = "CYCLE VOLUME" + "  ·  " + "dominant side vs last %d" % CVOL_BASE_N
PANE_TITLE_LOB = "CYCLE BOOK" + "  ·  " + "bid / ask vs last %d" % LOB_BASE_N
PANE_TITLE_SPD = "CYCLE SPEED" + "  ·  " + "ticks/s vs last %d" % SPEED_BASE_N
PANE_TITLE_INTERP = "INTERPRETATION" + "  ·  " + "one row per cycle"

# Pane names, top-left. Muted on purpose -- they label the pane, they are not part of the reading.
PANE_TITLE_COL = "#7d8492"
PANE_TITLE_PT = 8
PANE_TITLE_GUARD_PX = 130       # a top-anchored badge row starts after the name instead of under it
FLOW_CROSS_DASH_PX = 9.0            # dash length / gap, in PIXELS. The dashes are emitted as segments rather
FLOW_CROSS_GAP_PX = 7.0             # than left to a dashed pen: Qt's dasher measured ~100x more expensive.
# A cross that held its side for MIN_HOLD_SECS but never reached MIN_SPREAD_PCT is still a cycle, just a weak
# one -- it gets a GRAY line instead of a coloured one (user 2026-09-10) rather than being dropped.
FLOW_CROSS_WEAK_COL = "#8a919c"

# --- Resting-liquidity pane (Buy/Sell Flow mode, user 2026-09-09): limit-order $ within +-N ticks of mid.
# --- Cycle pane (Buy/Sell Flow, user 2026-09-10). A cycle = a run where one side owns the smoothed flow.
CYCLE_PANE_ON = True
                                # so those blocks are drawn HOLLOW and can never read as strong
                                # plus ~0.003 ms/point: 3000 pts = 13.0 ms/paint (32% of a core), 900 = 7.4,
                                # 600 = 6.0, 400 = 5.4. At ~1500 px this is a point per 2.5 px -- still a
                                # smooth line, and 3000 was 2 points PER PIXEL, i.e. invisible detail.
CYCLE_RECALC_SECS = 0.5         # the store re-keys on every live batch; a full 72 h rebuild is ~17 ms
# What that much dominant volume NORMALLY buys, in ticks -- the MEASURED quintile curve, interpolated.

LIQ_PANE_ON = True              # the pane itself (hamburger 'Flow' -> 'Limit orders pane'); persisted
LIQ_RADIUS_TICKS = 100          # default half-width, in TICKS (0.01 -> +-$1.00)
LIQ_RADIUS_CHOICES = (10, 25, 50, 100, 200)     # must be a SUBSET of depth_store.LIQ_RADII (one response, no refetch)
LIQ_SMOOTH_SECS = 60            # rolling MEAN over the lines (the book is a level, not a flow); 0 = raw
LIQ_SMOOTH_CHOICES = (0, 30, 60, 300)
LIQ_MAX_COLS = 900              # columns requested per window (payload ~52 KB; the reduction is the real cost)
LIQ_COL_SECS = 15               # seconds per column: the source is the ~30 s snapshot cadence, so a column per
                                # PIXEL just rasterises duplicates (profiled +25 ms/frame at 900 columns)
LIQ_REQ_DEBOUNCE_SECS = 0.35    # settle time after a pan/zoom before asking for a new window

FLOW_MAX_POINTS = 3000          # decimation ceiling per curve (a 1920-px chart can't resolve more)

# --- Volume Burst badges (m10_burst, user 2026-09-08): one side >= BURST_X the other over BURST_WINDOW_SECS,
# read off the SAME flow bins; the badge shows the strongest multiple reached inside each candle.
BURST_X = 2.0                   # default "at least x2"
BURST_X_CHOICES = (1.5, 2.0, 3.0, 5.0)
BURST_WINDOW_SECS = 60          # default rolling window (== the tablet gauge)
BURST_WINDOW_CHOICES = (30, 60, 120)   # 10 s is degenerate on this instrument: one side is often literally $0
BURST_FLOOR_PCT = 90.0          # a burst window must be busier than this percentile of the windows on screen
#                                 (self-scaling: no absolute $ knob, and the badge marks the standouts, not every bar)
BURST_CAP = 50.0                # displayed multiple ceiling (a near-empty other side would read as infinity)
BURST_BACKFILL_SECS = 21600     # ONE chunk of history (== the BP chunk size); the layer walks back in chunks
FLOW_HISTORY_SECS = 259200   # 72 h -- the daemon's own tape retention. At 24 h a Scan Start
                             # further back than a day left the view's left side permanently
                             # empty (measured: -30 h filled 24.0 h = 80% of the view)       # ... until the bins cover this much of the LIVE edge (replay targets its own range)
FLOW_BF_CHUNK_SECS = 7200      # 2 h per history chunk: the window fills PROGRESSIVELY instead of waiting on
#                                one 6 h transfer (~300k trades) before anything shows
FLOW_BF_SPACING_SECS = 4.0      # min seconds between chunk requests (only ONE is ever in flight anyway) -- the
#                                 daemon serves each from SQLite on a shared core, so stay gentle

# --- The PRICE pane covers the WHOLE view (2026-09-14). The shared crosses() read returns the NEWEST
# FLOW_CROSS_MAX cycles only (the last ~8 h at the 77 s median), so a two-day view stopped there; the cache
# now grows to PX_CACHE_MAX (3,600 cycles ~ the daemon's 72 h of tape at the 77 s median) through FILL READS -- one extra read per tick over the
# next PX_FILL_SPAN_SECS left of the oldest cached cycle until the view is covered (~6 ticks for 48 h) --
# and the picture holds the whole cache in ~96-candle strips (a tail rebuild per closed cycle, ~1-3 ms).
# MEASURED on the pane: 1,222 candles paint in 4.2 ms full / 0.16 ms live-edge sliver; a 48 h cold cross
# read is ~30 ms (9 ms with the bounded scan), its memo hit 12 us. Worst full repaint = the whole cache on
# screen (a 72 h view): ~15 ms, paid once per pan step at that zoom, never per live tick.
PX_FILL_SPAN_SECS = 6 * 3600.0
# --- The flow bins PERSIST: data/flow_bins.npz (~12 MB at 72 h; save 21 ms on a thread, load 23 ms), so a
# relaunch opens with the whole tape instead of walking 24+ chunks back through the daemon, and the backfill
# only fills the gap since the last save. The chunk spacing adapts to the daemon's measured round trip.
FLOW_BINS_FILE = "flow_bins.npz"
FLOW_SAVE_SECS = 120.0
FLOW_BINS_MAX_AGE_SECS = 72 * 3600.0     # older than the daemon's own tape -> ignored
FLOW_BF_SPACING_MIN_SECS = 0.5           # the pump waits max(this, the last chunk's round trip), capped by FLOW_BF_SPACING_SECS

# ---------------------------------------------------------------------------- HLH Volume Profile (m10_hlh)
# The user's TradingView indicator ported to both canvases (study/pine/hlh_volume_profile.pine, spec in
# study/pine/HLH_VOLUME_PROFILE_SPEC.md). The engine is app/hlh_profile.py; the drawing is app/hlh_draw.py; the
# 1-minute (day) / 5-minute (week) candles come from Binance REST klines (app/hlh_feed.py) -- the same source
# TradingView charts for this symbol, so the volume is CONTRACTS and the profile matches what the user validated.
HLH_DAYS = 2                    # complete DAY periods on the chart when the layer is on (yesterday + today): the
                                # Zero Point is pulled back to 00:00 of the oldest one ("automatically load 2 days")
HLH_TZ = "UTC"                  # days and weeks are cut in this zone (the Pine default; IANA name)
HLH_DAY_TF = "1m"               # intrabar resolution of the day profile
HLH_WEEK_TF = "5m"              # ... and of the week profile (a week of 1m is 10,080 candles)
HLH_ROWS = 60                   # price rows of a profile
HLH_WIDTH_PCT = 28              # profile width, % of the period's x span
HLH_LOW_MAX_PCT = 50.0          # LOW must be < this % of the POC
HLH_HIGH_MIN_PCT = 50.0         # HIGH must be > this % of the POC
HLH_SHARED_PCT = 66.0           # a LOW shared by 2 Ds turns red above this % of an apex (then merges)
HLH_MERGE = True                # merge the 2 Ds of a red LOW
HLH_UNCOVERED = True            # purple HIGH + U-D in every area no D covers
HLH_MAX_DS = 0                  # 0 = until no HIGH is left
HLH_VA_PCT = 70.0               # value area % of each bloc
HLH_TP_BIN_DAY_MIN = 30         # time-profile bin, day
HLH_TP_BIN_WEEK_MIN = 240       # ... week
HLH_TP_BOTH = "Keep both"       # MAX-time != MAX-volume: "Keep both" or "Keep neither"
HLH_NUM_LOWS = True             # number the LOWs each leg walks through
HLH_SHOW_LVL = True             # each D's end levels to the period end
HLH_SHOW_TP = True              # time profile inside each D area
HLH_TP_BLOCS = True             # label each bloc (time + volume)
HLH_TP_TOTAL = True             # label the D area total
HLH_TP_PEAK = False             # label the busiest bin
HLH_SHOW_VA = True              # VAH / VAL of each bloc (the Block Lines)
HLH_SHOW_POC_RUNS = False       # ⚠ REMOVED 2026-09-23 at the user's word, from the tablet (7035ba2) and then the
                                # terminal: the menu option is gone and every canvas builds with poc_runs=False.
                                # bloc_poc_runs() and its drawing branch are kept as library code. Was: shade a
                                # run of >= HLH_POC_RUN_MIN klines that CLOSED above (or all below)
#                                 a bloc's POC -- acceptance on one side of it. Only a CLOSE on the other side
#                                 divides a run (user 2026-09-20: the open plays no part, and a kline outside
#                                 the bloc's price band still counts by its close). The area spans the run's
#                                 time and runs from the POC out to the run's furthest price.
HLH_POC_RUN_CAUSAL = True       # the user's exception: a kline that closed across the POC AS IT WAS when the
#                                 kline formed (the bloc's POC from its candles up to then) divides too, even
#                                 if the POC has since moved past its close -- that division was real then.
#                                 This is the DEFAULT of the hamburger sub-toggle 'm10_hlh_pocruns', which is
#                                 what actually gates the drawing; it persists with the rest of the menu.
HLH_POC_RUN_MIN = 5             # how many candles in a row make a group (the user's number)
HLH_POC_RUN_TR = 88             # its fill transparency (0 = opaque, 100 = invisible): a wash, not a block
HLH_SHOW_BLOC_POC = True        # the bloc's POC: the row holding most of its OWN volume, drawn in the bloc's
#                                 colour, thin and SOLID. Built with the period's own spreading rule so the two
#                                 cannot disagree. (Replaced a high/low MIDLINE, 2026-09-16.)
HLH_D_WIDTH = 2                 # D line width (px)
HLH_LVL_WIDTH = 3               # level line width (px)
HLH_VA_WIDTH = 1                # block line width (px; 2 in Block Lines Only)
HLH_DIM_TR = 80                 # transparency of the LOWs / HIGHs a D used up
HLH_TP_TR = 75                  # time-profile fill transparency (kept blocs)
HLH_TP_DIM_TR = 90              # ... of the other blocs
HLH_POLL_SECS = 10.0            # REST poll of the forming candles (limit 5 rows = weight 1)
HLH_RECALC_SECS = 1.0           # the forming period is recomputed at most this often (and only on new data)
# -- the chain: merges 3 / 4 compare a finished day with the day before it (and older days a merged bloc reaches),
# and the bloc colours rank each bloc against the HLH_VOL_LOOK blocs before it, whatever their day. So the feed
# loads HLH_HIST_DAYS day periods (the Pine's "Days to draw", 10 there) and the engine folds them oldest -> newest
# exactly as the Pine feeds them; only the last HLH_DAYS are DRAWN. 5 days of 1m = 7,200 rows = 5 REST pages.
HLH_HIST_DAYS = 5               # day periods computed (>= HLH_DAYS)
HLH_HIST_WEEKS = 3              # week periods computed (this week + 2 finished ones); only this week is drawn
# -- Merges (the Pine's "Merges" group): the rules that build the FINAL blocs, run again and again until nothing
# is left to merge. They change the blocs on the chart AND in the tables.
HLH_MERGE_BLOCS = True          # merge 1: time-overlapping blocs, high-low >= HLH_INSIDE_PCT % inside -> the lower volume joins the higher
HLH_INSIDE_PCT = 50.0
HLH_D_MERGE = True              # merge 2: same D, VAH-VAL >= HLH_D_MERGE_PCT % inside -> collage of ONLY their candles
HLH_D_MERGE_PCT = 50.0
HLH_DAY_MERGE = True            # merge 3 (finished days only): day N + day N-1 blocs OVERLAPPING >= HLH_DAY_MERGE_PCT % of the smaller VAH-VAL range
HLH_DAY_MERGE_PCT = 50.0
HLH_INS_MERGE = True            # merge 3: ... or one INSIDE the other, the smaller range >= HLH_INS_MERGE_PCT % of the bigger
HLH_INS_MERGE_PCT = 50.0
HLH_EDGE_MERGE = True           # merge 4 (finished days only): blocs sharing a day, one's VAL within HLH_EDGE_TICKS ticks of the other's VAH
HLH_EDGE_TICKS = 1
HLH_MERGE_SPAN = "36h"          # a merged bloc spans at most this (first candle -> last close; x 7 for weeks): "12h", "24h",
                                # "36h", "48h", "72h", "96h", "1 week", or "No merge (day N alone)" = nothing from earlier days
HLH_MERGE_SPAN_CHOICES = ("No merge (day N alone)", "12h", "24h", "36h", "48h", "72h", "96h", "1 week")
                                # ... the hamburger dropdown under the HLH layer (the Pine's input, persisted hlh_merge_span)
# -- Bloc colours (VAH / VAL of the final blocs): rank = % of the previous HLH_VOL_LOOK blocs with a LOWER volume
HLH_VOL_LOOK = 20
HLH_GOLD_PCT = 69.0             # rank above -> orange, 4 px
HLH_GRAY_PCT = 29.0             # rank at or below -> dark gray, 2 px; between -> its D colour (never orange / gray / the last coloured bloc's), 3 px
HLH_C_GOLD = "#FF9800"
HLH_C_GRAY = "#505050"
# -- the outer value area: a second, wider VA of the same bloc, dashed 1 px in the bloc's line colour
HLH_SHOW_VA2 = True
HLH_VA2_PCT = 90.0
# -- Tables (display only; the terminal's sub-toggle "Tables" gates all three): under the day's low, left edge at midnight
HLH_TABLE1 = True               # every final bloc by volume (lowest -> highest)
HLH_TABLE2 = True               # blocs per D
HLH_TABLE3 = True               # day N vs day N-1 (finished days only)

TAPE_BACKFILL_SECS = 300        # Trades scanner mode: history window requested on entry (raw aggTrades from
                                # trade_tape; ~5 min fills the table instantly without a heavy tunnel transfer)
# Big Player Levels overlay (m10_bigplayer, user 2026-09-04): a SINGLE executed print >= BIGPLAYER_MIN_USD draws a
# BUBBLE at its bar + price, sized by the print's USD amount (log, 12..46 px), amount centred on it; same bar + price
# + side prints merge into one summed bubble. Individual tape prints (aggTrade), NOT the per-level candle bubbles.
# Prints >= BIGPLAYER_STORE_FLOOR_USD are retained so the slider can move without a re-backfill; deeper history comes
# from study/bigprint_archive (replay). Most recent BIGPLAYER_MAX_LINES bubbles drawn.
BIGPLAYER_MIN_USD = 500_000.0
BIGPLAYER_STORE_FLOOR_USD = 50_000.0
BIGPLAYER_MAX_LINES = 400      # user 2026-09-09: bubbles/diamonds stopped part-way back; both caps raised
#                                to cover a full screen of bars (they keep the MOST RECENT N, so a low cap truncates history)
# SWEEPS (user 2026-09-06): one taker order eating through the book = aggTrade prints with the SAME millisecond +
# side at >= BIGPLAYER_SWEEP_MIN_LEVELS distinct prices. Grouped before the store floor, summed; the group is kept
# when its total >= BIGPLAYER_STORE_FLOOR_USD and shown when >= the Big Player $ slider. Most recent
# BIGPLAYER_SWEEP_MAX drawn (each = capsule + end-level line + label).
BIGPLAYER_SWEEP_MIN_LEVELS = 2
BIGPLAYER_SWEEP_MAX = 400
BIGPLAYER_LABEL_MAX = 60       # only the newest N marks carry the $ text: every label is re-drawn on each
#                                crosshair move, and 160+ amounts overlap into noise anyway (2026-09-09)      # was 40 = HALF the bubble cap, which is why diamonds ran out first
# CONTINUITY (2026-09-07): the live store is JOURNALED (data/bigprint_journal.jsonl) and backfilled from the daemon's
# tape for exactly the gap since the newest journaled print, in chunks, up to the tape's retention (72 h); the
# current month's big-print archive refreshes itself from Binance's daily dumps every few hours.
BIGPLAYER_BACKFILL_HOURS = 72         # == DEPTH_RETENTION_HOURS (the daemon's trade tape)
BIGPLAYER_BACKFILL_CHUNK_SECS = 21600 # one trades_window request per 6 h of gap (~70k raw trades each)
BIGPLAYER_JOURNAL_HOURS = 72          # what the journal keeps / reloads (older bars come from the archive)
BIGPLAYER_ARCHIVE_REFRESH_SECS = 21600  # rebuild the current month from the daily dumps when its file is older than this
# CAMPAIGNS (user 2026-09-06 as BURSTS, 2026-09-07 as CAMPAIGNS): same-side prints / sweeps within this many ms of the
# previous same-side one are ONE player working the book -> one diamond, totals summed (drawn like the atomic sweeps).
BIGPLAYER_CAMPAIGN_MS = 30      # CAMPAIGN window (2026-09-07, "the 12:01:08 fight"): same-side big-player events within
                                # 30 ms of the previous same-side event are ONE player, other side in between and price
                                # reversals between orders allowed. Raw aggTrade study (study/campaign_gap_study.py, 35 h):
                                # same-side inter-order gaps are 5x background at 2-3 ms, 1.6x at 6-10 ms, a quiet trough
                                # (0.4-0.9x) at 11-100 ms, then the market's normal cadence from 100 ms. Replaces
                                # BIGPLAYER_BURST_MS (1 s -> 10 -> 2 -> 1 ms the same day; the 1 ms + monotonic ORDER rule
                                # lives on in bigprint_store.group_sweeps and the tablet's drop-down).
DOM_VP_BACKFILL_SECS = 21600    # DOM scanner mode: executed-trade history for the ladder's Volume Profile —
                                # 6h covers every VP window choice (5M..6H filter locally, no re-requests).
                                # ~60-80k trades ≈ 3MB b64 one-shot on entry (well under trade_tape's 72h)
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
# CAUSAL HISTORY REPLAY (app/radarrun_causal.py, 2026-09-04): the batch detect the chart draws from is NOT causal — the
# wall layer re-evaluates with later bars and erases ~69% of at-close 30m fires at the NEXT bar. History the terminal
# never watched live is therefore re-detected close-by-close in a lowest-priority background process and unioned into
# the persisted fired record, so replay/reload show what the live chart showed at each bar's close.
RR_CAUSAL_ON = True             # master switch for the background causal backfill
RR_CAUSAL_WARM = 2000           # trailing bars each per-close detect sees (== the canonical study harness window)
RR_CAUSAL_MIN_WARM = 300        # earliest close (index into the loaded history) that gets replayed: less context = unfaithful
RR_CAUSAL_CHUNK = 400           # closes per background job (~0.2 s/close on 30m); results land progressively
RR_FIRED_MAX = 20000            # persisted fires kept per tf (was 5000; the backfill needs room for 18 months of 30m)
REHYDRATE_LIMIT = 1440          # main.py:248 — last 24h of entries per tf (legacy replay)
SAVE_INTERVAL_SECS = 15         # main.py:286 — periodic footprint flush (legacy JSON)
SYNC_INTERVAL_SECS = 10         # async SQLite upsert cadence (replaces JSON flush)
CATCHUP_CHUNK_SIZE = 1000       # closed buckets per CATCHUP_CHUNK frame (legacy monolithic size; see ENCODE_CHUNK)
CATCHUP_ENCODE_CHUNK = 100      # 2026-09-06: buckets / clock candles encoded per frame on the daemon loop, with a
#                                 sleep(0) between frames -> the 150 ms live edge keeps flowing during a catch-up /
#                                 clock resync (json.dumps holds the GIL for its whole call: 1000-bucket frames were
#                                 multi-second freezes of price / DOM / tape in EVERY window)
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
# AGGTRADE GAP FILL (2026-09-06, feeds.MarketDataCore._agg_fill_gap): the @aggTrade stream is the ONLY source of the
# volume buckets, the clock engines and the trade tape. Every message it missed used to be a PERMANENT hole: a
# slow-consumer disconnect during the 15:01 UTC flash-dip burst (13.8k trades/min) + the restart 60 s later erased
# the 104.80 low from every bucket tf (5m bucket 15:01-15:06 carried 52k SOL vs 329k on the official candle).
# aggTrade ids are contiguous per symbol, so a gap is DETECTABLE (a > last+1) and REST (fromId pagination, weight
# 20, ~2-day horizon) refills it EXACTLY and IN ORDER before the next live trade is routed. The last routed id is
# persisted with every engine snapshot (meta agg_last_id/agg_last_ms) -> a restart resumes the tape where the
# persisted state ends (boot replay BEFORE listening). The websocket buffer is raised so a loop stall never pushes
# back on Binance; a fill that fails is logged as a hole (never retried per trade, never silent).
AGG_WS_MAX_QUEUE = 65536        # client-side message buffer (~5 min of burst tape) instead of TCP backpressure
AGG_FILL_PACE_S = 0.6           # sleep between REST pages (~100 req/min = 2000 weight/min of the 2400 ceiling)
AGG_FILL_MAX_REQ = 2400         # per fill, 1000 trades each (~10 h of tape); beyond -> logged hole
AGG_REPLAY_MAX_S = 6 * 3600     # resume points older than this are not replayed (logged hole; REST horizon ~2 d)
AGG_FILL_YIELD_EVERY = 2000     # replayed trades routed between event-loop yields (the live edge keeps flowing)
DEPTH_HIST_URL = (
    "https://data.binance.vision/data/futures/um/daily/klines/"
    "SOLUSDT/{tf}/SOLUSDT-{tf}-{date}.zip"
)
OI_POLL_SECS = 5                # main.py:629
PULSE_BROADCAST_SECS = 0.4      # main.py:877 — DOM/OI pulse cadence
TRADES_LIVE_SECS = 0.15         # live trade-batch push cadence (2026-09-07: was the 0.4 s pulse; the tape / DOM
                                #   last price on every client now ticks like the chart's LIVE_EDGE_SECS edge)
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
