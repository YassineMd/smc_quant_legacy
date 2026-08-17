"""Full equity MONTE-CARLO of the 30m Radar Runner. Bootstrap-resamples the ACTUAL net, one-position-at-a-time
R-multiple sequence (fees+slip already inside each R) into N forward account paths at fixed-fractional R% risk/trade.
Per (dataset x TP) reports the DISTRIBUTION of: final return over a fixed campaign, max drawdown, P(profit), and a
prop first-passage (reach +10% before a 10% trailing DD, cap 1500 trades) -> pass% + trades/days-to-pass. Also dumps
JSON (equity fan percentile bands + return/DD histograms) for the headline DAEMON 0.2% cell. python study/radarrun_30m_montecarlo.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_30m_honest_mc import all_signals   # (A, tp) -> [(k, exit_bar, net_R), ...] chronological

RP = 0.5           # risk per trade, % of account (fixed-fractional). DD/return scale ~linearly in RP.
H_FAN = 300        # campaign length for the return/DD distribution (~56 days at ~5.4 trades/day)
CAP = 1500         # max trades before a prop path is called unresolved
TARGET, DDLIM = 10.0, 10.0
N = 20000
TRADES_PER_DAY = {"RECON": None, "DAEMON": None}   # filled from the pool build (span-based)


def pool_one_at_a_time(A, tp):
    """The honest, non-overlapping R sequence: enter -> let it finish -> then next."""
    sigs = all_signals(A, tp); seq = []; busy = -1
    for (k, ex, R) in sigs:
        if k > busy:
            seq.append(R); busy = ex
    return np.array(seq, dtype=float)


def simulate(pool, rng):
    """N bootstrap paths. Returns fan (N x H_FAN+1), finals, maxdds (over H_FAN), and prop first-passage arrays."""
    fan = np.empty((N, H_FAN + 1)); finals = np.empty(N); maxdds = np.empty(N)
    passed = np.zeros(N, dtype=bool); ttp = np.full(N, -1)      # trades-to-pass (-1 = not passed)
    for i in range(N):
        samp = pool[rng.integers(0, len(pool), CAP)]
        eq = np.concatenate([[0.0], np.cumsum(RP * samp)])      # equity path in % (len CAP+1)
        # ---- fixed campaign stats (first H_FAN trades) ----
        seg = eq[:H_FAN + 1]; fan[i] = seg
        peak = np.maximum.accumulate(seg); finals[i] = seg[-1]; maxdds[i] = np.max(peak - seg)
        # ---- prop first-passage over the full CAP ----
        pk = np.maximum.accumulate(eq); dd = pk - eq
        i_pass = np.argmax(eq >= TARGET) if (eq >= TARGET).any() else -1
        i_fail = np.argmax(dd >= DDLIM) if (dd >= DDLIM).any() else -1
        if i_pass != -1 and (i_fail == -1 or i_pass <= i_fail):
            passed[i] = True; ttp[i] = i_pass
    return fan, finals, maxdds, passed, ttp


def main():
    rng = np.random.default_rng(7)
    dump = None
    print("==== 30m Radar Runner  EQUITY MONTE-CARLO  (N=%d paths, R=%.2f%%/trade, campaign=%d trades) ====" % (N, RP, H_FAN), flush=True)
    print("  each path = bootstrap of the real net one-at-a-time R sequence (fees+slip inside R)\n", flush=True)
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        A = get_buckets("30m", root)
        for tp in (0.002, 0.003):
            pool = pool_one_at_a_time(A, tp)
            evR = pool.mean(); win = 100.0 * (pool > 0).mean()
            fan, finals, maxdds, passed, ttp = simulate(pool, rng)
            fq = np.percentile(finals, [5, 25, 50, 75, 95])
            dq = np.percentile(maxdds, [50, 75, 95, 99])
            pprob = 100.0 * (finals > 0).mean()
            passpct = 100.0 * passed.mean()
            tq = np.percentile(ttp[passed], [25, 50, 75]) if passed.any() else [0, 0, 0]
            print("---- %s  TP %.2f%%  (pool n=%d, win=%.0f%%, EV=%+.3fR/trade) ----" % (ds, tp * 100, len(pool), win, evR), flush=True)
            print("   campaign +%d trades:  final%%  p5/p25/med/p75/p95 = %+.1f / %+.1f / %+.1f / %+.1f / %+.1f   P(profit)=%.0f%%"
                  % (H_FAN, fq[0], fq[1], fq[2], fq[3], fq[4], pprob), flush=True)
            print("                          maxDD%%  med/p75/p95/p99 = %.1f / %.1f / %.1f / %.1f" % (dq[0], dq[1], dq[2], dq[3]), flush=True)
            print("   prop (reach +10%% before 10%% trailing-DD):  pass=%.1f%%   trades-to-pass p25/med/p75 = %d/%d/%d  (~%d/%d/%d days @5.4/day)\n"
                  % (passpct, tq[0], tq[1], tq[2], tq[0] / 5.4, tq[1] / 5.4, tq[2] / 5.4), flush=True)
            if ds == "DAEMON" and tp == 0.002:
                band = np.percentile(fan, [5, 25, 50, 75, 95], axis=0)   # 5 x (H_FAN+1)
                idx = np.linspace(0, len(fan) - 1, 80).astype(int)       # 80 thinned spaghetti curves
                fh, fe = np.histogram(finals, bins=40)
                dh, de = np.histogram(maxdds, bins=40)
                dump = {
                    "meta": {"ds": ds, "tp": tp, "N": int(N), "RP": RP, "H": int(H_FAN),
                             "win": win, "ev": evR, "pass": passpct, "n": int(len(pool)),
                             "final_med": float(fq[2]), "final_p5": float(fq[0]), "final_p95": float(fq[4]),
                             "dd_med": float(dq[0]), "dd_p95": float(dq[2]), "pprob": pprob,
                             "ttp_med": float(tq[1])},
                    "steps": list(range(H_FAN + 1)),
                    "bands": {k: band[j].round(3).tolist() for j, k in enumerate(["p5", "p25", "p50", "p75", "p95"])},
                    "spaghetti": fan[idx].round(3).tolist(),
                    "final_hist": {"counts": fh.tolist(), "edges": fe.round(2).tolist()},
                    "dd_hist": {"counts": dh.tolist(), "edges": de.round(2).tolist()},
                }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "radarrun_30m_mc.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f)
    print("wrote %s" % out, flush=True)


if __name__ == "__main__":
    main()
