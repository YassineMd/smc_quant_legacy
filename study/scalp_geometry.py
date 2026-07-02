"""S1 — SCALP GEOMETRY STUDY (no feature screening). Architect-approved phase.

Re-walks price paths from the snapshot (bucket OHLC), same episode universe (idx 16+):
  * MFE/MAE per direction at horizons 5/10/15/30/60 min — PERCENTILE tables.
  * Geometry grid TP x SL x horizon — per cell: resolution/TP-first (per direction)/whipsaw/unresolved/
    ambiguous rates, geometric null SL/(TP+SL), FEE-ADJUSTED breakeven p* = (SL+f)/(TP+SL) under three
    fee scenarios, and REQUIRED LIFT = p* - null = f/(TP+SL) (analytic — geometry+fees only).
  * PARITY GATE: the walker at the ORIGINAL geometry (0.5/0.3, 6h) must reproduce the dataset's frozen
    L.02 rates before any grid cell is trusted.
Conventions carried from the frozen labeler: barrier touched when the bucket's high/low reaches it; a
bucket spanning BOTH barriers -> SL (ambiguity, reported per cell; >15% flagged tape-resolution-needed);
horizon from the entry bucket's close; a touch counts if its bucket STARTS within the horizon.

HARD STOP after the report — the architect + Yassine pick 1-2 geometries before any re-labeling (S2).
"""
import os, sys, csv, json, sqlite3, time
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict   # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
FIRST_IDX = 16
HORIZONS_MIN = [5, 10, 15, 30, 60]
TP_GRID = [0.0010, 0.0015, 0.0020, 0.0025, 0.0030]          # 0.10..0.30 %
SL_GRID = [0.0005, 0.00075, 0.0010, 0.0015, 0.0020]         # 0.05..0.20 %
H_GRID_MIN = [15, 30, 60]
# Round-trip fee scenarios (fractions). ONE place — Yassine may supply his real tier.
FEES = {"taker_taker": 0.0009, "maker_taker": 0.00065, "maker_maker": 0.0004}
PCTS = [10, 25, 50, 75, 90]
LIFT_OK = 0.10            # payable when required lift <= 10pp (the achievable zone seen so far)
AMBIG_FLAG = 0.15


def load():
    db = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    con.close()
    bks = [_bucket_from_dict(d) for d in raw]
    return (np.array([b.high for b in bks], float), np.array([b.low for b in bks], float),
            np.array([b.close_price for b in bks], float),
            np.array([b.start_time for b in bks], float), np.array([b.end_time for b in bks], float))


def first_touch_tables(hi, lo, cl, st, et, levels, max_h_s):
    """Per episode i (FIRST_IDX..n-1): for every level v (fraction), the FIRST bucket j>i whose high >=
    entry*(1+v) (up) / low <= entry*(1-v) (dn), scanning only while start_time <= entry_close + max_h_s.
    Returns (up_idx, up_t, dn_idx, dn_t, mfe_up, mfe_dn) — touch bucket index (-1 = none), its start_time,
    and running max-up/max-dn displacement fractions AT each MFE horizon checkpoint."""
    n = len(hi); L = len(levels)
    ents = list(range(FIRST_IDX, n))
    up_idx = np.full((len(ents), L), -1, np.int32); dn_idx = np.full((len(ents), L), -1, np.int32)
    up_t = np.full((len(ents), L), np.inf); dn_t = np.full((len(ents), L), np.inf)
    hchk = [h * 60.0 for h in HORIZONS_MIN]
    mfe_up = np.full((len(ents), len(hchk)), np.nan); mfe_dn = np.full((len(ents), len(hchk)), np.nan)
    for ei, i in enumerate(ents):
        entry = cl[i]; t0 = et[i]; tmax = t0 + max_h_s
        upL = entry * (1.0 + np.array(levels)); dnL = entry * (1.0 - np.array(levels))
        got_up = 0; got_dn = 0
        run_hi = entry; run_lo = entry
        chk = 0
        j = i + 1
        while j < n and st[j] <= tmax:
            # MFE checkpoints: bucket belongs to horizon h if it STARTS within h (labeler convention)
            while chk < len(hchk) and st[j] > t0 + hchk[chk]:
                mfe_up[ei, chk] = run_hi / entry - 1.0; mfe_dn[ei, chk] = 1.0 - run_lo / entry
                chk += 1
            if hi[j] > run_hi:
                run_hi = hi[j]
            if lo[j] < run_lo:
                run_lo = lo[j]
            if got_up < L:
                for k in range(got_up, L):
                    if hi[j] >= upL[k]:
                        up_idx[ei, k] = j; up_t[ei, k] = st[j]; got_up = k + 1
                    else:
                        break
            if got_dn < L:
                for k in range(got_dn, L):
                    if lo[j] <= dnL[k]:
                        dn_idx[ei, k] = j; dn_t[ei, k] = st[j]; got_dn = k + 1
                    else:
                        break
            j += 1
        while chk < len(hchk):
            mfe_up[ei, chk] = run_hi / entry - 1.0; mfe_dn[ei, chk] = 1.0 - run_lo / entry
            chk += 1
    return np.array(ents), up_idx, up_t, dn_idx, dn_t, mfe_up, mfe_dn


def cell_outcomes(up_idx, up_t, dn_idx, dn_t, kt, ks, t0s, h_s):
    """Vectorized cell evaluation for LONG (tp=up level kt, sl=dn level ks) within horizon h_s.
    Returns (tp_first, sl_first, ambig, unresolved) boolean arrays. SHORT = swap up/dn."""
    tin = t0s + h_s
    u_ok = (up_idx[:, kt] >= 0) & (up_t[:, kt] <= tin)
    d_ok = (dn_idx[:, ks] >= 0) & (dn_t[:, ks] <= tin)
    ui = np.where(u_ok, up_idx[:, kt], np.iinfo(np.int32).max)
    di = np.where(d_ok, dn_idx[:, ks], np.iinfo(np.int32).max)
    ambig = u_ok & d_ok & (ui == di)
    tp = u_ok & (ui < di)
    sl = (d_ok & (di < ui)) | ambig                    # ambiguity -> SL (frozen convention)
    unres = ~u_ok & ~d_ok
    return tp, sl, ambig, unres


def main():
    t0 = time.time()
    hi, lo, cl, st, et = load()
    n = len(hi)
    levels = sorted(set(TP_GRID + SL_GRID + [0.005, 0.003]))     # grid + the ORIGINAL geometry for parity
    li = {v: k for k, v in enumerate(levels)}
    max_h = max(max(H_GRID_MIN) * 60.0, 6 * 3600.0)              # walk far enough for the 6h parity check
    print("[%.0fs] walking %d episodes x %d levels (to %.0f min)..."
          % (time.time() - t0, n - FIRST_IDX, len(levels), max_h / 60), flush=True)
    ents, up_idx, up_t, dn_idx, dn_t, mfe_up, mfe_dn = first_touch_tables(hi, lo, cl, st, et, levels, max_h)
    t0s = et[ents]
    print("[%.0fs] walk done" % (time.time() - t0), flush=True)

    # ── PARITY GATE: original geometry 0.5/0.3, 6h must reproduce the frozen labels ──
    df = pd.read_parquet(os.path.join(OUT, "dataset.parquet"))
    ref = {}
    for d in ("long", "short"):
        sub = df[df["L.01"] == d]["L.02"].value_counts()
        ref[d] = dict(TP=int(sub.get("TP", 0)), SL=int(sub.get("SL", 0)), UN=int(sub.get("UNRESOLVED", 0)))
    tpL, slL, amL, unL = cell_outcomes(up_idx, up_t, dn_idx, dn_t, li[0.005], li[0.003], t0s, 6 * 3600.0)
    tpS, slS, amS, unS = cell_outcomes(dn_idx, dn_t, up_idx, up_t, li[0.005], li[0.003], t0s, 6 * 3600.0)
    print("\n== PARITY GATE (walker @ 0.5/0.3, 6h vs frozen dataset labels) ==")
    ok = True
    for d, tp, sl, un in (("long", tpL, slL, unL), ("short", tpS, slS, unS)):
        got = (int(tp.sum()), int(sl.sum()), int(un.sum()))
        exp = (ref[d]["TP"], ref[d]["SL"], ref[d]["UN"])
        match = got == exp
        ok &= match
        print("  %-5s walker TP/SL/UN = %s  vs dataset %s  -> %s" % (d, got, exp, "MATCH" if match else "MISMATCH"))
    assert ok, "PARITY FAIL — do not trust the grid"
    print("  PARITY PASS")

    # ── MFE/MAE percentile tables ──
    mfe_rows = []
    for hx, hmin in enumerate(HORIZONS_MIN):
        for d, arr in (("long", mfe_up), ("short", mfe_dn)):        # MFE long = max-up; MAE long = max-dn
            fav = (mfe_up if d == "long" else mfe_dn)[:, hx] * 100
            adv = (mfe_dn if d == "long" else mfe_up)[:, hx] * 100
            row = {"horizon_min": hmin, "direction": d}
            for p in PCTS:
                row["MFE_p%d" % p] = round(float(np.nanpercentile(fav, p)), 4)
                row["MAE_p%d" % p] = round(float(np.nanpercentile(adv, p)), 4)
            mfe_rows.append(row)
    pd.DataFrame(mfe_rows).to_csv(os.path.join(OUT, "scalp_mfe_mae.csv"), index=False)

    # ── geometry grid ──
    rows = []
    for hmin in H_GRID_MIN:
        h_s = hmin * 60.0
        for tp in TP_GRID:
            for sl in SL_GRID:
                tpL, slL, amL, unL = cell_outcomes(up_idx, up_t, dn_idx, dn_t, li[tp], li[sl], t0s, h_s)
                tpS, slS, amS, unS = cell_outcomes(dn_idx, dn_t, up_idx, up_t, li[tp], li[sl], t0s, h_s)
                nn = len(ents)
                resL = tpL.sum() + slL.sum(); resS = tpS.sum() + slS.sum()
                whip = (slL & slS).sum() / nn                       # joint both-SL
                null = sl / (tp + sl)
                rec = dict(h_min=hmin, tp_pct=tp * 100, sl_pct=sl * 100, span_pct=(tp + sl) * 100,
                           null=round(100 * null, 2),
                           res_rate=round(100 * (resL + resS) / (2 * nn), 1),
                           tpL_res=round(100 * tpL.sum() / max(1, resL), 2),
                           tpS_res=round(100 * tpS.sum() / max(1, resS), 2),
                           whip=round(100 * whip, 2),
                           unres=round(100 * (unL.sum() + unS.sum()) / (2 * nn), 2),
                           ambigL=round(100 * amL.sum() / nn, 2), ambigS=round(100 * amS.sum() / nn, 2))
                for tag, f in FEES.items():
                    be = (sl + f) / (tp + sl)
                    rec["be_" + tag] = round(100 * be, 2)
                    rec["lift_" + tag] = round(100 * (be - null), 2)
                rec["payable_mm"] = (FEES["maker_maker"] / (tp + sl)) <= LIFT_OK
                rec["ambig_flag"] = max(rec["ambigL"], rec["ambigS"]) > AMBIG_FLAG * 100
                rows.append(rec)
    grid = pd.DataFrame(rows)
    with open(os.path.join(OUT, "scalp_geometry_grid.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# S1 geometry grid. Required lift = fee/(TP+SL) (analytic). Rates from the 4-day snapshot "
                "(both regimes spent/known) — geometry selection material, NOT edge evidence.\n")
        grid.to_csv(f, index=False)
    print("[%.0fs] grid done (%d cells)" % (time.time() - t0, len(grid)), flush=True)
    return grid, mfe_rows


if __name__ == "__main__":
    main()
