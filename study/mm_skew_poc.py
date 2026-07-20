"""Refined MM/Skew strategy on 1h (latest data):
  LONG  = bull & skew>0 & panel-2 non-locked spread >= +35 & close > POC-baseline
  SHORT = bear & skew<0 & panel-2 non-locked spread <= -35 & close < POC-baseline
  (dropped: close-on-the-wick.  POC-baseline = 5% EMA of poc_price, causal, per pivot_detect.)
SL 0.1% beyond high/low, TP = RR*SL, first-touch SL-first, win/loss. Per side + equity, RR sweep.
Also isolates the POC-baseline filter's marginal effect (with vs without).
Run:  python study/mm_skew_poc.py
"""
from __future__ import annotations
import os, sys, math, statistics
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app import pivot_detect as PD
from app.footprint_panel import profile_skewness
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

RRS = [0.5, 0.7, 1.0, 1.5]
THR = 35.0


def build():
    _, raws, _ = load_archive("1h")
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r); d["open"] = o; d["close"] = c; d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l
        d["tv"] = r.get("target_vol", 0.0) or 0.0; d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o; d["poc"] = float(r.get("poc_price", 0.0) or 0.0)
        A.append(d)
    n = len(A)
    share = PD.eff_causal_share(A); spr = (2.0 * np.asarray(share, float) - 1.0) * 100.0
    poc = [a["poc"] for a in A]; base = [0.0] * n
    base[0] = poc[0] if poc[0] > 0 else next((p for p in poc if p > 0), 0.0)
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95 if poc[k] > 0 else base[k - 1]
    for i in range(n):
        A[i]["spread"] = float(spr[i]); A[i]["base"] = base[i]
    first = next((i for i in range(n) if A[i]["tv"] >= 100000.0
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, n))]) >= 100000.0), n)
    span = (A[-1].get("start_time", 0) - A[first].get("start_time", 0)) / 86400.0
    return A[first:], span


def sig(b, use_poc=True, use_spread=True):
    if b["sk"] is None:
        return 0
    if b["up"] and b["sk"] > 0 and (not use_spread or b["spread"] >= THR) and (not use_poc or b["c"] > b["base"]):
        return +1
    if b["dn"] and b["sk"] < 0 and (not use_spread or b["spread"] <= -THR) and (not use_poc or b["c"] < b["base"]):
        return -1
    return 0


def collect(M, rr, use_poc=True, use_spread=True):
    out = []
    for i in range(len(M) - 1):
        s = sig(M[i], use_poc, use_spread)
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            continue
        out.append(dict(side=s, win=(res[0] == "TP"), retf=res[1], jclose=res[2], i=i))
    return out


def equity(M, rr, use_poc=True, use_spread=True, fee=0.0, side_only=0):
    bal = S.BAL0; i = 0; n = w = 0; peak = bal; dd = 0.0
    while i < len(M) - 1:
        s = sig(M[i], use_poc, use_spread)
        if s == 0 or (side_only and s != side_only):
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * res[1] - notional * fee
        n += 1; w += (1 if res[0] == "TP" else 0)
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = res[2] + 1
    return (bal / S.BAL0 - 1) * 100.0, n, (100 * w / n if n else float("nan")), dd * 100.0


def wl(sigs, side=None):
    ss = sigs if side is None else [x for x in sigs if x["side"] == side]
    if not ss:
        return (float("nan"), 0)
    return (100.0 * sum(1 for x in ss if x["win"]) / len(ss), len(ss))


def main():
    M, span = build()
    print(f"mature 1h bars: {len(M)}   span: {span:.1f} days (latest data)")
    print("LONG = bull & skew>0 & spread>=+35 & close>POC-base ; SHORT = bear & skew<0 & spread<=-35 & close<POC-base\n")

    print("=" * 94)
    print("WIN% per side per RR   [break-even = 1/(1+RR)]")
    print("=" * 94)
    print(f"  {'RR':>5} {'BE%':>5} | {'ALL n/win':>14} | {'LONG n/win':>14} | {'SHORT n/win':>15}")
    for rr in RRS:
        s = collect(M, rr); be = 100.0 / (1 + rr)
        a = wl(s); lo = wl(s, +1); sh = wl(s, -1)
        print(f"  1:{rr:>3} {be:>4.0f} | {a[1]:>4} {a[0]:>7.1f}% | {lo[1]:>4} {lo[0]:>7.1f}% | {sh[1]:>4} {sh[0]:>7.1f}%")

    print("\n" + "=" * 94)
    print("EQUITY (one-at-a-time, notional=balance)  gross | net@0.08%   — ALL / LONG-only / SHORT-only")
    print("=" * 94)
    for rr in RRS:
        ga, na, wa, da = equity(M, rr, fee=0.0); _, _, _, _ = 0, 0, 0, 0
        gan, nan_, wan, dan = equity(M, rr, fee=0.0008)
        gl, nl, wlw, dl = equity(M, rr, fee=0.0, side_only=+1); gln, _, _, _ = equity(M, rr, fee=0.0008, side_only=+1)
        gs, ns, wsw, ds = equity(M, rr, fee=0.0, side_only=-1); gsn, _, _, _ = equity(M, rr, fee=0.0008, side_only=-1)
        print(f"  1:{rr}: ALL g{ga:+.1f}%/n{gan:+.1f}%(#{na},dd{da:.0f}) | "
              f"LONG g{gl:+.1f}%/n{gln:+.1f}%(#{nl}) | SHORT g{gs:+.1f}%/n{gsn:+.1f}%(#{ns})")

    print("\n" + "=" * 94)
    print("MARGINAL EFFECT of each filter (win% @ RR 1:1.0, per side)")
    print("=" * 94)
    combos = [("spread only (no POC)", True, False), ("POC only (no spread)", False, True),
              ("spread + POC (full)", True, True)]
    # note: use_poc/use_spread flags map below
    for lbl, up, us in [("base: bull/bear&skew", False, False), ("+ spread>=|35|", False, True),
                        ("+ POC baseline", True, False), ("+ BOTH (full rule)", True, True)]:
        s = collect(M, 1.0, use_poc=up, use_spread=us)
        lo = wl(s, +1); sh = wl(s, -1)
        print(f"  {lbl:22s}: LONG {lo[0]:5.1f}%(n{lo[1]:<3})  SHORT {sh[0]:5.1f}%(n{sh[1]:<3})")

    print("\n" + "=" * 94); print("SPLIT-HALF (win% per side, full rule, RR 1:1.0 & 1:1.5)"); print("=" * 94)
    mid = len(M) // 2
    for rr in (1.0, 1.5):
        for half, sub in (("H1", M[:mid]), ("H2", M[mid:])):
            s = collect(sub, rr); lo = wl(s, +1); sh = wl(s, -1)
            print(f"  1:{rr} {half}: LONG {lo[0]:5.1f}%(n{lo[1]:<2})  SHORT {sh[0]:5.1f}%(n{sh[1]:<2})")
        print()


if __name__ == "__main__":
    main()
