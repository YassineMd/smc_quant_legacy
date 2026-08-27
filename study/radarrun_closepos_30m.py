"""CLOSE POSITION IN THE 20-BAR HL WINDOW as a WINNER/LOSER differentiator — 30m bucket Radar Runner,
DESCRIPTIVE study (user 2026-08-27 hypothesis: a LONG whose signal-bar close sits CLOSER TO THE WINDOW LOW
wins more; a SHORT whose close sits closer to the WINDOW HIGH wins more). Canonical harness trades: cached
union fire sets, NATIVE radar SL, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03%
slip/leg; exits 0.2% net and RR 1:1; recon and DAEMON reported separately.

PRE-REGISTERED (frozen; descriptive — no tradeability verdict): window = the ema_ext spec's last 20 CLOSED
bars INCLUDING the fire bar; hi/lo = unconditional window max high / min low (ties -> most recent).
pos = (close - lo) / (hi - lo) in [0,1] (0 = at the window low, 1 = at the window high).
FAV = 1 - pos for LONG / pos for SHORT (1 = the hypothesis's favorable edge). Per era x exit:
(a) winners-vs-losers stats box on FAV; (b) DISJOINT FAV quintiles (era+exit edges): n / win% / avg net;
(c) per-side FAV terciles (symmetry check).
python study/radarrun_closepos_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hldelta import ema20, P
from study.radarrun_hld_winloss_30m import trades

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("RR 1:1", "rr", 1.0)]


def positions(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    out = {}
    for f in fires:
        b = int(f[0])
        hi_p = lo_p = None
        for i in range(max(0, b - P + 1), b + 1):
            if H[i] > 0 and (hi_p is None or H[i] >= hi_p):
                hi_p = H[i]
            if L[i] > 0 and (lo_p is None or L[i] <= lo_p):
                lo_p = L[i]
        if hi_p is None or lo_p is None or hi_p <= lo_p or C[b] <= 0:
            continue
        out[b] = min(1.0, max(0.0, (C[b] - lo_p) / (hi_p - lo_p)))
    return out


def report(fires, A, T1, H1, L1):
    pmap = positions(fires, A)
    for cname, kind, val in TPS:
        tk = [(b, s, net) for (b, s, net) in trades(fires, kind, val, T1, H1, L1) if b in pmap]
        fav = np.array([(1.0 - pmap[b]) if s > 0 else pmap[b] for (b, s, net) in tk])
        nets = np.array([net for (_, _, net) in tk])
        sides = np.array([s for (_, s, _) in tk])
        w = nets > 0; l = nets < 0
        print("  -- %s (n=%d, win %.1f%%) --" % (cname, len(tk), 100 * w.mean()), flush=True)
        for tag, m in (("WINNERS", w), ("LOSERS ", l)):
            print("     %s n=%-5d fav mean %.3f  med %.3f  share fav>0.5 %5.1f%%"
                  % (tag, m.sum(), fav[m].mean(), np.median(fav[m]), 100 * (fav[m] > 0.5).mean()), flush=True)
        q = np.quantile(fav, [0.2, 0.4, 0.6, 0.8])
        edges = [-0.001] + list(q) + [1.001]
        print("     FAV QUINTILES (disjoint; 1 = close at the hypothesised good edge):", flush=True)
        for j in range(5):
            m = (fav >= edges[j]) & (fav < edges[j + 1])
            if not m.any():
                continue
            print("       Q%d [%.3f .. %.3f]: n=%-5d win %5.1f%%  avg %+.3f%%"
                  % (j + 1, fav[m].min(), fav[m].max(), m.sum(), 100 * (nets[m] > 0).mean(), 100 * nets[m].mean()), flush=True)
        for sname, sm in (("LONG ", sides > 0), ("SHORT", sides < 0)):
            if sm.sum() < 12:
                continue
            fq = np.quantile(fav[sm], [1 / 3, 2 / 3])
            se = [-0.001] + list(fq) + [1.001]
            row = []
            for j in range(3):
                m = sm & (fav >= se[j]) & (fav < se[j + 1])
                row.append("T%d n=%d win %.1f%% avg %+.3f%%" % (j + 1, m.sum(), 100 * (nets[m] > 0).mean(), 100 * nets[m].mean()))
            print("     %s terciles: %s" % (sname, " | ".join(row)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("CLOSE POSITION in the 20-bar HL window vs WINNER/LOSER — 30m Radar Runner | native SL | descriptive | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 110, flush=True)
    print("RECON 2025-01 .. 2026-06", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(load_fires("bucket", "30m"), A, T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 110, flush=True)
    print("DAEMON (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json"))), Ad, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
