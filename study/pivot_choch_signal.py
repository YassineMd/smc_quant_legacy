"""Does a CHoCH (scalp ZigZag Change-of-Character) forecast PIVOT-ZZTRAIL trade quality?
  H1: an OPPOSITE CHoCH between D (detection) and the entry -> early signal of a LOSER.
      (long -> a bearish CHoCH; short -> a bullish CHoCH, break bar in [D, entry].)
  H2: a WITH CHoCH raises win probability -> tested (a) between D and entry (actionable, known pre-entry) and
      (b) after entry, during the hold (diagnostic only, known post-entry).
Trades = the frozen base C (all E2-tier entries, structural SL + ZigZag trailing exit, no filter). Win = gross>0.
Run: python study/pivot_choch_signal.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT, detect_choch        # noqa: E402

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
    chochs = detect_choch(list(hi), list(lo), list(cl))       # (swing_bar, swing_price, break_bar, dir)

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

    def walk(j0, buy, sl0, trail):
        entry = float(cl[j0]); exitlvl = sl0; tp = 0
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                lvl = trail[tp][1]
                exitlvl = max(exitlvl, lvl) if buy else min(exitlvl, lvl); tp += 1
            if (lo[j] <= exitlvl) if buy else (hi[j] >= exitlvl):
                g = (exitlvl - entry) / entry * 100.0 if buy else (entry - exitlvl) / entry * 100.0
                return g, j
        g = (cl[-1] - entry) / entry * 100.0 if buy else (entry - cl[-1]) / entry * 100.0
        return g, n - 1

    # E2-tier trades
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []
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
                trades.append((det, ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((det, e2, buy))

    def choch_in(lo_bar, hi_bar, direction):
        return any(lo_bar <= bb <= hi_bar and d == direction for _sb, _sp, bb, d in chochs)

    rows = []      # (gross, opp_pre, with_pre, with_post)
    for det, j0, buy in trades:
        if buy:
            sl0 = last_low(det, "LL")
            sl0 = sl0 * (1 - SL_PAD) if sl0 else float(cl[j0]) * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            wdir, odir = "bull", "bear"
        else:
            sl0 = last_high(det, "HH")
            sl0 = sl0 * (1 + SL_PAD) if sl0 else float(cl[j0]) * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            wdir, odir = "bear", "bull"
        g, ex = walk(j0, buy, sl0, trail)
        rows.append((g, choch_in(det, j0, odir), choch_in(det, j0, wdir), choch_in(j0 + 1, ex, wdir)))

    A = np.array([r[0] for r in rows])
    opp_pre = np.array([r[1] for r in rows]); with_pre = np.array([r[2] for r in rows])
    with_post = np.array([r[3] for r in rows]); with_any = with_pre | with_post

    def grp(mask):
        a = A[mask]
        if not len(a):
            return "  %-34s n=%-3d |   --   |   --" % ("", 0)
        return "n=%-3d | win %5.1f%% | net %+6.3f%%" % (len(a), 100.0 * np.mean(a > 0), a.mean() - FEE)

    print("PIVOT-ZZTRAIL + CHoCH signal test (scalp %.2f%%), %d trades\n" % (ZIGZAG_PCT, len(rows)))
    print("  ALL trades                         : " + grp(np.ones(len(rows), bool)))
    print("\n  H1: OPPOSITE CHoCH between D and entry -> loser signal?")
    print("    with opposite CHoCH [D,entry]    : " + grp(opp_pre))
    print("    without                          : " + grp(~opp_pre))
    print("\n  H2a: WITH CHoCH between D and entry (actionable) -> winner signal?")
    print("    with a with-CHoCH [D,entry]      : " + grp(with_pre))
    print("    without                          : " + grp(~with_pre))
    print("\n  H2b: WITH CHoCH pre OR post-entry (diagnostic) -> winner signal?")
    print("    with a with-CHoCH (pre|post)     : " + grp(with_any))
    print("    without                          : " + grp(~with_any))


if __name__ == "__main__":
    main()
