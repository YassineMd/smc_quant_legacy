"""M10-SWEEP + S5b — full Mode-10 extraction over ALL 1m data + the corrected confluence study.

DATA: frozen snapshot (Jun 28 - Jul 2) merged with the fresh pull (Jun 29 - Jul 3), dedup by
bucket_id -> one continuous 1m span.

PART 1 — sweep table (m10_sweep_1m.parquet + .csv), one row per bar b (idx >= 16), selection
[b-15, b], every value via the panel/region_state pure code VERBATIM:
 * P1-P4 + S.01-S.14: features_b.compute_bscope (the terminal's _refresh_selection_stats math,
   badge = LOCKED index) — full B-scope keyset.
 * P5/P6/P7 (phase rows BEFORE / START-DURING / END per side) + dominant phase: setups_S3.phase_traj
   with the 16-bar selection (S3.W_SEL = 16).
 * P9 bull/bear/sum + P0 smoothed bull/bear/sum: p9_global's body carried line-for-line (extended
   returns only; sum0 asserted equal to setups_S3.p9_global's output).
 * P0 confirmed-cross log: the terminal's _draw_level_crosses/_last_cross carried line-for-line —
   ONE most-recent confirmed cross per level {+50, 0, -50} (zero line INCLUDED) inside the LOCKED
   slice of the selection (vals[:16-7]); the settling/forming dot region is EXCLUDED. Distilled to
   last / 2nd-last marker columns (level, direction, bars-ago; bars-ago fractional — the marker's
   interpolated x, exactly what the panel draws).
 * LEG columns both sides: leg1' = two most recent confirmed markers both bullish (long) / both
   bearish (short), any level, >= 2 markers required. leg2 = eff-agg share at the locked index
   >= 65% (S3 A3 verbatim). leg3 = phase dominant == START/DURING. leg4 = P6 spread >= 15pp.
   fire_long / fire_short = AND of four.
 * OUTCOME columns (instrumentation, overlapping): fwd-30min MFE% / MAE% / end% from bar close.
 * P8 EXCLUDED: size_thr was never persisted to history -> P8 cannot be replayed offline.

STEP 0 GATE: bucket idx 20977 (2026-07-03 03:20:49 UTC = 04:20:49 operator local, identity verified
by idx + exact OHLC match with the screenshot) must evaluate fire_long == TRUE, else STOP and print
the four legs vs the screenshot (81% eff-agg, START/DURING 52%, crosses -50/0/+50 all green).

PART 2 — S5b (pre-registered CORRECTION of S5's mistranslated leg 1, not a tweak; multiplicity +2
-> program counter 454): identical episode machinery to S5 — 30-min windows, global non-overlap
lockout, insufficient-lookback/end-of-data excluded and counted, attrition chain, standalone rates,
200-draw seeded control, regime split at Jun-30 00:00 UTC. Underpowered rule stands (n < 20/side ->
counts only).

The sweep table is INSTRUMENTATION — any rule found by browsing it must be pre-registered and judged
on forward tape before it counts.
"""
import os, sys, csv, json, sqlite3, calendar, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
from app import region_state as R, config              # noqa: E402
import setups_S3 as S3                                 # noqa: E402
import s5_confluence as S5                             # noqa: E402  (sets S3.W_SEL = 16; dist/ep_stats)
import features_b as FTB                               # noqa: E402
import features as FT                                  # noqa: E402

assert S3.W_SEL == 16
LOCK = S3.LOCK; LW = S3.LW
REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
DBS = ("study/data/history_snapshot_20260702.db", "study/data/history_snapshot_20260703.db")
ANCHOR_BID = 20977
WIN = 1800.0
SEED, N_DRAWS = 13, 200
REGIME_CUT = calendar.timegm((2026, 6, 30, 0, 0, 0))
FIRST = 16                                             # spec: one row per bar b with idx >= 16
PHASE_NAMES = ("BEFORE", "STARTDUR", "END")


def load_merged():
    by_bid = {}
    per_db = {}
    for db in DBS:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(REPO, db), uri=True)
        raw = [json.loads(x[0]) for x in con.execute(
            "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
        tc = int(con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()[0])
        con.close()
        base = tc - len(raw)
        per_db[db] = (len(raw), base + 1, base + len(raw))
        for j, d in enumerate(raw):
            by_bid[base + j + 1] = d                    # later db (fresh pull) wins on dupes
    bids = sorted(by_bid)
    gaps = [(a, b) for a, b in zip(bids, bids[1:]) if b != a + 1]
    dup = sum(v[0] for v in per_db.values()) - len(bids)
    return bids, [by_bid[b] for b in bids], per_db, gaps, dup


# ---- p9_global body carried LINE-FOR-LINE from setups_S3 (extended returns only) ----------------
def p9_full(snaps):
    n = len(snaps)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    a_sh = np.array(R.rolling_share(ab, ar, LW), float)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.array(R.rolling_share(eb, er_, LW), float)
    rb = [s.get("buyer_er", 0.0) for s in snaps]; rs_ = [s.get("seller_er", 0.0) for s in snaps]
    r_sh = np.array(R.rolling_share(rb, rs_, LW), float)
    lean = (1 - 2 * a_sh) * 100 + (2 * e_sh - 1) * 100 + (2 * r_sh - 1) * 100
    ex = R.trailing_exhaustion(snaps, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    s4 = np.empty(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        s4[k] = hold
    bull = (lean + s4) / 4.0; bear = (lean - s4) / 4.0
    idx0 = np.maximum(np.arange(n) - LOCK, 0)
    sb = (bull + bull[idx0]) / 2.0; sr = (bear + bear[idx0]) / 2.0
    sum0 = sb + sr                                      # == p9_global's P0 smoothed SUM line
    return a_sh, e_sh, r_sh, bull, bear, sb, sr, sum0


# ---- terminal _draw_level_crosses inner _last_cross carried LINE-FOR-LINE -----------------------
def _last_cross(vals, ex, lo_k, hi_k, L, up_c, dn_c, confirm_end):
    last = None
    for k in range(lo_k, hi_k):
        a = float(vals[k - 1]) - L; b = float(vals[k]) - L
        if a == 0.0 or b == 0.0 or (a < 0) == (b < 0):
            continue                              # no sign change across L
        newpos = b > 0                            # crossed to ABOVE L (upward)
        if all((float(vals[j]) - L > 0) == newpos for j in range(k, min(confirm_end, k + 2))):
            frac = a / (a - b)                    # interpolate the crossing x
            last = (ex[k - 1] + frac * (ex[k] - ex[k - 1]), L, up_c if newpos else dn_c)
    return last


def sel_markers(sum0, b):
    """The panel's CONFIRMED (locked, X) markers for selection [b-15, b]: one per level, forming
    (settling-dot) region excluded. Returns [(x_rel, level, dir)], x_rel in selection space 0..15."""
    vals = sum0[b - 15:b + 1]; ex = list(range(16))
    end = 16 - LOCK                                     # locked region = vals[:end], as the panel
    out = []
    for L in (50.0, 0.0, -50.0):
        m = _last_cross(vals, ex, 1, end, L, 1, -1, end)
        if m is not None:
            out.append(m)
    out.sort(key=lambda m: -m[0])                       # most recent first
    return out


def main():
    t0 = time.time()
    bids, raws, per_db, gaps, dup = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    bid_arr = np.array(bids)
    print("[%4.0fs] merged %d bars (dedup removed %d) span %s -> %s UTC, gaps: %s"
          % (time.time() - t0, n, dup, time.strftime("%m-%d %H:%M", time.gmtime(et[0])),
             time.strftime("%m-%d %H:%M", time.gmtime(et[-1])), gaps or "none"), flush=True)
    for db, (cnt, b0, b1) in per_db.items():
        print("   %s: %d rows, bids %d..%d" % (os.path.basename(db), cnt, b0, b1))

    a_sh, e_sh, r_sh, p9b, p9r, p0b, p0r, sum0 = p9_full(snaps)
    chk = S3.p9_global(snaps)[3]
    assert np.allclose(sum0, chk), "p9_full diverged from setups_S3.p9_global"
    rs = FT.repo_series(snaps, bks)
    print("[%4.0fs] global series ready (p9_full == p9_global asserted)" % (time.time() - t0), flush=True)

    rows = []
    for b in range(FIRST, n):
        row = {"ts": round(float(et[b]), 3), "bucket_id": int(bid_arr[b])}
        for k, v in FTB.compute_bscope(snaps, rs, b).items():
            row[k[2:]] = v                              # strip "B-" prefix -> P1.01 ... S.14
        upv, dnv = S3.phase_traj(a_sh, e_sh, r_sh, b)
        for j, tag in ((0, "p5"), (1, "p6"), (2, "p7")):
            row[tag + "_up"] = round(upv[j], 3); row[tag + "_dn"] = round(dnv[j], 3)
            row[tag + "_spr"] = round(upv[j] - dnv[j], 3)
        row["phase_dom_up"] = PHASE_NAMES[int(np.argmax(upv))]
        row["phase_dom_dn"] = PHASE_NAMES[int(np.argmax(dnv))]
        li = max(0, b - LOCK)
        row["p9_bull"] = round(float(p9b[li]), 3); row["p9_bear"] = round(float(p9r[li]), 3)
        row["p9_sum"] = round(float(p9b[li] + p9r[li]), 3)
        row["p0_bull"] = round(float(p0b[li]), 3); row["p0_bear"] = round(float(p0r[li]), 3)
        row["p0_sum"] = round(float(sum0[li]), 3)
        mk = sel_markers(sum0, b)
        for j, tag in ((0, "cross1"), (1, "cross2")):
            if j < len(mk):
                x, L, d = mk[j]
                row[tag + "_level"] = L
                row[tag + "_dir"] = "up" if d > 0 else "down"
                row[tag + "_ago"] = round(15.0 - x, 3)
            else:
                row[tag + "_level"] = np.nan; row[tag + "_dir"] = ""; row[tag + "_ago"] = np.nan
        # legs (leg2 = S3 A3 verbatim; leg3/leg4 = S3 A4/A5 verbatim)
        two = len(mk) >= 2
        row["leg1_long"] = bool(two and mk[0][2] > 0 and mk[1][2] > 0)
        row["leg1_short"] = bool(two and mk[0][2] < 0 and mk[1][2] < 0)
        sh = e_sh[max(0, b - LOCK)]
        row["leg2_long"] = bool(sh * 100 >= 65.0); row["leg2_short"] = bool((1 - sh) * 100 >= 65.0)
        row["leg3_long"] = bool(int(np.argmax(upv)) == 1); row["leg3_short"] = bool(int(np.argmax(dnv)) == 1)
        sd_up, sd_dn = upv[1], dnv[1]
        row["leg4_long"] = bool((sd_up - sd_dn) >= 15.0); row["leg4_short"] = bool((sd_dn - sd_up) >= 15.0)
        row["fire_long"] = row["leg1_long"] and row["leg2_long"] and row["leg3_long"] and row["leg4_long"]
        row["fire_short"] = row["leg1_short"] and row["leg2_short"] and row["leg3_short"] and row["leg4_short"]
        # per-bar overlapping forward-30min instrumentation
        j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
        if et[b] + WIN <= et[-1] and j1 > b + 1:
            base = cl[b]; w = slice(b + 1, j1)
            row["fwd30_mfe"] = round(max(0.0, (float(np.max(hi[w])) - base) / base * 100.0), 4)
            row["fwd30_mae"] = round(min(0.0, (float(np.min(lo_[w])) - base) / base * 100.0), 4)
            row["fwd30_end"] = round((float(cl[w][-1]) - base) / base * 100.0, 4)
        else:
            row["fwd30_mfe"] = row["fwd30_mae"] = row["fwd30_end"] = np.nan
        rows.append(row)
        if (b - FIRST) % 1000 == 0:
            print("[%4.0fs] sweep b=%d/%d" % (time.time() - t0, b, n), flush=True)
    df = pd.DataFrame(rows)

    # ---- STEP 0 GATE --------------------------------------------------------------------------
    ga = df[df["bucket_id"] == ANCHOR_BID]
    if len(ga) != 1:
        print("GATE FAIL: anchor bid %d not in sweep" % ANCHOR_BID); sys.exit(2)
    g = ga.iloc[0]
    print("\nSTEP 0 — ANCHOR PARITY (bid %d, %s UTC):" % (
        ANCHOR_BID, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(g["ts"]))))
    print("  eff-agg badge (leg2 path) %.1f%%  | B-scope P2.01 %.1f%%  [screenshot: 81%%]"
          % (e_sh[max(0, int(np.where(bid_arr == ANCHOR_BID)[0][0]) - LOCK)] * 100, g["P2.01"]))
    print("  absorption badge P1.01 %.1f%%  [screenshot second badge: 26%%]" % g["P1.01"])
    print("  P0 smoothed sum @lock %+.1f  [screenshot: +55.6%%]" % g["p0_sum"])
    print("  phase UP [BEFORE/STARTDUR/END] = [%.0f, %.0f, %.0f]  [screenshot: 13/52/35]"
          % (g["p5_up"], g["p6_up"], g["p7_up"]))
    print("  phase DOWN = [%.0f, %.0f, %.0f]; P6 spread %+.1f" % (g["p5_dn"], g["p6_dn"], g["p7_dn"], g["p6_spr"]))
    print("  markers: c1 %s@%s ago %.2f | c2 %s@%s ago %.2f  [screenshot: -50/0/+50 all green]"
          % (g["cross1_dir"], g["cross1_level"], g["cross1_ago"],
             g["cross2_dir"], g["cross2_level"], g["cross2_ago"]))
    print("  legs L: 1'=%s 2=%s 3=%s 4=%s -> fire_long=%s"
          % (g["leg1_long"], g["leg2_long"], g["leg3_long"], g["leg4_long"], g["fire_long"]))
    if not bool(g["fire_long"]):
        print("\nGATE FAIL — reconciliation needed; nothing written, study NOT run.")
        sys.exit(2)
    print("GATE PASS\n", flush=True)

    df.to_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"), index=False)
    df.to_csv(os.path.join(OUT, "m10_sweep_1m.csv"), index=False)

    # ---- PART 2 — S5b episodes (S5 machinery) ---------------------------------------------------
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

    fl = df["fire_long"].to_numpy(); fs = df["fire_short"].to_numpy()
    eps = []; n_eod = 0; n_locked = 0; lock_until = -1e18
    for r in range(len(df)):
        i = r + FIRST
        if not (fl[r] or fs[r]):
            continue
        if et[i] < lock_until:
            n_locked += 1
            continue
        if et[i] + WIN > et[-1]:
            n_eod += 1
            continue
        e = excursion(i)
        e.update(ts=float(et[i]), bid=int(bid_arr[i]), side="long" if fl[r] else "short",
                 base=float(cl[i]))
        eps.append(e)
        lock_until = et[i] + WIN

    with open(os.path.join(OUT, "s5b_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "side", "baseline", "MFE_pct", "MAE_pct", "end_pct",
                    "t_max_up_min", "t_max_dn_min"])
        for e in eps:
            w.writerow([round(e["ts"], 3), e["bid"], e["side"], e["base"], round(e["mfe"], 4),
                        round(e["mae"], 4), round(e["end"], 4), round(e["t_up"], 2), round(e["t_dn"], 2)])

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
        return {k: (float(np.mean(v)), float(np.std(v))) for k, v in draws.items()}

    ep_l = [e for e in eps if e["side"] == "long"]; ep_s = [e for e in eps if e["side"] == "short"]
    ctrl = {"long": control(len(ep_l)), "short": control(len(ep_s))}
    print("[%4.0fs] episodes long=%d short=%d (locked-skip %d, eod %d)"
          % (time.time() - t0, len(ep_l), len(ep_s), n_locked, n_eod), flush=True)

    # ---- report ---------------------------------------------------------------------------------
    span_days = (et[-1] - et[0]) / 86400.0
    ev = n - FIRST
    att = {}
    for side in ("long", "short"):
        c1 = df["leg1_" + side].to_numpy()
        c2 = c1 & df["leg2_" + side].to_numpy()
        c3 = c2 & df["leg3_" + side].to_numpy()
        c4 = c3 & df["leg4_" + side].to_numpy()
        att[side] = dict(solo=[int(df["leg%d_%s" % (k, side)].sum()) for k in (1, 2, 3, 4)],
                         chain=[int(x.sum()) for x in (c1, c2, c3, c4)])

    def fmt_d(d):
        return "| %.3f | %.3f | %.3f | %.3f | %.3f | %.3f |" % (
            d["mean"], d["med"], d["p25"], d["p75"], d["p90"], d["mx"])

    def side_block(side, ep_side):
        st = S5.ep_stats(ep_side)
        nn = st["n"]
        lines = ["### %s — %d episodes (%.2f fires/day)" % (side.upper(), nn, nn / span_days), ""]
        if nn < S5.UNDERPOWERED_N:
            lines += ["**UNDERPOWERED (n = %d < %d): counts only, per protocol. Episodes in the "
                      "CSV; verdict deferred to forward tape.**" % (nn, S5.UNDERPOWERED_N), ""]
            return lines
        tu = np.array([e["t_up"] for e in ep_side]); td = np.array([e["t_dn"] for e in ep_side])
        lines += ["| metric | mean | median | p25 | p75 | p90 | max |", "|---|---|---|---|---|---|---|",
                  "| MFE % " + fmt_d(st["mfe"]), "| \\|MAE\\| % " + fmt_d(st["amae"]), "",
                  "Time-to-max: up median %.1f min / down median %.1f min. Ratio view: median MFE "
                  "**%.3f%%** vs median \\|MAE\\| **%.3f%%**; MFE > \\|MAE\\| in **%.1f%%** of episodes; "
                  "end-of-window mean %+.3f%% / median %+.3f%%."
                  % (float(np.median(tu)), float(np.median(td)), st["mfe"]["med"], st["amae"]["med"],
                     st["pct_win"], st["end_mean"], st["end_med"]), ""]
        c = ctrl[side]
        lines += ["Control (null: %d random non-overlapping 30-min windows, %d seeded draws, seed %d):"
                  % (st["n"], N_DRAWS, SEED),
                  "", "| stat | actual | control (mean ± sd) |", "|---|---|---|",
                  "| median MFE %% | %.3f | %.3f ± %.3f |" % (st["mfe"]["med"], c["med_mfe"][0], c["med_mfe"][1]),
                  "| median \\|MAE\\| %% | %.3f | %.3f ± %.3f |" % (st["amae"]["med"], c["med_amae"][0], c["med_amae"][1]),
                  "| %% MFE > \\|MAE\\| | %.1f | %.1f ± %.1f |" % (st["pct_win"], c["pct_win"][0], c["pct_win"][1]),
                  "| mean end %% | %+.3f | %+.3f ± %.3f |" % (st["end_mean"], c["mean_end"][0], c["mean_end"][1]),
                  "| median end %% | %+.3f | %+.3f ± %.3f |" % (st["end_med"], c["med_end"][0], c["med_end"][1]), ""]
        pre = [e for e in ep_side if e["ts"] < REGIME_CUT]; post = [e for e in ep_side if e["ts"] >= REGIME_CUT]
        lines += ["Regime split (cut 2026-06-30 00:00 UTC):", "",
                  "| regime | n | med MFE | med \\|MAE\\| | % MFE>\\|MAE\\| | med end |", "|---|---|---|---|---|---|"]
        for nm, part in (("pre (chop)", pre), ("post (bull)", post)):
            s = S5.ep_stats(part)
            lines.append("| %s | %d | %.3f | %.3f | %s | %s |" % (
                nm, s["n"], s["mfe"]["med"], s["amae"]["med"],
                "%.1f" % s["pct_win"] if s["n"] else "-", "%+.3f" % s["end_med"] if s["n"] else "-"))
        lines.append("")
        return lines

    md = ["# S5b — Corrected Confluence Study + M10 Sweep (1m, merged span)", "",
          "_**Pre-registered CORRECTION of S5's leg 1 (mistranslation from the operator's screenshot,"
          " bucket idx 20977) — not a tweak: multiplicity +2 (long, short) -> program counter 454.**"
          " Legs 2-4 unchanged (65% share / START-DURING dominant / +15pp spread, frozen). The sweep"
          " table is INSTRUMENTATION — any rule found by browsing it must be pre-registered and"
          " judged on forward tape before it counts. Excursions are an information measure — no fees,"
          " no execution model. P8 is EXCLUDED from the sweep (size_thr never persisted)._", "",
          "## Data",
          "Merged %d bars (frozen %s + fresh pull %s, dedup by bucket_id removed %d overlap rows), "
          "continuous bids %d..%d, span %s -> %s UTC (%.2f days), gaps: %s. Evaluable rows %d "
          "(first %d excluded for lookback)."
          % (n, "9686..19685", "12261..22260", dup, bids[0], bids[-1],
             time.strftime("%m-%d %H:%M", time.gmtime(et[0])),
             time.strftime("%m-%d %H:%M", time.gmtime(et[-1])), span_days, gaps or "none", ev, FIRST), "",
          "## Step 0 — anchor parity (gate PASSED)",
          "Anchor = bucket idx %d, end 2026-07-03 03:20:49 UTC (= 04:20:49 operator local, UTC+1); "
          "identity verified by idx + exact OHLC match (O 80.79 H 80.80 L 80.77 C 80.79)." % ANCHOR_BID, "",
          "| quantity | computed | screenshot |", "|---|---|---|",
          "| eff-agg badge (leg2 path) | %.1f%% | 81%% |" % (g["P2.01"]),
          "| absorption badge P1.01 | %.1f%% | 26%% (2nd badge) |" % g["P1.01"],
          "| P0 smoothed sum @lock | %+.1f | +55.6 |" % g["p0_sum"],
          "| phase UP row | %.0f / %.0f / %.0f | 13 / 52 / 35 |" % (g["p5_up"], g["p6_up"], g["p7_up"]),
          "| confirmed markers | %s@%s, %s@%s | -50/0/+50 all green |"
          % (g["cross1_dir"], g["cross1_level"], g["cross2_dir"], g["cross2_level"]),
          "| fire_long | %s | TRUE |" % g["fire_long"], "",
          "**Leg 1' semantics (the correction):** Panel-0's confirmed cross MARKERS — one most-recent "
          "confirmed cross per level {+50, 0, -50}, ZERO LINE INCLUDED, detected inside the locked "
          "slice of the 16-bar selection exactly as `_draw_level_crosses` draws its X's (settling/"
          "forming dots excluded; confirmed = new side holds >= 2 buckets within the locked slice). "
          ">= 2 markers required; the two most recent both up -> LONG, both down -> SHORT. S5's leg "
          "used S3's A2 instead (±50 only, 0-line excluded, both levels required) — that mistranslation "
          "made the leg nearly unfireable.", "",
          "## Leg attrition (all %d evaluable bars)" % ev, "",
          "| side | leg | standalone | cumulative (1..k) |", "|---|---|---|---|"]
    leg_names = ("1' two most-recent markers on-side", "2 eff-agg >= 65%",
                 "3 phase dominant START/DURING", "4 P6 spread >= 15pp")
    for side in ("long", "short"):
        for k in range(4):
            md.append("| %s | %s | %d (%.2f%%) | %d (%.3f%%) |" % (
                side, leg_names[k], att[side]["solo"][k], 100.0 * att[side]["solo"][k] / ev,
                att[side]["chain"][k], 100.0 * att[side]["chain"][k] / ev))
    md += ["", "## Episodes (non-overlapping 30-min windows; %d skipped inside open windows, "
           "%d end-of-data excluded)" % (n_locked, n_eod), ""]
    for side, ep_side in (("long", ep_l), ("short", ep_s)):
        md += side_block(side, ep_side)
    md += ["## Honest flags",
           "- The merged span is %.2f days; the pre-Jun-30 regime is only ~1.5 days of it." % span_days,
           "- 1m is spent for MINING; this study evaluates one pre-registered corrected rule. The "
           "sweep table exists for instrumentation, and anything derived from browsing it needs "
           "pre-registration + forward judgment.",
           "- Per-bar fwd30 columns in the sweep OVERLAP — never use them as independent samples; "
           "the episode CSV is the non-overlapping view.",
           "", "## HARD STOP", "No variants; judged once on this tape; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5b.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%4.0fs] table (%d rows x %d cols) + episodes + report written"
          % (time.time() - t0, len(df), df.shape[1]), flush=True)


if __name__ == "__main__":
    main()
