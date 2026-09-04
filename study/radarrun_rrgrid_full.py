"""RADAR RUNNER — plain parent badge at RR exits, FULL 18mo (user 2026-09-04: "RR1:1.5 is almost
always positive on the screens, isn't it?").

The screens' ALL row at RR1:1.5 was positive on 4/4 independent 16-day draws (+0.198/+0.059/
+0.046/+0.058, avgR agreeing). That row is just EVERY parent union badge traded at its own
bracket (entry = badge close, SL = badge candle-anchored stop) with a single RR take-profit —
no child detection — so the full-data answer is cheap. PRE-REGISTERED: RR grid 1 / 1.25 / 1.5 /
1.75 / 2 around the noticed cell, both parents (30m bucket canonical union + 15m clock union),
full period, eras separate, 1m first-touch ties-against, canonical fees, non-overlap taken(),
prop MC. PREDICTION ON RECORD: the falsification battery killed expectancy levers on this set —
expect ~-0.03..-0.06%/trade; a 4/4-screen streak is ~1-in-16 under a coin flip and the draws
share the regime mix, so it proves little by itself.
python study/radarrun_rrgrid_full.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, CACHE_30, fires_15m_clock
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ

GRID = (("RR1:1", "rr", 1.0), ("RR1:1.25", "rr", 1.25), ("RR1:1.5", "rr", 1.5),
        ("RR1:1.75", "rr", 1.75), ("RR1:2", "rr", 2.0))


def main():
    from study.radarrun_hyro_prop import mc, day_blocks
    t0 = time.time()
    print("RR GRID on ALL parent badges — FULL 18mo, both parents | parent bracket, single RR TP\n",
          flush=True)
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]
    for tag, fires in (("30mBKT", json.load(open(CACHE_30))), ("15mCLK", fires_15m_clock())):
        trades = [dict(t=et, s=int(s), e=float(e), sl=float(sl))
                  for (b, et, s, e, sl) in fires if abs(e - sl) > 1e-9]
        print("%s: %d parent badges" % (tag, len(trades)), flush=True)
        for ename, kind, val in GRID:
            report_cell(tag, ename, trades, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
