"""Hypothesis: a HOLLOW D is worth taking when it FADES the trend at the opposing wick:
    DOWNtrend  -> take hollow in the BUY zone  (Inzone-buy, the buyer wick / support)
    UPtrend    -> take hollow in the SELL zone (Inzone-sell, the seller wick / resistance)
Trend = sign of price change over the last 6h / 12h before detection (test both + 'both agree' + a 0.5% deadband
version). We keep the FROZEN hollow entry (E-held-else-E2) and just ask which hollow trades this trend x zone
rule keeps. Reports three-outcome (winner net>+0.05%% / breakeven |.|<=0.05%% / loser<-0.05%%) for the TAKE set,
each target cell, the WRONG-trend cells (does trend actually separate?), and trend-agnostic wick references.
Run: python study/pivot_hollow_trend_zone.py
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


def zone5(px, low, vlo, vhi, high):
    if px < low:
        return "beyond-down"
    if px <= vlo:
        return "inzone-buy"
    if px < vhi:
        return "body"
    if px <= high:
        return "inzone-sell"
    return "beyond-up"


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

    def chg_over(det, hours):
        t0 = et[det] - hours * 3600.0
        i = bisect.bisect_left(et, t0)
        if i >= det or i < 0:
            return None
        base = float(cl[i])
        return (float(cl[det]) - base) / base * 100.0 if base else None

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
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        if tier != "hollow":
            continue                                              # hypothesis = hollow only
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = None
        if e_held:
            j0 = ent
        else:
            te = float(et[ent])
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        i4 = bisect.bisect_right(z_et, et[det]) - 1
        if i4 < 0:
            continue
        z = zone5(float(cl[det]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])
        c6 = chg_over(det, 6.0); c12 = chg_over(det, 12.0)
        if c6 is None or c12 is None:
            continue                                              # need 12h lookback
        rows.append(dict(g=walk(det, j0, buy), buy=buy, z=z, c6=c6, c12=c12))

    G = np.array([r["g"] for r in rows]) - FEE

    def trend(c, thr):
        return "up" if c > thr else ("down" if c < -thr else "flat")

    def line(tag, mask):
        m = np.array(mask, bool); a = G[m]; nn = len(a)
        if nn == 0:
            print("    %-30s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
        print("    %-30s n=%-2d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f)"
              % (tag, nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0))

    zbuy = np.array([r["z"] == "inzone-buy" for r in rows])
    zsell = np.array([r["z"] == "inzone-sell" for r in rows])
    C6 = np.array([r["c6"] for r in rows]); C12 = np.array([r["c12"] for r in rows])
    print("HOLLOW fade-the-trend-at-the-wick  (%d hollow trades with 12h lookback)\n" % len(rows))
    print("  reference (trend-agnostic wicks):")
    line("all hollow", np.ones(len(rows), bool))
    line("hollow  Inzone-buy", zbuy); line("hollow  Inzone-sell", zsell)

    for name, C, thr in (("6h  (sign)", C6, 0.0), ("12h (sign)", C12, 0.0),
                         ("6h  (>0.5% deadband)", C6, 0.5), ("12h (>0.5% deadband)", C12, 0.5)):
        up = C > thr; dn = C < -thr
        take = (dn & zbuy) | (up & zsell)
        print("\n  TREND = %s  [up %d / down %d / flat %d]:" % (name, int(up.sum()), int(dn.sum()), int((~up & ~dn).sum())))
        line("TAKE (dn&buyzone | up&sellzone)", take)
        line("  cell A: down & Inzone-buy", dn & zbuy)
        line("  cell B: up   & Inzone-sell", up & zsell)
        line("  WRONG: up   & Inzone-buy", up & zbuy)
        line("  WRONG: down & Inzone-sell", dn & zsell)

    # both agree
    up2 = (C6 > 0) & (C12 > 0); dn2 = (C6 < 0) & (C12 < 0)
    take2 = (dn2 & zbuy) | (up2 & zsell)
    print("\n  TREND = 6h AND 12h agree  [up %d / down %d / mixed %d]:" % (int(up2.sum()), int(dn2.sum()), int((~up2 & ~dn2).sum())))
    line("TAKE (dn&buyzone | up&sellzone)", take2)
    line("  cell A: down & Inzone-buy", dn2 & zbuy)
    line("  cell B: up   & Inzone-sell", up2 & zsell)

    # by side within the 6h TAKE set
    up = C6 > 0; dn = C6 < 0; take = (dn & zbuy) | (up & zsell)
    buy = np.array([r["buy"] for r in rows])
    print("\n  6h TAKE set by D side:")
    line("  buy D", take & buy); line("  sell D", take & ~buy)


if __name__ == "__main__":
    main()
