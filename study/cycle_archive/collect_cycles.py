# -*- coding: utf-8 -*-
"""CYCLE ARCHIVE -- one harvest of everything the cycle chart knows, before the daemon's 72 h window rolls past it.

WHY (user 2026-09-21: "I would like to run some strategy testing -- do I have enough data on the cycle chart?"): the
cycle chart is rebuilt from the daemon's ROLLING 72 h of tape and order book, and nothing older holds either. The
impact half of interest x impact needs the recorded BOOK (the far side's resting wall at each cycle's open), which no
exchange publishes as history -- so the numbers the terminal shows can only be kept by SAVING them as they go by.
72 h hold ~75 Takeover signals; a test needs hundreds, across regimes. This script is how they accumulate.

WHAT ONE HARVEST HOLDS (one immutable .npz per run, never rewritten -- merging is the loader's job):
  * cycles   every cycle of the store, from an UNCAPPED crosses() read: start, end, side, strength, move, both sides'
             taker $, open / high / low / close
  * iimp     the INTEREST x IMPACT pane's OWN numbers per rated cycle -- harvested from the pane while the view is walked
             across the tape in 3 h steps, so every value is the one the terminal draws (its baselines, its climb model,
             its book wall), never a re-implementation: both sides' I x I, their previous bar's, the interest ratios, the
             impact score, leader, wall, reach, move, kept, the vacuum / quiet read, the buyer / seller scores
  * colours  each cycle candle's cached STATE colour on the PRICE pane (breakout buy / sell, absorbed, neutral)
  * badges   the TAKEOVER marks exactly as the terminal drew them -- the terminal's own signal record -- and the same rule
             recomputed here from the harvested numbers, with the two compared (gate 1 of the honest-test gates)
  * walls    both sides' resting $ at every cycle's open, and the book means over it, so an impact score can be
             recomputed later even under a changed rule
  * bins     the store's 1-second bins: buy $, sell $, last / high / low price -- the price path for first-touch TP / SL
             resolution, finer than the 1 m the gates ask for
  * meta     the rule's constants, the lookback, the flow window, the commit, the walk's log

IT CHANGES NOTHING: a copy of terminal_ui.json in a temp DATA_DIR, an offscreen window, the daemon read over the same
tunnel the terminal uses (opened here only if it is not already up, and then closed again). Output goes to
study/cycle_archive/data/ and, unless --no-upload, to gs://smc-quant-archive/solusdt/cycles/ .

Run it at least every 2 days (the I x I rows start one lookback into the window, so a harvest covers ~68 h):
    python study/cycle_archive/collect_cycles.py            # harvest + upload
    python study/cycle_archive/collect_cycles.py --no-upload
Exit code 0 = saved (and uploaded), 2 = no daemon, 3 = the store never filled, 1 = anything else."""
import os, sys, time, json, shutil, tempfile, subprocess, argparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
ap = argparse.ArgumentParser()
ap.add_argument("--no-upload", action="store_true", help="keep the harvest local only")
ap.add_argument("--step-hours", type=float, default=3.0, help="how far each view of the walk advances")
ap.add_argument("--overlap-mins", type=float, default=60.0, help="how far each view reaches back into the one before it")
ap.add_argument("--hours", type=float, default=0.0, help="harvest only the newest H hours (0 = the whole retention); "
                                                         "a light run for checking the collector, never for the archive")
ARGS = ap.parse_args()

from app import config                                        # noqa: E402
TMP = tempfile.mkdtemp(prefix="cycarch_")
_ui = os.path.join(REPO, "data", "terminal_ui.json")
if os.path.exists(_ui):
    shutil.copy(_ui, os.path.join(TMP, "terminal_ui.json"))   # the user's lookback / flow window, read-only
config.DATA_DIR = TMP
import numpy as np                                            # noqa: E402
from PySide6 import QtWidgets                                 # noqa: E402
qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from app import terminal as _term                             # noqa: E402
from app.terminal import MinimalTerminalWindow                # noqa: E402
from app.flow_interp import BREAK_BUY_COLS, BREAK_SELL_COLS   # noqa: E402  (the bright contra pair are breakouts too)

GCS = str(getattr(config, "ARCHIVE_GCS", "gs://smc-quant-archive/solusdt")).rstrip("/") + "/cycles/"
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
T0 = time.time()
hms = lambda x: time.strftime("%m-%d %H:%M", time.localtime(x))


def log(msg):
    print("[%6.1fs] %s" % (time.time() - T0, msg), flush=True)


def spin(sec):
    e = time.time() + sec
    while time.time() < e:
        qapp.processEvents(); time.sleep(0.004)


def finish(code, tunnel=None, w=None):
    try:
        if w is not None:
            w.close(); qapp.processEvents()
    except Exception:
        pass
    try:
        if tunnel is not None:
            tunnel.stop()                                   # kills ONLY a tunnel this run launched
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)


# ------------------------------------------------------------------ the daemon
tunnel = _term.SSHTunnelManager()
if not _term._ipc_port_open():
    log("no tunnel on %s:%s -- opening one" % (config.IPC_HOST, config.IPC_PORT))
    tunnel.ensure()
    _tw = time.time()
    while not _term._ipc_port_open() and time.time() - _tw < 60.0:
        time.sleep(1.0)
w = MinimalTerminalWindow("5m"); w._rr_persist_save = lambda tf: None
w.resize(1600, 1000); w.show()
spin(8.0)
_tw = time.time()
while not w.worker.connected and time.time() - _tw < 45.0:
    spin(1.0)
if not w.worker.connected:
    log("NO DAEMON -- nothing harvested"); finish(2, tunnel, w)
w._set_scanner("flow"); spin(6.0)
# this session's own toggles (the temp UI state -- the user's are untouched): the PRICE and I x I panes and Takeover on,
# the HLH layer off (its klines feed is not needed here)
for _nm, _on in (("px_on", True), ("iimp_on", True)):
    _cb = getattr(w.menu, _nm, None)
    if _cb is not None and _cb.isChecked() != _on:
        _cb.setChecked(_on)
for _k, _on in (("cyc_takeover", True), ("m10_hlh", False)):
    _cb = w.menu.layer_checks.get(_k)
    if _cb is not None and _cb.isChecked() != _on:
        _cb.setChecked(_on)
try:
    w._iimp_combo.setCurrentIndex(0)
except Exception:
    pass
spin(3.0)
# THE WHOLE WINDOW, or the archive has holes. The store backfills newest-first in chunks, one in flight at a time, and
# a chunk can take the daemon the better part of a minute: the first harvest waited for "the start stopped moving for
# 25 s" and walked away with 44 h of a 72 h tape. The terminal's OWN queue says when it is done -- nothing queued,
# nothing in flight -- and the wait is on that, with the plan re-made for the full retention if the window ever
# planned less (in Flow mode it plans for what is on screen).
_want0 = time.time() - float(config.FLOW_HISTORY_SECS) + 900.0
_short = float(ARGS.hours) > 0.0
if _short:
    # a CHECKING run: keep the daemon's work to the chunks this window needs (the read-back included)
    _cut = time.time() - (float(ARGS.hours) * 3600.0 + float(w._lb_secs()) + 5400.0)
    _tq = time.time()
    while not w.__dict__.get("_flow_bf_queue") and time.time() - _tq < 30.0:
        spin(0.5)
    w._flow_bf_queue = [c for c in (w.__dict__.get("_flow_bf_queue") or []) if float(c[1]) > _cut]
    _want0 = _cut
_tw = time.time(); _last_log = 0.0; _idle_since = None
while time.time() - _tw < 1500.0:
    spin(1.0)
    _sp = w._flow.span(); _s0 = None if _sp is None else float(_sp[0])
    _busy = bool(w.__dict__.get("_flow_bf_queue")) or w.__dict__.get("_flow_bf_inflight") is not None
    if not _busy:
        if (not _short) and _s0 is not None and _s0 > _want0 + 3600.0 and (_idle_since is None or time.time() - _idle_since > 20.0):
            try:
                w._flow_bf_plan(0.0, 0.0)                   # ask for the full retention, newest first
            except Exception:
                pass
            _idle_since = time.time()
            continue
        if _idle_since is None:
            _idle_since = time.time()
        elif time.time() - _idle_since >= 8.0:             # idle, and it stayed idle: the backfill is complete
            break
    else:
        _idle_since = None
    if time.time() - _last_log > 30.0:
        _last_log = time.time()
        log("backfilling: store starts %s, %d chunk(s) queued" % ("-" if _s0 is None else hms(_s0), len(w.__dict__.get("_flow_bf_queue") or [])))
sp_ = w._flow.span()
if sp_ is None or (float(sp_[1]) - float(sp_[0])) < 6 * 3600.0:
    log("the store holds %s -- too little to harvest" % (None if sp_ is None else "%.1f h" % ((sp_[1] - sp_[0]) / 3600.0)))
    finish(3, tunnel, w)
A, B = float(sp_[0]), float(sp_[1])
N_LB = int(w._lb_n()); LB = float(w._lb_secs())
log("store %s -> %s (%.1f h) | lookback N = %d (%.1f h read-back) | flow window %d s" % (hms(A), hms(B), (B - A) / 3600.0, N_LB, LB / 3600.0, int(w._flow_win)))

# ------------------------------------------------------------------ 1) every cycle, uncapped
XARGS = (float(w._flow_win), float(config.FLOW_CROSS_MIN_SPREAD_PCT), float(config.FLOW_CROSS_MIN_HOLD_SECS),
         10 ** 6, float(config.FLOW_CROSS_CONTEXT_SECS), float(config.TICK_SIZE))
t, is_buy, strong, move, cbuy, csell, t_end, done = w._flow.crosses(A, B, *XARGS)
px0, px1 = w._flow.crosses_px(A, B, *XARGS)
pxh, pxl = w._flow.crosses_hl(A, B, *XARGS)
log("cycles %d (%d finished)" % (int(t.size), int(np.sum(done))))

# ------------------------------------------------------------------ 2) the panes' own numbers, view by view
F_NUM = ("x0", "x1", "liib", "liis", "pliib", "pliis", "arb", "ars", "score", "wall", "reach", "mv", "kept", "mult", "sbuy", "ssell")
F_FLAG = ("up", "contra", "good", "vac", "quiet")
rows = {}; colours = {}; badges = {}; steps = []
STEP = float(ARGS.step_hours) * 3600.0
# ⚠ THE VIEWS OVERLAP. A cycle that straddles a view's right edge is in that view's pane rows but NOT in the PRICE
# pane's candle cache yet, so the badge pass has no candle to join it to: the first full harvest drew 66 marks where the
# rule gives 67, and the missing one sat across a boundary (as did all 15 pane rows that came back without a colour).
# Each view therefore reaches back into the one before it, far enough to hold any such cycle whole; what a later view
# says about the overlap replaces what the earlier one said.
OVER = float(ARGS.overlap_mins) * 60.0
A0 = A + LB + 600.0
if _short:
    A0 = max(A0, B - float(ARGS.hours) * 3600.0)
a = A0
while a < B - 60.0:
    b = min(a + STEP, B + 120.0)
    va = max(A0, a - OVER)                                   # the view's own left edge
    w._flow_follow = False; w._flow_last_set = (va, b)
    w.vb.setXRange(va, b, padding=0.0)
    _tw = time.time(); got = False
    while time.time() - _tw < 120.0:                        # the BOOK window for this view (the walls)
        spin(0.5)
        d = w.__dict__.get("_liq_data")
        if d is not None and float(d[0]) <= va + 0.03 * (b - va) and float(d[1]) >= min(b, time.time()) - 0.30 * (b - va):
            got = True
            break
    _t_book = time.time() - _tw
    spin(1.0)
    now = time.time()
    w._iimp_t = 0.0; w._iimp_sig = None; w._iimp_tick(now)
    L = w.__dict__.get("_iimp_last")
    if L is not None and not bool(L.get("vac_on", False)):   # a dict built before the badge asked for the vacuum read
        w._iimp_t = 0.0; w._iimp_sig = None; w._iimp_tick(time.time()); L = w.__dict__.get("_iimp_last")
    # the PRICE pane's candle cache must hold this view's cycles before the badge pass joins the two
    _tw = time.time()
    while time.time() - _tw < 25.0:
        w._px_t = 0.0
        try:
            w._px_tick(time.time())
        except Exception:
            pass
        arr = w.__dict__.get("_px_arr")
        # ... the cycles that END inside the view: the one straddling its right edge is never cached from this view
        # (waiting for it cost 25 s a step on the first harvest) -- the next, overlapping view holds it whole
        fin = None if L is None else np.asarray(L["x0"], float)[(~np.asarray(L["form"], bool)) & (np.asarray(L["x1"], float) <= b - 1.0)]
        if fin is None or fin.size == 0 or (arr is not None and np.size(arr[0]) and
                                            float(np.min(np.abs(np.asarray(arr[0], float) - float(fin[-1])))) <= 1.0 and
                                            float(np.min(np.abs(np.asarray(arr[0], float) - float(fin[0])))) <= 1.0):
            break
        spin(0.5)
    _t_px = time.time() - _tw
    w._px_iib_src = None; w._px_iib_tick(time.time())
    n_new = n_b = 0
    if L is not None and np.size(L["x0"]):
        _form = np.asarray(L["form"], bool)
        for i in range(int(np.size(L["x0"]))):
            if bool(_form[i]):
                continue
            k = round(float(L["x0"][i]), 2)
            if k < va - 1.0:
                continue
            n_new += int(k not in rows)
            rows[k] = tuple(float(L[f][i]) for f in F_NUM) + tuple(int(L[f][i]) for f in F_FLAG)
    arr = w.__dict__.get("_px_arr")
    if arr is not None and np.size(arr[0]):
        _ct = np.asarray(arr[0], float); _cc = np.asarray(arr[6], int)
        for j in np.flatnonzero((_ct >= va - 1.0) & (_ct < b + 1.0)):
            colours[round(float(_ct[j]), 2)] = int(_cc[j])
    bl = w.__dict__.get("_px_iib_last")
    if bl is not None:
        for _side, _keys in ((1, bl["buy"]), (-1, bl["sell"])):
            for kx in np.asarray(_keys, float):
                if kx >= va - 1.0:
                    n_b += int(round(float(kx), 2) not in badges)
                    badges[round(float(kx), 2)] = _side
    steps.append((va, b, bool(got), 0 if L is None else int(np.size(L["x0"])), n_new, n_b))
    log("view %s .. %s | book %s in %.0f s, candles in %.0f s | pane rows +%d (total %d) | new badges %d"
        % (hms(va), hms(b), "ok" if got else "TIMEOUT", _t_book, _t_px, n_new, len(rows), n_b))
    a = b

# ------------------------------------------------------------------ 3) the walls, the bins
wall_k = sorted(w._iimp_wall_cache); book_k = sorted(w._iimp_book_cache)
walls = np.array([[k, w._iimp_wall_cache[k][0], w._iimp_wall_cache[k][1]] for k in wall_k], dtype=np.float64).reshape(-1, 3)
books = np.array([[k, w._iimp_book_cache[k][0], w._iimp_book_cache[k][1]] for k in book_k], dtype=np.float64).reshape(-1, 3)
st = w._flow
bin_secs = float(st.bin); bin_base = int(st._base)
b_buy = np.asarray(st._buy, dtype=np.float32).copy(); b_sell = np.asarray(st._sell, dtype=np.float32).copy()
b_px = np.asarray(st._px, dtype=np.float64).copy(); b_pxh = np.asarray(st._pxh, dtype=np.float64).copy()
b_pxl = np.asarray(st._pxl, dtype=np.float64).copy()

# ------------------------------------------------------------------ 4) the rule, recomputed here, against the marks drawn
ks = sorted(rows)
R = np.array([rows[k] for k in ks], dtype=np.float64).reshape(-1, len(F_NUM) + len(F_FLAG))
ci = {f: i for i, f in enumerate(F_NUM + F_FLAG)}
tick = float(config.TICK_SIZE)
cyc_ix = {round(float(x), 2): j for j, x in enumerate(t)}
mine = {}
for r in R:
    k = round(float(r[ci["x0"]]), 2)
    j = cyc_ix.get(k)
    if j is None or not bool(done[j]):
        continue
    col = colours.get(k, -1)
    lt = int(r[ci["vac"]]) if bool(config.PX_IIB_VACUUM) else 0
    if lt == 0 and bool(config.PX_IIB_QUIET):
        lt = int(r[ci["quiet"]])
    side = 1 if col in BREAK_BUY_COLS else (-1 if col in BREAK_SELL_COLS else (lt if col < 0 else 0))
    if side == 0:
        continue
    own, oth = (r[ci["liib"]], r[ci["liis"]]) if side > 0 else (r[ci["liis"]], r[ci["liib"]])
    pown, poth = (r[ci["pliib"]], r[ci["pliis"]]) if side > 0 else (r[ci["pliis"]], r[ci["pliib"]])
    if not (own > 0 and oth < 0):
        continue
    if bool(config.PX_IIB_REQUIRE_GAIN) and not (np.isfinite(pown) and np.isfinite(poth) and own > pown and oth <= poth):
        continue
    if float(config.PX_IIB_MIN_SPREAD) > 0 and (2.0 ** own - 2.0 ** oth) < float(config.PX_IIB_MIN_SPREAD):
        continue
    if col < 0 and float(config.PX_IIB_KEPT_MIN) > 0:        # a light-flow candle must have HELD its move
        ext = float(pxh[j]) if side > 0 else float(pxl[j])
        rch = side * (ext - float(px0[j])) / tick; mvv = side * (float(px1[j]) - float(px0[j])) / tick
        if not (rch >= float(config.IIMP_KEEP_MIN_TICKS) - 1e-9 and (mvv / max(rch, 1e-9)) >= float(config.PX_IIB_KEPT_MIN) - 1e-12):
            continue
    mine[k] = side
drawn = {k: v for k, v in badges.items()}
both = sum(1 for k in drawn if mine.get(k) == drawn[k])
only_drawn = sorted(k for k in drawn if mine.get(k) != drawn[k]); only_mine = sorted(k for k in mine if drawn.get(k) != mine[k])
_nocol = sorted(k for k in rows if k not in colours)
log("pane rows without a candle colour: %d of %d%s" % (len(_nocol), len(rows), "" if not _nocol else " (last at %s)" % hms(_nocol[-1])))
log("TAKEOVER: drawn by the terminal %d (buy %d / sell %d) | recomputed here %d | agree %d | drawn only %d | recomputed only %d"
    % (len(drawn), sum(1 for v in drawn.values() if v > 0), sum(1 for v in drawn.values() if v < 0), len(mine), both, len(only_drawn), len(only_mine)))

# ------------------------------------------------------------------ 5) save, upload
try:
    commit = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
except Exception:
    commit = ""
stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
meta = {"harvested_utc": stamp, "harvested_unix": time.time(), "store": [A, B], "lookback": N_LB, "lookback_secs": LB,
        "min_n": int(w._lb_min_n()), "flow_win": float(w._flow_win), "tick": tick, "commit": commit,
        "cycles": int(t.size), "cycles_finished": int(np.sum(done)), "iimp_rows": int(R.shape[0]),
        "iimp_cols": list(F_NUM + F_FLAG), "colour_cols": ["t", "colour"], "badge_cols": ["t", "side"],
        "wall_cols": ["t", "ask_usd", "bid_usd"], "book_cols": ["t", "bid_mean", "ask_mean"],
        "bin_secs": bin_secs, "bin_base": bin_base, "bins": int(b_buy.size),
        "steps": [[float(x[0]), float(x[1]), bool(x[2]), int(x[3]), int(x[4]), int(x[5])] for x in steps],
        "book_timeouts": int(sum(1 for x in steps if not x[2])), "rows_without_colour": len(_nocol),
        "overlap_secs": OVER, "short_run_hours": float(ARGS.hours),
        "takeover": {"drawn": len(drawn), "recomputed": len(mine), "agree": both, "drawn_only": only_drawn, "recomputed_only": only_mine},
        "rule": {k: getattr(config, k) for k in ("PX_IIB_REQUIRE_GAIN", "PX_IIB_MIN_SPREAD", "PX_IIB_VACUUM", "PX_IIB_QUIET", "PX_IIB_KEPT_MIN",
                                                 "IIMP_KEEP_MIN_TICKS", "IIMP_CLIP", "IIMP_WALL_RADIUS", "SPEED_FLAT_TICKS")},
        "model": {"IIMP_COEF_BUY": list(config.IIMP_COEF_BUY), "IIMP_COEF_SELL": list(config.IIMP_COEF_SELL)},
        "cross": {"min_spread_pct": float(config.FLOW_CROSS_MIN_SPREAD_PCT), "min_hold_secs": float(config.FLOW_CROSS_MIN_HOLD_SECS),
                  "context_secs": float(config.FLOW_CROSS_CONTEXT_SECS)}}
base = ("check_%s" if _short else "cycles_%s") % stamp
f_npz = os.path.join(DATA, base + ".npz"); f_json = os.path.join(DATA, base + ".json")
np.savez_compressed(
    f_npz, t=t, t_end=t_end, done=done, is_buy=is_buy, strong=strong, move=move, cbuy=cbuy, csell=csell,
    o=px0, c=px1, h=pxh, l=pxl, iimp=R,
    colours=np.array([[k, colours[k]] for k in sorted(colours)], dtype=np.float64).reshape(-1, 2),
    badges=np.array([[k, drawn[k]] for k in sorted(drawn)], dtype=np.float64).reshape(-1, 2),
    recomputed=np.array([[k, mine[k]] for k in sorted(mine)], dtype=np.float64).reshape(-1, 2),
    walls=walls, books=books, bin_buy=b_buy, bin_sell=b_sell, bin_px=b_px, bin_pxh=b_pxh, bin_pxl=b_pxl,
    meta=np.array(json.dumps(meta)))
json.dump(meta, open(f_json, "w"), indent=1)
log("saved %s (%.1f MB): %d cycles, %d pane rows (%.0f%% of finished), %d colours, %d walls, %d one-second bins"
    % (os.path.basename(f_npz), os.path.getsize(f_npz) / 1e6, int(t.size), int(R.shape[0]),
       100.0 * R.shape[0] / max(1, int(np.sum(done))), len(colours), int(walls.shape[0]), int(b_buy.size)))
rc = 0
if not ARGS.no_upload and not _short:
    gs = shutil.which("gsutil") or shutil.which("gsutil.cmd")
    if not gs:
        log("gsutil not found -- the harvest stays LOCAL only"); rc = 1
    else:
        for f in (f_npz, f_json):
            try:
                subprocess.run([gs, "-q", "cp", f, GCS], check=True, timeout=600)
                log("uploaded %s -> %s" % (os.path.basename(f), GCS))
            except Exception as ex:
                log("UPLOAD FAILED for %s: %s" % (os.path.basename(f), ex)); rc = 1
log("done")
finish(rc, tunnel, w)
