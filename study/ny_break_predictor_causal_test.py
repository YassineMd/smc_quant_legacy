"""HARD CAUSAL test of the 3:45pm break-side predictor as a TRADE FILTER. Decision uses ONLY info known at 16:00 (the
15:45 bar's body dir + strength); entry = the actual break close (>=16:00); barrier walk forward; fees. Tests whether
gating the validated SHORT break on a CONFIRMED-bearish 15:45 bar (decisive down body, |body|>=THR) improves P&L /
prop-MC vs the ungated short -- or just cuts trade count. NO lookahead: (a) 15:45 + THR=0.5 pre-registered from theory
(last range bar, >50%% body = decisive); (b) WALK-FORWARD -- pick the best predictor bar on 2025 ONLY, freeze, apply to
2026; (c) threshold sensitivity 0.4/0.5/0.6. Also the LONG gated on confirmed-bullish. clock 15m, IS(2025)/OOS(2026),
HyroTrader $200k R0.4 prop-MC. python study/ny_break_predictor_causal_test.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600; THR = 0.5
SLOTS = [(h, m) for h in (13, 14, 15) for m in (0, 15, 30, 45)]
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


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    trades = []                                                   # each: dict(ts, yr, side, net, r, slotbf={slot:(dir,bf)})
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
        k = None; side = 0
        for i in bi:
            if C[i] > rhi:
                k = i; side = 1; break
            if C[i] < rlo:
                k = i; side = -1; break
        if side == 0:
            continue
        slotbf = {}
        for i in ri:
            r_ = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / r_ if r_ > 0 else 0.0
            bdir = 0 if bf < 0.10 else (1 if C[i] > O[i] else -1)
            slotbf[(HR[i], MN[i])] = (bdir, bf)
        entry = C[k]; sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        mult = TP_LOW if (rng / entry * 100.0) < TP_THR else TP_HIGH; tp = entry + side * mult * rng
        seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD]
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
        sld = abs(sl - entry) / entry
        trades.append(dict(ts=ST[k], yr=datetime.fromtimestamp(ST[k], tz=timezone.utc).year, side=side,
                           net=net, r=(net / sld if sld > 0 else 0.0), slotbf=slotbf))
    return trades


def conf(t, slot, want_side, thr):                               # 15:45 (or slot) confirms `want_side` decisively?
    dd, bf = t["slotbf"].get(slot, (0, 0.0))
    return dd == want_side and bf >= thr


def stat(ts, yr=None):
    r = [t for t in ts if (yr is None or t["yr"] == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t["net"] for t in r]) * 100.0; rm = np.array([t["r"] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def propline(nm, ts, ntot=None):
    m = mc(day_blocks([(t["ts"], t["r"]) for t in ts])[0]) if ts else dict(p=0, med=0, dd99=0, worst=0)
    cov = (" cov%3.0f%%" % (100.0 * len(ts) / ntot)) if ntot else ""
    print("    %-26s%s | ALL %s | IS %s | OOS %s | R0.4 pass%5.1f%% DDp99%4.1f%% worst%4.1f%%"
          % (nm, cov, stat(ts), stat(ts, 2025), stat(ts, 2026), m["p"], m["dd99"], m["worst"]), flush=True)


def main():
    T = collect()
    sh = [t for t in T if t["side"] == -1]; lo = [t for t in T if t["side"] == 1]
    print("HARD CAUSAL test — 3:45pm predictor as a TRADE FILTER on the NY break | clock 15m | R0.4 HyroTrader | decision @16:00, entry @break", flush=True)
    print("total breaks %d (short %d / long %d). THR body>=%.1f pre-registered.\n" % (len(T), len(sh), len(lo), THR), flush=True)

    print("== SHORT gated on CONFIRMED-BEARISH 15:45 (pre-registered) ==", flush=True)
    s15 = (15, 45)
    gated = [t for t in sh if conf(t, s15, -1, THR)]
    antg = [t for t in sh if not conf(t, s15, -1, THR)]
    propline("ALL short breaks (ungated)", sh)
    propline("GATED: 15:45 conf-bear", gated, ntot=len(sh))
    propline("ANTI: not conf-bear", antg, ntot=len(sh))

    print("\n== threshold sensitivity (15:45 conf-bear gate) ==", flush=True)
    for thr in (0.4, 0.5, 0.6):
        g = [t for t in sh if conf(t, s15, -1, thr)]
        propline("THR>=%.1f" % thr, g, ntot=len(sh))

    print("\n== WALK-FORWARD: pick best predictor bar on 2025 (IS gated-short exp), freeze -> 2026 (OOS) ==", flush=True)
    best = None
    for slot in SLOTS:
        gIS = [t for t in sh if t["yr"] == 2025 and conf(t, slot, -1, THR)]
        if len(gIS) < 15:
            continue
        e = np.mean([t["net"] for t in gIS])
        if best is None or e > best[1]:
            best = (slot, e)
    if best:
        slot = best[0]
        gOOS = [t for t in sh if t["yr"] == 2026 and conf(t, slot, -1, THR)]
        allOOS = [t for t in sh if t["yr"] == 2026]
        print("   IS-best bar = %02d:%02d (IS gated exp %+.3f%%). Frozen -> OOS:" % (slot[0], slot[1], best[1] * 100), flush=True)
        print("      OOS ungated short : %s" % stat(allOOS), flush=True)
        print("      OOS gated (%02d:%02d): %s" % (slot[0], slot[1], stat(gOOS)), flush=True)
        print("      (15:45 pre-reg OOS gated: %s)" % stat([t for t in sh if t["yr"] == 2026 and conf(t, s15, -1, THR)]), flush=True)

    print("\n== LONG gated on CONFIRMED-BULLISH 15:45 (is the dead long revived?) ==", flush=True)
    propline("ALL long breaks (ungated)", lo)
    propline("GATED: 15:45 conf-bull", [t for t in lo if conf(t, s15, 1, THR)], ntot=len(lo))


if __name__ == "__main__":
    main()
