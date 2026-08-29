"""RADAR RUNNER 15m CLOCK candle — EMA-bias + Mov.Magnitude filter, HONEST test (all 9 gates).

Signals: the union-persist fires for 15m CLOCK (study/out/rr_union_clock_15m_s1.json = exactly what the terminal
persists; gate 1). No batch repaint.

Filter (user 2026-08-29), CAUSAL at each fire's breakout bar k on the 15m CLOCK series:
  * LONG  only if bias is BULLISH or BULLISH RETRACEMENT  AND EMA20 > EMA50  AND Mov.Magnitude(signal candle) > 15
  * SHORT only if bias is BEARISH or BEARISH RETRACEMENT  AND EMA20 < EMA50  AND Mov.Magnitude(signal candle) > 15
Mov.Magnitude = ((close*100/ref - 100)**2)*100, ref = the candle's FAR extreme (low if up / high if down) --
the terminal's own metric (app/flow_flip_detect.mov_magn). Bias = the terminal's per-leg tag computed live-edge
AT bar k, data <= k only (causal, gate 5). Cached to study/out/rr_clock15m_bias_at_fire.json (~7 min).

Exits: 0.2 / 0.4 / 0.5% net (gross 0.0024/0.0044/0.0054) + RR 1:1 / 1:1.5 / 1:2. 1m CLOCK first-touch, ties
AGAINST; fees 0.04% RT + 0.03% slip/taker leg. Non-overlap taken() (gate 4). Both years (gate 2). Prop MC (gate 6).
Reports FILTER (bias+ema+movmag) vs bias+ema-only vs ALL, so the movmag contribution is visible.

python study/radarrun_biasfilter_clock15m.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRES = os.path.join(ROOT, "study", "out", "rr_union_clock_15m_s1.json")
BCACHE = os.path.join(ROOT, "study", "out", "rr_clock15m_bias_at_fire.json")
FEE, SLIP, CAPMIN, WIN = 0.0004, 0.0003, 20000, 336
MOVMAG_MIN = 15.0
BULL = {"BULLISH", "BULLISH RETRACEMENT"}
BEAR = {"BEARISH", "BEARISH RETRACEMENT"}


def _f(b, k="start_time"):
    from study.candle_bias_1h import _f as ff
    return ff(b.get(k))


def load15():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f as ff
    return sorted(load_archive("15m", root="study/clock_archive")[1], key=lambda b: ff(b.get("start_time", 0)))


def mov_magn(b):
    o = float(b.get("open", 0.0) or 0.0); c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0); l = float(b.get("low", 0.0) or 0.0)
    if o <= 0 or c <= 0:
        return 0.0
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def causal_bias(A, bars):
    if os.path.exists(BCACHE):
        d = json.load(open(BCACHE))
        if all(str(b) in d for b in bars):
            return {int(k): v for k, v in d.items()}
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.terminal import MinimalTerminalWindow
    from pyqtgraph.Qt import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MinimalTerminalWindow("1m"); w._tf = "15m"; w._chart_source = "time"; w._mmx_last_forming = False
    for k in ("ema20", "ema50", "ema100", "ema_ext", "ema_hlread", "ema_stack", "ema_trendlvl", "ema_walls",
              "ema_walls_prev", "ema_walls_line", "ema_walls_merge", "ema_trendvp", "ema_poc", "ema_poc_prev"):
        cb = w.menu.sub_checks[k]; cb.blockSignals(True)
        cb.setChecked(k in ("ema20", "ema50", "ema_stack", "ema_trendlvl")); cb.blockSignals(False)
    out = {}; t0 = time.time(); U = sorted(set(bars))
    for n, k in enumerate(U, 1):
        lo = max(0, k + 1 - WIN); win = A[lo:k + 1]; w._rr_warm = A[:lo]
        w._ema_lvl_cache = None; w._ema_stk_cache = None; w._ema_depth_key = None
        w._ema_gray_set = set(); w._ema_hide_set = set(); w._ema_col_drawn = None; w._ema_pin_t = None
        w.vb.setXRange(0, len(win), padding=0); w._draw_emas(win, np.arange(len(win), dtype=float))
        c = w._ema_lvl_cache
        out[k] = (c[5] if c else None) or "NONE"
        if n % 500 == 0:
            print("  bias %d/%d  (%.0f ms/bar)" % (n, len(U), (time.time() - t0) / n * 1000), flush=True)
    json.dump({str(k): v for k, v in out.items()}, open(BCACHE, "w"))
    return out


def ema(closes, span):
    a = 2.0 / (span + 1.0); y = np.empty(len(closes)); y[0] = closes[0]
    for i in range(1, len(closes)):
        y[i] = a * closes[i] + (1.0 - a) * y[i - 1]
    return y


def main():
    from study.candle_bias_1h import _f as ff
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    fires = json.load(open(FIRES))
    A = load15()
    print("clock-15m union fires: %d   archive bars: %d" % (len(fires), len(A)), flush=True)
    closes = np.array([float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A])
    e20 = ema(closes, 20); e50 = ema(closes, 50)
    mm = np.array([mov_magn(b) for b in A])
    bias = causal_bias(A, [int(f[0]) for f in fires])

    from collections import Counter
    print("bias distribution over ALL fires: %s" % dict(Counter(bias.get(int(f[0]), "NONE") for f in fires)), flush=True)
    print("Mov.Magnitude quantiles over fire bars: p50=%.1f p75=%.1f p90=%.1f  (>%.0f: %d fires)"
          % (np.percentile([mm[int(f[0])] for f in fires], 50), np.percentile([mm[int(f[0])] for f in fires], 75),
             np.percentile([mm[int(f[0])] for f in fires], 90), MOVMAG_MIN,
             sum(1 for f in fires if mm[int(f[0])] > MOVMAG_MIN)), flush=True)

    def bias_ema_ok(f):
        k, t, s, e, sl = f; b = bias.get(int(k), "NONE")
        return (b in BULL and e20[k] > e50[k]) if s > 0 else (b in BEAR and e20[k] < e50[k])
    be = [f for f in fires if bias_ema_ok(f)]                       # bias + EMA only
    filt = [f for f in be if mm[int(f[0])] > MOVMAG_MIN]            # + Mov.Magnitude > 15
    print("bias+EMA keeps %d ; + Mov.Magnitude>%.0f keeps %d (long %d / short %d)"
          % (len(be), MOVMAG_MIN, len(filt), sum(1 for f in filt if f[2] > 0), sum(1 for f in filt if f[2] < 0)),
          flush=True)

    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: ff(b.get("start_time", 0)))
    T1 = np.array([ff(b.get("start_time")) for b in A1]); H1 = np.array([ff(b.get("high")) for b in A1])
    L1 = np.array([ff(b.get("low")) for b in A1]); del A1

    def resolve_1m(s, e, sl, kind, val, t0):
        sld = abs(e - sl) / e
        if sld <= 0:
            return None, None, None
        g = val if kind == "fix" else val * sld
        tp = e * (1 + s * g); i0 = int(np.searchsorted(T1, t0 - 1))
        for j in range(i0, min(len(T1), i0 + CAPMIN)):
            hi = H1[j]; lo = L1[j]
            sl_hit = (lo <= sl) if s > 0 else (hi >= sl)
            tp_hit = (hi >= tp) if s > 0 else (lo <= tp)
            if sl_hit:
                return s * (sl - e) / e - FEE - 2 * SLIP, T1[j], sld
            if tp_hit:
                return g - FEE - SLIP, T1[j], sld
        return 0.0 - FEE - 2 * SLIP, T1[min(len(T1) - 1, i0 + CAPMIN - 1)], sld

    def evaluate(fset, kind, val):
        taken = []; busy = -1.0
        for (k, t, s, e, sl) in fset:
            if t < busy:
                continue
            net, tx, sld = resolve_1m(s, e, sl, kind, val, t)
            if net is None:
                continue
            taken.append((t, net, net / sld, datetime.fromtimestamp(t, tz=timezone.utc).year))
            busy = tx
        if len(taken) < 10:
            return dict(n=len(taken))
        nets = np.array([x[1] for x in taken]); rs = np.array([x[2] for x in taken]); yr = np.array([x[3] for x in taken])
        d = dict(n=len(taken), win=100 * (nets > 0).mean(), avg=nets.mean() * 100,
                 y25=nets[yr == 2025].mean() * 100 if (yr == 2025).any() else float("nan"),
                 y26=nets[yr == 2026].mean() * 100 if (yr == 2026).any() else float("nan"),
                 n25=int((yr == 2025).sum()), n26=int((yr == 2026).sum()))
        d["prop"] = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R")["p"]
        return d

    def fmt(d):
        if d.get("n", 0) < 10:
            return "n=%d (too few)" % d.get("n", 0)
        return ("n=%-4d win %5.1f%%  avg %+.3f%%  prop %4.1f%%   | 2025 n=%-4d %+.3f%%  2026 n=%-4d %+.3f%%"
                % (d["n"], d["win"], d["avg"], d["prop"], d["n25"], d["y25"], d["n26"], d["y26"]))

    CONFIGS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054),
               ("RR 1:1", "rr", 1.0), ("RR 1:1.5", "rr", 1.5), ("RR 1:2", "rr", 2.0)]
    print("\n" + "=" * 112)
    print("RADAR RUNNER 15m CLOCK — bias+EMA+MovMag>%.0f  vs  bias+EMA  vs  ALL  |  union fires, 1m clock first-touch"
          % MOVMAG_MIN)
    print("=" * 112)
    for name, kind, val in CONFIGS:
        print("\n%-9s" % name)
        print("   ALL        : %s" % fmt(evaluate(fires, kind, val)))
        print("   bias+EMA    : %s" % fmt(evaluate(be, kind, val)))
        print("   +MovMag>15  : %s" % fmt(evaluate(filt, kind, val)))


if __name__ == "__main__":
    main()
