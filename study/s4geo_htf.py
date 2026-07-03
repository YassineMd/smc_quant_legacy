"""S4-GEO STAGE 2 — THE EXAM: 5m/15m/1h/4h tables (never analyzed; all prior work was 1m only).

Data: buckets with start_time >= 2026-06-21 06:00 UTC, from the same snapshot. FRESH data — Track B
holdout PASS means PASS. Pipeline imported UNCHANGED from stage 1 (assign_groups / cell_membership /
boot_ci); M/P from the shared app.bar_quantiles module; strict ties; next-bar doji excluded.

TRACK A — pre-registered (declared before this data was read), judged on the FULL per-tf dataset, no split:
  H1: L3 O>P>M>C -> next bar DOWN     H2: L2 P-highest+C-lowest -> next bar DOWN
  PASS = lift<0 AND 90% day-block CI clear of 0 AND n>=100 (>=50 on 4h); PARTIAL = negative but CI spans
  0 or under the n bar; FAIL = flip/~0 (lift >= 0).
TRACK B — full 50-cell screen per tf, 70% discovery / 30% SEALED holdout, 1-bucket embargo. Survivor:
  disc n>=100 (>=50 on 4h), |lift|>=5pp, CI clear of 0. Holdout judged ONCE: same sign AND >=50% effect
  AND n>=30 -> PASS. Multiplicity: 50 cells x 4 tfs = 200.
Step 0 characterization (counts, span, ladder coverage, tie rates) reported BEFORE outcomes are read.
"""
import os, sys, csv, json, sqlite3, calendar, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict                      # noqa: E402
from app import bar_quantiles as BQ                                # noqa: E402
from s4geo_1m import assign_groups, cell_membership, boot_ci, KEYS  # noqa: E402  (stage-1 pipeline, unchanged)

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
DB = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
CUTOFF = calendar.timegm((2026, 6, 21, 6, 0, 0))          # 2026-06-21 06:00 UTC
TFS = ("5m", "15m", "1h", "4h")
THIN_TF = 400
L4_PAIRS = (("O", "C"), ("O", "M"), ("O", "P"), ("C", "M"), ("C", "P"), ("M", "P"))


def n_min(tf):
    return 50 if tf == "4h" else 100


def load_tf(tf):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    raw = [json.loads(x[0]) for x in con.execute(
        "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,))]
    tc = int(con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_" + tf,)).fetchone()[0])
    con.close()
    base_id = tc - len(raw)
    first = next((i for i, d in enumerate(raw) if float(d["start_time"]) >= CUTOFF), len(raw))
    bars, n_no_ladder, gaps = [], 0, 0
    prev_end = None
    for j, d in enumerate(raw[first:]):
        b = _bucket_from_dict(d)
        if prev_end is not None and float(d["start_time"]) - prev_end > 2.0:
            gaps += 1
        prev_end = float(b.end_time)
        _, med, _ = BQ.vq(b.levels or {})
        p = BQ.poc(b.levels or {})
        if med != med or p != p:
            n_no_ladder += 1
            bars.append(None)
            continue
        bars.append(dict(bid=base_id + first + j + 1, ts=float(b.end_time), st=float(d["start_time"]),
                         O=float(b.open_price), C=float(b.close_price), M=float(med), P=float(p)))
    return bars, dict(post_cut=len(bars), no_ladder=n_no_ladder, gaps=gaps,
                      span=((raw[first]["start_time"], raw[-1]["end_time"]) if first < len(raw) else (0, 0)))


def step0(all_bars):
    """Characterization only — no outcomes read."""
    lines = []
    for tf in TFS:
        bars, st = all_bars[tf]
        usable = [b for b in bars if b is not None]
        cov = 100.0 * len(usable) / max(1, st["post_cut"])
        g = [assign_groups(b) for b in usable]
        n = max(1, len(g))
        t1h = 100.0 * sum(1 for x in g if x["L1_high"] is None) / n
        t1l = 100.0 * sum(1 for x in g if x["L1_low"] is None) / n
        t2 = 100.0 * sum(1 for x in g if x["L2"] is None) / n
        t3 = 100.0 * sum(1 for x in g if x["L3"] is None) / n
        mp = 100.0 * sum(1 for x in g if x["L4_MP"] is None) / n
        d0 = time.strftime("%m-%d %H:%M", time.gmtime(st["span"][0]))
        d1 = time.strftime("%m-%d %H:%M", time.gmtime(st["span"][1]))
        thin = "THIN" if len(usable) < THIN_TF else ""
        lines.append((tf, st["post_cut"], "%s -> %s" % (d0, d1), round(cov, 2), st["gaps"],
                      round(t1h, 2), round(t1l, 2), round(t2, 2), round(t3, 2), round(mp, 2), thin))
    return lines


def build_rows(bars):
    rows, n_doji = [], 0
    for i in range(len(bars) - 1):
        b, nx = bars[i], bars[i + 1]
        if b is None or nx is None:
            continue
        if nx["O"] == nx["C"]:
            n_doji += 1
            continue
        rows.append((b, nx["C"] > nx["O"]))
    groups = [assign_groups(b) for b, _ in rows]
    up = np.array([u for _, u in rows])
    ts = np.array([b["ts"] for b, _ in rows])
    return rows, groups, up, ts, (ts // 86400).astype(int), n_doji


def track_a(tf, cells, up, days):
    """Full fresh dataset, no split. Returns verdict rows for H1/H2."""
    out = []
    for hyp, cell in (("H1", "L3:O>P>M>C"), ("H2", "L2:P>C")):
        inc = cells[cell]
        n = int(inc.sum())
        base = 100.0 * up.mean()
        pu = 100.0 * up[inc].mean() if n else float("nan")
        lift = pu - base if n else float("nan")
        lo, hi, nb = boot_ci(up, inc, days)
        if n == 0 or lift != lift or lift >= 0:
            verdict = "FAIL"
        elif hi == hi and hi < 0 and n >= n_min(tf):
            verdict = "PASS"
        else:
            verdict = "PARTIAL"
        out.append(dict(tf=tf, hyp=hyp, cell=cell, n=n, base=round(base, 2),
                        pup=round(pu, 2) if pu == pu else "", lift=round(lift, 2) if lift == lift else "",
                        ci_lo=round(lo, 2) if lo == lo else "", ci_hi=round(hi, 2) if hi == hi else "",
                        blocks=nb, verdict=verdict))
    return out


def track_b(tf, cells, up, ts, days):
    tcut = ts.min() + 0.70 * (ts.max() - ts.min())
    disc = ts <= tcut
    cut_i = int(np.searchsorted(ts, tcut, side="right"))
    hold = np.zeros(len(ts), bool); hold[cut_i + 1:] = True         # 1-bucket embargo, holdout SEALED
    out = []
    for name, inc in cells.items():
        rec = {"tf": tf, "cell": name}
        for tag, sl in (("disc", disc), ("hold", hold)):
            m = inc & sl
            n = int(m.sum())
            base = 100.0 * up[sl].mean()
            pu = 100.0 * up[m].mean() if n else float("nan")
            lo, hi, nb = boot_ci(up[sl], inc[sl], days[sl]) if n else (float("nan"), float("nan"), 0)
            rec.update({tag + "_n": n, tag + "_pup": round(pu, 2) if pu == pu else "",
                        tag + "_base": round(base, 2), tag + "_lift": round(pu - base, 2) if pu == pu else "",
                        tag + "_ci_lo": round(lo, 2) if lo == lo else "",
                        tag + "_ci_hi": round(hi, 2) if hi == hi else "", tag + "_blocks": nb})
        dl, lo, hi = rec["disc_lift"], rec["disc_ci_lo"], rec["disc_ci_hi"]
        surv = (rec["disc_n"] >= n_min(tf) and dl != "" and abs(dl) >= 5.0
                and lo != "" and (lo > 0 or hi < 0))
        ok = False
        if surv and rec["hold_n"] >= 30 and rec["hold_lift"] != "":
            ok = ((dl > 0) == (rec["hold_lift"] > 0)) and abs(rec["hold_lift"]) >= 0.5 * abs(dl)
        rec["survivor"] = surv
        rec["holdout_pass"] = ok
        out.append(rec)
    return out, int(disc.sum()), int(hold.sum())


def write_csvs(tf, rows, groups, brecs):
    with open(os.path.join(OUT, "s4geo_cells_%s.csv" % tf), "w", newline="", encoding="utf-8") as f:
        f.write("# S4-GEO stage 2 (%s). FRESH data exam: holdout PASS means PASS. Outcome = next-bar "
                "direction (information measure, no fees).\n" % tf)
        w = csv.DictWriter(f, fieldnames=list(brecs[0].keys())); w.writeheader()
        for r in brecs:
            w.writerow(r)
    with open(os.path.join(OUT, "s4geo_assignments_%s.csv" % tf), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "O", "C", "M", "P", "L1_high", "L1_low", "L2", "L3",
                    "L4_OC", "L4_OM", "L4_OP", "L4_CM", "L4_CP", "L4_MP"])
        for (b, _), g in zip(rows, groups):
            w.writerow([round(b["ts"], 3), b["bid"], b["O"], b["C"], b["M"], b["P"],
                        g["L1_high"] or "TIE", g["L1_low"] or "TIE", g["L2"] or "TIE", g["L3"] or "TIE"]
                       + [("T" if g["L4_%s%s" % (a, c)] is True else
                           "F" if g["L4_%s%s" % (a, c)] is False else "TIE") for a, c in L4_PAIRS])


def md_table(brecs, prefix, thin_l3=False):
    lines = ["| cell | disc n | P(up) | lift | 90% CI | hold n | lift | verdict |",
             "|---|---|---|---|---|---|---|---|"]
    for r in brecs:
        if not r["cell"].startswith(prefix):
            continue
        flags = []
        if r["survivor"]:
            flags.append("SURV")
            flags.append("**PASS**" if r["holdout_pass"] else "holdout-FAIL")
        if thin_l3 and (r["disc_n"] < 100 or r["hold_n"] < 30):
            flags.append("thin")
        lines.append("| %s | %d | %s | %s | [%s,%s] | %d | %s | %s |" % (
            r["cell"], r["disc_n"], r["disc_pup"], r["disc_lift"], r["disc_ci_lo"], r["disc_ci_hi"],
            r["hold_n"], r["hold_lift"], " ".join(flags)))
    return "\n".join(lines)


def main():
    t0 = time.time()
    all_bars = {tf: load_tf(tf) for tf in TFS}
    s0 = step0(all_bars)
    print("STEP 0 — characterization (before outcomes are read)")
    print("tf | post-cut buckets | span (UTC) | ladder cov % | gaps>2s | tie% L1hi/L1lo/L2/L3 | M==P% | flag")
    for r in s0:
        print("%-3s| %5d | %s | %6.2f | %3d | %5.2f/%5.2f/%5.2f/%5.2f | %5.2f | %s" %
              (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]))
    print("[%.0fs] step 0 done\n" % (time.time() - t0), flush=True)
    if "--step0" in sys.argv:                      # characterization only — outcomes never read
        sys.exit(0)

    a_rows, b_all, tf_meta = [], {}, {}
    for tf in TFS:
        bars, _st = all_bars[tf]
        rows, groups, up, ts, days, n_doji = build_rows(bars)
        cells = cell_membership(None, groups, up)
        a_rows += track_a(tf, cells, up, days)
        brecs, dn, hn = track_b(tf, cells, up, ts, days)
        b_all[tf] = brecs
        tf_meta[tf] = dict(rows=len(rows), doji=n_doji, disc_n=dn, hold_n=hn,
                           base=100.0 * up.mean(),
                           disc_base=100.0 * up[ts <= ts.min() + 0.7 * (ts.max() - ts.min())].mean(),
                           days=len(np.unique(days)),
                           usable=sum(1 for b in bars if b is not None))
        write_csvs(tf, rows, groups, brecs)
        print("[%.0fs] %s done: rows=%d doji=%d disc/hold %d/%d" %
              (time.time() - t0, tf, len(rows), n_doji, dn, hn), flush=True)
    return s0, a_rows, b_all, tf_meta


if __name__ == "__main__":
    s0, a_rows, b_all, tf_meta = main()
    print("\nTRACK A — pre-registered verdicts (full fresh data, no split)")
    for r in a_rows:
        print("  %-3s %s %-12s n=%-5d lift %+7.2f CI[%s,%s] -> %s" %
              (r["tf"], r["hyp"], r["cell"], r["n"], r["lift"] if r["lift"] != "" else float("nan"),
               r["ci_lo"], r["ci_hi"], r["verdict"]))
    print("\nTRACK B — survivors & sealed-holdout verdicts (multiplicity 50x4=200)")
    for tf in TFS:
        for r in b_all[tf]:
            if r["survivor"]:
                print("  %-3s %-22s disc n=%-5d lift %+7.2f CI[%s,%s] | hold n=%-4d lift %s -> %s" %
                      (tf, r["cell"], r["disc_n"], r["disc_lift"], r["disc_ci_lo"], r["disc_ci_hi"],
                       r["hold_n"], r["hold_lift"], "PASS" if r["holdout_pass"] else "holdout-FAIL"))
    npass = sum(1 for tf in TFS for r in b_all[tf] if r["holdout_pass"])
    print("holdout PASSes: %d / 200 cells screened" % npass)
    # stash everything the report generator needs
    with open(os.path.join(OUT, "_s4geo_htf_state.json"), "w", encoding="utf-8") as f:
        json.dump({"s0": s0, "a": a_rows, "meta": tf_meta}, f)
