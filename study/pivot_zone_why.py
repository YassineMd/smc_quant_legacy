"""WHY does the 4h in-zone filter cut losers? Decompose the mechanism. In-zone (two-sided band, detection price):
LONG entry within the GREEN buyer wick [low, vq_lo] / SHORT within the RED seller wick [vq_hi, high] of the last
completed 4h bucket. Candidate mechanisms:
  (1) SMALLER MAE  - entering at a proven heavy-volume wick (support/resistance) -> price digs less against you
                     -> fewer initial-SL stop-outs.
  (2) MORE ARMING  - in-zone trades run far enough (+0.40%%) to arm the breakeven lock more often -> reversals
                     scratch at ~breakeven instead of becoming full losers.
  (3) TIGHTER/BETTER STOP - structural SL distance differs.
For every frozen-strategy trade we record MAE, MFE, armed?, structural stop distance, and exit type
(initial SL / trail / breakeven lock / end-of-data), then compare IN vs OUT zone, and dissect the LOSERS.
Run: python study/pivot_zone_why.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
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
        """returns dict: gross, mae, mfe, armed, stopdist(%), exit ('SL0'/'trail'/'lock'/'end')."""
        entry = float(cl[j0])
        if buy:
            sl0 = last(lows, det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else entry * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            sl0 = last(highs, det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        stopdist = abs(entry - sl0) / entry * 100.0
        exitlvl = sl0; tp = 0; armed = False; mae = 0.0; mfe = 0.0
        for j in range(j0 + 1, n):
            adv = (entry - lo[j]) / entry * 100.0 if buy else (hi[j] - entry) / entry * 100.0
            fav = (hi[j] - entry) / entry * 100.0 if buy else (entry - lo[j]) / entry * 100.0
            mae = max(mae, adv); mfe = max(mfe, fav)
            moved = False
            while tp < len(trail) and trail[tp][0] <= j:
                l2 = trail[tp][1]
                if (l2 > exitlvl) if buy else (l2 < exitlvl):
                    exitlvl = l2; moved = True
                tp += 1
            e = exitlvl; lock_binds = False
            if armed:
                if (lock_lvl > e) if buy else (lock_lvl < e):
                    e = lock_lvl; lock_binds = True
            if (lo[j] <= e) if buy else (hi[j] >= e):
                g = ((e - entry) if buy else (entry - e)) / entry * 100.0
                if lock_binds:
                    ex = "lock"
                elif abs(e - sl0) < 1e-9:
                    ex = "SL0"
                else:
                    ex = "trail"
                return dict(gross=g, mae=mae, mfe=mfe, armed=armed, stopdist=stopdist, exit=ex)
            if (hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl):
                armed = True
        g = ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0
        return dict(gross=g, mae=mae, mfe=mfe, armed=armed, stopdist=stopdist, exit="end")

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
        px = float(cl[det])
        inz = (z_low[i4] <= px <= z_lo[i4]) if buy else (z_hi[i4] <= px <= z_high[i4])
        r = walk(det, j0, buy); r["inz"] = inz; r["buy"] = buy
        rows.append(r)

    def sub(m):
        return [r for r in rows if m(r)]

    IN = sub(lambda r: r["inz"]); OUT = sub(lambda r: not r["inz"]); FEE_ = FEE

    def grp(tag, R):
        if not R:
            print("  %-9s n=0" % tag); return
        g = np.array([r["gross"] for r in R]); mae = np.array([r["mae"] for r in R])
        mfe = np.array([r["mfe"] for r in R]); sd = np.array([r["stopdist"] for r in R])
        arm = np.mean([r["armed"] for r in R]) * 100.0; los = g < 0
        print("  %-9s n=%-3d | loser %4.1f%% | MAE med %.2f%% mean %.2f%% | MFE med %.2f%% | armed %4.1f%% | stopdist %.2f%% | net %+.3f%%"
              % (tag, len(R), 100.0 * np.mean(los), np.median(mae), mae.mean(), np.median(mfe), arm,
                 sd.mean(), g.mean() - FEE_))

    print("WHY in-zone cuts losers — mechanism decomposition (%d trades, %d excl no-4h)\n" % (len(rows), no4h))
    grp("ALL", rows); grp("IN-ZONE", IN); grp("OUT-ZONE", OUT)

    # exit-type mix
    def mix(tag, R):
        c = {"SL0": 0, "trail": 0, "lock": 0, "end": 0}
        for r in R:
            c[r["exit"]] += 1
        tot = max(1, len(R))
        print("  %-9s exits: initial-SL %4.1f%% | trail %4.1f%% | lock %4.1f%% | end %4.1f%%"
              % (tag, 100.0 * c["SL0"] / tot, 100.0 * c["trail"] / tot, 100.0 * c["lock"] / tot, 100.0 * c["end"] / tot))
    print("\n  EXIT MIX:")
    mix("IN-ZONE", IN); mix("OUT-ZONE", OUT)

    # the losers themselves
    def losers(tag, R):
        L = [r for r in R if r["gross"] < 0]
        if not L:
            print("  %-9s no losers" % tag); return
        mae = np.array([r["mae"] for r in L]); g = np.array([r["gross"] for r in L])
        arm = np.mean([r["armed"] for r in L]) * 100.0
        exits = {}
        for r in L:
            exits[r["exit"]] = exits.get(r["exit"], 0) + 1
        print("  %-9s losers n=%-2d | avg loss %+.3f%% | MAE med %.2f%% | armed %4.1f%% | exits %s"
              % (tag, len(L), g.mean(), np.median(mae), arm, exits))
    print("\n  LOSER DISSECTION:")
    losers("IN-ZONE", IN); losers("OUT-ZONE", OUT)

    # the KEY comparison: did in-zone win by avoiding deep adverse moves? fraction with MAE beyond a few thresholds
    print("\n  ADVERSE-EXCURSION distribution (fraction of trades whose MAE exceeds X):")
    for thr in (0.2, 0.3, 0.5, 0.8):
        fi = np.mean([r["mae"] > thr for r in IN]) * 100.0
        fo = np.mean([r["mae"] > thr for r in OUT]) * 100.0
        print("    MAE > %.1f%% :  IN-ZONE %4.1f%%   OUT-ZONE %4.1f%%" % (thr, fi, fo))


if __name__ == "__main__":
    main()
