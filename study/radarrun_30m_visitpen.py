"""Does the VISIT penetration (how deep price pushed into the wall/radar BEFORE the breakout) separate 30m Radar Runner
winners from losers? Causal (known at the breakout bar). visit_pen = how far the visit's extreme pushed PAST the wall P
toward the ATTACKED side, in band units (support: below P ; resistance: above P), over the visit bars [a, k-1]. Shipped
spec (MINVISIT=1, candle-SL + 0.5% TP). Reported: AUC per year + tercile win%/meanR per year + DAEMON out-of-sample
(recon bands) -- an edge must hold BOTH recon years AND forward. python study/radarrun_30m_visitpen.py"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003; SLBUF = 0.003


def native_30m(A15):
    tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
    T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
    out = []; acc = None
    for b in A15:
        v = _f(b.get("curr_vol"))
        if acc is None:
            acc = {"open_price": _f(b.get("open_price", b.get("open"))), "close_price": _f(b.get("close_price", b.get("close"))),
                   "high": _f(b.get("high")), "low": _f(b.get("low")), "curr_vol": v, "buy_vol": _f(b.get("buy_vol")),
                   "sell_vol": _f(b.get("sell_vol")), "start_time": b.get("start_time"), "end_time": b.get("end_time"), "levels": {}}
            for p, vv in (b.get("levels") or {}).items():
                acc["levels"][p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
        else:
            acc["close_price"] = _f(b.get("close_price", b.get("close")))
            acc["high"] = max(acc["high"], _f(b.get("high"))); acc["low"] = min(acc["low"], _f(b.get("low"))); acc["curr_vol"] += v
            acc["buy_vol"] += _f(b.get("buy_vol")); acc["sell_vol"] += _f(b.get("sell_vol")); acc["end_time"] = b.get("end_time")
            for p, vv in (b.get("levels") or {}).items():
                e = acc["levels"].get(p)
                if e is None:
                    acc["levels"][p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
                else:
                    e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))
        if acc["curr_vol"] >= T:
            out.append(acc); acc = None
    if acc is not None:
        out.append(acc)
    return out


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return s * (tp - entry) / entry, off + 1
    return (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def signals(A):
    n = len(A)
    O = np.array([_f(b.get("open_price")) for b in A]); C = np.array([_f(b.get("close_price")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
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
                    ev[(k, side)] = (rlo, rhi, a); break
        if c1 >= n:
            break
        c0 += step - 1000
    rows = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi, a = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        P = 0.5 * (rlo + rhi); band = (rhi - rlo) / (2 * RM)
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0 or band <= 0 or a >= k:
            continue
        if s > 0:                                            # support: attacked side = DOWN -> deepest low vs the wall P
            visit_pen = max(0.0, (P - float(Lo[a:k].min()))) / band
        else:                                                # resistance: attacked = UP -> highest high vs P
            visit_pen = max(0.0, (float(Hi[a:k].max()) - P)) / band
        j0 = k + 1; j1 = min(n, k + 1 + H)
        gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP
        y = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        rows.append({"vp": visit_pen, "win": 1 if net > 0 else 0, "R": net / dist, "y": y}); last = k + int(off)
    return rows


def auc(x, y):
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 < 3 or n0 < 3:
        return float("nan")
    order = np.argsort(x, kind="mergesort"); ranks = np.empty(len(x)); ranks[order] = np.arange(1, len(x) + 1)
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def terciles(rows, q, label):
    parts = []
    for lab, sel in (("LO", lambda v: v < q[0]), ("MID", lambda v: q[0] <= v < q[1]), ("HI", lambda v: v >= q[1])):
        g = [r for r in rows if sel(r["vp"])]
        if g:
            parts.append("%s %.0f%%/%+.3f(n%d)" % (lab, 100 * np.mean([r["win"] for r in g]), np.mean([r["R"] for r in g]), len(g)))
    print("     %s:  %s" % (label, "   ".join(parts)), flush=True)


def main():
    rows = signals(native_30m(sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))))
    print("VISIT penetration (depth into the wall before breakout) -- 30m Radar Runner\n", flush=True)
    for Y in (2025, 2026):
        yr = [r for r in rows if r["y"] == Y]
        a = auc(np.array([r["vp"] for r in yr]), np.array([r["win"] for r in yr]))
        print("  %d: n=%d  win=%.0f%%  AUC(vp->win)=%.3f" % (Y, len(yr), 100 * np.mean([r["win"] for r in yr]), a), flush=True)
    q = np.quantile([r["vp"] for r in rows], [1 / 3, 2 / 3])
    print("\n  tercile win%%/meanR per year (bands q=%.2f/%.2f):" % (q[0], q[1]), flush=True)
    for Y in (2025, 2026):
        terciles([r for r in rows if r["y"] == Y], q, str(Y))
    try:
        fr = signals(native_30m(sorted(load_archive("15m")[1], key=lambda b: _f(b.get("start_time", 0)))))
        print("\n  DAEMON out-of-sample (recon bands, n=%d):" % len(fr), flush=True)
        terciles(fr, q, "fwd ")
    except Exception as e:
        print("  (daemon skipped: %s)" % e, flush=True)


if __name__ == "__main__":
    main()
