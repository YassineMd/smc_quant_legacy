"""FRAME SWEEP — SEL-k for k=1..16: the SEL16 recipe with the frame length as the ONLY knob.

Per k: B-panel features recomputed on [i-k+1, i] (features_b.compute_bscope sel_len=k, global rs like the
exam); entry-bucket E/G features are k-independent (from dataset.parquet); C.* exists only for k=16.
Bins re-derived on the DISCOVERY slice per k (same qcut recipe as the exam); W-STAT weights reused with
per-row renormalization (predict divides by contributing weight — NO re-tuning of relative weights).

HONESTY: the holdout is SPENT — it is used here as learning material only. Picking the best of 15 on ~8
independent 3h blocks is selection-biased BY CONSTRUCTION; the winner is a v2 hypothesis whose real test
is the forward log / future snapshots, not these numbers.
"""
import os, sys, csv, json, time, sqlite3
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import features as FT, features_b as FTB, analysis_A, exam           # noqa: E402
from app.persistence import _bucket_from_dict                         # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
R_TP, FEE = 1.667, 0.3
MIN_BIN = 100


def expectancy(p):
    p = p / 100.0
    return p * R_TP - (1 - p) - FEE


def fit_binner(col, tp):
    """Discovery-fit binner for ONE feature on ONE side (same recipe as exam.fit_binners): quartile edges /
    categories from the side's resolved discovery rows; per-bin TP%/n from the same rows."""
    s = col.dropna()
    if s.empty or s.nunique() < 2:
        return None
    if analysis_A.is_categorical(col, s):
        cats = set(str(x) for x in s.unique())
        lab = col.map(lambda v: (str(v) if (not pd.isna(v) and str(v) in cats) else None))
        kind, spec = "cat", cats
    else:
        try:
            _, edges = pd.qcut(s, 4, retbins=True, duplicates="drop")
        except Exception:
            return None
        if len(edges) < 3:
            return None
        idx = pd.cut(col, edges, labels=False, include_lowest=True)
        lab = idx.map(lambda v: None if pd.isna(v) else "Q%d" % (int(v) + 1))
        kind, spec = "num", edges
    tpr, n = {}, {}
    for b in pd.unique(lab.dropna()):
        m = (lab == b).values
        n[b] = int(m.sum()); tpr[b] = 100.0 * tp[m].mean() if n[b] else None
    return exam.Binner(kind, spec, tpr, n)


def predict(frame, binners, weights, base):
    num = np.zeros(len(frame)); den = np.zeros(len(frame))
    for f, w in weights.items():
        bn = binners.get(f)
        if bn is None or f not in frame.columns:
            continue
        lab = bn.labels(frame[f]); tpr = lab.map(bn.tpr).astype(float); nn = lab.map(bn.n).astype(float)
        used = (lab.notna() & tpr.notna() & (nn >= MIN_BIN)).values
        num += w * np.where(used, tpr.values - base, 0.0); den += np.where(used, w, 0.0)
    return np.where(den > 0, base + num / den, base)


def main(ks=None):
    t0 = time.time()
    db = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    bks = [_bucket_from_dict(d) for d in raw]; snaps = [b.full_snapshot() for b in bks]
    base_id = int(tc[0]) - len(bks)
    rs = FT.repo_series(snaps, bks)
    print("[%.0fs] snapshot + repo series ready (%d buckets)" % (time.time() - t0, len(bks)), flush=True)

    bundle = json.load(open(os.path.join(REPO, "app", "score_v1.json")))
    codes = list(bundle["sides"]["long"]["features"].keys())
    bcodes = [c for c in codes if c.startswith("B-")]
    ccodes = [c for c in codes if c.startswith("C.")]
    ecodes = [c for c in codes if not c.startswith(("B-", "C."))]     # E*.01 + G + E52.01 (k-independent)

    df = pd.read_parquet(os.path.join(OUT, "dataset.parquet"))
    keep = ["bucket_id", "L.06", "L.02"] + [c for c in ecodes if c != "E52.01"] + bcodes + ccodes
    L = df[df["L.01"] == "long"][keep].rename(columns={"L.02": "outL"})
    S = df[df["L.01"] == "short"][["bucket_id", "L.02"]].rename(columns={"L.02": "outS"})
    m = L.merge(S, on="bucket_id").reset_index(drop=True)
    m["E52.01"] = m["bucket_id"].map(analysis_A.large_net_map(db))
    ts = m["L.06"].astype(float); tmin, tmax = df["L.06"].min(), df["L.06"].max()
    tcut = tmin + 0.70 * (tmax - tmin); emb = tcut + 6 * 3600
    m["_win"] = np.where(ts <= tcut, "disc", np.where(ts >= emb, "hold", "emb"))
    m["_j"] = (m["bucket_id"] - base_id - 1).astype(int)

    # side-resolved masks (mirror exam.fit_binners universes) + evaluation mask (both resolved)
    rL = m["outL"].isin(["TP", "SL"]); rS = m["outS"].isin(["TP", "SL"])
    dL = (m["_win"] == "disc") & rL; dS = (m["_win"] == "disc") & rS
    tpL = (m["outL"] == "TP").values; tpS = (m["outS"] == "TP").values
    baseL = 100.0 * tpL[dL.values].mean(); baseS = 100.0 * tpS[dS.values].mean()
    both = rL & rS
    print("[%.0fs] rows=%d  disc baselines L %.2f / S %.2f" % (time.time() - t0, len(m), baseL, baseS), flush=True)

    wst = {"long": {}, "short": {}}
    with open(os.path.join(OUT, "score_weights.csv"), encoding="utf-8") as f:
        rd = csv.DictReader(r for r in f if not r.startswith("#"))
        for row in rd:
            if row["variant"] == "W-STAT":
                wst[row["direction"]][row["feature_code"]] = float(row["weight"])

    results = []; bfeat_cache = {}
    for k in (ks if ks is not None else list(range(1, 16)) + [16]):
        tk = time.time()
        if k == 16:
            Fk = m[bcodes + ccodes]                                   # the dataset's own frozen SEL16 features
        else:
            rows = np.empty((len(m), len(bcodes)), dtype=object)
            for ri, j in enumerate(m["_j"].values):
                bs = FTB.compute_bscope(snaps, rs, int(j), sel_len=k)   # returns {"B-P1.01": ...} directly
                rows[ri] = [bs.get(c) for c in bcodes]
            Fk = pd.DataFrame(rows, columns=bcodes, index=m.index)
            for c in bcodes:                                          # numeric where possible (strings stay)
                Fk[c] = pd.to_numeric(Fk[c], errors="ignore")
        feats = pd.concat([m[[c for c in ecodes]], Fk], axis=1)
        avail = [c for c in codes if c in feats.columns]
        binners = {}; wts = {}
        for side, msk, tp in (("long", dL, tpL), ("short", dS, tpS)):
            bn_side = {}
            for f in avail:
                bn = fit_binner(feats.loc[msk.values, f], tp[msk.values])
                if bn is not None:
                    bn_side[f] = bn
            binners[side] = bn_side
            wts[side] = {f: wst[side][f] for f in avail if f in wst[side]}
        pl = predict(feats, binners["long"], wts["long"], baseL)
        ps = predict(feats, binners["short"], wts["short"], baseS)
        gap = (pl - baseL) - (ps - baseS)
        side_long = gap >= 0
        rec = {"k": k}
        for win in ("disc", "hold"):
            mm = (both & (m["_win"] == win)).values
            sel_tp = np.where(side_long[mm], tpL[mm], tpS[mm])
            aL, aS = 100.0 * tpL[mm].mean(), 100.0 * tpS[mm].mean()
            st = 100.0 * sel_tp.mean()
            rec.update({win + "_n": int(mm.sum()), win + "_selTP": st, win + "_alwL": aL, win + "_alwS": aS,
                        win + "_delta": st - max(aL, aS), win + "_E": expectancy(st)})
            if win == "hold":
                g = gap[mm]
                rec["hold_flips"] = int(np.sum(np.sign(g[1:]) != np.sign(g[:-1])))
        results.append(rec)
        print("[%.0fs] k=%-2d  disc selTP %.1f (Δ%+.1f)   HOLD selTP %.1f (Δ%+.1f vs better-fixed)  flips %d   (%.0fs)"
              % (time.time() - t0, k, rec["disc_selTP"], rec["disc_delta"], rec["hold_selTP"],
                 rec["hold_delta"], rec["hold_flips"], time.time() - tk), flush=True)
        bfeat_cache[k] = (Fk, binners, wts, avail)

    if ks is None:
        cols = ["k", "disc_n", "disc_selTP", "disc_alwL", "disc_alwS", "disc_delta", "disc_E",
                "hold_n", "hold_selTP", "hold_alwL", "hold_alwS", "hold_delta", "hold_E", "hold_flips"]
        with open(os.path.join(OUT, "frame_sweep.csv"), "w", newline="", encoding="utf-8") as f:
            f.write("# SEL-k frame sweep. Bins fit on DISCOVERY only; 'hold' = the SPENT holdout (learning "
                    "material, selection-biased across 15 trials — NOT validation). Winner = v2 hypothesis.\n")
            w = csv.writer(f); w.writerow(cols)
            for r in results:
                w.writerow([("%.2f" % r[c]) if isinstance(r[c], float) else r[c] for c in cols])
        print("\nwrote study/out/frame_sweep.csv")
        best = max((r for r in results if r["k"] < 16), key=lambda r: r["hold_delta"])
        print("best k<16 by hold_delta: k=%d (Δ%+.2f, selTP %.1f)  |  k=16 reference: Δ%+.2f"
              % (best["k"], best["hold_delta"], best["hold_selTP"],
                 next(r["hold_delta"] for r in results if r["k"] == 16)))
    return results, bfeat_cache, (m, baseL, baseS)


def write_bundle(k):
    """Freeze the SEL-k bundle to app/score_v1.json (same schema as SEL16 + a 'frame' key). Bins from the
    sweep's discovery fit; weights = frozen W-STAT renormalized over the SEL-k subset. Labeled a spent-data
    pick — forward-test only."""
    results, cache, (m, baseL, baseS) = main(ks=[k])
    Fk, binners, wts, avail = cache[k]
    old = json.load(open(os.path.join(REPO, "app", "score_v1.json")))
    kinds = {c: old["sides"]["long"]["features"][c]["kind"] for c in old["sides"]["long"]["features"]}
    r = results[0]
    bundle = {
        "variant": "W-STAT-SEL%d" % k, "frozen_date": "2026-07-02", "frame": k,
        "note": ("Forward-test display; NOT a validated probability. Frame k=%d chosen by the frame sweep "
                 "on SPENT data (selection-biased across 15 trials): spent-holdout selTP %.1f%% "
                 "(Δ%+.1f vs better-fixed) vs SEL16 %.1f-ish — parity-level, not an edge. The full-exam "
                 "W-STAT verdict was FAIL; the SEL subset removed the level/tempo/day-rank poison." )
                % (k, r["hold_selTP"], r["hold_delta"], 48.8),
        "display": "edge-mode: single L-S gap line (pred - own baseline per side); zero-crossing colored "
                   "teal(long)/magenta(short); forward log keeps RAW pred.",
        "retained_pct": {}, "sides": {}}
    for s in ("long", "short"):
        tot = sum(wts[s].values())
        feats = {}
        for f, w in wts[s].items():
            bn = binners[s].get(f)
            if bn is None:
                continue
            bd = {"kind": bn.kind, "tpr": bn.tpr, "n": bn.n}
            if bn.kind == "num":
                bd["edges"] = [float(x) for x in bn.spec]
            else:
                bd["cats"] = [str(x) for x in bn.spec]
            feats[f] = {"weight": w / tot, "kind": kinds.get(f, "native"), "bin": bd}
        bundle["sides"][s] = {"baseline": (baseL if s == "long" else baseS), "features": feats}
        bundle["retained_pct"][s] = 100.0 * tot
        print("%-5s SEL%d bundle: %d features (weights renormed over subset)" % (s, k, len(feats)))
    path = os.path.join(REPO, "app", "score_v1.json")
    json.dump(bundle, open(path, "w"), indent=1)
    print("wrote %s (variant %s, frame=%d)" % (os.path.relpath(path, REPO), bundle["variant"], k))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "bundle":
        write_bundle(int(sys.argv[2]))
    else:
        main()
