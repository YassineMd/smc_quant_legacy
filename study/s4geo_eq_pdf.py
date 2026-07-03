"""S4-GEO stage 3 — reading-first PDF report (study/out/S4GEO_EQ_report.pdf).

Reads the stage-3 cells CSVs + meta json written by s4geo_eq.py. One section per timeframe: every
cell whose majority next-bar direction exceeds 50% (at that tf's min-n; sub-min-n and THIN rows kept
but greyed), sorted by |lift| desc, majority probability bolded, sealed-holdout PASS starred.
"""
import os, sys, csv, json, time
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
TFS = ("1m", "5m", "15m", "1h", "4h")

INK = colors.HexColor("#1C2733")
GREY = colors.HexColor("#9AA4AE")
HDR_BG = colors.HexColor("#22303F")
ROW_ALT = colors.HexColor("#F1F4F7")
GREEN = colors.HexColor("#0E7A3D")
RED = colors.HexColor("#B02A2A")

S_TITLE = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=INK)
S_SUB = ParagraphStyle("s", fontName="Helvetica", fontSize=10.5, leading=15, textColor=INK)
S_H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK,
                     spaceBefore=6, spaceAfter=2)
S_BASE = ParagraphStyle("b", fontName="Helvetica-Oblique", fontSize=9.5, leading=13,
                        textColor=colors.HexColor("#4A5763"))
S_SMALL = ParagraphStyle("m", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK)


def nice(cell):
    tag, body = cell.split(":", 1)
    if tag == "T1":
        for op in (">=", "<=", ">", "<", "="):
            for a in ("O", "C", "M"):
                if body.startswith(a + op):
                    txt = "%s %s %s" % (a, op, body[len(a) + len(op):])
                    if body == "O=C":
                        txt += "  (doji bar)"
                    return txt
        return body
    if tag == "T2":
        return body.replace("&", " & ")
    if tag == "T3":
        x = body[0]
        return "%s %s" % (x, "highest-or-tied" if ">=" in body else "lowest-or-tied")
    return body + "  (M=P bars)"


def load(tf):
    out = []
    with open(os.path.join(OUT, "s4geo_eq_cells_%s.csv" % tf), encoding="utf-8") as f:
        next(f)
        for r in csv.DictReader(f):
            r["full_n"] = int(r["full_n"])
            r["full_pup"] = float(r["full_pup"]) if r["full_pup"] else None
            r["full_lift"] = float(r["full_lift"]) if r["full_lift"] else None
            r["survivor"] = r["survivor"] == "True"
            r["holdout_pass"] = r["holdout_pass"] == "True"
            out.append(r)
    return out


def tf_table(tf, recs, m):
    rows = [r for r in recs if r["full_n"] > 0 and r["full_pup"] is not None
            and abs(r["full_pup"] - 50.0) > 1e-9]
    rows.sort(key=lambda r: -abs(r["full_lift"]))
    data = [["shape", "n", "P(UP)", "P(DOWN)", "lift", "holdout"]]
    meta_rows = []
    for r in rows:
        pup = r["full_pup"]; pdn = 100.0 - pup
        verdict = ("PASS *" if r["holdout_pass"] else "fail") if r["survivor"] else "-"
        data.append([nice(r["cell"]), "%d" % r["full_n"], "%.1f%%" % pup, "%.1f%%" % pdn,
                     "%+.1f pp" % r["full_lift"], verdict])
        meta_rows.append(dict(grey=(r["full_n"] < m["min_n"]) or m["thin"],
                              up=pup > 50.0, lift=r["full_lift"], surv=r["survivor"],
                              ok=r["holdout_pass"]))
    t = Table(data, colWidths=[62 * mm, 16 * mm, 20 * mm, 22 * mm, 22 * mm, 20 * mm],
              repeatRows=1, hAlign="LEFT")
    st = [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
          ("FONTSIZE", (0, 0), (-1, 0), 9),
          ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
          ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
          ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
          ("FONTSIZE", (0, 1), (-1, -1), 8.5),
          ("TEXTCOLOR", (0, 1), (-1, -1), INK),
          ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
          ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
          ("TOPPADDING", (0, 0), (-1, -1), 2.5),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
          ("LINEBELOW", (0, 0), (-1, 0), 0.75, HDR_BG),
          ("LINEBELOW", (0, -1), (-1, -1), 0.5, GREY)]
    for i, mr in enumerate(meta_rows, start=1):
        if mr["grey"]:
            st.append(("TEXTCOLOR", (0, i), (-1, i), GREY))
            continue
        # bold the majority-direction probability
        st.append(("FONTNAME", (2 if mr["up"] else 3, i), (2 if mr["up"] else 3, i),
                   "Helvetica-Bold"))
        st.append(("TEXTCOLOR", (4, i), (4, i), GREEN if mr["lift"] > 0 else RED))
        if mr["ok"]:
            st.append(("FONTNAME", (5, i), (5, i), "Helvetica-Bold"))
    t.setStyle(TableStyle(st))
    return t


def build():
    meta = json.load(open(os.path.join(OUT, "_s4geo_eq_meta.json"), encoding="utf-8"))
    cells = {tf: load(tf) for tf in TFS}
    doc = SimpleDocTemplate(os.path.join(OUT, "S4GEO_EQ_report.pdf"), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="S4-GEO Stage 3 — Equality Extension",
                            author="smc_quant study pipeline")
    el = []

    # ---- cover -------------------------------------------------------------
    el.append(Paragraph("S4-GEO Stage 3", S_TITLE))
    el.append(Paragraph("Equality Extension — bar geometry vs next-bar direction, tick-exact",
                        ParagraphStyle("t2", parent=S_TITLE, fontSize=13, leading=17,
                                       textColor=colors.HexColor("#4A5763"))))
    el.append(Spacer(1, 8 * mm))
    cov = [["tf", "data span (UTC)", "rows", "recovered tie-bars", "baseline UP", "flags"]]
    for tf in TFS:
        m = meta[tf]
        fl = " ".join((["SPENT"] if m["spent"] else []) + (["THIN"] if m["thin"] else []))
        cov.append([tf, "%s  ->  %s" % (time.strftime("%b %d %H:%M", time.gmtime(m["span"][0])),
                                        time.strftime("%b %d %H:%M", time.gmtime(m["span"][1]))),
                    "%d" % m["rows"], "%d  (%.1f%%)" % (m["recovered"], 100.0 * m["recovered"] / m["rows"]),
                    "%.2f%%" % m["base"], fl])
    ct = Table(cov, colWidths=[12 * mm, 52 * mm, 16 * mm, 36 * mm, 24 * mm, 20 * mm], hAlign="LEFT")
    ct.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
                            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    el.append(ct)
    el.append(Spacer(1, 6 * mm))
    el.append(Paragraph(
        "<b>Tick:</b> $0.01, derived from data — every O/C/H/L and ladder price on all five "
        "timeframes is cent-aligned (gcd of scaled prices = 1 cent). Equality means equal after "
        "rounding to this tick.", S_SUB))
    el.append(Spacer(1, 3 * mm))
    el.append(Paragraph(
        "<b>Honesty framing.</b> Tie-bar cells (the “=” pairs, multi-equalities, and the "
        "M=P-collapsed orderings) are the FIRST ANALYSIS of bars every earlier stage excluded. "
        "Extended non-tie cells (strict pairs, or-tied extremes) are re-cuts of already-mined data "
        "and are characterization, not discovery. The 1m dataset is spent throughout. Outcome is "
        "next-bar direction — an information measure: no barriers, no fees, not profitability. "
        "Holdouts (last 30%, 1-bucket embargo) were sealed and judged once.", S_SUB))
    el.append(Spacer(1, 3 * mm))
    el.append(Paragraph(
        "<b>Multiplicity.</b> This stage screens 40 cells × 5 timeframes = 200. S4-GEO running "
        "total: 50 (stage 1) + 200 (stage 2) + 200 (stage 3) = <b>450 cells</b>, on top of ~35 "
        "earlier trials against the spent 1m data.", S_SUB))

    # ---- per-tf sections ----------------------------------------------------
    for tf in TFS:
        m = meta[tf]
        el.append(PageBreak())
        note = "  —  SPENT (characterization only)" if m["spent"] else \
               ("  —  THIN (114 usable bars; all rows greyed)" if m["thin"] else "")
        el.append(Paragraph("%s%s" % (tf, note), S_H))
        el.append(Paragraph(
            "Cells leaning past 50%% next-bar direction, sorted by |lift|. Baseline P(UP) = "
            "<b>%.2f%%</b> over %d rows; survivor min-n %d; grey = below min-n%s. "
            "* = sealed-holdout PASS." % (m["base"], m["rows"], m["min_n"],
                                          " or THIN tf" if m["thin"] else ""), S_BASE))
        el.append(Spacer(1, 2 * mm))
        el.append(tf_table(tf, cells[tf], m))

    # ---- final page ----------------------------------------------------------
    el.append(PageBreak())
    el.append(Paragraph("Cross-timeframe summary — holdout PASSes only", S_H))
    ps = [["tf", "shape", "class", "disc n / lift", "90% CI", "hold n / lift"]]
    for tf in TFS:
        for r in cells[tf]:
            if r["holdout_pass"]:
                ps.append([tf, nice(r["cell"]), r["class"],
                           "%s / %s pp" % (r["disc_n"], r["disc_lift"]),
                           "[%s, %s]" % (r["disc_ci_lo"], r["disc_ci_hi"]),
                           "%s / %s pp" % (r["hold_n"], r["hold_lift"])])
    pt = Table(ps, colWidths=[12 * mm, 52 * mm, 18 * mm, 30 * mm, 28 * mm, 28 * mm], hAlign="LEFT")
    pt.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
                            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    el.append(pt)
    el.append(Spacer(1, 5 * mm))
    el.append(Paragraph("Caveats", ParagraphStyle("h2", parent=S_H, fontSize=12)))
    for c in (
        "The 1m slice is spent (~35+ cumulative trials plus stages 1–3); its PASS is a "
        "characterization, not a confirmed edge — and its 5m sibling cell flipped sign in holdout.",
        "The HTF window is ~11 days (June 21 – July 2). Day-block bootstrap runs on 4–12 "
        "blocks; regime coverage is one market phase. 4h is THIN (114 bars) throughout.",
        "Equality is tick-exact at $0.01. A different binning (e.g. coarser “near-equal” "
        "bands) was NOT tested and would be a new multiplicity charge.",
        "Non-tie cells re-cut data already mined by stages 1–2 on the same window; only the "
        "tie-bar cells are first-look. All holdouts were judged exactly once — these verdicts "
        "are final for this snapshot; next evidence must come from forward snapshots."):
        el.append(Paragraph("•  " + c, S_SMALL))
        el.append(Spacer(1, 1.5 * mm))
    doc.build(el)
    print("PDF written:", os.path.join(OUT, "S4GEO_EQ_report.pdf"))


if __name__ == "__main__":
    build()
