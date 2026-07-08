"""Re-test PIVOT-4HZONE (the wick location filter) with the current, larger trade set. A BUY pivot is 'in zone'
if its detection price is at/below the last COMPLETED 4h bucket's BUYER wick (price <= vq_lo); a SELL if
at/above the SELLER wick (price >= vq_hi). Two views: (1) RAW fires at the fixed +0.5/-0.3 exit -> TP% in vs
out + Fisher (directly comparable to the original 62.5 vs 34.2, p=0.006); (2) the FROZEN strategy (trailing +
lock) -> win/net/total in vs out. Run: python study/pivot_4hwick.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003; H_S = 6 * 3600.0
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


def load_4h_wicks():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        for (x,) in con.execute("SELECT data FROM closed_buckets WHERE tf='4h' ORDER BY id"):
            b = json.loads(x)
            if b.get("levels"):
                by[float(b["end_time"])] = b
        con.close()
    b4 = [by[k] for k in sorted(by)]
    z_et = [float(b["end_time"]) for b in b4]; z_lo = []; z_hi = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); z_lo.append(float(q[0])); z_hi.append(float(q[2]))
    return z_et, z_lo, z_hi


def fisher(a, b, c, d):
    N = a + b + c + d; r1 = a + b; r2 = c + d; c1 = a + c
    def pab(x): return comb(r1, x) * comb(r2, c1 - x) / comb(N, c1)
    p0 = pab(a); lo = max(0, c1 - r2); hi = min(r1, c1)
    return sum(pab(x) for x in range(lo, hi + 1) if pab(x) <= p0 + 1e-12)


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
    z_et, z_lo, z_hi = load_4h_wicks()
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

    def in_zone(det, buy):
        i = bisect.bisect_right(z_et, et[det]) - 1
        if i < 0:
            return None
        return (cl[det] <= z_lo[i]) if buy else (cl[det] >= z_hi[i])

    def walk_fixed(j0, buy):
        entry = float(cl[j0]); slv = entry * (1 - SL) if buy else entry * (1 + SL)
        tpv = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= slv) if buy else (hi[j] >= slv):
                return False
            if (hi[j] >= tpv) if buy else (lo[j] <= tpv):
                return True
        return None

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
    scan = {"long": 0, "short": 0}; raw = []; strat = []      # raw: (tp, zone) ; strat: (gross, zone)
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; z = in_zone(det, buy)
        if z is None:
            continue
        rf = walk_fixed(ent, buy)                                  # raw pivot at the fixed exit
        if rf is not None:
            raw.append((rf, z))
        p2d = spr(det, buy)
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
        if j0 is not None:
            strat.append((walk(det, j0, buy), z))

    # (1) RAW fires, fixed exit, TP% in vs out
    yin = [t for t, z in raw if z]; yout = [t for t, z in raw if not z]
    a = sum(yin); b = len(yin) - a; c = sum(yout); d = len(yout) - c
    print("PIVOT-4HZONE re-test (wick filter), current tape\n")
    print("  (1) RAW fires @ fixed +0.5/-0.3 exit  (compare: original in 62.5%% vs out 34.2%%, p=0.006):")
    print("      IN-ZONE  n=%-3d TP %.1f%%  |  OUT n=%-3d TP %.1f%%  |  Fisher p=%.4f"
          % (len(yin), 100.0 * a / max(1, len(yin)), len(yout), 100.0 * c / max(1, len(yout)), fisher(a, b, c, d)))
    # (2) frozen strategy, in vs out
    gin = np.array([g for g, z in strat if z]); gout = np.array([g for g, z in strat if not z])
    dd = 1000.0 / 100.0
    print("\n  (2) FROZEN strategy (trailing + lock):")
    for tag, arr in (("ALL     ", np.array([g for g, z in strat])), ("IN-ZONE ", gin), ("OUT-ZONE", gout)):
        if len(arr):
            print("      %s n=%-3d | win %.1f%% | net %+.3f%% | TOTAL %+.2f%% ($%+.0f)"
                  % (tag, len(arr), 100.0 * np.mean(arr > 0), (arr - FEE).mean(), (arr - FEE).sum(), (arr - FEE).sum() * dd))


if __name__ == "__main__":
    main()
