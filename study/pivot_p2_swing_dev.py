"""Hypothesis: the aligned P2 (panel-2 eff-agg spread) read at the scalp-ZigZag swings that DEVELOP after a
confirmed entry separates winners from losers, and grades winner size (will the trailing SL hit soon = small,
or ride = big). Tested LOCKED (spr[pb-7], causal at pb) and UNLOCKED (spr[pb], centered -> 7-bar look-ahead).
To avoid the tautology that a running winner mechanically prints aligned flow, the EARLY read = the FIRST
post-entry swing only (the true predictor); the ALL-swings mean is shown too but is partly circular.
Trades = base C (structural SL + ZigZag trailing exit). Run: python study/pivot_p2_swing_dev.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; LOCK = 7


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
    sw = zigzag_confirmed(list(hi), list(lo), thr)             # (pivot_bar, price, is_high, confirm_bar)
    pivbars = sorted(pb for pb, p, ih, cb in sw)               # scalp swing pivot bars, ascending
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
                g = ((exitlvl - entry) if buy else (entry - exitlvl)) / entry * 100.0
                return g, j
        return (((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0), n - 1

    import bisect
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []      # (gross, first_unl, first_lok, all_unl, all_lok, nsw)
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
        g, ex = walk(det, j0, buy)
        # scalp swing PIVOT bars that occurred during the hold (j0, ex]
        lo_p = bisect.bisect_right(pivbars, j0); hi_p = bisect.bisect_right(pivbars, ex)
        pbs = pivbars[lo_p:hi_p]
        unl = [spr(pb, buy) for pb in pbs]; lok = [spr(pb - LOCK, buy) for pb in pbs]
        rows.append((g, unl, lok))                             # gross, unlocked list, locked list

    have = [r for r in rows if r[1]]                           # >=1 post-entry swing
    G = np.array([r[0] for r in have]); base = G - FEE
    print("PIVOT-ZZTRAIL: P2 SIGN-FLIP (aligned eff-agg < 0) at a post-entry scalp swing -> loser?  (scalp %.2f%%)"
          % ZIGZAG_PCT)
    print("  %d trades, %d with >=1 post-entry swing | flip = flow turned AGAINST the trade at a swing" % (len(rows), len(have)))
    print("  BASE (all with a post-entry swing): n=%d | win %.1f%% | net %+6.3f%%\n"
          % (len(G), 100.0 * np.mean(G > 0), base.mean()))

    def lst(r, which):
        return r[1] if which == "unl" else r[2]

    def flagmask(which, mode):
        out = []
        for r in have:
            v = lst(r, which)
            if mode == "first":
                out.append(v[0] < 0)
            elif mode == "first2":
                out.append(any(x < 0 for x in v[:2]))
            else:  # any (circular)
                out.append(any(x < 0 for x in v))
        return np.array(out)

    def line(tag, m):
        a = G[m]
        if not len(a):
            return "    %-9s n=0" % tag
        return "    %-9s n=%-3d | win %5.1f%% | net %+6.3f%%" % (tag, len(a), 100.0 * np.mean(a > 0), a.mean() - FEE)

    for which, label in (("lok", "LOCKED  (causal — actionable)"), ("unl", "UNLOCKED (7-bar look-ahead)")):
        print("  == %s ==" % label)
        for mode, desc in (("first", "FIRST post-entry swing P2<0"),
                           ("first2", "any of FIRST-2 swings P2<0"),
                           ("any", "ANY post-entry swing P2<0 (circular)")):
            m = flagmask(which, mode)
            print("   %s:" % desc)
            print(line("flip<0", m)); print(line("no flip", ~m))
        print("")

    # THRESHOLD sweep on the LOCKED first-post-entry-swing P2 (causal). Ladder = robust; single best cut = overfit-prone.
    fl = np.array([r[2][0] for r in have])                     # first post-entry swing, locked P2
    print("  == LOCKED first-swing P2: BUCKET LADDER (is the relationship monotonic?) ==")
    edges = [(-1e9, -20), (-20, 0), (0, 20), (20, 40), (40, 60), (60, 1e9)]
    for a_lo, a_hi in edges:
        m = (fl >= a_lo) & (fl < a_hi); a = G[m]
        lab = ("<%+d" % a_hi) if a_lo < -1e8 else ((">=%+d" % a_lo) if a_hi > 1e8 else "[%+d,%+d)" % (a_lo, a_hi))
        print("    %-11s n=%-3d | win %5.1f%% | net %+6.3f%%"
              % (lab, len(a), 100.0 * np.mean(a > 0) if len(a) else 0.0, (a.mean() - FEE) if len(a) else 0.0))
    print("  == cut point sweep (trades with first-swing P2 BELOW the cut = the flagged bucket) ==")
    for T in (-20, -10, 0, 10, 20, 30):
        b = G[fl < T]; ab = G[fl >= T]
        print("    cut %+3d | BELOW n=%-3d win %5.1f%% net %+6.3f%%  |  AT/ABOVE n=%-3d win %5.1f%% net %+6.3f%%"
              % (T, len(b), 100.0 * np.mean(b > 0) if len(b) else 0.0, (b.mean() - FEE) if len(b) else 0.0,
                 len(ab), 100.0 * np.mean(ab > 0) if len(ab) else 0.0, (ab.mean() - FEE) if len(ab) else 0.0))


if __name__ == "__main__":
    main()
