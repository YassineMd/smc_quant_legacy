"""STANDALONE — the PEAK/TROUGH FADE detector, causal + sequential backtest. NOT Pivot V3.

Setup (from swing_reversal.py's signature):
  SHORT a peak : P0 (locked sum0) > +50  [exhaustion]  AND  P2 (locked eff-agg) still bullish [peak, not reversed]
                 AND a TRIGGER = absorption candle (buy-led, closed DOWN)  OR  scalp ZigZag prints a Lower-High.
  LONG a trough: mirror (P0 < -50, P2 still bearish, absorption sell-led-closed-up OR scalp Higher-Low).
  STOP  = just beyond the recent local extreme (max high / min low over the last K bars).
  TARGET= fixed R:R multiple of that risk.  Fee 0.10 taker/taker.  SEQUENTIAL (one trade at a time).
Everything reads locked (data up to j-LOCK) / confirmed values -> causal. Run: python study/peak_detector.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, config               # noqa: E402
import pivot_v3_de_zone_pdf as B                          # 1m loader + zz  # noqa: E402

LOCK = PD.LOCK; FEE = 0.10; BE = 0.05
SCALP_PCT = 0.0015; K = 10; STOP_PAD = 0.001; P0_LVL = 50.0; SL_FIX = 0.002   # fixed 0.2% stop


def zz(H, L, thr):
    n = len(H); piv = []; d = 0; hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        h = H[i]; l = L[i]
        if d >= 0:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi * (1 - thr):
                piv.append((hi_i, hi, True, i)); d = -1; lo, lo_i = l, i; continue
        if d <= 0:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo * (1 + thr):
                piv.append((lo_i, lo, False, i)); d = 1; hi, hi_i = h, i
    return piv


def stats(nets):
    a = np.asarray(nets, float); nn = len(a)
    if nn == 0:
        return dict(n=0, w=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if (nn > 1 and a.std(ddof=1) > 1e-9) else 0.0
    return dict(n=nn, w=w, l=l, mean=float(a.mean()), tot=float(a.sum()), t=float(t))


def main():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    op = np.array([float(d.get("open", d.get("open_price", 0.0))) for d in raws])
    cl = np.array([float(d.get("close", d.get("close_price", 0.0))) for d in raws])
    hi = np.array([float(d.get("high", 0.0)) for d in raws]); lo = np.array([float(d.get("low", 0.0)) for d in raws])
    bvv = np.array([float(d.get("buy_vol", 0.0)) for d in raws]); svv = np.array([float(d.get("sell_vol", 0.0)) for d in raws])
    et = np.array([float(d.get("end_time", 0.0)) for d in raws])
    _a, e_sh, _r, sum0 = PD._p9_global(snaps)
    z_et, z_vlo, z_vhi, z_low, z_high = B.load_4h()                               # 4h volume-profile wick bands

    def in_sell_area(j):                                                          # price in the 4h SELLER wick [vq_hi, high]
        i4 = bisect.bisect_right(z_et, et[j]) - 1
        return i4 >= 0 and z_vhi[i4] <= cl[j] <= z_high[i4]

    def in_buy_area(j):                                                           # price in the 4h BUYER wick [low, vq_lo]
        i4 = bisect.bisect_right(z_et, et[j]) - 1
        return i4 >= 0 and z_low[i4] <= cl[j] <= z_vlo[i4]

    scalp = zz(list(hi), list(lo), SCALP_PCT); sc = np.array([""] * n, dtype=object)
    ph = pl = None; ss = []
    for pb, p, ih, cb in scalp:
        if ih:
            lab = "HH" if (ph is not None and p > ph) else ("LH" if ph is not None else ""); ph = p
        else:
            lab = "HL" if (pl is not None and p > pl) else ("LL" if pl is not None else ""); pl = p
        if lab:
            ss.append((cb, lab))
    ss.sort(); cur = ""; si = 0
    for j in range(n):
        while si < len(ss) and ss[si][0] <= j:
            cur = ss[si][1]; si += 1
        sc[j] = cur

    def p0cross(j, red):                                                         # recent (<=15 bar) P0 cross of +-50/0
        for k in range(max(1, j - 15), j + 1):
            for Lv in (50.0, 0.0, -50.0):
                if sum0[k - 1] - Lv > 0 and sum0[k] - Lv <= 0 and red:
                    return True
                if sum0[k - 1] - Lv < 0 and sum0[k] - Lv >= 0 and not red:
                    return True
        return False

    def p0(j):
        return sum0[max(0, j - LOCK)]

    def p2(j):
        return (2 * e_sh[max(0, j - LOCK)] - 1) * 100.0

    def scan(rr, trig_and):
        nets = []; why = {}; longs = []; shorts = []
        j = LOCK + K + 2
        while j < n - 1:
            ab_s = bvv[j] > svv[j] and cl[j] < op[j]; ab_l = svv[j] > bvv[j] and cl[j] > op[j]   # absorption candle
            trg_s = (ab_s and sc[j] == "LH") if trig_and else (ab_s or sc[j] == "LH")             # AND vs OR
            trg_l = (ab_l and sc[j] == "HL") if trig_and else (ab_l or sc[j] == "HL")
            # ARM on the full SSS confluence: 4h wick + P0>|50| + P2 STILL WITH the trend (peak) + P0-cross + trigger
            short_arm = in_sell_area(j) and p0(j) > P0_LVL and p2(j) > 0 and p0cross(j, True) and trg_s
            long_arm = in_buy_area(j) and p0(j) < -P0_LVL and p2(j) < 0 and p0cross(j, False) and trg_l
            if short_arm or long_arm:
                bull = long_arm; eb = j
                entry = float(cl[eb])
                if bull:
                    ext = float(np.min(lo[eb - K:eb + 1])); stop = ext * (1 - STOP_PAD); risk = (entry - stop) / entry; tp = entry * (1 + rr * risk)
                else:
                    ext = float(np.max(hi[eb - K:eb + 1])); stop = ext * (1 + STOP_PAD); risk = (stop - entry) / entry; tp = entry * (1 - rr * risk)
                if risk <= 1e-6:
                    j += 1; continue
                res = None
                for kk in range(eb + 1, n):
                    if (lo[kk] <= stop) if bull else (hi[kk] >= stop):
                        res = ("stop", stop, kk); break
                    if (hi[kk] >= tp) if bull else (lo[kk] <= tp):
                        res = ("tp", tp, kk); break
                if res is None:
                    res = ("edge", float(cl[-1]), n - 1)
                r, px, xb = res
                net = (((px - entry) if bull else (entry - px)) / entry * 100.0) - FEE
                nets.append(net); why[r] = why.get(r, 0) + 1
                (longs if bull else shorts).append(net)
                j = xb + 1; continue
            j += 1
        return nets, why, longs, shorts

    print("SSS Setup A — 4h wick + P0>|%.0f| + P2-peak + P0-cross; STRUCTURAL stop (beyond the 10-bar extreme)," % P0_LVL)
    print("fixed R:R, immediate entry, SEQUENTIAL, causal. n=%d.\n" % n)
    for trig_and in (False, True):
        print("=== trigger = %s ===" % ("absorption AND scalp reversal (stricter)" if trig_and
                                        else "absorption OR scalp reversal (recorded SSS)"))
        for rr in (1.0, 1.5, 2.0, 3.0):
            nets, why, longs, shorts = scan(rr, trig_and)
            o = stats(nets); ol = stats(longs); os_ = stats(shorts)
            if o["n"] == 0:
                print("  R:R %.1f | no trades" % rr); continue
            print("  R:R %.1f | n=%-3d W%d/L%d (%.0f%% win) | net %+.4f%%/tr | sum %+.1f%% | t=%+.2f | GROSS %+.4f%% | exits %s"
                  % (rr, o["n"], o["w"], o["l"], 100 * o["w"] / o["n"], o["mean"], o["tot"], o["t"], o["mean"] + FEE,
                     "/".join("%s%d" % (k, v) for k, v in why.items())))
            print("           long n=%d net %+.4f%% t%+.2f | short n=%d net %+.4f%% t%+.2f"
                  % (ol["n"], ol["mean"], ol["t"], os_["n"], os_["mean"], os_["t"]))
        print("")


if __name__ == "__main__":
    main()
