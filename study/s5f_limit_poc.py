"""S5f — LIMIT-AT-POC ENTRY STUDY (LOCKED variant only; CORRECTED context leg; pre-registered;
multiplicity +4 cells (side x exit-arm) -> program counter 506).

STEP 0 — idx 21340 post-mortem (printed before the study, report section 0): under S5d's
EXISTS-form leg 5w the bar was LONG-eligible because SOME zone open sat above the close; the
corrected ALL-form (C < MIN open of zone bars b-99..b-49 — the whole zone reads net-down, the
PULLBACK rule) rejects it. 21340 is the permanent regression anchor for leg 5 (asserted).

LEG 5 CORRECTED: LONG iff C(b) < min{open[b-99..b-49]}; SHORT iff C(b) > max of the same zone;
tick-exact ties excluded, counted. Universe idx >= 100. Fires RE-DETECTED: sweep-locked legs 1'-4
AND corrected leg 5 (a strict subset of S5d-locked fires by construction — new fires asserted 0).

LIMIT ENTRY per fire (independent; orders may overlap in time — stated): limit = the terminal's POC
baseline at the fire bar — the visible gray center line, `_bucket_row`'s slow POC EMA
(terminal.py: baseline = poc_price * 0.05 + prev * 0.95, seeded at the first bar; carried here
verbatim over the merged tape; seed weight at the fires < 0.7%). Side sanity: long limit must sit
BELOW fire close, short ABOVE -> else DEGENERATE, skipped, counted. Rest window 30 min (bar-END
window, matching the episode convention); fill = bar low <= limit (long) / high >= limit (short),
entry AT the limit price, fill minute from the touch bar's START (S1 convention). TOUCH-FILL
OPTIMISM flagged: a real maker order needs a trade-through, not a touch. No fill -> CANCELLED,
counted, with the 30-min MFE/MAE counterfactual from the FIRE close.

EXITS from FILL, two arms per side, 6h cap anchored at the fill time:
 A. FIXED TP +0.5% / SL -0.3% from the fill (S1 walker verbatim). Fill-bar handling (conservative,
    documented): an SL touch on the fill bar itself -> SL flagged ambiguous; a TP touch on the fill
    bar is IGNORED (sequence unknowable, no look-ahead credit); the walk proper starts next bar.
    Unresolved at cap excluded from rates, counted.
 B. SIGNAL-DEATH, S5e LOCKED rule verbatim: newest confirmed locked marker against the position AND
    own-side locked eff-agg share < 50% at bar close (evaluable from the fill bar's close), hard SL
    -0.3% intrabar beneath (fill-bar SL flagged as in A), 6h cap -> exit at cap close (realized,
    S5e semantics — arm A and B keep their reference studies' cap conventions; stated).

FEES: expectancy net at MAKER-in/TAKER-out 0.065% RT (the limit entry's point) AND taker/taker
0.10% for comparability. Underpowered rule n < 20 -> counts only. HARD STOP after the report.
"""
import os, sys, csv, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict                       # noqa: E402
from m10_sweep_s5b import load_merged, p9_full, sel_markers, LOCK   # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
WIN = 1800.0; H_S = 6 * 3600.0
FEE_MK, FEE_TK = 0.065, 0.10
ANCHOR = 21340
FIRST = 100
ARMS = ("FIXED", "SIGNAL")
CELLS = tuple((s, a) for s in ("long", "short") for a in ARMS)
UNDER_N = 20


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    op = np.array([b.open_price for b in bks])
    bid_arr = np.array(bids); b0 = bids[0]
    snaps = [b.full_snapshot() for b in bks]
    _, e_sh, _, _, _, _, _, sum0 = p9_full(snaps)
    df = pd.read_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"))
    idx = np.arange(16, n)

    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)
    zmax = pd.Series(rop).rolling(51).max().shift(49).to_numpy()
    zmin = pd.Series(rop).rolling(51).min().shift(49).to_numpy()
    okk = np.arange(n) >= FIRST
    old_L = np.zeros(n, bool); old_L[okk] = rcl[okk] < zmax[okk]          # S5d EXISTS form
    old_S = np.zeros(n, bool); old_S[okk] = rcl[okk] > zmin[okk]
    new_L = np.zeros(n, bool); new_L[okk] = rcl[okk] < zmin[okk]          # CORRECTED ALL form
    new_S = np.zeros(n, bool); new_S[okk] = rcl[okk] > zmax[okk]
    tie_L = int((okk & (rcl == zmin)).sum()); tie_S = int((okk & (rcl == zmax)).sum())

    # ---- STEP 0: the 21340 post-mortem ----------------------------------------------------------
    a = ANCHOR - b0
    zone = slice(a - 99, a - 48)                                          # opens for N = 100..50
    k_wit = (a - 99) + int(np.argmax(rop[zone]))                          # strongest EXISTS witness
    n_wit = a - k_wit + 1
    pm = ["## 0. Post-mortem — idx 21340 (permanent leg-5 regression anchor)",
          "",
          "C(21340) = %.2f. S5d's EXISTS form was satisfied by N = %d: open(b-N+1) = open(idx %d) "
          "= %.2f > close — ONE old open above the close was enough, even though the zone's MINIMUM "
          "open is %.2f, i.e. the zone as a whole did NOT read net-down. Corrected ALL form: "
          "C = %.2f >= min open %.2f -> **long-ineligible. Rejected.**"
          % (cl[a], n_wit, bid_arr[k_wit], rop[k_wit] / 100.0, zmin[a] / 100.0,
             cl[a], zmin[a] / 100.0)]
    fl_sweep = df.fire_long.to_numpy(); r_a = a - 16
    assert bool(fl_sweep[r_a]) and bool(old_L[a]) and not bool(new_L[a]), "anchor regression failed"
    in_eps = any(int(r["bucket_id"]) == ANCHOR for r in
                 csv.DictReader(open(os.path.join(OUT, "s5d_episodes_locked.csv"), encoding="utf-8")))
    pm.append("")
    pm.append("Sweep check: locked legs 1'-4 fired at 21340 and S5d's leg 5w passed it (%s an S5d "
              "locked episode); the corrected leg kills it. Asserted in code — any future change "
              "to leg 5 must keep rejecting this bar." % ("it became" if in_eps else "it was not"))
    print("\n".join(pm), flush=True)

    # ---- re-detected fires + funnel -------------------------------------------------------------
    fire = {"long": fl_sweep & new_L[idx], "short": df.fire_short.to_numpy() & new_S[idx]}
    oldf = {"long": fl_sweep & old_L[idx], "short": df.fire_short.to_numpy() & old_S[idx]}
    for s in ("long", "short"):
        assert not (fire[s] & ~oldf[s]).any()                             # strict subset -> no new
    funnel = {s: dict(old=int(oldf[s].sum()), new=int(fire[s].sum()),
                      killed=int((oldf[s] & ~fire[s]).sum())) for s in ("long", "short")}

    # POC baseline — terminal _bucket_row recursion, verbatim, seeded at bar 0
    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])
    base = np.empty(n)
    base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95

    memo = {}

    def sig_exit(j, side):
        if j not in memo:
            memo[j] = (sel_markers(sum0, j), e_sh[max(0, j - LOCK)])
        mk, sh = memo[j]
        if side == "long":
            return bool(mk) and mk[0][2] < 0 and sh < 0.5
        return bool(mk) and mk[0][2] > 0 and (1.0 - sh) < 0.5

    trades = []                                                           # long-format rows
    for s in ("long", "short"):
        long = s == "long"
        for r in np.flatnonzero(fire[s]):
            b = int(idx[r])
            lim = float(base[b]); c0 = float(cl[b])
            row = dict(ts=float(et[b]), bid=int(bid_arr[b]), side=s, fire_close=c0,
                       limit=lim, status="", fillmin="", arm="", outcome="", pnl="",
                       mins="", cf_mfe="", cf_mae="")
            if (lim >= c0) if long else (lim <= c0):
                row["status"] = "DEGENERATE"
                trades.append(row)
                continue
            j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
            jf = None
            for j in range(b + 1, j1):
                if (lo_[j] <= lim) if long else (hi[j] >= lim):
                    jf = j
                    break
            if jf is None:
                row["status"] = "CANCELLED"
                if j1 > b + 1:
                    w = slice(b + 1, j1)
                    row["cf_mfe"] = round(max(0.0, ((np.max(hi[w]) - c0) / c0 if long
                                                    else (c0 - np.min(lo_[w])) / c0) * 100.0), 4)
                    row["cf_mae"] = round(min(0.0, ((np.min(lo_[w]) - c0) / c0 if long
                                                    else (c0 - np.max(hi[w])) / c0) * 100.0), 4)
                trades.append(row)
                continue
            t_fill = float(st[jf]); fillmin = (t_fill - et[b]) / 60.0
            sl_lvl = lim * (1 - 0.003) if long else lim * (1 + 0.003)
            tp_lvl = lim * (1 + 0.005) if long else lim * (1 - 0.005)
            for arm in ARMS:
                rr = dict(row, status="FILLED", fillmin=round(fillmin, 2), arm=arm)
                out, px, mins, amb = None, None, None, False
                if (lo_[jf] <= sl_lvl) if long else (hi[jf] >= sl_lvl):   # fill-bar SL, flagged
                    out, px, mins, amb = "SL", sl_lvl, 0.0, True
                elif arm == "SIGNAL" and sig_exit(jf, s):
                    out, px, mins = "SIGNAL", float(cl[jf]), (et[jf] - t_fill) / 60.0
                if out is None:
                    j = jf + 1; j_last = jf
                    while j < n and st[j] <= t_fill + H_S:
                        j_last = j
                        if (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl):
                            hit_tp = (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl)
                            out, px, mins = "SL", sl_lvl, (st[j] - t_fill) / 60.0
                            amb = arm == "FIXED" and hit_tp        # both-in-one-bar -> SL (S1)
                            break
                        if arm == "FIXED":
                            if (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl):
                                out, px, mins = "TP", tp_lvl, (st[j] - t_fill) / 60.0
                                break
                        else:
                            if sig_exit(j, s):
                                out, px, mins = "SIGNAL", float(cl[j]), (et[j] - t_fill) / 60.0
                                break
                        j += 1
                    if out is None:
                        if arm == "FIXED":
                            out = "EOD" if t_fill + H_S > et[-1] else "UNRESOLVED"
                        else:
                            out, px, mins = "CAP", float(cl[j_last]), (et[j_last] - t_fill) / 60.0
                rr["outcome"] = out + ("*" if amb else "")
                if px is not None:
                    pnl = ((px - lim) / lim if long else (lim - px) / lim) * 100.0
                    rr["pnl"] = round(pnl, 4); rr["mins"] = round(mins, 2)
                trades.append(rr)

    with open(os.path.join(OUT, "s5f_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "side", "status", "fire_close", "limit_price",
                    "fill_minute", "exit_arm", "outcome", "pnl_pct", "minutes_held",
                    "cf_mfe_pct", "cf_mae_pct"])
        for t in trades:
            w.writerow([round(t["ts"], 3), t["bid"], t["side"], t["status"], t["fire_close"],
                        round(t["limit"], 4), t["fillmin"], t["arm"], t["outcome"], t["pnl"],
                        t["mins"], t["cf_mfe"], t["cf_mae"]])

    # ---- aggregates ------------------------------------------------------------------------------
    def cell(s, arm):
        return [t for t in trades if t["side"] == s and t["arm"] == arm and t["pnl"] != ""]

    def agg(ts_):
        pnl = np.array([t["pnl"] for t in ts_], float)
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return dict(n=len(ts_), nw=len(w_), nl=len(l_),
                    aw=float(w_.mean()) if len(w_) else 0.0,
                    al=float(l_.mean()) if len(l_) else 0.0,
                    eg=float(pnl.mean()) if len(ts_) else float("nan"),
                    mh=float(np.median([t["mins"] for t in ts_])) if ts_ else float("nan"))

    stat = {"long": {}, "short": {}}
    for s in ("long", "short"):
        deg = sum(1 for t in trades if t["side"] == s and t["status"] == "DEGENERATE")
        cans = [t for t in trades if t["side"] == s and t["status"] == "CANCELLED"]
        fills = sorted({t["bid"] for t in trades if t["side"] == s and t["status"] == "FILLED"})
        fmins = sorted(float(t["fillmin"]) for t in trades
                       if t["side"] == s and t["status"] == "FILLED" and t["arm"] == "FIXED")
        stat[s] = dict(deg=deg, canc=len(cans), fills=len(fills), fmins=fmins,
                       cf_mfe=[float(t["cf_mfe"]) for t in cans if t["cf_mfe"] != ""],
                       cf_mae=[float(t["cf_mae"]) for t in cans if t["cf_mae"] != ""])

    # head-to-head references (committed)
    gref = {}
    for r in csv.DictReader(open(os.path.join(OUT, "s5d_grid.csv"), encoding="utf-8")):
        if r["variant"] == "locked" and float(r["sl_pct"]) == 0.30 and r["exit"] == "TP0.5":
            gref[r["side"]] = float(r["exp_net_pct"])
    e5 = {"long": [], "short": []}
    for r in csv.DictReader(open(os.path.join(OUT, "s5e_trades.csv"), encoding="utf-8")):
        if r["variant"] == "locked":
            e5[r["side"]].append(float(r["pnl_pct"]))

    md = ["# S5f — Limit-at-POC entry (LOCKED, corrected pullback context)", "",
          "_**Pre-registered; +4 cells (side x arm) -> counter 506. Corrected leg 5 = ALL-form"
          " pullback (C below the zone's MIN open for longs) — the EXISTS form is retired; ties"
          " excluded (long %d / short %d in-universe). Fires re-detected (locked legs 1'-4 +"
          " corrected leg 5), each simulated independently — resting limits may overlap in time."
          " Touch-fill OPTIMISM flag: a bar-low touch is treated as a maker fill; real queues fill"
          " later or never. Limit = the terminal POC baseline (`_bucket_row` 5%%/95%% POC EMA,"
          " carried verbatim, full-tape seed). Fees: net at maker/taker %.3f%% RT and taker/taker"
          " %.2f%% RT._" % (tie_L, tie_S, FEE_MK, FEE_TK), ""] + pm + ["",
          "## 1. Funnel (re-detected fires vs S5d-locked)", "",
          "| side | S5d fires (EXISTS) | corrected fires (ALL) | killed | new | degenerate | "
          "cancelled | FILLED |", "|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        md.append("| %s | %d | %d | %d | 0 | %d | %d | %d |" % (
            s, funnel[s]["old"], funnel[s]["new"], funnel[s]["killed"],
            stat[s]["deg"], stat[s]["canc"], stat[s]["fills"]))
    md += ["", "## 2. Time-to-fill (filled fires)", ""]
    for s in ("long", "short"):
        fm = stat[s]["fmins"]
        md.append("- %s: n=%d, median %s min, p90 %s min." % (
            s, len(fm), "%.1f" % float(np.median(fm)) if fm else "-",
            "%.1f" % float(np.percentile(fm, 90)) if fm else "-"))
    md += ["", "## 3. Per side x arm (entry = limit price)", "",
           "| cell | n | W/L | avgW | avgL | exp gross | net 0.065 | net 0.10 | med hold (min) |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s, arm in CELLS:
        x = agg(cell(s, arm))
        under = " _(under)_" if x["n"] < UNDER_N else ""
        if x["n"]:
            md.append("| %s-%s%s | %d | %d/%d | %+.3f | %+.3f | %+.3f%% | %+.3f%% | %+.3f%% | %.1f |" % (
                s, arm, under, x["n"], x["nw"], x["nl"], x["aw"], x["al"], x["eg"],
                x["eg"] - FEE_MK, x["eg"] - FEE_TK, x["mh"]))
        else:
            md.append("| %s-%s | 0 | - | - | - | - | - | - | - |" % (s, arm))
    md += ["", "## 4. Unfilled counterfactual (30-min from fire close)", ""]
    for s in ("long", "short"):
        cm, ca = stat[s]["cf_mfe"], stat[s]["cf_mae"]
        md.append("- %s cancelled n=%d: the missed move — median MFE %s%% / median MAE %s%%." % (
            s, stat[s]["canc"], "%.3f" % float(np.median(cm)) if cm else "-",
            "%.3f" % float(np.median(ca)) if ca else "-"))
    md += ["", "## 5. Head-to-head vs the locked market-entry cells (net at taker/taker 0.10)",
           "_Imperfect comparison — S5d/S5e ran on the EXISTS-form fire set; this study's set is"
           " the corrected strict subset._", "",
           "| side | S5f FIXED | S5f SIGNAL | S5d grid TP0.5/SL0.3 | S5e signal-death |",
           "|---|---|---|---|---|"]
    for s in ("long", "short"):
        xa = agg(cell(s, "FIXED")); xb = agg(cell(s, "SIGNAL"))
        md.append("| %s | %s | %s | %+.3f%% | %+.3f%% |" % (
            s, "%+.3f%%" % (xa["eg"] - FEE_TK) if xa["n"] else "-",
            "%+.3f%%" % (xb["eg"] - FEE_TK) if xb["n"] else "-",
            gref[s], float(np.mean(e5[s])) - FEE_TK))
    md += ["", "## Honest flags",
           "- Touch-fill optimism: every fill here assumes the resting order trades on a touch.",
           "- The corrected context is strict; small n everywhere -> counts only below n=%d." % UNDER_N,
           "- Arm A keeps S1 cap semantics (unresolved excluded), arm B keeps S5e's (cap close"
           " realized) — per their reference studies; stated.",
           "- Spent tape; forward snapshots are the judge.",
           "", "## HARD STOP", "No re-tuning; one limit rule, two pre-registered arms."]
    with open(os.path.join(OUT, "analysis_report_S5f.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] report + episodes CSV written" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
