"""S4-GEO STAGE 3 — EQUALITY EXTENSION, all timeframes (1m/5m/15m/1h/4h).

Same pipeline (shared bar_quantiles M/P, next-bar direction, next-bar doji excluded from OUTCOME,
cutoff start_time >= 2026-06-21 06:00 UTC where data extends earlier). NEW: equality is a first-class
state, TICK-EXACT — prices compared after rounding to the instrument tick, derived from data ($0.01:
every O/C/H/L and ladder price across all tfs is cent-aligned; gcd of cents = 1).

FRAMING (per tf, restated in the report): tie-bar cells (T1 "=", T2, T4) = FIRST ANALYSIS of
previously-excluded bars; extended non-tie cells (T1 >/<, T3) = re-cuts of mined data
(characterization). 1m is SPENT throughout. Multiplicity: 40 cells x 5 tfs = 200 this stage;
S4-GEO running total 50 + 200 + 200 = 450.

THE 40 CELLS: T1 (18) three-state pairs {>,<,=} for O-C,O-M,O-P,C-M,C-P,M-P; T2 (8) multi-equalities;
T3 (8) X highest-or-tied / lowest-or-tied; T4 (6) M=P-collapsed strict orderings on the M=P universe.
Survivor rule: disc n >= {1m:100, 5m:100, 15m:50, 1h:25, 4h:14}, |lift| >= 5pp, 90% day-block CI clear
of 0. Sealed holdout judged ONCE: same sign, >= 50% effect, n >= 15 -> PASS. 4h THIN throughout.
"""
import os, sys, csv, json, sqlite3, calendar, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict      # noqa: E402
from app import bar_quantiles as BQ                # noqa: E402
from s4geo_1m import boot_ci                       # noqa: E402  (stage-1 bootstrap, unchanged)

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
DB = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
CUTOFF = calendar.timegm((2026, 6, 21, 6, 0, 0))
TFS = ("1m", "5m", "15m", "1h", "4h")
MIN_N = {"1m": 100, "5m": 100, "15m": 50, "1h": 25, "4h": 14}
TICK = 0.01
PAIRS = (("O", "C"), ("O", "M"), ("O", "P"), ("C", "M"), ("C", "P"), ("M", "P"))
T2_DEFS = (("O=C=M", ("OC", "OM")), ("O=C=P", ("OC", "OP")), ("O=M=P", ("OM", "OP")),
           ("C=M=P", ("CM", "CP")), ("O=C&M=P", ("OC", "MP")), ("O=M&C=P", ("OM", "CP")),
           ("O=P&C=M", ("OP", "CM")), ("O=C=M=P", ("OC", "OM", "OP")))
T4_ORDERS = ("C>MP>O", "C>O>MP", "MP>C>O", "MP>O>C", "O>MP>C", "O>C>MP")


def cents(x):
    return int(round(x / TICK))


def load_tf(tf):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    raw = [json.loads(x[0]) for x in con.execute(
        "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,))]
    tc = int(con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_" + tf,)).fetchone()[0])
    con.close()
    base_id = tc - len(raw)
    first = next((i for i, d in enumerate(raw) if float(d["start_time"]) >= CUTOFF), len(raw))
    bars, n_no_ladder = [], 0
    for j, d in enumerate(raw[first:]):
        b = _bucket_from_dict(d)
        _, med, _ = BQ.vq(b.levels or {})
        p = BQ.poc(b.levels or {})
        if med != med or p != p:
            n_no_ladder += 1
            bars.append(None)
            continue
        bars.append(dict(bid=base_id + first + j + 1, ts=float(b.end_time),
                         O=float(b.open_price), C=float(b.close_price), M=float(med), P=float(p)))
    span = (float(raw[first]["start_time"]), float(raw[-1]["end_time"])) if first < len(raw) else (0, 0)
    return bars, n_no_ladder, span


def assign(b):
    """Tick-exact three-state pair map + T2/T3/T4 memberships for one bar."""
    r = {k: cents(b[k]) for k in ("O", "C", "M", "P")}
    s = {}
    for a, c in PAIRS:
        s[a + c] = ">" if r[a] > r[c] else "<" if r[a] < r[c] else "="
    t2 = [name for name, req in T2_DEFS if all(s[p] == "=" for p in req)]
    mx, mn = max(r.values()), min(r.values())
    t3hi = [k for k in ("O", "C", "M", "P") if r[k] == mx]
    t3lo = [k for k in ("O", "C", "M", "P") if r[k] == mn]
    t4 = None
    if s["MP"] == "=":
        v = {"O": r["O"], "C": r["C"], "MP": r["M"]}
        for order in T4_ORDERS:
            a, m, c = order.split(">")
            if v[a] > v[m] > v[c]:
                t4 = order
                break
    return s, t2, t3hi, t3lo, t4


def cell_arrays(assigns):
    cells, cls = {}, {}
    for a, c in PAIRS:
        for st, tag in ((">", ">"), ("<", "<"), ("=", "=")):
            nm = "T1:%s%s%s" % (a, tag, c)
            cells[nm] = np.array([g[0][a + c] == st for g in assigns])
            cls[nm] = "EQ" if st == "=" else "RECUT"
    for name, _req in T2_DEFS:
        nm = "T2:" + name
        cells[nm] = np.array([name in g[1] for g in assigns])
        cls[nm] = "EQ"
    for x in ("O", "C", "M", "P"):
        nm = "T3:%s>=rest" % x
        cells[nm] = np.array([x in g[2] for g in assigns]); cls[nm] = "RECUT"
        nm = "T3:%s<=rest" % x
        cells[nm] = np.array([x in g[3] for g in assigns]); cls[nm] = "RECUT"
    for order in T4_ORDERS:
        nm = "T4:" + order
        cells[nm] = np.array([g[4] == order for g in assigns])
        cls[nm] = "EQ"
    assert len(cells) == 40, len(cells)
    return cells, cls


def run_tf(tf):
    bars, n_no_ladder, span = load_tf(tf)
    rows, n_doji = [], 0
    for i in range(len(bars) - 1):
        b, nx = bars[i], bars[i + 1]
        if b is None or nx is None:
            continue
        if cents(nx["O"]) == cents(nx["C"]):          # tick-exact next-bar doji -> outcome excluded
            n_doji += 1
            continue
        rows.append((b, cents(nx["C"]) > cents(nx["O"])))
    assigns = [assign(b) for b, _ in rows]
    up = np.array([u for _, u in rows])
    ts = np.array([b["ts"] for b, _ in rows])
    days = (ts // 86400).astype(int)
    recovered = sum(1 for g in assigns if "=" in g[0].values())
    cells, cls = cell_arrays(assigns)

    tcut = ts.min() + 0.70 * (ts.max() - ts.min())
    disc = ts <= tcut
    cut_i = int(np.searchsorted(ts, tcut, side="right"))
    hold = np.zeros(len(ts), bool); hold[cut_i + 1:] = True      # 1-bucket embargo, holdout sealed

    recs = []
    for name, inc in cells.items():
        rec = {"cell": name, "class": cls[name], "full_n": int(inc.sum())}
        fb = 100.0 * up.mean()
        fpu = 100.0 * up[inc].mean() if rec["full_n"] else float("nan")
        rec["full_pup"] = round(fpu, 2) if fpu == fpu else ""
        rec["full_lift"] = round(fpu - fb, 2) if fpu == fpu else ""
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
        surv = (rec["disc_n"] >= MIN_N[tf] and dl != "" and abs(dl) >= 5.0
                and lo != "" and (lo > 0 or hi < 0))
        ok = False
        if surv and rec["hold_n"] >= 15 and rec["hold_lift"] != "":
            ok = ((dl > 0) == (rec["hold_lift"] > 0)) and abs(rec["hold_lift"]) >= 0.5 * abs(dl)
        rec["survivor"] = surv
        rec["holdout_pass"] = ok
        recs.append(rec)

    with open(os.path.join(OUT, "s4geo_eq_cells_%s.csv" % tf), "w", newline="", encoding="utf-8") as f:
        f.write("# S4-GEO stage 3 (%s), tick=%.2f. EQ cells = first analysis of previously-excluded "
                "bars; RECUT cells = re-cuts of mined data. 1m spent throughout.\n" % (tf, TICK))
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys())); w.writeheader()
        for r in recs:
            w.writerow(r)
    with open(os.path.join(OUT, "s4geo_eq_assign_%s.csv" % tf), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "O", "C", "M", "P",
                    "OC", "OM", "OP", "CM", "CP", "MP", "T2", "T3_hi", "T3_lo", "T4"])
        for (b, _), (s, t2, t3hi, t3lo, t4) in zip(rows, assigns):
            w.writerow([round(b["ts"], 3), b["bid"], b["O"], b["C"], b["M"], b["P"]]
                       + [s[a + c] for a, c in PAIRS]
                       + [";".join(t2) or "-", "".join(t3hi), "".join(t3lo), t4 or "-"])

    meta = dict(rows=len(rows), doji=n_doji, no_ladder=n_no_ladder, recovered=recovered,
                span=span, base=round(100.0 * up.mean(), 2),
                disc_base=round(100.0 * up[disc].mean(), 2), hold_base=round(100.0 * up[hold].mean(), 2),
                disc_n=int(disc.sum()), hold_n=int(hold.sum()), days=len(np.unique(days)),
                min_n=MIN_N[tf], thin=tf == "4h", spent=tf == "1m")
    return recs, meta


if __name__ == "__main__":
    t0 = time.time()
    all_recs, all_meta = {}, {}
    for tf in TFS:
        all_recs[tf], all_meta[tf] = run_tf(tf)
        m = all_meta[tf]
        print("[%3.0fs] %-3s rows=%-5d doji=%-3d recovered=%-5d (%.1f%%) base=%.2f%% disc/hold %d/%d"
              % (time.time() - t0, tf, m["rows"], m["doji"], m["recovered"],
                 100.0 * m["recovered"] / m["rows"], m["base"], m["disc_n"], m["hold_n"]), flush=True)
    print("\nSURVIVORS & sealed-holdout verdicts (min-n per tf: %s)" % MIN_N)
    for tf in TFS:
        for r in all_recs[tf]:
            if r["survivor"]:
                print("  %-3s %-12s [%s] disc n=%-5d lift %+7.2f CI[%s,%s] | hold n=%-4d lift %s -> %s"
                      % (tf, r["cell"], r["class"], r["disc_n"], r["disc_lift"], r["disc_ci_lo"],
                         r["disc_ci_hi"], r["hold_n"], r["hold_lift"],
                         "PASS" if r["holdout_pass"] else "holdout-FAIL"))
    npass = sum(1 for tf in TFS for r in all_recs[tf] if r["holdout_pass"])
    print("holdout PASSes: %d | this stage 40x5=200 screened | S4-GEO running total 450 cells" % npass)
    with open(os.path.join(OUT, "_s4geo_eq_meta.json"), "w", encoding="utf-8") as f:
        json.dump(all_meta, f)
