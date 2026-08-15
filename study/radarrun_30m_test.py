"""Test a 30m TF for the Radar Runner. The recon archive has no native 30m, but its buckets are VOLUME-paced, so a 30m
volume bucket == two consecutive 15m buckets merged (OHLC first/last/max/min, buy/sell/curr_vol summed, per-price
`levels` footprint merged, poc recomputed). Runs the census + TP x slippage robustness sweep (cand+0.3cap SL) on 15m,
30m, 1h side-by-side so the frequency-vs-robustness trade-off is directly comparable. The question: does 30m keep 1h-
style both-year/6bps robustness at ~2x the frequency, or go slippage-fragile like 15m?"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; H = 200; FEE = 0.0004
TPS = [0.003, 0.004, 0.005]; SLIPS = [0.0, 0.0003, 0.0006]


def agg2(b0, b1):
    m = {"open_price": _f(b0.get("open_price", b0.get("open"))),
         "close_price": _f(b1.get("close_price", b1.get("close"))),
         "high": max(_f(b0.get("high")), _f(b1.get("high"))),
         "low": min(_f(b0.get("low")), _f(b1.get("low"))),
         "buy_vol": _f(b0.get("buy_vol")) + _f(b1.get("buy_vol")),
         "sell_vol": _f(b0.get("sell_vol")) + _f(b1.get("sell_vol")),
         "curr_vol": _f(b0.get("curr_vol")) + _f(b1.get("curr_vol")),
         "start_time": b0.get("start_time"), "end_time": b1.get("end_time")}
    lv = {}
    for src in (b0.get("levels") or {}, b1.get("levels") or {}):
        for p, vv in src.items():
            e = lv.get(p)
            if e is None:
                lv[p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
            else:
                e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))
    m["levels"] = lv
    if lv:
        m["poc_price"] = float(max(lv.items(), key=lambda kv: kv[1]["b"] + kv[1]["s"])[0])
    return m


def get_buckets(tf):
    if tf == "30m":
        A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
        return [agg2(A15[2 * i], A15[2 * i + 1]) for i in range(len(A15) // 2)]
    return sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def run(tf):
    A = get_buckets(tf); n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000

    rows = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.003), rlo) if up else min(Hi[k] * (1 + 0.003), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        d = {tp: sim(s, entry, entry * (1 + s * tp), sl, ph, pl, pc) for tp in TPS}
        rows.append((k, int(yr[k]), dist, d))
    span = (ST[-1] - ST[0]) / 86400.0; mo = span / 30.437
    # take() non-overlap for census at TP=0.5
    taken = []; last = -1
    for (k, y, dist, d) in rows:
        if k <= last:
            continue
        taken.append((k, y, dist, d)); last = k + int(d[0.005][2])
    nl = sum(1 for (k, side) in ev if side == "S")
    exp = np.mean([(d[0.005][1] - FEE - 0.0003 - (0.0003 if d[0.005][0] != "tp" else 0.0)) / dist
                   for (k, y, dist, d) in taken])
    win = 100 * np.mean([1.0 if (d[0.005][1] > 0) else 0.0 for (k, y, dist, d) in taken])
    print("\n########  TF = %s  (bars=%d, span=%.1fmo)  ########" % (tf, n, mo), flush=True)
    print("  census: raw=%d (L%d/S%d)  tradeable=%d  %.1f/mo  win=%.0f%%  exp(TP0.5,3bps)=%+.3fR"
          % (len(ev), len(ev) - nl, nl, len(taken), len(taken) / mo, win, exp), flush=True)
    print("  robustness sweep (cand+0.3cap SL, avg %%/trade net):", flush=True)
    for tp in TPS:
        line = "    TP=%.1f%%" % (tp * 100)
        for slip in SLIPS:
            r25 = []; r26 = []; last = -1
            for (k, y, dist, d) in rows:
                if k <= last:
                    continue
                outc, gross, off = d[tp]
                net = gross - FEE - slip - (slip if outc != "tp" else 0.0)
                (r25 if y == 2025 else r26).append(net); last = k + int(off)
            a25 = np.array(r25); a26 = np.array(r26)
            line += "  |%.0fbps 25:%+.3f 26:%+.3f" % (slip * 1e4, a25.mean() * 100 if len(a25) else 0,
                                                      a26.mean() * 100 if len(a26) else 0)
        print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "30m", "1h"]):
        try:
            run(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
