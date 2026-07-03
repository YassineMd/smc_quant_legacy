"""S5d — RANGE-CONTEXT CONFLUENCE (pre-registered variant of S5c's context leg, Yassine's design;
multiplicity +4 cells (long/short x locked/unlocked) -> program counter 466).

LEG 5w (replaces S5c's fixed-50): for fire bar b over lookbacks N = 50..100 inclusive, composite
bar = O(b-N+1) vs C(b). LONG eligible <=> EXISTS N with O(b-N+1) > C(b) <=> C(b) < max{open[b-99..
b-49]}; SHORT <=> C(b) > min of the same zone (max/min form, rolling window). Tick-exact cents
comparisons, matching S5c's convention. Both sides can be eligible on one bar (different N) — legal;
the momentum legs pick the side. Universe idx >= 100 (S5c: 50, S5b: 16 — change reported). This is
a STRICTLY LOOSER context than S5c fixed-50 (N=50 is in the zone) — S5d fires are a superset of
S5c's, asserted and stated.

TRIGGERS: legs 1'-4 AND leg 5w, per side; V-LOCKED / V-UNLOCKED exactly as S5c (legs 1'/3/4 from
the committed sweep; both leg-2 variants recomputed from e_sh, locked asserted equal to the sweep).

MEASUREMENT per variant x side: (A) 30-min excursions, S5b machinery verbatim (per-cell non-overlap,
200-draw seed-13 control, Jun-30 regime split, full episode tables, n<20 -> counts only);
(B) every-setup-taken at window end, gross + net taker 0.10% RT; (C) barriers 0.5/0.3/6h with the
S1 path-walker conventions verbatim (ambiguity -> SL flagged, unresolved counted; fires overlap in
time for this block — independent sims).

TOP TABLE: no-context (S5b-r line; unlocked no-context stream reproduced with S5b's global-lockout
semantics and asserted == the original run) vs fixed-50 (S5c, committed CSVs) vs range (S5d) —
fires/day, excursion medians, every-taken mean%, barrier TP% — per variant, both sides. The two
never-measured comparison numbers (barriers on the no-context fire sets; if-taken on committed
sets) are mechanical completions mandated by this study, not new hypothesis cells.
"""
import os, sys, csv, calendar, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
import s5_confluence as S5                             # noqa: E402
from m10_sweep_s5b import load_merged, p9_full, LOCK   # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
WIN = 1800.0; H_S = 6 * 3600.0
TP, SL = 0.005, 0.003
NULL = 100.0 * SL / (TP + SL); BREAKEVEN = 50.0
SEED, N_DRAWS = 13, 200
REGIME_CUT = calendar.timegm((2026, 6, 30, 0, 0, 0))
FIRST = 100                                            # spec: universe idx >= 100
CELLS = (("locked", "long"), ("locked", "short"), ("unlocked", "long"), ("unlocked", "short"))


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    op = np.array([b.open_price for b in bks]); cl = np.array([b.close_price for b in bks])
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    bid_arr = np.array(bids); b0 = bids[0]
    snaps = [b.full_snapshot() for b in bks]
    _, e_sh, _, _, _, _, _, _ = p9_full(snaps)
    df = pd.read_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"))
    assert int(df.bucket_id.iloc[0]) == bids[16] and len(df) == n - 16

    idx = np.arange(16, n)
    sh = e_sh[np.maximum(0, idx - LOCK)]
    spr2 = (2.0 * sh - 1.0) * 100.0
    leg2 = {"locked": {"long": spr2 >= 65.0, "short": -spr2 >= 65.0},
            "unlocked": {"long": sh * 100.0 >= 65.0, "short": (1 - sh) * 100.0 >= 65.0}}
    assert (leg2["locked"]["long"] == df.leg2_long.to_numpy()).all()
    assert (leg2["locked"]["short"] == df.leg2_short.to_numpy()).all()
    l134 = {s: (df["leg1_" + s].to_numpy() & df["leg3_" + s].to_numpy()
                & df["leg4_" + s].to_numpy()) for s in ("long", "short")}

    # leg 5 contexts (tick-exact cents, S5c convention)
    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)
    fix_L = np.zeros(n, bool); fix_S = np.zeros(n, bool)
    fix_L[50:] = rop[1:n - 49] > rcl[50:]; fix_S[50:] = rop[1:n - 49] < rcl[50:]
    rmax = pd.Series(rop).rolling(51).max().shift(49).to_numpy()
    rmin = pd.Series(rop).rolling(51).min().shift(49).to_numpy()
    rng_L = np.zeros(n, bool); rng_S = np.zeros(n, bool)
    ok = np.arange(n) >= FIRST
    rng_L[ok] = rcl[ok] < rmax[ok]; rng_S[ok] = rcl[ok] > rmin[ok]
    both = int((rng_L & rng_S & ok).sum())
    # fixed-50 open (N=50) is inside the 50..100 zone -> strict superset, asserted on the universe
    assert not (fix_L[FIRST:] & ~rng_L[FIRST:]).any() and not (fix_S[FIRST:] & ~rng_S[FIRST:]).any()

    def excursion(i):
        j1 = int(np.searchsorted(et, et[i] + WIN, side="right"))
        w = slice(i + 1, j1)
        if j1 <= i + 1:
            return None
        base = cl[i]
        k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
        return dict(mfe=max(0.0, (float(np.max(hi[w])) - base) / base * 100.0),
                    mae=min(0.0, (float(np.min(lo_[w])) - base) / base * 100.0),
                    end=(float(cl[w][-1]) - base) / base * 100.0,
                    t_up=(float(et[i + 1 + k_up]) - et[i]) / 60.0,
                    t_dn=(float(et[i + 1 + k_dn]) - et[i]) / 60.0)

    def run_cell(fire_rows, side):
        """Per-cell lockout episode stream over sweep-row fire mask (S5c semantics)."""
        out = []; n_eod = 0; n_lk = 0; lock_until = -1e18
        for r in np.flatnonzero(fire_rows):
            i = int(idx[r])
            if et[i] < lock_until:
                n_lk += 1
                continue
            if et[i] + WIN > et[-1]:
                n_eod += 1
                continue
            e = excursion(i)
            e.update(ts=float(et[i]), bid=int(bid_arr[i]), side=side, base=float(cl[i]))
            out.append(e)
            lock_until = et[i] + WIN
        return out, n_lk, n_eod

    # ---- S5d fires + episodes -------------------------------------------------------------------
    inuniv = idx >= FIRST
    d_fire, d_eps, d_excl, att = {}, {}, {}, {}
    for v, s in CELLS:
        f4 = l134[s] & leg2[v][s] & inuniv
        l5 = (rng_L if s == "long" else rng_S)[idx]
        d_fire[(v, s)] = f4 & l5
        att[(v, s)] = (int(f4.sum()), int((f4 & l5).sum()))
        d_eps[(v, s)], nlk, neod = run_cell(f4 & l5, s)
        d_excl[(v, s)] = (nlk, neod)
    l5w_rate = {"long": 100.0 * rng_L[FIRST:].mean(), "short": 100.0 * rng_S[FIRST:].mean()}

    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5d_episodes_%s.csv" % v), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "bucket_id", "side", "baseline", "MFE_pct", "MAE_pct", "end_pct",
                        "t_max_up_min", "t_max_dn_min"])
            for s in ("long", "short"):
                for e in d_eps[(v, s)]:
                    w.writerow([round(e["ts"], 3), e["bid"], s, e["base"], round(e["mfe"], 4),
                                round(e["mae"], 4), round(e["end"], 4), round(e["t_up"], 2),
                                round(e["t_dn"], 2)])

    # ---- comparison sets: committed CSVs (no re-detection) + the one uncommitted stream ---------
    def read_eps(path):
        out = {"long": [], "short": []}
        with open(os.path.join(OUT, path), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[r["side"]].append(dict(ts=float(r["ts"]), bid=int(r["bucket_id"]),
                                           base=float(r["baseline"]), mfe=float(r["MFE_pct"]),
                                           mae=float(r["MAE_pct"]), end=float(r["end_pct"]),
                                           side=r["side"]))
        return out

    s5b_lock = read_eps("s5b_episodes.csv")                       # S5b-r committed (locked, no context)
    assert len(s5b_lock["long"]) == 35 and len(s5b_lock["short"]) == 13
    s5c_eps = {"locked": read_eps("s5c_episodes_locked.csv"), "unlocked": read_eps("s5c_episodes_unlocked.csv")}
    assert [len(s5c_eps[v][s]) for v, s in CELLS] == [16, 7, 34, 15]
    # unlocked no-context: reproduce the ORIGINAL S5b stream (global lockout across sides, idx >= 16)
    fl_u = l134["long"] & leg2["unlocked"]["long"]; fs_u = l134["short"] & leg2["unlocked"]["short"]
    s5b_unlk = {"long": [], "short": []}
    lock_until = -1e18
    for r in range(len(df)):
        i = r + 16
        if not (fl_u[r] or fs_u[r]) or et[i] < lock_until:
            continue
        if et[i] + WIN > et[-1]:
            continue
        s = "long" if fl_u[r] else "short"
        e = excursion(i); e.update(ts=float(et[i]), bid=int(bid_arr[i]), side=s, base=float(cl[i]))
        s5b_unlk[s].append(e)
        lock_until = et[i] + WIN
    assert len(s5b_unlk["long"]) == 55 and len(s5b_unlk["short"]) == 26, \
        (len(s5b_unlk["long"]), len(s5b_unlk["short"]))           # == the original S5b run

    # ---- barrier walker (S1 conventions verbatim, as s5c_barriers) ------------------------------
    def barrier(ep, side):
        i = ep["bid"] - b0; entry = ep["base"]; t_ent = et[i]
        if side == "long":
            upl, dnl = entry * (1 + TP), entry * (1 - SL)
            tp_hit = lambda j: hi[j] >= upl; sl_hit = lambda j: lo_[j] <= dnl
        else:
            dnl, upl = entry * (1 - TP), entry * (1 + SL)
            tp_hit = lambda j: lo_[j] <= dnl; sl_hit = lambda j: hi[j] >= upl
        j = i + 1
        while j < n and st[j] <= t_ent + H_S:
            a, b = tp_hit(j), sl_hit(j)
            if a or b:
                return ("SL" if b else "TP"), (st[j] - t_ent) / 60.0, (a and b)
            j += 1
        return ("EOD" if t_ent + H_S > et[-1] else "UNRESOLVED"), None, False

    def barrier_block(eps_side, side):
        rows = [(e, *barrier(e, side)) for e in eps_side]
        ntp = sum(1 for _e, o, _m, _a in rows if o == "TP"); nsl = sum(1 for _e, o, _m, _a in rows if o == "SL")
        nun = sum(1 for _e, o, _m, _a in rows if o == "UNRESOLVED")
        neod = sum(1 for _e, o, _m, _a in rows if o == "EOD"); namb = sum(1 for _e, _o, _m, a in rows if a)
        res = ntp + nsl; p = 100.0 * ntp / res if res else float("nan")
        mins = [m for _e, _o, m, _a in rows if m is not None]
        return dict(rows=rows, ntp=ntp, nsl=nsl, nun=nun, neod=neod, namb=namb, res=res, p=p,
                    gsum=0.5 * ntp - 0.3 * nsl, nsum=0.5 * ntp - 0.3 * nsl - 0.10 * res,
                    med=float(np.median(mins)) if mins else float("nan"),
                    p90=float(np.percentile(mins, 90)) if mins else float("nan"))

    def iftaken(eps_side, side, fee):
        pnl = np.array([(e["end"] if side == "long" else -e["end"]) - fee for e in eps_side])
        if len(pnl) == 0:
            return None
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return dict(w=len(w_), l=len(l_), f=len(pnl) - len(w_) - len(l_),
                    wr=100.0 * len(w_) / len(pnl), sum=float(pnl.sum()), mean=float(pnl.mean()),
                    aw=float(w_.mean()) if len(w_) else 0.0, al=float(l_.mean()) if len(l_) else 0.0)

    # barrier TP% for committed fixed-50 sets comes from the committed trades CSV, not re-simulated
    s5c_bar = {}
    with open(os.path.join(OUT, "s5c_barrier_trades.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["variant"], r["side"])
            s5c_bar.setdefault(k, [0, 0])
            if r["outcome"] in ("TP", "SL"):
                s5c_bar[k][0 if r["outcome"] == "TP" else 1] += 1

    d_bar = {c: barrier_block(d_eps[c], c[1]) for c in CELLS}
    none_eps = {("locked", "long"): s5b_lock["long"], ("locked", "short"): s5b_lock["short"],
                ("unlocked", "long"): s5b_unlk["long"], ("unlocked", "short"): s5b_unlk["short"]}
    none_bar = {c: barrier_block(none_eps[c], c[1]) for c in CELLS}

    with open(os.path.join(OUT, "s5d_barrier_trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "variant", "side", "entry", "outcome",
                    "minutes_to_resolution", "ambiguous_flag"])
        for v, s in CELLS:
            for e, o, m, a in d_bar[(v, s)]["rows"]:
                w.writerow([round(e["ts"], 3), e["bid"], v, s, e["base"], o,
                            round(m, 2) if m is not None else "", int(a)])

    # ---- controls for the four S5d cells (fixed order, one rng) ---------------------------------
    eligible = np.array([i for i in range(FIRST, n) if et[i] + WIN <= et[-1]])
    rng = np.random.default_rng(SEED)

    def control(n_side):
        if n_side == 0:
            return None
        keys = ("med_mfe", "med_amae", "pct_win", "mean_end")
        draws = {k: [] for k in keys}
        for _ in range(N_DRAWS):
            acc = []
            for i in rng.permutation(eligible):
                if all(abs(et[i] - et[j]) >= WIN for j in acc):
                    acc.append(int(i))
                    if len(acc) == n_side:
                        break
            es = [excursion(i) for i in acc]
            mfe = np.array([e["mfe"] for e in es]); amae = np.array([abs(e["mae"]) for e in es])
            end = np.array([e["end"] for e in es])
            for k, v in (("med_mfe", np.median(mfe)), ("med_amae", np.median(amae)),
                         ("pct_win", 100.0 * np.mean(mfe > amae)), ("mean_end", np.mean(end))):
                draws[k].append(float(v))
        return {k: (float(np.mean(x)), float(np.std(x))) for k, x in draws.items()}

    ctrl = {c: control(len(d_eps[c])) for c in CELLS}
    span_days = (et[-1] - et[0]) / 86400.0
    for c in CELLS:
        print("  S5d %s-%s: 4-leg %d -> +5w %d -> eps %d | barrier TP%% %s"
              % (c[0], c[1], att[c][0], att[c][1], len(d_eps[c]),
                 "%.1f" % d_bar[c]["p"] if d_bar[c]["res"] else "-"), flush=True)

    # ---- report ---------------------------------------------------------------------------------
    def med(ep, key):
        return float(np.median([abs(e[key]) for e in ep])) if ep else float("nan")

    md = ["# S5d — Range-Context Confluence (N ∈ [50,100] zone; locked + unlocked)", "",
          "_**Pre-registered variant of S5c's context leg (Yassine's design); multiplicity +4 cells"
          " -> program counter 466.** Leg 5w: LONG eligible iff C(b) is below the MAX open of the"
          " zone bars b-99..b-49 (equivalently EXISTS N in [50,100] with O(b-N+1) > C(b)); SHORT"
          " mirrored on the MIN open. Tick-exact cents, rolling max/min implementation. Both sides"
          " CAN be eligible on one bar (different N) — legal, the momentum legs pick the side"
          " (%d such bars in-universe here). **Strictly LOOSER than S5c's fixed-50 — every S5c fire"
          " is an S5d fire (asserted on the universe).** Universe idx >= %d: %d evaluable rows"
          " (S5c: >= 50, 12,525; S5b: >= 16, 12,559). Legs 1'-4 identical to S5c per variant;"
          " machinery identical (per-cell non-overlap, seed-13 control, Jun-30 regime split,"
          " n<20 -> counts only). Barrier block: fires simulated INDEPENDENTLY and may overlap in"
          " time. Comparison-table completions measured fresh under this mandate: barriers on the"
          " two no-context fire sets and if-taken lines on committed sets — mechanical, not new"
          " hypothesis cells; the unlocked no-context stream is reproduced with S5b's original"
          " global-lockout semantics and asserted == the original run (55L/26S)._"
          % (both, FIRST, n - FIRST), "",
          "## TOP — no context vs fixed-50 vs range, per variant x side", "",
          "| variant | side | context | eps | fires/day | med MFE | med \\|MAE\\| | taken mean% (gross) | barrier TP% (res) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for v, s in CELLS:
        rows3 = (("none", none_eps[(v, s)], none_bar[(v, s)]["p"], none_bar[(v, s)]["res"]),
                 ("fixed-50", s5c_eps[v][s],
                  100.0 * s5c_bar[(v, s)][0] / max(1, sum(s5c_bar[(v, s)])), sum(s5c_bar[(v, s)])),
                 ("range", d_eps[(v, s)], d_bar[(v, s)]["p"], d_bar[(v, s)]["res"]))
        for nm, ep, btp, bres in rows3:
            it = iftaken(ep, s, 0.0)
            md.append("| %s | %s | %s | %d | %.2f | %.3f | %.3f | %s | %s |" % (
                v.upper(), s, nm, len(ep), len(ep) / span_days, med(ep, "mfe"), med(ep, "mae"),
                "%+.3f" % it["mean"] if it else "-", "%.1f" % btp if bres else "-"))
    md += ["", "## Attrition (universe idx >= %d)" % FIRST, "",
           "leg 5w standalone eligibility: long %.1f%% / short %.1f%% of universe bars (loose by design)."
           % (l5w_rate["long"], l5w_rate["short"]), "",
           "| cell | 4-leg fires | + leg 5w | kept | episodes | locked-skip / eod |", "|---|---|---|---|---|---|"]
    for c in CELLS:
        md.append("| %s-%s | %d | %d | %.0f%% | %d | %d / %d |" % (
            c[0].upper(), c[1], att[c][0], att[c][1],
            100.0 * att[c][1] / att[c][0] if att[c][0] else 0.0, len(d_eps[c]), *d_excl[c]))
    md.append("")

    for v, s in CELLS:
        ep_c = d_eps[(v, s)]; nn = len(ep_c)
        md += ["## %s-%s — %d episodes (%.2f fires/day)" % (v.upper(), s, nn, nn / span_days), ""]
        if nn < S5.UNDERPOWERED_N:
            md += ["**A. UNDERPOWERED (n = %d < %d): counts only — no distributions/control; B and C"
                   " below are counts, no verdict language.**" % (nn, S5.UNDERPOWERED_N), ""]
        else:
            stt = S5.ep_stats(ep_c); c = ctrl[(v, s)]
            md += ["**A. Excursions** — full episode table:", "",
                   "| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |", "|---|---|---|---|---|---|---|---|"]
            for e in ep_c:
                md.append("| %s | %d | %.2f | %.3f | %.3f | %+.3f | %.1f | %.1f |" % (
                    time.strftime("%m-%d %H:%M", time.gmtime(e["ts"])), e["bid"], e["base"],
                    e["mfe"], e["mae"], e["end"], e["t_up"], e["t_dn"]))
            pre = [e for e in ep_c if e["ts"] < REGIME_CUT]; post = [e for e in ep_c if e["ts"] >= REGIME_CUT]
            md += ["", "med MFE %.3f vs med \\|MAE\\| %.3f; MFE>\\|MAE\\| %.1f%%; end mean %+.3f%% / med %+.3f%%."
                   % (stt["mfe"]["med"], stt["amae"]["med"], stt["pct_win"], stt["end_mean"], stt["end_med"]),
                   "Control (n=%d, %d draws, seed %d): med MFE %.3f±%.3f | med \\|MAE\\| %.3f±%.3f | "
                   "win %.1f±%.1f | mean end %+.3f±%.3f."
                   % (nn, N_DRAWS, SEED, c["med_mfe"][0], c["med_mfe"][1], c["med_amae"][0],
                      c["med_amae"][1], c["pct_win"][0], c["pct_win"][1], c["mean_end"][0], c["mean_end"][1]),
                   "Regime: pre n=%d med end %s | post n=%d med end %s."
                   % (len(pre), "%+.3f%%" % float(np.median([e["end"] for e in pre])) if pre else "-",
                      len(post), "%+.3f%%" % float(np.median([e["end"] for e in post])) if post else "-"), ""]
        itg = iftaken(ep_c, s, 0.0); itn = iftaken(ep_c, s, 0.10)
        if itg:
            md += ["**B. Every setup taken (window end):**",
                   "- GROSS: W/L/F %d/%d/%d | win %.1f%% | sum %+.2f%% | mean %+.3f%% | avgW %+.3f / avgL %+.3f"
                   % (itg["w"], itg["l"], itg["f"], itg["wr"], itg["sum"], itg["mean"], itg["aw"], itg["al"]),
                   "- NET 0.10%% RT: W/L/F %d/%d/%d | win %.1f%% | sum %+.2f%% | mean %+.3f%%"
                   % (itn["w"], itn["l"], itn["f"], itn["wr"], itn["sum"], itn["mean"]), ""]
        x = d_bar[(v, s)]
        md += ["**C. Barriers 0.5/0.3/6h** (refs: null %.1f%%, fee breakeven %.1f%%): TP %d / SL %d / "
               "unres %d / eod %d (ambig %d) -> TP %s of resolved; expectancy gross %s net %s; sums "
               "gross %+.2f%% net %+.2f%%; res-time med %s / p90 %s min."
               % (NULL, BREAKEVEN, x["ntp"], x["nsl"], x["nun"], x["neod"], x["namb"],
                  "%.1f%%" % x["p"] if x["res"] else "-",
                  "%+.3f%%" % (x["p"] / 100 * 0.5 - (1 - x["p"] / 100) * 0.3) if x["res"] else "-",
                  "%+.3f%%" % (x["p"] / 100 * 0.5 - (1 - x["p"] / 100) * 0.3 - 0.10) if x["res"] else "-",
                  x["gsum"], x["nsum"],
                  "%.1f" % x["med"] if x["med"] == x["med"] else "-",
                  "%.1f" % x["p90"] if x["p90"] == x["p90"] else "-"), ""]

    md += ["## Honest flags",
           "- Leg 5w is looser by construction; its standalone pass rate (%.0f-%.0f%%) means the"
           " momentum legs, not the context, do nearly all the filtering here."
           % (min(l5w_rate.values()), max(l5w_rate.values())),
           "- Same %.2f-day tape as S5b/S5c; 1m spent for mining; barrier fires overlap in time"
           " (independent sims)." % span_days,
           "- No other ranges, no threshold variants were run.",
           "", "## HARD STOP", "Judged once; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5d.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] report + 2 episode CSVs + barrier CSV written" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
