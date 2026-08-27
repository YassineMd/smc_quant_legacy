"""EMA-HL DELTA as a WINNER/LOSER differentiator — 30m bucket Radar Runner, DESCRIPTIVE study (user
2026-08-27: does the delta separate winners from losers?). Canonical harness trades: cached union fire
sets, NATIVE radar SL, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg; exits
0.2% net and RR 1:1; recon and DAEMON reported separately.

PRE-REGISTERED (frozen; descriptive — no tradeability verdict): DELTA per fire exactly as
radarrun_hldelta.py (20-bar window incl. the fire bar; window high/low each measured vertically to EMA20
AT its own bar; signed net). ALIGNED delta = side * delta (positive = the skew agrees with the trade's
direction). Per era x exit: (a) winners-vs-losers stats box (mean/median aligned + |delta|, share
aligned>0); (b) DISJOINT quintile bands of aligned delta (edges from that era+exit's own taken trades):
n / win% / avg net per band.
python study/radarrun_hld_winloss_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hldelta import ema20, P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("RR 1:1", "rr", 1.0)]


def deltas(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E = ema20(C)
    out = {}
    for f in fires:
        b = int(f[0])
        hi_p = hi_i = lo_p = lo_i = None
        for i in range(max(0, b - P + 1), b + 1):
            if H[i] > 0 and (hi_p is None or H[i] >= hi_p):
                hi_p, hi_i = H[i], i
            if L[i] > 0 and (lo_p is None or L[i] <= lo_p):
                lo_p, lo_i = L[i], i
        if hi_p is None or lo_p is None or E[hi_i] <= 0 or E[lo_i] <= 0:
            continue
        out[b] = ((hi_p - E[hi_i]) / E[hi_i] + (lo_p - E[lo_i]) / E[lo_i]) * 100.0   # signed, in %
    return out


def trades(fires, kind, val, T1, H1, L1):
    """Per-trade walk identical to the canonical eval_1m (non-overlap, resolve_1m): [(b, s, net)]."""
    from study.radarrun_bkt1h_deltapct_confirm import resolve_1m
    taken = []; busy = -1.0
    for (b, t, s, e, sl) in fires:
        if t < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        g = val if kind == "fix" else val * sld
        net, tx = resolve_1m(s, e, sl, g, t, T1, H1, L1)
        if net is None:
            continue
        taken.append((int(b), int(s), net)); busy = tx
    return taken


def report(fires, A, T1, H1, L1):
    dmap = deltas(fires, A)
    for cname, kind, val in TPS:
        tk = [(b, s, net) for (b, s, net) in trades(fires, kind, val, T1, H1, L1) if b in dmap]
        al = np.array([s * dmap[b] for (b, s, net) in tk])       # aligned delta (%; + = agrees with the side)
        ab = np.array([abs(dmap[b]) for (b, s, net) in tk])
        nets = np.array([net for (_, _, net) in tk])
        w = nets > 0; l = nets < 0
        print("  -- %s (n=%d, win %.1f%%) --" % (cname, len(tk), 100 * w.mean()), flush=True)
        for tag, m in (("WINNERS", w), ("LOSERS ", l)):
            print("     %s n=%-5d aligned mean %+.3f%%  med %+.3f%%  share>0 %5.1f%%  |  |delta| mean %.3f%%  med %.3f%%"
                  % (tag, m.sum(), al[m].mean(), np.median(al[m]), 100 * (al[m] > 0).mean(),
                     ab[m].mean(), np.median(ab[m])), flush=True)
        q = np.quantile(al, [0.2, 0.4, 0.6, 0.8])
        edges = [-np.inf] + list(q) + [np.inf]
        print("     aligned-delta QUINTILES (disjoint):", flush=True)
        for j in range(5):
            m = (al >= edges[j]) & (al < edges[j + 1])
            if not m.any():
                continue
            print("       Q%d [%+.2f .. %+.2f%%]: n=%-5d win %5.1f%%  avg %+.3f%%"
                  % (j + 1, al[m].min(), al[m].max(), m.sum(), 100 * (nets[m] > 0).mean(), 100 * nets[m].mean()), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("EMA-HL DELTA vs WINNER/LOSER — 30m bucket Radar Runner | native SL | descriptive | pre-registered\n", flush=True)
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
