"""PIVOT V3 — D-ENTRY tests, CAUSAL ONLY. Common basis = the FROZEN first-print D tier.

Entry = the D bar close (enter at detection). Exit = the V3 default (frozen ZZTRAIL): no TP; initial SL 0.1% below
last LL (long)/above last HH (short); trail 0.05% beyond each new HL(long)/LH(short); +0.4% MFE -> lock stop +0.1%;
fee 0.10% netted. Tier = FROZEN non-locked (first-print) aligned P2 spread @D: >80 cyan/orange | >63&<=80 red/green
| <=63 hollow.

  TEST 1: every fired D-setup, entered at D.
  TEST 2: only when the HMS (harmonic-mean-spread box) is IN FAVOUR — net side of the last 3 LOCKED cycles as-of D
          (window starts 100 before D, noise runs <4 buckets merged) agrees with the trade (green->long / red->short).

CAUSAL: tier reads the first-print value @D; HMS reads only LOCKED cycles (settled >=7 buckets behind D); only the
exit walk uses forward price. Reports winners/breakeven/losers per D tier (three-outcome NET + t).
Run: python study/pivot_v3_d_entry.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; SL = 0.003
SL_PAD = 0.001; TRAIL = 0.0005; ARM = 0.0040; LOCK = 0.0010; BE = 0.05
LW = config.LIVE_PANEL_WINDOW; LOCK_LAG = LW // 2         # 7 — buckets for a P2 cycle to settle (lock)
WBACK = 100                                               # HMS window starts this many buckets before D
MIN_CYC = 4                                               # P2 cycles shorter than this (buckets) are NOISE -> merged


def causal_share(bull, bear, window):
    h = max(1, window) // 2
    b = np.asarray(bull, float); r = np.asarray(bear, float)
    B = np.concatenate([[0.0], np.cumsum(b)]); Rr = np.concatenate([[0.0], np.cumsum(r)])
    out = np.empty(len(b))
    for i in range(len(b)):
        lo = max(0, i - h); sb = B[i + 1] - B[lo]; sr = Rr[i + 1] - Rr[lo]; tot = sb + sr
        out[i] = sb / tot if tot > 0 else 0.5
    return out


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
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps); e_sh = np.asarray(e_sh, float)   # CENTERED (settled) — HMS reads its LOCKED cycles
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh_c = causal_share(eb, er_, LW)                                     # FIRST-PRINT (frozen) — the V3 tier
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])

    def cycles_from(w0, edge):
        """Cycles on centered e_sh over [w0, edge] (first truncated at w0), NOISE runs (<MIN_CYC) merged out."""
        cyc = []; i0 = w0; dom = e_sh[w0] >= 0.5
        for k in range(w0 + 1, edge + 1):
            dk = e_sh[k] >= 0.5
            if dk != dom:
                cyc.append([i0, k - 1, dom]); i0 = k; dom = dk
        cyc.append([i0, edge, dom])
        while len(cyc) > 1:
            si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
            if (cyc[si][1] - cyc[si][0] + 1) >= MIN_CYC:
                break
            cyc[si][2] = not cyc[si][2]
            merged = [cyc[0]]
            for c in cyc[1:]:
                if c[2] == merged[-1][2]:
                    merged[-1][1] = c[1]
                else:
                    merged.append(c)
            cyc = merged
        return cyc

    def hms_at_d(edge, buy):
        """As-of D, over the last 3 LOCKED cycles (window 100 before D): returns (favourable, HM@D).
        favourable = the net side of those cycles agrees with the trade. HM@D = the HARMONIC MEAN (not the spread)
        of the trade-ALIGNED share over that span, as a % — >50 favourable, <50 against, magnitude = strength.
        (None, None) if no locked cycle."""
        cyc = cycles_from(max(0, edge - WBACK), edge)
        locked = [c for c in cyc if c[1] < edge - LOCK_LAG]
        if not locked:
            return None, None
        l3 = locked[-3:]; s0 = l3[0][0]; s1 = l3[-1][1]
        seg = e_sh[s0:s1 + 1]
        fav = (float(np.mean(seg)) >= 0.5) == buy
        aligned = seg if buy else (1.0 - seg)                          # trade-aligned share (>0.5 favours the trade)
        av = aligned[aligned > 1e-6]
        hm = (len(av) / float(np.sum(1.0 / av))) * 100.0 if len(av) else 50.0
        return fav, hm

    # labelled ZigZag swings for the ZZTRAIL stop
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

    def last(arr, eb, label):
        r = None
        for c, pb, p, lab in arr:
            if pb >= eb:
                break
            if lab == label:
                r = p
        return r

    def walk(eb, buy):
        """V3 default exit off entry bar eb (entry-anchored structural SL + HL/LH trail + breakeven lock)."""
        entry = float(cl[eb])
        if buy:
            s0 = last(lows, eb, "LL"); s0 = s0 * (1 - SL_PAD) if s0 else entry * (1 - SL)
            trail = sorted((cb, p * (1 - TRAIL)) for cb, pb, p, lab in lows if lab == "HL" and pb > eb)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            s0 = last(highs, eb, "HH"); s0 = s0 * (1 + SL_PAD) if s0 else entry * (1 + SL)
            trail = sorted((cb, p * (1 + TRAIL)) for cb, pb, p, lab in highs if lab == "LH" and pb > eb)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        exitlvl = s0; tp = 0; armed = False
        for j in range(eb + 1, n):
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

    def tier_of(det, buy):
        p2d = (1.0 if buy else -1.0) * (2.0 * float(e_sh_c[det]) - 1.0) * 100.0   # FROZEN first-print, aligned
        return "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]        # one entry per fired D-run
        buy = s == "long"; fav, hm = hms_at_d(det, buy)
        rows.append(dict(g=walk(det, buy) - FEE, tier=tier_of(det, buy), fav=fav, hm=hm))

    def show(tag, arr):
        a = np.asarray(arr)
        if not len(a):
            print("    %-14s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum()); nn = len(a)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if nn > 1 and a.std(ddof=1) > 0 else 0.0
        print("    %-14s n=%-3d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f) | t=%+.2f"
              % (tag, nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0, t))

    print("PIVOT V3 — D-ENTRY (causal), frozen first-print tier | %d fired D-setups\n" % len(rows))
    print("TEST 1 — every D, entered at D:")
    show("ALL", [r["g"] for r in rows])
    for t in ("cyan/orange", "red/green", "hollow"):
        show(t, [r["g"] for r in rows if r["tier"] == t])
    fav = [r for r in rows if r["fav"] is True]
    print("\nTEST 2 — D entered ONLY when HMS is in favour  [%d of %d kept | %d against | %d no-locked-cycle]:"
          % (len(fav), len(rows), sum(1 for r in rows if r["fav"] is False), sum(1 for r in rows if r["fav"] is None)))
    show("ALL", [r["g"] for r in fav])
    for t in ("cyan/orange", "red/green", "hollow"):
        show(t, [r["g"] for r in fav if r["tier"] == t])

    def hm_report(tag, subset):
        sub = [r for r in subset if r["hm"] is not None]
        if not sub:
            print("  %s: n=0" % tag); return
        hms = np.array([r["hm"] for r in sub]); gs = np.array([r["g"] for r in sub])
        win = gs > BE; be = np.abs(gs) <= BE; los = gs < -BE
        print("\n  %s (n=%d) — HM@D = aligned harmonic mean %%  (>50 favours the trade):" % (tag, len(sub)))
        for lab, m in (("winners", win), ("breakeven", be), ("losers", los)):
            if m.any():
                print("     %-9s n=%-3d | HM@D mean %5.1f  median %5.1f  [p25 %4.1f  p75 %4.1f]"
                      % (lab, int(m.sum()), hms[m].mean(), float(np.median(hms[m])),
                         float(np.percentile(hms[m], 25)), float(np.percentile(hms[m], 75))))
        print("     corr(HM@D, net PnL) = %+.2f" % np.corrcoef(hms, gs)[0, 1])
        print("     HM@D bucket |  n  |  W%%  | BE%%  |  L%%  |    net")
        for lab2, mask in (("<40", hms < 40), ("40-50", (hms >= 40) & (hms < 50)),
                           ("50-60", (hms >= 50) & (hms < 60)), ("60-70", (hms >= 60) & (hms < 70)),
                           (">=70", hms >= 70)):
            a = gs[mask]
            if len(a):
                print("     %-10s | %3d | %4.0f | %4.0f | %4.0f | %+.3f%%"
                      % (lab2, len(a), (a > BE).mean() * 100, (np.abs(a) <= BE).mean() * 100,
                         (a < -BE).mean() * 100, a.mean()))

    print("\n" + "=" * 78)
    print("HM@D  —  correlation / threshold vs outcome  (Test-1 set: every D):")
    hm_report("ALL tiers", rows)
    hm_report("cyan/orange only", [r for r in rows if r["tier"] == "cyan/orange"])


if __name__ == "__main__":
    main()
