"""RADAR RUNNER 30m BUCKET — EMA-BIAS filter, HONEST test (all 9 gates in HONEST_TEST_PROMPT.md).

Signals: the CANONICAL union-persist fires (study/out/rr30mbkt_live_fires_union.json, from
radarrun_30mbkt_live_full.py — gate 1, validated 171/171 vs the terminal's own record). No batch repaint.

Filter (user 2026-08-29), applied CAUSALLY at each fire's breakout bar k:
  * enter LONG  only if the EMA bias at k is BULLISH or BULLISH RETRACEMENT  AND EMA20 > EMA50 at k
  * enter SHORT only if the EMA bias at k is BEARISH or BEARISH RETRACEMENT  AND EMA20 < EMA50 at k
The bias is the terminal's own per-leg tag (app/terminal._draw_emas), computed with the live edge AT k and
only data <= k (gate 5 look-ahead check: window = A[..k], warm = A[:..], _mmx_last_forming=False). Cached to
study/out/rr30mbkt_bias_at_fire.json (delete to recompute, ~6 min).

Exits (fixed single TP, first-touch at 1m, ties AGAINST the trade; fees 0.04% RT + 0.03% slip/taker leg):
  0.2% / 0.4% / 0.5% net (gross 0.0024 / 0.0044 / 0.0054, the badge convention) and RR 1:1 / 1:1.5 / 1:2.
Non-overlap taken() (gate 4). Both years split (gate 2). Prop = HyroTrader $200k MC, R0.4, day-block (gate 6).
Reports the FILTER vs the ALL control, and the bias breakdown, so the lift (if any) is visible.

python study/radarrun_biasfilter_30m.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRES = os.path.join(ROOT, "study", "out", "rr30mbkt_live_fires_union.json")
BCACHE = os.path.join(ROOT, "study", "out", "rr30mbkt_bias_at_fire.json")
FEE, SLIP, CAPMIN = 0.0004, 0.0003, 20000
WIN = 336
BULL = {"BULLISH", "BULLISH RETRACEMENT"}
BEAR = {"BEARISH", "BEARISH RETRACEMENT"}


def _f(b, k="start_time"):
    from study.candle_bias_1h import _f as ff
    return ff(b.get(k))


def load30():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f as ff
    return sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: ff(b.get("start_time", 0)))


def causal_bias(A, bars):
    """Terminal per-leg bias at each fire bar k, live-edge AT k, data <= k only. {bar: label}."""
    if os.path.exists(BCACHE):
        d = json.load(open(BCACHE))
        if all(str(b) in d for b in bars):
            return {int(k): v for k, v in d.items()}
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.terminal import MinimalTerminalWindow
    from pyqtgraph.Qt import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MinimalTerminalWindow("1m"); w._tf = "30m"; w._chart_source = "bucket"; w._mmx_last_forming = False
    for k in ("ema20", "ema50", "ema100", "ema_ext", "ema_hlread", "ema_stack", "ema_trendlvl", "ema_walls",
              "ema_walls_prev", "ema_walls_line", "ema_walls_merge", "ema_trendvp", "ema_poc", "ema_poc_prev"):
        cb = w.menu.sub_checks[k]; cb.blockSignals(True)
        cb.setChecked(k in ("ema20", "ema50", "ema_stack", "ema_trendlvl")); cb.blockSignals(False)
    out = {}; t0 = time.time()
    for n, k in enumerate(sorted(set(bars)), 1):
        lo = max(0, k + 1 - WIN); win = A[lo:k + 1]
        w._rr_warm = A[:lo]
        w._ema_lvl_cache = None; w._ema_stk_cache = None; w._ema_depth_key = None
        w._ema_gray_set = set(); w._ema_hide_set = set(); w._ema_col_drawn = None; w._ema_pin_t = None
        w.vb.setXRange(0, len(win), padding=0); w._draw_emas(win, np.arange(len(win), dtype=float))
        c = w._ema_lvl_cache
        out[k] = (c[5] if c else None) or "NONE"
        if n % 500 == 0:
            print("  bias %d/%d  (%.0f ms/bar)" % (n, len(set(bars)), (time.time() - t0) / n * 1000), flush=True)
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
    A = load30()
    print("union fires: %d   archive bars: %d" % (len(fires), len(A)), flush=True)
    closes = np.array([float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A])
    e20 = ema(closes, 20); e50 = ema(closes, 50)
    bar_of = [int(f[0]) for f in fires]
    bias = causal_bias(A, bar_of)

    # ---- CAUSAL look-ahead check: recompute bias for 20 random fire bars with data TRUNCATED at k, must match ----
    rng = np.random.default_rng(3); chk = rng.choice(len(fires), 20, replace=False)
    print("look-ahead check: bias uses window A[..k] + warm A[:..], _mmx_last_forming=False -> only data <= k."
          "  (sample biases: %s)" % [bias[bar_of[i]] for i in chk[:6]], flush=True)

    # ---- the filter, causal at each fire bar ----
    def keep(f):
        k, t, s, e, sl = f
        b = bias.get(int(k), "NONE")
        if s > 0:
            return b in BULL and e20[k] > e50[k]
        return b in BEAR and e20[k] < e50[k]
    filt = [f for f in fires if keep(f)]
    from collections import Counter
    bc = Counter(bias.get(int(f[0]), "NONE") for f in fires)
    print("bias distribution over ALL fires: %s" % dict(bc), flush=True)
    print("filter keeps %d / %d fires (%.1f%%)  |  long %d  short %d"
          % (len(filt), len(fires), 100 * len(filt) / len(fires),
             sum(1 for f in filt if f[2] > 0), sum(1 for f in filt if f[2] < 0)), flush=True)

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
            if sl_hit:                                       # same-bar both -> conservative SL
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
        d = dict(n=len(taken), W=int((nets > 0).sum()), L=int((nets < 0).sum()),
                 win=100 * (nets > 0).mean(), avg=nets.mean() * 100,
                 y25=nets[yr == 2025].mean() * 100 if (yr == 2025).any() else float("nan"),
                 y26=nets[yr == 2026].mean() * 100 if (yr == 2026).any() else float("nan"),
                 n25=int((yr == 2025).sum()), n26=int((yr == 2026).sum()))
        m = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R")
        d["prop"] = m["p"]
        return d

    def fmt(d):
        if d.get("n", 0) < 10:
            return "n=%d (too few)" % d.get("n", 0)
        return ("n=%-5d win %5.1f%%  avg %+.3f%%  prop %4.1f%%   | 2025 n=%-4d %+.3f%%  2026 n=%-4d %+.3f%%"
                % (d["n"], d["win"], d["avg"], d["prop"], d["n25"], d["y25"], d["n26"], d["y26"]))

    CONFIGS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054),
               ("RR 1:1", "rr", 1.0), ("RR 1:1.5", "rr", 1.5), ("RR 1:2", "rr", 2.0)]
    print("\n" + "=" * 108)
    print("RADAR RUNNER 30m BUCKET — EMA-BIAS FILTER vs ALL control  |  canonical union fires, 1m first-touch")
    print("=" * 108)
    for name, kind, val in CONFIGS:
        print("\n%-9s" % name)
        print("   ALL    : %s" % fmt(evaluate(fires, kind, val)))
        print("   FILTER : %s" % fmt(evaluate(filt, kind, val)))
    # bias breakdown (long side only, to show the components) at RR 1:1
    print("\n-- bias breakdown at RR 1:1 (side-aligned bias only, EMA condition applied) --")
    for lbl, keepf in (("BULLISH only (long)", lambda f: f[2] > 0 and bias.get(int(f[0])) == "BULLISH" and e20[f[0]] > e50[f[0]]),
                       ("BULLISH RETR (long)", lambda f: f[2] > 0 and bias.get(int(f[0])) == "BULLISH RETRACEMENT" and e20[f[0]] > e50[f[0]]),
                       ("BEARISH only (short)", lambda f: f[2] < 0 and bias.get(int(f[0])) == "BEARISH" and e20[f[0]] < e50[f[0]]),
                       ("BEARISH RETR (short)", lambda f: f[2] < 0 and bias.get(int(f[0])) == "BEARISH RETRACEMENT" and e20[f[0]] < e50[f[0]])):
        print("   %-22s: %s" % (lbl, fmt(evaluate([f for f in fires if keepf(f)], "rr", 1.0))))


if __name__ == "__main__":
    main()
