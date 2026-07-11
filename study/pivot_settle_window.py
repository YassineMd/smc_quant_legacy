"""Study the SETTLING WINDOW — what happens between the moment a signal prints (entry bar j0, on the unlocked
value) and when it confirms/repaints 7 buckets later (c = j0 + LOCK). Compares:
  A = enter@PRINT (j0)            — the settled-immediate look-ahead reference (+$234).
  B = enter@CONFIRMATION (j0+7)   — wait the full settle, then enter at market (no pullback).
Then dissects the [j0 -> c] window (aligned to the trade): close move, MFE, MAE, split by A-winner/breakeven/loser
— to see whether the 'edge' is simply the favourable price move that happens DURING settling (i.e. the look-ahead).
Three-outcome NET + t. Run: python study/pivot_settle_window.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, config               # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05
LOCK_LAG = config.LIVE_PANEL_WINDOW // 2        # 7


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
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps); e_sh = np.asarray(e_sh, float)
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
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = None
        if e_held:
            if tier == "hollow":
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
        c = j0 + LOCK_LAG
        gA = walk(det, j0, buy) - FEE
        gB = (walk(det, c, buy) - FEE) if c < n else None
        px0 = float(cl[j0]); sg = 1.0 if buy else -1.0
        end = min(c, n - 1)
        # settling window [j0+1 .. c], aligned to the trade
        mfe = mae = 0.0
        for k in range(j0 + 1, end + 1):
            fav = sg * (float(hi[k] if buy else lo[k]) - px0) / px0 * 100.0
            adv = sg * (px0 - float(lo[k] if buy else hi[k])) / px0 * 100.0
            mfe = max(mfe, fav); mae = max(mae, adv)
        cmove = sg * (float(cl[end]) - px0) / px0 * 100.0        # close move over the window
        rows.append(dict(buy=buy, gA=gA, gB=gB, mfe=mfe, mae=mae, cmove=cmove))

    gA = np.array([r["gA"] for r in rows]); gB = np.array([r["gB"] if r["gB"] is not None else np.nan for r in rows])
    mfe = np.array([r["mfe"] for r in rows]); mae = np.array([r["mae"] for r in rows]); cmove = np.array([r["cmove"] for r in rows])
    win = gA > BE; los = gA < -BE; beq = np.abs(gA) <= BE

    def show(tag, a):
        a = np.asarray(a); a = a[~np.isnan(a)]
        if not len(a):
            print("  %-28s n=0" % tag); return
        w = int((a > BE).sum()); l = int((a < -BE).sum()); nn = len(a)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if nn > 1 and a.std(ddof=1) > 0 else 0.0
        print("  %-28s n=%-3d | W %5.1f%% | L %5.1f%% | net %+.3f%% | TOT %+.2f%% ($%+.0f) | t=%+.2f"
              % (tag, nn, 100.0 * w / nn, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0, t))

    print("SETTLING WINDOW study  (print bar j0 -> confirmation j0+%d), %d trades\n" % (LOCK_LAG, len(rows)))
    print("A vs B — where you enter:")
    show("  A: enter@PRINT (look-ahead)", gA)
    show("  B: enter@CONFIRMATION+%d" % LOCK_LAG, gB)
    print("\nWhat happens in the %d-bucket window [j0 -> j0+%d], aligned to the trade:" % (LOCK_LAG, LOCK_LAG))
    print("  %-16s | close-move | MFE(fav) | MAE(adv)" % "A-outcome")
    for tag, m in (("WINNERS", win), ("BREAKEVEN", beq), ("LOSERS", los)):
        if m.any():
            print("  %-16s | %+8.3f%% | %+7.3f%% | %+7.3f%%" % (tag, cmove[m].mean(), mfe[m].mean(), mae[m].mean()))
    print("\n  corr(window close-move, full trade gA) = %.2f" % np.corrcoef(cmove, gA)[0, 1])
    # how much of the eventual move is already spent by confirmation, for winners
    wmask = win & ~np.isnan(gB)
    print("  A-winners: mean full trade %+.3f%% | mean move already made by confirmation %+.3f%% (%.0f%% of it)"
          % (gA[wmask].mean(), cmove[wmask].mean(), 100.0 * cmove[wmask].mean() / max(1e-9, gA[wmask].mean())))
    print("  => entering at confirmation (B) forgoes that window move; A 'knew' it because the settled signal IS it.")


if __name__ == "__main__":
    main()
