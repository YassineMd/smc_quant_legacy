"""S5i — CORRECTED CONFLUENCE + ROUTER ENTRY + FIXED EXIT (LOCKED values only). Supersedes the
S5f/g/h fire sets (leg 5 re-specified as a PRIORITY SCAN). Pre-registered; multiplicity +2 cells
(long/short) -> program counter 514. Merged 1m span, universe idx >= 100.

THE FIVE LEGS (all on the fire bar, LOCKED values; long stated, short mirrored):
 1. P0 two most recent confirmed markers both green (any level incl. 0; forming dot excluded) —
    the sweep's leg1 columns (sel_markers verbatim).
 2. P2 eff-agg bull SHARE >= 65% — as written in the mandate (share form, == locked badge spread
    >= 30 points; the S5b-r alert rule uses spread >= 65 — this leg is intentionally the looser
    pre-registered form and is stated as such to kill any future translation dispute).
 3. Phase table UP dominant == START/DURING — sweep leg3.
 4. P6 DURING spread >= +15pp — sweep leg4.
 5. CONTEXT SCAN 50->100, FIRST MATCH WINS: for N = 50..100 in order, compare O(b-N+1) vs C(b)
    tick-strict; first RED (O>C) -> LONG-context, first GREEN (O<C) -> SHORT-context, flat -> next
    N; nothing decisive by 100 -> no fire. One side per bar by construction. NEW code; self-test
    prints the deciding N on 5 sample fires.

EPISODES: fire window = [fire close, +1h]. FIRE-SEARCH BLACKOUT until the window ends (both sides).
Excursion extraction ALWAYS (filled or not): raw win1h max/min % from fire close + minutes to each.

ROUTER (POC baseline, moving, S5g code path): LONG close <= baseline -> MARKET at fire close;
close > baseline -> WAIT the 1h window for the first SUBSEQUENT bar whose low touches the CURRENT
baseline, entry AT that bar's baseline; no touch -> CANCELLED, counted. SHORT mirrored. All taker;
net at 0.10% RT. (No fire-bar self-touch here — 'first subsequent bar' per spec; no geometry
tie/degenerate rule in this mandate.)

EXIT — FIXED ONLY: TP +0.5% / SL -0.3% from entry, S1 walker (both-in-one-bar -> SL flagged;
entry-bar SL for touch entries flagged, entry-bar TP ignored), 6h cap from entry -> UNRESOLVED,
counted. Trades may outlive their 1h window; only the fire search is blacked out.
Underpowered rule: n < 20/side -> counts only. HARD STOP after the report.
"""
import os, sys, csv, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict                       # noqa: E402
from m10_sweep_s5b import load_merged, p9_full, LOCK                # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
WIN = 3600.0; H_S = 6 * 3600.0
FEE = 0.10
FIRST = 100
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
    _, e_sh, _, _, _, _, _, _s0 = p9_full(snaps)
    df = pd.read_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"))
    idx = np.arange(16, n)

    # legs 1-4 (sweep verbatim for 1/3/4; leg 2 = share form recomputed exactly from e_sh)
    sh = e_sh[np.maximum(0, idx - LOCK)]
    l2 = {"long": sh * 100.0 >= 65.0, "short": (1 - sh) * 100.0 >= 65.0}
    legs14 = {s: (df["leg1_" + s].to_numpy() & l2[s] & df["leg3_" + s].to_numpy()
                  & df["leg4_" + s].to_numpy()) for s in ("long", "short")}

    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)

    def context(b):
        """Priority scan N=50..100, first decisive composite wins. -> (side|None, deciding_N|None)"""
        c = rcl[b]
        for N in range(50, 101):
            o = rop[b - N + 1]
            if o > c:
                return "long", N
            if o < c:
                return "short", N
        return None, None

    # fire bars (pre-blackout), per side
    fire_bars = {"long": [], "short": []}
    decN = {}
    for s in ("long", "short"):
        for r in np.flatnonzero(legs14[s]):
            b = int(idx[r])
            if b < FIRST:
                continue
            side, N = context(b)
            if side == s:
                fire_bars[s].append(b)
                decN[b] = N
    # self-test: deciding N on 5 sample fires
    samp = (fire_bars["long"] + fire_bars["short"])[:5]
    print("LEG-5 SELF-TEST (first 5 fires): scan 50->100, first decisive wins")
    for b in samp:
        N = decN[b]
        print("  bid %d C=%.2f -> N=%d: O(b-N+1)=O(bid %d)=%.2f (%s) => %s-context"
              % (bid_arr[b], cl[b], N, bid_arr[b - N + 1], op[b - N + 1],
                 "RED O>C" if rop[b - N + 1] > rcl[b] else "GREEN O<C",
                 "long" if rop[b - N + 1] > rcl[b] else "short"), flush=True)

    # comparison fire sets: S5d-locked (EXISTS) and S5f/g/h (ALL form), locked-spread legs
    zmax = pd.Series(rop).rolling(51).max().shift(49).to_numpy()
    zmin = pd.Series(rop).rolling(51).min().shift(49).to_numpy()
    okk = np.arange(n) >= FIRST
    exists_L = np.zeros(n, bool); exists_L[okk] = rcl[okk] < zmax[okk]
    exists_S = np.zeros(n, bool); exists_S[okk] = rcl[okk] > zmin[okk]
    all_L = np.zeros(n, bool); all_L[okk] = rcl[okk] < zmin[okk]
    all_S = np.zeros(n, bool); all_S[okk] = rcl[okk] > zmax[okk]
    cmp_sets = {}
    for s, exl, alll in (("long", exists_L, all_L), ("short", exists_S, all_S)):
        lockedf = df["fire_" + s].to_numpy()                        # locked-spread legs 1-4
        cmp_sets[("s5d", s)] = {int(idx[r]) for r in np.flatnonzero(lockedf & exl[idx])}
        cmp_sets[("s5h", s)] = {int(idx[r]) for r in np.flatnonzero(lockedf & alll[idx])}

    # POC baseline (terminal recursion, verbatim)
    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95
    rbase = np.round(base * 100).astype(np.int64)

    def walk_fixed(j_e, entry, side, entry_at_close):
        long = side == "long"
        sl_lvl = entry * (1 - 0.003) if long else entry * (1 + 0.003)
        tp_lvl = entry * (1 + 0.005) if long else entry * (1 - 0.005)
        t_e = float(et[j_e]) if entry_at_close else float(st[j_e])
        if not entry_at_close:
            if (lo_[j_e] <= sl_lvl) if long else (hi[j_e] >= sl_lvl):
                return "SL*", -0.3, 0.0
        j = j_e + 1
        while j < n and st[j] <= t_e + H_S:
            a = (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl)
            b_ = (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl)
            if a or b_:
                m = (st[j] - t_e) / 60.0
                return ("SL" + ("*" if a else ""), -0.3, m) if b_ else ("TP", 0.5, m)
            j += 1
        return "UNRESOLVED", None, None

    # episodes with 1h fire-search blackout (both sides share it)
    allf = sorted([(b, s) for s in ("long", "short") for b in fire_bars[s]])
    rows = []; n_blk = 0
    blackout_until = -1e18
    for b, s in allf:
        if et[b] < blackout_until:
            n_blk += 1
            continue
        blackout_until = et[b] + WIN
        long = s == "long"; c0 = float(cl[b]); B = float(base[b])
        j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
        row = dict(ts=round(float(et[b]), 3), fire_bid=int(bid_arr[b]), side=s, decN=decN[b],
                   baseline=round(B, 4), route="", status="", entry="", delay="",
                   outcome="", pnl="", mins="", w_max="", w_min="", t_max="", t_min="")
        if j1 > b + 1:                                              # 1h excursions, always
            w = slice(b + 1, j1)
            k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
            row["w_max"] = round((float(np.max(hi[w])) - c0) / c0 * 100.0, 4)
            row["w_min"] = round((float(np.min(lo_[w])) - c0) / c0 * 100.0, 4)
            row["t_max"] = round((float(et[b + 1 + k_up]) - et[b]) / 60.0, 2)
            row["t_min"] = round((float(et[b + 1 + k_dn]) - et[b]) / 60.0, 2)
        mkt = (rcl[b] <= rbase[b]) if long else (rcl[b] >= rbase[b])
        row["route"] = "MKT" if mkt else "WAIT"
        if mkt:
            row["status"] = "MKT"; row["entry"] = round(c0, 4); row["delay"] = 0.0
            out, pnl, mins = walk_fixed(b, c0, s, entry_at_close=True)
        else:
            j_t = None
            for j in range(b + 1, j1):                              # first SUBSEQUENT bar
                if (lo_[j] <= base[j]) if long else (hi[j] >= base[j]):
                    j_t = j
                    break
            if j_t is None:
                row["status"] = "CANCELLED"
                rows.append(row)
                continue
            entry = float(base[j_t])
            row["status"] = "TOUCH"; row["entry"] = round(entry, 4)
            row["delay"] = round((st[j_t] - et[b]) / 60.0, 2)
            out, pnl, mins = walk_fixed(j_t, entry, s, entry_at_close=False)
        row["outcome"] = out
        row["pnl"] = round(pnl, 4) if pnl is not None else ""
        row["mins"] = round(mins, 2) if mins is not None else ""
        rows.append(row)

    with open(os.path.join(OUT, "s5i_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "fire_bid", "side", "decN", "route", "baseline",
                                          "status", "entry", "delay", "outcome", "pnl", "mins",
                                          "w_max", "w_min", "t_max", "t_min"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---- report ----------------------------------------------------------------------------------
    def eps(s, statuses=("MKT", "TOUCH")):
        return [r for r in rows if r["side"] == s and r["status"] in statuses]

    md = ["# S5i — Corrected Confluence + Router Entry + Fixed Exit (LOCKED)", "",
          "_**Pre-registered; +2 cells -> counter 514. Supersedes the S5f/g/h fire sets: leg 5 is"
          " now a PRIORITY SCAN (N = 50..100, first decisive composite wins, one side per bar)."
          " Leg 2 is the SHARE form as mandated (bull share >= 65%% == locked badge spread >= 30 —"
          " looser than the S5b-r alert's spread >= 65; stated here so the translation is on the"
          " record). Legs 1/3/4 = the committed sweep columns (locked markers / phase / P6"
          " spread). Fire windows are 1h with a fire-search BLACKOUT (%d fires absorbed); trades"
          " may outlive their window. Router: at-or-through the baseline -> MARKET at fire close,"
          " else WAIT the hour for a touch of the MOVING baseline (no fire-bar self-touch, per"
          " spec). All taker; net %.2f%% RT. Exit fixed TP+0.5/SL-0.3 only (S1 conventions,"
          " * = ambiguous flag). Underpowered rule: n < %d/side -> counts only._"
          % (n_blk, FEE, UNDER_N), "",
          "## 1. Funnel", "",
          "| side | fire bars | vs S5d-locked (EXISTS) | vs S5f/g/h (ALL) | episodes | MKT | WAIT | "
          "touched | CANCELLED |", "|---|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        fb = set(fire_bars[s])
        d5 = cmp_sets[("s5d", s)]; h5 = cmp_sets[("s5h", s)]
        ep_all = [r for r in rows if r["side"] == s]
        nmkt = sum(1 for r in ep_all if r["status"] == "MKT")
        nwait = sum(1 for r in ep_all if r["status"] in ("TOUCH", "CANCELLED"))
        ntch = sum(1 for r in ep_all if r["status"] == "TOUCH")
        ncan = sum(1 for r in ep_all if r["status"] == "CANCELLED")
        md.append("| %s | %d | +%d regained / -%d lost (n=%d) | +%d / -%d (n=%d) | %d | %d | %d | %d | %d |" % (
            s, len(fb), len(fb - d5), len(d5 - fb), len(d5), len(fb - h5), len(h5 - fb), len(h5),
            len(ep_all), nmkt, nwait, ntch, ncan))
    md += ["", "## 2. Economics per side (fixed exit; filled entries only)", "",
           "| side | n | TP/SL/unres | TP rate (res) | avgW | avgL | exp gross | exp net | med hold | "
           "touch delay med/p90 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        e = eps(s)
        pnls = [r["pnl"] for r in e if r["pnl"] != ""]
        ntp = sum(1 for r in e if r["outcome"] == "TP")
        nsl = sum(1 for r in e if r["outcome"].startswith("SL"))
        nun = sum(1 for r in e if r["outcome"] == "UNRESOLVED")
        res = ntp + nsl
        dls = sorted(r["delay"] for r in e if r["status"] == "TOUCH")
        under = " _(under)_" if len(e) < UNDER_N else ""
        if pnls:
            arr = np.array(pnls, float)
            md.append("| %s%s | %d | %d/%d/%d | %s | +0.500 | -0.300 | %+.3f%% | %+.3f%% | %s | %s |" % (
                s, under, len(e), ntp, nsl, nun,
                "%.1f%%" % (100.0 * ntp / res) if res else "-",
                float(arr.mean()), float(arr.mean()) - FEE,
                "%.1f" % float(np.median([r["mins"] for r in e if r["mins"] != ""])) if res else "-",
                "%.1f/%.1f" % (float(np.median(dls)), float(np.percentile(dls, 90))) if dls else "-"))
        else:
            md.append("| %s | %d | 0/0/%d | - | - | - | - | - | - | - |" % (s, len(e), nun))
    md += ["", "## 3. 1h excursions from fire close (raw extremes), split by route", "",
           "| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |",
           "|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        for stt in ("MKT", "TOUCH", "CANCELLED"):
            sub = [r for r in rows if r["side"] == s and r["status"] == stt and r["w_max"] != ""]
            if not sub:
                md.append("| %s | 0 | %s | - | - | - | - |" % (s, stt))
                continue
            mx = np.array([r["w_max"] for r in sub], float)
            mn = np.array([r["w_min"] for r in sub], float)
            md.append("| %s | %d | %s | %+.3f | %+.3f/%+.3f | %+.3f | %+.3f/%+.3f |" % (
                s, len(sub), stt, float(np.median(mx)), float(np.percentile(mx, 25)),
                float(np.percentile(mx, 75)), float(np.median(mn)),
                float(np.percentile(mn, 25)), float(np.percentile(mn, 75))))
    md += ["", "## Honest flags",
           "- Leg 2 share-form is the mandated translation; it is looser than the terminal alert's"
           " spread-65 rule — the S5i fire set is NOT comparable 1:1 to S5b-r locked cells.",
           "- Touch entries assume a fill on a bar-low touch of the moving line (taker at the line;"
           " no slippage).",
           "- Spent tape; underpowered cells are counts only; the fire-search blackout makes"
           " episodes disjoint by construction but trades may overlap beyond the hour.",
           "", "## HARD STOP", "Judged once; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5i.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] %d episodes (%d blacked out) | report + CSV written"
          % (time.time() - t0, len(rows), n_blk), flush=True)


if __name__ == "__main__":
    main()
