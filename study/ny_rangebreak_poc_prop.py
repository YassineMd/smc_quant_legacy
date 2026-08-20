"""Prop-MC of the NY short-break POC re-entry — REAL account impact (the add is a 2nd full unit, NOT averaged) + the
midpoint-POC robustness check. Sizing = R-based: unit1 risks base R at its SL (sl1_dist); the add is the SAME notional
as unit1, so trade_R = r1 + r2 where r_i = unit_i net return / sl1_dist. A filled+stopped trade loses >1R (both units) ->
this is what shows leverage-vs-edge. HyroTrader $200k (target10/max6/daily4), day-block MC. Compare BASELINE vs POC(VWAP)
vs POC(MIDPOINT), on 30m clock + 30m bucket (+ pooled). SHORT side. IN-SAMPLE. python study/ny_rangebreak_poc_prop.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR = 0.0004, 0.0003, 0.001, 2.85
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600
TARGET, MAXDD, NPATH, MAXD, RP = 10.0, 6.0, 20000, 400, 0.4
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def _vol(b):
    v = b.get("curr_vol")
    if v is None:
        v = (_f(b.get("buy_vol", 0) or 0) + _f(b.get("sell_vol", 0) or 0))
    return _f(v or 0)


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n); V = np.zeros(n)
    PP = np.zeros(n); HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        V[i] = _vol(b); PP[i] = _f(b.get("poc_price", 0) or 0)
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, V, PP, HR, DATE, WD, n


def sim(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry):
    """SHORT. returns trade_R (r1 + r2) + outcome. r_i = unit net return / sl1_dist (unit2 same notional as unit1)."""
    tpmult = 2.0 if rngpct < TP_THR else 0.5
    sl1 = (sl - entry1) / entry1                                     # unit1 risk fraction
    filled = False; et = ST[k]
    def avg():
        return (entry1 + poc) / 2.0 if filled else entry1
    def tp():
        return avg() - tpmult * rng
    def R_at(exitp, is_tp):
        f = FEE + SLIP + (0.0 if is_tp else SLIP)
        r1 = ((entry1 - exitp) / entry1 - f) / sl1
        if filled:
            r2 = ((poc - exitp) / poc - f) / sl1
            return r1 + r2
        return r1
    for j in range(k + 1, n):
        if ST[j] > et + MAXHOLD:
            return R_at(C[j - 1], False), "end", filled
        hi = Hi[j]; lo = Lo[j]
        if reentry and not filled and hi >= poc:
            filled = True
        if hi >= sl:
            return R_at(sl, False), "sl", filled
        if lo <= tp():
            return R_at(tp(), True), "tp", filled
    return R_at(C[-1], False), "end", filled


def trades(root, tf, mode):
    O, C, Hi, Lo, ST, V, PP, HR, DATE, WD, n = load_arrays(root, tf)
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    out = []
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
        mid = (rhi + rlo) / 2.0
        vsum = sum(V[i] for i in ri)
        vwap = (sum(((Hi[i] + Lo[i] + C[i]) / 3.0) * V[i] for i in ri) / vsum) if vsum > 0 else mid
        ppw = [i for i in ri if PP[i] > 0]                          # footprint POC = curr_vol-weighted per-bar poc_price
        poclvl = (sum(PP[i] * V[i] for i in ppw) / sum(V[i] for i in ppw)) if (ppw and sum(V[i] for i in ppw) > 0) else vwap
        k = None
        for i in bi:
            if C[i] < rlo:
                k = i; break
        if k is None:
            continue
        entry1 = C[k]; rngpct = 100.0 * rng / entry1; sl = whi * (1 + SL_PAD)
        reentry = (mode != "base")
        poc = {"mid": mid, "vwap": vwap, "poc": poclvl}.get(mode, mid)
        if poc <= entry1:
            poc = mid if mid > entry1 else (entry1 + rng * 0.5)
        R, outc, filled = sim(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry)
        out.append((ST[k], R))
    return out


def day_blocks(tr):
    by = {}
    for ts, R in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(R)
    if not by:
        return [], 0
    d0, d1 = min(by), max(by); res = []; d = d0
    while d <= d1:
        res.append(by.get(d, [])); d += timedelta(days=1)
    return res, (d1 - d0).days + 1


def mc(days):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dn in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for R in day:
                eq += RP * R; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if ipeak - eq >= 4.0:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(dn)
    return dict(p=100.0 * passes / NPATH, med=(np.percentile(dtp, 50) if dtp else 0),
                dd99=np.percentile(mdds, 99), worst=max(mdds))


def report(name, tr):
    Rs = np.array([t[1] for t in tr]); days, nd = day_blocks(tr)
    m = mc(days)
    print("  %-26s n=%-4d win%4.1f%% avgR%+.3f  | R0.4 pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
          % (name, len(tr), 100.0 * (Rs > 0).mean(), Rs.mean(), m["p"], m["med"], m["dd99"], m["worst"]), flush=True)


MODES = (("base", "BASELINE (1 unit)"), ("mid", "POC re-entry (MIDPOINT)"),
         ("vwap", "POC re-entry (VWAP)"), ("poc", "POC re-entry (footprint POC)"))


def main():
    print("NY short-break POC re-entry — PROP-MC (REAL 2-unit sizing, base-risk held const) | R0.4 | HyroTrader $200k | IN-SAMPLE", flush=True)
    print("avgR = ACCOUNT expectancy per trade in base-risk units (SUM of both units, NOT per-unit avg). 3 POC-level proxies.\n", flush=True)
    for root, tf in SRCS:
        lab = ("clock" if "clock" in root else "bucket") + " " + tf
        print("---- %s ----" % lab, flush=True)
        for mode, nm in MODES:
            report(nm, trades(root, tf, mode))
    print("---- POOLED 30c + 30bkt ----", flush=True)
    for mode, nm in MODES:
        pooled = []
        for root, tf in SRCS:
            pooled += trades(root, tf, mode)
        pooled.sort()
        report(nm, pooled)


if __name__ == "__main__":
    main()
