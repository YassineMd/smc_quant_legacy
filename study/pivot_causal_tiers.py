"""v2 on CAUSAL (live-tradeable) tape, split by the D-fill tier — where the tier is the CAUSAL (unlocked /
as-first-printed) tier the D actually showed live, since that is what routes the entry and the v2 filter live.
Everything (tier / E-held / E2) reads the causal (left-clamped) eff-agg; the fires stay locked/causal. Reports,
per tier (hollow / cyan-orange / red-green): three-outcome (winner net>+0.05%% / breakeven |.|<=0.05%% /
loser<-0.05%%) for the v1 causal book (all trades) and the v2 causal book (zone filter + hollow avoid).
Run: python study/pivot_causal_tiers.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05; LW = config.LIVE_PANEL_WINDOW
AVOID = {("buy", "inzone-sell", "body"), ("sell", "inzone-sell", "inzone-sell"),
         ("buy", "beyond-down", "beyond-down"), ("sell", "beyond-up", "beyond-up")}


def take_rule(zone, buy, tier):
    own_in = (zone == "inzone-buy") if buy else (zone == "inzone-sell")
    rev_in = (zone == "inzone-sell") if buy else (zone == "inzone-buy")
    own_bey = (zone == "beyond-down") if buy else (zone == "beyond-up")
    if tier == "hollow":
        return rev_in or (zone == "body") or own_bey
    if tier == "cyan/orange":
        return own_in
    return own_in or own_bey


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
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps); e_sh = np.asarray(e_sh, float)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh_c = causal_share(eb, er_, LW)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        return None if i4 < 0 else zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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

    def spr(esh, k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(esh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

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
        buy = s == "long"
        p2d = spr(e_sh_c, det, buy)                              # CAUSAL tier (first-print)
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        liv = [spr(e_sh_c, k, buy) for k in range(det, ent + 1)]
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
                if spr(e_sh_c, j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        zD = zone_at(det); zE = zone_at(j0)
        take = None
        if zD is not None and zE is not None:
            take = take_rule(zD, buy, tier) or take_rule(zE, buy, tier)
            if tier == "hollow" and (("buy" if buy else "sell"), zD, zE) in AVOID:
                take = False
        rows.append(dict(g=walk(det, j0, buy) - FEE, tier=tier, take=bool(take)))

    def show(tag, arr):
        a = np.asarray(arr)
        if not len(a):
            print("    %-14s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum()); nn = len(a)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if nn > 1 and a.std(ddof=1) > 0 else 0.0
        print("    %-14s n=%-3d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f) | t=%+.2f"
              % (tag, nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0, t))

    print("v2 CAUSAL (live-tradeable), split by the CAUSAL D-tier  (%d causal trades)\n" % len(rows))
    print("  v1 CAUSAL (all trades, no filter):")
    show("ALL", [r["g"] for r in rows])
    for t in ("hollow", "cyan/orange", "red/green"):
        show(t, [r["g"] for r in rows if r["tier"] == t])
    print("\n  v2 CAUSAL (zone filter + hollow avoid):")
    show("ALL", [r["g"] for r in rows if r["take"]])
    for t in ("hollow", "cyan/orange", "red/green"):
        show(t, [r["g"] for r in rows if r["take"] and r["tier"] == t])


if __name__ == "__main__":
    main()
