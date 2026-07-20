"""Assemble the MMXSKEW / MMXSKEW-ORB PDF report from study/out/report_metrics.json + rep_*.png.
Run:  python study/report_pdf.py   ->   study/out/MMXSKEW_Report.pdf
"""
from __future__ import annotations
import os, json, struct
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
                                PageBreak, KeepTogether)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
NAVY, TEAL, GREEN, RED, AMBER = HexColor("#0f172a"), HexColor("#0ea5e9"), HexColor("#16a34a"), HexColor("#dc2626"), HexColor("#b45309")
INK, GRAY, LIGHT, CARD, LINE = HexColor("#1e293b"), HexColor("#64748b"), HexColor("#f1f5f9"), HexColor("#f8fafc"), HexColor("#e2e8f0")
CW = A4[0] - 3.2 * cm                                   # content width (1.6cm margins)

D = json.load(open(os.path.join(OUT, "report_metrics.json")))


def st(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13)
    base.update(kw); return ParagraphStyle(name, **base)


TITLE = st("t", fontName="Helvetica-Bold", fontSize=23, textColor=white, leading=26)
SUB = st("s", fontSize=10.5, textColor=HexColor("#cbd5e1"), leading=14)
H = st("h", fontName="Helvetica-Bold", fontSize=14, textColor=white, leading=17)
BODY = st("b", fontSize=9.5, leading=13.5)
CAP = st("c", fontSize=8, textColor=GRAY, leading=11, fontName="Helvetica-Oblique")
SMALL = st("sm", fontSize=8.5, textColor=GRAY, leading=11)
CARDV = st("cv", fontName="Helvetica-Bold", fontSize=17, alignment=TA_CENTER, leading=19)
CARDL = st("cl", fontSize=7.6, textColor=GRAY, alignment=TA_CENTER, leading=9)


def png_size(p):
    with open(p, "rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24]); return w, h


def img(prefix, kind, width=CW):
    p = os.path.join(OUT, f"{prefix}_{kind}.png"); w, h = png_size(p)
    return Image(p, width=width, height=width * h / w)


def band(text, bg, style=H, pad=7):
    t = Table([[Paragraph(text, style)]], colWidths=[CW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), 11),
                           ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]))
    return t


def cards(items):
    # items: list of (label, value, color) ; 3 per row
    rows = []
    for r in range(0, len(items), 3):
        chunk = items[r:r + 3]
        cell = []
        for lab, val, col in chunk:
            inner = Table([[Paragraph(val, st("v", fontName="Helvetica-Bold", fontSize=17,
                                              alignment=TA_CENTER, leading=19, textColor=col))],
                           [Paragraph(lab, CARDL)]], colWidths=[CW / 3 - 6])
            inner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CARD),
                                       ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                                       ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                                       ("TOPPADDING", (0, 1), (-1, 1), 0)]))
            cell.append(inner)
        while len(cell) < 3:
            cell.append("")
        rows.append(cell)
    t = Table(rows, colWidths=[CW / 3] * 3)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def kv_table(pairs, width):
    data = [[Paragraph(f"<b>{k}</b>", SMALL), Paragraph(v, st("r", fontSize=8.5, alignment=TA_RIGHT, textColor=INK))]
            for k, v in pairs]
    t = Table(data, colWidths=[width * 0.58, width * 0.42])
    sty = [("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3.2),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2), ("LEFTPADDING", (0, 0), (-1, -1), 6),
           ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    t.setStyle(TableStyle(sty)); return t


def money(v):
    return ("-$" if v < 0 else "$") + f"{abs(v):,.0f}"


def strat_section(key, accent):
    m = D[key]["metrics"]; mc = D[key]["mc"]; pre = D[key]["prefix"]
    rules = {"MMXSKEW": "v1.1 — dir + skew + panel-2 spread ±35 + POC-baseline + long delta&lt;15. Fixed SL 0.1% "
                        "beyond the candle extreme, TP = 1.5× SL. All qualifying 1h signals.",
             "MMXSKEW-ORB": "v1.1 signal + NY-session filter: 1 trade/day, the first setup after the 13:30–14:00 UTC "
                            "opening range that breaks it. SL 0.1% beyond the session OPEN, TP = 1.5× SL."}[key]
    story = [band(key, accent), Spacer(1, 5),
             Paragraph(rules, BODY),
             Paragraph(f"{D[key]['first']} → {D[key]['last']} &nbsp;·&nbsp; {m['n']} trades &nbsp;·&nbsp; RR 1:{D['rr']} "
                       f"&nbsp;·&nbsp; ${D['bal0']:,.0f} start, 10%×10× (full-notional)", SMALL), Spacer(1, 8)]
    pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    story.append(cards([
        ("NET PROFIT", f"{m['net_pct']:+.1f}%", GREEN if m["net_pct"] >= 0 else RED),
        ("WIN RATE", f"{m['win_rate']:.1f}%", INK),
        ("PROFIT FACTOR", pf, GREEN if m["profit_factor"] >= 1 else RED),
        ("MAX DRAWDOWN", f"{m['max_dd']:.1f}%", AMBER),
        ("EXPECTANCY / TRADE", f"{m['expectancy']:+.3f}%", GREEN if m["expectancy"] >= 0 else RED),
        ("SHARPE (per-trade)", f"{m['sharpe']:.2f}", INK),
    ]))
    def capimg(cap, kind):
        return KeepTogether([Paragraph(cap, CAP), Spacer(1, 2), img(pre, kind)])
    story += [Spacer(1, 10),
              capimg("<b>Equity curve</b> — $200k, compounding, net of 0.08% fees", "equity"), Spacer(1, 8),
              capimg("<b>Trades on price</b> — entries marked ▲ long / ▼ short, green = win, red = loss", "trades"), Spacer(1, 8),
              capimg("<b>Monte Carlo</b> — 20,000 bootstrap resamples of the trade set", "mc"), Spacer(1, 10)]
    left = kv_table([
        ("Trades (win / loss)", f"{m['n']}  ({m['wins']} / {m['losses']})"),
        ("Win rate", f"{m['win_rate']:.1f}%"),
        ("Net profit", f"{money(m['net_profit'])}  ({m['net_pct']:+.1f}%)"),
        ("Average profit (win)", f"{m['avg_win']:+.3f}%   ({money(m['avg_win_usd'])})"),
        ("Average loss", f"{m['avg_loss']:+.3f}%   ({money(m['avg_loss_usd'])})"),
        ("Payoff ratio (win/loss)", f"{m['payoff']:.2f}"),
        ("Profit factor", pf),
        ("Expectancy / trade", f"{m['expectancy']:+.3f}%"),
    ], CW / 2 - 8)
    right = kv_table([
        ("Best / worst trade", f"{m['best']:+.2f}% / {m['worst']:+.2f}%"),
        ("Max drawdown", f"{m['max_dd']:.1f}%"),
        ("Sharpe (per-trade)", f"{m['sharpe']:.2f}"),
        ("Avg trade duration", f"{m['avg_dur']:.1f} bars"),
        ("Max win / loss streak", f"{m['win_streak']} / {m['loss_streak']}"),
        ("MC  P(profitable)", f"{mc['p_profit']:.0f}%"),
        ("MC  median / 5–95%", f"{mc['p50']:+.1f}%  /  {mc['p5']:+.0f}…{mc['p95']:+.0f}%"),
        ("MC  drawdown (p95)", f"{mc['dd_p95']:.1f}%"),
    ], CW / 2 - 8)
    tt = Table([[left, right]], colWidths=[CW / 2, CW / 2])
    tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (0, 0), 8)]))
    story += [Paragraph("<b>Full statistics</b>", st("fs", fontName="Helvetica-Bold", fontSize=10, textColor=accent)),
              Spacer(1, 4), tt]
    return story


def comparison():
    a, b = D["MMXSKEW"]["metrics"], D["MMXSKEW-ORB"]["metrics"]
    am, bm = D["MMXSKEW"]["mc"], D["MMXSKEW-ORB"]["mc"]
    def pf(x): return "∞" if x == float("inf") else f"{x:.2f}"
    rows = [["Metric", "MMXSKEW (v1.1)", "MMXSKEW-ORB"]]
    rows += [
        ["Trades", str(a["n"]), str(b["n"])],
        ["Win rate", f"{a['win_rate']:.1f}%", f"{b['win_rate']:.1f}%"],
        ["Net profit", f"{a['net_pct']:+.1f}%", f"{b['net_pct']:+.1f}%"],
        ["Avg profit / avg loss", f"{a['avg_win']:+.2f}% / {a['avg_loss']:+.2f}%", f"{b['avg_win']:+.2f}% / {b['avg_loss']:+.2f}%"],
        ["Profit factor", pf(a["profit_factor"]), pf(b["profit_factor"])],
        ["Expectancy / trade", f"{a['expectancy']:+.3f}%", f"{b['expectancy']:+.3f}%"],
        ["Max drawdown", f"{a['max_dd']:.1f}%", f"{b['max_dd']:.1f}%"],
        ["Sharpe (per-trade)", f"{a['sharpe']:.2f}", f"{b['sharpe']:.2f}"],
        ["MC P(profitable)", f"{am['p_profit']:.0f}%", f"{bm['p_profit']:.0f}%"],
        ["MC median 28d return", f"{am['p50']:+.1f}%", f"{bm['p50']:+.1f}%"],
    ]
    t = Table(rows, colWidths=[CW * 0.4, CW * 0.3, CW * 0.3])
    sty = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), white),
           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
           ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (-1, -1), "CENTER"),
           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, LIGHT]), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
           ("TEXTCOLOR", (0, 1), (0, -1), INK)]
    # highlight ORB winner column
    t.setStyle(TableStyle(sty)); return t


def header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(GRAY); canvas.setFont("Helvetica", 7.5)
    canvas.drawString(1.6 * cm, 1.05 * cm, "SMC Quant · MM×Skew strategy report · in-sample, forward-test pending")
    canvas.drawRightString(A4[0] - 1.6 * cm, 1.05 * cm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE); canvas.line(1.6 * cm, 1.35 * cm, A4[0] - 1.6 * cm, 1.35 * cm)
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(os.path.join(OUT, "MMXSKEW_Report.pdf"), pagesize=A4,
                            leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.6 * cm,
                            title="MMXSKEW Strategy Report")
    S = []
    # cover header
    cover = Table([[Paragraph("MM×Skew — Strategy Performance Report", TITLE)],
                   [Paragraph("SOLUSDT · 1h constant-volume · MMXSKEW v1.1 &amp; MMXSKEW-ORB variant", SUB)],
                   [Paragraph(f"Data: {D['MMXSKEW']['first']} → {D['MMXSKEW']['last']} 2026 "
                              f"({D['span']:.1f} days) &nbsp;·&nbsp; RR 1:{D['rr']} &nbsp;·&nbsp; generated {D['generated_utc']}", SUB)]],
                  colWidths=[CW])
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 16),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 16), ("TOPPADDING", (0, 0), (0, 0), 16),
                               ("BOTTOMPADDING", (0, -1), (-1, -1), 14), ("TOPPADDING", (0, 1), (-1, -1), 2)]))
    S += [cover, Spacer(1, 10)]
    warn = Table([[Paragraph("<b>⚠ In-sample results.</b> One 28-day (likely uptrend) regime. MMXSKEW-ORB has only "
                             "11 trades — its Monte Carlo P(profit) is optimistic (it resamples a small, mostly-winning "
                             "set and cannot see regime risk). Treat magnitudes as illustrative; forward tape is the real test.",
                             st("w", fontSize=8.5, textColor=HexColor("#7c2d12"), leading=12))]], colWidths=[CW])
    warn.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor("#fef3c7")), ("BOX", (0, 0), (-1, -1), 0.6, AMBER),
                              ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    S += [warn, Spacer(1, 12)]
    S += strat_section("MMXSKEW", TEAL)
    S += [PageBreak()]
    S += strat_section("MMXSKEW-ORB", GREEN)
    S += [PageBreak(), band("Side-by-side comparison", NAVY), Spacer(1, 8), comparison(), Spacer(1, 12)]
    S += [Paragraph("<b>Methodology &amp; caveats</b>", st("m", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY)),
          Spacer(1, 4),
          Paragraph("All features are causal (known at the signal candle's close); the panel-2 spread was truncation-tested "
                    "(spread[i] independent of all future bars). Exits are fixed SL/TP, first-touch, SL-first on a bar that "
                    "spans both barriers — every trade is a win (TP) or a loss (SL), no breakeven. Sizing: $200k, 10% margin × "
                    "10× leverage = full-balance notional, one position at a time, compounding; 0.08% round-trip fee. Monte Carlo "
                    "= 20,000 bootstrap resamples (return distribution) + 20,000 order permutations (drawdown). "
                    "The delta filter, panel-2 spread edge and ORB timing are validated in-sample / split-half only — the "
                    "sample is small (esp. MMXSKEW-ORB, n=11) and covers one market regime. These are forward-test candidates, "
                    "not proven live edges.", st("mm", fontSize=8.5, textColor=GRAY, leading=12.5))]
    doc.build(S, onFirstPage=header, onLaterPages=header)
    print("wrote", os.path.join(OUT, "MMXSKEW_Report.pdf"))


if __name__ == "__main__":
    build()
