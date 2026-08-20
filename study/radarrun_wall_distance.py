"""Hypothesis (user 2026-08-20): Radar Runner LOSERS are where the entry is CLOSER to the OPPOSING wall than to the
WITH-trade wall. For a LONG (support breakout up): with-wall = nearest SUPPORT below (backing) ; opp-wall = nearest
RESISTANCE above (ceiling/obstacle). Mirror for SHORT. Classify each RR trade opp_closer (opp_dist < with_dist) vs
with_closer, compare win% + expectancy, OOS-split. If the hypothesis holds, opp_closer trades lose materially more.
Live sources 15c/30c/30bkt (+1h). TP 0.25% candle-SL, higher-TF barrier. Walls formed causally (<= signal bar). IN-SAMPLE.
python study/radarrun_wall_distance.py"""
import os, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
from study.radarrun_winrate_dd import sim
from app import absorption_level_detect as AL
FEE, SLIP, TP, H, RM = 0.0004, 0.0003, 0.0025, 200, 3.0
CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("bucket", "study/recon_archive", "30m"), ("clock", "study/clock_archive", "1h"),
         ("bucket", "study/recon_archive", "1h")]


def load_A(root, tf):
    return sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))


def all_walls(A):
    """chunked AL.detect -> S/R lists of (i0_global, price), sorted by i0."""
    n = len(A); Sw = []; Rw = []; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); Sl = A[c0:c1]
        try:
            ws = AL.detect(Sl, skip_last=False, radar_mult=RM)
        except Exception:
            ws = []
        for w in ws:
            side = w.get("side"); P = _f(w.get("price")); i0 = int(w.get("i0", -1))
            if side in ("S", "R") and P > 0 and i0 >= 0:
                (Sw if side == "S" else Rw).append((i0 + c0, P))
        if c1 >= n:
            break
        c0 += step - 1000
    Sw.sort(); Rw.sort()
    return Sw, Rw


def nearest(walls, i0s, k, entry, below):
    """nearest wall PRICE (formed <= k) that is below/above entry. returns dist or None."""
    idx = bisect.bisect_right(i0s, k)                      # walls formed at/before bar k
    best = None
    for j in range(idx):
        p = walls[j][1]
        if below and p < entry:
            d = entry - p
            if best is None or d < best:
                best = d
        elif (not below) and p > entry:
            d = p - entry
            if best is None or d < best:
                best = d
    return best


def main():
    print("RR LOSER hypothesis: entry closer to OPP wall than WITH wall = loser? | TP0.25%% candle-SL | OOS | IN-SAMPLE\n", flush=True)
    for dsname, root, tf in CELLS:
        A = load_A(root, tf); n = len(A)
        Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        Sw, Rw = all_walls(A); S_i0 = [w[0] for w in Sw]; R_i0 = [w[0] for w in Rw]
        sigs = detect(A, SLBUF.get(tf, 0.003))[0]
        rows = []; last = -1
        for (k, s, entry, sl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0); last = k + int(off)
            yr = datetime.fromtimestamp(float(ST[k]), tz=timezone.utc).year
            sup = nearest(Sw, S_i0, k, entry, below=True)     # support below
            res = nearest(Rw, R_i0, k, entry, below=False)    # resistance above
            if s > 0:
                with_d, opp_d = sup, res                       # long: with=support below, opp=resistance above
            else:
                with_d, opp_d = res, sup                       # short: with=resistance above, opp=support below
            grp = "na"
            if with_d is not None and opp_d is not None:
                grp = "opp_closer" if opp_d < with_d else "with_closer"
            rows.append((net, yr, grp))
        def stat(g, yr):
            r = [x[0] for x in rows if x[2] == g and x[1] == yr]
            if not r:
                return "n=0"
            a = np.array(r) * 100.0
            return "n=%-4d win%4.1f%% exp%+.4f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())
        na = sum(1 for x in rows if x[2] == "na")
        print("================ %s %s  (%d RR trades, %d n/a no-both-walls) ================" % (dsname, tf, len(rows), na), flush=True)
        for g in ("opp_closer", "with_closer"):
            print("  %-12s  IS %s | OOS %s" % (g, stat(g, 2025), stat(g, 2026)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
