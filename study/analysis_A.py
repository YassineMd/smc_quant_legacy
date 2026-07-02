"""Analysis Phase — Part A: univariate screening of entry-legal features vs TP/SL, DISCOVERY only.

HOLDOUT IS SEALED: rows with entry_ts >= (70%-cut + 6h embargo) are never loaded into any statistic.
Univariate only — no models, no multivariate. Effect size = top-vs-bottom bin TP-rate gap; uncertainty =
block bootstrap over 3h TIME blocks (1000 reps, 90% CI); rows overlap heavily so we resample BLOCKS, never
rows. Reference lines on every table: direction baseline, 37.5% geometric null, 48.8% fee breakeven.
"""
from __future__ import annotations
import os, sys, json, sqlite3
import numpy as np, pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from app import config
from app.persistence import _bucket_from_dict

OUT = os.path.join(REPO, "study", "out")
NULL_GEOM = 37.5           # geometric random-walk null (SL/(TP+SL)=0.3/0.8)
BREAKEVEN = 48.8           # fee-adjusted breakeven
BLOCK_S = 3 * 3600         # 3h time blocks
R = 1000                   # bootstrap reps
MIN_BIN = 30               # min n for a categorical bin to be eligible as top/bottom
SEED = 20260702
np.random.seed(SEED)

WHITELIST = ("E", "G", "C.", "K.", "B-")          # entry-legal prefixes
EXCLUDE_PREFIX = ("L.", "O.", "J-", "X-")
SZ_PREFIX = ("E50", "E51", "E52")                 # trade-size (age-masked; screen on sz-present only)

# ── large_net_side (E52.01) — architect-frozen APPROXIMATE(fixed-273c), ENTRY BUCKET only ──
LARGE_BINS = [i for i in range(config.SIZE_HIST_NBINS)
              if (0.0 if i == 0 else config.SIZE_HIST_EDGES[i - 1]) >= 273.0]   # [15..20]

def large_net_map(db):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
    con.close()
    n = len(raw); base = (int(tc[0]) if tc else n) - n
    out = {}
    for i, d in enumerate(raw):
        b = _bucket_from_dict(d)
        vb = getattr(b, "sz_vb", []); vs = getattr(b, "sz_vs", [])
        present = (sum(vb) + sum(vs)) > 0                       # sz age-mask: only exists post 2026-06-30
        if not present:
            out[base + i + 1] = None; continue
        lb = sum(vb[k] for k in LARGE_BINS); ls = sum(vs[k] for k in LARGE_BINS)
        out[base + i + 1] = "none" if (lb + ls) == 0 else ("large-buy" if lb >= ls else "large-sell")
    return out


def is_categorical(s_full, s):
    return (s_full.dtype == object or s_full.dtype == bool
            or (pd.api.types.is_integer_dtype(s_full) and s.nunique() <= 6))


def make_bins(series):
    """Return (kind, per-row bin label Series (NaN where feature null), ordered bin labels) or None."""
    s = series.dropna()
    if s.empty or s.nunique() < 2:
        return None
    if is_categorical(series, s):
        cats = sorted(s.unique(), key=lambda x: str(x))
        lab = series.map(lambda v: None if pd.isna(v) else str(v))
        return ("cat", lab, [str(c) for c in cats])
    try:
        _, edges = pd.qcut(s, 4, retbins=True, duplicates="drop")
    except Exception:
        return None
    if len(edges) < 3:
        return None
    idx = pd.cut(series, bins=edges, labels=False, include_lowest=True)
    labels = ["Q%d" % (i + 1) for i in range(len(edges) - 1)]
    lab = idx.map(lambda v: None if pd.isna(v) else labels[int(v)])
    return ("num", lab, labels)


def bin_stats(dfd, lab, blabels):
    """Per bin: n, tp, clean-reverse SL, whipsaw SL, tp_rate, whip_frac (of resolved)."""
    rows = {}
    tp_mask = (dfd["L.02"] == "TP").values
    whip_mask = (dfd["L.08"] == "WHIPSAW").values
    for b in blabels:
        m = (lab == b).values
        n = int(m.sum())
        if n == 0:
            rows[b] = dict(n=0, tp=0, clean=0, whip=0, tpr=np.nan, whipf=np.nan); continue
        tp = int((m & tp_mask).sum())
        sl = n - tp
        whip = int((m & ~tp_mask & whip_mask).sum())
        clean = sl - whip
        rows[b] = dict(n=n, tp=tp, clean=clean, whip=whip,
                       tpr=100.0 * tp / n, whipf=100.0 * whip / n)
    return rows


def block_matrix(dfd, lab, top, bot, blocks, B):
    """B x 4 : per block [tp_top, n_top, tp_bot, n_bot] for the chosen top/bottom bins."""
    M = np.zeros((B, 4))
    tp = (dfd["L.02"] == "TP").values
    lt = (lab == top).values; lb = (lab == bot).values
    for j, blk in enumerate(blocks):
        sel = (dfd["_blk"] == blk).values
        M[j, 0] = (sel & lt & tp).sum(); M[j, 1] = (sel & lt).sum()
        M[j, 2] = (sel & lb & tp).sum(); M[j, 3] = (sel & lb).sum()
    return M


def screen_direction(dfd, feats, big, resample_counts, blocks, B):
    baseline = 100.0 * (dfd["L.02"] == "TP").mean()
    recs = []
    for f in feats:
        approx = "APPROX(fixed-273c)" if f == "E52.01" else ("sz-coverage" if f.startswith(SZ_PREFIX) else "")
        b = make_bins(dfd[f])
        if b is None:
            recs.append(dict(feature=f, note="degenerate/constant", effect=np.nan)); continue
        kind, lab, blabels = b
        st = bin_stats(dfd, lab, blabels)
        eligible = [x for x in blabels if st[x]["n"] >= MIN_BIN]
        if len(eligible) < 2:
            recs.append(dict(feature=f, note="insufficient bin n", effect=np.nan)); continue
        if kind == "num":
            top, bot = eligible[-1], eligible[0]                 # Q(high) vs Q(low) — directional
        else:
            top = max(eligible, key=lambda x: st[x]["tpr"])       # best vs worst category (max-min spread)
            bot = min(eligible, key=lambda x: st[x]["tpr"])
        effect = st[top]["tpr"] - st[bot]["tpr"]
        # bootstrap CI over 3h blocks
        M = block_matrix(dfd, lab, top, bot, blocks, B)
        reps = resample_counts @ M                                # R x 4
        with np.errstate(invalid="ignore", divide="ignore"):
            gaps = np.where((reps[:, 1] > 0) & (reps[:, 3] > 0),
                            100.0 * reps[:, 0] / reps[:, 1] - 100.0 * reps[:, 2] / reps[:, 3], np.nan)
        gaps = gaps[~np.isnan(gaps)]
        ci_lo, ci_hi = (np.percentile(gaps, 5), np.percentile(gaps, 95)) if len(gaps) >= 50 else (np.nan, np.nan)
        cov = 100.0 * dfd[f].notna().mean()
        eff_blocks = int(((M[:, 1] + M[:, 3]) > 0).sum())
        recs.append(dict(
            feature=f, family=("B-" if f.startswith("B-") else f[0]), approx=approx, kind=kind,
            n_resolved=int(dfd[f].notna().sum()), coverage=cov, n_cats=len(blabels),
            top_bin=top, bot_bin=bot, tpr_top=st[top]["tpr"], tpr_bot=st[bot]["tpr"],
            n_top=st[top]["n"], n_bot=st[bot]["n"], whipf_top=st[top]["whipf"], whipf_bot=st[bot]["whipf"],
            effect=effect, ci_lo=ci_lo, ci_hi=ci_hi, eff_blocks=eff_blocks, baseline=baseline,
            bins=st, blabels=blabels, note=""))
    return recs, baseline


def main():
    df = pd.read_parquet(os.path.join(OUT, "dataset.parquet"))
    ts = df["L.06"].astype(float)
    tmin, tmax = ts.min(), ts.max()
    tcut = tmin + 0.70 * (tmax - tmin)
    emb = tcut + 6 * 3600
    disc = df[ts <= tcut].copy()                                  # DISCOVERY; holdout (ts>=emb) SEALED
    n_holdout_sealed = int((ts >= emb).sum())                     # structural count only; no stats on it
    disc["_ts"] = ts[ts <= tcut].values
    disc["_blk"] = ((disc["_ts"] - tmin) // BLOCK_S).astype(int)

    # E52.01 large_net_side (APPROX, entry bucket) merged by bucket_id
    big = large_net_map(os.path.join(REPO, "study", "data", "history_snapshot_20260702.db"))
    disc["E52.01"] = disc["bucket_id"].map(big)

    feats = [c for c in df.columns if c.startswith(WHITELIST) and not c.startswith(EXCLUDE_PREFIX)
             and c != "bucket_id"]
    feats = [c for c in feats if c == "E52.01" or not disc[c].isna().all()]
    if "E52.01" not in feats:
        feats.append("E52.01")
    feats = sorted(set(feats))

    priors = ["B-P1.03", "B-P1.04", "B-P2.03", "B-P4.02", "E60.01", "C.01", "C.02", "E52.01"]

    out = {}
    for d in ("long", "short"):
        dd = disc[(disc["L.01"] == d) & (disc["L.02"].isin(["TP", "SL"]))].copy()
        blocks = sorted(dd["_blk"].unique()); B = len(blocks)
        blk_index = {b: j for j, b in enumerate(blocks)}
        dd["_blk"] = dd["_blk"].map(blk_index)                    # reindex 0..B-1 for the matrix
        draws = np.random.randint(0, B, size=(R, B))
        rc = np.zeros((R, B))
        np.add.at(rc, (np.repeat(np.arange(R), B), draws.ravel()), 1.0)
        recs, baseline = screen_direction(dd, feats, big, rc, list(range(B)), B)
        out[d] = dict(recs=recs, baseline=baseline, B=B, n=len(dd),
                      tp=int((dd["L.02"] == "TP").sum()), sl=int((dd["L.02"] == "SL").sum()))
    return df, disc, feats, priors, out, dict(tmin=tmin, tmax=tmax, tcut=tcut, emb=emb,
                                              n_disc=len(disc), n_holdout=n_holdout_sealed)


if __name__ == "__main__":
    import report_A
    report_A.build(*main())
