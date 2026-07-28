"""15m Engulfing S/R — NORMAL tier only, with HEAVY absorption (A>=thr OR A_h2>0.5 & A>0) instead of the easy gate.

Everything else = the live rule (last-mitigation regime, engulf marubozu, body>wick, close beyond prev body, tier-skew:
normal wants ANTI-aligned skew; S/R-overlap guard; SL 0.1% off prev candle; TP 1:1.2). Gold cannot occur under a heavy
gate; blue (at-S/R) is classified but NOT reported. Report = normal(red/green) + its US sub-pocket, per side, per year.

CLI: python study/engulf_heavy_normal_15m.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.absorb_engulf_lastmit_15m as ENG

TF = "15m"; HS = (0.0, 0.3, 0.75)


def _fmt(lab, rows):
    st = ENG.stats(rows)
    if st is None:
        print("  %-18s   n=0" % lab); return
    m, win, net, pf, avgsl, mdd, pnl = st
    pfs = "inf " if pf == float("inf") else "%5.2f" % pf
    print("  %-18s %4d  %5.1f%%  %s  %+7.1f%%  %5.2f%%  %5.1f%%  %+10.0f" % (lab, m, win, pfs, net, avgsl, mdd, pnl))


def table(rows, title):
    print("=" * 92)
    print(title)
    print("  %-18s %4s  %5s   %5s  %7s  %6s  %6s  %10s" % ("tier / side", "n", "win%", "PF", "net%", "avgSL", "maxDD", "P&L $"))
    for tlab, tset in (("RED/GREEN", ("normal",)), ("US (cyan/mag)", ("us",)), ("NORMAL+US", ("normal", "us"))):
        sub = [r for r in rows if r["tier"] in tset]
        print("  " + "-" * 88)
        _fmt(tlab + " ALL", sub)
        _fmt(tlab + " LONG", [r for r in sub if r["side"] > 0])
        _fmt(tlab + " SHORT", [r for r in sub if r["side"] < 0])
    print("  " + "-" * 88)
    nu = [r for r in rows if r["tier"] in ("normal", "us")]
    _fmt("NORMAL+US 2025", [r for r in nu if r["yr"] == 2025])
    _fmt("NORMAL+US 2026", [r for r in nu if r["yr"] == 2026])


def main():
    for H in HS:
        rows = ENG.analyze(TF, body_break=True, opp_guard=False, tier_skew=True, heavy=True, heavy_thr=H)
        rows = [r for r in rows if r["tier"] in ("normal", "us")]     # NORMAL tier only (drop any blue at-S/R)
        table(rows, "15m Engulf S/R  |  NORMAL tier ONLY  |  HEAVY absorption A>=%.2f OR (A_h2>0.5 & A>0)  |  non-overlap" % H)
        print()


if __name__ == "__main__":
    main()
