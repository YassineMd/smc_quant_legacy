"""WEIGHT SWEEP — FULL re-derivation (steps 2→3→4) per frame k=1..16.

Per k: features on the k-frame (B-* via compute_bscope sel_len=k; C@k = context over the k-1 priors —
only k=16 equals the registry's literal C.* definition; E*/G* entry-bucket features are k-independent),
bins re-fit on DISCOVERY per side, raw_strength_i = Σ_bins (n_bin/n_total)·|TP%_bin − baseline| (n<100
bins contribute 0), duplicate-value columns deduped per k (groups verified per k), weight_i = rs_i/Σ.
W-STAT exclusion = the POST-AUDIT flag set {level, tempo, clock, warmup-artifact, suspect-regime}
(decomposition_A.decomp_flag).

GATES: k=16 with the OLD flag set must reproduce score_weights.csv (renormalized over the shared roster)
before any other k is trusted. Same episode universe for every k (dataset rows, entries from idx 16).

HONESTY: the holdout is SPENT and now carries 15 extra trials of selection bias from the frame sweep —
every holdout number below is comparison material; forward data is the only judge. Panel unchanged.
"""
import os, sys, csv, json, time, sqlite3, hashlib
from collections import defaultdict
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import features as FT, features_b as FTB, analysis_A, exam, score_core   # noqa: E402
import make_score_bundle as MSB                                          # noqa: E402
from decomposition_A import decomp_flag                                  # noqa: E402
import correlation_A as CA                                               # noqa: E402
from frame_sweep import fit_binner, predict, MIN_BIN                     # noqa: E402
from app.persistence import _bucket_from_dict                            # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
NEW_EXCL = {"level", "tempo", "clock", "warmup-artifact", "suspect-regime"}
OLD_EXCL = {"level", "tempo"}
HOLDOUT_LABEL = ("comparison material — 15 extra trials of selection bias; forward data is the only judge")


def col_digest(s):
    return hashlib.md5(pd.util.hash_pandas_object(s, index=False).values.tobytes()).hexdigest()


def raw_strength(bn, base):
    ntot = sum(bn.n.values())
    if ntot == 0:
        return 0.0
    return sum((bn.n[b] / ntot) * abs(bn.tpr[b] - base)
               for b in bn.n if bn.n[b] >= MIN_BIN and bn.tpr[b] is not None)


def derive(feats_df, roster, masks, tps, bases, excl_flags, flag_fn=decomp_flag):
    """One window's weights: dedup -> per-side binners -> raw_strength -> normalized weight tables.
    Returns {side: {"binners":…, "wall":{f:w}, "wstat":{f:w}, "kept":…, "degen":…}}, alias_groups."""
    disc_mask = masks["disc_any"]
    groups = defaultdict(list)
    for f in roster:
        groups[col_digest(feats_df.loc[disc_mask, f])].append(f)
    alias = {}
    for g in groups.values():
        g = sorted(g)
        for a in g[1:]:
            alias[a] = g[0]
    canon = [f for f in roster if f not in alias]
    out = {}
    for side in ("long", "short"):
        msk = masks[side]; tp = tps[side]; base = bases[side]
        bn_side = {}; rs_side = {}
        for f in canon:
            bn = fit_binner(feats_df.loc[msk, f], tp[msk.values])
            if bn is None:
                continue
            r = raw_strength(bn, base)
            if r > 0:
                bn_side[f] = bn; rs_side[f] = r
        def norm(codes):
            tot = sum(rs_side[f] for f in codes)
            return {f: rs_side[f] / tot for f in codes} if tot > 0 else {}
        wall = norm(list(rs_side))
        wstat = norm([f for f in rs_side if flag_fn(f) not in excl_flags])
        out[side] = dict(binners=bn_side, wall=wall, wstat=wstat, rs=rs_side,
                         kept=len(rs_side), degen=len(canon) - len(rs_side))
    agroups = [frozenset(g) for g in groups.values() if len(g) > 1]
    return out, agroups, alias


def main():
    t0 = time.time()
    db = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    bks = [_bucket_from_dict(d) for d in raw]; snaps = [b.full_snapshot() for b in bks]
    base_id = int(tc[0]) - len(bks)
    rs = FT.repo_series(snaps, bks)

    # W-STAT csv (the frozen original) + the SEL roster derived the same way the bundle derived it
    wcsv = {"long": {}, "short": {}}
    with open(os.path.join(OUT, "score_weights.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(r for r in f if not r.startswith("#")):
            if row["variant"] == "W-STAT":
                wcsv[row["direction"]][row["feature_code"]] = float(row["weight"])
    def sel_ok(f):
        if not score_core.is_sel16(f):
            return False
        if f.startswith("E") and f != "E52.01" and \
                FT.classify_transform(MSB.TEXT.get(f, "")) not in MSB.SAFE_E_KINDS:
            return False
        return True
    roster = sorted(f for f in wcsv["long"] if sel_ok(f))
    print("[%.0fs] roster: %d codes (identical across k; C@k relabel — only k=16 is the registry literal)"
          % (time.time() - t0, len(roster)), flush=True)

    df = pd.read_parquet(os.path.join(OUT, "dataset.parquet"))
    bcodes = [c for c in roster if c.startswith("B-")]
    ccodes = [c for c in roster if c.startswith("C.")]
    ecodes = [c for c in roster if not c.startswith(("B-", "C."))]
    keep = ["bucket_id", "L.06", "L.02"] + [c for c in ecodes if c != "E52.01"] + bcodes + ccodes
    L = df[df["L.01"] == "long"][keep].rename(columns={"L.02": "outL"})
    S = df[df["L.01"] == "short"][["bucket_id", "L.02"]].rename(columns={"L.02": "outS"})
    m = L.merge(S, on="bucket_id").reset_index(drop=True)
    m["E52.01"] = m["bucket_id"].map(analysis_A.large_net_map(db))
    ts = m["L.06"].astype(float); tmin, tmax = df["L.06"].min(), df["L.06"].max()
    tcut = tmin + 0.70 * (tmax - tmin); emb = tcut + 6 * 3600
    m["_win"] = np.where(ts <= tcut, "disc", np.where(ts >= emb, "hold", "emb"))
    m["_j"] = (m["bucket_id"] - base_id - 1).astype(int)
    rL = m["outL"].isin(["TP", "SL"]); rS = m["outS"].isin(["TP", "SL"])
    masks = {"long": (m["_win"] == "disc") & rL, "short": (m["_win"] == "disc") & rS,
             "disc_any": (m["_win"] == "disc")}
    tps = {"long": (m["outL"] == "TP").values, "short": (m["outS"] == "TP").values}
    bases = {"long": 100.0 * tps["long"][masks["long"].values].mean(),
             "short": 100.0 * tps["short"][masks["short"].values].mean()}
    both = rL & rS
    print("[%.0fs] rows=%d (identical universe every k) · baselines L %.2f / S %.2f"
          % (time.time() - t0, len(m), bases["long"], bases["short"]), flush=True)

    # regression: parametrized build_context default == dataset C columns
    md = 0.0
    for j in (200, 3000, 7000):
        got = FT.build_context(snaps, rs, j)
        row = m[m["_j"] == j]
        if len(row):
            r0 = row.iloc[0]
            for c, v in got.items():
                if v is None or isinstance(v, str) or c not in r0 or pd.isna(r0[c]):
                    continue
                md = max(md, abs(float(v) - float(r0[c])))
    print("[%.0fs] build_context default parity vs dataset C.*: max dev %.2e" % (time.time() - t0, md), flush=True)
    assert md < 1e-9

    # ── k=16 PARITY GATE (old flag set; dataset features) ──
    # Two tiers: (a) FULL-PRECISION raw strengths vs the ORIGINAL pipeline recomputed in memory
    # (analysis_A screening bins -> weights_A.raw_strength) — must match < 1e-9; (b) the frozen 4-decimal
    # score_weights.csv within its quantization bound (0.5e-4 amplified by subset renorm 1/Σ ≈ 2.2).
    F16 = m[bcodes + ccodes + [c for c in ecodes]]
    d16, g16, alias16 = derive(F16, roster, masks, tps, bases, OLD_EXCL, flag_fn=CA.flag)   # ORIGINAL flags
    print("\n== k=16 PARITY GATE (old flags {level,tempo}; must reproduce the original pipeline) ==", flush=True)
    import weights_A
    _dfA, _discA, _featsA, _priorsA, outA, _metaA = analysis_A.main()
    for side in ("long", "short"):
        recsA = {r["feature"]: r for r in outA[side]["recs"]}
        baseA = outA[side]["baseline"]
        rs_mine = d16[side]["rs"]
        shared_rs = [f for f in rs_mine if f in recsA and "bins" in recsA[f]]
        dev_rs = max(abs(rs_mine[f] - weights_A.raw_strength(recsA[f], baseA)[0]) for f in shared_rs)
        mine = d16[side]["wstat"]
        shared = [f for f in mine if f in wcsv[side]]
        tot = sum(wcsv[side][f] for f in shared)
        ref = {f: wcsv[side][f] / tot for f in shared}
        dev_csv = max(abs(mine[f] - ref[f]) for f in shared)
        # csv 4dp worst case: |δ_f/Σ − w_f·Σδ/Σ²| ≤ (0.5e-4/Σ)·(1 + N·max_w) — each of the N stored
        # weights carries up to half-ULP rounding, and the renormalization couples them.
        maxw = max(ref.values())
        qbound = (0.5e-4 / max(tot, 1e-9)) * (1.0 + len(shared) * maxw) + 1e-9
        missing = [f for f in mine if f not in wcsv[side]] + [f for f in wcsv[side]
                   if f in rs_mine and f not in mine]
        print("  %-5s full-precision max|Δrs|=%.2e (n=%d)  ·  csv max|Δw|=%.2e (bound %.2e)  ·  unshared=%s"
              % (side, dev_rs, len(shared_rs), dev_csv, qbound, missing or "none"))
        assert dev_rs < 1e-9, "PARITY FAIL (full precision) — do not trust other k"
        assert dev_csv < qbound, "PARITY FAIL (csv beyond quantization bound)"
    print("  PARITY PASS — proceeding.", flush=True)

    # newly-removed by the post-audit flag set (within roster)
    newly = sorted(f for f in roster if decomp_flag(f) in NEW_EXCL and CA.flag(f) not in OLD_EXCL)
    print("\npost-audit flag set newly removes from W-STAT: %s" % (newly or "none"), flush=True)

    # ── the sweep ──
    summary = []
    all_groups = {}
    for k in range(1, 17):
        tk = time.time()
        if k == 16:
            Fk = F16
        else:
            rows = np.empty((len(m), len(bcodes) + len(ccodes)), dtype=object)
            for ri, j in enumerate(m["_j"].values):
                bs = FTB.compute_bscope(snaps, rs, int(j), sel_len=k)
                cx = FT.build_context(snaps, rs, int(j), n_ctx=k - 1)
                rows[ri] = [bs.get(c) for c in bcodes] + [cx.get(c) for c in ccodes]
            Fk = pd.DataFrame(rows, columns=bcodes + ccodes, index=m.index)
            for c in Fk.columns:
                try:
                    Fk[c] = pd.to_numeric(Fk[c])
                except (ValueError, TypeError):
                    pass
            Fk = pd.concat([Fk, m[ecodes]], axis=1)
        dk, gk, aliask = derive(Fk, roster, masks, tps, bases, NEW_EXCL)
        all_groups[k] = set(gk)
        # per-k weight CSV (both variants)
        path = os.path.join(OUT, "score_weights_k%d.csv" % k)
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("# SEL-k=%d full re-derivation (discovery-only fit). C@k = context over k-1 priors "
                    "(registry-literal only at k=16). W-STAT excl = post-audit flag set. HOLDOUT numbers "
                    "elsewhere are %s.\n" % (k, HOLDOUT_LABEL))
            w = csv.writer(f)
            w.writerow(["variant", "direction", "rank", "feature_code", "flag", "weight",
                        "cumulative_weight", "raw_strength", "deduped_aliases"])
            sums = {}
            for variant in ("W-ALL", "W-STAT"):
                for side in ("long", "short"):
                    tab = dk[side]["wall" if variant == "W-ALL" else "wstat"]
                    cum = 0.0
                    rev_alias = defaultdict(list)
                    for a, c in aliask.items():
                        rev_alias[c].append(a)
                    for rk, (fc, wt) in enumerate(sorted(tab.items(), key=lambda kv: -kv[1]), 1):
                        cum += wt
                        w.writerow([variant, side, rk, fc, decomp_flag(fc), "%.4f" % wt, "%.4f" % cum,
                                    "%.4f" % dk[side]["rs"][fc], ",".join(rev_alias.get(fc, []))])
                    sums[(variant, side)] = sum(tab.values())
        sum_ok = all(abs(v - 1.0) < 1e-9 for v in sums.values())
        # score + calibration + holdout comparison (new W-STAT weights)
        pl = predict(Fk, dk["long"]["binners"], dk["long"]["wstat"], bases["long"])
        ps = predict(Fk, dk["short"]["binners"], dk["short"]["wstat"], bases["short"])
        gap = (pl - bases["long"]) - (ps - bases["short"])
        cal = {}
        for side, pred, tp in (("L", pl, tps["long"]), ("S", ps, tps["short"])):
            mm = masks["long" if side == "L" else "short"].values
            q = pd.qcut(pd.Series(pred[mm]), 10, labels=False, duplicates="drop")
            cal[side] = [round(100.0 * tp[mm][(q == d).values].mean()) for d in sorted(q.dropna().unique())]
        rec = {"k": k, "sum_ok": sum_ok,
               "kept_L": dk["long"]["kept"], "degen_L": dk["long"]["degen"],
               "kept_S": dk["short"]["kept"], "degen_S": dk["short"]["degen"],
               "alias_groups": len(gk)}
        for win in ("disc", "hold"):
            mm = (both & (m["_win"] == win)).values
            sel_tp = np.where(gap[mm] >= 0, tps["long"][mm], tps["short"][mm])
            aL, aS = 100.0 * tps["long"][mm].mean(), 100.0 * tps["short"][mm].mean()
            st = 100.0 * sel_tp.mean()
            rec.update({win + "_selTP": st, win + "_delta": st - max(aL, aS), win + "_n": int(mm.sum())})
        summary.append(rec)
        print("[%.0fs] k=%-2d Σ=1:%s kept L/S %d/%d (degen %d/%d) aliases=%d | cal L D1→D10 %s | "
              "disc %.1f (Δ%+.1f) | HOLD* %.1f (Δ%+.1f) (%.0fs)"
              % (time.time() - t0, k, "OK" if sum_ok else "FAIL", rec["kept_L"], rec["kept_S"],
                 rec["degen_L"], rec["degen_S"], rec["alias_groups"],
                 "%d→%d" % (cal["L"][0], cal["L"][-1]) if cal["L"] else "-",
                 rec["disc_selTP"], rec["disc_delta"], rec["hold_selTP"], rec["hold_delta"],
                 time.time() - tk), flush=True)

    # alias-group diffs vs k=16
    print("\nalias-group check vs k=16:")
    for k in range(1, 17):
        diff = all_groups[k] ^ all_groups[16]
        if diff:
            print("  k=%d groups differ: %s" % (k, [sorted(g) for g in diff]))
    if all(all_groups[k] == all_groups[16] for k in range(1, 17)):
        print("  identical dedup groups at every k")

    with open(os.path.join(OUT, "weight_sweep_summary.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# Full weight re-derivation per k. HOLD* = %s\n" % HOLDOUT_LABEL)
        cols = ["k", "sum_ok", "kept_L", "degen_L", "kept_S", "degen_S", "alias_groups",
                "disc_n", "disc_selTP", "disc_delta", "hold_n", "hold_selTP", "hold_delta"]
        w = csv.writer(f); w.writerow(cols)
        for r in summary:
            w.writerow([("%.2f" % r[c]) if isinstance(r[c], float) else r[c] for c in cols])
    print("\nwrote weight_sweep_summary.csv + score_weights_k1..16.csv  ·  HOLD* = " + HOLDOUT_LABEL)


if __name__ == "__main__":
    main()
