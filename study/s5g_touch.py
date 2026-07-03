"""S5g — BASELINE-TOUCH ENTRY (corrects S5f's mistranslation: a TOUCH TRIGGER on the MOVING POC
baseline, not a resting limit frozen at fire; pre-registered; multiplicity +4 cells -> counter 510).

FIRES: identical to S5f — locked legs 1'-4 + corrected ALL-form pullback leg 5 (regression anchor
21340 asserted rejected). CLUSTER RULE (machinery-honest, new): fires landing inside an ARMED
window collapse into that ONE setup (live = one setup, not stacked orders); raw fires vs armed
setups reported.

ENTRY: from the fire bar the setup is ARMED 30 min (bar-END window). Entry at the FIRST bar whose
range touches THAT BAR'S OWN baseline value — the terminal `_bucket_row` 5%/95% POC EMA re-read
every bar (moving line): long lo[j] <= base[j], short hi[j] >= base[j]; the fire bar itself counts
if it already touches (operator rule; the intrabar touch precedes the close-time fire — optimism
flagged). Entry price = base[j] (touch price), TAKER fill — taker/taker 0.10% RT is the primary net
line. SANITY at the touch bar: price must FALL ONTO the line (long: bar open above the baseline,
cents; short mirrored); open==line -> TIE, open through the line -> DEGENERATE; both counted, the
setup ends there (first-touch decides). No touch in 30 min -> UNTRIGGERED, counted, with the 30-min
counterfactual MFE/MAE from the fire close.

EXITS from entry (same as S5f): A. FIXED TP+0.5/SL-0.3 (S1 walker; entry-bar SL touch -> SL flagged
ambiguous, entry-bar TP ignored; 6h cap from the touch, unresolved excluded). B. SIGNAL-DEATH
locked (S5e rule verbatim; signal evaluable from the touch bar's close; hard SL -0.3 intrabar;
6h cap -> cap close realized). Setups simulated independently; positions may overlap in time.

HEAD-TO-HEAD (one fire set, three entry styles): S5f resting limit (committed CSV; per raw fire —
uncollapsed, caveat stated) vs S5g touch vs MARKET-AT-FIRE computed here on the same armed setups
(entry = fire close, both arms, S5e-style walk from the next bar). Underpowered n < 20 -> counts
only. HARD STOP after the report.
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

    # corrected ALL-form fires (S5f block, verbatim) + anchor regression
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
    rbase = np.round(base * 100).astype(np.int64)                   # cents, for the sanity compares

    memo = {}

    def sig_exit(j, side):
        if j not in memo:
            memo[j] = (sel_markers(sum0, j), e_sh[max(0, j - LOCK)])
        mk, sh = memo[j]
        if side == "long":
            return bool(mk) and mk[0][2] < 0 and sh < 0.5
        return bool(mk) and mk[0][2] > 0 and (1.0 - sh) < 0.5

    def walk_exit(j_e, entry, side, arm, entry_at_close):
        """Exit walk from entry bar j_e. entry_at_close: market-at-fire (walk starts next bar,
        no entry-bar ambiguity); else touch entry (entry-bar SL flagged, TP ignored, signal at
        close evaluable)."""
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

    # ---- cluster raw fires into armed setups ----------------------------------------------------
    setups = []                                                     # per side, ordered
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

    rows = []
    for su in setups:
        s, b = su["side"], su["b"]; long = s == "long"
        j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
        j_t = None
        for j in range(b, j1):                                      # fire bar itself counts
            if (lo_[j] <= base[j]) if long else (hi[j] >= base[j]):
                j_t = j
                break
        common = dict(sid=su["sid"], fire_bid=int(bid_arr[b]), side=s, absorbed=su["absorbed"],
                      ts=float(et[b]))
        if j_t is None:
            w = slice(b + 1, j1)
            cf_mfe = cf_mae = ""
            if j1 > b + 1:
                c0 = cl[b]
                cf_mfe = round(max(0.0, ((np.max(hi[w]) - c0) / c0 if long
                                         else (c0 - np.min(lo_[w])) / c0) * 100.0), 4)
                cf_mae = round(min(0.0, ((np.min(lo_[w]) - c0) / c0 if long
                                         else (c0 - np.max(hi[w])) / c0) * 100.0), 4)
            rows.append(dict(common, status="UNTRIGGERED", touch_bid="", delay="", base_t="",
                             entry="", arm="", outcome="", pnl="", mins="",
                             cf_mfe=cf_mfe, cf_mae=cf_mae))
            continue
        geo_open = rop[j_t]                                          # cents open vs cents baseline
        if geo_open == rbase[j_t]:
            rows.append(dict(common, status="TIE", touch_bid=int(bid_arr[j_t]), delay="",
                             base_t=round(float(base[j_t]), 4), entry="", arm="", outcome="",
                             pnl="", mins="", cf_mfe="", cf_mae=""))
            continue
        if (geo_open < rbase[j_t]) if long else (geo_open > rbase[j_t]):
            rows.append(dict(common, status="DEGENERATE", touch_bid=int(bid_arr[j_t]), delay="",
                             base_t=round(float(base[j_t]), 4), entry="", arm="", outcome="",
                             pnl="", mins="", cf_mfe="", cf_mae=""))
            continue
        entry = float(base[j_t]); delay = (st[j_t] - et[b]) / 60.0 if j_t > b else 0.0
        for arm in ARMS:
            out, pnl, mins = walk_exit(j_t, entry, s, arm, entry_at_close=False)
            rows.append(dict(common, status="TOUCHED", touch_bid=int(bid_arr[j_t]),
                             delay=round(delay, 2), base_t=round(entry, 4), entry=round(entry, 4),
                             arm=arm, outcome=out, pnl=round(pnl, 4) if pnl is not None else "",
                             mins=round(mins, 2) if mins is not None else "", cf_mfe="", cf_mae=""))
        # market-at-fire comparison arm on the same setup
        for arm in ARMS:
            out, pnl, mins = walk_exit(b, float(cl[b]), s, arm, entry_at_close=True)
            rows.append(dict(common, status="MKT_AT_FIRE", touch_bid="", delay="",
                             base_t="", entry=round(float(cl[b]), 4), arm=arm, outcome=out,
                             pnl=round(pnl, 4) if pnl is not None else "",
                             mins=round(mins, 2) if mins is not None else "", cf_mfe="", cf_mae=""))

    # market-at-fire also for setups that never touched / tied / degenerate (same fire set!)
    for su in setups:
        s, b = su["side"], su["b"]
        if any(r["sid"] == su["sid"] and r["status"] == "MKT_AT_FIRE" for r in rows):
            continue
        for arm in ARMS:
            out, pnl, mins = walk_exit(b, float(cl[b]), s, arm, entry_at_close=True)
            rows.append(dict(sid=su["sid"], fire_bid=int(bid_arr[b]), side=s,
                             absorbed=su["absorbed"], ts=float(et[b]), status="MKT_AT_FIRE",
                             touch_bid="", delay="", base_t="", entry=round(float(cl[b]), 4),
                             arm=arm, outcome=out, pnl=round(pnl, 4) if pnl is not None else "",
                             mins=round(mins, 2) if mins is not None else "", cf_mfe="", cf_mae=""))

    with open(os.path.join(OUT, "s5g_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sid", "fire_bid", "side", "absorbed", "ts", "status",
                                          "touch_bid", "delay", "base_t", "entry", "arm",
                                          "outcome", "pnl", "mins", "cf_mfe", "cf_mae"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["sid"], r["status"], r["arm"])):
            w.writerow(r)

    # ---- aggregates ------------------------------------------------------------------------------
    def agg(sel):
        ts_ = [r for r in rows if sel(r) and r["pnl"] != ""]
        pnl = np.array([r["pnl"] for r in ts_], float)
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return dict(n=len(ts_), nw=len(w_), nl=len(l_),
                    aw=float(w_.mean()) if len(w_) else 0.0, al=float(l_.mean()) if len(l_) else 0.0,
                    eg=float(pnl.mean()) if len(ts_) else float("nan"),
                    mh=float(np.median([r["mins"] for r in ts_])) if ts_ else float("nan"))

    n_setup = {s: sum(1 for su in setups if su["side"] == s) for s in ("long", "short")}
    n_touch = {s: len({r["sid"] for r in rows if r["side"] == s and r["status"] == "TOUCHED"})
               for s in ("long", "short")}
    n_untrig = {s: sum(1 for r in rows if r["side"] == s and r["status"] == "UNTRIGGERED")
                for s in ("long", "short")}
    n_geo = {s: sum(1 for r in rows if r["side"] == s and r["status"] in ("TIE", "DEGENERATE"))
             for s in ("long", "short")}

    # S5f committed per-cell nets for the head-to-head
    f5 = {}
    for r in csv.DictReader(open(os.path.join(OUT, "s5f_episodes.csv"), encoding="utf-8")):
        if r["status"] == "FILLED" and r["pnl_pct"] != "":
            f5.setdefault((r["side"], r["exit_arm"]), []).append(float(r["pnl_pct"]))

    md = ["# S5g — Baseline-Touch Entry (locked, corrected pullback; moving line)", "",
          "_**Pre-registered correction of S5f's entry mistranslation — a touch TRIGGER on the"
          " MOVING baseline, taker fill at the line, not a resting maker limit frozen at fire."
          " +4 cells -> counter 510.** Fire set identical to S5f (anchor 21340 asserted rejected);"
          " NEW cluster rule: fires inside an armed 30-min window collapse to one setup. Primary"
          " net line = taker/taker %.2f%% RT (touch = market entry). Fire-bar self-touch is allowed"
          " per the operator rule and is OPTIMISTIC (the touch precedes the close-time fire) —"
          " flagged. Setups simulated independently._" % FEE, "",
          "## 1. Funnel", "",
          "| side | raw fires | armed setups | touched | untriggered | tie/degenerate |",
          "|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        md.append("| %s | %d | %d | %d | %d | %d |" % (
            s, raw_ct[s], n_setup[s], n_touch[s], n_untrig[s], n_geo[s]))
    dl = [r["delay"] for r in rows if r["status"] == "TOUCHED" and r["arm"] == "FIXED"]
    md += ["", "## 2. Touch delay",
           "n=%d touched setups: median %s min, p90 %s min, fire-bar self-touches: %d."
           % (len(dl), "%.1f" % float(np.median(dl)) if dl else "-",
              "%.1f" % float(np.percentile(dl, 90)) if dl else "-",
              sum(1 for d in dl if d == 0.0)), "",
           "## 3. Economics per side x arm (touch entries; gross and net %.2f)" % FEE, "",
           "| cell | n | W/L | avgW | avgL | exp gross | exp net | med hold |",
           "|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        for arm in ARMS:
            x = agg(lambda r, s=s, a=arm: r["side"] == s and r["arm"] == a and r["status"] == "TOUCHED")
            under = " _(under)_" if x["n"] < UNDER_N else ""
            if x["n"]:
                md.append("| %s-%s%s | %d | %d/%d | %+.3f | %+.3f | %+.3f%% | %+.3f%% | %.1f |" % (
                    s, arm, under, x["n"], x["nw"], x["nl"], x["aw"], x["al"], x["eg"],
                    x["eg"] - FEE, x["mh"]))
            else:
                md.append("| %s-%s | 0 | - | - | - | - | - | - |" % (s, arm))
    md += ["", "## 4. Untriggered counterfactual (30-min from fire close)", "",
           "| sid | side | fire bucket | cf MFE % | cf MAE % |", "|---|---|---|---|---|"]
    for r in rows:
        if r["status"] == "UNTRIGGERED":
            md.append("| %d | %s | %d | %s | %s |" % (r["sid"], r["side"], r["fire_bid"],
                                                      r["cf_mfe"], r["cf_mae"]))
    md += ["", "## 5. Head-to-head — one fire set, three entry styles (net %.2f taker/taker)" % FEE,
           "_S5f limit ran per RAW fire (uncollapsed) on the same corrected fire set — caveat._",
           "", "| side | arm | S5f resting limit | S5g touch | market-at-fire |",
           "|---|---|---|---|---|"]
    for s in ("long", "short"):
        for arm in ARMS:
            xg = agg(lambda r, s=s, a=arm: r["side"] == s and r["arm"] == a and r["status"] == "TOUCHED")
            xm = agg(lambda r, s=s, a=arm: r["side"] == s and r["arm"] == a and r["status"] == "MKT_AT_FIRE")
            fl = f5.get((s, arm), [])
            md.append("| %s | %s | %s | %s | %s |" % (
                s, arm, "%+.3f%%" % (float(np.mean(fl)) - FEE) if fl else "-",
                "%+.3f%%" % (xg["eg"] - FEE) if xg["n"] else "-",
                "%+.3f%%" % (xm["eg"] - FEE) if xm["n"] else "-"))
    md += ["", "## Honest flags",
           "- All cells rest on single-digit setups -> counts only (n < %d) throughout; no verdict"
           " language anywhere in this study." % UNDER_N,
           "- Fire-bar self-touch entries are optimistic (touch precedes the fire's close-time"
           " confirmation); market-at-fire has no such issue (entry at the close).",
           "- No slippage on the touch fill; the taker fee line is the honesty floor.",
           "- Spent tape; the corrected-pullback fire set is ~1 armed setup/day — forward"
           " accumulation is slow by construction.",
           "", "## HARD STOP", "One trigger rule, two pre-registered arms, one comparison table."]
    with open(os.path.join(OUT, "analysis_report_S5g.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] %d setups (%d rows) | report + CSV written"
          % (time.time() - t0, len(setups), len(rows)), flush=True)


if __name__ == "__main__":
    main()
