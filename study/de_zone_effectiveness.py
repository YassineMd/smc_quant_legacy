"""PIVOT V3 — where do D and E entries perform best, by zone? CAUSAL, live-simulated.

For every fired D (enter-at-D) and its New-E (enter-at-E), split BUY/SELL and FADED/NON-FADED, this classifies the
entry THREE ways and measures the causal V3-ZZTRAIL net:

  1. CURRENT ZONE  — entry close vs the last COMPLETED 4h bucket's wick zones (5-zone: below-buy / buy / body /
     sell / above-sell), exactly the tier zone the strategy uses.
  2. LAST 4h VP    — entry close vs that same completed 4h bucket's VOLUME PROFILE: above VAH / upper-VA (median..
     VAH) / lower-VA (VAL..median) / below VAL.  (VAL/VAH = 70% value area, median = q50.)
  3. FORMING 4h VP — entry close vs the CURRENT non-100%-filled 4h bucket's partial VP, rebuilt AS-OF the entry bar
     from the 1m footprints since the last 4h close (same 4-bin scheme).

Everything is read the way it is LIVE at the entry bar: tier = frozen FIRST-PRINT eff-agg at D; zones read the 4h
bucket completed before the bar + the bar's own close; the forming VP uses only 1m data up to the bar. The exit
(V3 ZZTRAIL) is the only forward-looking part. Three-outcome NET after 0.10 fee. Run: python study/de_zone_effectiveness.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402
import pivot_v3_de_zone_pdf as B                           # reuse the causal loaders/helpers  # noqa: E402

FEE = B.FEE; P2D_HI = B.P2D_HI; P2D_VHI = B.P2D_VHI
SL = B.SL; SL_PAD = B.SL_PAD; TRAIL = B.TRAIL; ARM = B.ARM; LOCK = B.LOCK; BE = B.BE
LW = B.LW; LOCK_LAG = B.LOCK_LAG; WBACK = B.WBACK; MIN_CYC = B.MIN_CYC; E_WIN = B.E_WIN

# terminal V3 take-rules (raw zone5 names), so faded/non-faded matches the live overlay exactly
V3_TAKE = {("long", "inzone-buy"), ("long", "beyond-up"), ("short", "inzone-sell"), ("short", "beyond-down")}
E_TAKE_ANY = {("long", "inzone-buy", "body"), ("short", "inzone-sell", "body"),
              ("long", "beyond-down", "inzone-buy"), ("short", "beyond-up", "inzone-sell")}
E_TAKE_CYAN = {("long", "body", "inzone-sell"), ("short", "body", "inzone-buy")}


def vp_bin(px, val, med, vah):
    """4-bin volume-profile position (NaN VA -> None)."""
    if not (val == val and vah == vah and med == med and vah > val):
        return None
    if px > vah:
        return "above VAH"
    if px > med:
        return "upper VA"
    if px >= val:
        return "lower VA"
    return "below VAL"


VP_BINS = ["above VAH", "upper VA", "lower VA", "below VAL"]
Z5_BINS = ["beyond-down", "inzone-buy", "body", "inzone-sell", "beyond-up"]
Z5_NAME = {"beyond-down": "below buy area", "inzone-buy": "buy area", "body": "body",
           "inzone-sell": "sell area", "beyond-up": "above sell area"}


def load_4h_vp():
    """Per completed 4h bucket: (et, low, vlo=q25, vhi=q75, high, val, med, vah)."""
    import glob, json, sqlite3
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        for (x,) in con.execute("SELECT data FROM closed_buckets WHERE tf='4h' ORDER BY id"):
            b = json.loads(x)
            if b.get("levels"):
                by[float(b["end_time"])] = b
        con.close()
    b4 = [by[k] for k in sorted(by)]
    et = [float(b["end_time"]) for b in b4]
    low = []; vlo = []; vhi = []; high = []; val = []; med = []; vah = []; tv = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); va = bar_quantiles.value_area(b["levels"])
        low.append(float(b["low"])); high.append(float(b["high"]))
        vlo.append(float(q[0])); med.append(float(q[1])); vhi.append(float(q[2]))
        val.append(float(va[0])); vah.append(float(va[1]))
        tv.append(float(b.get("target_vol", 0.0)))
    return (np.array(et), np.array(low), np.array(vlo), np.array(vhi), np.array(high),
            np.array(val), np.array(med), np.array(vah), np.array(tv))


def load_1m_ids():
    """Same merge as B.load_1m but also returns the GLOBAL 1m Idx (meta.total_closed_1m base) per bar."""
    import glob, json, sqlite3
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    ks = sorted(by)
    return [by[b] for b in ks], ks


def build():
    raws, ids = load_1m_ids(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(eb, er_, LW), float)       # LOCKED (read at j-LOCK_LAG -> causal)
    e_sh_c = B.causal_share(eb, er_, LW)                          # FIRST-PRINT (causal) -> tier
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks])
    z_et, z_low, z_vlo, z_vhi, z_high, z_val, z_med, z_vah, z_tv = load_4h_vp()

    def z4_idx(bar):
        return bisect.bisect_right(z_et, et[bar]) - 1           # last COMPLETED 4h bucket as-of the bar (causal)

    def zone5_at(bar):
        i4 = z4_idx(bar)
        return None if i4 < 0 else B.zone5(float(cl[bar]), z_low[i4], z_vlo[i4], z_vhi[i4], z_high[i4])

    def vpfill_at(bar):
        i4 = z4_idx(bar)
        return None if i4 < 0 else vp_bin(float(cl[bar]), z_val[i4], z_med[i4], z_vah[i4])

    def forming(bar):
        """Reconstruct the CURRENT non-100%-filled 4h bucket AS-OF `bar` from the 1m footprints since the last 4h
        close (causal). Returns dict(low, high, vlo, vhi, val, med, vah, cv, tv) or None."""
        i4 = z4_idx(bar); start_t = z_et[i4] if i4 >= 0 else -1.0
        j0 = bisect.bisect_right(et, start_t)
        if j0 > bar:
            return None
        agg = {}; flo = 1e18; fhi = -1e18; cv = 0.0
        for j in range(j0, bar + 1):
            flo = min(flo, float(lo[j])); fhi = max(fhi, float(hi[j]))
            cv += float(raws[j].get("curr_vol", 0.0)) or (float(raws[j].get("buy_vol", 0.0)) + float(raws[j].get("sell_vol", 0.0)))
            for ps, vv in (raws[j].get("levels") or {}).items():
                a = agg.get(ps)
                if a is None:
                    a = [0.0, 0.0]; agg[ps] = a
                a[0] += float(vv.get("b", 0.0)); a[1] += float(vv.get("s", 0.0))
        if len(agg) < 2 or fhi <= flo:
            return None
        lv = {p: {"b": v[0], "s": v[1]} for p, v in agg.items()}
        q = bar_quantiles.vq(lv); va = bar_quantiles.value_area(lv)
        return dict(low=flo, high=fhi, vlo=float(q[0]), vhi=float(q[2]), val=float(va[0]), med=float(q[1]),
                    vah=float(va[1]), cv=cv, tv=(z_tv[i4] if i4 >= 0 else 0.0))

    def vpform_at(bar):
        fm = forming(bar)
        return None if fm is None else vp_bin(float(cl[bar]), fm["val"], fm["med"], fm["vah"])

    def mae_mfe(det, buy):
        """1h-window excursions as %-from-entry, signed to the position: LOWER reach + HIGHER reach. For a BUY the
        lower reach is adverse (negative) and the higher reach favourable (positive)."""
        entry = float(cl[det]); t_end = et[det] + 3600.0; mn = entry; mx = entry
        for j in range(det + 1, n):
            if et[j] > t_end:
                break
            mn = min(mn, float(lo[j])); mx = max(mx, float(hi[j]))
        return (mn - entry) / entry * 100.0, (mx - entry) / entry * 100.0

    # --- swing ZigZag (for the ZZTRAIL stop/trail), causal ---
    sw = B.zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
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

    def last(arr, eb_, label):
        r = None
        for c, pb, p, lab in arr:
            if pb >= eb_:
                break
            if lab == label:
                r = p
        return r

    def walk(eb_, buy):
        entry = float(cl[eb_])
        if buy:
            s0 = last(lows, eb_, "LL"); s0 = s0 * (1 - SL_PAD) if s0 else entry * (1 - SL)
            trail = sorted((cb, p * (1 - TRAIL)) for cb, pb, p, lab in lows if lab == "HL" and pb > eb_)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            s0 = last(highs, eb_, "HH"); s0 = s0 * (1 + SL_PAD) if s0 else entry * (1 + SL)
            trail = sorted((cb, p * (1 + TRAIL)) for cb, pb, p, lab in highs if lab == "LH" and pb > eb_)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        exitlvl = s0; tp = 0; armed = False
        for j in range(eb_ + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                exitlvl = max(exitlvl, trail[tp][1]) if buy else min(exitlvl, trail[tp][1]); tp += 1
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
        return "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")

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
            cyc[si][2] = not cyc[si][2]; merged = [cyc[0]]
            for c in cyc[1:]:
                if c[2] == merged[-1][2]:
                    merged[-1][1] = c[1]
                else:
                    merged.append(c)
            cyc = merged
        return np.asarray([c[1] for c in cyc]), cyc

    def find_E(det, buy):
        ends, cyc = cycles_from(max(0, det - WBACK), n - 1)
        t0 = et[det]; sg = 1.0 if buy else -1.0
        for j in range(det, n):
            if et[j] > t0 + E_WIN:
                break
            jl = j - LOCK_LAG
            if jl < 0 or sg * (2.0 * float(e_sh[jl]) - 1.0) * 100.0 < 15.0:
                continue
            m = int(np.searchsorted(ends, j - LOCK_LAG, side="left"))
            if m == 0:
                continue
            l3 = cyc[max(0, m - 2):m]; s0 = l3[0][0]; s1 = l3[-1][1]
            if (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5) != buy:
                continue
            ci = int(np.searchsorted(ends, j, side="left")); cs = cyc[min(ci, len(cyc) - 1)][0]
            if (float(np.mean(e_sh_c[cs:j + 1])) >= 0.5) == buy:
                return j
        return None

    def e_ok(buy, tier, dz, ez):
        s = "long" if buy else "short"
        return ((s, dz, ez) in E_TAKE_ANY) or (tier == "cyan" and (s, dz, ez) in E_TAKE_CYAN)

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; usedE = {"long": set(), "short": set()}; recs = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        buy = s == "long"; dz5 = zone5_at(det)
        if dz5 is None:
            continue
        tier = tier_of(det, buy)
        step3 = (s, dz5) in V3_TAKE and tier == "cyan"
        # New-E for the non-step3 D's (Path B). e_valid = a DISTINCT (non-dedup) New-E fired within 4h of the D;
        # e_recorded (non-faded) = it is one of the 6 V3 E-entry combos (E_TAKE_ANY any-tier + E_TAKE_CYAN cyan-only).
        e0 = None if step3 else find_E(det, buy)
        ez5 = None; e_recorded = False; e_valid = False
        if e0 is not None and e0 > det and e0 not in usedE[s]:
            usedE[s].add(e0); ez5 = zone5_at(e0); e_valid = True
            e_recorded = (ez5 is not None) and e_ok(buy, tier, dz5, ez5)
        d_nonfaded = step3 or e_recorded          # bright D badge (Path A or a recorded-combo E)
        fm = forming(det); _mae, _mfe = mae_mfe(det, buy)
        fme = forming(e0) if e_valid else None; e_m = mae_mfe(e0, buy) if e_valid else (None, None)
        recs.append(dict(
            side=("Buy" if buy else "Sell"), buy=buy, idx=ids[det], det=det,
            # D entry (enter at the D)
            d_tier=tier, d_step3=step3,               # V3 D-entry = cyan/orange tier + Step-3 directional zone
            d_net=walk(det, buy) - FEE, d_nonfaded=d_nonfaded,
            d_z5=dz5, d_vpfill=vpfill_at(det),
            d_zform=(B.zone5(float(cl[det]), fm["low"], fm["vlo"], fm["vhi"], fm["high"]) if fm else None),
            d_vpform=(vp_bin(float(cl[det]), fm["val"], fm["med"], fm["vah"]) if fm else None),
            d_fill=(min(100.0, 100.0 * fm["cv"] / fm["tv"]) if (fm and fm["tv"] > 0) else None),
            d_mae=_mae, d_mfe=_mfe,
            # E entry (enter at the E) — same fields as the D, each read AS-OF the E bar. e_valid gates them.
            e_tier=tier, e_idx=(ids[e0] if e_valid else None), e_det=(e0 if e_valid else None),
            e_net=(walk(e0, buy) - FEE) if e_valid else None,
            e_nonfaded=e_recorded,
            e_z5=ez5, e_vpfill=(vpfill_at(e0) if e_valid else None),
            e_zform=(B.zone5(float(cl[e0]), fme["low"], fme["vlo"], fme["vhi"], fme["high"]) if fme else None),
            e_vpform=(vp_bin(float(cl[e0]), fme["val"], fme["med"], fme["vah"]) if fme else None),
            e_fill=(min(100.0, 100.0 * fme["cv"] / fme["tv"]) if (fme and fme["tv"] > 0) else None),
            e_mae=e_m[0], e_mfe=e_m[1]))
    return recs


def outc(nets):
    a = np.asarray([x for x in nets if x is not None], float); m = len(a)
    if m == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    return dict(n=m, w=w, b=b, l=l, mean=float(a.mean()))


def report(recs):
    systems = [("CURRENT ZONE (4h wick 5-zone)", "z5", Z5_BINS, Z5_NAME),
               ("LAST 4h FILLED — volume profile", "vpfill", VP_BINS, None),
               ("CURRENT FORMING 4h — volume profile", "vpform", VP_BINS, None)]
    fams = [("Buy",  True,  "d"), ("Sell", False, "d"), ("Buy",  True,  "e"), ("Sell", False, "e")]
    print("=" * 96)
    print("PIVOT V3 — D/E ENTRY EFFECTIVENESS BY ZONE  (causal; V3 ZZTRAIL exit; net after 0.10 fee)")
    print("W = net>+0.05% · BE = |net|<=0.05% · L = net<-0.05%.  n<8 shown but treat as underpowered.")
    print("=" * 96)
    for stitle, skey, bins, names in systems:
        print("\n" + "#" * 96 + "\n### %s\n" % stitle + "#" * 96)
        for fside, fbuy, de in fams:
            for faded_lbl, want_nf in (("NON-FADED", True), ("FADED", False)):
                sub = [r for r in recs if r["side"] == fside
                       and r["%s_net" % de] is not None
                       and r["%s_nonfaded" % de] == want_nf]
                if not sub:
                    continue
                print("\n  %s-%s  %s   (n=%d)" % (fside, de.upper(), faded_lbl,
                      len([r for r in sub if r["%s_%s" % (de, skey)] is not None])))
                print("    %-18s %4s %4s %4s %4s   %10s" % ("zone", "n", "W", "BE", "L", "net/trade"))
                rows = []
                for z in bins:
                    o = outc([r["%s_net" % de] for r in sub if r["%s_%s" % (de, skey)] == z])
                    if o["n"] == 0:
                        continue
                    lbl = names[z] if names else z
                    rows.append((z, lbl, o))
                    star = "  <==" if (o["n"] >= 8 and o["mean"] > BE) else ("  (x)" if (o["n"] >= 8 and o["mean"] < -BE) else "")
                    print("    %-18s %4d %4d %4d %4d   %+9.3f%%%s" % (lbl, o["n"], o["w"], o["b"], o["l"], o["mean"], star))
                allo = outc([r["%s_net" % de] for r in sub])
                print("    %-18s %4d %4d %4d %4d   %+9.3f%%" % ("ALL", allo["n"], allo["w"], allo["b"], allo["l"], allo["mean"]))


# --- OWN-RELATIVE zone maps (pool buy+sell: buy-in-buy-wick == sell-in-sell-wick, buy-below-value == sell-above) ---
Z5_OWN_BUY = {"beyond-down": "deep own", "inzone-buy": "own wick", "body": "body",
              "inzone-sell": "opp wick", "beyond-up": "deep opp"}
Z5_OWN_SELL = {"beyond-up": "deep own", "inzone-sell": "own wick", "body": "body",
               "inzone-buy": "opp wick", "beyond-down": "deep opp"}
VP_OWN_BUY = {"below VAL": "deep own", "lower VA": "own half", "upper VA": "opp half", "above VAH": "deep opp"}
VP_OWN_SELL = {"above VAH": "deep own", "upper VA": "own half", "lower VA": "opp half", "below VAL": "deep opp"}
Z5_OWN = ["deep own", "own wick", "body", "opp wick", "deep opp"]
VP_OWN = ["deep own", "own half", "opp half", "deep opp"]


def own(zone, buy, kind):
    if zone is None:
        return None
    if kind == "z5":
        return (Z5_OWN_BUY if buy else Z5_OWN_SELL).get(zone)
    return (VP_OWN_BUY if buy else VP_OWN_SELL).get(zone)


def report_pooled(recs):
    systems = [("CURRENT ZONE (own-relative wick)", "z5", Z5_OWN),
               ("LAST 4h FILLED VP (own-relative)", "vpfill", VP_OWN),
               ("FORMING 4h VP (own-relative)", "vpform", VP_OWN)]
    print("\n\n" + "=" * 96)
    print("POOLED buy+sell, OWN-RELATIVE zones  (own = the side you're trading INTO: buy low / sell high)")
    print("=" * 96)
    for de in ("d", "e"):
        for faded_lbl, want_nf in (("NON-FADED", True), ("FADED", False)):
            sub = [r for r in recs if r["%s_net" % de] is not None and r["%s_nonfaded" % de] == want_nf]
            if not sub:
                continue
            allo = outc([r["%s_net" % de] for r in sub])
            print("\n" + "-" * 96)
            print("  %s ENTRIES  %s   (n=%d, ALL net %+.3f%%, %.0f%% W)"
                  % (de.upper(), faded_lbl, allo["n"], allo["mean"], 100 * allo["w"] / max(1, allo["n"])))
            for stitle, skey, bins in systems:
                print("    %s:" % stitle)
                for z in bins:
                    o = outc([r["%s_net" % de] for r in sub if own(r["%s_%s" % (de, skey)], r["buy"], skey) == z])
                    if o["n"] == 0:
                        continue
                    star = "  <==" if (o["n"] >= 10 and o["mean"] > BE) else ("  (x)" if (o["n"] >= 10 and o["mean"] < -BE) else "")
                    print("        %-10s  n=%-3d  W%d/BE%d/L%d   %+8.3f%%%s" % (z, o["n"], o["w"], o["b"], o["l"], o["mean"], star))


if __name__ == "__main__":
    import pickle
    _pk = os.path.join(REPO, "study", "out", "de_zone_recs.pkl")
    if os.path.exists(_pk):
        with open(_pk, "rb") as _f:
            recs = pickle.load(_f)
    else:
        recs = build()
        os.makedirs(os.path.dirname(_pk), exist_ok=True)
        with open(_pk, "wb") as _f:
            pickle.dump(recs, _f)
    report(recs)
    report_pooled(recs)
