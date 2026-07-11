"""STANDALONE test (NOT Pivot V3). Pure signal entries, SEQUENTIAL (one trade at a time).

ENTRY (scan every bar, only when flat): direction is whichever side BOTH agree on --
  - Panel-2 eff-agg LIVE spread (first-print) >= 50 in favour, AND
  - HMS (harmonic-mean spread over the last 2 LOCKED cycles) >= 20 in favour.
STOP: at the last confirmed swing LOW (LL/HL) for longs / swing HIGH (HH/LH) for shorts.
TAKE PROFIT / exit: the LOCKED HM cycle has flipped to the opposite side AND the eff-agg live spread has turned
  against the trade (aligned <= 0). e.g. long entered in a bull cycle -> exit at a LOCKED bear cycle with eff-agg red.
Fee 0.10 taker/taker. Run: python study/eff_hms_ride.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import region_state as R, config                # noqa: E402
from app.structure import ZIGZAG_PCT                      # noqa: E402
import pivot_v3_de_zone_pdf as B                          # 1m loader + zz  # noqa: E402

FEE = 0.10; BE = 0.05
LW = config.LIVE_PANEL_WINDOW; LOCK_LAG = LW // 2; MIN_CYC = 4; WBACK = 100
EFF_MIN = 50.0; HMS_MIN = 20.0                            # entry thresholds (in favour)


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
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    ebu, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(ebu, er_, LW), float)                       # settled (centered) share
    e_sh_c = B.causal_share(ebu, er_, LW)                                         # first-print (live) share
    cl = np.array([b.close_price for b in bks]); hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    eff_bull = (2.0 * e_sh_c - 1.0) * 100.0                                       # live eff-agg spread, +bull / -bear

    cyc = build_cycles(e_sh, MIN_CYC)                                             # settled cycle structure
    cyc_dom = np.zeros(n, bool); cyc_ends = [c[1] for c in cyc]
    for a, b, dom in cyc:
        cyc_dom[a:b + 1] = dom
    locked_side = np.array([cyc_dom[max(0, j - LOCK_LAG)] for j in range(n)])     # LOCKED side visible at bar j

    def hms_bull(bar):                                                            # HMS over last 2 LOCKED cycles, +bull
        m = bisect.bisect_left(cyc_ends, bar - LOCK_LAG)
        if m < 1:
            return 0.0
        win = cyc[max(0, m - 2):m]; s0 = win[0][0]; s1 = win[-1][1]
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1.0 - e_sh[k]) for k in range(s0, s1 + 1)]
        dom = [v for v in dom if v > 1e-6]
        hm = (len(dom) / sum(1.0 / v for v in dom)) if dom else 0.5
        spread = (2.0 * hm - 1.0) * 100.0
        net_bull = (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5)
        return spread if net_bull else -spread

    # last CONFIRMED swing low / high price per bar (ZigZag; cb = confirm bar)
    sw = B.zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    lows = []; highs = []; ph = pl = None
    for pb, p, ih, cb in sw:
        if ih:
            lab = None if ph is None else ("HH" if p > ph else "LH"); ph = p
            if lab:
                highs.append((cb, p))
        else:
            lab = None if pl is None else ("HL" if p > pl else "LL"); pl = p
            if lab:
                lows.append((cb, p))
    lastlow = np.full(n, np.nan); lasthigh = np.full(n, np.nan)
    lows.sort(); highs.sort(); li = hj = 0; cl_ = ch_ = np.nan
    for j in range(n):
        while li < len(lows) and lows[li][0] <= j:
            cl_ = lows[li][1]; li += 1
        while hj < len(highs) and highs[hj][0] <= j:
            ch_ = highs[hj][1]; hj += 1
        lastlow[j] = cl_; lasthigh[j] = ch_

    def walk(eb, bull):
        entry = float(cl[eb])
        sl = lastlow[eb] if bull else lasthigh[eb]
        if not np.isfinite(sl) or (sl >= entry if bull else sl <= entry):        # no/invalid swing -> 0.3% fallback
            sl = entry * (1 - 0.003) if bull else entry * (1 + 0.003)
        for j in range(eb + 1, n):
            if (lo[j] <= sl) if bull else (hi[j] >= sl):
                return ((sl - entry) if bull else (entry - sl)) / entry * 100.0, "SL", j
            eff_al = eff_bull[j] if bull else -eff_bull[j]
            cyc_opp = (not locked_side[j]) if bull else bool(locked_side[j])
            if cyc_opp and eff_al <= 0.0:                                        # locked cycle flipped AND eff-agg red
                return ((cl[j] - entry) if bull else (entry - cl[j])) / entry * 100.0, "signal", j
        return ((cl[-1] - entry) if bull else (entry - cl[-1])) / entry * 100.0, "edge", n - 1

    nets = []; why = {}; holds = []; longs = []; shorts = []
    j = LOCK_LAG + 1
    while j < n - 1:
        align = True if eff_bull[j] >= EFF_MIN else (False if eff_bull[j] <= -EFF_MIN else None)
        if align is not None:
            hb = hms_bull(j)
            if (hb >= HMS_MIN) if align else (hb <= -HMS_MIN):
                g, r, xb = walk(j, align)
                net = g - FEE
                nets.append(net); why[r] = why.get(r, 0) + 1; holds.append(xb - j)
                (longs if align else shorts).append(net)
                j = xb + 1                                                        # SEQUENTIAL: resume after the exit
                continue
        j += 1

    o = stats(nets)
    print("EFF-AGG(>=%.0f) + HMS(>=%.0f) entry, SEQUENTIAL; SL last swing; exit = LOCKED cycle flip AND eff-agg red. "
          "tape n=%d bars.\n" % (EFF_MIN, HMS_MIN, n))
    if o["n"] == 0:
        print("  no trades fired."); return
    print("  TRADES n=%d | W %d / BE %d / L %d  (%.1f%% win) | net %+.4f%%/tr | sum %+.1f%% | t=%+.2f | avg hold %.0f bars"
          % (o["n"], o["w"], o["b"], o["l"], 100 * o["w"] / o["n"], o["mean"], o["tot"], o["t"], np.mean(holds)))
    print("  exit mix: " + "  ".join("%s=%.0f%%" % (k, 100 * v / o["n"]) for k, v in sorted(why.items(), key=lambda kv: -kv[1])))
    ol = stats(longs); os_ = stats(shorts)
    print("  long : n=%d net %+.4f%%/tr t%+.2f | short: n=%d net %+.4f%%/tr t%+.2f"
          % (ol["n"], ol["mean"], ol["t"], os_["n"], os_["mean"], os_["t"]))


if __name__ == "__main__":
    main()
