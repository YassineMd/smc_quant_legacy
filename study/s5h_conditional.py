"""S5h — CONDITIONAL BASELINE ENTRY (Yassine's routing rule, corrected translation; NO resting
limit anywhere; pre-registered; multiplicity +2 cells (long/short) -> program counter 512).

FIRES: identical to S5g — locked legs 1'-4 + ALL-form pullback leg 5, cluster rule -> armed setups
(21340 anchor asserted). Same 7-setup universe on this tape.

ENTRY ROUTER at the fire bar vs THAT bar's POC baseline B(fire):
 LONG : close >  B -> WAIT-FOR-TOUCH   |  close <= B -> MARKET at fire close, immediately.
 SHORT: close <  B -> WAIT-FOR-TOUCH   |  close >= B -> MARKET at fire close.
 WAIT leg = S5g touch semantics VERBATIM (armed 30 min, entry at the first bar touching the CURRENT
 moving baseline, entry = B(t); fire-bar self-touch counts; first-touch geometry check: open above
 the line = valid / == tie / through = degenerate, counted). No touch -> UNTRIGGERED + 30-min
 counterfactual from fire close. ALL entries taker; primary net line 0.10% RT.

EXITS both arms (S5g walk code verbatim): A. FIXED TP+0.5/SL-0.3 (S1, 6h cap, unresolved excluded);
B. SIGNAL-DEATH locked (S5e rule, hard SL -0.3, cap close realized).

COMPARISON on the same setups: ALL-MARKET and ALL-TOUCH recomputed here with the same code and
PARITY-ASSERTED against the committed s5g_episodes.csv (sid-for-sid), then the CONDITIONAL router —
three strategies x two arms, plus per-setup rows so each routing decision is visible.

FORWARD LEDGER: study/out/forward_ledger.md gains the CONDITIONAL config as a frozen entry beside
S5e's (freeze discipline: router + both arms, no parameter changes ever). ~7 setups on this tape ->
machinery-correctness + registration run, NOT a verdict (underpowered rule stands). HARD STOP.
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
FEE = 0.10
ANCHOR = 21340; FIRST = 100
ARMS = ("FIXED", "SIGNAL")
UNDER_N = 20
LEDGER = os.path.join(OUT, "forward_ledger.md")


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
    new_L = np.zeros(n, bool); new_L[okk] = rcl[okk] < zmin[okk]
    new_S = np.zeros(n, bool); new_S[okk] = rcl[okk] > zmax[okk]
    assert not new_L[ANCHOR - b0], "21340 regression anchor violated"
    fire = {"long": df.fire_long.to_numpy() & new_L[idx],
            "short": df.fire_short.to_numpy() & new_S[idx]}

    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95
    rbase = np.round(base * 100).astype(np.int64)

    memo = {}

    def sig_exit(j, side):
        if j not in memo:
            memo[j] = (sel_markers(sum0, j), e_sh[max(0, j - LOCK)])
        mk, sh = memo[j]
        if side == "long":
            return bool(mk) and mk[0][2] < 0 and sh < 0.5
        return bool(mk) and mk[0][2] > 0 and (1.0 - sh) < 0.5

    def walk_exit(j_e, entry, side, arm, entry_at_close):
        long = side == "long"
        sl_lvl = entry * (1 - 0.003) if long else entry * (1 + 0.003)
        tp_lvl = entry * (1 + 0.005) if long else entry * (1 - 0.005)
        t_e = float(st[j_e]) if not entry_at_close else float(et[j_e])
        out, px, mins, amb = None, None, None, False
        if not entry_at_close:
            if (lo_[j_e] <= sl_lvl) if long else (hi[j_e] >= sl_lvl):
                out, px, mins, amb = "SL", sl_lvl, 0.0, True
            elif arm == "SIGNAL" and sig_exit(j_e, side):
                out, px, mins = "SIGNAL", float(cl[j_e]), (et[j_e] - t_e) / 60.0
        if out is None:
            j = j_e + 1; j_last = j_e
            while j < n and st[j] <= t_e + H_S:
                j_last = j
                if (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl):
                    hit_tp = (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl)
                    out, px, mins = "SL", sl_lvl, (st[j] - t_e) / 60.0
                    amb = arm == "FIXED" and hit_tp
                    break
                if arm == "FIXED":
                    if (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl):
                        out, px, mins = "TP", tp_lvl, (st[j] - t_e) / 60.0
                        break
                else:
                    if sig_exit(j, side):
                        out, px, mins = "SIGNAL", float(cl[j]), (et[j] - t_e) / 60.0
                        break
                j += 1
            if out is None:
                if arm == "FIXED":
                    out = "EOD" if t_e + H_S > et[-1] else "UNRESOLVED"
                else:
                    out, px, mins = "CAP", float(cl[j_last]), (et[j_last] - t_e) / 60.0
        pnl = None if px is None else ((px - entry) / entry if long else (entry - px) / entry) * 100.0
        return out + ("*" if amb else ""), pnl, mins

    def touch_leg(b, s):
        """S5g touch semantics verbatim: (status, j_t, entry, delay, cf_mfe, cf_mae)."""
        long = s == "long"
        j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
        j_t = None
        for j in range(b, j1):
            if (lo_[j] <= base[j]) if long else (hi[j] >= base[j]):
                j_t = j
                break
        if j_t is None:
            cf_mfe = cf_mae = ""
            if j1 > b + 1:
                w = slice(b + 1, j1); c0 = cl[b]
                cf_mfe = round(max(0.0, ((np.max(hi[w]) - c0) / c0 if long
                                         else (c0 - np.min(lo_[w])) / c0) * 100.0), 4)
                cf_mae = round(min(0.0, ((np.min(lo_[w]) - c0) / c0 if long
                                         else (c0 - np.max(hi[w])) / c0) * 100.0), 4)
            return "UNTRIGGERED", None, None, None, cf_mfe, cf_mae
        if rop[j_t] == rbase[j_t]:
            return "TIE", j_t, None, None, "", ""
        if (rop[j_t] < rbase[j_t]) if long else (rop[j_t] > rbase[j_t]):
            return "DEGENERATE", j_t, None, None, "", ""
        return "TOUCHED", j_t, float(base[j_t]), (st[j_t] - et[b]) / 60.0 if j_t > b else 0.0, "", ""

    # ---- armed setups (S5g cluster, verbatim -> identical sids) ---------------------------------
    setups = []
    raw_ct = {}
    for s in ("long", "short"):
        bars = [int(idx[r]) for r in np.flatnonzero(fire[s])]
        raw_ct[s] = len(bars)
        armed_until = -1e18
        for b in bars:
            if et[b] < armed_until:
                setups[-1]["absorbed"] += 1
                continue
            setups.append(dict(sid=len(setups) + 1, side=s, b=b, absorbed=0))
            armed_until = et[b] + WIN

    # ---- three strategies per setup --------------------------------------------------------------
    per = {}                                                        # sid -> per-strategy dict
    rows = []
    for su in setups:
        sid, s, b = su["sid"], su["side"], su["b"]
        long = s == "long"
        B = float(base[b]); c0 = float(cl[b])
        route = "WAIT" if ((c0 > B) if long else (c0 < B)) else "MARKET"
        d = dict(mkt={}, tch={}, cond={}, route=route, side=s, fire_bid=int(bid_arr[b]))
        for arm in ARMS:                                            # ALL-MARKET
            out, pnl, mins = walk_exit(b, c0, s, arm, entry_at_close=True)
            d["mkt"][arm] = (out, pnl, mins)
        stt, j_t, entry_t, delay, cf_mfe, cf_mae = touch_leg(b, s)  # ALL-TOUCH
        d["tch_status"] = stt
        for arm in ARMS:
            d["tch"][arm] = walk_exit(j_t, entry_t, s, arm, False) if stt == "TOUCHED" else (stt, None, None)
        if route == "MARKET":                                       # CONDITIONAL
            d["cond_entry"] = ("MARKET", c0, "")
            for arm in ARMS:
                d["cond"][arm] = d["mkt"][arm]
        else:
            d["cond_entry"] = ("TOUCH", entry_t if stt == "TOUCHED" else "", delay if stt == "TOUCHED" else "")
            for arm in ARMS:
                d["cond"][arm] = d["tch"][arm]
        d["cf"] = (cf_mfe, cf_mae)
        per[sid] = d
        for arm in ARMS:
            out, pnl, mins = d["cond"][arm]
            rows.append(dict(sid=sid, side=s, fire_bid=d["fire_bid"], route=route,
                             entry_style=d["cond_entry"][0],
                             entry=round(d["cond_entry"][1], 4) if d["cond_entry"][1] != "" else "",
                             delay=round(d["cond_entry"][2], 2) if d["cond_entry"][2] != "" else "",
                             arm=arm, outcome=out,
                             pnl=round(pnl, 4) if pnl is not None else "",
                             mins=round(mins, 2) if mins is not None else "",
                             cf_mfe=cf_mfe if route == "WAIT" and stt == "UNTRIGGERED" else "",
                             cf_mae=cf_mae if route == "WAIT" and stt == "UNTRIGGERED" else ""))

    # ---- parity vs the committed S5g CSV (same sids, same code paths) ---------------------------
    g5 = {"TOUCHED": {}, "MKT_AT_FIRE": {}}
    for r in csv.DictReader(open(os.path.join(OUT, "s5g_episodes.csv"), encoding="utf-8")):
        if r["status"] in g5 and r["pnl"] != "":
            g5[r["status"]][(int(r["sid"]), r["arm"])] = float(r["pnl"])
    for sid, d in per.items():
        for arm in ARMS:
            if d["tch_status"] == "TOUCHED" and d["tch"][arm][1] is not None:
                assert abs(d["tch"][arm][1] - g5["TOUCHED"][(sid, arm)]) < 1e-3
            if d["mkt"][arm][1] is not None:
                assert abs(d["mkt"][arm][1] - g5["MKT_AT_FIRE"][(sid, arm)]) < 1e-3
    print("[%3.0fs] parity vs s5g_episodes.csv OK (%d setups)" % (time.time() - t0, len(setups)), flush=True)

    with open(os.path.join(OUT, "s5h_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sid", "side", "fire_bid", "route", "entry_style",
                                          "entry", "delay", "arm", "outcome", "pnl", "mins",
                                          "cf_mfe", "cf_mae"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    def agg(sel):
        ts_ = [r for r in rows if sel(r) and r["pnl"] != ""]
        pnl = np.array([r["pnl"] for r in ts_], float)
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return dict(n=len(ts_), nw=len(w_), nl=len(l_), sum=float(pnl.sum()) if len(ts_) else 0.0,
                    mean=float(pnl.mean()) if len(ts_) else float("nan"))

    md = ["# S5h — Conditional Baseline Entry (the routing rule, locked pullback setups)", "",
          "_**Pre-registered; +2 cells -> counter 512. NO resting limit anywhere.** Router at the"
          " fire bar vs B(fire): long WAITs for the moving-line touch only when the close is above"
          " the baseline, else enters MARKET at the fire close (short mirrored). WAIT leg = S5g"
          " semantics verbatim; all entries taker, net line %.2f%% RT. Same armed-setup universe as"
          " S5g (cluster rule; anchor 21340 holds); ALL-MARKET and ALL-TOUCH recomputed and parity-"
          "asserted sid-for-sid against the committed s5g_episodes.csv. **~%d setups on this tape:"
          " machinery-correctness + forward-ledger registration, NOT a verdict.**_"
          % (FEE, len(setups)), "",
          "## 1. Router split", "",
          "| side | setups | MARKET | WAIT | WAIT->touched | WAIT->untriggered | WAIT->tie/degen |",
          "|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        sus = [d for d in per.values() if d["side"] == s]
        wt = [d for d in sus if d["route"] == "WAIT"]
        md.append("| %s | %d | %d | %d | %d | %d | %d |" % (
            s, len(sus), sum(1 for d in sus if d["route"] == "MARKET"), len(wt),
            sum(1 for d in wt if d["tch_status"] == "TOUCHED"),
            sum(1 for d in wt if d["tch_status"] == "UNTRIGGERED"),
            sum(1 for d in wt if d["tch_status"] in ("TIE", "DEGENERATE"))))
    md += ["", "## 2. Conditional economics per side x arm (all cells n < %d -> counts only)" % UNDER_N,
           "", "| cell | n | W/L | sum | mean | net %.2f |" % FEE, "|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        for arm in ARMS:
            x = agg(lambda r, s=s, a=arm: r["side"] == s and r["arm"] == a)
            if x["n"]:
                md.append("| %s-%s | %d | %d/%d | %+.2f%% | %+.3f%% | %+.3f%% |" % (
                    s, arm, x["n"], x["nw"], x["nl"], x["sum"], x["mean"], x["mean"] - FEE))
            else:
                md.append("| %s-%s | 0 | - | - | - | - |" % (s, arm))
    md += ["", "## 3. The comparison — same setups, three entry strategies (pnl % gross; * = ambig flag)",
           "", "| sid | side | route | MKT fix | MKT sig | TOUCH fix | TOUCH sig | COND fix | COND sig |",
           "|---|---|---|---|---|---|---|---|---|"]

    def fmt(t):
        o, p, _m = t
        return ("%+.3f" % p) if p is not None else o[:7]
    for sid in sorted(per):
        d = per[sid]
        md.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            sid, d["side"], d["route"], fmt(d["mkt"]["FIXED"]), fmt(d["mkt"]["SIGNAL"]),
            fmt(d["tch"]["FIXED"]), fmt(d["tch"]["SIGNAL"]),
            fmt(d["cond"]["FIXED"]), fmt(d["cond"]["SIGNAL"])))
    for arm in ARMS:
        tots = []
        for strat in ("mkt", "tch", "cond"):
            ps = [per[sid][strat][arm][1] for sid in per if per[sid][strat][arm][1] is not None]
            tots.append("%+.2f%% (n=%d)" % (sum(ps), len(ps)) if ps else "-")
        md.append("| **sum %s** | | | %s | | %s | | %s | |" % (arm, tots[0], tots[1], tots[2]))
    md += ["", "## 4-5. Registration",
           "The CONDITIONAL router (this exact rule, both arms, no parameter changes ever) is now a"
           " frozen entry in the forward ledger beside S5e — see study/out/forward_ledger.md."
           " Underpowered by construction on this tape; the ledger accumulates forward setups"
           " (~1/day) until n >= 20 per side.",
           "", "## HARD STOP", "Judged once; forward tape is the judge."]
    with open(os.path.join(OUT, "analysis_report_S5h.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    # ---- forward ledger --------------------------------------------------------------------------
    ledger = """# FORWARD LEDGER — frozen configurations under test on forward tape

Freeze discipline: entries here are NEVER re-tuned. Each new snapshot re-runs them unchanged and
appends results; a config graduates or dies on accumulated forward sample only (n >= 20 per side).

| id | registered | fires (frozen) | entry (frozen) | exits (frozen) | fee basis | status |
|---|---|---|---|---|---|---|
| S5E-SIGDEATH | 2026-07-03 (2ec478a) | S5b locked+unlocked legs 1'-4 + EXISTS-form range context (S5d) | market at fire close | signal-death (newest marker against + own-side share < 50, AND) with hard SL -0.3, 6h cap | taker/taker 0.10 | 3/4 cells net-positive on spent tape; awaiting forward n |
| S5H-CONDROUTER | 2026-07-04 (this commit) | locked legs 1'-4 + ALL-form pullback leg 5 + cluster rule (anchor 21340 must stay rejected) | ROUTER: close beyond baseline -> MARKET at close; else WAIT 30 min for moving-baseline touch (taker) | arm A fixed TP+0.5/SL-0.3; arm B signal-death locked | taker/taker 0.10 | registered; ~1 setup/day; underpowered until forward n >= 20 |

Re-run recipe per new snapshot: study/s5e_signal_exit.py (needs the S5b/S5d chain) and
study/s5h_conditional.py (self-contained on the merged tape + sweep parquet).
"""
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.write(ledger)
    print("[%3.0fs] report + CSV + forward ledger written" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
