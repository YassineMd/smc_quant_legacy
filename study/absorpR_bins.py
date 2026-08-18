"""What absorpR VALUE differentiates RR winners from losers? Bin by actual value (not quantile) -> n/win%/net-return
per band, per year. Shows whether it's a bright-line threshold or a smooth gradient. Usage: python study/absorpR_bins.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_15m_sldist_test import build

EDGES = [-np.inf, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.5, np.inf]


def main():
    for tf in ("5m", "15m", "30m"):
        sld, aR, net, win, yr = build(tf)
        fin = np.isfinite(aR)
        aR, net, win, yr = aR[fin], net[fin], win[fin], yr[fin]
        pct = np.percentile(aR, [5, 25, 50, 75, 95])
        print("\n==== %s  (n=%d)  absorpR distribution p5/25/50/75/95 = %s ====" % (
            tf, len(aR), "  ".join("%+.2f" % p for p in pct)), flush=True)
        print("  absorpR band        |    2025 (n / win%% / net%%)   |    2026 (n / win%% / net%%)", flush=True)
        print("  " + "-" * 78, flush=True)
        for i in range(len(EDGES) - 1):
            lo, hi = EDGES[i], EDGES[i + 1]
            lbl = ("%+.2f..%+.2f" % (lo, hi)) if np.isfinite(lo) and np.isfinite(hi) else \
                  ("< %+.2f" % hi if not np.isfinite(lo) else ">= %+.2f" % lo)
            cells = []
            for Y in (2025, 2026):
                m = (aR >= lo) & (aR < hi) & (yr == Y)
                if m.sum() < 15:
                    cells.append("  n<15               ")
                else:
                    cells.append("n%-4d  %4.1f%%  %+.4f%%" % (m.sum(), 100 * win[m].mean(), 100 * net[m].mean()))
            print("  %-19s | %s | %s" % (lbl, cells[0], cells[1]), flush=True)


if __name__ == "__main__":
    main()
