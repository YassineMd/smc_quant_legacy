"""RADAR RUNNER 30m BUCKET — FIRST signal per clean-trend vline, fixed SL 3% / TP 1%. HONEST (9 gates).

Rule (user 2026-08-29):
  * keep only RadarRun fires that print while the causal EMA bias is a CLEAN trend -- BULLISH or BEARISH.
    Exclude BULLISH/BEARISH RETRACEMENT, RANGING, undetermined.
  * take only the FIRST fire after each vertical flip line -> exactly one trade per trend/vline (no more, no less).
  * fixed SL 3% and TP 1% from the breakout close.

Signals: canonical union fires (171/171 validated). Bias + STATE (which vline) are the terminal's own per-leg
tag + the opening flip time of the current drawn state, computed live-edge AT the fire bar, data <= k only
(causal, gate 5). Cached to study/out/rr30mbkt_biasstate_at_fire.json (delete to recompute, ~6 min).

Two side conventions reported: ALIGNED (long in a bull trend / short in a bear trend -- trade WITH the trend,
the natural reading of "1 signal per trend") and NATIVE (whatever side the breakout printed). 1m first-touch,
ties AGAINST; fees 0.04% RT + 0.03% slip/taker leg. Non-overlap taken() (gate 4). Both years (gate 2). Prop MC.

python study/radarrun_firstpertrend_30m.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRES = os.path.join(ROOT, "study", "out", "rr30mbkt_live_fires_union.json")
BCACHE = os.path.join(ROOT, "study", "out", "rr30mbkt_biasstate_at_fire.json")
FEE, SLIP, CAPMIN, WIN = 0.0004, 0.0003, 20000, 336
SL_FRAC, TP_FRAC = 0.03, 0.01
TREND = {"BULLISH", "BEARISH"}


def load30():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    return sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))


def bias_state(A, bars):
    """Per fire bar k: (bias label, state_key) where state_key = start_time of the flip that opened the current
    DRAWN bias state (its vertical line). Live-edge AT k, data <= k only."""
    if os.path.exists(BCACHE):
        d = json.load(open(BCACHE))
        if all(str(b) in d for b in bars):
            return {int(k): tuple(v) for k, v in d.items()}
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.terminal import MinimalTerminalWindow
    from pyqtgraph.Qt import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MinimalTerminalWindow("1m"); w._tf = "30m"; w._chart_source = "bucket"; w._mmx_last_forming = False
    for k in ("ema20", "ema50", "ema100", "ema_ext", "ema_hlread", "ema_stack", "ema_trendlvl", "ema_walls",
              "ema_walls_prev", "ema_walls_line", "ema_walls_merge", "ema_trendvp", "ema_poc", "ema_poc_prev"):
        cb = w.menu.sub_checks[k]; cb.blockSignals(True)
        cb.setChecked(k in ("ema20", "ema50", "ema_stack", "ema_trendlvl")); cb.blockSignals(False)
    out = {}; t0 = time.time(); U = sorted(set(bars))
    for n, k in enumerate(U, 1):
        lo = max(0, k + 1 - WIN); win = A[lo:k + 1]; w._rr_warm = A[:lo]
        tk = float(A[k].get("start_time", 0.0) or 0.0)
        w._ema_lvl_cache = None; w._ema_stk_cache = None; w._ema_depth_key = None
        w._ema_gray_set = set(); w._ema_hide_set = set(); w._ema_col_drawn = None; w._ema_pin_t = None
        w.vb.setXRange(0, len(win), padding=0); w._draw_emas(win, np.arange(len(win), dtype=float))
        c = w._ema_lvl_cache
        lbl = (c[5] if c else None) or "NONE"
        ft = getattr(w, "_ema_flip_times", {}) or {}; hide = getattr(w, "_ema_hide_set", set()) or set()
        cand = [t for ai, t in ft.items() if ai not in hide and t <= tk + 0.5]
        out[k] = (lbl, max(cand) if cand else -1.0)
        if n % 500 == 0:
            print("  bias+state %d/%d  (%.0f ms/bar)" % (n, len(U), (time.time() - t0) / n * 1000), flush=True)
    json.dump({str(k): list(v) for k, v in out.items()}, open(BCACHE, "w"))
    return out


def main():
    from study.candle_bias_1h import _f
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    fires = json.load(open(FIRES))
    A = load30()
    print("union fires: %d   archive bars: %d" % (len(fires), len(A)), flush=True)
    bs = bias_state(A, [int(f[0]) for f in fires])
    from collections import Counter
    print("bias distribution: %s" % dict(Counter(bs.get(int(f[0]), ("NONE", -1))[0] for f in fires)), flush=True)

    # ---- selection: clean-trend fires, FIRST per state (vline). Two side conventions. ----
    def first_per_state(aligned):
        picked = {}                                            # state_key -> earliest qualifying fire
        for f in sorted(fires, key=lambda f: f[1]):            # time order
            k, t, s, e, sl = f
            lbl, sk = bs.get(int(k), ("NONE", -1.0))
            if lbl not in TREND or sk < 0:
                continue
            if aligned and not ((lbl == "BULLISH" and s > 0) or (lbl == "BEARISH" and s < 0)):
                continue                                       # trade WITH the trend only
            key = (lbl, round(sk, 3))
            if key not in picked or t < picked[key][1]:
                picked[key] = f
        return sorted(picked.values(), key=lambda f: f[1])
    aligned = first_per_state(True); native = first_per_state(False)
    print("selected  ALIGNED: %d states (long %d / short %d)   NATIVE: %d states"
          % (len(aligned), sum(1 for f in aligned if f[2] > 0), sum(1 for f in aligned if f[2] < 0),
             len(native)), flush=True)

    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1])
    L1 = np.array([_f(b.get("low")) for b in A1]); del A1

    def resolve(s, e, t0):
        sl = e * (1 - s * SL_FRAC); tp = e * (1 + s * TP_FRAC)
        i0 = int(np.searchsorted(T1, t0 - 1))
        for j in range(i0, min(len(T1), i0 + CAPMIN)):
            hi = H1[j]; lo = L1[j]
            sl_hit = (lo <= sl) if s > 0 else (hi >= sl)
            tp_hit = (hi >= tp) if s > 0 else (lo <= tp)
            if sl_hit:                                         # same-bar both -> conservative SL
                return -SL_FRAC - FEE - 2 * SLIP, T1[j]
            if tp_hit:
                return TP_FRAC - FEE - SLIP, T1[j]
        return -FEE - 2 * SLIP, T1[min(len(T1) - 1, i0 + CAPMIN - 1)]   # unresolved -> flat-ish

    def evaluate(fset):
        taken = []; busy = -1.0; drop = 0
        for (k, t, s, e, sl) in fset:
            if t < busy:
                drop += 1; continue
            net, tx = resolve(s, e, t)
            taken.append((t, net, net / SL_FRAC, datetime.fromtimestamp(t, tz=timezone.utc).year))
            busy = tx
        if len(taken) < 10:
            return dict(n=len(taken), drop=drop)
        nets = np.array([x[1] for x in taken]); rs = np.array([x[2] for x in taken]); yr = np.array([x[3] for x in taken])
        d = dict(n=len(taken), drop=drop, W=int((nets > 0).sum()), L=int((nets < 0).sum()),
                 win=100 * (nets > 0).mean(), avg=nets.mean() * 100, tot=nets.sum() * 100,
                 y25=nets[yr == 2025].mean() * 100 if (yr == 2025).any() else float("nan"),
                 y26=nets[yr == 2026].mean() * 100 if (yr == 2026).any() else float("nan"),
                 n25=int((yr == 2025).sum()), n26=int((yr == 2026).sum()),
                 w25=100 * (nets[yr == 2025] > 0).mean() if (yr == 2025).any() else float("nan"),
                 w26=100 * (nets[yr == 2026] > 0).mean() if (yr == 2026).any() else float("nan"))
        d["prop"] = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R")["p"]
        return d

    def fmt(d):
        if d.get("n", 0) < 10:
            return "n=%d (too few; %d dropped by non-overlap)" % (d.get("n", 0), d.get("drop", 0))
        return ("n=%-4d win %5.1f%%  avg %+.3f%%  tot %+.1f%%  prop %4.1f%%  (%d dropped overlap)\n"
                "        2025 n=%-4d win %4.1f%% %+.3f%%   2026 n=%-4d win %4.1f%% %+.3f%%"
                % (d["n"], d["win"], d["avg"], d["tot"], d["prop"], d["drop"],
                   d["n25"], d["w25"], d["y25"], d["n26"], d["w26"], d["y26"]))

    print("\n" + "=" * 100)
    print("RADAR RUNNER 30m — FIRST fire per clean-trend vline  |  SL 3%% / TP 1%% fixed  |  1m first-touch")
    print("=" * 100)
    print("\nALIGNED (long in bull / short in bear):\n   %s" % fmt(evaluate(aligned)))
    print("\nNATIVE (signal's own side):\n   %s" % fmt(evaluate(native)))
    # baseline: EVERY clean-trend fire (not just first-per-state), aligned -> is the first-per-state doing anything?
    allalign = sorted([f for f in fires if bs.get(int(f[0]), ("NONE", -1))[0] in TREND
                       and ((bs[int(f[0])][0] == "BULLISH" and f[2] > 0) or (bs[int(f[0])][0] == "BEARISH" and f[2] < 0))],
                      key=lambda f: f[1])
    print("\nbaseline ALL clean-trend aligned fires (not first-per-state):\n   %s" % fmt(evaluate(allalign)))


if __name__ == "__main__":
    main()
