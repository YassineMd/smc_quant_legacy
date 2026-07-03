"""S5d-GRID — exit-geometry matrix on the EXISTING S5d fire sets (NO re-detection; input = the
committed s5d_episodes_locked/unlocked.csv, keyed by bucket_id). CHARACTERIZATION on spent tape:
the grid is a MAP, not a decision — any preferred cell is a pre-registered FORWARD hypothesis.
Multiplicity: +32 cells noted (16 geometries x 2 sides; the variants view the same fire
information) -> program counter 466 + 32 = 498.

GRID: SL in {0.20, 0.25, 0.30, 0.40}% x EXIT in {fixed TP 0.5 / 0.75 / 1.0, TRAIL}. Entry =
fire-bar close; S1 path-walker conventions verbatim (bar-START horizon inclusion, inclusive touch,
6h cap, one bar spanning both levels -> SL + ambiguous flag, UNRESOLVED / end-of-data counted and
excluded from rates). Fires are simulated INDEPENDENTLY and may overlap in time.

TRAIL (long; short mirrored): arm when the bar high reaches entry+0.5%; then exit when price
retraces 0.3% off the post-arm maximum; SL governs until armed (armed exits are always >= +0.2%,
above every SL in the grid, so the trail supersedes the SL). SEQUENCING (conservative, no
look-ahead, documented): post-arm retrace is evaluated against the extreme up to the PREVIOUS bar —
the current bar's new extreme takes effect from the next bar; the ARMING bar is the exception (no
prior extreme): if its own low already retraces 0.3% off the arm level, it exits at
arm x (1 - 0.3%) and is FLAGGED ambiguous, mirroring the S1 both-in-one-bar rule. An armed trade
still open at the 6h cap is UNRESOLVED (counted).

Underpowered rule: cells resting on < 20 resolved trades are counts only (italic in the matrices).
HARD STOP — no further grids, no threshold variants beyond this spec.
"""
import os, sys, csv, calendar, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
from m10_sweep_s5b import load_merged                  # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
H_S = 6 * 3600.0
FEE = 0.10
SLS = (0.20, 0.25, 0.30, 0.40)
EXITS = ("TP0.5", "TP0.75", "TP1.0", "TRAIL")
TPS = {"TP0.5": 0.5, "TP0.75": 0.75, "TP1.0": 1.0}
ARM, TRL = 0.005, 0.003
REGIME_CUT = calendar.timegm((2026, 6, 30, 0, 0, 0))
UNDER_RES = 20
CELLS = (("locked", "long"), ("unlocked", "long"), ("locked", "short"), ("unlocked", "short"))
REF = (0.30, "TP0.5")


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    b0 = bids[0]

    fires = {c: [] for c in CELLS}
    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5d_episodes_%s.csv" % v), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                i = int(r["bucket_id"]) - b0
                assert abs(cl[i] - float(r["baseline"])) < 1e-9
                fires[(v, r["side"])].append(dict(i=i, entry=float(r["baseline"]), ts=float(r["ts"])))
    assert [len(fires[c]) for c in CELLS] == [26, 51, 12, 24]

    def sim(i, entry, side, slp, ex):
        """-> (pnl_pct | None, minutes | None, status, ambig). Status: TP/SL/TRAIL/UNRESOLVED/EOD."""
        long = side == "long"
        sl_lvl = entry * (1 - slp / 100.0) if long else entry * (1 + slp / 100.0)
        sl_hit = (lambda j: lo_[j] <= sl_lvl) if long else (lambda j: hi[j] >= sl_lvl)
        t_ent = et[i]
        if ex != "TRAIL":
            tpp = TPS[ex]
            tp_lvl = entry * (1 + tpp / 100.0) if long else entry * (1 - tpp / 100.0)
            tp_hit = (lambda j: hi[j] >= tp_lvl) if long else (lambda j: lo_[j] <= tp_lvl)
            j = i + 1
            while j < n and st[j] <= t_ent + H_S:
                a, b = tp_hit(j), sl_hit(j)
                if a or b:
                    m = (st[j] - t_ent) / 60.0
                    return (-slp, m, "SL", a and b) if b else (tpp, m, "TP", False)
                j += 1
            return None, None, ("EOD" if t_ent + H_S > et[-1] else "UNRESOLVED"), False
        # TRAIL
        arm_lvl = entry * (1 + ARM) if long else entry * (1 - ARM)
        arm_hit = (lambda j: hi[j] >= arm_lvl) if long else (lambda j: lo_[j] <= arm_lvl)

        def pnl_of(px):
            return ((px - entry) / entry if long else (entry - px) / entry) * 100.0

        armed = False; ext = None
        j = i + 1
        while j < n and st[j] <= t_ent + H_S:
            m = (st[j] - t_ent) / 60.0
            if not armed:
                a, b = arm_hit(j), sl_hit(j)
                if b:                                      # spanning arm+SL -> SL, flagged (S1 rule)
                    return -slp, m, "SL", bool(a)
                if a:
                    armed = True
                    trl0 = arm_lvl * (1 - TRL) if long else arm_lvl * (1 + TRL)
                    if (lo_[j] <= trl0) if long else (hi[j] >= trl0):
                        return pnl_of(trl0), m, "TRAIL", True   # same-bar arm+retrace, conservative
                    ext = hi[j] if long else lo_[j]
            else:
                trl = ext * (1 - TRL) if long else ext * (1 + TRL)    # vs the PREVIOUS bar's extreme
                if (lo_[j] <= trl) if long else (hi[j] >= trl):
                    return pnl_of(trl), m, "TRAIL", False
                ext = max(ext, hi[j]) if long else min(ext, lo_[j])
            j += 1
        return None, None, ("EOD" if t_ent + H_S > et[-1] else "UNRESOLVED"), False

    # ---- full grid --------------------------------------------------------------------------
    res = {}                                              # (v,s,sl,ex) -> list of per-fire dicts
    for (v, s), fs in fires.items():
        for slp in SLS:
            for ex in EXITS:
                rows = []
                for f in fs:
                    pnl, m, stt, amb = sim(f["i"], f["entry"], s, slp, ex)
                    rows.append(dict(ts=f["ts"], pnl=pnl, m=m, st=stt, amb=amb))
                res[(v, s, slp, ex)] = rows

    def agg(rows):
        pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
        wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
        mins = [r["m"] for r in rows if r["m"] is not None]
        return dict(n=len(rows), n_res=len(pnls),
                    n_unres=sum(1 for r in rows if r["st"] == "UNRESOLVED"),
                    n_eod=sum(1 for r in rows if r["st"] == "EOD"),
                    n_amb=sum(1 for r in rows if r["amb"]),
                    wr=100.0 * len(wins) / len(pnls) if pnls else float("nan"),
                    aw=float(np.mean(wins)) if wins else 0.0,
                    al=float(np.mean(losses)) if losses else 0.0,
                    eg=float(np.mean(pnls)) if pnls else float("nan"),
                    en=float(np.mean(pnls)) - FEE if pnls else float("nan"),
                    med=float(np.median(mins)) if mins else float("nan"))

    grid = {k: agg(rows) for k, rows in res.items()}
    with open(os.path.join(OUT, "s5d_grid.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["variant", "side", "sl_pct", "exit", "n", "n_resolved", "win_rate",
                    "n_unresolved", "n_eod", "n_ambiguous", "avg_win_pct", "avg_loss_pct",
                    "exp_gross_pct", "exp_net_pct", "med_minutes"])
        for (v, s, slp, ex), a in grid.items():
            w.writerow([v, s, slp, ex, a["n"], a["n_res"],
                        round(a["wr"], 2) if a["n_res"] else "", a["n_unres"], a["n_eod"],
                        a["n_amb"], round(a["aw"], 4), round(a["al"], 4),
                        round(a["eg"], 4) if a["n_res"] else "",
                        round(a["en"], 4) if a["n_res"] else "",
                        round(a["med"], 1) if a["med"] == a["med"] else ""])

    # ---- winner-clip: current-geometry (SL0.30/TP0.5) winners stopped by a tighter SL ----------
    def clipped(f, s, slp):
        """Sequenced walk: does SL' hit strictly BEFORE +0.5 (same-bar both -> clipped, S1 rule)?"""
        pnl, _m, stt, _a = sim(f["i"], f["entry"], s, slp, "TP0.5")
        return stt == "SL"
    clip = {}
    for (v, s) in CELLS:
        ref_rows = res[(v, s, REF[0], REF[1])]
        winners = [f for f, r in zip(fires[(v, s)], ref_rows) if r["st"] == "TP"]
        clip[(v, s)] = {slp: sum(1 for f in winners if clipped(f, s, slp))
                        for slp in SLS if slp != REF[0]}
        clip[(v, s)]["n_win"] = len(winners)

    # ---- report ---------------------------------------------------------------------------------
    def cellv(v, s, slp, ex, sub=None):
        rows = res[(v, s, slp, ex)]
        if sub is not None:
            rows = [r for r in rows if sub(r)]
        pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
        return (float(np.mean(pnls)) - FEE if pnls else float("nan")), len(pnls)

    md = ["# S5d-GRID — exit-geometry matrix on the S5d fires", "",
          "_**Characterization on spent tape: this grid is a MAP, not a decision — any preferred"
          " cell is a pre-registered FORWARD hypothesis. Multiplicity +32 noted -> program counter"
          " 498.** Inputs = the committed S5d fire sets (26/51 long, 12/24 short; no re-detection)."
          " S1 walker conventions; fires overlap in time (independent sims). TRAIL = arm +0.5%%,"
          " exit on a 0.3%% retrace off the post-arm extreme; retrace is judged against the extreme"
          " up to the PREVIOUS bar (no look-ahead); the arming bar's own-bar retrace exits at"
          " arm-0.3%% and is flagged ambiguous; armed-at-cap = UNRESOLVED. Values below = NET"
          " expectancy %%/trade at taker %.2f%% RT; matrices in fire counts n, resolved varies by"
          " cell. _Italic_ = cell rests on < %d resolved trades (counts only)._" % (FEE, UNDER_RES), ""]
    for v, s in CELLS:
        nn = len(fires[(v, s)])
        md += ["## %s %s — %d fires (net expectancy %%/trade; **bold** = best, † = current "
               "geometry SL 0.30 / TP 0.5)" % (v.upper(), s.upper(), nn), "",
               "| SL \\ exit | TP0.5 | TP0.75 | TP1.0 | TRAIL |", "|---|---|---|---|---|"]
        best = max(((slp, ex) for slp in SLS for ex in EXITS),
                   key=lambda k: (grid[(v, s, k[0], k[1])]["en"]
                                  if grid[(v, s, k[0], k[1])]["n_res"] else -1e9))
        for slp in SLS:
            cells_ = []
            for ex in EXITS:
                a = grid[(v, s, slp, ex)]
                txt = "%+.3f" % a["en"] if a["n_res"] else "-"
                if a["n_res"] < UNDER_RES:
                    txt = "_%s_" % txt
                if (slp, ex) == best:
                    txt = "**%s**" % txt
                if (slp, ex) == REF:
                    txt += " †"
                cells_.append(txt)
            md.append("| %.2f | %s |" % (slp, " | ".join(cells_)))
        a = grid[(v, s, best[0], best[1])]
        md += ["", "Best cell SL %.2f / %s: win %.1f%% (res %d/%d, unres %d, eod %d, ambig %d), "
               "avgW %+.3f / avgL %+.3f, gross %+.3f%% -> net %+.3f%%, med res %.1f min."
               % (best[0], best[1], a["wr"], a["n_res"], a["n"], a["n_unres"], a["n_eod"],
                  a["n_amb"], a["aw"], a["al"], a["eg"], a["en"], a["med"]), ""]
        # robustness of the best cell: other variant + regime halves
        ov = "unlocked" if v == "locked" else "locked"
        en_ov, nr_ov = cellv(ov, s, best[0], best[1])
        en_pre, nr_pre = cellv(v, s, best[0], best[1], sub=lambda r: r["ts"] < REGIME_CUT)
        en_post, nr_post = cellv(v, s, best[0], best[1], sub=lambda r: r["ts"] >= REGIME_CUT)
        md += ["Robustness: same cell in %s = %s (res %d); regime halves: pre %s (res %d) / post "
               "%s (res %d). %s" % (
                   ov.upper(), "%+.3f%%" % en_ov if nr_ov else "-", nr_ov,
                   "%+.3f%%" % en_pre if nr_pre else "-", nr_pre,
                   "%+.3f%%" % en_post if nr_post else "-", nr_post,
                   ("Positive across both variants and both halves." if
                    all(x == x and x > 0 for x in (en_ov, en_pre, en_post)) else
                    "NOT uniform — carried by a slice; treat as tape-specific until forward data.")), ""]
    md += ["## Winner-clip — the cost of tightening the SL",
           "Current-geometry (SL 0.30 / TP 0.5) winners stopped by a tighter SL BEFORE reaching "
           "+0.5, path-sequenced per fire (same-bar both -> clipped, S1 rule):", "",
           "| cell | winners | clipped @0.20 | @0.25 | @0.40 |", "|---|---|---|---|---|"]
    for v, s in CELLS:
        c = clip[(v, s)]
        md.append("| %s-%s | %d | %d | %d | %d |" % (v.upper(), s, c["n_win"],
                                                     c[0.20], c[0.25], c[0.40]))
    md += ["", "## Honest flags",
           "- 32 cells on 26-51 (long) / 12-24 (short) fires is a MINED grid: it locates the "
           "trade-off, it cannot confirm a cell. Only forward tape can.",
           "- The trail's conservative sequencing UNDERSTATES trail exits (same-bar new-extreme "
           "retraces credit the previous extreme).",
           "- Locked-short cells all rest on 12 fires -> counts only throughout.",
           "", "## HARD STOP", "No further grids, no threshold variants beyond this spec."]
    with open(os.path.join(OUT, "analysis_report_S5d_grid.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%2.0fs] grid CSV (%d cells) + report written" % (time.time() - t0, len(grid)), flush=True)


if __name__ == "__main__":
    main()
