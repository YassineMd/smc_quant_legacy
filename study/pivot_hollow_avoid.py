"""HOLLOW — where to AVOID, in plain terms: split by SIDE (Buy D / Sell D), then by WHERE D PRINTED (absolute 4h
zone) and WHERE THE FILL PRINTED (E-held/E2 absolute zone). Non-merged 4h wick. Zones: Beyond-down / Buy zone
(buyer wick) / Body / Sell zone (seller wick) / Beyond-up. Reports per-side marginals (by D-zone and by fill-zone)
and the joint 'D-zone -> fill-zone' cells, sorted WORST-first, flagging AVOID (net<=0 or loss>=50%, n>=3), then a
consolidated AVOID list. Three-outcome on NET. Frozen entries+exit. Run: python study/pivot_hollow_avoid.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05
ZORDER = ["beyond-down", "inzone-buy", "body", "inzone-sell", "beyond-up"]
ZL = {"beyond-down": "Beyond-down", "inzone-buy": "Buy zone", "body": "Body",
      "inzone-sell": "Sell zone", "beyond-up": "Beyond-up"}


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


def zone5(px, low, vlo, vhi, high):
    if px < low:
        return "beyond-down"
    if px <= vlo:
        return "inzone-buy"
    if px < vhi:
        return "body"
    if px <= high:
        return "inzone-sell"
    return "beyond-up"


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

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        if i4 < 0:
            return None
        return zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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
        if tier != "hollow":
            continue
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = ent if e_held else None
        if not e_held:
            te = float(et[ent])
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        zD = zone_at(det); zE = zone_at(j0)
        if zD is None or zE is None:
            continue
        rows.append(dict(g=walk(det, j0, buy), buy=buy, zD=zD, zE=zE))

    G = np.array([r["g"] for r in rows]) - FEE
    ZD = np.array([r["zD"] for r in rows]); ZE = np.array([r["zE"] for r in rows]); BUY = np.array([r["buy"] for r in rows])

    def st_(mask):
        a = G[np.array(mask, bool)]; nn = len(a)
        if nn == 0:
            return None
        return dict(n=nn, w=int((a > BE).sum()), l=int((a < -BE).sum()), be=int((np.abs(a) <= BE).sum()),
                    wp=100.0 * (a > BE).mean(), lp=100.0 * (a < -BE).mean(), tot=a.sum() * 10.0, mean=a.mean())

    def show(tag, s):
        if s is None:
            print("    %-34s n=0" % tag); return
        flag = "  <<< AVOID" if (s["n"] >= 3 and (s["tot"] <= 0 or s["lp"] >= 50.0)) else ""
        print("    %-34s n=%-2d | W %5.1f%% | BE %2d | L %5.1f%% | net %+.3f%% | $%+.0f%s"
              % (tag, s["n"], s["wp"], s["be"], s["lp"], s["mean"], s["tot"], flag))

    print("HOLLOW — where to AVOID  (%d hollow trades; AVOID = n>=3 and net<=0 or loss>=50%%)\n" % len(rows))
    show("ALL hollow", st_(np.ones(len(rows), bool)))

    for side, bb, dz, ez in (("BUY D (long)", True, "buy zone", "buy wick"), ("SELL D (short)", False, "sell zone", "sell wick")):
        sm = BUY == bb
        print("\n== %s ==  (own wick = %s)" % (side, dz))
        print("  by WHERE D PRINTED:")
        for z in ZORDER:
            show(ZL[z], st_(sm & (ZD == z)))
        print("  by WHERE THE FILL PRINTED (E-held/E2):")
        for z in ZORDER:
            show(ZL[z], st_(sm & (ZE == z)))

    print("\n== JOINT 'D printed -> fill printed' cells (n>=3), WORST first ==")
    cells = []
    for bb, sd in ((True, "Buy D"), (False, "Sell D")):
        for zd in ZORDER:
            for ze in ZORDER:
                m = (BUY == bb) & (ZD == zd) & (ZE == ze); s = st_(m)
                if s and s["n"] >= 3:
                    cells.append((s["tot"], sd, zd, ze, s))
    for tot, sd, zd, ze, s in sorted(cells, key=lambda x: x[0]):
        show("%s: D in %-11s -> fill in %-11s" % (sd, ZL[zd], ZL[ze]), s)

    print("\n== AVOID LIST (net-negative hollow combos, n>=3) ==")
    bad = [(tot, sd, zd, ze, s) for tot, sd, zd, ze, s in cells if s["tot"] <= 0]
    tot_lost = sum(t for t, *_ in bad)
    for tot, sd, zd, ze, s in sorted(bad, key=lambda x: x[0]):
        print("   %-7s D in %-11s -> fill in %-11s : n=%-2d  L %.0f%%  $%+.0f"
              % (sd, ZL[zd], ZL[ze], s["n"], s["lp"], s["tot"]))
    print("   --> avoiding these %d combos skips %d hollow trades and removes $%+.0f of P&L"
          % (len(bad), sum(s["n"] for *_, s in bad), tot_lost))


if __name__ == "__main__":
    main()
