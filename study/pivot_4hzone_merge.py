"""Does the NEIGHBOUR-MERGED 4h wick zone differ from the non-merged single-bucket zone (as drawn in the
terminal)? The terminal now walks back from the last COMPLETED 4h bucket and merges any consecutive neighbour
that SHARES the band, narrowing the price band to the common intersection and extending the left edge back.
This script measures, on the FROZEN strategy trades:
  (A) MERGE FREQUENCY  - how often the last-completed 4h bucket shares its buy/sell band with >=1 neighbour,
                         how many candles get merged, and how much the band narrows.
  (B) CLASSIFICATION   - how many trades flip in/out of the zone between non-merged and merged (two-sided band
                         membership: buy price within [low, vq_lo] merged-> [blo, bhi]; sell within [vq_hi, high]).
  (C) COMPOSITION      - win%/net/total for IN-ZONE under each definition, so we see if the merge changes the edge.
Also prints the original one-sided filter (price <= wick top) for continuity with pivot_4hwick.py.
Run: python study/pivot_4hzone_merge.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; LOOKBACK = 10   # match terminal cb[-10:]


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
    """dedup 4h buckets by end_time; return end_time, buyer-wick-top (vq_lo), seller-wick-bottom (vq_hi),
    candle low, candle high — all per completed bucket, chronological."""
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


def merged_bands(vlo, vhi, lw, hg):
    """per 4h-bucket index i: merge back from i through overlapping neighbours (intersection), same rule as the
    terminal _merge_4h_zones. Returns per-index (blo, bhi, bspan, slo, shi, sspan) where *span = #candles merged
    (1 = no neighbour shared)."""
    N = len(vlo); out = []
    for i in range(N):
        blo, bhi = lw[i], vlo[i]; bspan = 1                       # buy = [low, vq_lo]
        for k in range(i - 1, max(-1, i - LOOKBACK), -1):
            if max(blo, lw[k]) < min(bhi, vlo[k]):
                blo, bhi = max(blo, lw[k]), min(bhi, vlo[k]); bspan += 1
            else:
                break
        slo, shi = vhi[i], hg[i]; sspan = 1                       # sell = [vq_hi, high]
        for k in range(i - 1, max(-1, i - LOOKBACK), -1):
            if max(slo, vhi[k]) < min(shi, hg[k]):
                slo, shi = max(slo, vhi[k]), min(shi, hg[k]); sspan += 1
            else:
                break
        out.append((blo, bhi, bspan, slo, shi, sspan))
    return out


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
    bands = merged_bands(z_lo, z_hi, z_low, z_high)
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
        i4 = bisect.bisect_right(z_et, et[det]) - 1               # last COMPLETED 4h bucket at detection
        if i4 < 0:
            no4h += 1; continue
        px = float(cl[det]); blo, bhi, bspan, slo, shi, sspan = bands[i4]
        # membership (two-sided band) + original one-sided filter
        if buy:
            nm = z_low[i4] <= px <= z_lo[i4]; mg = blo <= px <= bhi
            nm1 = px <= z_lo[i4]; mg1 = px <= bhi; span = bspan
        else:
            nm = z_hi[i4] <= px <= z_high[i4]; mg = slo <= px <= shi
            nm1 = px >= z_hi[i4]; mg1 = px >= slo; span = sspan
        rows.append((walk(det, j0, buy), nm, mg, nm1, mg1, span))

    G = np.array([r[0] for r in rows]); NM = np.array([r[1] for r in rows]); MG = np.array([r[2] for r in rows])
    NM1 = np.array([r[3] for r in rows]); MG1 = np.array([r[4] for r in rows]); SPAN = np.array([r[5] for r in rows])
    d = 1000.0 / 100.0

    print("PIVOT-4HZONE  merged vs non-merged neighbour zone  (%d trades, %d excluded: no 4h bucket yet)\n"
          % (len(rows), no4h))

    # (A) merge frequency on the buckets actually seen at trade time
    shared = int(np.sum(SPAN > 1))
    print("  (A) MERGE FREQUENCY (last-completed 4h bucket at each trade's detection):")
    print("      shared with >=1 neighbour : %d / %d trades (%.1f%%)  |  avg candles merged %.2f  |  max %d"
          % (shared, len(rows), 100.0 * shared / max(1, len(rows)), SPAN.mean(), int(SPAN.max())))

    def comp(tag, m):
        a = G[m]
        if not len(a):
            print("      %-13s n=0" % tag); return
        w = a[a > 0]
        print("      %-13s n=%-3d | win %5.1f%% | net %+.3f%% | TOTAL %+.2f%% ($%+.0f)"
              % (tag, len(a), 100.0 * np.mean(a > 0), a.mean() - FEE, (a - FEE).sum(), (a - FEE).sum() * d))

    # (B) classification flips (two-sided band membership)
    flip_out = int(np.sum(NM & ~MG))          # was in-zone non-merged, now out under merge (band narrowed)
    flip_in = int(np.sum(~NM & MG))           # was out, now in
    print("\n  (B) CLASSIFICATION — two-sided band membership (buy in [low..vq_lo], sell in [vq_hi..high]):")
    print("      IN non-merged %d | IN merged %d | agree %d | flipped OUT (band narrowed) %d | flipped IN %d"
          % (int(NM.sum()), int(MG.sum()), int(np.sum(NM == MG)), flip_out, flip_in))

    print("\n  (C) COMPOSITION by zone definition:")
    comp("ALL", np.ones(len(G), bool))
    comp("IN non-merged", NM); comp("IN merged", MG)
    comp("OUT non-merged", ~NM); comp("OUT merged", ~MG)
    if flip_out:
        comp("FLIPPED-OUT", NM & ~MG)          # the trades the merge newly excludes — are they losers or winners?

    print("\n  one-sided filter (price at/below buy wick top / at/above sell wick bottom) for continuity:")
    print("      IN non-merged %d | IN merged %d | flipped OUT %d | flipped IN %d"
          % (int(NM1.sum()), int(MG1.sum()), int(np.sum(NM1 & ~MG1)), int(np.sum(~NM1 & MG1))))
    comp("1s IN non-merged", NM1); comp("1s IN merged", MG1)


if __name__ == "__main__":
    main()
