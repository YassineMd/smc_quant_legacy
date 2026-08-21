"""LONDON-2pm momentum -> NY continuation. Weekdays only. Signal at 2pm (=13:00 UTC, user's Morocco convention): if the
2pm price > London open (07:00 UTC open) -> LONG in NY; if < -> SHORT. Enter at 2pm. TP 0.5%. SL 0.1% beyond the London
range (07:00-13:00 high/low) -- long: londonLow*(1-.001); short: londonHigh*(1+.001). Hold through NY, flatten at 21:00.
Tests the London->NY continuation premise (⚠ S6-H4 found London->NY direction INDEPENDENT). Reports directional hit rate +
P&L + prop-MC + timing variants (2pm=13/14, hold NY/day/2d). clock 15m, IS(2025)/OOS(2026). python study/ny_london2pm_momentum.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, TP_FRAC, SL_PAD = 0.0004, 0.0003, 0.005, 0.001
LON_OPEN = 7; MAXHOLD2D = 48 * 3600
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


def _net(side, entry, exitp, is_tp):
    return side * (exitp - entry) / entry - FEE - SLIP - (0.0 if is_tp else SLIP)


def run(twopm, hold, D):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    tr = []; nlong = 0; dhit = 0; dn = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        m2i = {(HR[i], MN[i]): i for i in idxs}
        o07 = m2i.get((LON_OPEN, 0)); e2 = m2i.get((twopm, 0))
        if o07 is None or e2 is None:
            continue
        lon = [i for i in idxs if LON_OPEN <= HR[i] < twopm]       # London range 07:00 .. 2pm
        if not lon:
            continue
        lonHi = max(Hi[i] for i in lon); lonLo = min(Lo[i] for i in lon)
        lopen = O[o07]; p2 = O[e2]
        if p2 == lopen:
            continue
        side = 1 if p2 > lopen else -1
        if side > 0:
            nlong += 1
        entry = p2; sl = lonLo * (1 - SL_PAD) if side > 0 else lonHi * (1 + SL_PAD)
        tp = entry * (1 + side * TP_FRAC)
        if (sl >= entry or tp <= entry) if side > 0 else (sl <= entry or tp >= entry):
            continue
        # walk window
        if hold == "ny":
            seq = [j for j in idxs if j > e2 and 13 <= HR[j] <= 20]
        elif hold == "day":
            seq = [j for j in idxs if j > e2]
        else:                                                     # 2-day
            seq = [j for j in range(e2 + 1, n) if ST[j] <= ST[e2] + MAXHOLD2D]
        net = None
        for j in seq:
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            if adverse:
                net = _net(side, entry, sl, False); break
            if favor:
                net = _net(side, entry, tp, True); break
        exitp = None
        if net is None:
            exitp = C[seq[-1]] if seq else entry; net = _net(side, entry, exitp, False)
        else:
            exitp = sl if net < 0 and False else exitp            # (exit price not needed further)
        # directional hit: did price at end of NY move in the signalled direction vs entry?
        endp = C[seq[-1]] if seq else entry
        dn += 1; dhit += 1 if (endp > entry) == (side > 0) else 0
        sld = abs(sl - entry) / entry
        tr.append(dict(ts=ST[e2], yr=datetime.fromtimestamp(ST[e2], tz=timezone.utc).year, net=net,
                       r=(net / sld if sld > 0 else 0.0), sld=sld))
    return tr, nlong, (dhit / dn if dn else 0.0), dn


def stat(tr, yr=None):
    r = [t for t in tr if (yr is None or t["yr"] == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t["net"] for t in r]) * 100.0; rm = np.array([t["r"] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, tr):
    m = mc(day_blocks([(t["ts"], t["r"]) for t in tr])[0]) if tr else dict(p=0, dd99=0, worst=0)
    sld = np.mean([t["sld"] for t in tr]) * 100 if tr else 0
    print("  %-28s SLd%.2f%% RR%.2f | ALL %s | IS %s | OOS %s | R0.4 pass%5.1f%% DDp99%4.1f%% worst%4.1f%%"
          % (nm, sld, (TP_FRAC * 100.0 / sld if sld else 0), stat(tr), stat(tr, 2025), stat(tr, 2026), m["p"], m["dd99"], m["worst"]), flush=True)


def main():
    D = load()
    print("LONDON-2pm momentum -> NY | weekdays | 2pm=13:00 UTC | TP 0.5%% | SL 0.1%% past London(07-2pm) range | clock 15m\n", flush=True)
    tr, nl, dhit, dn = run(13, "ny", D)
    print("  signal: long %.0f%% / short %.0f%%  |  directional hit (2pm->21:00 continues London lean) = %.1f%% (vs 50%%, n=%d)\n"
          % (100 * nl / max(1, len(tr) + 0), 100 - 100 * nl / max(1, len(tr)), 100 * dhit, dn), flush=True)
    print("== PRIMARY: 2pm=13:00, hold->21:00 (NY close) ==", flush=True)
    line("2pm=13:00 hold->NY", tr)
    print("\n== timing / hold variants ==", flush=True)
    for tp_ in (13, 14):
        for hd in ("ny", "day", "2d"):
            t2, _, _, _ = run(tp_, hd, D)
            line("2pm=%02d:00 hold=%s" % (tp_, hd), t2)


if __name__ == "__main__":
    main()
