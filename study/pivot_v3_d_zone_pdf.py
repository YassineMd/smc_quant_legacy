"""PIVOT V3 — TEST 3: D-entries ONLY, sliced by their POSITION in the NON-MERGED 4H zone. CAUSAL.

Entry = D bar close. Exit = V3 default frozen ZZTRAIL (no TP; 0.1% LL/HH stop; 0.05% HL/LH trail; +0.4%->+0.1%
lock; fee 0.10). Tier = FROZEN first-print aligned P2 spread @D (>80 / >63&<=80 / <=63). Zone = the D close vs the
last completed (non-merged) 4H candle: below buy area / buy area / body / sell area / above sell area.

Generates study/out/pivot_v3_d_zone.pdf: winners/breakeven/losers by 4H zone, by side (Buy D / Sell D), by tier,
and the full Side x Tier x Zone cross-tab. Run: python study/pivot_v3_d_zone_pdf.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; SL = 0.003
SL_PAD = 0.001; TRAIL = 0.0005; ARM = 0.0040; LOCK = 0.0010; BE = 0.05; LW = config.LIVE_PANEL_WINDOW
LOCK_LAG = LW // 2; WBACK = 100; MIN_CYC = 4              # HMS: last-3-locked cycles, window 100 before D, noise<4 merged
ZONES = ["below buy area", "buy area", "body", "sell area", "above sell area"]
ZMAP = {"beyond-down": "below buy area", "inzone-buy": "buy area", "body": "body",
        "inzone-sell": "sell area", "beyond-up": "above sell area"}
TIERS = ["cyan/orange", "red/green", "hollow"]


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


def build_records():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh_c = causal_share(eb, er_, LW)                                     # FIRST-PRINT (frozen) tier
    e_sh = np.asarray(R.rolling_share(eb, er_, LW), float)                 # CENTERED — HMS reads its LOCKED cycles
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
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
        return cyc

    def hms_favours(edge, buy):
        cyc = cycles_from(max(0, edge - WBACK), edge)
        locked = [c for c in cyc if c[1] < edge - LOCK_LAG]
        if not locked:
            return None
        l3 = locked[-3:]; s0 = l3[0][0]; s1 = l3[-1][1]
        return (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5) == buy

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
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        buy = s == "long"; z = zone_at(det)
        if z is None:
            continue
        rows.append(dict(side="Buy D" if buy else "Sell D", tier=tier_of(det, buy),
                         zone=z, net=walk(det, buy) - FEE, fav=hms_favours(det, buy)))
    return rows


# ---------------------------------------------------------------- outcome aggregation
def outcome(nets):
    a = np.asarray(nets, float)
    n = len(a)
    if n == 0:
        return dict(n=0, w=0, b=0, l=0, wp=0.0, bp=0.0, lp=0.0, mean=0.0, tot=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    return dict(n=n, w=w, b=b, l=l, wp=100.0 * w / n, bp=100.0 * b / n, lp=100.0 * l / n,
                mean=float(a.mean()), tot=float(a.sum()) * 10.0)


def build_pdf(rows, path, filt=""):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=15, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#555555"), spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=10.5, spaceBefore=8, spaceAfter=3,
                        textColor=colors.HexColor("#1a1a1a"))

    def cell_color(mean):
        if mean > BE:
            return colors.HexColor("#e7f6ec")
        if mean < -BE:
            return colors.HexColor("#fbe9e9")
        return colors.HexColor("#f4f4f4")

    HEAD = ["group", "n", "W", "BE", "L", "W%", "BE%", "L%", "net/trade", "total $"]

    def otable(title, pairs):
        """pairs = [(label, outcome_dict), ...]"""
        data = [HEAD]; styles = []
        for i, (lab, o) in enumerate(pairs, start=1):
            data.append([lab, o["n"], o["w"], o["b"], o["l"],
                         "%.0f%%" % o["wp"], "%.0f%%" % o["bp"], "%.0f%%" % o["lp"],
                         "%+.3f%%" % o["mean"] if o["n"] else "-",
                         "%+.0f" % o["tot"] if o["n"] else "-"])
            if o["n"]:
                styles.append(("BACKGROUND", (8, i), (9, i), cell_color(o["mean"])))
        t = Table(data, colWidths=[42*mm, 9*mm, 8*mm, 8*mm, 8*mm, 12*mm, 12*mm, 12*mm, 20*mm, 16*mm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 7.4), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (0, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8),
        ] + styles))
        return [Paragraph(title, h2), t, Spacer(1, 3)]

    def sel(pred):
        return outcome([r["net"] for r in rows if pred(r)])

    story = [Paragraph("PIVOT V3 — Test 3: D-entries by 4H zone position" + (" (%s)" % filt if filt else ""), h1)]
    if filt:
        story.append(Paragraph("Filter: only D's taken when the harmonic-mean-spread (HMS) backdrop is IN FAVOUR "
                               "of the trade — net side of the last 3 LOCKED cycles (window 100 before D) agrees "
                               "(green&rarr;long / red&rarr;short). Cells are small — read as structure, not size.",
                               ParagraphStyle("fn", parent=ss["Normal"], fontSize=8,
                                              textColor=colors.HexColor("#8a4b00"), spaceAfter=2)))
    story += [Paragraph("Causal. Enter at D · frozen first-print tier · V3 default ZZTRAIL exit (no TP, 0.1% stop, "
                       "0.05% trail, +0.4%&rarr;+0.1% lock, fee 0.10) · zone = D close vs last non-merged 4H candle · "
                       "three-outcome on NET (winner &gt;+0.05% / breakeven |&middot;|&le;0.05% / loser &lt;-0.05%).", sub)]

    story += otable("Overall", [("ALL D-entries", sel(lambda r: True))])
    story += otable("By 4H zone position (all D's)",
                    [(z, sel(lambda r, z=z: r["zone"] == z)) for z in ZONES])
    story += otable("By side", [(s, sel(lambda r, s=s: r["side"] == s)) for s in ("Buy D", "Sell D")])
    story += otable("By D tier", [(t, sel(lambda r, t=t: r["tier"] == t)) for t in TIERS])
    story += otable("Zone x Side",
                    [("%s | %s" % (z, s), sel(lambda r, z=z, s=s: r["zone"] == z and r["side"] == s))
                     for z in ZONES for s in ("Buy D", "Sell D")
                     if any(r["zone"] == z and r["side"] == s for r in rows)])
    story += otable("Zone x Tier",
                    [("%s | %s" % (z, t), sel(lambda r, z=z, t=t: r["zone"] == z and r["tier"] == t))
                     for z in ZONES for t in TIERS
                     if any(r["zone"] == z and r["tier"] == t for r in rows)])
    for t in TIERS:                                          # FULL — grouped BY D TIER (Buy D then Sell D, by zone)
        story += otable("FULL — %s  (Buy D, then Sell D, across zones)" % t,
                        [("%s | %s" % (s, z), sel(lambda r, s=s, t=t, z=z:
                          r["side"] == s and r["tier"] == t and r["zone"] == z))
                         for s in ("Buy D", "Sell D") for z in ZONES
                         if any(r["side"] == s and r["tier"] == t and r["zone"] == z for r in rows)])

    SimpleDocTemplate(path, pagesize=A4, topMargin=12*mm, bottomMargin=10*mm,
                      leftMargin=12*mm, rightMargin=12*mm).build(story)


def main():
    rows = build_records()
    outp = os.path.join(REPO, "study", "out"); os.makedirs(outp, exist_ok=True)
    pdf_all = os.path.join(outp, "pivot_v3_d_zone.pdf")
    pdf_hms = os.path.join(outp, "pivot_v3_d_zone_hms.pdf")
    build_pdf(rows, pdf_all)
    fav = [r for r in rows if r["fav"] is True]
    build_pdf(fav, pdf_hms, filt="HMS-favourable only")
    print("PIVOT V3 — Test 3 by 4H zone | all D's=%d | HMS-favourable=%d\n" % (len(rows), len(fav)))
    for tag, subset in (("ALL D's", rows), ("HMS-FAVOURABLE", fav)):
        print("  [%s]  %-16s |  n | W/BE/L | net/tr | tot$" % (tag, "zone"))
        for z in ZONES:
            o = outcome([r["net"] for r in subset if r["zone"] == z])
            print("     %-16s | %2d | %d/%d/%d | %+.3f%% | %+.0f" % (z, o["n"], o["w"], o["b"], o["l"], o["mean"], o["tot"]))
        print()
    print("PDFs -> %s\n        %s" % (pdf_all, pdf_hms))


if __name__ == "__main__":
    main()
