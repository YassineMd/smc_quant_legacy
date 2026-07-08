"""Hypothesis (out-of-zone losers): a trade that fires OUTSIDE its own 4h wick zone loses when it fires close to
the OPPOSITE zone -- a SHORT firing down near the GREEN/buy zone (support), a LONG firing up near the RED/sell
zone (resistance). i.e. fading INTO the opposing 4h level is the loser tell.

Own zone   : long -> GREEN buy wick [low, vq_lo] ; short -> RED sell wick [vq_hi, high]
Opposite   : long -> RED  ; short -> GREEN
Price used : the E2/E-held ENTRY price (cl[j0]) -- 'where the fire is'. 4h bucket = last completed at detection.

For each trade compute own_dist / opp_dist (% distance from entry price to each band; 0 if inside). Split ALL
trades into IN-OWN (in zone), OUT-closer-to-OWN, OUT-closer-to-OPPOSITE, and report win/net/total per group and
per side. Then the direct test: among OUT trades, mean lean (own_dist - opp_dist) for winners vs losers. Finally
the FILTER: drop OUT-closer-to-OPPOSITE -> losers/winners cut, net effect. Frozen exit (structural SL) throughout.
Run: python study/pivot_zone_opposite.py
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


def bdist(px, a, b):
    """% -free raw distance from px to band [a,b] (0 if inside)."""
    if px < a:
        return a - px
    if px > b:
        return px - b
    return 0.0


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
        px = float(cl[j0])
        green = (z_low[i4], z_lo[i4]); red = (z_hi[i4], z_high[i4])       # [low..vq_lo] , [vq_hi..high]
        own, opp = (green, red) if buy else (red, green)
        own_d = bdist(px, own[0], own[1]) / px * 100.0
        opp_d = bdist(px, opp[0], opp[1]) / px * 100.0
        rows.append((walk(det, j0, buy), buy, own_d, opp_d))

    G = np.array([r[0] for r in rows]); BUY = np.array([r[1] for r in rows])
    OWN = np.array([r[2] for r in rows]); OPP = np.array([r[3] for r in rows])
    in_own = OWN <= 1e-9                                                 # inside own zone (= 'in zone')
    out = ~in_own
    closer_opp = out & (OPP < OWN)                                       # out of own zone AND nearer the opposite
    closer_own = out & (OPP >= OWN)                                      # out but still leaning to own side
    d = 1000.0 / 100.0

    def rep(tag, m):
        a = G[m]
        if not len(a):
            print("  %-22s n=0" % tag); return
        l = a[a < 0]
        print("  %-22s n=%-3d | win %5.1f%% | losers %2d | net %+.3f%% | TOTAL %+.2f%% ($%+.0f)"
              % (tag, len(a), 100.0 * np.mean(a > 0), int(np.sum(a < 0)), a.mean() - FEE,
                 (a - FEE).sum(), (a - FEE).sum() * d))

    print("OUT-OF-ZONE 'into the opposite wick' test  (%d trades, %d excluded: no 4h bucket)\n" % (len(rows), no4h))
    print("  GROUPS (own zone = long GREEN / short RED ; opposite = the other):")
    rep("IN-OWN (in zone)", in_own)
    rep("OUT closer to OWN", closer_own)
    rep("OUT closer to OPPOSITE", closer_opp)

    print("\n  DIRECT TEST — among OUT trades, mean lean (own_dist - opp_dist); NEGATIVE = nearer the opposite:")
    ow = G[out] > 0; win_lean = (OWN[out] - OPP[out])[ow]; los_lean = (OWN[out] - OPP[out])[~ow]
    print("    winners  n=%-3d mean lean %+.3f%%   |   losers  n=%-3d mean lean %+.3f%%"
          % (len(win_lean), win_lean.mean() if len(win_lean) else 0.0,
             len(los_lean), los_lean.mean() if len(los_lean) else 0.0))

    for side, msk in (("LONG", BUY), ("SHORT", ~BUY)):
        print("\n  %s only:" % side)
        rep("  IN-OWN", in_own & msk)
        rep("  OUT closer OWN", closer_own & msk)
        rep("  OUT closer OPP", closer_opp & msk)

    print("\n  FILTER — drop OUT-closer-to-OPPOSITE (keep the rest):")
    keep = ~closer_opp
    cut = closer_opp
    print("    cuts %d trades: %d losers, %d winners"
          % (int(cut.sum()), int(np.sum(G[cut] < 0)), int(np.sum(G[cut] > 0))))
    print("    KEEP n=%-3d win %.1f%% net %+.3f%% | TOTAL %+.2f%% ($%+.0f)   vs  ALL %+.2f%% ($%+.0f)  -> %+.2f%%"
          % (int(keep.sum()), 100.0 * np.mean(G[keep] > 0), (G[keep] - FEE).mean(),
             (G[keep] - FEE).sum(), (G[keep] - FEE).sum() * d, (G - FEE).sum(), (G - FEE).sum() * d,
             (G[keep] - FEE).sum() - (G - FEE).sum()))


if __name__ == "__main__":
    main()
