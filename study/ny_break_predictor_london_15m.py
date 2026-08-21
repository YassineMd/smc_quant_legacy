"""London-session version of the 3:45 conspiracy: is there a LONDON 15m bar (07:00-15:45 UTC) whose DIRECTION foreshadows
the NY break side (13-16 body range, first 16-21 close beyond)? Scans all 36 slots -- the pre-range London bars (07-12:45)
give hours of LEAD. Per slot: agreement acc = P(bar dir == break side), decisive-body acc (body>=0.5), doji rate, LEAD to
16:00, IS(2025)/OOS(2026). Base-rate controlled; 36-slot scan WILL overfit one -> OOS + a coherent gradient are the real
tests. clock 15m. python study/ny_break_predictor_london_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from math import comb
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; DOJI = 0.10; THR = 0.5
SLOTS = [(h, m) for h in range(7, 16) for m in (0, 15, 30, 45)]   # London 07:00 .. 15:45
ROOT, TF = "study/clock_archive", "15m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, MN, DATE, WD, n


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    rows = []                                                     # (yr, break_side, {slot:(dir,bf)})
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        if rhi <= rlo:
            continue
        side = 0
        for i in bi:
            if C[i] > rhi:
                side = 1; break
            if C[i] < rlo:
                side = -1; break
        if side == 0:
            continue
        sd = {}
        for i in idxs:
            key = (HR[i], MN[i])
            if 7 <= HR[i] <= 15:
                r_ = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / r_ if r_ > 0 else 0.0
                sd[key] = (0 if bf < DOJI else (1 if C[i] > O[i] else -1), bf)
        yr = datetime.fromtimestamp(ST[bi[0]], tz=timezone.utc).year
        rows.append((yr, side, sd))
    return rows


def acc(rows, slot, yr=None, minbf=0.0):
    r = [(sd, dirs.get(slot, (0, 0.0))) for (y, sd, dirs) in rows if (yr is None or y == yr)]
    r = [(s, dd) for (s, (dd, bf)) in r if dd != 0 and bf >= minbf]
    if not r:
        return 0, 0.0
    return len(r), sum(1 for s, dd in r if dd == s) / len(r)


def main():
    rows = collect()
    ns = sum(1 for _, s, _ in rows if s < 0); base = max(ns, len(rows) - ns) / len(rows)
    print("LONDON bar -> NY break side | clock 15m | breaks n=%d  base rate %.1f%% (beat THIS)\n" % (len(rows), 100 * base), flush=True)
    print("  slot   lead   n    acc    IS     OOS  | decisive(body>=.5): n   acc    OOS   doji%", flush=True)
    best_pre = None; best_all = None
    for h, m in SLOTS:
        lead = (16 * 60) - (h * 60 + m + 15)
        nA, aA = acc(rows, (h, m)); _, aI = acc(rows, (h, m), 2025); _, aO = acc(rows, (h, m), 2026)
        nD, aD = acc(rows, (h, m), None, THR); _, aDO = acc(rows, (h, m), 2026, THR)
        dj = np.mean([dirs.get((h, m), (0, 0))[0] == 0 for (_, _, dirs) in rows])
        star = ""
        if best_all is None or aO > best_all[1]:
            best_all = ((h, m), aO)
        if h < 13 and (best_pre is None or aO > best_pre[1]):     # best PRE-RANGE (true London, hours of lead)
            best_pre = ((h, m), aO, aA); star = " <pre"
        print("  %02d:%02d  %3dm  %3d  %.3f  %.3f  %.3f  |  n=%-3d  %.3f  %.3f  %2.0f%%%s"
              % (h, m, lead, nA, aA, aI, aO, nD, aD, aDO, 100 * dj, star), flush=True)
    print("\nBEST overall by OOS acc: %02d:%02d OOS %.3f" % (best_all[0][0], best_all[0][1], best_all[1]), flush=True)
    print("BEST PRE-RANGE (07-12:45, hours of lead) by OOS: %02d:%02d  acc ALL %.3f / OOS %.3f  vs base %.3f"
          % (best_pre[0][0], best_pre[0][1], best_pre[2], best_pre[1], base), flush=True)


if __name__ == "__main__":
    main()
