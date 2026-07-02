"""DECOMPOSITION — pure direction vs shared movement. FULL 4 days (holdout spent-as-test, now material).
Univariate tables only; no verdicts, no models, no weight changes.

Per feature, per bin (FROZEN discovery edges/categories, both-resolved buckets, n>=100):
  excess_diff = (TP%_long - base_L) - (TP%_short - base_S)   # subtract the shared period-drift constant
  shared      = (TP%_long - base_L) + (TP%_short - base_S)   # both-sides lift = movement/anti-chop
base_L/base_S = full-4-day per-direction baselines. Direction list ranks max|excess_diff|; Movement list
ranks max shared. Block-bootstrap 90% CI (3h blocks, full span) on each top-20 headline.
"""
import os, sys, csv, json, sqlite3
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import analysis_A, exam, correlation_A as C
REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
BLOCK_S = 3 * 3600; R = 1000; MINBIN = 100
NOTE = ("# NOTE: flag=empty is necessary but not sufficient — apply the clock/volume/price test when reading. "
        "The excess_diff subtraction AMPLIFIES drift-trackers (regime/time/tempo/level), it does not clean them.")


def decomp_flag(f):
    """Audit-driven flag backfill (Part-A flags missed newer offenders); persisted so future runs keep them."""
    if f.startswith(("E01.", "E02.")):
        return "clock"                       # UTC hour-of-day = the session clock, not a signal
    if f.startswith("E32.") or f == "B-S.03":
        return "tempo"                       # adaptive target_vol size / selection total volume = volume regime
    if f == "E55.09":
        return "warmup-artifact"             # percentile-window fill health = a warmup proxy, not a market read
    if f == "B-S.10":
        return "suspect-regime"              # Sum ClS — regime-correlated, treat with suspicion
    return C.flag(f)                          # else the Part-A level/tempo flag


def load():
    df, disc, feats, priors, out, meta = analysis_A.main()
    big = analysis_A.large_net_map(os.path.join(REPO, "study", "data", "history_snapshot_20260702.db"))
    df = df.copy(); df["E52.01"] = df["bucket_id"].map(big)
    binners = exam.fit_binners(disc, out, feats)
    ts = df["L.06"].astype(float); tmin = ts.min()
    df["_blk"] = ((ts - tmin) // BLOCK_S).astype(int)
    L = df[df["L.01"] == "long"]
    Scols = ["bucket_id", "L.02", "L.08", "_blk"]
    S = df[df["L.01"] == "short"][["bucket_id", "L.02"]].rename(columns={"L.02": "L.02_S"})
    m = L.merge(S, on="bucket_id")
    m = m[m["L.02"].isin(["TP", "SL"]) & m["L.02_S"].isin(["TP", "SL"])].reset_index(drop=True)
    return m, feats, binners


def bin_stats(m, lab, base_L, base_S):
    lt = (m["L.02"] == "TP").values; st = (m["L.02_S"] == "TP").values
    wh = (m["L.08"] == "WHIPSAW").values
    rows = {}
    for b in pd.unique(lab.dropna()):
        idx = (lab == b).values; n = int(idx.sum())
        if n < MINBIN:
            continue
        tL = 100.0 * lt[idx].mean(); tS = 100.0 * st[idx].mean()
        rows[b] = dict(n=n, TP_L=tL, TP_S=tS, whip=100.0 * wh[idx].mean(),
                       raw=tL - tS, excess=(tL - base_L) - (tS - base_S), shared=(tL - base_L) + (tS - base_S))
    return rows


def boot(m, lab, b, kind, R=R, seed=11):
    rng = np.random.default_rng(seed)
    blocks = m["_blk"].unique(); B = len(blocks)
    by = {x: np.where(m["_blk"].values == x)[0] for x in blocks}
    lt = (m["L.02"] == "TP").values; st = (m["L.02_S"] == "TP").values; inb = (lab == b).values
    vals = []
    for _ in range(R):
        p = np.concatenate([by[x] for x in rng.choice(blocks, B, replace=True)])
        bL = 100.0 * lt[p].mean(); bS = 100.0 * st[p].mean()
        mm = inb[p]
        if mm.sum() < 20:
            continue
        tL = 100.0 * lt[p][mm].mean(); tS = 100.0 * st[p][mm].mean()
        vals.append(((tL - bL) - (tS - bS)) if kind == "excess" else ((tL - bL) + (tS - bS)))
    if len(vals) < 50:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 5)), float(np.percentile(vals, 95))


def main():
    m, feats, binners = load()
    base_L = 100.0 * (m["L.02"] == "TP").mean(); base_S = 100.0 * (m["L.02_S"] == "TP").mean()
    print("full-4-day both-resolved buckets: %d | base_L %.2f  base_S %.2f | 3h-blocks %d"
          % (len(m), base_L, base_S, m["_blk"].nunique()))
    dir_rows, mov_rows = [], []
    for f in feats:
        bn = binners.get((f, "long"))
        if bn is None:
            continue
        lab = bn.labels(m[f])
        st = bin_stats(m, lab, base_L, base_S)
        if not st:
            continue
        bd = max(st, key=lambda b: abs(st[b]["excess"]))
        bm = max(st, key=lambda b: st[b]["shared"])
        base = dict(feature=f, plain=C.plain(f), flag=decomp_flag(f))
        eb_d = int(m.loc[(lab == bd).values, "_blk"].nunique()); eb_m = int(m.loc[(lab == bm).values, "_blk"].nunique())
        dir_rows.append({**base, "bin": bd, **st[bd], "eff_blocks": eb_d, "_lab": lab})
        mov_rows.append({**base, "bin": bm, **st[bm], "eff_blocks": eb_m, "_lab": lab})
    dir_rows.sort(key=lambda r: -abs(r["excess"]))
    mov_rows.sort(key=lambda r: -r["shared"])

    for rows, kind, headline in ((dir_rows, "excess", "excess"), (mov_rows, "shared", "shared")):
        for i, r in enumerate(rows):
            if i < 20:
                r["ci_lo"], r["ci_hi"] = boot(m, r["_lab"], r["bin"], kind)
            else:
                r["ci_lo"], r["ci_hi"] = "", ""

    cols = ["rank", "feature_code", "plain_name", "flag", "best_bin", "n", "TP_L", "TP_S", "whip_pct",
            "raw_diff", "excess_diff", "shared", "ci_lo", "ci_hi", "eff_blocks"]
    def write(path, rows):
        with open(path, "w", newline="", encoding="utf-8") as fo:
            fo.write(NOTE + "\n")
            w = csv.writer(fo); w.writerow(cols)
            for i, r in enumerate(rows, 1):
                w.writerow([i, r["feature"], r["plain"], r["flag"], r["bin"], r["n"],
                            round(r["TP_L"], 1), round(r["TP_S"], 1), round(r["whip"], 1), round(r["raw"], 1),
                            round(r["excess"], 2), round(r["shared"], 2),
                            (round(r["ci_lo"], 2) if isinstance(r["ci_lo"], float) else ""),
                            (round(r["ci_hi"], 2) if isinstance(r["ci_hi"], float) else ""), r["eff_blocks"]])
    write(os.path.join(OUT, "decomposition_direction.csv"), dir_rows)
    write(os.path.join(OUT, "decomposition_movement.csv"), mov_rows)
    print("wrote decomposition_direction.csv (%d), decomposition_movement.csv (%d)" % (len(dir_rows), len(mov_rows)))

    def show(title, rows, val, note):
        print("\n=== %s — top 20 (%s) ===" % (title, note))
        print("  %-9s %-6s %-16s %6s %6s %6s %6s  %-14s  %s"
              % ("feat", "flag", "best_bin", "TP_L", "TP_S", "whip", "n", val, "name"))
        for i, r in enumerate(rows[:20], 1):
            v = r["excess"] if val == "excess" else r["shared"]
            ci = ("[%+.1f,%+.1f]" % (r["ci_lo"], r["ci_hi"])) if isinstance(r["ci_lo"], float) else ""
            print("  %-9s %-6s %-16s %6.1f %6.1f %6.1f %6d  %+6.2f %-8s %s"
                  % (r["feature"], r["flag"] or "-", str(r["bin"])[:16], r["TP_L"], r["TP_S"], r["whip"],
                     r["n"], v, ci, r["plain"][:34]))
    show("PURE-DIRECTION", dir_rows, "excess", "max |excess_diff| = long-lean(+) / short-lean(-); flags AMPLIFIED by subtraction")
    show("MOVEMENT", mov_rows, "shared", "max shared = both-sides lift; low whip% = anti-chop candidates")


if __name__ == "__main__":
    main()
