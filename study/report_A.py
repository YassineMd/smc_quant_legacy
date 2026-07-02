"""Render Part-A discovery outputs: analysis_report_A_discovery.md + screening_{long,short}.csv."""
import os, csv, math, datetime as dt
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "study", "out")
NULL_GEOM, BREAKEVEN = 37.5, 48.8


def is_level(f):
    """Non-stationary ABSOLUTE price-level features: they proxy calendar/regime (price drifts across the
    window), not a tradeable entry signal — flag despite high effect."""
    return f.startswith(("E04.", "E09.", "K.01", "K.02", "K.03", "K.10"))


def _rank(recs):
    def key(r):
        e = r.get("effect", np.nan)
        return -abs(e) if (e is not None and not (isinstance(e, float) and math.isnan(e))) else 1e9
    ranked = sorted(recs, key=key)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def _fx(v, nd=1):
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else ("%.*f" % (nd, v))


def _csv(path, ranked):
    cols = ["rank", "feature", "family", "approx", "kind", "n_cats", "n_resolved", "coverage",
            "top_bin", "n_top", "tpr_top", "bot_bin", "n_bot", "tpr_bot",
            "effect", "ci_lo", "ci_hi", "eff_blocks", "whipf_top", "whipf_bot", "baseline", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        for r in ranked:
            w.writerow([r.get(c, "") if not isinstance(r.get(c), float) else _fx(r.get(c), 3) for c in cols])


def _prior_block(L, r):
    if r is None or "bins" not in r:
        L.append("_not screenable (%s)_\n" % (r.get("note", "n/a") if r else "missing")); return
    L.append("| bin | n | TP%% | whip%% |\n|---|---|---|---|")
    for b in r["blabels"]:
        st = r["bins"][b]
        tag = " ◄top" if b == r["top_bin"] else (" ◄bot" if b == r["bot_bin"] else "")
        L.append("| %s%s | %d | %s | %s |" % (b, tag, st["n"], _fx(st["tpr"]), _fx(st["whipf"])))
    L.append("\n**effect (top−bottom) = %s pp**  ·  90%% CI [%s, %s]  ·  eff-blocks %s  ·  rank %d/%d%s\n"
             % (_fx(r["effect"]), _fx(r["ci_lo"]), _fx(r["ci_hi"]), r["eff_blocks"], r["rank"],
                r["_ntot"], ("  ·  " + r["approx"] if r["approx"] else "")))


def build(df, disc, feats, priors, out, meta):
    for d in ("long", "short"):
        out[d]["ranked"] = _rank(out[d]["recs"])
        for r in out[d]["ranked"]:
            r["_ntot"] = len(out[d]["ranked"])
        _csv(os.path.join(OUT, "screening_%s.csv" % d), out[d]["ranked"])

    L = []
    L.append("# Barrier Study — Analysis Part A: univariate discovery screen\n")
    L.append("_Generated %s UTC · DISCOVERY only · **HOLDOUT SEALED** (no statistic computed on it) · "
             "univariate, no models._\n" % dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))

    L.append("\n## Method\n")
    L.append("- Winners = outcome **TP**, losers = **SL** (UNRESOLVED dropped). Whipsaw buckets are SL on both "
             "directions (correct). Per direction, per entry-legal computed feature.\n")
    L.append("- **Effect size** = TP-rate gap, top-vs-bottom bin. Numeric → quartiles (Q4 vs Q1, directional); "
             "categorical/flag → best-vs-worst category (max−min spread, upward-biased for high cardinality).\n")
    L.append("- **Uncertainty** = block bootstrap over **3h TIME blocks** (%d reps, 90%% CI). Rows overlap "
             "heavily, so BLOCKS are resampled, never rows. Effective-block count reported with every CI.\n" % 1000)
    L.append("- **Reference lines** (every table): direction discovery baseline · **%.1f%% geometric null** · "
             "**%.1f%% fee breakeven**.\n" % (NULL_GEOM, BREAKEVEN))
    L.append("- **Leakage guard:** features restricted to E*/G*/C.*/K.*/B-* by prefix whitelist; J-/X-/O.*/L.* "
             "never enter as features. The clean-reverse/whipsaw loser split uses L.08 only to *describe* the "
             "loss, never as a predictor.\n")

    span = (meta["tmax"] - meta["tmin"]) / 86400
    L.append("\n## Data health\n")
    L.append("| item | value |\n|---|---|")
    L.append("| total span | %.2f days |" % span)
    L.append("| discovery rows (≤70%% time) | %d |" % meta["n_disc"])
    L.append("| holdout rows (SEALED, ≥cut+6h) | %d — not inspected |" % meta["n_holdout"])
    for d in ("long", "short"):
        o = out[d]
        L.append("| %s discovery | TP %d / SL %d · baseline **%.1f%%** · %d 3h-blocks |"
                 % (d, o["tp"], o["sl"], o["baseline"], o["B"]))
    sz_cov = {d: int(disc[(disc["L.01"] == d)]["E52.01"].notna().sum()) for d in ("long", "short")}
    L.append("| E52.01 sz-present rows (long/short) | %d / %d — ~1.3 discovery days |"
             % (sz_cov["long"], sz_cov["short"]))
    deg = {d: sum(1 for r in out[d]["recs"] if r.get("note")) for d in ("long", "short")}
    L.append("| features screened | %d (degenerate/low-n long %d, short %d) |"
             % (len(feats), deg["long"], deg["short"]))
    L.append("\n> **sz caveat:** E52.01 (and E50/E51) are trade-size features that began 2026-06-30; per the "
             "coverage rule they are screened ONLY on sz-present rows (~1.3 discovery days), so absence isn't "
             "confused with signal. They are under-powered here and get their real test in forward confirmation.\n")

    for d in ("long", "short"):
        o = out[d]
        L.append("\n## %s — top 15 by |effect size|\n" % d.upper())
        L.append("_baseline %.1f%% · null %.1f%% · breakeven %.1f%%_\n" % (o["baseline"], NULL_GEOM, BREAKEVEN))
        L.append("| # | feature | kind | top bin (TP%% , n) | bottom bin (TP%% , n) | effect pp | 90% CI | blk | flag |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        shown = [r for r in o["ranked"] if "bins" in r][:15]
        for r in shown:
            L.append("| %d | %s | %s | %s (%s, %d) | %s (%s, %d) | **%s** | [%s, %s] | %d | %s |"
                     % (r["rank"], r["feature"], r["kind"], r["top_bin"], _fx(r["tpr_top"]), r["n_top"],
                        r["bot_bin"], _fx(r["tpr_bot"]), r["n_bot"], _fx(r["effect"]),
                        _fx(r["ci_lo"]), _fx(r["ci_hi"]), r["eff_blocks"],
                        ("⚠level " if is_level(r["feature"]) else "")
                        + (r["approx"] or ("cat×%d" % r["n_cats"] if r["kind"] == "cat" else ""))))

    L.append("\n## Pre-registered trader priors (shown regardless of rank)\n")
    lookup = {d: {r["feature"]: r for r in out[d]["ranked"]} for d in ("long", "short")}
    names = {"B-P1.03": "absorption spread", "B-P1.04": "absorption dominant side",
             "B-P2.03": "eff-agg spread", "B-P4.02": "last-exhausted side",
             "E60.01": "12-state verdict at entry", "C.01": "15-candle context direction",
             "C.02": "15-candle context %chg", "E52.01": "large-order net side APPROX(fixed-273c)"}
    for p in priors:
        L.append("\n### %s — `%s`\n" % (names.get(p, p), p))
        for d in ("long", "short"):
            L.append("**%s** (baseline %.1f%% · null %.1f%% · breakeven %.1f%%):\n"
                     % (d, out[d]["baseline"], NULL_GEOM, BREAKEVEN))
            _prior_block(L, lookup[d].get(p))

    L.append("\n## Caveats\n")
    L.append("1. **Wide CIs by construction** — only ~%d–%d independent 3h blocks; treat point effects as "
             "screening signal, not proof. The holdout is the real test.\n" % (out["long"]["B"], out["short"]["B"]))
    L.append("2. **Multiple comparisons** — %d features screened; high-cardinality categoricals (e.g. E60.01 "
             "12-state) use a best-vs-worst spread that is upward-biased. Ranking is for shortlisting only.\n" % len(feats))
    L.append("3. **sz under-power** — E52.01/E50*/E51* rest on ~1.3 discovery days (post-2026-06-30).\n")
    L.append("4. **⚠ Non-stationary LEVEL features are regime confounds, not signals** — absolute price levels "
             "(E04.* close, E09.* POC, K.01/K.02/K.03/K.10 KC bands & rolling-POC) rank high only because price "
             "trended across the 4-day window, so 'price bin' proxies the calendar. They are tagged `⚠level` and "
             "should be **discounted** as entry features regardless of effect size. Relatedly, the market-**tempo** "
             "axis (E31 avg-velocity, B-S.02 duration, E29 vel, G08.1, E11 volume) flips sign long↔short — a "
             "directional-**regime** read, not a per-side edge; only the holdout can tell regime-fit from signal.\n")
    L.append("5. **Genuinely stationary candidates** to weigh for the shortlist: the flow/structure features that "
             "are ratios/shares/states/flags (B-P3.08 both-hot E/R, C.08 context-state fade, the absorption/eff-agg "
             "spreads, exhaustion side) — comparable across time by construction.\n")
    L.append("6. **Holdout sealed** — no statistic here touched the last 30%%; it unseals once, after the "
             "architect + Yassine register the shortlist.\n")
    L.append("\n**HARD STOP — discovery screen only. No holdout, no models, no multivariate fits.**\n")

    rp = os.path.join(OUT, "analysis_report_A_discovery.md")
    open(rp, "w", encoding="utf-8").write("\n".join(L))
    print("wrote %s" % os.path.relpath(rp, REPO))
    for d in ("long", "short"):
        print("wrote study/out/screening_%s.csv (%d features)" % (d, len(out[d]["ranked"])))
    # console headline
    for d in ("long", "short"):
        top = [r for r in out[d]["ranked"] if "bins" in r][:5]
        print("\n%s baseline=%.1f%% | top5:" % (d.upper(), out[d]["baseline"]))
        for r in top:
            print("  %-9s effect=%+.1fpp CI[%s,%s] top=%s(%.1f) bot=%s(%.1f) blk=%d %s"
                  % (r["feature"], r["effect"], _fx(r["ci_lo"]), _fx(r["ci_hi"]),
                     r["top_bin"], r["tpr_top"], r["bot_bin"], r["tpr_bot"], r["eff_blocks"], r["approx"]))
