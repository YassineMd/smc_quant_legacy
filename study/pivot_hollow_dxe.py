"""HOLLOW only — joint of D-zone x ENTRY-zone (where D printed vs where the E-held/E2 fill printed). Non-merged
4h wick, 5 zones (bottom->top): Beyond down / Inzone buy / Body range / Inzone sell / Beyond up. Reports: hollow
baseline; marginal by D-zone; marginal by entry-zone; AGREE (same zone) vs MOVED; a 5x5 win%%(n) + net$ matrix
(D rows x entry cols); and the full joint detail (three-outcome per non-empty cell), incl. a side split of the
standout cells. Frozen entries+exit. Three-outcome on NET. Run: python study/pivot_hollow_dxe.py
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
ZONES = ["beyond-down", "inzone-buy", "body", "inzone-sell", "beyond-up"]
AB = {"beyond-down": "BeyDn", "inzone-buy": "InBuy", "body": "Body", "inzone-sell": "InSell", "beyond-up": "BeyUp"}


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

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        if i4 < 0:
            return None
        return zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        if tier != "hollow":
            continue
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = ent if e_held else None
        if not e_held:
            te = float(et[ent])
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        zD = zone_at(det); zE = zone_at(j0)
        if zD is None or zE is None:
            continue
        rows.append(dict(g=walk(det, j0, buy), buy=buy, zD=zD, zE=zE, eheld=e_held))

    G = np.array([r["g"] for r in rows]) - FEE
    ZD = np.array([r["zD"] for r in rows]); ZE = np.array([r["zE"] for r in rows])
    BUY = np.array([r["buy"] for r in rows])

    def line(tag, mask):
        m = np.array(mask, bool); a = G[m]; nn = len(a)
        if nn == 0:
            print("  %-30s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
        print("  %-30s n=%-3d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f)"
              % (tag, nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0))

    print("HOLLOW  —  D-zone x ENTRY-zone joint  (%d hollow trades)\n" % len(rows))
    line("ALL hollow", np.ones(len(rows), bool))
    print("\n  MARGINAL by D-zone (where D printed):")
    for z in ZONES:
        line(AB[z], ZD == z)
    print("\n  MARGINAL by ENTRY-zone (where E-held/E2 filled):")
    for z in ZONES:
        line(AB[z], ZE == z)
    same = ZD == ZE
    print("\n  AGREE (D & entry same zone) vs MOVED:")
    line("AGREE (zD==zE)", same); line("MOVED (zD!=zE)", ~same)

    print("\n  MATRIX  win%%(n)  —  rows = D-zone, cols = ENTRY-zone:")
    hdr = "    %-8s" % "D \\ E" + "".join("%8s" % AB[z] for z in ZONES) + "   rowTOT$"
    print(hdr)
    for zd in ZONES:
        cells = ""
        for ze in ZONES:
            m = (ZD == zd) & (ZE == ze); nn = int(m.sum())
            if nn == 0:
                cells += "%8s" % "."
            else:
                cells += "%8s" % ("%.0f%%·%d" % (100.0 * (G[m] > BE).mean(), nn))
        rt = G[ZD == zd].sum() * 10.0
        print("    %-8s%s   %+6.0f" % (AB[zd], cells, rt))
    print("    %-8s" % "colTOT$" + "".join("%8.0f" % (G[ZE == ze].sum() * 10.0) for ze in ZONES))

    print("\n  JOINT DETAIL (three-outcome, non-empty cells, n>=3 first):")
    cells = []
    for zd in ZONES:
        for ze in ZONES:
            m = (ZD == zd) & (ZE == ze); nn = int(m.sum())
            if nn > 0:
                cells.append((nn, zd, ze, m))
    for nn, zd, ze, m in sorted(cells, key=lambda x: -x[0]):
        line("D=%-7s -> E=%-7s" % (AB[zd], AB[ze]), m)

    print("\n  SIDE split of the biggest cells (n>=6):")
    for nn, zd, ze, m in sorted(cells, key=lambda x: -x[0]):
        if nn >= 6:
            line("  %s->%s  BUY" % (AB[zd], AB[ze]), m & BUY)
            line("  %s->%s  SELL" % (AB[zd], AB[ze]), m & ~BUY)


if __name__ == "__main__":
    main()
