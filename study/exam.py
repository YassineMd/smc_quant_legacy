"""WALK-FORWARD SELECTION EXAM. FREEZE (discovery) -> UNSEAL ONCE (holdout) -> D+C reports.

pred%_s,V(row) = baseline_s + Σ_i w_{i,V,s}·(TP%_i[bin] − baseline_s), renormalized over contributing
features (non-null, frozen bin n≥100). Weights = score_weights.csv verbatim; bins = frozen discovery
quartiles/categories with discovery TP%/n. Selection: side = argmax(pred_long, pred_short), one trade/bucket.

Modes:  python exam.py freeze   -> discovery-only self-review (NO holdout access)
        python exam.py unseal   -> the one holdout run; writes reports D + C + trades CSV
"""
import os, sys, csv, json
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import analysis_A
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
MIN_BIN = 100; R_TP = 1.667; FEE_R = 0.3; BREAKEVEN = 48.8
OFIELDS = ["O.06", "O.12", "O.24", "O.18", "O.23"]

CONDS = [  # (id, direction, kind, spec)
    ("L1", "long", "eq", ("B-P3.08", 1)), ("L2", "long", "eq", ("C.08", "NEUTRAL")),
    ("L3", "long", "sz", ("E52.01", "large-buy")), ("L4", "long", "eq", ("E60.01", "BEAR TRAP")),
    ("L5", "long", "eq", ("C.01", 1)), ("L6", "long", "q1", ("B-P1.03",)),
    ("X1", "short", "eq", ("B-P3.08", 2)), ("X2", "short", "eq", ("C.08", "STRONG BULL")),
    ("X3", "short", "sz", ("E52.01", "large-sell")), ("X4", "short", "eq", ("E60.01", "BEAR EXHAUSTION")),
    ("X5", "short", "eq", ("B-P4.02", "bull")), ("X6", "short", "eq", ("C.01", 0)),
]


def load_weights():
    W = {}
    with open(os.path.join(OUT, "score_weights.csv"), encoding="utf-8") as f:
        rd = csv.reader(r for r in f if not r.startswith("#")); hdr = next(rd)
        ix = {c: i for i, c in enumerate(hdr)}
        for row in rd:
            W.setdefault((row[ix["variant"]], row[ix["direction"]]), {})[row[ix["feature_code"]]] = float(row[ix["weight"]])
    return W


class Binner:
    def __init__(self, kind, spec, tpr, n):
        self.kind, self.spec, self.tpr, self.n = kind, spec, tpr, n
    def labels(self, series):
        if self.kind == "num":
            idx = pd.cut(series, self.spec, labels=False, include_lowest=True)
            return idx.map(lambda v: None if pd.isna(v) else "Q%d" % (int(v) + 1))
        return series.map(lambda v: (str(v) if (not pd.isna(v) and str(v) in self.spec) else None))


def fit_binners(disc, out, feats):
    binners = {}
    for s in ("long", "short"):
        dds = disc[(disc["L.01"] == s) & (disc["L.02"].isin(["TP", "SL"]))]
        recs = {r["feature"]: r for r in out[s]["recs"]}
        for f in feats:
            rec = recs.get(f)
            if not rec or "bins" not in rec:
                continue
            col = dds[f].dropna()
            tpr = {b: rec["bins"][b]["tpr"] for b in rec["bins"]}; n = {b: rec["bins"][b]["n"] for b in rec["bins"]}
            if analysis_A.is_categorical(dds[f], col):
                binners[(f, s)] = Binner("cat", set(str(x) for x in col.unique()), tpr, n)
            else:
                try:
                    _, e = pd.qcut(col, 4, retbins=True, duplicates="drop")
                except Exception:
                    continue
                if len(e) >= 3:
                    binners[(f, s)] = Binner("num", e, tpr, n)
    return binners


def make_predict(binners, baseline, W):
    def predict(frame, s, V):
        base = baseline[s]; num = np.zeros(len(frame)); den = np.zeros(len(frame))
        for f, w in W[(V, s)].items():
            bn = binners.get((f, s))
            if bn is None or f not in frame.columns:
                continue
            lab = bn.labels(frame[f]); tpr = lab.map(bn.tpr).astype(float); nn = lab.map(bn.n).astype(float)
            used = (lab.notna() & tpr.notna() & (nn >= MIN_BIN)).values
            num += w * np.where(used, tpr.values - base, 0.0); den += np.where(used, w, 0.0)
        return np.where(den > 0, base + num / den, base)
    return predict


def load_all():
    df, disc, feats, priors, out, meta = analysis_A.main()
    big = analysis_A.large_net_map(os.path.join(REPO, "study", "data", "history_snapshot_20260702.db"))
    df = df.copy(); df["E52.01"] = df["bucket_id"].map(big)
    ts = df["L.06"].astype(float); tmin, tmax = ts.min(), ts.max()
    tcut = tmin + 0.70 * (tmax - tmin); emb = tcut + 6 * 3600
    df["_ts"] = ts; df["_blk"] = ((ts - tmin) // (3 * 3600)).astype(int)
    df["_zone"] = np.where(ts <= tcut, "discovery", np.where(ts >= emb, "holdout", "embargo"))
    W = load_weights(); baseline = {s: out[s]["baseline"] for s in ("long", "short")}
    binners = fit_binners(disc, out, feats); predict = make_predict(binners, baseline, W)
    return dict(df=df, disc=disc, out=out, feats=feats, W=W, baseline=baseline, binners=binners,
                predict=predict, meta=meta, split=(tmin, tcut, emb, tmax))


# ── per-bucket merged frame (both sides) for a zone ──
def bucket_frame(df, zone):
    z = df[df["_zone"] == zone]
    L = z[z["L.01"] == "long"].copy()
    scol = ["bucket_id", "L.02", "L.08"] + OFIELDS
    S = z[z["L.01"] == "short"][scol].rename(columns={c: c + "_S" for c in scol if c != "bucket_id"})
    return L.merge(S, on="bucket_id")


def selection(m, V, predict):
    pl = predict(m, "long", V); ps = predict(m, "short", V)
    side = np.where(pl >= ps, "long", "short")
    def pick(colL, colS):
        return np.where(side == "long", m[colL].values, m[colS].values)
    sel = pd.DataFrame({"bucket_id": m["bucket_id"].values, "ts": m["_ts"].values, "_blk": m["_blk"].values,
                        "side": side, "pred_long": pl, "pred_short": ps, "gap": np.abs(pl - ps),
                        "out": pick("L.02", "L.02_S"), "whip": pick("L.08", "L.08_S"),
                        "long_out": m["L.02"].values, "short_out": m["L.02_S"].values})
    for o in OFIELDS:
        sel[o] = pick(o, o + "_S")
    both = sel[(sel["long_out"].isin(["TP", "SL"])) & (sel["short_out"].isin(["TP", "SL"]))].copy()
    return both


def rate(a):
    return 100.0 * np.mean(a) if len(a) else float("nan")


def block_boot(sel, better_side, R=1000, seed=7):
    rng = np.random.default_rng(seed)
    blocks = sel["_blk"].unique(); B = len(blocks)
    by = {b: sel.index[sel["_blk"] == b].to_numpy() for b in blocks}
    st = (sel["out"] == "TP").values
    bt = (sel["long_out"].values == "TP") if better_side == "long" else (sel["short_out"].values == "TP")
    pos = {ix: i for i, ix in enumerate(sel.index)}
    stp, gap = [], []
    for _ in range(R):
        idx = np.concatenate([by[b] for b in rng.choice(blocks, B, replace=True)])
        p = np.array([pos[i] for i in idx])
        stp.append(100.0 * st[p].mean()); gap.append(100.0 * (st[p].mean() - bt[p].mean()))
    return (np.percentile(stp, 5), np.percentile(stp, 95)), (np.percentile(gap, 5), np.percentile(gap, 95)), B


def expectancy(p):
    p = p / 100.0
    return p * R_TP - (1 - p) * 1.0 - FEE_R


def eval_cond(frame, cond, binners):
    cid, dirn, kind, spec = cond
    u = frame[(frame["L.01"] == dirn) & (frame["L.02"].isin(["TP", "SL"]))]
    if kind == "sz":
        u = u[u["E52.01"].notna()]
    if kind == "q1":
        lab = binners[(spec[0], dirn)].labels(u[spec[0]]); true = (lab == "Q1").values
    elif kind == "sz":
        true = (u[spec[0]] == spec[1]).values
    else:
        true = (u[spec[0]] == spec[1]).values
    tp = (u["L.02"] == "TP").values
    ntrue = int(true.sum())
    tpt = rate(tp[true]); tpf = rate(tp[~true])
    return ntrue, tpt, tpf, (tpt - tpf), dirn


def decile_rows(pred, tp):
    q = pd.qcut(pred, 10, labels=False, duplicates="drop"); out = []
    for d in sorted(pd.unique(q[~pd.isna(q)])):
        m = (q == d); out.append((int(d) + 1, int(m.sum()), float(np.mean(pred[m])), rate(tp[m])))
    return out


def cond_table(frame, binners):
    rows = []
    for cond in CONDS:
        ntrue, tpt, tpf, eff, dirn = eval_cond(frame, cond, binners)
        rows.append((cond[0], dirn, ntrue, tpt, tpf, eff))
    return rows


def freeze(A):
    df, disc, out, W, baseline, binners, predict = (A[k] for k in
        ("df", "disc", "out", "W", "baseline", "binners", "predict"))
    print("=" * 80 + "\nFREEZE (discovery only, holdout untouched)")
    frozen = {"baseline": baseline, "thresholds": {}, "gap_edges": {}, "cond_disc": {}}
    for V in ("W-ALL", "W-STAT"):
        for s in ("long", "short"):
            fr = disc[(disc["L.01"] == s) & (disc["L.02"].isin(["TP", "SL"]))]
            pred = predict(fr, s, V); tp = (fr["L.02"] == "TP").values
            frozen["thresholds"]["%s|%s" % (s, V)] = {"top_decile": float(np.percentile(pred, 90)),
                                                       "top_quintile": float(np.percentile(pred, 80))}
        m = bucket_frame(df, "discovery")
        gap = np.abs(predict(m, "long", V) - predict(m, "short", V))
        frozen["gap_edges"][V] = [float(np.percentile(gap, p)) for p in range(0, 101, 10)]
    print("\n--- 12 ride-along conditions: DISCOVERY effects (freeze) ---")
    print("  id  dir   n_true  TP%_true  TP%_false  effect_pp")
    for cid, dirn, ntrue, tpt, tpf, eff in cond_table(disc, binners):
        frozen["cond_disc"][cid] = dict(n=ntrue, tpt=tpt, tpf=tpf, eff=eff, dir=dirn)
        print("  %-3s %-5s %6d   %6.1f    %6.1f    %+7.1f" % (cid, dirn, ntrue, tpt, tpf, eff))
    print("  sign cross-check vs Part-A priors: L1/L2(NEUTRAL fade)/L4(BEAR TRAP)/L5(ctx up) +, "
          "X2(STRONG BULL fade)/X4(BEAR EXH)/X5(bull last-exh) + expected.")
    print("\n--- in-sample selection review (discovery; exercises STEP-2 path) ---")
    for V in ("W-ALL", "W-STAT"):
        both = selection(bucket_frame(df, "discovery"), V, predict)
        st = rate((both["out"] == "TP").values)
        al = rate((both["long_out"] == "TP").values); ash = rate((both["short_out"] == "TP").values)
        print("  %-6s sel TP%%=%.1f  always-long=%.1f  always-short=%.1f  n=%d" % (V, st, al, ash, len(both)))
    json.dump(frozen, open(os.path.join(OUT, "exam_frozen.json"), "w"), indent=1)
    print("\nwrote study/out/exam_frozen.json  ·  holdout STILL SEALED\n" + "=" * 80)
    return frozen


def window_stats(sel, V):
    st = rate((sel["out"] == "TP").values)
    al = rate((sel["long_out"] == "TP").values); ash = rate((sel["short_out"] == "TP").values)
    better = "long" if al >= ash else "short"; base = max(al, ash)
    whip = rate(((sel["out"] == "SL") & (sel["whip"] == "WHIPSAW")).values)
    (cl, ch), (gl, gh), B = block_boot(sel.reset_index(drop=True), better)
    return dict(n=len(sel), sel_tp=st, al=al, ash=ash, better=better, base=base, whip=whip,
                exp=expectancy(st), exp_base=expectancy(base), ci=(cl, ch), gapci=(gl, gh), blocks=B)


def wrong_votes(m, los, V, binners, W, baseline):
    from collections import defaultdict
    ml = m.merge(los[["bucket_id", "side"]], on="bucket_id")   # attach features to the losing buckets
    contrib = defaultdict(list)
    for side in ("long", "short"):
        sub = ml[ml["side"] == side]
        if len(sub) == 0:
            continue
        base = baseline[side]
        for f, w in W[(V, side)].items():
            bn = binners.get((f, side))
            if bn is None or f not in sub.columns:
                continue
            lab = bn.labels(sub[f]); tpr = lab.map(bn.tpr).astype(float); nn = lab.map(bn.n).astype(float)
            used = (lab.notna() & tpr.notna() & (nn >= MIN_BIN)).values
            dev = np.where(used, w * (tpr.values - base), 0.0)
            contrib[f].extend(dev[used].tolist())
    rows = [(f, float(np.mean(c)), len(c)) for f, c in contrib.items() if len(c) >= 20]
    rows.sort(key=lambda x: -x[1]); return rows[:10]


def unseal(A):
    df, disc, out, W, baseline, binners, predict = (A[k] for k in
        ("df", "disc", "out", "W", "baseline", "binners", "predict"))
    frozen = freeze(A)                      # reprints freeze; holdout still untouched up to here
    print("\n" + "#" * 80 + "\nUNSEALING HOLDOUT — one shot, no re-runs.\n" + "#" * 80)
    hold = df[df["_zone"] == "holdout"]
    D = ["# Walk-Forward Selection Exam — Report D\n",
         "_Generated %s UTC · holdout UNSEALED once, no re-runs. Fees R=0.3%%, TP=1.667R, "
         "breakeven≈48.8%%._\n" % pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M")]
    D.append("\n## Split\ndiscovery %d buckets · embargo %d · holdout %d (post-embargo).\n"
             % ((df["_zone"] == "discovery").sum() // 2, (df["_zone"] == "embargo").sum() // 2,
                (df["_zone"] == "holdout").sum() // 2))
    D.append("**v1 5-item checklist: DROPPED** (superseded by the weighted score; ride-along = the 12 "
             "conditions only).\n")
    trades = []
    for V in ("W-ALL", "W-STAT"):
        selD = selection(bucket_frame(df, "discovery"), V, predict)
        selH = selection(bucket_frame(df, "holdout"), V, predict)
        wD = window_stats(selD, V); wH = window_stats(selH, V)
        # headline verdict (holdout)
        gl, gh = wH["gapci"]
        if wH["sel_tp"] > wH["base"] and gl > 0 and wH["n"] >= 300:
            verdict = "PASS"
        elif wH["sel_tp"] > wH["base"]:
            verdict = "PARTIAL"
        else:
            verdict = "FAIL"
        D.append("\n## %s — selection vs baselines\n" % V)
        D.append("| window | n | sel TP%% | always-L | always-S | better | Δ vs better | 90%% CI (sel) | "
                 "gap CI | whip%% | E[R] sel | E[R] better | blk |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for lbl, w in (("in-sample", wD), ("HOLDOUT", wH)):
            D.append("| %s | %d | %.1f | %.1f | %.1f | %s %.1f | %+.1f | [%.1f, %.1f] | [%.1f, %.1f] | %.1f | "
                     "%+.2f | %+.2f | %d |" % (lbl, w["n"], w["sel_tp"], w["al"], w["ash"], w["better"], w["base"],
                     w["sel_tp"] - w["base"], w["ci"][0], w["ci"][1], w["gapci"][0], w["gapci"][1], w["whip"],
                     w["exp"], w["exp_base"], w["blocks"]))
        D.append("\n**HEADLINE VERDICT (holdout, %s): %s** — sel TP%% %.1f vs better-fixed %.1f (%s), "
                 "gap 90%% CI [%.1f, %.1f], n=%d, ~%d blocks.\n"
                 % (V, verdict, wH["sel_tp"], wH["base"], wH["better"], gl, gh, wH["n"], wH["blocks"]))
        # calibration (holdout, per side)
        D.append("\n### Holdout calibration — pred%% decile vs ACTUAL TP%% (does the ordering survive?)\n")
        for s in ("long", "short"):
            fr = hold[(hold["L.01"] == s) & (hold["L.02"].isin(["TP", "SL"]))]
            pred = predict(fr, s, V); tp = (fr["L.02"] == "TP").values
            cells = " · ".join("D%d %.0f%%(n%d)" % (d, tpr, n) for d, n, pm, tpr in decile_rows(pred, tp))
            D.append("- **%s**: %s\n" % (s, cells))
        # gap-decile (holdout) using frozen edges
        edges = sorted(set(frozen["gap_edges"][V]))
        gd = pd.cut(selH["gap"], edges, labels=False, include_lowest=True) if len(edges) >= 3 \
            else pd.Series([np.nan] * len(selH))
        D.append("\n### Holdout gap-decile — selected TP%% by |pred gap| decile (bigger gap = better trade?)\n| dec | n | sel TP%% |\n|---|---|---|")
        mono = []
        for d in sorted(pd.unique(gd[~pd.isna(gd)])):
            mm = (gd == d); v = rate((selH["out"][mm] == "TP").values); mono.append(v)
            D.append("| %d | %d | %.1f |" % (int(d) + 1, int(mm.sum()), v))
        D.append("_monotonic-ish: %s_\n" % ("yes (rising)" if mono == sorted(mono) else "no"))
        # excursions (holdout selected)
        D.append("\n### Holdout selected-trade excursions (median)\n")
        tps = selH[selH["out"] == "TP"]; sls = selH[selH["out"] == "SL"]
        D.append("- TP trades O.06 beyond-TP %%: %.3f (n%d) · SL trades O.12 beyond-SL %%: %.3f · O.24 near-win: "
                 "%.3f · O.18 tease %%: %.3f · O.23 time-in-profit s: %.0f (n%d)\n"
                 % (tps["O.06"].median(), len(tps), sls["O.12"].median(), sls["O.24"].median(),
                    sls["O.18"].median(), sls["O.23"].median(), len(sls)))
        for lbl, sel in (("discovery", selD), ("holdout", selH)):
            for _, r in sel.iterrows():
                trades.append([r["bucket_id"], "%.0f" % r["ts"], r["side"], "%.2f" % r["pred_long"],
                               "%.2f" % r["pred_short"], "%.2f" % r["gap"], V, lbl, r["out"],
                               int(r["whip"] == "WHIPSAW")] + ["%.4f" % r[o] if pd.notna(r[o]) else "" for o in OFIELDS])
    # ride-along
    D.append("\n## Ride-along — 12 conditions (holdout vs discovery), mechanical verdict\n")
    D.append("| id | dir | disc eff | hold n_true | hold TP_t | hold TP_f | hold eff | verdict |\n|---|---|---|---|---|---|---|---|")
    ver = {}
    for cond in CONDS:
        cid = cond[0]; de = frozen["cond_disc"][cid]["eff"]
        hn, ht, hf, he, dirn = eval_cond(hold, cond, binners)
        ss = (de > 0) == (he > 0) and abs(de) > 1e-9 and not np.isnan(he)
        if ss and hn >= 30 and abs(he) >= 0.5 * abs(de):
            v = "PASS"
        elif ss:
            v = "PARTIAL"
        else:
            v = "FAIL"
        ver[cid] = v
        D.append("| %s | %s | %+.1f | %d | %s | %s | %+.1f | %s |" % (cid, dirn, de, hn,
                 "%.1f" % ht if not np.isnan(ht) else "-", "%.1f" % hf if not np.isnan(hf) else "-", he, v))
    from collections import Counter
    D.append("\nride-along tally: %s\n" % dict(Counter(ver.values())))

    # trades CSV
    tpath = os.path.join(OUT, "walkforward_trades.csv")
    with open(tpath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bucket_id", "ts", "side", "pred_long", "pred_short", "gap", "variant", "window",
                    "outcome", "whipsaw"] + OFIELDS)
        w.writerows(trades)
    open(os.path.join(OUT, "analysis_report_D_walkforward.md"), "w", encoding="utf-8").write("\n".join(D))

    # ── STEP 3 autopsy ──
    C = ["# Walk-Forward Exam — Report C: Autopsy\n",
         "_Post-hoc fields allowed (firewalled from entry logic). Discovery + spent holdout both learning "
         "material now._\n"]
    for V in ("W-ALL", "W-STAT"):
        C.append("\n## %s\n" % V)
        for lbl, zone in (("discovery", "discovery"), ("holdout", "holdout")):
            mz = bucket_frame(df, zone); sel = selection(mz, V, predict)
            win = sel[sel["out"] == "TP"]; los = sel[sel["out"] == "SL"]
            whip = (los["whip"] == "WHIPSAW").mean() * 100 if len(los) else float("nan")
            hh = pd.to_datetime(sel["ts"], unit="s").dt.hour
            C.append("\n### %s (win %d / loss %d)\n" % (lbl, len(win), len(los)))
            C.append("- losses: whipsaw %.0f%% vs clean-reverse %.0f%%.\n" % (whip, 100 - whip))
            C.append("- medians win vs loss: O.24 near-win %.3f/%.3f · O.18 tease %.3f/%.3f · O.23 time-in-profit "
                     "%.0f/%.0f · O.12 beyond-SL -/%.3f.\n" % (
                     win["O.24"].median(), los["O.24"].median(), win["O.18"].median(), los["O.18"].median(),
                     win["O.23"].median(), los["O.23"].median(), los["O.12"].median()))
            C.append("- loss hours (top): %s\n" % dict(hh[sel["out"].values == "SL"].value_counts().head(4)))
            if len(los) >= 20:
                wv = wrong_votes(mz, los, V, binners, W, baseline)
                C.append("- most-wrong votes on losses (feature: mean up-vote): %s\n"
                         % ", ".join("%s %.3f" % (f, m) for f, m, _ in wv[:8]))
    open(os.path.join(OUT, "analysis_report_C_autopsy.md"), "w", encoding="utf-8").write("\n".join(C))
    print("wrote analysis_report_D_walkforward.md, analysis_report_C_autopsy.md, walkforward_trades.csv (%d rows)"
          % len(trades))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "freeze"
    A = load_all()
    if mode == "freeze":
        freeze(A)
    elif mode == "unseal":
        unseal(A)


if __name__ == "__main__":
    main()
