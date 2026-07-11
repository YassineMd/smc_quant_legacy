"""PIVOT V3 — where do D's and E's LAND in the 4H zone, by tier. CAUSAL.

Part A — every fired D (direct-D entry): D-tier x the D's own 4H-zone position (D close vs last non-merged 4H).
Part B — every NEW-E entry (on the non-Step-3 D's): D-tier x the E's OWN 4H-zone position (E close vs the 4H
         candle as-of the E bar) — so you can see where the entry actually lands vs where its D fired.

NEW E = first bar (<=4h from D) with aligned LOCKED P2 spread >= 15 AND HMS in favour (last 2 locked cycles,
window 100 before D, noise<4) AND the current forming HM cycle also in favour (causal). Tier = frozen first-print.
Exit = V3 default ZZTRAIL, fee 0.10, three-outcome NET. Output: study/out/pivot_v3_de_zone.pdf
Run: python study/pivot_v3_de_zone_pdf.py
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
ZONES = ["below buy area", "buy area", "body", "sell area", "above sell area"]
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
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    return dict(n=n, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()) * 10.0)


def build_records():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(eb, er_, LW), float)
    e_sh_c = causal_share(eb, er_, LW)
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
        return np.asarray([c[1] for c in cyc]), cyc

    def find_E(det, start, buy):
        ends, cyc = cycles_from(max(0, det - WBACK), n - 1)
        t0 = et[det]; sg = 1.0 if buy else -1.0
        for j in range(start, n):
            if et[j] > t0 + E_WIN:
                break
            jl = j - LOCK_LAG
            if jl < 0 or sg * (2.0 * float(e_sh[jl]) - 1.0) * 100.0 < 15.0:
                continue
            m = int(np.searchsorted(ends, j - LOCK_LAG, side="left"))
            if m == 0:
                continue
            l3 = cyc[max(0, m - 2):m]; s0 = l3[0][0]; s1 = l3[-1][1]       # last 2 locked cycles (HMS)
            if (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5) != buy:
                continue
            ci = int(np.searchsorted(ends, j, side="left"))
            cs = cyc[min(ci, len(cyc) - 1)][0]
            if (float(np.mean(e_sh_c[cs:j + 1])) >= 0.5) == buy:
                return j
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
        buy = s == "long"; dz = zone_at(det)
        if dz is None:
            continue
        tier = tier_of(det, buy); side = "Buy D" if buy else "Sell D"
        step3 = (tier == "cyan/orange") and ((side, dz) in V3OK)
        rec = dict(side=side, tier=tier, d_zone=dz, step3=step3,
                   d_net=walk(det, buy) - FEE, d_net_flip=walk(det, not buy) - FEE,   # direct-D + its FADE
                   e_zone=None, e_net=None, e_net_flip=None, same_candle=False,
                   d_time=float(et[det]), e_time=None)             # entry timestamps (for the forward-audit split)
        if not step3:
            e0 = find_E(det, det, buy)                             # first qualifying bar from the D bar
            rec["same_candle"] = (e0 == det)                       # confluence already ON the D bar -> filter the setup
            if e0 is not None and e0 > det and e0 not in usedE[s]: # valid E: after D AND not a dup (dedup one E/bar/side)
                usedE[s].add(e0)
                rec["e_zone"] = zone_at(e0); rec["e_net"] = walk(e0, buy) - FEE
                rec["e_net_flip"] = walk(e0, not buy) - FEE        # FADE test: take the OPPOSITE position (same exit, mirrored)
                rec["e_time"] = float(et[e0])
        rows.append(rec)
    return rows


def build_pdf(rows, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=15, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#555"), spaceAfter=5)
    hA = ParagraphStyle("hA", parent=ss["Heading1"], fontSize=12, spaceBefore=8, spaceAfter=2,
                        textColor=colors.HexColor("#0b0b0b"))
    hT = ParagraphStyle("hT", parent=ss["Heading2"], fontSize=10, spaceBefore=6, spaceAfter=2,
                        textColor=colors.HexColor("#333"))
    TCOL = {"cyan/orange": "#0684a6", "red/green": "#1f8f3a", "hollow": "#777"}

    def cell_bg(mean, nn):
        if nn == 0:
            return colors.HexColor("#ffffff")
        if mean > BE:
            return colors.HexColor("#e7f6ec")
        if mean < -BE:
            return colors.HexColor("#fbe9e9")
        return colors.HexColor("#f4f4f4")

    def tier_table(title, color, pairs, hdr0="side · 4H zone"):
        data = [[hdr0, "n", "W", "BE", "L", "net/trade", "total $"]]; styles = []
        for i, (z, o) in enumerate(pairs, start=1):
            data.append([z, o["n"], o["w"], o["b"], o["l"],
                         "%+.3f%%" % o["mean"] if o["n"] else "-", "%+.0f" % o["tot"] if o["n"] else "-"])
            styles.append(("BACKGROUND", (5, i), (6, i), cell_bg(o["mean"], o["n"])))
            if z.startswith("ALL"):                              # bold the subtotal row
                styles.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
                styles.append(("LINEABOVE", (0, i), (-1, i), 0.6, colors.HexColor("#999")))
        t = Table(data, colWidths=[52*mm, 10*mm, 10*mm, 10*mm, 10*mm, 22*mm, 18*mm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 7.6), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(color)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
        ] + styles))
        return [Paragraph(title, hT), t, Spacer(1, 2)]

    story = [Paragraph("PIVOT V3 — where D's and E's fire, by tier &times; 4H zone", h1),
             Paragraph("Causal. Tier = frozen first-print. Direct-D exit &amp; New-E exit both = V3 ZZTRAIL "
                       "(no TP, 0.1% stop, 0.05% trail, +0.4%&rarr;+0.1% lock, fee 0.10). Zone = close vs last "
                       "non-merged 4H candle. Three-outcome NET (W &gt;+0.05% / BE |&middot;|&le;0.05% / L &lt;-0.05%).", sub)]

    def side_zone_pairs(recs, netkey, zonekey):
        pairs = []
        for sd in ("Buy D", "Sell D"):
            ss = [r for r in recs if r["side"] == sd]
            for z in ZONES:
                o = outcome([r[netkey] for r in ss if r[zonekey] == z])
                if o["n"]:
                    pairs.append(("%s · %s" % (sd[:-2], z), o))
            pairs.append(("ALL %s" % sd[:-2], outcome([r[netkey] for r in ss])))
        return pairs

    ZAB = {"below buy area": "belowBuy", "buy area": "buyArea", "body": "body",
           "sell area": "sellArea", "above sell area": "aboveSell"}

    # PART A — D's by tier x SIDE x zone (direct-D, the D's OWN zone)
    story.append(Paragraph("Part A — where each D fired (D-tier &times; Buy/Sell &times; the D's 4H zone) · entered at D", hA))
    for t in TIERS:
        story += tier_table(t.upper(), TCOL[t], side_zone_pairs([r for r in rows if r["tier"] == t], "d_net", "d_zone"))

    # PART B — D-zone -> E-zone MIGRATION (D-tier x Buy/Sell x D's zone x E's zone), New-E outcome
    story.append(Paragraph("Part B — D-zone &rarr; E-zone migration (D-tier &times; Buy/Sell &times; D's 4H zone &times; E's 4H zone) · New-E", hA))
    ef = [r for r in rows if r["e_zone"] is not None]
    for t in TIERS:
        for sd in ("Buy D", "Sell D"):
            recs = [r for r in ef if r["tier"] == t and r["side"] == sd]
            if not recs:
                continue
            pairs = []
            for dz in ZONES:
                for ez in ZONES:
                    o = outcome([r["e_net"] for r in recs if r["d_zone"] == dz and r["e_zone"] == ez])
                    if o["n"]:
                        pairs.append(("%s -> %s" % (ZAB[dz], ZAB[ez]), o))
            pairs.append(("ALL %s E" % sd[:-2], outcome([r["e_net"] for r in recs])))
            story += tier_table("%s  —  %s E" % (t.upper(), sd[:-2]), TCOL[t], pairs, hdr0="D-zone -> E-zone")

    # PART 3 — E entries by tier x SIDE x the E's 4H zone (path B only; Step-3 direct-D / path A excluded)
    story.append(Paragraph("Part 3 — E entries by D-tier &times; Buy/Sell &times; the E's 4H zone "
                           "(path B only — Step-3 direct-D excluded)", hA))
    for t in TIERS:
        story += tier_table("%s  —  E entries" % t.upper(), TCOL[t],
                            side_zone_pairs([r for r in ef if r["tier"] == t], "e_net", "e_zone"),
                            hdr0="side · E 4H zone")

    SimpleDocTemplate(path, pagesize=A4, topMargin=11*mm, bottomMargin=9*mm,
                      leftMargin=12*mm, rightMargin=12*mm).build(story)


def main():
    rows = build_records()
    outp = os.path.join(REPO, "study", "out"); os.makedirs(outp, exist_ok=True)
    pdf = os.path.join(outp, "pivot_v3_de_zone.pdf")
    build_pdf(rows, pdf)
    ef = [r for r in rows if r["e_zone"] is not None]
    sc = [r for r in rows if r["same_candle"]]
    print("D's=%d | NEW-E fired (strictly after D)=%d | same-candle E's FILTERED=%d\n" % (len(rows), len(ef), len(sc)))
    print("  Same-candle E's filtered, by D tier x 4H zone:")
    for t in TIERS:
        for z in ZONES:
            cnt = sum(1 for r in sc if r["tier"] == t and r["d_zone"] == z)
            if cnt:
                print("    %-12s | %-16s | %d" % (t, z, cnt))
    print("\n  D-zone -> E-zone migration (New-E outcome, E strictly after D), non-empty cells:")
    for dz in ZONES:
        for ez in ZONES:
            o = outcome([r["e_net"] for r in ef if r["d_zone"] == dz and r["e_zone"] == ez])
            if o["n"]:
                print("    D:%-16s -> E:%-16s | n=%-2d | %d/%d/%d | net %+.3f%% | tot %+.0f"
                      % (dz, ez, o["n"], o["w"], o["b"], o["l"], o["mean"], o["tot"]))
    print("\nPDF -> %s" % pdf)


if __name__ == "__main__":
    main()
