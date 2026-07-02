"""Build app/score_v1.json — the FROZEN SEL16 score bundle — from score_weights.csv (W-STAT) + the exam's
frozen discovery bins. Weights renormalized over the SEL16 subset (deterministic, one-time; NOT re-tuned).
Then PARITY: live 16-frame scorer vs the exam machinery restricted to the same SEL16 subset, >=200 buckets.
"""
import os, sys, csv, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import exam, analysis_A, features as FT, score_core
REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
FROZEN_DATE = "2026-07-02"

TEXT = {}
with open(os.path.join(REPO, "study", "data", "column_contract.tsv"), encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="\t"):
        TEXT[r["code"]] = r["text"]

SAFE_E_KINDS = {None, "raw", "sign", "log"}      # scalar .01 transforms that need no trailing window


def e_kind(code):
    if code == "E52.01":
        return "native"
    return FT.classify_transform(TEXT.get(code, "")) or "raw"


def build():
    A = exam.load_all()
    W, baseline, binners = A["W"], A["baseline"], A["binners"]
    bundle = {"variant": "W-STAT-SEL16", "frozen_date": FROZEN_DATE, "note":
              "Forward-test display; the walk-forward exam verdict was FAIL. Not a validated probability.",
              "display": "edge-mode since 2026-07-02 (panel plots pred - own baseline); scoring "
              "(weights/bins/math) and the forward log (raw pred) are UNCHANGED.",
              "retained_pct": {}, "sides": {}}
    for s in ("long", "short"):
        wst = W[("W-STAT", s)]
        subset = {}
        for f, w in wst.items():
            if not score_core.is_sel16(f):
                continue
            k = "native" if not f.startswith("E") else e_kind(f)
            if f.startswith("E") and f != "E52.01" and FT.classify_transform(TEXT.get(f, "")) not in SAFE_E_KINDS:
                continue                          # a .01 whose text maps to a trailing transform -> drop
            if (f, s) not in binners:
                continue
            subset[f] = (w, k)
        tot = sum(w for w, _ in subset.values())
        feats = {}
        for f, (w, k) in subset.items():
            bn = binners[(f, s)]
            bd = {"kind": bn.kind, "tpr": bn.tpr, "n": bn.n}
            if bn.kind == "num":
                bd["edges"] = [float(x) for x in bn.spec]
            else:
                bd["cats"] = [str(x) for x in bn.spec]
            feats[f] = {"weight": w / tot, "kind": k, "bin": bd}
        bundle["sides"][s] = {"baseline": baseline[s], "features": feats}
        bundle["retained_pct"][s] = 100.0 * tot
        print("%-5s SEL16: %d features, retained %.1f%% of W-STAT weight (B-*=%d C.*=%d E.01=%d G=%d)"
              % (s, len(feats), 100.0 * tot,
                 sum(f.startswith("B-") for f in feats), sum(f.startswith("C.") for f in feats),
                 sum(f.startswith("E") for f in feats), sum(f.startswith("G") for f in feats)))
    path = os.path.join(REPO, "app", "score_v1.json")
    json.dump(bundle, open(path, "w"), indent=1)
    print("wrote %s" % os.path.relpath(path, REPO))
    return A, bundle


def parity(A, bundle):
    df = A["df"]
    con = __import__("sqlite3").connect("file:%s?mode=ro" % exam.__dict__.get("SNAP", os.path.join(
        REPO, "study", "data", "history_snapshot_20260702.db")), uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    from app.persistence import _bucket_from_dict
    bks = [_bucket_from_dict(d) for d in raw]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks); base_id = (int(tc[0]) if tc else n) - n; ids = [base_id + i + 1 for i in range(n)]
    dset = df[df["L.01"] == "long"].set_index("bucket_id")   # one row/bucket; entry-legal feats identical
    fk = {s: {c: bundle["sides"][s]["features"][c]["kind"] for c in bundle["sides"][s]["features"]}
          for s in ("long", "short")}
    rng = np.random.default_rng(1); js = rng.choice(range(20, n), size=250, replace=False)
    maxdev = {"long": 0.0, "short": 0.0}; ncmp = 0
    for j in js:
        bid = ids[j]
        if bid not in dset.index:
            continue
        row = dset.loc[bid]
        live_feat = score_core.sel16_features(snaps[j - 15:j + 1], bks[j - 15:j + 1], fk["long"])
        for s in ("long", "short"):
            sb = bundle["sides"][s]
            live = score_core.score_side(live_feat, sb)
            ref_feat = {}
            for c in sb["features"]:
                ref_feat[c] = score_core.large_net_entry(bks[j]) if c == "E52.01" else (
                    None if pd.isna(row[c]) else row[c])
            ref = score_core.score_side(ref_feat, sb)
            if live is not None and ref is not None:
                maxdev[s] = max(maxdev[s], abs(live - ref))
        ncmp += 1
    print("\nPARITY (live 16-frame vs exam-machinery/full-history restricted to SEL16), n=%d buckets:" % ncmp)
    print("  max |live - ref| : long %.4f pp   short %.4f pp" % (maxdev["long"], maxdev["short"]))
    print("  (deviation source = the pre-roll/trailing-truncation on B-* & C.08; pure C/raw-E/G/S-stats match)")
    return maxdev


if __name__ == "__main__":
    A, bundle = build()
    parity(A, bundle)
