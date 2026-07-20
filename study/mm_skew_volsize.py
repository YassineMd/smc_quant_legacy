"""Volatility-scaled position sizing on the FROZEN MM×Skew 1h strategy (study/MMXSKEW.md).
Thesis under test: size UP in low volatility, DOWN in high volatility.

PART A — is there a VOL-CONDITIONAL EDGE? split the strategy's signals by the signal bar's trailing
         volatility (low/mid/high tercile) and compare win% + mean per-trade return. If low-vol trades
         win more, the thesis has a real basis for THIS strategy; if flat, vol-scaling only reshapes risk.
PART B — SIZING SIM: flat notional vs inverse-vol notional (scale = clip(vol_med/vol, cap)). Reported both
         EXPOSURE-MATCHED (mean scale renormalised to 1 -> isolates the TIMING of up/down-sizing) and RAW.
         Metrics: return %, maxDD %, return/DD, per-trade Sharpe.  vol = trailing-40-bar return std (causal).
Run:  python study/mm_skew_volsize.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

M, span = P.build()
# trailing volatility per mature bar (causal): std of trailing-40 close-to-close returns, in %
rets = [0.0] * len(M)
for i in range(1, len(M)):
    if M[i - 1]["c"] > 0:
        rets[i] = (M[i]["c"] - M[i - 1]["c"]) / M[i - 1]["c"]
for i in range(len(M)):
    M[i]["vol"] = statistics.pstdev(rets[max(0, i - 40):i + 1]) * 100.0 if i >= 3 else 0.0
VOL_MED = statistics.median([M[i]["vol"] for i in range(len(M)) if M[i]["vol"] > 0])
print(f"mature 1h bars {len(M)}  span {span:.1f}d   trailing-40 vol: median={VOL_MED:.3f}%  "
      f"p10={statistics.quantiles([M[i]['vol'] for i in range(len(M)) if M[i]['vol']>0], n=10)[0]:.3f}%")


def taken(rr):
    i = 0; out = []
    while i < len(M) - 1:
        s = P.sig(M[i])
        if s == 0:
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        out.append(dict(retf=res[1], win=(res[0] == "TP"), vol=M[i]["vol"], side=s))
        i = res[2] + 1
    return out


def sharpe(pnls):
    if len(pnls) < 2 or statistics.pstdev(pnls) == 0:
        return float("nan")
    return statistics.mean(pnls) / statistics.pstdev(pnls) * math.sqrt(len(pnls))


def run(tk, scales, fee):
    bal = S.BAL0; peak = bal; dd = 0.0; pnls = []
    for t, sc in zip(tk, scales):
        notional = S.POS_FRAC * bal * S.LEV * sc
        pnl = notional * t["retf"] - notional * fee
        pnls.append(pnl / bal); bal += pnl
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    ret = (bal / S.BAL0 - 1) * 100.0
    return ret, dd * 100.0, (ret / (dd * 100.0) if dd > 0 else float("nan")), sharpe(pnls)


print("\n" + "=" * 96)
print("PART A — VOL-CONDITIONAL EDGE  (signals split by trailing vol tercile; is low-vol better?)")
print("=" * 96)
for rr in (1.0, 1.5):
    tk = taken(rr); xs = sorted(tk, key=lambda z: z["vol"]); n = len(xs); c1 = n // 3; c2 = 2 * n // 3
    print(f"  RR 1:{rr}  (n={n})")
    for lbl, lo, hi in (("LOW  vol", 0, c1), ("MID  vol", c1, c2), ("HIGH vol", c2, n)):
        g = xs[lo:hi]
        wr = 100 * sum(1 for x in g if x["win"]) / len(g)
        mret = statistics.mean(x["retf"] for x in g) * 100
        print(f"    {lbl}: n={len(g):<3} win={wr:5.1f}%  mean/trade={mret:+.4f}%  "
              f"vol[{g[0]['vol']:.2f}..{g[-1]['vol']:.2f}]%")


print("\n" + "=" * 96)
print("PART B — SIZING SIM  flat vs inverse-vol (scale=clip(vol_med/vol, cap))")
print("=" * 96)
for rr in (1.0, 1.5):
    tk = taken(rr)
    print(f"\n  RR 1:{rr}  (n={len(tk)})   metric: return% | maxDD% | ret/DD | Sharpe")
    for fee, fl in ((0.0, "gross"), (0.0008, "net")):
        # flat
        rf = run(tk, [1.0] * len(tk), fee)
        print(f"    {fl:5s}  FLAT             : {rf[0]:+7.1f}% | {rf[1]:5.1f}% | {rf[2]:+5.2f} | {rf[3]:+.2f}")
        for cap in ((0.5, 2.0), (0.33, 3.0)):
            raw = [min(cap[1], max(cap[0], VOL_MED / t["vol"])) if t["vol"] > 0 else 1.0 for t in tk]
            ms = statistics.mean(raw); norm = [s / ms for s in raw]     # exposure-matched (mean scale = 1)
            rn = run(tk, norm, fee); rr_ = run(tk, raw, fee)
            print(f"           VOL {cap[0]}-{cap[1]}x matched: {rn[0]:+7.1f}% | {rn[1]:5.1f}% | {rn[2]:+5.2f} | {rn[3]:+.2f}   "
                  f"(raw avg-scale {ms:.2f}x: {rr_[0]:+.1f}% dd{rr_[1]:.0f}%)")
