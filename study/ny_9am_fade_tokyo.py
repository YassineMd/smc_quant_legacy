"""9am-bar FADE. If the 9am bar is bullish -> SHORT; if bearish -> LONG (fade). TP 0.2%% NET; SL beyond the Tokyo-session
(00:00 -> 9am) high/low. 9am = 08:00 UTC (user's Morocco = UTC+1 convention; 09:00 UTC tested as a variant). '9am bar' =
the 1h candle (08:00-09:00) primary, 15m (08:00-08:15) as a variant. Entry at the bar's close (market). Hold to end of
day. Reports the fade directional hit rate + P&L + prop-MC, vs the WITH (non-fade) counterpart, timing variants. clock
15m, IS(2025)/OOS(2026). python study/ny_9am_fade_tokyo.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, TP_NET, SL_PAD = 0.0004, 0.0003, 0.002, 0.0005
MAXHOLD2D = 48 * 3600
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


def run(H, bar_tf, fade, hold, D):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    tr = []; dhit = 0; dn = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        m2i = {(HR[i], MN[i]): i for i in idxs}
        # Tokyo range 00:00 -> H:00
        tok = [i for i in idxs if HR[i] < H]
        if not tok:
            continue
        tHi = max(Hi[i] for i in tok); tLo = min(Lo[i] for i in tok)
        # 9am bar
        if bar_tf == "1h":
            b0 = m2i.get((H, 0)); b3 = m2i.get((H, 45)); ent = m2i.get((H + 1, 0))
            if b0 is None or b3 is None or ent is None:
                continue
            bopen = O[b0]; bclose = C[b3]
        else:                                                     # 15m 9am bar
            b0 = m2i.get((H, 0)); ent = m2i.get((H, 15))
            if b0 is None or ent is None:
                continue
            bopen = O[b0]; bclose = C[b0]
        if bclose == bopen:
            continue
        bar_bull = bclose > bopen
        side = (-1 if bar_bull else 1) if fade else (1 if bar_bull else -1)
        entry = O[ent]
        sl = tLo * (1 - SL_PAD) if side > 0 else tHi * (1 + SL_PAD)
        tp = entry * (1 + side * (TP_NET + FEE + SLIP))
        if (sl >= entry or tp <= entry) if side > 0 else (sl <= entry or tp >= entry):
            continue
        if hold == "day":
            seq = [j for j in idxs if j > ent]
        else:
            seq = [j for j in range(ent + 1, n) if ST[j] <= ST[ent] + MAXHOLD2D]
        net = None
        for j in seq:
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            if adverse:
                net = _net(side, entry, sl, False); break
            if favor:
                net = _net(side, entry, tp, True); break
        if net is None:
            net = _net(side, entry, C[seq[-1]], False) if seq else 0.0
        endp = C[seq[-1]] if seq else entry
        dn += 1; dhit += 1 if (endp > entry) == (side > 0) else 0
        sld = abs(sl - entry) / entry
        tr.append(dict(ts=ST[ent], yr=datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, net=net,
                       r=(net / sld if sld > 0 else 0.0), sld=sld))
    return tr, (dhit / dn if dn else 0), dn


def stat(tr, yr=None):
    r = [t for t in tr if (yr is None or t["yr"] == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t["net"] for t in r]) * 100.0; rm = np.array([t["r"] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, tr):
    m = mc(day_blocks([(t["ts"], t["r"]) for t in tr])[0]) if tr else dict(p=0, dd99=0, worst=0)
    sld = np.mean([t["sld"] for t in tr]) * 100 if tr else 0
    print("  %-26s SLd%.2f%% RR%.2f | ALL %s | IS %s | OOS %s | R0.4 pass%5.1f%% DDp99%4.1f%%"
          % (nm, sld, (TP_NET * 100.0 / sld if sld else 0), stat(tr), stat(tr, 2025), stat(tr, 2026), m["p"], m["dd99"]), flush=True)


def main():
    D = load()
    print("9am-bar FADE -> TP 0.2%% net, SL past Tokyo(00-9am) high/low | weekdays | ALL DATA = 15m CLOCK, 9am bar = 15m bar\n", flush=True)
    print("== PRIMARY: 15m-CLOCK 9am bar, FADE, hold->EoD ==", flush=True)
    for H in (8, 9):
        trf, dhf, dnf = run(H, "15m", True, "day", D)
        trw, dhw, _ = run(H, "15m", False, "day", D)
        lab = "09:00 UTC (=9am UTC)" if H == 9 else "08:00 UTC (=9am Morocco)"
        print("  -- 9am = %s --" % lab, flush=True)
        print("     FADE directional hit (EoD move matches faded side) = %.1f%% (vs 50%%, n=%d)" % (100 * dhf, dnf), flush=True)
        line("FADE 9am=%02d:00 15m" % H, trf)
        line("WITH 9am=%02d:00 15m" % H, trw)
    print("\n== 15m 9am bar, FADE, hold=2-day ==", flush=True)
    for H in (8, 9):
        t3, _, _ = run(H, "15m", True, "2d", D)
        line("fade 9am=%02d:00 15m 2d" % H, t3)
    print("\n== (reference) 1h 9am bar ==", flush=True)
    for H in (8, 9):
        t4, _, _ = run(H, "1h", True, "day", D)
        line("fade 9am=%02d:00 1h" % H, t4)


if __name__ == "__main__":
    main()
