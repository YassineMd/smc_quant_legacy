"""S5c — CONTEXT-50 CONFLUENCE (pre-registered extension of S5b/S5b-r; NOT a tuning pass).

New leg from Yassine's live logic; multiplicity +4 cells (long/short x locked/unlocked) -> program
counter 458.

LEG 5 — 50-BAR CONTEXT (frozen, not tunable this run): composite bar over [b-49, b]:
O* = open(b-49), C* = close(b). LONG eligible: O* > C* (net 50-bar decline — bull momentum fired in
FADE context). SHORT eligible: O* < C* (net rise). Tick-exact equality ($0.01) -> neither, counted.
Universe starts at idx >= 50 (S5b used idx >= 16 — change reported).

VARIANTS (both run, side by side):
 V-LOCKED   = S5b-r legs 1'-4: leg 2 reads the LOCKED eff-agg badge SPREAD >= 65 (share >= 82.5%),
              the terminal confluence-alert rule — what the eyes see.
 V-UNLOCKED = original S5b legs 1'-4 pre-correction: leg 2 = share >= 65% (spread >= 30). Legs 1',
              3, 4 are identical between variants.
 fire = legs 1'-4 AND leg 5, per side, per variant.

MACHINERY — identical to S5b, reused verbatim: legs 1/3/4 taken from the committed sweep table
(m10_sweep_1m.parquet); both leg-2 variants recomputed exactly from the same e_sh series (locked
variant asserted equal to the sweep's leg2 columns row-for-row); 30-min windows from fire close,
MFE%/MAE%/end%, t_max both sides, NON-OVERLAP lockout PER VARIANT-SIDE (4 independent streams),
lookback + end-of-data excluded and counted, 200-draw seeded random-window control (seed 13),
regime split at Jun-30 00:00 UTC, underpowered rule n < 20 -> counts only, no distributions.
"""
import os, sys, csv, calendar, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
import s5_confluence as S5                             # noqa: E402  (ep_stats/dist/UNDERPOWERED_N)
from m10_sweep_s5b import load_merged, p9_full, LOCK   # noqa: E402  (S5b machinery, verbatim)

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
WIN = 1800.0
SEED, N_DRAWS = 13, 200
REGIME_CUT = calendar.timegm((2026, 6, 30, 0, 0, 0))
FIRST = 50                                             # spec: universe idx >= 50 (S5b: 16)
CELLS = (("locked", "long"), ("locked", "short"), ("unlocked", "long"), ("unlocked", "short"))


def main():
    t0 = time.time()
    bids, raws, per_db, gaps, dup = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    op = np.array([b.open_price for b in bks]); cl = np.array([b.close_price for b in bks])
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    et = np.array([b.end_time for b in bks]); bid_arr = np.array(bids)
    snaps = [b.full_snapshot() for b in bks]
    _, e_sh, _, _, _, _, _, _ = p9_full(snaps)
    df = pd.read_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"))
    assert int(df.bucket_id.iloc[0]) == bids[16] and len(df) == n - 16
    print("[%3.0fs] merged %d bars; sweep table joined" % (time.time() - t0, n), flush=True)

    # leg 2 both variants, recomputed exactly; locked asserted against the sweep's columns
    idx = np.arange(16, n)                              # sweep row r <-> merged bar r+16
    sh = e_sh[np.maximum(0, idx - LOCK)]
    spr2 = (2.0 * sh - 1.0) * 100.0
    l2_lock_L = spr2 >= 65.0; l2_lock_S = -spr2 >= 65.0
    l2_unlk_L = sh * 100.0 >= 65.0; l2_unlk_S = (1 - sh) * 100.0 >= 65.0
    assert (l2_lock_L == df.leg2_long.to_numpy()).all() and (l2_lock_S == df.leg2_short.to_numpy()).all()

    # leg 5 (tick-exact cents) on merged bars
    rO = np.round(op * 100).astype(np.int64); rC = np.round(cl * 100).astype(np.int64)
    l5_L = np.zeros(n, bool); l5_S = np.zeros(n, bool); n_flat50 = 0
    for b in range(49, n):
        a, c = rO[b - 49], rC[b]
        if a > c:
            l5_L[b] = True
        elif a < c:
            l5_S[b] = True
        elif b >= FIRST:
            n_flat50 += 1

    l134 = {s: (df["leg1_" + s].to_numpy() & df["leg3_" + s].to_numpy()
                & df["leg4_" + s].to_numpy()) for s in ("long", "short")}
    four = {("locked", "long"): l134["long"] & l2_lock_L,
            ("locked", "short"): l134["short"] & l2_lock_S,
            ("unlocked", "long"): l134["long"] & l2_unlk_L,
            ("unlocked", "short"): l134["short"] & l2_unlk_S}
    inuniv = idx >= FIRST                               # sweep rows inside the S5c universe
    fires, att = {}, {}
    for cell in CELLS:
        v, s = cell
        f4 = four[cell] & inuniv
        l5 = (l5_L if s == "long" else l5_S)[idx]
        att[cell] = (int(f4.sum()), int((f4 & l5).sum()))
        fires[cell] = f4 & l5

    # ---- episodes: independent lockout per cell (S5b excursion, verbatim) ----------------------
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

    eps = {}; excl = {}
    for cell in CELLS:
        v, s = cell
        out = []; n_eod = 0; n_locked = 0; lock_until = -1e18
        for r in np.flatnonzero(fires[cell]):
            i = int(idx[r])
            if et[i] < lock_until:
                n_locked += 1
                continue
            if et[i] + WIN > et[-1]:
                n_eod += 1
                continue
            e = excursion(i)
            e.update(ts=float(et[i]), bid=int(bid_arr[i]), side=s, base=float(cl[i]))
            out.append(e)
            lock_until = et[i] + WIN
        eps[cell] = out; excl[cell] = (n_locked, n_eod)

    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5c_episodes_%s.csv" % v), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "bucket_id", "side", "baseline", "MFE_pct", "MAE_pct", "end_pct",
                        "t_max_up_min", "t_max_dn_min"])
            for s in ("long", "short"):
                for e in eps[(v, s)]:
                    w.writerow([round(e["ts"], 3), e["bid"], s, e["base"], round(e["mfe"], 4),
                                round(e["mae"], 4), round(e["end"], 4), round(e["t_up"], 2),
                                round(e["t_dn"], 2)])

    # ---- control (S5b verbatim; one rng, cells in fixed order) ---------------------------------
    eligible = np.array([i for i in range(FIRST, n) if et[i] + WIN <= et[-1]])
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
        return {k: (float(np.mean(x)), float(np.std(x))) for k, x in draws.items()}

    ctrl = {cell: control(len(eps[cell])) if len(eps[cell]) >= S5.UNDERPOWERED_N else None
            for cell in CELLS}
    for cell in CELLS:
        print("  %s-%s: 4-leg %d -> +leg5 %d -> episodes %d (locked-skip %d, eod %d)"
              % (cell[0], cell[1], att[cell][0], att[cell][1], len(eps[cell]), *excl[cell]), flush=True)

    # ---- report ---------------------------------------------------------------------------------
    span_days = (et[-1] - et[0]) / 86400.0
    univ = n - FIRST

    def pnl_line(ep_side, side, fee):
        pnl = np.array([(e["end"] if side == "long" else -e["end"]) - fee for e in ep_side])
        w_ = pnl[pnl > 0]; l_ = pnl[pnl < 0]
        return ("W/L/F %d/%d/%d | sum %+.2f%% | mean %+.3f%%/trade | avg win %+.3f%% vs avg loss %+.3f%%"
                % (len(w_), len(l_), len(pnl) - len(w_) - len(l_), pnl.sum(), pnl.mean(),
                   w_.mean() if len(w_) else 0.0, l_.mean() if len(l_) else 0.0))

    def fmt_d(d):
        return "| %.3f | %.3f | %.3f | %.3f | %.3f | %.3f |" % (
            d["mean"], d["med"], d["p25"], d["p75"], d["p90"], d["mx"])

    md = ["# S5c — Context-50 Confluence (locked + unlocked variants, 1m merged span)", "",
          "_**Pre-registered extension of S5b/S5b-r — NOT a tuning pass. New leg 5 from the live "
          "logic; multiplicity +4 cells (long/short x locked/unlocked) -> program counter 458.** "
          "Leg 5 (frozen, 50 not tunable this run): composite 50-bar bar over [b-49, b]; LONG "
          "eligible only after a net 50-bar DECLINE (O* > C*, tick-exact at $0.01), SHORT after a "
          "net rise — bull momentum in fade context and vice versa. Legs 1'-4 per variant: V-LOCKED "
          "= S5b-r (badge spread >= 65); V-UNLOCKED = original S5b pre-correction (share >= 65); "
          "legs 1'/3/4 identical between variants. Machinery identical to S5b (30-min windows, "
          "per-cell non-overlap, seed-13 200-draw control, Jun-30 regime split, underpowered rule "
          "n < 20). Excursions are an information measure; gross unless stated._", "",
          "## Data & universe",
          "Same merged tape as S5b: %d bars, %s -> %s UTC (%.2f days). **Universe change: idx >= %d "
          "-> %d evaluable rows (S5b: idx >= 16, 12,559 rows; 34 rows lost to the longer lookback).** "
          "50-bar flat composites (O* == C* tick-exact): %d bars -> neither side, excluded."
          % (n, time.strftime("%m-%d %H:%M", time.gmtime(et[0])),
             time.strftime("%m-%d %H:%M", time.gmtime(et[-1])), span_days, FIRST, univ, n_flat50), "",
          "## 1. Attrition — momentum fires vs the 50-bar context", "",
          "| cell | 4-leg fires | + leg 5 (context) | kept | episodes | locked-skip / eod |",
          "|---|---|---|---|---|---|"]
    for cell in CELLS:
        a4, a5 = att[cell]
        md.append("| %s-%s | %d | %d | %.0f%% | %d | %d / %d |" % (
            cell[0].upper(), cell[1], a4, a5, 100.0 * a5 / a4 if a4 else 0.0,
            len(eps[cell]), excl[cell][0], excl[cell][1]))
    md += ["",
           "Reading: 'kept' = the share of momentum fires that happened in FADE context (against "
           "the 50-bar drift); the rest fired with the trend and are excluded by leg 5.", ""]

    powered = [c for c in CELLS if len(eps[c]) >= S5.UNDERPOWERED_N]
    md += ["## 2-3. Per-cell results", ""]
    for cell in CELLS:
        v, s = cell
        ep_c = eps[cell]
        nn = len(ep_c)
        md += ["### %s-%s — %d episodes (%.2f fires/day)" % (v.upper(), s, nn, nn / span_days), ""]
        if nn < S5.UNDERPOWERED_N:
            md += ["**UNDERPOWERED (n = %d < %d): counts only, per protocol — no distributions, no "
                   "control, no if-taken line. Episodes in the CSV; forward tape is the judge.**"
                   % (nn, S5.UNDERPOWERED_N), ""]
            continue
        st = S5.ep_stats(ep_c)
        md += ["| ts (UTC) | bucket | base | MFE% | MAE% | end% | t_up | t_dn |",
               "|---|---|---|---|---|---|---|---|"]
        for e in ep_c:
            md.append("| %s | %d | %.2f | %.3f | %.3f | %+.3f | %.1f | %.1f |" % (
                time.strftime("%m-%d %H:%M", time.gmtime(e["ts"])), e["bid"], e["base"],
                e["mfe"], e["mae"], e["end"], e["t_up"], e["t_dn"]))
        c = ctrl[cell]
        md += ["", "| metric | mean | median | p25 | p75 | p90 | max |", "|---|---|---|---|---|---|---|",
               "| MFE % " + fmt_d(st["mfe"]), "| \\|MAE\\| % " + fmt_d(st["amae"]), "",
               "Ratio: med MFE %.3f vs med \\|MAE\\| %.3f; MFE > \\|MAE\\| %.1f%%; end mean %+.3f%% / "
               "med %+.3f%%." % (st["mfe"]["med"], st["amae"]["med"], st["pct_win"],
                                 st["end_mean"], st["end_med"]),
               "", "Control (n=%d windows, %d draws, seed %d): med MFE %.3f±%.3f | med \\|MAE\\| "
               "%.3f±%.3f | win %.1f±%.1f | mean end %+.3f±%.3f"
               % (st["n"], N_DRAWS, SEED, c["med_mfe"][0], c["med_mfe"][1], c["med_amae"][0],
                  c["med_amae"][1], c["pct_win"][0], c["pct_win"][1], c["mean_end"][0], c["mean_end"][1]), ""]
        pre = [e for e in ep_c if e["ts"] < REGIME_CUT]; post = [e for e in ep_c if e["ts"] >= REGIME_CUT]
        md += ["Regime split: pre (chop) n=%d med end %s | post (bull) n=%d med end %s"
               % (len(pre), "%+.3f%%" % float(np.median([e["end"] for e in pre])) if pre else "-",
                  len(post), "%+.3f%%" % float(np.median([e["end"] for e in post])) if post else "-"), "",
               "If every setup were taken (window-end, %s side):" % s,
               "- GROSS: " + pnl_line(ep_c, s, 0.0),
               "- NET taker 0.10% RT: " + pnl_line(ep_c, s, 0.10), ""]

    md += ["## 4. LOCKED vs UNLOCKED — side by side", "",
           "| cell | 4-leg | +leg5 | episodes | status | med MFE | med \\|MAE\\| | win% | med end |",
           "|---|---|---|---|---|---|---|---|---|"]
    for cell in CELLS:
        ep_c = eps[cell]
        if len(ep_c) >= S5.UNDERPOWERED_N:
            st = S5.ep_stats(ep_c)
            md.append("| %s-%s | %d | %d | %d | powered | %.3f | %.3f | %.1f | %+.3f |" % (
                cell[0].upper(), cell[1], att[cell][0], att[cell][1], len(ep_c),
                st["mfe"]["med"], st["amae"]["med"], st["pct_win"], st["end_med"]))
        else:
            md.append("| %s-%s | %d | %d | %d | UNDERPOWERED | - | - | - | - |" % (
                cell[0].upper(), cell[1], att[cell][0], att[cell][1], len(ep_c)))
    verdict = ("All four cells UNDERPOWERED — the context filter cut fires as anticipated; the "
               "machinery reruns unchanged on forward snapshots. STOP." if not powered else
               "%d/4 cells powered; underpowered cells defer to forward snapshots." % len(powered))
    md += ["", "## Honest flags",
           "- Thresholds frozen: 50 bars, 65/30-spread, 15pp — nothing tuned this run.",
           "- Fee line is taker 0.10% round-trip on window-end only — no slippage, no stop logic; "
           "it is an accounting view, not a backtest.",
           "- %.2f-day tape, one bull phase + ~1.5 days chop; 1m spent for mining." % span_days,
           "", "## VERDICT", verdict, "", "## HARD STOP",
           "Judged once; no variants beyond the two pre-registered; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5c.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] report + 2 episode CSVs written | %s" % (time.time() - t0, verdict), flush=True)


if __name__ == "__main__":
    main()
