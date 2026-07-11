"""RANDOM-ENTRY test (NOT Pivot V3). Isolates whether "ride the HM cycle" has any edge on RANDOM entries.

Rule: enter at a bar in the direction of the currently-LOCKED HM cycle (bullish cycle -> long, bearish -> short);
fixed stop 0.2%; TAKE PROFIT = exit the moment the LOCKED HM cycle FLIPS to the opposite side (e.g. entered in a
bull cycle -> exit when a bear cycle locks). No fixed TP. Fee 0.10 taker/taker.

"Random entry" = the expected outcome over ALL eligible bars (the full population = a uniformly-random entry).
WITH-cycle vs AGAINST-cycle (fade) reported side by side; if WITH >> AGAINST the cycle-ride carries an edge.
Run: python study/random_cycle_ride.py
"""
import os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import region_state as R, config                # noqa: E402
from app.structure import ZIGZAG_PCT                      # noqa: E402
import pivot_v3_de_zone_pdf as B                          # reuse the 1m loader + zz  # noqa: E402

FEE = 0.10; SL_P = 0.002; BE = 0.05
LW = config.LIVE_PANEL_WINDOW; LOCK_LAG = LW // 2; MIN_CYC = 4


def build_cycles(sh, min_len):
    """De-noised HM cycles (same rule as the panel): runs of `sh` on one side of 0.5, short runs absorbed."""
    n = len(sh)
    cyc = []; i0 = 0; dom = sh[0] >= 0.5
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
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    ebu, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(ebu, er_, LW), float)                       # settled (centered) share
    cl = np.array([b.close_price for b in bks]); hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])

    cyc = build_cycles(e_sh, MIN_CYC)                                             # settled cycle structure
    cyc_dom = np.zeros(n, bool)
    for a, b, dom in cyc:
        cyc_dom[a:b + 1] = dom
    # the LOCKED side visible at bar j = the settled cycle side that has cleared the centered-window lock edge
    locked_side = np.array([cyc_dom[max(0, j - LOCK_LAG)] for j in range(n)])

    # MICROSTRUCTURE SWING (ZigZag): per bar, is the most recent CONFIRMED swing bullish (HH/HL) or bearish (LL/LH)?
    sw = B.zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    labeled = []; ph = pl = None
    for pb, p, ih, cb in sw:
        if ih:
            lab = "HH" if (ph is not None and p > ph) else ("LH" if ph is not None else None); ph = p
        else:
            lab = "HL" if (pl is not None and p > pl) else ("LL" if pl is not None else None); pl = p
        if lab:
            labeled.append((cb, lab))
    labeled.sort()
    last_sw = np.full(n, -1, int)                                                # -1 none / 1 bull(HH,HL) / 0 bear(LL,LH)
    cur = -1; si = 0
    for j in range(n):
        while si < len(labeled) and labeled[si][0] <= j:
            cur = 1 if labeled[si][1] in ("HH", "HL") else 0; si += 1
        last_sw[j] = cur
    # next bar (>= j) where the locked side differs from locked_side[j] -> the cycle-flip exit, precomputed
    next_flip = np.full(n, n, int)
    nf = n
    for j in range(n - 2, -1, -1):
        if locked_side[j + 1] != locked_side[j]:
            nf = j + 1
        next_flip[j] = nf

    def walk(eb, bull):
        entry = float(cl[eb]); sl = entry * (1 - SL_P) if bull else entry * (1 + SL_P)
        flip = next_flip[eb]                                                      # first opposite-locked-cycle bar
        for j in range(eb + 1, min(flip, n)):
            if (lo[j] <= sl) if bull else (hi[j] >= sl):
                return ((sl - entry) if bull else (entry - sl)) / entry * 100.0, "SL", j - eb
        if flip < n:
            return ((cl[flip] - entry) if bull else (entry - cl[flip])) / entry * 100.0, "flip", flip - eb
        return ((cl[-1] - entry) if bull else (entry - cl[-1])) / entry * 100.0, "edge", n - 1 - eb

    def run(fade):                                                               # ALL bars (overlapping = uniformly-random)
        nets = []; why = {}; holds = []; longs = []; shorts = []
        for eb in range(LOCK_LAG + 1, n - 1):
            bull = bool(locked_side[eb]) ^ fade
            g, r, hold = walk(eb, bull)
            net = g - FEE
            nets.append(net); why[r] = why.get(r, 0) + 1; holds.append(hold)
            (longs if bull else shorts).append(net)
        return np.array(nets), why, np.array(holds), longs, shorts

    def run_seq(fade, struct=False):                                            # SEQUENTIAL: one trade at a time
        nets = []; why = {}; holds = []; longs = []; shorts = []
        j = LOCK_LAG + 1
        while j < n - 1:
            bull = bool(locked_side[j]) ^ fade
            if struct and (last_sw[j] == -1 or last_sw[j] != (1 if bull else 0)):   # swing must agree: long->HH/HL, short->LL/LH
                j += 1; continue
            g, r, hold = walk(j, bull)
            net = g - FEE
            nets.append(net); why[r] = why.get(r, 0) + 1; holds.append(hold)
            (longs if bull else shorts).append(net)
            j = j + hold + 1                                                     # re-enter only after this trade closes
        return np.array(nets), why, np.array(holds), longs, shorts

    print("RANDOM-ENTRY / cycle-ride exit. Enter in the LOCKED HM-cycle direction; SL 0.2%%; exit when the locked")
    print("cycle FLIPS. Fee 0.10. tape n=%d bars. SEQUENTIAL = one trade at a time (re-enter after the prior closes).\n" % n)

    def report(tag, runner, fade):
        nets, why, holds, longs, shorts = runner(fade)
        o = stats(nets)
        print("%s (n=%d)" % (tag, o["n"]))
        print("  W %d / BE %d / L %d  (%.1f%% win) | net %+.4f%%/tr | sum %+.1f%% | t=%+.1f | avg hold %.0f bars"
              % (o["w"], o["b"], o["l"], 100 * o["w"] / o["n"], o["mean"], o["tot"], o["t"], holds.mean()))
        print("  exit: " + "  ".join("%s=%.0f%%" % (k, 100 * v / o["n"]) for k, v in sorted(why.items(), key=lambda kv: -kv[1])))
        ol = stats(longs); os_ = stats(shorts)
        print("  long : net %+.4f%%/tr (n=%d, t%+.1f) | short: net %+.4f%%/tr (n=%d, t%+.1f)"
              % (ol["mean"], ol["n"], ol["t"], os_["mean"], os_["n"], os_["t"]))

    print("--- SEQUENTIAL + SWING FILTER (long only if last swing HH/HL; short only if LL/LH) ---")
    report("WITH-cycle + swing-filter", lambda f: run_seq(f, struct=True), fade=False)
    print("")
    report("AGAINST + swing-filter", lambda f: run_seq(f, struct=True), fade=True)
    print("\n--- SEQUENTIAL, NO swing filter (reference) ---")
    report("WITH-cycle", lambda f: run_seq(f, struct=False), fade=False)


if __name__ == "__main__":
    main()
