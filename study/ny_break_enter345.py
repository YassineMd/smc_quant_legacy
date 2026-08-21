"""ENTER AT 3:45 (16:00, the 15:45 bar's close) instead of chasing the break. SHORT only. On a decisive-bearish 15:45
(body>=THR), enter SHORT at the 16:00 open (= 3:45 close) -- BEFORE the break, at a better (higher, near the range edge)
price than the break close. SL 0.1%% past the range wick, adaptive TP, 2-day hold. Causal (decision + entry @16:00).
Compare vs the validated break-entry short. Also the aligned-only diagnostic (3:45 entry vs break entry on the SAME days
that do break short) to isolate the entry-price benefit. exp per-unit net %%; avgR net/stop; prop-MC HyroTrader $200k
R0.4; IS(2025)/OOS(2026). clock 15m. python study/ny_break_enter345.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600; THR = 0.5; S15 = (15, 45)
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


def walk(side, entry, sl, tp, start, ST, C, Hi, Lo, n):
    seq = [j for j in range(start, n) if ST[j] <= ST[start] + MAXHOLD]
    for j in seq:
        if (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl):
            return _net(side, entry, sl, False)
        if (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp):
            return _net(side, entry, tp, True)
    return _net(side, entry, C[seq[-1]], False) if seq else 0.0


def rec(bag, ts, side, entry, sl, tp, start, ST, C, Hi, Lo, n):
    if (tp >= entry or sl <= entry) if side < 0 else (tp <= entry or sl >= entry):
        return
    net = walk(side, entry, sl, tp, start, ST, C, Hi, Lo, n); sld = abs(sl - entry) / entry
    bag.append(dict(ts=ts, yr=datetime.fromtimestamp(ts, tz=timezone.utc).year, net=net, r=(net / sld if sld > 0 else 0.0)))


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    B = {k: [] for k in ("brk_all", "e345_all", "brk_algn", "e345_algn")}
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        whi = max(Hi[i] for i in ri); wlo = min(Lo[i] for i in ri); rng = whi - wlo
        if rng <= 0:
            continue
        d15 = 0
        for i in ri:
            if (HR[i], MN[i]) == S15:
                r_ = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / r_ if r_ > 0 else 0.0
                d15 = 0 if bf < THR else (1 if C[i] > O[i] else -1)
        k = None; bside = 0
        for i in bi:
            if C[i] < rlo:
                k = i; bside = -1; break
            if C[i] > rhi:
                k = i; bside = 1; break
        # baseline: validated SHORT break (enter at break close)
        if bside == -1:
            e = C[k]; mlt = TP_LOW if (rng / e * 100.0) < TP_THR else TP_HIGH
            rec(B["brk_all"], ST[k], -1, e, whi * (1 + SL_PAD), e - mlt * rng, k + 1, ST, C, Hi, Lo, n)
        if d15 != -1:                                             # only decisive-bearish 3:45 days for the 3:45 entry
            continue
        # ENTER-345: SHORT at the 16:00 open (= 3:45 close), every decisive-bearish day (causal, no break wait)
        k16 = bi[0]; e16 = O[k16]; mlt = TP_LOW if (rng / e16 * 100.0) < TP_THR else TP_HIGH
        rec(B["e345_all"], ST[k16], -1, e16, whi * (1 + SL_PAD), e16 - mlt * rng, k16, ST, C, Hi, Lo, n)
        # aligned diagnostic: same days that DO break short -> 3:45 entry vs break entry (entry-price benefit)
        if bside == -1:
            e = C[k]; mltb = TP_LOW if (rng / e * 100.0) < TP_THR else TP_HIGH
            rec(B["brk_algn"], ST[k], -1, e, whi * (1 + SL_PAD), e - mltb * rng, k + 1, ST, C, Hi, Lo, n)
            rec(B["e345_algn"], ST[k16], -1, e16, whi * (1 + SL_PAD), e16 - mlt * rng, k16, ST, C, Hi, Lo, n)
    return B


def stat(ts, yr=None):
    r = [t for t in ts if (yr is None or t["yr"] == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t["net"] for t in r]) * 100.0; rm = np.array([t["r"] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, ts, prop=True):
    ex = ""
    if prop and ts:
        m = mc(day_blocks([(t["ts"], t["r"]) for t in ts])[0]); ex = " | R0.4 pass%5.1f%% DDp99%4.1f%% worst%4.1f%%" % (m["p"], m["dd99"], m["worst"])
    print("  %-30s | ALL %s | IS %s | OOS %s%s" % (nm, stat(ts), stat(ts, 2025), stat(ts, 2026), ex), flush=True)


def main():
    B = collect()
    print("ENTER AT 3:45 (16:00) vs chase the break — SHORT only, decisive-bearish 3:45, clock 15m, causal\n", flush=True)
    line("BASELINE: break-entry short (all)", B["brk_all"])
    line("ENTER-345: short @16:00 (all dec-bear)", B["e345_all"])
    print("\n  -- diagnostic: SAME days that break short (3:45 entry vs break entry, entry-price benefit) --", flush=True)
    line("  break entry (on dec-bear days)", B["brk_algn"], prop=False)
    line("  3:45 entry (on dec-bear days)", B["e345_algn"], prop=False)


if __name__ == "__main__":
    main()
