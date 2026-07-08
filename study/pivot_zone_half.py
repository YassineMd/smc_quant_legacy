"""Hypothesis: split each 4h wick zone at its midpoint. LONG losers sit in the LOWER half of the GREEN buy zone
(near the 4h low, the OUTER extreme); SHORT losers sit in the UPPER half of the RED sell zone (near the 4h high,
the OUTER extreme). i.e. winners are the shallow dips into the zone, losers are the deep pushes to the extreme.

In-zone = detection price within the band. Buy band [low, vq_lo]; pos=(px-low)/(vq_lo-low). Sell band [vq_hi,high];
pos=(px-vq_hi)/(high-vq_hi). LONG lower-half = pos<0.5 (near low) ; SHORT upper-half = pos>=0.5 (near high).
Unified 'OUTER' half = long lower / short upper (the predicted loser half); 'INNER' = the other.
Three-outcome (winner net>+0.05%% / breakeven |net|<=0.05%% / loser net<-0.05%%). Frozen exit throughout.
Run: python study/pivot_zone_half.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
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


def load_4h():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        for (x,) in con.execute("SELECT data FROM closed_buckets WHERE tf='4h' ORDER BY id"):
            b = json.loads(x)
            if b.get("levels"):
                by[float(b["end_time"])] = b
        con.close()
    b4 = [by[k] for k in sorted(by)]
    et = [float(b["end_time"]) for b in b4]; vlo = []; vhi = []; lw = []; hg = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); vlo.append(float(q[0])); vhi.append(float(q[2]))
        lw.append(float(b["low"])); hg.append(float(b["high"]))
    return et, vlo, vhi, lw, hg


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
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()
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

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
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
        i4 = bisect.bisect_right(z_et, et[det]) - 1
        if i4 < 0:
            continue
        px = float(cl[det]); g = walk(det, j0, buy)
        if buy:
            band = z_lo[i4] - z_low[i4]
            if band <= 0 or not (z_low[i4] <= px <= z_lo[i4]):
                continue
            pos = (px - z_low[i4]) / band                 # 0 = at 4h low (outer), 1 = at vq_lo (inner)
            outer = 1.0 - pos
        else:
            band = z_high[i4] - z_hi[i4]
            if band <= 0 or not (z_hi[i4] <= px <= z_high[i4]):
                continue
            pos = (px - z_hi[i4]) / band                  # 0 = at vq_hi (inner), 1 = at 4h high (outer)
            outer = pos
        rows.append((g, buy, pos, outer))

    G = np.array([r[0] for r in rows]); NET = G - FEE; BUY = np.array([r[1] for r in rows])
    POS = np.array([r[2] for r in rows]); OUT = np.array([r[3] for r in rows])
    d = 1000.0 / 100.0
    win = NET > BE; be = np.abs(NET) <= BE; los = NET < -BE
    outer_half = OUT >= 0.5                                 # predicted loser half (long lower / short upper)

    def line(tag, m):
        g = NET[m]; nn = max(1, len(g))
        if not len(g):
            print("  %-22s n=0" % tag); return
        print("  %-22s n=%-2d | W %2d (%4.1f%%) | BE %2d (%4.1f%%) | L %2d (%4.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f)"
              % (tag, len(g), int(win[m].sum()), 100.0 * win[m].sum() / nn,
                 int(be[m].sum()), 100.0 * be[m].sum() / nn,
                 int(los[m].sum()), 100.0 * los[m].sum() / nn, g.mean(), g.sum(), g.sum() * d))

    print("ZONE-HALF location test — in-zone trades split at each wick's midpoint  (n=%d in-zone)\n" % len(rows))
    print("  LONG (buy zone, lower=near 4h low=OUTER):")
    line("  lower half (outer)", BUY & outer_half); line("  upper half (inner)", BUY & ~outer_half)
    print("\n  SHORT (sell zone, upper=near 4h high=OUTER):")
    line("  upper half (outer)", ~BUY & outer_half); line("  lower half (inner)", ~BUY & ~outer_half)
    print("\n  UNIFIED (both sides):")
    line("OUTER half (predicted L)", outer_half); line("INNER half", ~outer_half)

    print("\n  DIRECT TEST — mean 'outerness' (1=at the 4h extreme, 0=at the wick's inner edge vq):")
    for tag, m in (("winners", win), ("breakeven", be), ("losers", los)):
        if m.any():
            print("    %-10s n=%-2d | mean outerness %.2f | mean pos %.2f" % (tag, int(m.sum()), OUT[m].mean(), POS[m].mean()))
    print("\n  (hypothesis predicts losers have HIGHER outerness than winners)")


if __name__ == "__main__":
    main()
