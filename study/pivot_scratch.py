"""Test a 'scratch' rule ON TOP of the frozen strategy: if price goes -0.1% AGAINST entry (MAE), treat it as an
early loser sign and exit at breakeven (0%) or +0.1% on the recovery, instead of riding to the structural stop.
First DIAGNOSE: among base trades, do the ones that dip -0.1% actually lose more? (else scratching caps winners.)
Then MEASURE scratch-to-breakeven and scratch-to-+0.1% vs the frozen base. Run: python study/pivot_scratch.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; MAE = 0.0010   # -0.10% adverse = the scratch flag


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

    def walk(det, j0, buy, scratch):
        """scratch=None -> frozen base (struct SL + trail + lock). Else once -0.1% MAE is touched, exit at
        entry*(1+scratch) on the recovery (scratch=0 -> breakeven, 0.001 -> +0.1%). Returns (gross%, flagged)."""
        entry = float(cl[j0])
        if buy:
            sl0 = last(lows, det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else entry * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
            mae_lvl = entry * (1 - MAE); scr = entry * (1 + scratch) if scratch is not None else 0.0
        else:
            sl0 = last(highs, det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
            mae_lvl = entry * (1 + MAE); scr = entry * (1 - scratch) if scratch is not None else 0.0
        exitlvl = sl0; tp = 0; armed = False; scr_on = False; flagged = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                l2 = trail[tp][1]; exitlvl = max(exitlvl, l2) if buy else min(exitlvl, l2); tp += 1
            e = exitlvl
            if armed:
                e = max(e, lock_lvl) if buy else min(e, lock_lvl)
            if (lo[j] <= e) if buy else (hi[j] >= e):                      # 1. adverse stop (struct/trail/lock)
                return ((e - entry) if buy else (entry - e)) / entry * 100.0, flagged
            if scratch is not None and scr_on:                            # 2. scratch exit on recovery
                if (hi[j] >= scr) if buy else (lo[j] <= scr):
                    return ((scr - entry) if buy else (entry - scr)) / entry * 100.0, flagged
            if (lo[j] <= mae_lvl) if buy else (hi[j] >= mae_lvl):         # 3. -0.1% MAE -> flag / arm scratch
                flagged = True
                if scratch is not None:
                    scr_on = True
            if (hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl):         # 4. +0.4% MFE -> arm lock
                armed = True
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0, flagged

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trips = []          # (det, j0, buy)
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
        trips.append((det, j0, buy))

    base = np.array([walk(d, j, b, None)[0] for d, j, b in trips])
    flag = np.array([walk(d, j, b, None)[1] for d, j, b in trips])
    scrBE = np.array([walk(d, j, b, 0.0)[0] for d, j, b in trips])
    scr01 = np.array([walk(d, j, b, 0.001)[0] for d, j, b in trips])

    def line(tag, a):
        net = a - FEE
        return "  %-32s n=%-3d | win %5.1f%% | net %+.3f%%/trade | TOTAL %+7.2f%%" % (
            tag, len(a), 100.0 * np.mean(a > 0), net.mean(), net.sum())

    print("SCRATCH test: -0.1%% MAE -> exit at breakeven / +0.1%%  (on the frozen strategy, %d trades)\n" % len(base))
    print("  DIAGNOSTIC — do -0.1%% dips predict losers? (base outcomes, split by whether MAE hit -0.1%%)")
    bn = base - FEE
    print("    flagged (dipped -0.1%%)  : n=%-3d | win %5.1f%% | net %+.3f%%" %
          (flag.sum(), 100.0 * np.mean(base[flag] > 0), bn[flag].mean()))
    print("    NOT flagged            : n=%-3d | win %5.1f%% | net %+.3f%%" %
          ((~flag).sum(), 100.0 * np.mean(base[~flag] > 0), bn[~flag].mean()))
    print("\n  EXIT variants (whole book):")
    print(line("FROZEN base (no scratch)", base))
    print(line("+ scratch to BREAKEVEN (0%)", scrBE))
    print(line("+ scratch to +0.1%", scr01))
    for nm, a in (("scratch-BE", scrBE), ("scratch-0.1%", scr01)):
        saved = int(np.sum((base < 0) & (a > base + 1e-9)))
        capped = int(np.sum((base > 0) & (a < base - 1e-9)))
        print("    %-13s vs base: %d losers improved, %d winners trimmed, net %+.2f%%"
              % (nm, saved, capped, (a - FEE).sum() - (base - FEE).sum()))


if __name__ == "__main__":
    main()
