"""PIVOT V3 — NEW E entry test. CAUSAL. Tier = FROZEN first-print. Exit = V3 default ZZTRAIL.

New E = first bar (<=4h from D) where the aligned LOCKED P2 eff-agg spread (settled badge, LOCK buckets back) >= 15
AND the HMS is IN FAVOUR (net side of the last 2 LOCKED cycles) AND the CURRENT (forming) HM cycle is ALSO in
favour (causal first-print net side of the cycle containing the bar). HMS window = 100 before D, noise<4 merged.

Reports the new-E book by tier, and on the V3 Step-3 setups (cyan/orange + directional 4H zone) vs the D-entry
baseline (+$61). Run: python study/pivot_v3_newE.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; SL = 0.003
SL_PAD = 0.001; TRAIL = 0.0005; ARM = 0.0040; LOCK = 0.0010; BE = 0.05
LW = config.LIVE_PANEL_WINDOW; LOCK_LAG = LW // 2; WBACK = 100; MIN_CYC = 4; E_WIN = 4 * 3600.0
ZMAP = {"beyond-down": "below buy area", "inzone-buy": "buy area", "body": "body",
        "inzone-sell": "sell area", "beyond-up": "above sell area"}
TIERS = ["cyan/orange", "red/green", "hollow"]
V3OK = {("Buy D", "buy area"), ("Sell D", "sell area"), ("Buy D", "above sell area"), ("Sell D", "below buy area")}


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


def outcome(nets):
    a = np.asarray(nets, float); n = len(a)
    if n == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(n)) if n > 1 and a.std(ddof=1) > 0 else 0.0
    return dict(n=n, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()) * 10.0, t=t)


def show(tag, o):
    if o["n"] == 0:
        print("    %-22s n=0" % tag); return
    print("    %-22s n=%-3d | W %2d (%4.0f%%) | BE %2d (%4.0f%%) | L %2d (%4.0f%%) | net %+.3f%% | TOT $%+.0f | t=%+.2f"
          % (tag, o["n"], o["w"], 100 * o["w"] / o["n"], o["b"], 100 * o["b"] / o["n"],
             o["l"], 100 * o["l"] / o["n"], o["mean"], o["tot"], o["t"]))


def main():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(eb, er_, LW), float)                 # centered — HMS locked cycles
    e_sh_c = causal_share(eb, er_, LW)                                     # first-print — tier
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    op = np.array([float(s.get("open", s.get("open_price", 0.0))) for s in snaps])
    poc = np.array([float(s.get("poc_price", 0.0)) for s in snaps])
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95                       # moving POC baseline (5% EMA), verbatim
    et = np.array([b.end_time for b in bks])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        return None if i4 < 0 else ZMAP[zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])]

    def cycles_from(w0, edge):
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
        return [c[1] for c in cyc], cyc                                    # ends, cycles

    def find_E(det, buy):
        """First bar (<=4h from D) that is a baseline reversal bar with HMS confirming. Returns (E_bar, wait) or None."""
        ends, cyc = cycles_from(max(0, det - WBACK), n - 1)                # cycles once per D (causal-left; future
        ends = np.asarray(ends)                                            # bars only touch cycles that stay unlocked)
        t0 = et[det]; sg = 1.0 if buy else -1.0
        for j in range(det, n):                                       # start at D to catch the same-candle case
            if et[j] > t0 + E_WIN:
                break
            jl = j - LOCK_LAG                                             # aligned LOCKED P2 spread (settled badge) >= 15
            if jl < 0 or sg * (2.0 * float(e_sh[jl]) - 1.0) * 100.0 < 15.0:
                continue
            m = int(np.searchsorted(ends, j - LOCK_LAG, side="left"))      # locked cycles as-of j
            if m == 0:
                continue
            l3 = cyc[max(0, m - 2):m]; s0 = l3[0][0]; s1 = l3[-1][1]       # last 2 locked cycles (HMS)
            if (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5) != buy:           # HMS in favour?
                continue
            ci = int(np.searchsorted(ends, j, side="left"))               # current (forming) cycle containing j
            cs = cyc[min(ci, len(cyc) - 1)][0]
            if (float(np.mean(e_sh_c[cs:j + 1])) >= 0.5) == buy:          # current HM also in favour (causal)
                return None if j == det else (j, j - det)                 # confluence ON the D bar -> filter the setup
        return None

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
        p2d = (1.0 if buy else -1.0) * (2.0 * float(e_sh_c[det]) - 1.0) * 100.0
        return "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []; usedE = {"long": set(), "short": set()}
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        buy = s == "long"; z = zone_at(det)
        if z is None:
            continue
        tier = tier_of(det, buy); side = "Buy D" if buy else "Sell D"
        step3 = (tier == "cyan/orange") and ((side, z) in V3OK)
        e = find_E(det, buy); e_bar = e[0] if e else None
        take_e = (not step3) and (e_bar is not None) and (e_bar not in usedE[s])   # dedup: one E per bar per side
        if take_e:
            usedE[s].add(e_bar)
        rows.append(dict(side=side, tier=tier, zone=z, step3=step3,
                         g_d=walk(det, buy) - FEE,                         # direct-D entry
                         g_e=(walk(e_bar, buy) - FEE) if take_e else None, # NEW-E entry (deduped)
                         filled=take_e, wait=(e[1] if e else None)))

    step3 = [r for r in rows if r["step3"]]
    other = [r for r in rows if not r["step3"]]
    ofill = [r for r in other if r["filled"]]; waits = [r["wait"] for r in ofill]
    print("PIVOT V3 — Step-3 D's enter at D DIRECTLY; ALL OTHER D's enter via the NEW E. CAUSAL.")
    print("  %d D-setups = %d Step-3 (direct D) + %d others | of others: %d NEW-E filled, %d no-trade | E wait med %s max %s\n"
          % (len(rows), len(step3), len(other), len(ofill), len(other) - len(ofill),
             ("%.0f" % np.median(waits)) if waits else "-", ("%d" % max(waits)) if waits else "-"))
    show("Step-3 DIRECT-D", outcome([r["g_d"] for r in step3]))
    show("Others NEW-E (filled)", outcome([r["g_e"] for r in ofill]))
    show("COMBINED V3 book", outcome([r["g_d"] for r in step3] + [r["g_e"] for r in ofill]))
    print("\n  Others' NEW-E book by tier:")
    for t in TIERS:
        show(t, outcome([r["g_e"] for r in ofill if r["tier"] == t]))
    ZONES = ["below buy area", "buy area", "body", "sell area", "above sell area"]
    print("\n  Others' NEW-E book by zone:")
    for z in ZONES:
        show(z, outcome([r["g_e"] for r in ofill if r["zone"] == z]))
    print("\n  Others' NEW-E book by TIER x ZONE (D tier + 4H zone of each E's D):")
    for t in TIERS:
        for z in ZONES:
            o = outcome([r["g_e"] for r in ofill if r["tier"] == t and r["zone"] == z])
            if o["n"]:
                show("%s | %s" % (t, z), o)
    print("\n  (ref) if the OTHERS were entered DIRECT-D instead (the thing NEW-E has to beat):")
    show("Others DIRECT-D", outcome([r["g_d"] for r in other]))


if __name__ == "__main__":
    main()
