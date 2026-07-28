import os, sys
sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

RR = S.RR
be = (1 + MA.FEE) / (1 + RR)
print("FEE=%.5f RR=%.3f  break-even win=%.4f" % (MA.FEE, RR, be))

def rep(tf, rows):
    def cell(lab, rr):
        m = len(rr)
        if m == 0:
            print("    %-14s n=0" % lab); return
        nt = np.array([r["net"] for r in rr]); w = int((nt > 0).sum())
        tot = (np.prod(1 + nt) - 1) * 100
        print("    %-14s n=%3d  win %5.1f%%  net %+7.1f%%  p(vs BE)=%.3f"
              % (lab, m, 100*w/m, tot, S.bp(w, m, be)))
    print("\n==== tf=%s  PRIMARY (body, ab2<=0.75, short_close<high, bias ON) ====" % tf)
    print("  total taken:", len(rows))
    cell("ALL", rows)
    L = [r for r in rows if r["side"] > 0]; Sh = [r for r in rows if r["side"] < 0]
    cell("LONG", L); cell("SHORT", Sh)
    for yv in (2025, 2026):
        print("  --- year %d ---" % yv)
        cell("ALL %d" % yv, [r for r in rows if r["yr"] == yv])
        cell("LONG %d" % yv, [r for r in L if r["yr"] == yv])
        cell("SHORT %d" % yv, [r for r in Sh if r["yr"] == yv])

for tf in ("1h", "5m"):
    print("\nprecompute %s ..." % tf, flush=True)
    S.precompute(tf)
    rows = S.analyze_fast(tf, ab2=0.75, engulf="body", use_bias=True, short_close="high", body_frac=0.70)
    rep(tf, rows)
