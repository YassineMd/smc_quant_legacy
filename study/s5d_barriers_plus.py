"""S5d-BARRIERS+ — barrier outcomes + 1h post-TP / post-SL runs APPENDED to the committed S5d
episode CSVs. NO re-detection: input = the exact fire sets in s5d_episodes_locked/unlocked.csv,
keyed by bucket_id; original columns untouched, rows in place. No new cells (counter stays 466) —
these are outcome columns on the already-charged S5d cells.

Per fire, entry = fire-bar close:
1. BARRIER RACE — S1 path-walker conventions verbatim (inclusive touch, bar-START horizon test,
   both-in-one-bar -> SL + ambiguous flag, 6h cap, UNRESOLVED/EOD counted). Re-derived here and
   ASSERTED row-for-row equal to the committed s5d_barrier_trades.csv.
2. TP winners: 1-HOUR window from entry (bar-END convention, matching the episode MFE columns) —
   mfe_1h_pct (favorable, position direction), missed_tp_pct = mfe_1h_pct - 0.5 (negative for
   winners that hit TP after the hour — kept, factual), t_mfe_1h_min, run_1h_capped (the max
   printed in the window's last bar = right-censored).
3. SL losers: mae_1h_pct — max adverse within the hour ("how much worse it kept going"), same
   run_1h_capped censoring flag.

Fires are simulated INDEPENDENTLY and may overlap in time for this block. Appendix appended to
analysis_report_S5d.md (idempotent: a previous appendix is replaced). Underpowered rule stands
(n < 20 -> counts only, no verdict language). HARD STOP — no new thresholds, no re-detection.
"""
import os, sys, csv, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
from m10_sweep_s5b import load_merged                  # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
TP, SL = 0.005, 0.003
H_S = 6 * 3600.0; RUN_S = 3600.0
NULL = 100.0 * SL / (TP + SL); BREAKEVEN = 50.0
UNDERPOWERED_N = 20
CELLS = (("locked", "long"), ("locked", "short"), ("unlocked", "long"), ("unlocked", "short"))
NEWCOLS = ["outcome", "minutes_to_resolution", "ambiguous",
           "mfe_1h_pct", "missed_tp_pct", "t_mfe_1h_min", "mae_1h_pct", "run_1h_capped"]
APPENDIX_MARK = "## APPENDIX — barrier race + 1h post-TP / post-SL runs (S5d-BARRIERS+)"


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    b0 = bids[0]

    def race(i, entry, side):
        if side == "long":
            upl, dnl = entry * (1 + TP), entry * (1 - SL)
            tp_hit = lambda j: hi[j] >= upl; sl_hit = lambda j: lo_[j] <= dnl
        else:
            dnl, upl = entry * (1 - TP), entry * (1 + SL)
            tp_hit = lambda j: lo_[j] <= dnl; sl_hit = lambda j: hi[j] >= upl
        j = i + 1
        while j < n and st[j] <= et[i] + H_S:
            a, b = tp_hit(j), sl_hit(j)
            if a or b:
                return ("SL" if b else "TP"), (st[j] - et[i]) / 60.0, (a and b)
            j += 1
        return ("EOD" if et[i] + H_S > et[-1] else "UNRESOLVED"), None, False

    def run_1h(i, entry, side, favorable):
        """(run_pct, t_min, capped) over bars with end <= entry+1h; favorable=True -> position
        direction MFE, False -> adverse MAE. capped = the extreme prints in the window's last bar."""
        j1 = int(np.searchsorted(et, et[i] + RUN_S, side="right"))
        if j1 <= i + 1:
            return None, None, False
        w = slice(i + 1, j1)
        if (side == "long") == favorable:                    # long-favorable / short-adverse -> highs
            series = hi[w]; k = int(np.argmax(series)); v = (float(series[k]) - entry) / entry * 100.0
        else:
            series = lo_[w]; k = int(np.argmin(series)); v = (entry - float(series[k])) / entry * 100.0
        return v, (float(et[i + 1 + k]) - et[i]) / 60.0, (i + 1 + k) == (j1 - 1)

    # committed barrier outcomes for the parity assert
    committed = {}
    with open(os.path.join(OUT, "s5d_barrier_trades.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            committed[(r["variant"], int(r["bucket_id"]))] = (
                r["outcome"], r["minutes_to_resolution"], int(r["ambiguous_flag"]))

    cells = {c: [] for c in CELLS}
    for v in ("locked", "unlocked"):
        path = os.path.join(OUT, "s5d_episodes_%s.csv" % v)
        with open(path, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            hdr = list(rd.fieldnames)
            rows = list(rd)
        for r in rows:
            i = int(r["bucket_id"]) - b0; entry = float(r["baseline"]); s = r["side"]
            assert bids[i] == int(r["bucket_id"]) and abs(cl[i] - entry) < 1e-9
            out, mins, amb = race(i, entry, s)
            ck = committed[(v, int(r["bucket_id"]))]
            assert ck[0] == out and ck[2] == int(amb) and \
                (ck[1] == "" if mins is None else abs(float(ck[1]) - mins) < 0.01), (v, r["bucket_id"])
            r["outcome"] = out
            r["minutes_to_resolution"] = round(mins, 2) if mins is not None else ""
            r["ambiguous"] = int(amb)
            r["mfe_1h_pct"] = r["missed_tp_pct"] = r["t_mfe_1h_min"] = r["mae_1h_pct"] = ""
            r["run_1h_capped"] = ""
            if out == "TP":
                mfe, tm, cap = run_1h(i, entry, s, favorable=True)
                if mfe is not None:
                    r["mfe_1h_pct"] = round(mfe, 4); r["missed_tp_pct"] = round(mfe - 0.5, 4)
                    r["t_mfe_1h_min"] = round(tm, 2); r["run_1h_capped"] = int(cap)
            elif out == "SL":
                mae, _tm, cap = run_1h(i, entry, s, favorable=False)
                if mae is not None:
                    r["mae_1h_pct"] = round(mae, 4); r["run_1h_capped"] = int(cap)
            cells[(v, s)].append(r)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=hdr + NEWCOLS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("[%2.0fs] %s: %d rows updated (parity vs committed barrier CSV OK)"
              % (time.time() - t0, os.path.basename(path), len(rows)), flush=True)

    # ---- appendix -------------------------------------------------------------------------------
    md = ["", APPENDIX_MARK, "",
          "_Outcome columns appended to the committed S5d fire sets — no re-detection, no new cells"
          " (counter stays 466). Barrier race re-derived with the S1 walker and asserted equal to"
          " s5d_barrier_trades.csv row-for-row. Fires simulated INDEPENDENTLY — they may overlap in"
          " time. References: geometric null %.1f%%, taker breakeven %.1f%% (0.10%% RT). 1h runs are"
          " right-censored when the extreme prints in the window's last bar (capped flag; also in"
          " the CSVs). missed_tp can be negative for winners that hit TP after the hour._"
          % (NULL, BREAKEVEN), ""]
    for v, s in CELLS:
        rs = cells[(v, s)]
        ntp = sum(1 for r in rs if r["outcome"] == "TP"); nsl = sum(1 for r in rs if r["outcome"] == "SL")
        nun = sum(1 for r in rs if r["outcome"] == "UNRESOLVED")
        neod = sum(1 for r in rs if r["outcome"] == "EOD")
        res = ntp + nsl; p = 100.0 * ntp / res if res else float("nan")
        mins = [float(r["minutes_to_resolution"]) for r in rs if r["minutes_to_resolution"] != ""]
        in30 = sum(1 for m in mins if m <= 30.0)
        under = " — **UNDERPOWERED (n<%d): counts only, no verdict language**" % UNDERPOWERED_N \
            if len(rs) < UNDERPOWERED_N else ""
        md += ["### %s-%s — %d fires%s" % (v.upper(), s, len(rs), under), "",
               "TP %d / SL %d / unresolved %d / eod %d -> TP %s of resolved (null %.1f%%, breakeven "
               "%.1f%%). Expectancy/trade gross %s, net %s. Resolution med %s / p90 %s min; resolved "
               "inside 30 min: %d/%d."
               % (ntp, nsl, nun, neod, "%.1f%%" % p if res else "-", NULL, BREAKEVEN,
                  "%+.3f%%" % (p / 100 * 0.5 - (1 - p / 100) * 0.3) if res else "-",
                  "%+.3f%%" % (p / 100 * 0.5 - (1 - p / 100) * 0.3 - 0.10) if res else "-",
                  "%.1f" % float(np.median(mins)) if mins else "-",
                  "%.1f" % float(np.percentile(mins, 90)) if mins else "-", in30, len(mins)), ""]
        wins = [r for r in rs if r["outcome"] == "TP" and r["mfe_1h_pct"] != ""]
        if wins:
            md += ["WINNERS (post-TP run within 1h of entry):", "",
                   "| ts (UTC) | entry | min to TP | mfe_1h % | missed_tp % | capped |",
                   "|---|---|---|---|---|---|"]
            for r in wins:
                md.append("| %s | %.2f | %.1f | %.3f | %+.3f | %s |" % (
                    time.strftime("%m-%d %H:%M", time.gmtime(float(r["ts"]))), float(r["baseline"]),
                    float(r["minutes_to_resolution"]), float(r["mfe_1h_pct"]),
                    float(r["missed_tp_pct"]), "Y" if r["run_1h_capped"] == 1 else ""))
            mt = [float(r["missed_tp_pct"]) for r in wins]
            big = sum(1 for r in wins if float(r["mfe_1h_pct"]) >= 1.0)
            md += ["", "missed_tp median %+.3f%% / mean %+.3f%%; winners running >= +1.0%% within "
                   "the hour: %d/%d (%.0f%%)."
                   % (float(np.median(mt)), float(np.mean(mt)), big, len(wins),
                      100.0 * big / len(wins)), ""]
        loss = [float(r["mae_1h_pct"]) for r in rs if r["outcome"] == "SL" and r["mae_1h_pct"] != ""]
        if loss:
            md += ["LOSERS: median post-SL continuation (max adverse within 1h) %.3f%% (n=%d)."
                   % (float(np.median(loss)), len(loss)), ""]

    rp = os.path.join(OUT, "analysis_report_S5d.md")
    body = open(rp, encoding="utf-8").read()
    if APPENDIX_MARK in body:
        body = body[:body.index(APPENDIX_MARK)].rstrip() + "\n"
    with open(rp, "w", encoding="utf-8") as f:
        f.write(body + "\n".join(md) + "\n")
    print("[%2.0fs] appendix written into analysis_report_S5d.md" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
