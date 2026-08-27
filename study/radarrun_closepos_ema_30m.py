"""CLOSE-POSITION x EMA-SIDE cross-tab — 30m Radar Runner, DESCRIPTIVE follow-up (user 2026-08-27: does
'signal bar close above/below the EMA20' add anything ON TOP of the close-position (fav) read?). Same
canonical trades as radarrun_closepos_30m (native SL, 1m first-touch, non-overlap, fees+slip; exits 0.2%
net and RR 1:1). fav bands FIXED from the previous study's shape (stated, not fit here): LOW <0.45 /
MID 0.45-0.80 (the sweet band) / EDGE >0.80. EMA side per fire: WITH = long close>EMA20 / short close<EMA20;
AGAINST = opposite. Cells: fav band x EMA side -> n / win% / avg net. Recon shown for reference; DAEMON
decisive. Descriptive only.
python study/radarrun_closepos_ema_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hldelta import ema20
from study.radarrun_hld_winloss_30m import trades
from study.radarrun_closepos_30m import positions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("RR 1:1", "rr", 1.0)]
BANDS = [("LOW <0.45", 0.0, 0.45), ("MID 0.45-0.80", 0.45, 0.80), ("EDGE >0.80", 0.80, 1.01)]


def report(fires, A, T1, H1, L1):
    from study.candle_bias_1h import _f
    pmap = positions(fires, A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    E = ema20(C)
    for cname, kind, val in TPS:
        tk = [(b, s, net) for (b, s, net) in trades(fires, kind, val, T1, H1, L1) if b in pmap and C[int(b)] > 0]
        print("  -- %s (n=%d) --" % (cname, len(tk)), flush=True)
        for bname, lo, hi in BANDS:
            for ename in ("WITH-EMA", "AGAINST "):
                sel = []
                for (b, s, net) in tk:
                    fav = (1.0 - pmap[b]) if s > 0 else pmap[b]
                    if not (lo <= fav < hi):
                        continue
                    withe = (s > 0 and C[b] > E[b]) or (s < 0 and C[b] < E[b])
                    if (ename == "WITH-EMA") == withe:
                        sel.append(net)
                if not sel:
                    print("     %-14s %s  n=0" % (bname, ename), flush=True)
                    continue
                a = np.array(sel)
                print("     %-14s %s  n=%-4d win %5.1f%%  avg %+.3f%%"
                      % (bname, ename, len(a), 100 * (a > 0).mean(), 100 * a.mean()), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("CLOSE-POSITION x EMA-SIDE cross-tab — 30m Radar Runner | descriptive follow-up\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 100, flush=True)
    print("RECON 2025-01 .. 2026-06 (reference)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(load_fires("bucket", "30m"), A, T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 100, flush=True)
    print("DAEMON (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json"))), Ad, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
