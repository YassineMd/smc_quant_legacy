"""P0 divergence from D to the ENTRY (E2 or E-held). P0 = panel-0 smoothed SUM line (sum0 in pivot_detect,
the signed confluence oscillator that crosses +/-50; +ve = bull confluence). For each frozen-strategy trade we
read aligned-P0 (sum0 for long, -sum0 for short, so higher = more in the trade's favour) at the DETECTION bar D
and at the ENTRY bar, and the price move D->entry (aligned).

DIVERGENCE question: as price pulls back into the entry (price_aligned < 0), does P0 hold up / rise
(dp0 = P0_entry - P0_D > 0)?  That 'confluence building while price dips' is bullish/supportive divergence;
P0 eroding into the entry (dp0 < 0) is the warning. We split trades by dp0 sign and by the price/P0 sign cross
(BULL-DIV / BEAR-DIV / CONFIRM) and report the three-outcome mix (winner net>+0.05%% / breakeven |net|<=0.05%% /
loser net<-0.05%%), plus mean dp0 per outcome and corr(dp0, net). Frozen exit throughout.
Run: python study/pivot_p0_divergence.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05


def load_1m():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    return [by[b] for b in sorted(by)]


def zz(H, L, thr):
    n = len(H); piv = []; direction = 0; hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        h = H[i]; l = L[i]
        if direction >= 0:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi * (1 - thr):
                piv.append((hi_i, hi, True, i)); direction = -1; lo, lo_i = l, i; continue
        if direction <= 0:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo * (1 + thr):
                piv.append((lo_i, lo, False, i)); direction = 1; hi, hi_i = h, i
    return piv


def main():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    fires, e_sh, sum0 = PD.detect_pivots(snaps, return_eff=True)     # sum0 = P0 panel-0 SUM line, byte-identical
    e_sh = np.asarray(e_sh, float); sum0 = np.asarray(sum0, float)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    sw = zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    lows = []; highs = []; ph = pl = None
    for pb, p, ih, cb in sw:
        if ih:
            lab = None if ph is None else ("HH" if p > ph else "LH"); ph = p
            if lab:
                highs.append((cb, pb, p, lab))
        else:
            lab = None if pl is None else ("HL" if p > pl else "LL"); pl = p
            if lab:
                lows.append((cb, pb, p, lab))
    lows.sort(); highs.sort()

    def last(arr, det, label):
        r = None
        for c, pb, p, lab in arr:
            if c > det:
                break
            if lab == label:
                r = p
        return r

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(det, j0, buy):
        entry = float(cl[j0])
        if buy:
            sl0 = last(lows, det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else entry * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            sl0 = last(highs, det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        exitlvl = sl0; tp = 0; armed = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                l2 = trail[tp][1]; exitlvl = max(exitlvl, l2) if buy else min(exitlvl, l2); tp += 1
            e = exitlvl
            if armed:
                e = max(e, lock_lvl) if buy else min(e, lock_lvl)
            if (lo[j] <= e) if buy else (hi[j] >= e):
                return ((e - entry) if buy else (entry - e)) / entry * 100.0
            if (hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl):
                armed = True
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0

    fires = sorted(fires, key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = None
        if e_held:
            if tier == "hollow":
                j0 = ent
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                j0 = e2
        if j0 is None:
            continue
        g = walk(det, j0, buy)
        pd = float(cl[det]); pe = float(cl[j0])
        ap0_d = sum0[det] if buy else -sum0[det]                 # aligned P0 at D
        ap0_e = sum0[j0] if buy else -sum0[j0]                   # aligned P0 at entry
        dp0 = ap0_e - ap0_d                                      # P0 change into the entry (aligned)
        pxal = ((pe - pd) if buy else (pd - pe)) / pd * 100.0    # price move D->entry (aligned; <0 = pullback)
        rows.append((g, buy, ap0_d, ap0_e, dp0, pxal))

    G = np.array([r[0] for r in rows]); NET = G - FEE
    AP0D = np.array([r[2] for r in rows]); AP0E = np.array([r[3] for r in rows])
    DP0 = np.array([r[4] for r in rows]); PXAL = np.array([r[5] for r in rows])
    d = 1000.0 / 100.0
    win = NET > BE; be = np.abs(NET) <= BE; los = NET < -BE

    def line(tag, m):
        g = NET[m]; nn = max(1, len(g))
        if not len(g):
            print("  %-24s n=0" % tag); return
        print("  %-24s n=%-3d | W %2d (%4.1f%%) | BE %2d (%4.1f%%) | L %2d (%4.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f)"
              % (tag, len(g), int(win[m].sum()), 100.0 * win[m].sum() / nn,
                 int(be[m].sum()), 100.0 * be[m].sum() / nn,
                 int(los[m].sum()), 100.0 * los[m].sum() / nn, g.mean(), g.sum(), g.sum() * d))

    print("P0 DIVERGENCE  D -> entry (E2 / E-held)  — P0 = panel-0 SUM line, aligned to trade side  (n=%d)\n" % len(rows))
    print("  CONTEXT: mean aligned-P0 @D %+.1f -> @entry %+.1f | mean dP0 %+.1f | mean price move D->entry %+.2f%% (<0=pullback)"
          % (AP0D.mean(), AP0E.mean(), DP0.mean(), PXAL.mean()))

    print("\n  by dP0 sign (did aligned-P0 rise or fall into the entry?):")
    line("P0 ROSE  (dP0 > 0)", DP0 > 0)
    line("P0 FELL  (dP0 <= 0)", DP0 <= 0)

    print("\n  divergence cross (price move vs P0 move, both aligned):")
    line("BULL-DIV (px dn, P0 up)", (PXAL < 0) & (DP0 > 0))
    line("BEAR-DIV (px up, P0 dn)", (PXAL > 0) & (DP0 < 0))
    line("CONFIRM+ (px up, P0 up)", (PXAL > 0) & (DP0 > 0))
    line("CONFIRM- (px dn, P0 dn)", (PXAL < 0) & (DP0 < 0))

    print("\n  mean dP0 per outcome (does divergence separate winners from losers?):")
    for tag, m in (("winners", win), ("breakeven", be), ("losers", los)):
        if m.any():
            print("    %-10s n=%-3d | mean dP0 %+.2f | mean P0@D %+.1f | mean P0@entry %+.1f | mean px %+.2f%%"
                  % (tag, int(m.sum()), DP0[m].mean(), AP0D[m].mean(), AP0E[m].mean(), PXAL[m].mean()))
    cw = np.corrcoef(DP0, (NET > BE).astype(float))[0, 1]
    cn = np.corrcoef(DP0, NET)[0, 1]
    print("\n  corr(dP0, winner) %.2f | corr(dP0, net) %.2f" % (cw, cn))

    print("\n  ALL (reference):"); line("ALL", np.ones(len(G), bool))


if __name__ == "__main__":
    main()
