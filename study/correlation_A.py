"""Discovery add-on (Part A scope): per-feature correlation-with-winning, LONG & SHORT.

Discovery slice ONLY (same 70% cut + embargo); holdout SEALED. Univariate, no models.
- numeric   -> point-biserial Pearson r x 100 (SIGNED: + = higher value wins more)
- categorical/flag -> Cramer's V x 100 (UNSIGNED 0-100 association strength; type=cat)
Rows overlap heavily, so magnitudes read small (a ~10pp win-rate gap ~ r 8-10%); the RANKING is the signal.
"""
import os, sys, csv, math
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis_A
from report_A import is_level

REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "study", "out")
CONTRACT = os.path.join(REPO, "study", "data", "column_contract.tsv")

TEXT = {}
with open(CONTRACT, encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        TEXT[row["code"]] = row["text"]

NOTE = ("# NOTE: rows overlap heavily (episodes share buckets) -> correlation magnitudes read SMALL "
        "(a ~10pp win-rate gap is roughly r = 8-10%). The RANKING is the meaningful part, not the absolute "
        "size. level/tempo artifact flags still apply. Discovery slice only; HOLDOUT SEALED.")


def tempo(f):
    return f.startswith(("E31.", "E29.", "G08.", "E11.")) or f in ("B-S.02", "B-S.13")


def flag(f):
    return "level" if is_level(f) else ("tempo" if tempo(f) else "")


def plain(f):
    if f == "E52.01":
        return "large-order net side (APPROX fixed-273c)"
    return TEXT.get(f, f)


def point_biserial(x, y):
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def cramers_v(cat, y):
    ct = pd.crosstab(pd.Series(cat), pd.Series(y))
    if ct.shape[0] < 2 or ct.shape[1] < 2:
        return 0.0
    obs = ct.values.astype(float); n = obs.sum()
    exp = obs.sum(1, keepdims=True) * obs.sum(0, keepdims=True) / n
    chi2 = ((obs - exp) ** 2 / exp).sum()
    return float(math.sqrt(chi2 / n / (min(obs.shape) - 1)))


def main():
    df, disc, feats, priors, out, meta = analysis_A.main()
    for d in ("long", "short"):
        dd = disc[(disc["L.01"] == d) & (disc["L.02"].isin(["TP", "SL"]))].copy()
        win = (dd["L.02"] == "TP").astype(int).values
        ntot = len(dd)
        eff = {r["feature"]: r.get("effect") for r in out[d]["recs"]}
        rows = []
        for f in feats:
            s = dd[f]; mask = s.notna().values; sdrop = dd[f].dropna()
            n = int(mask.sum()); cov = 100.0 * n / ntot
            y = win[mask]; fl = flag(f)
            if analysis_A.is_categorical(dd[f], sdrop):
                typ = "cat"
                k = sdrop.nunique()
                if k > 30 or k > 0.2 * max(1, n):          # ID-like string (e.g. B-S.04 "sell|buy") -> not a feature
                    corr = float("nan"); fl = "degenerate"
                else:
                    corr = 100.0 * cramers_v(s[mask].astype(str).values, y)
            else:
                corr = 100.0 * point_biserial(s[mask].astype(float).values, y); typ = "num"
            e = eff.get(f)
            rows.append(dict(feature=f, plain=plain(f), type=typ, corr=corr, n=n, cov=cov,
                             effect=("" if e is None or (isinstance(e, float) and math.isnan(e)) else round(e, 1)),
                             flag=fl))
        rows.sort(key=lambda r: (-abs(r["corr"])) if r["corr"] == r["corr"] else 1e9)
        path = os.path.join(OUT, "correlation_%s.csv" % d)
        with open(path, "w", newline="", encoding="utf-8") as fo:
            fo.write(NOTE + "\n")
            w = csv.writer(fo)
            w.writerow(["feature_code", "plain_name", "type", "corr_pct", "n", "coverage_pct", "effect_pp", "flag"])
            for r in rows:
                cp = "" if r["corr"] != r["corr"] else round(r["corr"], 2)
                w.writerow([r["feature"], r["plain"], r["type"], cp, r["n"],
                            round(r["cov"], 1), r["effect"], r["flag"]])
        print("\nwrote %s" % os.path.relpath(path, REPO))
        print(NOTE)
        print("=== TOP 30 %s (baseline %.1f%%) — |corr%%| desc ===" % (d.upper(), out[d]["baseline"]))
        print("  %-9s %-4s %7s %6s %7s %-6s  %s" % ("feature", "type", "corr%", "n", "eff_pp", "flag", "name"))
        for r in rows[:30]:
            print("  %-9s %-4s %+7.2f %6d %7s %-6s  %s"
                  % (r["feature"], r["type"], r["corr"], r["n"], str(r["effect"]), r["flag"], r["plain"][:46]))


if __name__ == "__main__":
    main()
