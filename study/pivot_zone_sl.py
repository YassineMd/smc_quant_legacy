"""Zone-anchored stop test: instead of the structural SL (below the last ZigZag LL / above the last HH), place
the initial stop just OUTSIDE the 4h wick zone:
    LONG  SL = green-zone BOTTOM (4h candle low)  x (1 - 0.1%)
    SHORT SL = red-zone   TOP    (4h candle high) x (1 + 0.1%)
Everything else in the FROZEN exit is identical (HL/LH trail, +0.40%% arm -> +0.10%% breakeven lock, no fixed TP).
Guard: if the zone edge is NOT on the loss side of entry, fall back to the structural SL (and count it).
Runs three books on the same trades: BASE (structural), ZONE-SL non-merged, ZONE-SL merged (intersection band).
Reports win%/net/total, losers cut, avg loss, avg initial stop distance, and how many stops moved tighter/wider.
Run: python study/pivot_zone_sl.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; ZPAD = 0.001; LOOKBACK = 10


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


def merged_bands(vlo, vhi, lw, hg):
    N = len(vlo); out = []
    for i in range(N):
        blo, bhi = lw[i], vlo[i]
        for k in range(i - 1, max(-1, i - LOOKBACK), -1):
            if max(blo, lw[k]) < min(bhi, vlo[k]):
                blo, bhi = max(blo, lw[k]), min(bhi, vlo[k])
            else:
                break
        slo, shi = vhi[i], hg[i]
        for k in range(i - 1, max(-1, i - LOOKBACK), -1):
            if max(slo, vhi[k]) < min(shi, hg[k]):
                slo, shi = max(slo, vhi[k]), min(shi, hg[k])
            else:
                break
        out.append((blo, bhi, slo, shi))
    return out


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
    z_et, z_lo, z_hi, z_low, z_high = load_4h(); bands = merged_bands(z_lo, z_hi, z_low, z_high)
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

    def struct_sl(det, entry, buy):
        if buy:
            s = last(lows, det, "LL"); return s * (1 - SL_PAD) if s else entry * (1 - SL)
        s = last(highs, det, "HH"); return s * (1 + SL_PAD) if s else entry * (1 + SL)

    def walk(det, j0, buy, sl0):
        entry = float(cl[j0])
        if buy:
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
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
    scan = {"long": 0, "short": 0}; rows = []; no4h = 0
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
            no4h += 1; continue
        entry = float(cl[j0]); ssl = struct_sl(det, entry, buy)
        # zone-anchored candidate SL (non-merged uses raw 4h low/high; merged uses the intersection band edge)
        if buy:
            znm = z_low[i4] * (1 - ZPAD); zmg = bands[i4][0] * (1 - ZPAD)
            znm_ok = znm < entry; zmg_ok = zmg < entry
        else:
            znm = z_high[i4] * (1 + ZPAD); zmg = bands[i4][3] * (1 + ZPAD)
            znm_ok = znm > entry; zmg_ok = zmg > entry
        sl_nm = znm if znm_ok else ssl; sl_mg = zmg if zmg_ok else ssl
        g_base = walk(det, j0, buy, ssl)
        g_nm = walk(det, j0, buy, sl_nm)
        g_mg = walk(det, j0, buy, sl_mg)
        # initial stop distance (%) for each book
        dist = lambda x: abs(entry - x) / entry * 100.0
        rows.append((g_base, g_nm, g_mg, dist(ssl), dist(sl_nm), dist(sl_mg), znm_ok, zmg_ok,
                     ssl, sl_nm, sl_mg, buy))

    R = rows; d = 1000.0 / 100.0
    B = np.array([r[0] for r in R]); NM = np.array([r[1] for r in R]); MG = np.array([r[2] for r in R])
    dB = np.array([r[3] for r in R]); dNM = np.array([r[4] for r in R]); dMG = np.array([r[5] for r in R])
    nm_used = int(np.sum([r[6] for r in R])); mg_used = int(np.sum([r[7] for r in R]))

    def book(tag, g, dst, sl_arr_idx=None):
        w = g[g > 0]; l = g[g < 0]
        print("  %-16s n=%-3d | win %5.1f%% | losers %2d avgL %+.3f%% | net %+.3f%% | TOTAL %+.2f%% ($%+.0f) | avg stop %.2f%%"
              % (tag, len(g), 100.0 * np.mean(g > 0), int(np.sum(g < 0)), l.mean() if len(l) else 0.0,
                 g.mean() - FEE, (g - FEE).sum(), (g - FEE).sum() * d, dst.mean()))

    print("ZONE-ANCHORED STOP test — SL just outside the 4h wick zone vs structural (ZigZag) SL")
    print("  %d trades (%d excluded: no completed 4h bucket at detection)\n" % (len(R), no4h))
    book("BASE structural", B, dB)
    book("ZONE-SL nonmerged", NM, dNM)
    book("ZONE-SL merged", MG, dMG)
    print("\n  zone edge used as stop (on loss side of entry): non-merged %d/%d | merged %d/%d  (rest fell back to structural)"
          % (nm_used, len(R), mg_used, len(R)))
    # where did the stop move vs structural?
    tighter_nm = int(np.sum(dNM < dB - 1e-9)); wider_nm = int(np.sum(dNM > dB + 1e-9))
    tighter_mg = int(np.sum(dMG < dB - 1e-9)); wider_mg = int(np.sum(dMG > dB + 1e-9))
    print("  stop moved (vs structural): non-merged  tighter %d / wider %d ; merged  tighter %d / wider %d"
          % (tighter_nm, wider_nm, tighter_mg, wider_mg))

    # decompose: on trades where the zone stop is TIGHTER, does it help or hurt?
    def delta(tag, g, mask):
        if not mask.any():
            print("    %-22s n=0" % tag); return
        db = (B[mask] - FEE).sum(); dg = (g[mask] - FEE).sum()
        print("    %-22s n=%-3d | base TOTAL %+.2f%% -> zone %+.2f%% (%+.2f%%) | losers %d->%d"
              % (tag, int(mask.sum()), db, dg, dg - db, int(np.sum(B[mask] < 0)), int(np.sum(g[mask] < 0))))

    print("\n  EFFECT on the trades whose stop actually moved:")
    delta("nonmerged tighter", NM, dNM < dB - 1e-9)
    delta("nonmerged wider", NM, dNM > dB + 1e-9)
    delta("merged tighter", MG, dMG < dB - 1e-9)
    delta("merged wider", MG, dMG > dB + 1e-9)


if __name__ == "__main__":
    main()
