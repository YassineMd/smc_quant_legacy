"""STANDALONE test (NOT Pivot V3). P0 mean-reversion entry, SEQUENTIAL (one trade at a time).

ENTRY (P0 = the composite SUM 'blue line'; golden dashed refs at +-50; crosses green=up / red=down):
  - SHORT: the LOCKED blue line was ABOVE +50 and prints a RED cross (down through +50)  -> reverting down.
  - LONG : the LOCKED blue line was BELOW -50 and prints a GREEN cross (up through -50)   -> reverting up.
  AND the LOCKED P2 (eff-agg) spread is in favour of the position (aligned > 0),
  AND the current LOCKED-cycle P2 HMS is in favour (aligned > 0)  [the CURRENT locked cycle only, not the 2-cycle box].
STOP: 0.3%.  EXIT: when the LOCKED P2 spread is NO LONGER in favour (aligned <= 0).  Fee 0.10.
Causal: everything reads the LOCKED (settled) value at bar j = data up to j-LOCK (LOCK=7). Run: python study/p0_reversion.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # _p9_global -> sum0 (P0), e_sh (P2)  # noqa: E402
import pivot_v3_de_zone_pdf as B                          # 1m loader  # noqa: E402

FEE = 0.10; BE = 0.05; SL_P = 0.003; MIN_CYC = 4
LOCK = PD.LOCK; SELW = PD.SELW


def build_cycles(sh, min_len):
    n = len(sh); cyc = []; i0 = 0; dom = sh[0] >= 0.5
    for k in range(1, n):
        dk = sh[k] >= 0.5
        if dk != dom:
            cyc.append([i0, k - 1, dom]); i0 = k; dom = dk
    cyc.append([i0, n - 1, dom])
    while len(cyc) > 1:
        si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
        if (cyc[si][1] - cyc[si][0] + 1) >= min_len:
            break
        cyc[si][2] = not cyc[si][2]
        merged = [cyc[0]]
        for c in cyc[1:]:
            if c[2] == merged[-1][2]:
                merged[-1][1] = c[1]
            else:
                merged.append(c)
        cyc = merged
    return cyc


def stats(nets):
    a = np.asarray(nets, float); nn = len(a)
    if nn == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if (nn > 1 and a.std(ddof=1) > 1e-9) else 0.0
    return dict(n=nn, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()), t=float(t))


def main():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    a_sh, e_sh, r_sh, sum0 = PD._p9_global(snaps)                                 # sum0 = P0 blue line, e_sh = P2 share
    cl = np.array([b.close_price for b in bks]); hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])

    cyc = build_cycles(e_sh, MIN_CYC); cyc_ends = [c[1] for c in cyc]             # settled P2 cycles (for the HMS)

    def hms_aligned(lb, bull):                                                    # current LOCKED cycle HMS, +position
        m = min(bisect.bisect_left(cyc_ends, lb), len(cyc) - 1)
        s0 = cyc[m][0]; s1 = min(lb, cyc[m][1])
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1.0 - e_sh[k]) for k in range(s0, s1 + 1)]
        dom = [v for v in dom if v > 1e-6]
        hm = (len(dom) / sum(1.0 / v for v in dom)) if dom else 0.5
        spread = (2.0 * hm - 1.0) * 100.0
        net_bull = float(np.mean(e_sh[s0:s1 + 1])) >= 0.5
        hb = spread if net_bull else -spread
        return hb if bull else -hb

    def p2_locked_aligned(lb, bull):
        spr = (2.0 * float(e_sh[lb]) - 1.0) * 100.0
        return spr if bull else -spr

    def walk(eb, bull):
        entry = float(cl[eb]); sl = entry * (1 - SL_P) if bull else entry * (1 + SL_P)
        for j in range(eb + 1, n):
            if (lo[j] <= sl) if bull else (hi[j] >= sl):
                return ((sl - entry) if bull else (entry - sl)) / entry * 100.0, "SL", j
            if p2_locked_aligned(j - LOCK, bull) <= 0.0:                          # P2 locked spread turned against
                return ((cl[j] - entry) if bull else (entry - cl[j])) / entry * 100.0, "p2-against", j
        return ((cl[-1] - entry) if bull else (entry - cl[-1])) / entry * 100.0, "edge", n - 1

    nets = []; why = {}; holds = []; longs = []; shorts = []; n_sig = 0
    j = max(SELW, LOCK + 2)
    while j < n - 1:
        lb = j - LOCK
        short_sig = sum0[lb - 1] > 50.0 and sum0[lb] <= 50.0                      # RED cross down through +50
        long_sig = sum0[lb - 1] < -50.0 and sum0[lb] >= -50.0                     # GREEN cross up through -50
        bull = True if long_sig else (False if short_sig else None)
        if bull is not None:
            n_sig += 1
            if p2_locked_aligned(lb, bull) > 0.0 and hms_aligned(lb, bull) > 0.0:  # P2 locked + current-cycle HMS in favour
                g, r, xb = walk(j, bull)
                net = g - FEE
                nets.append(net); why[r] = why.get(r, 0) + 1; holds.append(xb - j)
                (longs if bull else shorts).append(net)
                j = xb + 1; continue                                             # SEQUENTIAL
        j += 1

    o = stats(nets)
    print("P0 reversion (red-cross@+50 -> short / green-cross@-50 -> long) + P2 LOCKED spread favour + current-cycle")
    print("HMS favour. SL 0.3%%; exit when LOCKED P2 spread turns against. SEQUENTIAL. tape n=%d bars.\n" % n)
    print("  raw P0 cross signals: %d  ->  passed P2+HMS filter & taken: %d" % (n_sig, o["n"]))
    if o["n"] == 0:
        print("  no trades."); return
    print("  TRADES n=%d | W %d / BE %d / L %d  (%.1f%% win) | net %+.4f%%/tr | sum %+.1f%% | t=%+.2f | avg hold %.0f bars"
          % (o["n"], o["w"], o["b"], o["l"], 100 * o["w"] / o["n"], o["mean"], o["tot"], o["t"], np.mean(holds)))
    print("  exit mix: " + "  ".join("%s=%.0f%%" % (k, 100 * v / o["n"]) for k, v in sorted(why.items(), key=lambda kv: -kv[1])))
    ol = stats(longs); os_ = stats(shorts)
    print("  long : n=%d net %+.4f%%/tr t%+.2f | short: n=%d net %+.4f%%/tr t%+.2f"
          % (ol["n"], ol["mean"], ol["t"], os_["n"], os_["mean"], os_["t"]))


if __name__ == "__main__":
    main()
