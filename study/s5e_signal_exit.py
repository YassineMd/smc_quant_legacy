"""S5e — SIGNAL-DEATH EXIT (pre-registered exit variant on the EXISTING S5d fire set; NO
re-detection, keyed by bucket_id; multiplicity +4 cells -> program counter 502).

ENTRY: the committed S5d fires (26/51 long, 12/24 short), entry = fire-bar close.

EXIT — evaluated per subsequent BAR CLOSE j on its rolling 16-bar selection [j-15, j], matching the
fire's variant semantics; BOTH legs required (AND):
 V-LOCKED  long : newest CONFIRMED P0 marker (locked X's only — m10_sweep_s5b.sel_markers verbatim)
                  is BEARISH  AND  locked eff-agg bull share e_sh[j-LOCK] < 50%.
 V-LOCKED  short: newest locked marker BULLISH AND bear share (1 - e_sh[j-LOCK]) < 50%.
 V-UNLOCKED     : same logic on unlocked values — newest marker among locked X's PLUS the settling
                  dots (the terminal's provisional region, _last_cross over the last LOCK bars with
                  confirm_end = selection end; the FORMING bucket does not exist offline) against
                  the position AND the unlocked own-side share e_sh[j] < 50%.
 Exit price = that bar's close. SAFETY NET: hard SL -0.3% from entry stays active throughout, S1
 intrabar conventions (inclusive touch; an SL touch in bar j precedes that bar's close-signal ->
 SL wins, the S1 ambiguity spirit). CAP: 6h -> exit at the last in-horizon bar's close, CAPPED.
 Hold-time conventions: SL = touch-bar START minus entry (S1); SIGNAL/CAP = exit-bar END minus
 entry (close-based). mfe_before_exit includes the exit bar's extreme; giveback = mfe - pnl.
 Fires are simulated independently and may overlap in time.

Underpowered rule: n < 20 -> counts only, no verdict language. HARD STOP after the report.
"""
import os, sys, csv, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict                       # noqa: E402
from m10_sweep_s5b import load_merged, p9_full, sel_markers, _last_cross, LOCK  # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
H_S = 6 * 3600.0; SLP = 0.3; FEE = 0.10
UNDER_N = 20
CELLS = (("locked", "long"), ("unlocked", "long"), ("locked", "short"), ("unlocked", "short"))


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    b0 = bids[0]
    snaps = [b.full_snapshot() for b in bks]
    _, e_sh, _, _, _, _, _, sum0 = p9_full(snaps)

    def markers_unlocked(b):
        """Locked X's + settling dots for selection [b-15, b] — the terminal's two regions;
        newest first. (Offline there is no forming bucket to exclude.)"""
        out = list(sel_markers(sum0, b))                            # locked region, verbatim
        vals = sum0[b - 15:b + 1]; ex = list(range(16)); end = 16 - LOCK
        for L in (50.0, 0.0, -50.0):
            m = _last_cross(vals, ex, max(1, end), 16, L, 1, -1, 16)   # settling region, verbatim
            if m is not None:
                out.append(m)
        out.sort(key=lambda m: -m[0])
        return out

    memo = {}

    def exit_signal(j, variant, side):
        if (j, variant) not in memo:
            mk = sel_markers(sum0, j) if variant == "locked" else markers_unlocked(j)
            sh = e_sh[max(0, j - LOCK)] if variant == "locked" else e_sh[j]
            memo[(j, variant)] = (mk, sh)
        mk, sh = memo[(j, variant)]
        if side == "long":
            cross = bool(mk) and mk[0][2] < 0
            share = sh < 0.5
        else:
            cross = bool(mk) and mk[0][2] > 0
            share = (1.0 - sh) < 0.5
        return cross, share

    fires = {c: [] for c in CELLS}
    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5d_episodes_%s.csv" % v), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                i = int(r["bucket_id"]) - b0
                assert abs(cl[i] - float(r["baseline"])) < 1e-9
                fires[(v, r["side"])].append(dict(i=i, entry=float(r["baseline"]), ts=float(r["ts"]),
                                                  bid=int(r["bucket_id"])))
    assert [len(fires[c]) for c in CELLS] == [26, 51, 12, 24]

    trades = {c: [] for c in CELLS}
    ctx = {c: dict(evals=0, cross=0, share=0, both=0) for c in CELLS}
    for (v, s), fs in fires.items():
        for f in fs:
            i, entry, t_ent = f["i"], f["entry"], et[f["i"]]
            long = s == "long"
            sl_lvl = entry * (1 - SLP / 100.0) if long else entry * (1 + SLP / 100.0)
            run = entry                                             # favorable extreme so far
            reason, px, mins = None, None, None
            j = i + 1; j_last = None
            while j < n and st[j] <= t_ent + H_S:
                j_last = j
                run = max(run, hi[j]) if long else min(run, lo_[j])
                if (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl):
                    reason, px, mins = "SL", sl_lvl, (st[j] - t_ent) / 60.0
                    break
                cross, share = exit_signal(j, v, s)
                cc = ctx[(v, s)]
                cc["evals"] += 1; cc["cross"] += cross; cc["share"] += share; cc["both"] += cross and share
                if cross and share:
                    reason, px, mins = "SIGNAL", float(cl[j]), (et[j] - t_ent) / 60.0
                    break
                j += 1
            if reason is None:                                      # 6h cap (all horizons fit the tape)
                assert j_last is not None
                reason, px, mins = "CAP", float(cl[j_last]), (et[j_last] - t_ent) / 60.0
            pnl = ((px - entry) / entry if long else (entry - px) / entry) * 100.0
            mfe = max(0.0, ((run - entry) / entry if long else (entry - run) / entry) * 100.0)
            trades[(v, s)].append(dict(ts=f["ts"], bid=f["bid"], entry=entry, reason=reason,
                                       px=px, pnl=pnl, mins=mins, mfe=mfe, gb=mfe - pnl))
    print("[%3.0fs] %d trades simulated" % (time.time() - t0, sum(len(x) for x in trades.values())), flush=True)

    with open(os.path.join(OUT, "s5e_trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "variant", "side", "entry", "exit_reason", "exit_price",
                    "pnl_pct", "minutes_held", "mfe_before_exit", "giveback"])
        for (v, s) in CELLS:
            for t in trades[(v, s)]:
                w.writerow([round(t["ts"], 3), t["bid"], v, s, t["entry"], t["reason"],
                            round(t["px"], 4), round(t["pnl"], 4), round(t["mins"], 2),
                            round(t["mfe"], 4), round(t["gb"], 4)])

    # S5d grid reference cells for the head-to-head
    gref = {}
    with open(os.path.join(OUT, "s5d_grid.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if float(r["sl_pct"]) == 0.30 and r["exit"] in ("TP0.5", "TRAIL"):
                gref[(r["variant"], r["side"], r["exit"])] = float(r["exp_net_pct"])

    # runners: S5d-BARRIERS+ TP winners with 1h max >= 1.0%
    runners = []
    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5d_episodes_%s.csv" % v), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["outcome"] == "TP" and r["mfe_1h_pct"] not in ("", None) \
                        and float(r["mfe_1h_pct"]) >= 1.0:
                    runners.append((v, r["side"], int(r["bucket_id"]), float(r["mfe_1h_pct"])))
    tr_by_key = {(v, s, t["bid"]): t for (v, s) in CELLS for t in trades[(v, s)]}

    def stats(ts_):
        pnl = np.array([t["pnl"] for t in ts_])
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return dict(n=len(ts_), nsig=sum(1 for t in ts_ if t["reason"] == "SIGNAL"),
                    nsl=sum(1 for t in ts_ if t["reason"] == "SL"),
                    ncap=sum(1 for t in ts_ if t["reason"] == "CAP"),
                    wr=100.0 * len(w_) / len(pnl), aw=float(w_.mean()) if len(w_) else 0.0,
                    al=float(l_.mean()) if len(l_) else 0.0, eg=float(pnl.mean()),
                    en=float(pnl.mean()) - FEE,
                    mh=float(np.median([t["mins"] for t in ts_])),
                    mg=float(np.median([t["gb"] for t in ts_])))

    md = ["# S5e — Signal-Death Exit on the S5d fires", "",
          "_**Pre-registered exit variant; +4 cells -> program counter 502. No re-detection** —"
          " entries are the committed S5d fire sets. Exit: newest P0 cross marker AGAINST the"
          " position AND own-side eff-agg share < 50%%, both on the fire's variant semantics"
          " (locked: X markers + locked share; unlocked: X + settling-dot markers + live-edge"
          " share), evaluated per bar close; hard SL -0.3%% intrabar throughout (S1 conventions,"
          " SL precedes a same-bar close signal); 6h cap flagged. Hold times: SL = touch-bar start,"
          " SIGNAL/CAP = exit-bar end. Fires overlap in time (independent sims). _Cells with"
          " n < %d: counts only._" % UNDER_N, "",
          "## 1. Per cell", "",
          "| cell | n | SIGNAL/SL/CAP | win% | avgW | avgL | exp gross | exp net | med hold (min) | med giveback |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for c in CELLS:
        x = stats(trades[c])
        under = " _(under)_" if x["n"] < UNDER_N else ""
        md.append("| %s-%s%s | %d | %d/%d/%d | %.1f | %+.3f | %+.3f | %+.3f%% | %+.3f%% | %.1f | %.3f |" % (
            c[0].upper(), c[1], under, x["n"], x["nsig"], x["nsl"], x["ncap"], x["wr"],
            x["aw"], x["al"], x["eg"], x["en"], x["mh"], x["mg"]))
    md += ["", "## 2. Head-to-head — expectancy net (same fires, taker 0.10% RT)", "",
           "| cell | SIGNAL-DEATH | fixed TP0.5/SL0.3 (S5d grid) | TRAIL SL0.3 (S5d grid) |",
           "|---|---|---|---|"]
    for c in CELLS:
        x = stats(trades[c])
        md.append("| %s-%s | %+.3f%% | %+.3f%% | %+.3f%% |" % (
            c[0].upper(), c[1], x["en"], gref[(c[0], c[1], "TP0.5")], gref[(c[0], c[1], "TRAIL")]))
    md += ["", "## 3. Runner capture — the direct test",
           "S5d-BARRIERS+ TP winners whose 1h max reached >= +1.0%: what the signal exit harvested:",
           "", "| variant | side | bucket | 1h max % | signal-exit pnl % | reason | held (min) |",
           "|---|---|---|---|---|---|---|"]
    caps = []
    for v, s, bid, m1 in sorted(runners):
        t = tr_by_key[(v, s, bid)]
        caps.append(t["pnl"])
        md.append("| %s | %s | %d | %.3f | %+.3f | %s | %.1f |" % (v, s, bid, m1, t["pnl"],
                                                                   t["reason"], t["mins"]))
    if caps:
        md += ["", "Median harvest on the %d runners: %+.3f%% (vs their median 1h max %.3f%%)."
               % (len(caps), float(np.median(caps)), float(np.median([m for *_x, m in runners])))]
    md += ["", "## 4. Exit-leg context (bar closes evaluated during holds, pre-exit)", "",
           "| cell | evals | cross-leg true | share-leg true | both (exit) |", "|---|---|---|---|---|"]
    for c in CELLS:
        cc = ctx[c]
        md.append("| %s-%s | %d | %.1f%% | %.1f%% | %.1f%% |" % (
            c[0].upper(), c[1], cc["evals"], 100.0 * cc["cross"] / max(1, cc["evals"]),
            100.0 * cc["share"] / max(1, cc["evals"]), 100.0 * cc["both"] / max(1, cc["evals"])))
    md += ["", "## Honest flags",
           "- Exit variant on spent tape; the S5d-GRID conclusion stands as reference — this adds"
           " ONE pre-registered exit family, not a search.",
           "- SL hold-time uses the S1 touch convention while SIGNAL/CAP use bar-end: median holds"
           " mix the two.", "", "## HARD STOP", "Judged once; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5e.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] report + trades CSV written" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
