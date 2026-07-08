"""Hypothesis: for E2-entered PIVOT-ZZTRAIL trades, entering BEYOND the original E confirms momentum ->
higher win probability.  LONG: E2 price > E price ;  SHORT: E2 price < E price.  (E = the greyed baseline-
touch bar `entry_i`; E2 = the flip-rescue bar. Both prices are known at the E2 bar, so this is a causal,
pre-entry filter.)  Trades = base C (structural SL + ZigZag trailing exit). Win = gross>0; net = mean-0.10.
Run: python study/pivot_e2_vs_e.py
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

    def walk(det, j0, buy):
        """EXACT base-C mechanics: structural SL from the last LL/HH confirmed by D, trail on HL/LH swings
        confirmed after entry. Matches pivot_structure_zztrade variant C (+18.67%)."""
        entry = float(cl[j0])
        if buy:
            sl0 = last_low(det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else entry * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
        else:
            sl0 = last_high(det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
        exitlvl = sl0; tp = 0
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                lvl = trail[tp][1]; exitlvl = max(exitlvl, lvl) if buy else min(exitlvl, lvl); tp += 1
            if (lo[j] <= exitlvl) if buy else (hi[j] >= exitlvl):
                return ((exitlvl - entry) if buy else (entry - exitlvl)) / entry * 100.0
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []          # (gross, chasing_e2)  chasing = E2 beyond E (drop these)
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
        if e_held:
            if tier == "hollow":
                rows.append((walk(det, ent, buy), False))          # E entry -> never dropped
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                chasing = (float(cl[e2]) > float(cl[ent])) if buy else (float(cl[e2]) < float(cl[ent]))
                rows.append((walk(det, e2, buy), chasing))

    allg = np.array([g for g, ch in rows]); keepg = np.array([g for g, ch in rows if not ch])
    dropg = np.array([g for g, ch in rows if ch])
    allnet = allg - FEE; keepnet = keepg - FEE; dropnet = dropg - FEE
    print("PIVOT-ZZTRAIL: adopt the 'skip chasing-E2 (E2 beyond E)' filter?  (base C mechanics)\n")
    print("  ADOPT NO  — all trades       : n=%-3d | per-trade %+6.3f%% | win %4.1f%% | TOTAL %+7.2f%%"
          % (len(allnet), allnet.mean(), 100.0 * np.mean(allg > 0), allnet.sum()))
    print("  ADOPT YES — drop chasing-E2  : n=%-3d | per-trade %+6.3f%% | win %4.1f%% | TOTAL %+7.2f%%"
          % (len(keepnet), keepnet.mean(), 100.0 * np.mean(keepg > 0), keepnet.sum()))
    print("  (the %d dropped chasing trades: per-trade %+6.3f%% | TOTAL %+7.2f%%)"
          % (len(dropnet), dropnet.mean() if len(dropnet) else 0.0, dropnet.sum()))
    diff = keepnet.sum() - allnet.sum()
    print("\n  => adopting %s the total by %+.2f%% (over this ~2-week in-sample tape)"
          % ("RAISES" if diff > 0 else "LOWERS", diff))
    print("     per-trade: %+.3f%% -> %+.3f%%   | trades: %d -> %d"
          % (allnet.mean(), keepnet.mean(), len(allnet), len(keepnet)))

    print("\n  LOSER count (trade closed below entry, gross<0):")
    print("    ADOPT NO : %2d losers / %d trades  (%d winners)" % (np.sum(allg < 0), len(allg), np.sum(allg > 0)))
    print("    ADOPT YES: %2d losers / %d trades  (%d winners)" % (np.sum(keepg < 0), len(keepg), np.sum(keepg > 0)))
    print("    -> adopting cuts %d losers and %d winners  (the %d dropped chasing-E2 = %d losers + %d winners)"
          % (np.sum(allg < 0) - np.sum(keepg < 0), np.sum(allg > 0) - np.sum(keepg > 0),
             len(dropg), np.sum(dropg < 0), np.sum(dropg > 0)))
    print("  MONEY-loser count (net<0 after the 0.10 fee):")
    print("    ADOPT NO : %2d / %d   |   ADOPT YES: %2d / %d   -> cuts %d net-losers"
          % (np.sum(allnet < 0), len(allnet), np.sum(keepnet < 0), len(keepnet),
             np.sum(allnet < 0) - np.sum(keepnet < 0)))


if __name__ == "__main__":
    main()
