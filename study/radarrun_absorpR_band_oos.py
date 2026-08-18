"""Does a WINDOWED absorpR rule beat take-all out-of-sample? Fit the band on 2025, test on UNSEEN 2026.

RR prop exit (0.2% TP + candle-SL). For each tf: (1) grid-fit the absorpR band [lo,hi] that maximizes 2025 net return
(keeping >=15% of trades), then test that FIXED band on 2026 vs take-all baseline (bootstrap 95% CI on the per-trade
net lift + the fraction of trades kept); (2) the same OOS check for a few PRE-SPECIFIED intuitive bands. A windowed
rule 'works' only if its 2026 net-per-trade beats baseline with a CI that excludes 0 -- and you weigh that against the
trades it drops. Usage: python study/radarrun_absorpR_band_oos.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_15m_sldist_test import build

INF = np.inf
LOS = [-INF, -0.5, -0.25, 0.0, 0.25]
HIS = [0.5, 1.0, 1.5, INF]
FIXED = [(-0.25, INF, "absorpR >= -0.25"), (0.0, INF, "absorpR >= 0"),
         (0.0, 1.5, "absorpR in [0, 1.5]"), (-0.25, 1.5, "absorpR in [-0.25, 1.5]")]


def oos(net, m_te, base_te, rng):
    """bootstrap 95% CI of (band 2026 net-per-trade  -  take-all 2026 net-per-trade)."""
    band = net[m_te]
    if len(band) < 20:
        return None
    d = [rng.choice(band, len(band)).mean() - rng.choice(base_te, len(base_te)).mean() for _ in range(3000)]
    lo, hi = np.percentile(d, [2.5, 97.5])
    return band.mean(), len(band), lo, hi


def main():
    for tf in ("5m", "15m"):
        sld, aR, net, win, yr = build(tf)
        fin = np.isfinite(aR); aR, net, yr = aR[fin], net[fin], yr[fin]
        tr = yr == 2025; te = yr == 2026
        base_tr, base_te = net[tr], net[te]
        rng = np.random.default_rng(7)
        print("\n================  %s  ================" % tf, flush=True)
        print("  take-all baseline: 2025 %+.4f%% (n=%d)  |  2026 %+.4f%% (n=%d)"
              % (100 * base_tr.mean(), tr.sum(), 100 * base_te.mean(), te.sum()), flush=True)

        # 1. grid-fit best band on 2025, test 2026
        best = None
        for lo in LOS:
            for hi in HIS:
                if lo >= hi:
                    continue
                m = tr & (aR >= lo) & (aR < hi)
                if m.sum() < 0.15 * tr.sum():
                    continue
                v = net[m].mean()
                if best is None or v > best[2]:
                    best = (lo, hi, v)
        lo, hi, fit_net = best
        r = oos(net, te & (aR >= lo) & (aR < hi), base_te, rng)
        print("\n  FIT on 2025 -> best band absorpR in [%s, %s]  (2025 net %+.4f%%)"
              % ("%.2f" % lo if np.isfinite(lo) else "-inf", "%.2f" % hi if np.isfinite(hi) else "+inf", 100 * fit_net),
              flush=True)
        if r:
            m, n, clo, chi = r
            print("    OOS 2026: band net %+.4f%% (n=%d, kept %.0f%% of trades)  |  lift vs take-all %+.4f%%  95%%CI[%+.4f,%+.4f]  cross0? %s"
                  % (100 * m, n, 100 * n / te.sum(), 100 * (m - base_te.mean()), 100 * clo, 100 * chi,
                     "YES" if clo < 0 < chi else "NO -> beats take-all"), flush=True)

        # 2. fixed intuitive bands, OOS
        print("\n  PRE-SPECIFIED bands, OOS 2026:", flush=True)
        for lo, hi, name in FIXED:
            r = oos(net, te & (aR >= lo) & (aR < hi), base_te, rng)
            if not r:
                print("    %-24s n<20" % name); continue
            m, n, clo, chi = r
            print("    %-24s net %+.4f%% (kept %3.0f%%)  lift %+.4f%%  95%%CI[%+.4f,%+.4f]  %s"
                  % (name, 100 * m, 100 * n / te.sum(), 100 * (m - base_te.mean()), 100 * clo, 100 * chi,
                     "** beats take-all" if clo > 0 else ("cross0" if clo < 0 < chi else "WORSE")), flush=True)


if __name__ == "__main__":
    main()
