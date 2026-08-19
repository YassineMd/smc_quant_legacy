"""Radar Runner with TIGHTER candle-anchored stops: SL = signal candle's opposite extreme -/+ {0.3,0.2,0.1}% (capped at
the radar extreme, which won't bind this tight). 1h + native 30m (15m volume-accumulation), TP 0.4/0.5%, both recon
years, slippage 0/3/6bps. Reports win / avg%net / net% / avg-LOSER / avg-stop-distance so the risk-vs-expectancy of
tightening the stop is visible. 0.04% RT fee, taken()-nonoverlap. CLI: [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; H = 200; FEE = 0.0004
TPS = [0.004, 0.005]; SLIPS = [0.0, 0.0003, 0.0006]
SL_BUFS = [("cand+0.3", 0.003), ("cand+0.2", 0.002), ("cand+0.1", 0.001)]


def _merge_lv(dst, b):
    for p, vv in (b.get("levels") or {}).items():
        e = dst.get(p)
        if e is None:
            dst[p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
        else:
            e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))


def build_native_30m(A15, T):
    """NATIVE: cut a 30m bucket each time cumulative curr_vol >= T (=2x median 15m target) -> uniform-volume bars."""
    out = []; acc = None
    for b in A15:
        if acc is None:
            acc = {"open_price": _f(b.get("open_price", b.get("open"))), "close_price": _f(b.get("close_price", b.get("close"))),
                   "high": _f(b.get("high")), "low": _f(b.get("low")), "buy_vol": _f(b.get("buy_vol")),
                   "sell_vol": _f(b.get("sell_vol")), "curr_vol": _f(b.get("curr_vol")), "start_time": b.get("start_time"),
                   "end_time": b.get("end_time"), "levels": {}}
            _merge_lv(acc["levels"], b)
        else:
            acc["close_price"] = _f(b.get("close_price", b.get("close")))
            acc["high"] = max(acc["high"], _f(b.get("high"))); acc["low"] = min(acc["low"], _f(b.get("low")))
            acc["buy_vol"] += _f(b.get("buy_vol")); acc["sell_vol"] += _f(b.get("sell_vol")); acc["curr_vol"] += _f(b.get("curr_vol"))
            acc["end_time"] = b.get("end_time"); _merge_lv(acc["levels"], b)
        if acc["curr_vol"] >= T:
            out.append(acc); acc = None
    if acc is not None:
        out.append(acc)
    return out


def get_buckets(tf):
    if tf == "30m":
        import statistics
        A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
        tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
        T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
        return build_native_30m(A15, T)
    return sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def study(tf):
    A = get_buckets(tf); n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

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
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        per = {}
        for bname, X in SL_BUFS:
            sl = max(Lo[k] * (1 - X), rlo) if up else min(Hi[k] * (1 + X), rhi)
            dist = abs(entry - sl) / entry
            per[bname] = (dist, {tp: sim(s, entry, entry * (1 + s * tp), sl, ph, pl, pc) for tp in TPS})
        rows.append((k, int(yr[k]), per))
    print("\n########  TF = %s  (events=%d)  ########" % (tf, len(rows)), flush=True)
    for bname, _X in SL_BUFS:
        avgdist = 100 * np.mean([r[2][bname][0] for r in rows])
        print("  == SL %s  (avg stop dist %.2f%%) ==" % (bname, avgdist), flush=True)
        for tp in TPS:
            for slip in SLIPS:
                line = "     TP%.1f slip%.0f" % (tp * 100, slip * 1e4)
                for Y in (2025, 2026):
                    acc = []; last = -1
                    for (k, y, per) in rows:
                        if y != Y or k <= last:
                            continue
                        outc, gross, off = per[bname][1][tp]
                        net = gross - FEE - slip - (slip if outc != "tp" else 0.0)
                        acc.append(net); last = k + int(off)
                    a = np.array(acc); los = a[a < 0]
                    line += "  |%d n=%-4d win=%2.0f%% avg=%+.3f%% net=%+.0f%% L=%.2f%%" % (
                        Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100, los.mean() * 100 if len(los) else 0)
                print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "30m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
