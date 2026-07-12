"""E-ENTRY (Path-B New-E) — per-entry causal report, split WINNERS / BREAKEVEN / LOSERS, to PDF, both sides.

Same study/params/format as the D reports, for the V3 E-ENTRY (Step 4, Path B): a New-E fires strictly after a
NON-Step-3 D (any tier) — first bar (<=4h) with aligned LOCKED P2 spread >=15 AND HMS-favour AND current-forming-HM
favour. non-faded = the E is one of the 6 recorded `side.Dzone->Ezone` combos (E_TAKE_ANY any-tier + E_TAKE_CYAN
cyan-only); faded = a New-E that fires but is NOT a recorded combo. Enter at the E bar close. Every field read AS-OF
the E bar; net = V3 ZZTRAIL after 0.10 fee (same measurement as the D study). 1h adv/fav signed to the position.
Run: python study/e_report.py  ->  study/out/{buy,sell}_e_{nonfaded,faded}.pdf
"""
import os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import de_zone_effectiveness as D                       # noqa: E402
from buy_d_report import fmt_idx, load_recs             # noqa: E402

BE = D.BE; Z5N = D.Z5_NAME


def build_pdf(recs, path, side, nonfaded=True):
    lbl = "NON-FADED" if nonfaded else "FADED"
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=14, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#555"), spaceAfter=6)
    hG = ParagraphStyle("hG", parent=ss["Heading1"], fontSize=11, spaceBefore=9, spaceAfter=3)
    buy = side == "Buy"

    grp = [r for r in recs if r["side"] == side and r.get("e_net") is not None
           and bool(r.get("e_nonfaded")) == nonfaded]
    win = [r for r in grp if r["e_net"] > BE]
    beg = [r for r in grp if abs(r["e_net"]) <= BE]
    los = [r for r in grp if r["e_net"] < -BE]
    hdr = ["Idx", "filled zone", "filled VP", "forming zone", "forming VP", "fill%", "1h adv%", "1h fav%", "net%"]

    def _adv(r):    # adverse (against the position), shown negative:  buy = the LOW reach, sell = the HIGH reach
        v = r["e_mae"] if buy else (None if r["e_mfe"] is None else -r["e_mfe"])
        return None if v is None else float(v)

    def _fav(r):    # favourable (with the position), shown positive:  buy = the HIGH reach, sell = the LOW reach
        v = r["e_mfe"] if buy else (None if r["e_mae"] is None else -r["e_mae"])
        return None if v is None else float(v)

    def data_for(group):
        out = [hdr]
        for r in sorted(group, key=lambda r: r["e_net"], reverse=True):
            out.append([fmt_idx(r["e_idx"]), Z5N.get(r["e_z5"], r["e_z5"] or "-"), r["e_vpfill"] or "-",
                        Z5N.get(r["e_zform"], r["e_zform"] or "-"), r["e_vpform"] or "-",
                        ("%.0f%%" % r["e_fill"]) if r["e_fill"] is not None else "-",
                        ("%+.2f%%" % _adv(r)) if _adv(r) is not None else "-",
                        ("%+.2f%%" % _fav(r)) if _fav(r) is not None else "-",
                        "%+.3f%%" % r["e_net"]])
        return out

    def table(group):
        data = data_for(group)
        t = Table(data, repeatRows=1,
                  colWidths=[20*mm, 26*mm, 19*mm, 26*mm, 19*mm, 14*mm, 18*mm, 18*mm, 19*mm])
        st = [("FONTSIZE", (0, 0), (-1, -1), 7.2), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
              ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "CENTER"),
              ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
              ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8)]
        for i in range(1, len(data)):
            net = float(data[i][-1].rstrip("%"))
            bg = colors.HexColor("#e7f6ec") if net > BE else (colors.HexColor("#fbe9e9") if net < -BE else colors.HexColor("#f4f4f4"))
            st.append(("BACKGROUND", (8, i), (8, i), bg))
        t.setStyle(TableStyle(st))
        return t

    def stat(group):
        a = np.array([r["e_net"] for r in group], float)
        return "n=%d &middot; net %+.3f%%/tr &middot; avg 1h adv %+.2f%% / fav %+.2f%%" % (
            len(group), (a.mean() if len(a) else 0.0),
            (np.mean([_adv(r) for r in group]) if group else 0.0),
            (np.mean([_fav(r) for r in group]) if group else 0.0))

    allo = np.array([r["e_net"] for r in grp], float)
    story = [Paragraph("Pivot V3 &mdash; %s-E %s (V3 E-entry, Path B), by outcome" % (side.upper(), lbl), h1),
             Paragraph("V3 E-ENTRY (Step 4, Path B): a New-E fires strictly after a NON-Step-3 D (any tier). "
                       "NON-FADED = the E is one of the 6 recorded side&middot;Dzone&rarr;Ezone combos; FADED = a "
                       "New-E that fires but is not a recorded combo. Enter at the E bar close. Causal (every field "
                       "read AS-OF the E bar). Zone = 4h 5-zone wick; VP = above VAH / upper VA / lower VA / below VAL "
                       "(70%% value area). fill%% = forming 4h vol/target at the E. 1h adv/fav = worst/best reach "
                       "within 1h, signed to the position (adv against = &minus;, fav with = +). net = V3 ZZTRAIL "
                       "after 0.10 fee. n=%d, net %+.3f%%/tr, %.0f%% win." % (
                           len(grp), allo.mean() if len(allo) else 0.0,
                           100.0 * len(win) / max(1, len(grp))), sub),
             Paragraph("WINNERS &nbsp;&mdash;&nbsp; " + stat(win), hG), table(win), Spacer(1, 6),
             Paragraph("BREAKEVEN &nbsp;&mdash;&nbsp; " + stat(beg), hG), table(beg), Spacer(1, 6),
             Paragraph("LOSERS &nbsp;&mdash;&nbsp; " + stat(los), hG), table(los)]
    SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=10*mm, rightMargin=10*mm,
                      topMargin=10*mm, bottomMargin=10*mm).build(story)
    print("wrote %s | winners=%d breakeven=%d losers=%d" % (path, len(win), len(beg), len(los)))


if __name__ == "__main__":
    recs = load_recs()
    out = os.path.join(REPO, "study", "out")
    build_pdf(recs, os.path.join(out, "buy_e_nonfaded.pdf"), "Buy", nonfaded=True)
    build_pdf(recs, os.path.join(out, "buy_e_faded.pdf"), "Buy", nonfaded=False)
    build_pdf(recs, os.path.join(out, "sell_e_nonfaded.pdf"), "Sell", nonfaded=True)
    build_pdf(recs, os.path.join(out, "sell_e_faded.pdf"), "Sell", nonfaded=False)
