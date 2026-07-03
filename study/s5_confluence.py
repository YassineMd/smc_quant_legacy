"""S5 — CONFLUENCE EXCURSION STUDY (Yassine's 4-leg pivot core), 1m, bar by bar.

External pre-registered hypothesis from the live strategy — FROZEN thresholds (65% share, +15pp
spread), no tuning, no variants. Characterization framing: the 1m dataset is SPENT for mining, but
this exact confluence has never been evaluated. Multiplicity +2 cells (long, short) -> running
counter 450 + 2 = 452.

VERBATIM REUSE (no re-implementations): p9_global / confirmed_crosses / phase_traj are imported from
study.setups_S3 (which builds on app.region_state pure functions); the two S3 legs that live as
closures inside setups_S3.main (leg_A2 = P0 crosses, A3 = P2 eff-agg share) are carried here
line-for-line. Selection = [b-15, b] — 16 bars, the standard panel window: S3.W_SEL is set to 16
for this study (S3's own 64-bar ruling applied to S3 only); the terminal lock (LOCK=7) and every
panel constant are untouched.

LEGS (LONG; SHORT = exact mirror):
 1. P0: the most-recent confirmed +-50 crosses of the smoothed-lean sum line inside the pre-lock
    selection — both levels present, both BULL (S3 leg_A2 semantics; confirmed = new side holds
    >= 2 buckets; 0-line crosses excluded).
 2. P2: eff-agg bull share at the LOCKED index >= 65%.
 3. Phase table: UP column's dominant phase is START/DURING (argmax of locked 3-vector == middle).
 4. P6: START/DURING-phase spread (UP middle minus DOWN middle) >= +15pp.

EPISODES: fire bar close = BASELINE (100%). Over the next 30 minutes wall-clock: MFE% = max high
above baseline (floored at 0), MAE% = max low below baseline (capped at 0, signed), end-of-window
close vs baseline, time-to-max-high and time-to-min-low (minutes). NON-OVERLAP: global lockout —
no new fire until the open window expires. First-15-bars lookback and end-of-data windows excluded,
counted. CONTROL: 200 seeded draws of same-count non-overlapping random 30-min windows, same span.
"""
import os, sys, csv, json, sqlite3, calendar, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict      # noqa: E402
import setups_S3 as S3                             # noqa: E402  (S3 pipeline, reused verbatim)

S3.W_SEL = 16          # S5 ruling: rolling selection [b-15, b] — the standard 16-bar panel window
LOCK = S3.LOCK         # 7, terminal lock — untouched

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
DB = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
WIN = 1800.0
SEED, N_DRAWS = 13, 200
REGIME_CUT = calendar.timegm((2026, 6, 30, 0, 0, 0))   # pre/post Jun-30 bull turn
UNDERPOWERED_N = 20


def dist(x):
    if len(x) == 0:
        return dict(mean=float("nan"), med=float("nan"), p25=float("nan"),
                    p75=float("nan"), p90=float("nan"), mx=float("nan"))
    return dict(mean=float(np.mean(x)), med=float(np.median(x)), p25=float(np.percentile(x, 25)),
                p75=float(np.percentile(x, 75)), p90=float(np.percentile(x, 90)), mx=float(np.max(x)))


def ep_stats(eps):
    mfe = np.array([e["mfe"] for e in eps], float)
    amae = np.array([abs(e["mae"]) for e in eps], float)
    end = np.array([e["end"] for e in eps], float)
    return dict(n=len(eps), mfe=dist(mfe), amae=dist(amae),
                pct_win=100.0 * float(np.mean(mfe > amae)) if len(eps) else float("nan"),
                end_mean=float(np.mean(end)) if len(eps) else float("nan"),
                end_med=float(np.median(end)) if len(eps) else float("nan"))


def main():
    t0 = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    bks = [_bucket_from_dict(d) for d in raw]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks); base_id = int(tc[0]) - n
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])

    a_sh, e_sh, r_sh, sum0 = S3.p9_global(snaps)
    crosses = S3.confirmed_crosses(list(sum0))
    print("[%3.0fs] prep done: %d bars, %d confirmed crosses on the P0 sum line"
          % (time.time() - t0, n, len(crosses)), flush=True)

    # structural characterization of leg 1 (reported as fact): how often do a +50 and a -50
    # confirmed cross land close enough to share one 8-bar pre-lock span?
    k_p = [k for k, L, _ in crosses if L == 50.0]
    k_m = [k for k, L, _ in crosses if L == -50.0]
    k_0 = [k for k, L, _ in crosses if L == 0.0]
    gaps = [abs(a - b) for a in k_p for b in k_m]
    gap_min = min(gaps) if gaps else -1
    gap_close = sum(1 for g in gaps if g <= (S3.W_SEL - 1 - LOCK))
    both_present = 0                                 # second, independent path; side-agnostic
    for i in range(16 - 1, n):
        lo_k, hi_k = i - (S3.W_SEL - 1), i - LOCK
        if any(lo_k <= k < hi_k for k in k_p) and any(lo_k <= k < hi_k for k in k_m):
            both_present += 1

    # ---- S3 leg_A2 carried VERBATIM (closure in setups_S3.main; window now via S3.W_SEL=16) ----
    def leg_p0(i, long):
        end = i - LOCK
        if end < 2:
            return False
        vis = [c for c in crosses if (i - S3.W_SEL + 1) <= c[0] < end and c[1] != 0.0]
        last_per = {}
        for k, L, isup in vis:
            last_per[L] = isup
        if len(last_per) < 2:
            return False
        return all(v == long for v in last_per.values())

    # ---- S3 A3 expression carried VERBATIM -----------------------------------------------------
    def leg_p2(i, long):
        sh = e_sh[max(0, i - LOCK)]
        return (sh * 100 >= 65.0) if long else ((1 - sh) * 100 >= 65.0)

    # legs for every evaluable bar (attrition is about trigger logic, before any lockout)
    first = 16 - 1                                    # b >= 15: [b-15, b] fits; first 15 bars excluded
    legs = {"long": np.zeros((n, 4), bool), "short": np.zeros((n, 4), bool)}
    for i in range(first, n):
        upv, dnv = S3.phase_traj(a_sh, e_sh, r_sh, i)
        for side, long in (("long", True), ("short", False)):
            colv = upv if long else dnv
            l3 = (int(np.argmax(colv)) == 1)                          # S3 A4, verbatim
            sd_up, sd_dn = upv[1], dnv[1]
            l4 = ((sd_up - sd_dn) >= 15.0) if long else ((sd_dn - sd_up) >= 15.0)   # S3 A5, verbatim
            legs[side][i] = (leg_p0(i, long), leg_p2(i, long), l3, l4)
        if (i - first) % 2000 == 0:
            print("[%3.0fs] legs i=%d" % (time.time() - t0, i), flush=True)

    # ---- episodes: global lockout, disjoint by construction ------------------------------------
    def excursion(i):
        j1 = int(np.searchsorted(et, et[i] + WIN, side="right"))       # bars with et <= fire+30m
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

    eps = []; n_eod = 0; n_locked = 0
    lock_until = -1e18
    for i in range(first, n):
        fl = legs["long"][i].all(); fs = legs["short"][i].all()
        if not (fl or fs):
            continue
        assert not (fl and fs)                       # impossible: P2 shares cannot both lead >= 65%
        if et[i] < lock_until:
            n_locked += 1
            continue
        if et[i] + WIN > et[-1]:
            n_eod += 1
            continue
        e = excursion(i)
        if e is None:
            n_eod += 1
            continue
        e.update(ts=float(et[i]), bid=base_id + i + 1, side="long" if fl else "short",
                 base=float(cl[i]))
        eps.append(e)
        lock_until = et[i] + WIN

    with open(os.path.join(OUT, "s5_confluence_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "side", "baseline", "MFE_pct", "MAE_pct", "end_pct",
                    "t_max_up_min", "t_max_dn_min"])
        for e in eps:
            w.writerow([round(e["ts"], 3), e["bid"], e["side"], e["base"], round(e["mfe"], 4),
                        round(e["mae"], 4), round(e["end"], 4), round(e["t_up"], 2), round(e["t_dn"], 2)])

    # ---- control: 200 seeded draws of same-count non-overlapping random windows ----------------
    eligible = np.array([i for i in range(first, n) if et[i] + WIN <= et[-1]])
    rng = np.random.default_rng(SEED)

    def control(n_side):
        if n_side == 0:
            return None
        keys = ("mean_mfe", "med_mfe", "mean_amae", "med_amae", "pct_win", "mean_end", "med_end")
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
            for k, v in (("mean_mfe", np.mean(mfe)), ("med_mfe", np.median(mfe)),
                         ("mean_amae", np.mean(amae)), ("med_amae", np.median(amae)),
                         ("pct_win", 100.0 * np.mean(mfe > amae)),
                         ("mean_end", np.mean(end)), ("med_end", np.median(end))):
                draws[k].append(float(v))
        return {k: (float(np.mean(v)), float(np.std(v))) for k, v in draws.items()}

    ep_l = [e for e in eps if e["side"] == "long"]; ep_s = [e for e in eps if e["side"] == "short"]
    ctrl = {"long": control(len(ep_l)), "short": control(len(ep_s))}
    print("[%3.0fs] episodes long=%d short=%d (locked-skip %d, end-of-data %d); control done"
          % (time.time() - t0, len(ep_l), len(ep_s), n_locked, n_eod), flush=True)

    # ---- report ---------------------------------------------------------------------------------
    span_days = (et[-1] - et[0]) / 86400.0
    ev = n - first                                   # evaluable bars
    att = {}
    for side in ("long", "short"):
        L = legs[side][first:]
        att[side] = dict(solo=[int(L[:, k].sum()) for k in range(4)],
                         chain=[int(L[:, :k + 1].all(axis=1).sum()) for k in range(4)])

    def fmt_d(d):
        return "| %.3f | %.3f | %.3f | %.3f | %.3f | %.3f |" % (
            d["mean"], d["med"], d["p25"], d["p75"], d["p90"], d["mx"])

    def side_block(side, ep_side):
        st = ep_stats(ep_side)
        nn = st["n"]
        lines = ["### %s — %d episodes (%.2f fires/day)" % (side.upper(), nn, nn / span_days), ""]
        if nn < UNDERPOWERED_N:
            lines += ["**UNDERPOWERED (n = %d < %d): no distribution / control / regime analysis "
                      "for this side — the study stops here per protocol. Episodes are in the CSV; "
                      "verdict deferred to forward data.**" % (nn, UNDERPOWERED_N), ""]
            return lines, False
        lines += ["| metric | mean | median | p25 | p75 | p90 | max |", "|---|---|---|---|---|---|---|",
                  "| MFE % " + fmt_d(st["mfe"]), "| \\|MAE\\| % " + fmt_d(st["amae"]), "",
                  "Ratio view: median MFE **%.3f%%** vs median \\|MAE\\| **%.3f%%**; MFE > \\|MAE\\| in "
                  "**%.1f%%** of episodes; end-of-window mean %+.3f%% / median %+.3f%%."
                  % (st["mfe"]["med"], st["amae"]["med"], st["pct_win"], st["end_mean"], st["end_med"]), ""]
        c = ctrl[side]
        lines += ["Control (null: %d random non-overlapping 30-min windows, %d seeded draws, seed %d) "
                  "— actual vs control mean ± sd:" % (st["n"], N_DRAWS, SEED),
                  "", "| stat | actual | control |", "|---|---|---|",
                  "| median MFE %% | %.3f | %.3f ± %.3f |" % (st["mfe"]["med"], c["med_mfe"][0], c["med_mfe"][1]),
                  "| median \\|MAE\\| %% | %.3f | %.3f ± %.3f |" % (st["amae"]["med"], c["med_amae"][0], c["med_amae"][1]),
                  "| %% MFE > \\|MAE\\| | %.1f | %.1f ± %.1f |" % (st["pct_win"], c["pct_win"][0], c["pct_win"][1]),
                  "| mean end %% | %+.3f | %+.3f ± %.3f |" % (st["end_mean"], c["mean_end"][0], c["mean_end"][1]),
                  "| median end %% | %+.3f | %+.3f ± %.3f |" % (st["end_med"], c["med_end"][0], c["med_end"][1]), ""]
        pre = [e for e in ep_side if e["ts"] < REGIME_CUT]; post = [e for e in ep_side if e["ts"] >= REGIME_CUT]
        lines += ["Regime split (cut 2026-06-30 00:00 UTC):", "",
                  "| regime | n | med MFE | med \\|MAE\\| | % MFE>\\|MAE\\| | med end |", "|---|---|---|---|---|---|"]
        for nm, part in (("pre (chop)", pre), ("post (bull)", post)):
            s = ep_stats(part)
            lines.append("| %s | %d | %.3f | %.3f | %s | %s |" % (
                nm, s["n"], s["mfe"]["med"], s["amae"]["med"],
                "%.1f" % s["pct_win"] if s["n"] else "-", "%+.3f" % s["end_med"] if s["n"] else "-"))
        lines.append("")
        return lines, True

    md = ["# S5 — Confluence Excursion Study (Yassine's 4-leg pivot core, 1m)", "",
          "_**External pre-registered hypothesis** from the live strategy; thresholds FROZEN (65% "
          "eff-agg share, +15pp phase spread), no tuning, no variants. **Characterization framing:** "
          "the 1m dataset is SPENT for mining, but this exact confluence has never been evaluated. "
          "Multiplicity: **+2 cells (long, short) -> running counter 452**. Excursions are an "
          "information measure — no fees, no execution model._", "",
          "**Verbatim reuse:** `p9_global` / `confirmed_crosses` / `phase_traj` imported from "
          "`setups_S3` (built on `app.region_state`); S3's closure legs A2 (P0 crosses) and A3 "
          "(P2 share) carried line-for-line. Selection [b-15, b] = 16 bars (`S3.W_SEL = 16`); "
          "terminal lock 7 and all panel constants untouched. Leg semantics: P0 = most-recent "
          "confirmed cross per ±50 level, both levels present in the pre-lock selection, both on "
          "the fire side; confirmed = new side holds >= 2 buckets; 0-line crosses excluded.", "",
          "## Data & exclusions",
          "%d 1m bars, %s -> %s UTC (%.2f days). Evaluable bars %d (first %d excluded for lookback); "
          "fires skipped inside an open 30-min window: %d; end-of-data windows excluded: %d. "
          "Confirmed ±50/0 crosses on the P0 sum line over the full series: %d."
          % (n, time.strftime("%m-%d %H:%M", time.gmtime(et[0])),
             time.strftime("%m-%d %H:%M", time.gmtime(et[-1])), span_days, ev, first, n_locked,
             n_eod, len(crosses)), "",
          "## Leg attrition (trigger selectivity, all evaluable bars)", "",
          "| side | leg | standalone pass | cumulative (1..k) |", "|---|---|---|---|"]
    leg_names = ("1 P0 both ±50 crosses on-side", "2 P2 eff-agg share >= 65%",
                 "3 phase dominant START/DURING", "4 P6 spread >= 15pp")
    for side in ("long", "short"):
        for k in range(4):
            md.append("| %s | %s | %d (%.2f%%) | %d (%.3f%%) |" % (
                side, leg_names[k], att[side]["solo"][k], 100.0 * att[side]["solo"][k] / ev,
                att[side]["chain"][k], 100.0 * att[side]["chain"][k] / ev))
    md += ["",
           "**Why leg 1 gates everything (verified structurally, two independent code paths):** the "
           "P0 sum line is the smoothed lean (averaged with its 7-bar-lagged value), so crossing BOTH "
           "-50 and +50 inside one 8-bar pre-lock span demands a >=100-point sweep of a deliberately "
           "sluggish line. Confirmed crosses on this tape: +50 x%d, 0 x%d, -50 x%d — yet a +50 and a "
           "-50 cross fall within 8 bars of each other only %d times (minimum observed gap %d bars), "
           "and exactly %d bar(s) hold both levels inside their pre-lock window at all (of which %d "
           "same-side). At the 16-bar selection, leg 1 alone caps the confluence near one fire per "
           "4-day tape; the 65/15 legs never get to filter."
           % (len(k_p), len(k_0), len(k_m), gap_close, gap_min, both_present,
              att["long"]["chain"][0] + att["short"]["chain"][0]),
           "", "## Episodes (non-overlapping, 30-min wall-clock windows)", ""]
    any_powered = False
    for side, ep_side in (("long", ep_l), ("short", ep_s)):
        blk, powered = side_block(side, ep_side)
        md += blk
        any_powered = any_powered or powered
    md += ["## Honest flags",
           "- 4-leg AND at frozen 65/15 thresholds -> few fires by design; sides under n=%d are "
           "reported as UNDERPOWERED and not interpreted." % UNDERPOWERED_N,
           "- The tape is %.2f days of one market phase (plus the Jun-30 turn); fires/day is not a "
           "stable estimate at this n." % span_days,
           "- 1m mining credibility is spent; whatever appears here is a hypothesis for forward "
           "snapshots, not a verdict.",
           "", "## HARD STOP", "No threshold variants were run. Judged once, characterization only."]
    with open(os.path.join(OUT, "analysis_report_S5_confluence.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] report + CSV written | powered side(s): %s" % (time.time() - t0, any_powered), flush=True)


if __name__ == "__main__":
    main()
