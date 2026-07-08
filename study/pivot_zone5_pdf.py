"""Render the FULL pivot report — D-tier x 5-zone x position — into ONE PDF, under BOTH zone anchors:
  * D anchor      = zone of the DETECTION price (cl[det]) vs the 4h bucket at detection.
  * ENTRY anchor  = zone of the ACTUAL FILL price (cl[j0], E2 / E-held) vs the 4h bucket at entry.
Non-merged single-bucket wick (low<=vq_lo<=vq_hi<=high; GREEN buyer wick [low,vq_lo], RED seller wick [vq_hi,high]).
Zones bottom->top: Beyond down / Inzone buy / Body range / Inzone sell / Beyond up. D-tier = D-badge fill
(cyan/orange >80 | red/green 63-80 | hollow). Three-outcome on NET. Tier & position are D-intrinsic (same under
both anchors); only ZONE differs. Out: study/out/pivot_zone5_report.pdf   Run: python study/pivot_zone5_pdf.py
"""
import os, sys, glob, json, sqlite3, bisect, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

from reportlab.lib.pagesizes import A4, landscape          # noqa: E402
from reportlab.lib.units import cm                         # noqa: E402
from reportlab.lib import colors                           # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: E402
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle  # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05
ZONES = ["beyond-down", "inzone-buy", "body", "inzone-sell", "beyond-up"]
ZLBL = {"beyond-down": "Beyond down", "inzone-buy": "Inzone buy", "body": "Body range",
        "inzone-sell": "Inzone sell", "beyond-up": "Beyond up"}
TIERS = ["cyan/orange", "red/green", "hollow"]


def tier_color(tier, side):
    if tier == "hollow":
        return "hollow"
    if tier == "cyan/orange":
        return "cyan (buy VHI)" if side == "buy" else "orange (sell VHI)"
    return "green (buy HI)" if side == "buy" else "red (sell HI)"


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


def build_trades():
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
        liv = [spr(k, buy) for k in range(det, ent + 1)]
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
                if spr(j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        zD = zone_at(det); zE = zone_at(j0)
        if zD is None or zE is None:
            continue
        rows.append((walk(det, j0, buy) - FEE, tier, "buy" if buy else "sell", zD, zE))
    span = (float(max(et)) - float(min(et))) / 86400.0
    wf = time.strftime("%b %d", time.gmtime(float(min(et)))); wt = time.strftime("%b %d", time.gmtime(float(max(et))))
    return rows, span, wf, wt


# ---- colour helpers ----------------------------------------------------------------
NAVY = colors.Color(0.12, 0.16, 0.22); SLATE = colors.Color(0.27, 0.33, 0.40)
LIGHT = colors.Color(0.94, 0.95, 0.97); ZEBRA = colors.Color(0.975, 0.98, 0.99)
POSGRN = colors.Color(0.09, 0.45, 0.20); NEGRED = colors.Color(0.72, 0.11, 0.14); MUTE = colors.Color(0.6, 0.63, 0.68)
GRN = (0.09, 0.55, 0.28); RED = (0.80, 0.20, 0.24)


def heat(frac, base):
    frac = max(0.0, min(1.0, frac)) * 0.72
    r, g, b = base
    return colors.Color(1 - (1 - r) * frac, 1 - (1 - g) * frac, 1 - (1 - b) * frac)


def main():
    rows, span, wf, wt = build_trades()
    NET = np.array([r[0] for r in rows]); TIER = np.array([r[1] for r in rows]); SIDE = np.array([r[2] for r in rows])
    ZD = np.array([r[3] for r in rows]); ZE = np.array([r[4] for r in rows]); N = len(rows)
    ndiff = int((ZD != ZE).sum())

    def stat(mask):
        g = NET[mask]; n = len(g)
        if n == 0:
            return None
        w = int((g > BE).sum()); b = int((np.abs(g) <= BE).sum()); l = int((g < -BE).sum())
        return dict(n=n, w=w, be=b, l=l, wp=100 * w / n, bep=100 * b / n, lp=100 * l / n, mean=g.mean(), tot=g.sum() * 10.0)

    data = [["Category", "n", "Winners", "Breakeven", "Losers", "Net / trade", "Total ($1k/tr)"]]; cmds = []

    def band(label, col=None):
        data.append([label, "", "", "", "", "", ""]); r = len(data) - 1
        cmds.extend([("SPAN", (0, r), (6, r)), ("BACKGROUND", (0, r), (6, r), col or NAVY),
                     ("TEXTCOLOR", (0, r), (6, r), colors.white), ("FONTNAME", (0, r), (6, r), "Helvetica-Bold"),
                     ("FONTSIZE", (0, r), (6, r), 8.5), ("TOPPADDING", (0, r), (6, r), 4), ("BOTTOMPADDING", (0, r), (6, r), 4)])

    def sub(label, n):
        data.append(["%s   (n=%d)" % (label, n), "", "", "", "", "", ""]); r = len(data) - 1
        cmds.extend([("SPAN", (0, r), (6, r)), ("BACKGROUND", (0, r), (6, r), SLATE),
                     ("TEXTCOLOR", (0, r), (6, r), colors.white), ("FONTNAME", (0, r), (6, r), "Helvetica-Bold"),
                     ("FONTSIZE", (0, r), (6, r), 7.5)])

    def row(label, s, strong=False, indent=False):
        lab = ("    " + label) if indent else label
        if s is None:
            data.append([lab, "0", "—", "—", "—", "—", "—"]); r = len(data) - 1
            cmds.extend([("TEXTCOLOR", (0, r), (6, r), MUTE), ("FONTSIZE", (0, r), (6, r), 7)]); return
        data.append([lab, str(s["n"]), "%d  ·  %.0f%%" % (s["w"], s["wp"]), "%d  ·  %.0f%%" % (s["be"], s["bep"]),
                     "%d  ·  %.0f%%" % (s["l"], s["lp"]), "%+.3f%%" % s["mean"], "$%+.0f" % s["tot"]]); r = len(data) - 1
        cmds.extend([("BACKGROUND", (2, r), (2, r), heat(s["wp"] / 100.0, GRN)),
                     ("BACKGROUND", (4, r), (4, r), heat(s["lp"] / 100.0, RED)),
                     ("TEXTCOLOR", (5, r), (5, r), POSGRN if s["mean"] > 0 else NEGRED),
                     ("TEXTCOLOR", (6, r), (6, r), POSGRN if s["tot"] > 0 else NEGRED), ("FONTSIZE", (0, r), (6, r), 7.3)])
        band_bg = LIGHT if strong else ZEBRA
        if strong:
            cmds.append(("FONTNAME", (0, r), (6, r), "Helvetica-Bold"))
        cmds.extend([("BACKGROUND", (0, r), (1, r), band_bg), ("BACKGROUND", (3, r), (3, r), band_bg),
                     ("BACKGROUND", (5, r), (6, r), band_bg)])

    def zone_marginal(ZC):
        for z in ZONES:
            row(ZLBL[z], stat(ZC == z), strong=True)

    def full_breakdown(ZC):
        for sd in ("buy", "sell"):
            for t in TIERS:
                m0 = (SIDE == sd) & (TIER == t)
                sub("%s D  ·  %s" % (sd.capitalize(), tier_color(t, sd)), int(m0.sum()))
                for z in ZONES:
                    row(ZLBL[z], stat(m0 & (ZC == z)), indent=True)

    band("OVERALL  (anchor-independent)")
    row("All trades", stat(np.ones(N, bool)), strong=True)
    band("MARGINAL · BY D-TIER  (anchor-independent — tier is a property of D)")
    for t in TIERS:
        row(t, stat(TIER == t), strong=True)
    band("MARGINAL · BY POSITION  (anchor-independent)")
    for sd in ("buy", "sell"):
        row("%s D" % sd.capitalize(), stat(SIDE == sd), strong=True)

    DBLUE = colors.Color(0.13, 0.30, 0.52); EGRN = colors.Color(0.12, 0.40, 0.28)
    band("ZONE MARGINAL · anchor = D  (detection price)", DBLUE); zone_marginal(ZD)
    band("ZONE MARGINAL · anchor = ENTRY  (E2 / E-held fill price)", EGRN); zone_marginal(ZE)
    band("FULL BREAKDOWN · POSITION × D-TIER × ZONE · anchor = D", DBLUE); full_breakdown(ZD)
    band("FULL BREAKDOWN · POSITION × D-TIER × ZONE · anchor = ENTRY (E2 / E-held)", EGRN); full_breakdown(ZE)

    out = os.path.join(REPO, "study", "out", "pivot_zone5_report.pdf")
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=1.1 * cm, rightMargin=1.1 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.0 * cm)
    ss = getSampleStyleSheet()
    ttl = ParagraphStyle("t", parent=ss["Title"], fontSize=16, textColor=NAVY, spaceAfter=2, leading=19)
    subs = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5, textColor=SLATE, leading=12)
    note = ParagraphStyle("n", parent=ss["Normal"], fontSize=7.2, textColor=colors.Color(0.35, 0.38, 0.43), leading=10)
    story = [Paragraph("PIVOT — D-Tier &times; 4h Zone &times; Position &nbsp;·&nbsp; D vs ENTRY anchor", ttl),
             Paragraph("Frozen PIVOT-ZZTRAIL · in-sample %s&ndash;%s 2026 (%.1f d) · n = %d · NON-MERGED 4h wick · "
                       "three-outcome on NET (fee 0.10%%). Zone anchor DISAGREES between D and entry on "
                       "<b>%d/%d trades (%.0f%%)</b> — hence both views." % (wf, wt, span, N, ndiff, N, 100.0 * ndiff / N), subs),
             Spacer(1, 7)]
    tbl = Table(data, repeatRows=1, colWidths=[6.6 * cm, 1.3 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm, 3.0 * cm, 3.0 * cm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (6, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (6, 0), 8),
        ("BACKGROUND", (0, 0), (6, 0), colors.Color(0.06, 0.09, 0.13)), ("TEXTCOLOR", (0, 0), (6, 0), colors.white),
        ("ALIGN", (1, 0), (6, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "LEFT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica"), ("TOPPADDING", (0, 1), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2.2), ("LINEBELOW", (0, 0), (6, 0), 0.6, NAVY),
        ("GRID", (0, 1), (6, -1), 0.25, colors.Color(0.85, 0.87, 0.9))] + cmds))
    story += [tbl, Spacer(1, 7),
              Paragraph("<b>Zones</b> (non-merged, single last-completed 4h candle, bottom&rarr;top): "
                        "<b>Beyond down</b> px&lt;low · <b>Inzone buy</b> low&ndash;vq_lo (green buyer wick) · "
                        "<b>Body range</b> vq_lo&ndash;vq_hi · <b>Inzone sell</b> vq_hi&ndash;high (red seller wick) · "
                        "<b>Beyond up</b> px&gt;high. <b>Anchor</b>: <font color='#2050a0'><b>D</b></font> = zone of "
                        "the detection price; <font color='#1f6647'><b>ENTRY</b></font> = zone of the actual E2/E-held "
                        "fill (where the trade lives). Tier & position are D-intrinsic — identical under both anchors; "
                        "only the ZONE rows differ.", note),
              Paragraph("<b>D-tier</b> colour is side-specific: VHI &gt;80 = cyan (buy) / orange (sell); HI 63&ndash;80 "
                        "= green (buy) / red (sell); hollow &le;63. <b>Three-outcome</b> on NET: winner &gt; +0.05%% · "
                        "breakeven |net| &le; 0.05%% · loser &lt; &minus;0.05%%. Total = net &times; $1,000. <b>Small-n:</b> "
                        "the three-deep cells are mostly n=1&ndash;5 — directional only. Strategy FROZEN; study readout.", note)]
    doc.build(story)
    print("wrote", out, "(%d trades, %d anchor-disagree)" % (N, ndiff))


if __name__ == "__main__":
    main()
