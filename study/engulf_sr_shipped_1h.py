"""Backtest the ACTUAL shipped 1h Engulfing-S/R detector (app/engulf_sr_detect.detect) on the 18-month recon 1h
buckets. Runs the real detector (VA edges + S/R zones + relaxations + confluence-1:2 TP + guards + flow ring), takes
the non-overlapping trade set (taken() basis), walks each with the conservative SL-adverse-first fill, and reports a
fee-aware breakdown + significance (bootstrap CI on mean net + circular-block-shift null on total net).

⚠ The recon (2025-01..2026-06) is the SAME data this candidate was tuned on -> this is an IN-SAMPLE read. The year
split (2025 vs 2026-H1) is a robustness proxy, not a true holdout.

Run: python study/engulf_sr_shipped_1h.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf_sr_detect as E

rng = np.random.default_rng(12345)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; yr = F["year"]; hh = F["h"]; ll = F["l"]; cc = F["c"]
FEE = MA.FEE
PAD = E.SL_PAD

marks = E.detect(A, skip_last=True)


def take(marks):
    """Non-overlap taken(): skip a signal whose entry bar <= the previous trade's EXIT bar. Walk conservative."""
    rows = []; last = -1
    for mk in marks:
        i = mk["i"]
        if i <= last:
            continue
        win, ej = MA.walk(A, i, mk["side"], mk["sl"], mk["tp"], n); last = ej
        e = mk["entry"]; d = abs(e - mk["sl"]) / e; rr = abs(mk["tp"] - e) / abs(e - mk["sl"])
        net = (rr * d if win else -d) - FEE
        rows.append(dict(net=net, win=bool(win), side=mk["side"], src=mk["src"], relaxed=bool(mk["relaxed"]),
                         flow_align=bool(mk["flow_align"]), yr=int(yr[i]), d=d, rr=rr, resolved=(ej < n - 1)))
    return rows


rows = take(marks)


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-22s n=0" % label); return
    nt = np.array([r["net"] for r in rs])
    w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); lo = -nt[nt < 0].sum(); pf = (gg / lo) if lo > 0 else float("inf")
    bal = MA.account(list(nt)); pnl = bal - MA.B0
    meannet = nt.mean() * 100
    # fee-adjusted breakeven win-rate given this cohort's mean stop distance and mean RR
    dm = np.mean([r["d"] for r in rs]); rm = np.mean([r["rr"] for r in rs])
    be = (1 + FEE / dm) / (rm + 1) * 100 if dm > 0 else float("nan")
    print("  %-22s n=%4d  win %5.1f%% (BE~%4.1f%%)  net %+7.1f%%  PF %.2f  mean/tr %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, be, tot, pf, meannet, bal, pnl / MA.B0 * 100))


nl = sum(1 for m in marks if m["side"] > 0)
print("=" * 118)
print("SHIPPED 1h Engulf-S/R detector on 18-mo recon 1h  |  %d buckets  |  raw signals %d (%dL/%dS)  |  taken %d  |  fee %.2f%%/rt"
      % (n, len(marks), nl, len(marks) - nl, len(rows), FEE * 100))
print("  Entry=c2 close, SL 0.1%% beyond c2 extreme, TP 1:1.2 (1:2 on VA+SR confluence). Account $%.0f @10%%x10 compounded." % MA.B0)
print("  IN-SAMPLE: recon is the data this candidate was tuned on; 2025/2026 split = robustness proxy, not a holdout.")
print("=" * 118)
rep("ALL", rows)
rep("LONG", [r for r in rows if r["side"] > 0])
rep("SHORT", [r for r in rows if r["side"] < 0])
print("  --- by year (proxy OOS) ---")
rep("2025", [r for r in rows if r["yr"] == 2025])
rep("2026-H1", [r for r in rows if r["yr"] == 2026])
print("  --- by source ---")
rep("VA only", [r for r in rows if r["src"] == "VA"])
rep("SR only", [r for r in rows if r["src"] == "SR"])
rep("VA+SR confluence", [r for r in rows if r["src"] == "VASR"])
print("  --- by c1 gate / flow ring ---")
rep("strict c1", [r for r in rows if not r["relaxed"]])
rep("relaxed c1", [r for r in rows if r["relaxed"]])
rep("flow-aligned", [r for r in rows if r["flow_align"]])
rep("flow-against", [r for r in rows if not r["flow_align"]])

# ------------------------------------------------------------------ significance
nt = np.array([r["net"] for r in rows])
print("\n" + "-" * 118)
# 1) bootstrap 95% CI on the MEAN per-trade net
B = 10000
means = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(B)]) * 100
lo, hi = np.percentile(means, [2.5, 97.5])
print("bootstrap mean net/trade: %+.4f%%   95%% CI [%+.4f%%, %+.4f%%]   -> %s"
      % (nt.mean() * 100, lo, hi, "CI clears 0" if lo > 0 else "CI INCLUDES 0 (not sig)"))

# 2) circular-block-shift null: shift ALL entry bars by a common random delta (preserves signal spacing), rebuild a
#    structural bracket at the shifted candle, keep side + RR, walk, sum compounded net. p = P(shift net >= real net).
real_tot = (np.prod(1 + nt) - 1)
base = [(m["i"], m["side"], abs(m["tp"] - m["entry"]) / abs(m["entry"] - m["sl"])) for m in marks]  # (i, side, rr)
S = 2000; ge = 0
for _ in range(S):
    dl = int(rng.integers(1, n))
    nets = []; last = -1
    for (i0, side, rr) in base:
        j = (i0 + dl) % n
        if j < 1 or j >= n - 1 or j <= last:
            continue
        e = cc[j]; sl = ll[j] * (1 - PAD) if side > 0 else hh[j] * (1 + PAD)
        if (side > 0 and sl >= e) or (side < 0 and sl <= e):
            continue
        d = abs(e - sl) / e; tp = e + rr * (e * d) * side
        win, ej = MA.walk(A, j, side, sl, tp, n); last = ej
        nets.append((rr * d if win else -d) - FEE)
    st = (np.prod(1 + np.array(nets)) - 1) if nets else -1.0
    ge += (st >= real_tot)
print("circular-block-shift null: real total net %+.1f%%   P(shift >= real) = %.3f over %d shifts   -> %s"
      % (real_tot * 100, ge / S, S, "timing carries signal" if ge / S < 0.05 else "NOT distinguishable from random timing"))
print("-" * 118)
