"""Test a breakeven-plus lock ON TOP of base C: once price reaches +0.35% in our favor (MFE), arm a stop at
+0.1% (lock a small profit). Effective stop = max(structural SL, ZigZag trail, +0.1% lock) for a long / min
for a short. Lock takes effect the bar AFTER +0.35% is first touched (conservative, no same-bar peek).
Study the impact vs base C on:  loser cut (losses converted to +0.1% wins) / winner cut (profit given up) /
big-winner cut (>=0.5% winners capped). Run: python study/pivot_be_lock.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001
ARM = 0.0040; LOCK = 0.0010; BIG = 0.5   # ADOPTED strategy params (2026-07-07): arm +0.40% MFE -> lock +0.10%
#                                          (chosen for robustness, not the 0.35/0.10 in-sample peak). BIG win >= +0.5%.


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


def zigzag_confirmed(H, L, thr):
    n = len(H)
    if n < 2:
        return []
    piv = []; direction = 0
    hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
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
    raws = load_1m()
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    thr = ZIGZAG_PCT / 100.0
    sw = zigzag_confirmed(list(hi), list(lo), thr)
    lows = []; highs = []
    ph = pl = None
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

    def last_low(det, label=None):
        r = None
        for c, pb, p, lab in lows:
            if c > det:
                break
            if label is None or lab == label:
                r = p
        return r

    def last_high(det, label=None):
        r = None
        for c, pb, p, lab in highs:
            if c > det:
                break
            if label is None or lab == label:
                r = p
        return r

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, buy, sl0, trail, arm_pct, lock_pct):
        """arm_pct None -> base C (no lock). Else arm a +lock_pct stop once +arm_pct MFE is touched (bar after)."""
        entry = float(cl[j0]); on = arm_pct is not None
        if on:
            arm_lvl = entry * (1 + arm_pct) if buy else entry * (1 - arm_pct)
            lock_lvl = entry * (1 + lock_pct) if buy else entry * (1 - lock_pct)
        exitlvl = sl0; tp = 0; armed = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                lvl = trail[tp][1]; exitlvl = max(exitlvl, lvl) if buy else min(exitlvl, lvl); tp += 1
            e = exitlvl
            if on and armed:
                e = max(e, lock_lvl) if buy else min(e, lock_lvl)
            if (lo[j] <= e) if buy else (hi[j] >= e):
                return ((e - entry) if buy else (entry - e)) / entry * 100.0
            if on and ((hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl)):
                armed = True
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []              # (j0, buy, sl0, trail) — structure precomputed
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
        if buy:
            sl0 = last_low(det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else float(cl[j0]) * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
        else:
            sl0 = last_high(det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else float(cl[j0]) * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
        trades.append((j0, buy, sl0, trail))

    def run(arm, lk):
        return np.array([walk(j0, buy, sl0, trail, arm, lk) for j0, buy, sl0, trail in trades])

    base_g = run(None, None); base_total = (base_g - FEE).sum()
    ARMS = [0.0025, 0.0030, 0.0035, 0.0040, 0.0050]
    LOCKS = [0.0005, 0.0010, 0.0015, 0.0020]
    print("BREAKEVEN-LOCK robustness sweep on base C (%d trades) — cell = TOTAL net%%\n" % len(trades))
    print("  arm \\ lock" + "".join("   +%.2f%%" % (lk * 100) for lk in LOCKS))
    for arm in ARMS:
        cells = []
        for lk in LOCKS:
            cells.append("      -- " if lk >= arm else "  %+7.2f" % (run(arm, lk) - FEE).sum())
        print("   +%.2f%%  " % (arm * 100) + "".join(cells))
    print("\n  BASE (no lock): %+.2f%%   -> every cell above this = robust improvement" % base_total)
    print("\n  detail at select cells (TOTAL / win%% / losers, base losers=%d):" % int(np.sum(base_g < 0)))
    for arm, lk in [(0.0035, 0.0010), (0.0030, 0.0010), (0.0040, 0.0010), (0.0035, 0.0005),
                    (0.0035, 0.0015), (0.0025, 0.0005), (0.0050, 0.0020)]:
        g = run(arm, lk)
        print("    arm +%.2f%% / lock +%.2f%% : %+7.2f%% | win %4.1f%% | losers %d"
              % (arm * 100, lk * 100, (g - FEE).sum(), 100.0 * np.mean(g > 0), int(np.sum(g < 0))))


if __name__ == "__main__":
    main()
