"""SELL-D — per-entry causal report, split WINNERS / BREAKEVEN / LOSERS, to PDF.

Mirror of buy_d_report.py for the SHORT side. Reads the cached recs from de_zone_effectiveness (rebuilds if missing).
Per Sell-D entry:
  * bucket Idx (terminal thousands-dot)
  * position in the LAST 4h FILLED bucket — 5-zone wick + volume-profile area (above VAH / upper VA / lower VA /
    below VAL, 70% value area)
  * position in the CURRENT non-100%-filled 4h bucket — 5-zone wick + volume-profile area (rebuilt as-of the D)
  * the forming 4h bucket's FILL % at the D bar
  * the 1h-window excursions signed TO THE SHORT: adverse% (price UP, against the short, negative) and favourable%
    (price DOWN, with the short, positive). NOTE mae_mfe() stores RAW (low%, high%) — for a short the low is the
    favourable move and the high is the adverse one, so adv = -d_mfe, fav = -d_mae.
All causal (each field read as-of the D bar). net = V3 ZZTRAIL after 0.10 fee. Split = by NET outcome.
Run: python study/sell_d_report.py   ->  study/out/sell_d_nonfaded.pdf  (+ sell_d_faded.pdf)
"""
import os, sys, pickle
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import de_zone_effectiveness as D                       # noqa: E402
from buy_d_report import fmt_idx, load_recs             # noqa: E402  (reuse the Idx formatter + recs loader)

BE = D.BE; Z5N = D.Z5_NAME


def build_pdf(recs, path, nonfaded=True):
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

    # V3 D-ENTRY scope: cyan/orange tier ONLY. non-faded = Step-3 directional-zone D-entry (d_step3);
    # faded = cyan/orange D whose 4H zone does NOT confirm direction (d_step3 False). Green/hollow excluded.
    selld = [r for r in recs if r["side"] == "Sell" and r.get("d_tier") == "cyan"
             and bool(r.get("d_step3")) == nonfaded and r.get("d_net") is not None]
    win = [r for r in selld if r["d_net"] > BE]
    beg = [r for r in selld if abs(r["d_net"]) <= BE]
    los = [r for r in selld if r["d_net"] < -BE]
    hdr = ["Idx", "filled zone", "filled VP", "forming zone", "forming VP", "fill%", "1h adv%", "1h fav%", "net%"]

    def _adv(r):    # adverse (against the short) = the HIGH reach, shown negative
        return None if r["d_mfe"] is None else -float(r["d_mfe"])

    def _fav(r):    # favourable (with the short) = the LOW reach, shown positive
        return None if r["d_mae"] is None else -float(r["d_mae"])

    def data_for(group):
        out = [hdr]
        for r in sorted(group, key=lambda r: r["d_net"], reverse=True):
            out.append([fmt_idx(r["idx"]), Z5N.get(r["d_z5"], r["d_z5"] or "-"), r["d_vpfill"] or "-",
                        Z5N.get(r["d_zform"], r["d_zform"] or "-"), r["d_vpform"] or "-",
                        ("%.0f%%" % r["d_fill"]) if r["d_fill"] is not None else "-",
                        ("%+.2f%%" % _adv(r)) if _adv(r) is not None else "-",
                        ("%+.2f%%" % _fav(r)) if _fav(r) is not None else "-",
                        "%+.3f%%" % r["d_net"]])
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
        a = np.array([r["d_net"] for r in group], float)
        return "n=%d &middot; net %+.3f%%/tr &middot; avg 1h adv %+.2f%% / fav %+.2f%%" % (
            len(group), (a.mean() if len(a) else 0.0),
            (np.mean([_adv(r) for r in group]) if group else 0.0),
            (np.mean([_fav(r) for r in group]) if group else 0.0))

    allo = np.array([r["d_net"] for r in selld], float)
    story = [Paragraph("Pivot V3 &mdash; SELL-D %s (cyan/orange tier, V3 D-entry), by outcome" % lbl, h1),
             Paragraph("Scope = cyan/orange tier ONLY (the V3 D-entry tiers). NON-FADED = Step-3 directional-zone "
                       "D-entry; FADED = cyan/orange D whose 4H zone does not confirm. Causal (every field read AS-OF "
                       "the D bar). Zone = 4h 5-zone wick position; VP area = above "
                       "VAH / upper VA / lower VA / below VAL (70%% value area). fill%% = forming 4h bucket vol/target "
                       "at the D. 1h adv/fav = worst/best reach within 1h of the D, signed to the SHORT (adv = price "
                       "UP against the short, &minus;; fav = price DOWN with the short, +). net = V3 ZZTRAIL after "
                       "0.10 fee. n=%d, net %+.3f%%/tr, %.0f%% win." % (
                           len(selld), allo.mean() if len(allo) else 0.0,
                           100.0 * len(win) / max(1, len(selld))), sub),
             Paragraph("WINNERS &nbsp;&mdash;&nbsp; " + stat(win), hG), table(win), Spacer(1, 6),
             Paragraph("BREAKEVEN &nbsp;&mdash;&nbsp; " + stat(beg), hG), table(beg), Spacer(1, 6),
             Paragraph("LOSERS &nbsp;&mdash;&nbsp; " + stat(los), hG), table(los)]
    SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=10*mm, rightMargin=10*mm,
                      topMargin=10*mm, bottomMargin=10*mm).build(story)
    print("wrote %s | winners=%d breakeven=%d losers=%d" % (path, len(win), len(beg), len(los)))


if __name__ == "__main__":
    recs = load_recs()
    build_pdf(recs, os.path.join(REPO, "study", "out", "sell_d_nonfaded.pdf"), nonfaded=True)
    build_pdf(recs, os.path.join(REPO, "study", "out", "sell_d_faded.pdf"), nonfaded=False)
