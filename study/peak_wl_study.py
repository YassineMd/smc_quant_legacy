"""STANDALONE — WINNERS vs LOSERS of the in-zone peak/trough fade. Why do the winners win? (t-table). NOT Pivot V3.

Re-runs the fade detector (SHORT in 4h sell wick / LONG in 4h buy wick, P0>|50|, absorption-or-scalp trigger, stop
beyond the recent extreme, R:R target, SEQUENTIAL), records the ENTRY features per trade, splits TP (winner) vs
stop (loser), and prints each feature's mean for each group + a Welch t (winner-vs-loser). Everything ALIGNED to the
TRADE ('+' = the market/panel leaning the trade's way = with-momentum; fades are typically negative). n=1m tape.
Run: python study/peak_wl_study.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, config               # noqa: E402
import pivot_v3_de_zone_pdf as B                          # noqa: E402

LOCK = PD.LOCK; FEE = 0.10; BE = 0.05; MIN_CYC = 4
SCALP_PCT = 0.0015; SWING_PCT = 0.005; K = 10; STOP_PAD = 0.001; P0_LVL = 50.0
RR = 1.5; REQ_P2 = True


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


def labels(piv, n):
    out = np.array([""] * n, dtype=object); ph = pl = None; ss = []
    for pb, p, ih, cb in piv:
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
        out[j] = cur
    return out


def build_cycles(sh, m):
    n = len(sh); cyc = []; i0 = 0; dom = sh[0] >= 0.5
    for k in range(1, n):
        if (sh[k] >= 0.5) != dom:
            cyc.append([i0, k - 1, dom]); i0 = k; dom = sh[k] >= 0.5
    cyc.append([i0, n - 1, dom])
    while len(cyc) > 1:
        si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
        if (cyc[si][1] - cyc[si][0] + 1) >= m:
            break
        cyc[si][2] = not cyc[si][2]; mg = [cyc[0]]
        for c in cyc[1:]:
            if c[2] == mg[-1][2]:
                mg[-1][1] = c[1]
            else:
                mg.append(c)
        cyc = mg
    return cyc


def welch_t(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va = a.var(ddof=1) / len(a); vb = b.var(ddof=1) / len(b)
    return (a.mean() - b.mean()) / np.sqrt(va + vb) if (va + vb) > 0 else 0.0


def main():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    op = np.array([float(d.get("open", d.get("open_price", 0.0))) for d in raws])
    cl = np.array([float(d.get("close", d.get("close_price", 0.0))) for d in raws])
    hi = np.array([float(d.get("high", 0.0)) for d in raws]); lo = np.array([float(d.get("low", 0.0)) for d in raws])
    bvv = np.array([float(d.get("buy_vol", 0.0)) for d in raws]); svv = np.array([float(d.get("sell_vol", 0.0)) for d in raws])
    et = np.array([float(d.get("end_time", 0.0)) for d in raws])
    _a, e_sh, _r, sum0 = PD._p9_global(snaps); e_shc = PD.eff_causal_share(snaps)
    z_et, z_vlo, z_vhi, z_low, z_high = B.load_4h()
    sc = labels(zz(list(hi), list(lo), SCALP_PCT), n); sw = labels(zz(list(hi), list(lo), SWING_PCT), n)
    cyc = build_cycles(e_sh, MIN_CYC); cyc_ends = [c[1] for c in cyc]

    def hms2(lb):
        m = bisect.bisect_left(cyc_ends, lb)
        if m < 1:
            return 0.0
        w = cyc[max(0, m - 2):m]; s0 = w[0][0]; s1 = w[-1][1]
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1 - e_sh[k]) for k in range(s0, s1 + 1) if 0 <= k < n]
        hm = len(dom) / sum(1 / v for v in dom) if dom else 0.5
        return (2 * hm - 1) * 100 * (1 if float(np.mean(e_sh[s0:s1 + 1])) >= 0.5 else -1)

    def cur_cycle(lb):
        m = min(bisect.bisect_left(cyc_ends, lb), len(cyc) - 1)
        s0 = cyc[m][0]; s1 = min(lb, cyc[m][1])
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1 - e_sh[k]) for k in range(s0, s1 + 1) if 0 <= k < n]
        hm = len(dom) / sum(1 / v for v in dom) if dom else 0.5
        sp = (2 * hm - 1) * 100 * (1 if float(np.mean(e_sh[s0:s1 + 1])) >= 0.5 else -1)
        return sp, ((et[s1] - et[s0]) / 60.0 if s1 > s0 else 0.0)

    def p0cross(j, red):
        for k in range(max(1, j - 15), j + 1):
            for Lv in (50.0, 0.0, -50.0):
                if sum0[k - 1] - Lv > 0 and sum0[k] - Lv <= 0 and red:
                    return True
                if sum0[k - 1] - Lv < 0 and sum0[k] - Lv >= 0 and not red:
                    return True
        return False

    def in_sell(j):
        i4 = bisect.bisect_right(z_et, et[j]) - 1
        return i4 >= 0 and z_vhi[i4] <= cl[j] <= z_high[i4]

    def in_buy(j):
        i4 = bisect.bisect_right(z_et, et[j]) - 1
        return i4 >= 0 and z_low[i4] <= cl[j] <= z_vlo[i4]

    trades = []; j = LOCK + K + 2
    while j < n - 1:
        lb = max(0, j - LOCK); p2b = (2 * e_sh[lb] - 1) * 100
        s_ok = in_sell(j) and sum0[lb] > P0_LVL and (p2b > 0 or not REQ_P2) and (bvv[j] > svv[j] and cl[j] < op[j] or sc[j] == "LH")
        l_ok = in_buy(j) and sum0[lb] < -P0_LVL and (p2b < 0 or not REQ_P2) and (svv[j] > bvv[j] and cl[j] > op[j] or sc[j] == "HL")
        if s_ok or l_ok:
            bull = l_ok; d = 1 if bull else -1; entry = float(cl[j])
            if bull:
                ext = float(np.min(lo[j - K:j + 1])); stop = ext * (1 - STOP_PAD); risk = (entry - stop) / entry; tp = entry * (1 + RR * risk)
            else:
                ext = float(np.max(hi[j - K:j + 1])); stop = ext * (1 + STOP_PAD); risk = (stop - entry) / entry; tp = entry * (1 - RR * risk)
            if risk <= 1e-6:
                j += 1; continue
            res = None
            for kk in range(j + 1, n):
                if (lo[kk] <= stop) if bull else (hi[kk] >= stop):
                    res = ("loss", stop, kk); break
                if (hi[kk] >= tp) if bull else (lo[kk] <= tp):
                    res = ("win", tp, kk); break
            if res is None:
                res = ("edge", float(cl[-1]), n - 1)
            r, px, xb = res
            up = cl[j] >= op[j]; chm, age = cur_cycle(lb)
            absorb = (bvv[j] > svv[j] and not up) if not bull else (svv[j] > bvv[j] and up)
            neon = (svv[j] > 1.5 * bvv[j] and not up) if not bull else (bvv[j] > 1.5 * svv[j] and up)
            bw = 1.0 if absorb else (0.7 if neon else 0.3)
            trades.append(dict(
                r=r, bull=bull,
                p2_lock=(2 * e_sh[lb] - 1) * 100 * d, p2_nonlock=(2 * e_shc[j] - 1) * 100 * d,
                hms2=hms2(lb) * d, cur_hm=chm * d, cyc_min=age, p0=sum0[lb] * d,
                p0_cross=1.0 if p0cross(j, red=(not bull)) else 0.0,
                absorb=1.0 if absorb else 0.0, neon=1.0 if neon else 0.0, border_w=bw,
                body_with=1.0 if (up == bull) else 0.0,
                scalp_rev=1.0 if (sc[j] in (("LH", "LL") if not bull else ("HH", "HL"))) else 0.0,
                swing_rev=1.0 if (sw[j] in (("LH", "LL") if not bull else ("HH", "HL"))) else 0.0,
                risk=risk * 100))
            j = xb + 1; continue
        j += 1

    W = [t for t in trades if t["r"] == "win"]; Lo = [t for t in trades if t["r"] == "loss"]
    print("PEAK/TROUGH FADE — WINNERS vs LOSERS (in-zone, R:R %.1f, P2-filter=%s). n=%d: W=%d L=%d edge=%d"
          % (RR, REQ_P2, len(trades), len(W), len(Lo), len(trades) - len(W) - len(Lo)))
    print("(aligned to the TRADE: '+' = panel leaning the trade's way = with-momentum. |t|>=2 = significant)\n")
    feats = [("p2_lock", "P2 eff-agg LOCKED spread"), ("p2_nonlock", "P2 eff-agg NON-LOCKED spread"),
             ("hms2", "P2 HMS (last-2-cycle) spread"), ("cur_hm", "P2 current-cycle HM spread"),
             ("cyc_min", "P2 current-cycle age (min)"), ("p0", "P0 level"),
             ("p0_cross", "P0 recent reversal cross (0/1)"), ("absorb", "candle ABSORB border (0/1)"),
             ("neon", "candle heavy-neon border (0/1)"), ("border_w", "candle border thickness"),
             ("body_with", "candle body with the trade (0/1)"), ("scalp_rev", "scalp ZZ reversed (0/1)"),
             ("swing_rev", "swing ZZ reversed (0/1)"), ("risk", "stop distance %")]
    print("  %-32s   winner    loser      t" % "feature")
    for key, lab in feats:
        mw = np.mean([t[key] for t in W]) if W else 0; ml = np.mean([t[key] for t in Lo]) if Lo else 0
        t = welch_t([t[key] for t in W], [t[key] for t in Lo])
        print("  %-32s %+8.3f %+8.3f %+7.2f%s" % (lab, mw, ml, t, "  <==" if abs(t) >= 2 else ""))


if __name__ == "__main__":
    main()
