"""Is Mode-4 KINETIC STRENGTH related to trade success? Kinetic = velocity x effort-ratio per bucket
(bull = (buy_vol/dur)*(buyer_er/100), bear the mirror). For each frozen-strategy trade, read the ALIGNED net
kinetic (bull-bear, signed to the trade) at D and at entry, and the trade-side kinetic, then split trades into
terciles by each and compare win%/net. Also the correlation with the outcome. Run: python study/pivot_kinetic.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010


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
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    # per-bucket kinetic (Mode-4 formula)
    BK = np.zeros(n); SK = np.zeros(n)
    for i, d in enumerate(raws):
        dur = max(1.0, float(d.get("end_time", 0.0)) - float(d.get("start_time", 0.0)))
        BK[i] = (float(d.get("buy_vol", 0.0)) / dur) * (float(d.get("buyer_er", 0.0)) / 100.0)
        SK[i] = (float(d.get("sell_vol", 0.0)) / dur) * (float(d.get("seller_er", 0.0)) / 100.0)

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
    scan = {"long": 0, "short": 0}; rows = []      # (gross, align_d, align_e, side_e)
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
        ad = (BK[det] - SK[det]) if buy else (SK[det] - BK[det])
        ae = (BK[j0] - SK[j0]) if buy else (SK[j0] - BK[j0])
        se = BK[j0] if buy else SK[j0]
        rows.append((g, ad, ae, se))

    G = np.array([r[0] for r in rows])

    def terciles(metric_idx, name):
        m = np.array([r[metric_idx] for r in rows]); order = np.argsort(m)
        k = len(rows) // 3
        grp = {"LOW ": order[:k], "MID ": order[k:2 * k], "HIGH": order[2 * k:]}
        # point-biserial-ish: correlation of the metric with win (gross>0) and with net
        cw = np.corrcoef(m, (G > 0).astype(float))[0, 1]
        cn = np.corrcoef(m, G)[0, 1]
        print("  %s  (corr with win %.2f | corr with net %.2f)" % (name, cw, cn))
        for tag, idx in grp.items():
            a = G[idx]
            print("     %s kinetic n=%-3d | win %5.1f%% | net %+.3f%% | mean kin %s"
                  % (tag, len(a), 100.0 * np.mean(a > 0), a.mean() - FEE,
                     ("%+.0f" % np.mean([rows[i][metric_idx] for i in idx]))))

    print("KINETIC STRENGTH vs frozen-strategy outcome — %d trades\n" % len(rows))
    terciles(1, "ALIGNED net kinetic @ D (bull-bear, signed to trade)")
    print()
    terciles(2, "ALIGNED net kinetic @ ENTRY")
    print()
    terciles(3, "TRADE-SIDE kinetic @ ENTRY (bull for long / bear for short)")


if __name__ == "__main__":
    main()
