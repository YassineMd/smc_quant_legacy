"""Full PIVOT-4HZONE (wick) analysis on the frozen strategy: DATA-COVERAGE check first (4h only started ~Jun
22, so exclude any trade with no completed 4h bucket behind it), then frequency (trades/day), the in-zone vs
out-zone composition (losers/winners/big-winners), the FILTER-cut version, and the SIZE-UP-in-zone model with
drawdown. In-zone = buy price<=4h buyer wick (vq_lo) / sell price>=seller wick (vq_hi), last COMPLETED 4h
bucket. Run: python study/pivot_4hzone_full.py
"""
import os, sys, glob, json, sqlite3, bisect, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BIG = 0.5; CAP = 1000.0


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
    z_et = [float(b["end_time"]) for b in b4]; z_lo = []; z_hi = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); z_lo.append(float(q[0])); z_hi.append(float(q[2]))
    return z_et, z_lo, z_hi


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
    z_et, z_lo, z_hi = load_4h()
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

    def zone(det, buy):
        i = bisect.bisect_right(z_et, et[det]) - 1
        if i < 0:
            return None
        return (cl[det] <= z_lo[i]) if buy else (cl[det] >= z_hi[i])

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
    scan = {"long": 0, "short": 0}; allt = []; no4h = 0
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
        z = zone(det, buy)
        if z is None:
            no4h += 1; continue                                       # no completed 4h bucket yet -> exclude
        allt.append((float(et[det]), walk(det, j0, buy), z))

    allt.sort()
    G = np.array([g for _t, g, _z in allt]); Z = np.array([z for _t, g, z in allt])
    span = (allt[-1][0] - allt[0][0]) / 86400.0
    print("PIVOT-4HZONE full analysis (wick filter, frozen strategy)\n")
    print("  DATA COVERAGE: 4h data starts %s UTC; %d trades analyzed (%d excluded: no completed 4h bucket yet)."
          % (time.strftime("%Y-%m-%d", time.gmtime(z_et[0])), len(allt), no4h))
    print("  trade span: %.1f days -> %.1f trades/day total | %.1f/day in-zone | %.1f/day out-zone\n"
          % (span, len(allt) / span, int(Z.sum()) / span, int((~Z).sum()) / span))

    def comp(tag, m):
        a = G[m]
        if not len(a):
            print("  %-9s n=0" % tag); return
        w = a[a > 0]; l = a[a < 0]; big = a[a >= BIG]
        print("  %-9s n=%-3d | win %5.1f%% | losers %2d | winners %2d | big(>=%.1f) %2d | avgW %+.3f avgL %+.3f | net %+.3f%% | TOTAL %+.2f%%"
              % (tag, len(a), 100.0 * np.mean(a > 0), int(np.sum(a < 0)), int(np.sum(a > 0)), BIG, len(big),
                 w.mean() if len(w) else 0.0, l.mean() if len(l) else 0.0, a.mean() - FEE, (a - FEE).sum()))

    print("  COMPOSITION:")
    comp("ALL", np.ones(len(G), bool)); comp("IN-ZONE", Z); comp("OUT-ZONE", ~Z)

    print("\n  IF YOU FILTER (in-zone only, cut out-zone):")
    print("    cuts %d losers, %d winners, %d big winners | keeps %d trades (%.1f/day)"
          % (int(np.sum(G[~Z] < 0)), int(np.sum(G[~Z] > 0)), int(np.sum(G[~Z] >= BIG)),
             int(Z.sum()), int(Z.sum()) / span))
    print("    total %+.2f%% (in-zone) vs %+.2f%% (all)  -> %+.2f%%"
          % ((G[Z] - FEE).sum(), (G - FEE).sum(), (G[Z] - FEE).sum() - (G - FEE).sum()))

    print("\n  IF YOU SIZE UP IN-ZONE (keep ALL trades, in-zone x m, out-zone x1) — $%.0f base/trade:" % CAP)
    d = CAP / 100.0
    for m in (1.0, 1.5, 2.0):
        w = np.where(Z, m, 1.0)
        sized = (G - FEE) * w                                         # per-trade net %, size-weighted
        bal = 0.0; peak = 0.0; dd = 0.0
        for x in sized:
            bal += x * d; peak = max(peak, bal); dd = max(dd, peak - bal)   # $ equity drawdown
        avg_size = CAP * w.mean()
        print("    in-zone %.1fx : TOTAL %+.2f%%-units  ->  $%+.0f profit | max DD $%.0f | avg $/trade $%.0f"
              % (m, sized.sum(), sized.sum() * d, dd, avg_size))


if __name__ == "__main__":
    main()
