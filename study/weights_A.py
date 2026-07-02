"""Exam-v2 STEP 1A — FROZEN evidence-score weight tables. Discovery slice ONLY, holdout SEALED.

raw_strength_i = Σ_bins (n_bin / n_total) · |TP%_bin − baseline|   (discovery bins; bins with n<100 -> 0;
sz features on sz-present rows only; duplicate-value columns deduped, counted once). weight_i = raw_strength_i
/ Σ raw_strength. Four tables: {LONG,SHORT} × {W-ALL, W-STAT}; W-STAT drops flag ∈ {level,tempo}.
NO re-tuning downstream — the exam consumes these verbatim.
"""
import os, sys, csv, hashlib
from collections import defaultdict
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis_A
import correlation_A as C          # flag(), plain(), TEXT  (no side effects on import)
try:
    sys.stdout.reconfigure(encoding="utf-8")   # allow the Σ sum-check line on Windows consoles
except Exception:
    pass

REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "study", "out")
MIN_BIN = 100                      # bins with n < this contribute 0 to raw_strength


def col_digest(s):
    return hashlib.md5(pd.util.hash_pandas_object(s, index=False).values.tobytes()).hexdigest()


def raw_strength(rec, baseline):
    if not rec or "bins" not in rec:
        return 0.0, 0
    bins = rec["bins"]; ntot = sum(b["n"] for b in bins.values())
    if ntot == 0:
        return 0.0, 0
    rs = 0.0
    for b in bins.values():
        if b["n"] >= MIN_BIN and b["tpr"] == b["tpr"]:
            rs += (b["n"] / ntot) * abs(b["tpr"] - baseline)
    return rs, ntot


def main():
    df, disc, feats, priors, out, meta = analysis_A.main()

    # ── dedup duplicate-value columns (global on discovery) ──
    byd = defaultdict(list)
    for f in feats:
        byd[col_digest(disc[f])].append(f)
    alias_of = {}; aliases = {}
    for grp in byd.values():
        g = sorted(grp); rep = g[0]
        aliases[rep] = g[1:]
        for a in g[1:]:
            alias_of[a] = rep
    canon = [f for f in feats if f not in alias_of]
    ndupe = sum(len(v) for v in aliases.values())

    recs = {d: {r["feature"]: r for r in out[d]["recs"]} for d in ("long", "short")}
    tables = {}
    for d in ("long", "short"):
        base = out[d]["baseline"]
        strengths = {}
        for f in canon:
            rs, ntot = raw_strength(recs[d].get(f), base)
            if rs > 0:
                strengths[f] = (rs, ntot)
        for variant in ("W-ALL", "W-STAT"):
            pool = {f: v for f, v in strengths.items()
                    if variant == "W-ALL" or C.flag(f) not in ("level", "tempo")}
            tot = sum(v[0] for v in pool.values())
            ranked = sorted(pool.items(), key=lambda kv: -kv[1][0])
            cum = 0.0; rows = []
            for i, (f, (rs, ntot)) in enumerate(ranked, 1):
                w = rs / tot; cum += w
                e = recs[d][f].get("effect")
                rows.append(dict(rank=i, feature=f, plain=C.plain(f), flag=C.flag(f),
                                 weight=w, cum=cum, effect=("" if e is None or e != e else round(e, 1)),
                                 n=ntot, aliases=",".join(aliases.get(f, []))))
            tables[(d, variant)] = rows

    # ── write CSV (all four tables, long format) ──
    path = os.path.join(OUT, "score_weights.csv")
    with open(path, "w", newline="", encoding="utf-8") as fo:
        fo.write("# FROZEN weights — exam v2 STEP 1A. The exam uses these tables VERBATIM; no re-tuning.\n")
        fo.write("# raw_strength = Sum_bins (n_bin/n_total)*|TP%%_bin - baseline|; bins n<%d -> 0; sz on "
                 "sz-present rows; duplicate-value columns deduped (%d aliases folded). Discovery slice; "
                 "HOLDOUT SEALED.\n" % (MIN_BIN, ndupe))
        w = csv.writer(fo)
        w.writerow(["variant", "direction", "rank", "feature_code", "plain_name", "flag",
                    "weight", "cumulative_weight", "effect_pp", "n", "deduped_aliases"])
        for (d, variant), rows in tables.items():
            for r in rows:
                w.writerow([variant, d, r["rank"], r["feature"], r["plain"], r["flag"],
                            "%.4f" % r["weight"], "%.4f" % r["cum"], r["effect"], r["n"], r["aliases"]])
    print("wrote %s  (deduped %d alias columns)" % (os.path.relpath(path, REPO), ndupe))

    # ── print top 25 of each table + sum-check ──
    for d in ("long", "short"):
        for variant in ("W-ALL", "W-STAT"):
            rows = tables[(d, variant)]
            tot = sum(r["weight"] for r in rows)
            print("\n=== %s × %s  (n_features=%d) ===" % (d.upper(), variant, len(rows)))
            print("  %-4s %-9s %8s %8s %6s %-6s  %s" % ("rk", "feature", "weight", "cum", "eff", "flag", "name"))
            for r in rows[:25]:
                print("  %-4d %-9s %8.4f %8.4f %6s %-6s  %s"
                      % (r["rank"], r["feature"], r["weight"], r["cum"], str(r["effect"]),
                         r["flag"], r["plain"][:42]))
            print("  Σ = %.4f" % tot)


if __name__ == "__main__":
    main()
