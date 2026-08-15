"""Combined 1h + 15m RADAR RUNNER signal census (NO filter). Per TF: tradeable N (non-overlap quick-TP exit), per-trade
expectancy in R (cand+0.3cap SL, TP=0.5%, 3bps slip), monthly rate, side skew. Then COMBINED N + rate + per-day
distribution, and a REDUNDANCY check (what fraction of 1h signals have a same-side 15m signal within +-2h -> the two TFs
firing on the same breakout, which shouldn't be double-counted). Ends with time-to-target under the blended edge."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; TP = 0.005; H = 200; FEE = 0.0004; SLIP = 0.0003


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def detect_tradeable(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])

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

    trades = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.003), rlo) if up else min(Hi[k] * (1 + 0.003), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        trades.append({"ts": float(ST[k]), "side": side, "acctR": net / dist})
        last = k + int(off)
    span = (ST[-1] - ST[0]) / 86400.0
    return trades, span


def census(tag, trades, span):
    mo = span / 30.437
    exp = np.mean([t["acctR"] for t in trades])
    win = 100 * np.mean([1.0 if t["acctR"] > 0 else 0.0 for t in trades])
    nl = sum(1 for t in trades if t["side"] == "S")
    print("  %-8s N=%-4d  (long %d / short %d)  %.1f/mo  exp=%+.3fR  win=%.0f%%"
          % (tag, len(trades), nl, len(trades) - nl, len(trades) / mo, exp, win))
    return exp


def main():
    t1h, s1h = detect_tradeable("1h")
    t15, s15 = detect_tradeable("15m")
    span = max(s1h, s15); mo = span / 30.437
    print("Combined 1h + 15m RADAR RUNNER census (NO filter, cand+0.3cap SL, TP=0.5%%, 3bps slip)  span=%.1f mo" % mo)
    e1 = census("1h", t1h, s1h)
    e15 = census("15m", t15, s15)

    allt = sorted(t1h + t15, key=lambda t: t["ts"])
    comb_mo = len(allt) / mo
    blended = np.mean([t["acctR"] for t in allt])
    print("  COMBINED N=%-4d  %.1f/mo  %.2f/day  blended exp=%+.3fR" % (len(allt), comb_mo, len(allt) / span, blended))

    # per-day distribution (combined)
    byday = {}
    for t in allt:
        d = int(t["ts"] // 86400); byday[d] = byday.get(d, 0) + 1
    from collections import Counter
    dist = Counter(byday.values()); total_days = int(span) + 1
    print("  per-DAY (combined): active=%d/%d (%.0f%%)  1=%d 2=%d 3=%d 4+=%d  max/day=%d"
          % (len(byday), total_days, 100 * len(byday) / total_days, dist.get(1, 0), dist.get(2, 0),
             dist.get(3, 0), sum(v for kk, v in dist.items() if kk >= 4), max(byday.values())))

    # redundancy: 1h signals with a same-side 15m signal within +-2h
    ts15 = {"S": [], "R": []}
    for t in t15:
        ts15[t["side"]].append(t["ts"])
    for k in ts15:
        ts15[k].sort()
    import bisect
    near = 0
    for t in t1h:
        arr = ts15[t["side"]]; lo = bisect.bisect_left(arr, t["ts"] - 7200); hi = bisect.bisect_right(arr, t["ts"] + 7200)
        if hi > lo:
            near += 1
    print("  REDUNDANCY: %d/%d (%.0f%%) of 1h signals have a same-side 15m signal within +-2h"
          % (near, len(t1h), 100 * near / len(t1h)))

    # time to +10% target under blended edge (independent-trade approximation)
    print("  time to +10%% target (blended %+.3fR/trade, %.1f trades/mo):" % (blended, comb_mo))
    for R in (0.005, 0.0075, 0.010):
        ntr = 0.10 / (blended * R)
        print("    R=%.2f%%: ~%.0f trades  (~%.1f months)" % (R * 100, ntr, ntr / comb_mo))


if __name__ == "__main__":
    main()
