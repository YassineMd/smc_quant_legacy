"""HARD CAUSAL test of FADING the decisive 3:45pm (15:45 UTC) bar. Hypothesis (from the causal filter test): a confirmed
break is an EXTENDED move that reverts, so fade the 3:45 momentum. THR body>=0.5 pre-registered. Two causal formulations:
  FADE-SIGNAL : at 16:00, enter the OPPOSITE of the decisive 15:45 dir (entry = 16:00 open), range bracket (SL 0.1% past
                the range wick on the momentum side, adaptive TP the fade way), 2-day hold. Decision+entry @16:00.
  FADE-BREAK  : only when the break CONFIRMS the 15:45 dir (the trades that bled), take the OPPOSITE at the break close,
                SL 0.1% past the breakout candle extreme (continuation stop), adaptive TP the fade way.
Each vs its WITH counterpart on the SAME days + the ungated break baseline. exp per-unit net %%; avgR net/stop; prop-MC
HyroTrader $200k R0.4; IS(2025)/OOS(2026). Fully causal, no lookahead. python study/ny_break_fade_causal.py"""
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


def walk(side, entry, sl, tp, start, ST, O, C, Hi, Lo, n):
    seq = [j for j in range(start, n) if ST[j] <= ST[start] + MAXHOLD]
    for j in seq:
        adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
        favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
        if adverse:
            return _net(side, entry, sl, False)
        if favor:
            return _net(side, entry, tp, True)
    return _net(side, entry, C[seq[-1]], False) if seq else 0.0


def rec(bag, ts, side, entry, sl, tp, start, ST, O, C, Hi, Lo, n):
    if (tp <= entry or sl >= entry) if side > 0 else (tp >= entry or sl <= entry):
        return
    net = walk(side, entry, sl, tp, start, ST, O, C, Hi, Lo, n); sld = abs(sl - entry) / entry
    bag.append(dict(ts=ts, yr=datetime.fromtimestamp(ts, tz=timezone.utc).year, net=net, r=(net / sld if sld > 0 else 0.0)))


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    B = {k: [] for k in ("base", "ws", "fs", "wb", "fb")}          # baseline / with-sig / fade-sig / with-brk / fade-brk
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
        # 15:45 decisive dir (known at 16:00)
        d15 = 0
        for i in ri:
            if (HR[i], MN[i]) == S15:
                r_ = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / r_ if r_ > 0 else 0.0
                d15 = 0 if bf < THR else (1 if C[i] > O[i] else -1)
        # break (first close beyond, 16-20)
        k = None; bside = 0
        for i in bi:
            if C[i] > rhi:
                k = i; bside = 1; break
            if C[i] < rlo:
                k = i; bside = -1; break
        # BASELINE ungated break (WITH the break direction)
        if k is not None:
            e = C[k]; sl = wlo * (1 - SL_PAD) if bside > 0 else whi * (1 + SL_PAD)
            mlt = TP_LOW if (rng / e * 100.0) < TP_THR else TP_HIGH
            rec(B["base"], ST[k], bside, e, sl, e + bside * mlt * rng, k + 1, ST, O, C, Hi, Lo, n)
        if d15 == 0:
            continue
        # SIGNAL trades: enter at the 16:00 open, WITH (d15) and FADE (-d15). Range bracket.
        k16 = bi[0]; e16 = O[k16]
        mlt16 = TP_LOW if (rng / e16 * 100.0) < TP_THR else TP_HIGH
        for tag, sd in (("ws", d15), ("fs", -d15)):
            sl = wlo * (1 - SL_PAD) if sd > 0 else whi * (1 + SL_PAD)
            rec(B[tag], ST[k16], sd, e16, sl, e16 + sd * mlt16 * rng, k16, ST, O, C, Hi, Lo, n)
        # BREAK trades: only when the break CONFIRMS d15 (the bled trades). WITH = bside, FADE = -bside at break close.
        if k is not None and bside == d15:
            e = C[k]; mlt = TP_LOW if (rng / e * 100.0) < TP_THR else TP_HIGH
            slw = wlo * (1 - SL_PAD) if bside > 0 else whi * (1 + SL_PAD)
            rec(B["wb"], ST[k], bside, e, slw, e + bside * mlt * rng, k + 1, ST, O, C, Hi, Lo, n)
            fs = -bside; slf = Hi[k] * (1 + SL_PAD) if fs < 0 else Lo[k] * (1 - SL_PAD)   # continuation stop past the break candle
            rec(B["fb"], ST[k], fs, e, slf, e + fs * mlt * rng, k + 1, ST, O, C, Hi, Lo, n)
    return B


def stat(ts, yr=None):
    r = [t for t in ts if (yr is None or t["yr"] == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t["net"] for t in r]) * 100.0; rm = np.array([t["r"] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, ts):
    m = mc(day_blocks([(t["ts"], t["r"]) for t in ts])[0]) if ts else dict(p=0, dd99=0, worst=0)
    print("  %-26s | ALL %s | IS %s | OOS %s | R0.4 pass%5.1f%% DDp99%4.1f%% worst%4.1f%%"
          % (nm, stat(ts), stat(ts, 2025), stat(ts, 2026), m["p"], m["dd99"], m["worst"]), flush=True)


def main():
    B = collect()
    print("HARD CAUSAL — FADE the decisive 3:45pm bar | clock 15m | THR>=%.1f | decision+entry @16:00 (signal) or @break | R0.4 HyroTrader\n" % THR, flush=True)
    line("BASELINE break (ungated)", B["base"])
    print("  -- SIGNAL: enter @16:00, decisive-3:45 days only --", flush=True)
    line("WITH 3:45 dir", B["ws"])
    line("FADE 3:45 dir", B["fs"])
    print("  -- BREAK: break confirmed the 3:45 dir (the bled trades) --", flush=True)
    line("WITH the confirmed break", B["wb"])
    line("FADE the confirmed break", B["fb"])


if __name__ == "__main__":
    main()
