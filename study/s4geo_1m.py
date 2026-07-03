"""S4-GEO STAGE 1 — bar-geometry groups (O, C, M, P orderings) vs NEXT-BAR direction, 1m ONLY.

FRAMING (mandated): the 1m dataset is SPENT (~35+ cumulative trials) — this stage is CHARACTERIZATION /
hypothesis generation. The SAME pipeline runs later, unchanged, on the never-analyzed 1h/4h tables —
THAT is the real exam. Outcome is an INFORMATION measure (next-bar direction), not profitability: no
barriers, no fees.

M = volume-weighted median, P = POC — both from the SHARED app.bar_quantiles module (the exact 'W'-mode
implementation). Strict > comparisons; exact ties among the relevant values exclude the bar from that
level (fractions reported). 50 cells total: L1 8 + L2 12 + L3 24 + L4 6.
"""
import os, sys, csv, json, sqlite3, time
from collections import Counter, defaultdict
from itertools import permutations
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict     # noqa: E402
from app import bar_quantiles as BQ               # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
KEYS = ("O", "C", "M", "P")
R_BOOT = 1000
SURV_N, SURV_LIFT, CAND_N = 100, 5.0, 30


def build_bars():
    db = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    bks = [_bucket_from_dict(d) for d in raw]
    base_id = int(tc[0]) - len(bks)
    bars = []
    n_no_ladder = 0
    for i, b in enumerate(bks):
        _, med, _ = BQ.vq(b.levels or {})
        p = BQ.poc(b.levels or {})
        if med != med or p != p:
            n_no_ladder += 1
            bars.append(None)
            continue
        bars.append(dict(i=i, bid=base_id + i + 1, ts=float(b.end_time),
                         O=float(b.open_price), C=float(b.close_price), M=float(med), P=float(p)))
    return bars, n_no_ladder


def assign_groups(v):
    """Group memberships for one bar dict with O/C/M/P. Strict >; ties exclude per level."""
    vals = [(k, v[k]) for k in KEYS]
    out = {}
    # L1: unique argmax / argmin
    mx = max(x for _, x in vals); mn = min(x for _, x in vals)
    hi_ties = [k for k, x in vals if x == mx]; lo_ties = [k for k, x in vals if x == mn]
    out["L1_high"] = ("high_" + hi_ties[0]) if len(hi_ties) == 1 else None
    out["L1_low"] = ("low_" + lo_ties[0]) if len(lo_ties) == 1 else None
    # L2: (highest, lowest) pair — both unique
    out["L2"] = (hi_ties[0] + ">" + lo_ties[0]) if (len(hi_ties) == 1 and len(lo_ties) == 1) else None
    # L3: full strict ordering — no ties anywhere
    if len({x for _, x in vals}) == 4:
        order = [k for k, _ in sorted(vals, key=lambda kv: -kv[1])]
        out["L3"] = ">".join(order)
    else:
        out["L3"] = None
    # L4: elementary pairs (cell true when LEFT > RIGHT; tie -> None for that pair)
    for a, b in (("O", "C"), ("O", "M"), ("O", "P"), ("C", "M"), ("C", "P"), ("M", "P")):
        out["L4_%s%s" % (a, b)] = (True if v[a] > v[b] else False) if v[a] != v[b] else None
    return out


def cell_membership(bars_idx, groups, outcomes):
    """cells: {cell_name: bool array over usable rows}. 50 cells exactly."""
    cells = {}
    for x in KEYS:
        cells["L1:high_%s" % x] = np.array([g["L1_high"] == "high_" + x for g in groups])
        cells["L1:low_%s" % x] = np.array([g["L1_low"] == "low_" + x for g in groups])
    for a in KEYS:
        for b in KEYS:
            if a != b:
                cells["L2:%s>%s" % (a, b)] = np.array([g["L2"] == a + ">" + b for g in groups])
    for perm in permutations(KEYS):
        nm = ">".join(perm)
        cells["L3:%s" % nm] = np.array([g["L3"] == nm for g in groups])
    for a, b in (("O", "C"), ("O", "M"), ("O", "P"), ("C", "M"), ("C", "P"), ("M", "P")):
        cells["L4:%s>%s" % (a, b)] = np.array([g["L4_%s%s" % (a, b)] is True for g in groups])
    assert len(cells) == 50, len(cells)
    return cells


def boot_ci(up, inc, days, R=R_BOOT, seed=13):
    """90% CI on (cell up-share − slice baseline up-share) via DAY-block bootstrap."""
    rng = np.random.default_rng(seed)
    udays = np.unique(days)
    if len(udays) < 2 or inc.sum() < 5:
        return np.nan, np.nan, len(udays)
    lifts = []
    for _ in range(R):
        pick = rng.choice(udays, len(udays), replace=True)
        m = np.zeros(len(up), bool); cnt = Counter(pick)
        # weighted resample: replicate day masks by multiplicity
        tot_up = tot_n = cell_up = cell_n = 0.0
        for d, k in cnt.items():
            dm = days == d
            tot_up += k * up[dm].sum(); tot_n += k * dm.sum()
            im = dm & inc
            cell_up += k * up[im].sum(); cell_n += k * im.sum()
        if cell_n < 5 or tot_n == 0:
            continue
        lifts.append(100.0 * (cell_up / cell_n - tot_up / tot_n))
    if len(lifts) < 50:
        return np.nan, np.nan, len(udays)
    return float(np.percentile(lifts, 5)), float(np.percentile(lifts, 95)), len(udays)


def main():
    t0 = time.time()
    bars, n_no_ladder = build_bars()
    n_all = len(bars)
    # next-bar outcome
    rows = []
    n_doji = 0
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
    days = (ts // 86400).astype(int)
    # tie fractions per level
    ties = {lvl: 100.0 * sum(1 for g in groups if g[key] is None) / len(groups)
            for lvl, key in (("L1_high", "L1_high"), ("L1_low", "L1_low"), ("L2", "L2"), ("L3", "L3"))}
    tie_l4 = {k: 100.0 * sum(1 for g in groups if g["L4_%s" % k] is None) / len(groups)
              for k in ("OC", "OM", "OP", "CM", "CP", "MP")}
    cells = cell_membership(None, groups, up)
    # split 70/30 with 1-bucket embargo
    tmin, tmax = ts.min(), ts.max()
    tcut = tmin + 0.70 * (tmax - tmin)
    disc = ts <= tcut
    cut_i = int(np.searchsorted(ts, tcut, side="right"))
    comp = np.zeros(len(ts), bool); comp[cut_i + 1:] = True          # 1-bucket embargo
    print("[%.0fs] bars=%d usable rows=%d (no-ladder %d, next-doji %d) | disc %d / comp %d"
          % (time.time() - t0, n_all, len(rows), n_no_ladder, n_doji, disc.sum(), comp.sum()), flush=True)

    out_rows = []
    for name, inc in cells.items():
        rec = {"cell": name}
        for tag, sl in (("disc", disc), ("comp", comp)):
            m = inc & sl
            n = int(m.sum())
            base = 100.0 * up[sl].mean()
            pu = 100.0 * up[m].mean() if n else np.nan
            lo, hi, nb = boot_ci(up[sl], inc[sl], days[sl]) if n else (np.nan, np.nan, 0)
            rec.update({tag + "_n": n, tag + "_pup": round(pu, 2) if pu == pu else "",
                        tag + "_base": round(base, 2), tag + "_lift": round(pu - base, 2) if pu == pu else "",
                        tag + "_ci_lo": round(lo, 2) if lo == lo else "", tag + "_ci_hi": round(hi, 2) if hi == hi else "",
                        tag + "_blocks": nb})
        d_lift = rec["disc_lift"]; ci_lo, ci_hi = rec["disc_ci_lo"], rec["disc_ci_hi"]
        surv = (rec["disc_n"] >= SURV_N and d_lift != "" and abs(d_lift) >= SURV_LIFT
                and ci_lo != "" and (ci_lo > 0 or ci_hi < 0))
        cand = False
        if surv and rec["comp_n"] >= CAND_N and rec["comp_lift"] != "":
            same = (d_lift > 0) == (rec["comp_lift"] > 0)
            cand = same and abs(rec["comp_lift"]) >= 0.5 * abs(d_lift)
        rec["survivor"] = surv; rec["candidate"] = cand
        out_rows.append(rec)

    with open(os.path.join(OUT, "s4geo_cells_1m.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# S4-GEO 1m cells. SPENT data: characterization only; candidates = pre-registered list "
                "for the 1h/4h exam. Outcome = next-bar direction (information measure, no fees).\n")
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys())); w.writeheader()
        for r in out_rows:
            w.writerow(r)

    with open(os.path.join(OUT, "s4geo_assignments_1m.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "O", "C", "M", "P", "L1_high", "L1_low", "L2", "L3",
                    "L4_OC", "L4_OM", "L4_OP", "L4_CM", "L4_CP", "L4_MP"])
        for (b, _), g in zip(rows, groups):
            w.writerow([round(b["ts"], 3), b["bid"], b["O"], b["C"], b["M"], b["P"],
                        g["L1_high"] or "TIE", g["L1_low"] or "TIE", g["L2"] or "TIE", g["L3"] or "TIE"]
                       + [("T" if g["L4_%s" % k] is True else "F" if g["L4_%s" % k] is False else "TIE")
                          for k in ("OC", "OM", "OP", "CM", "CP", "MP")])

    print("[%.0fs] wrote cells + assignments CSVs" % (time.time() - t0), flush=True)
    return out_rows, ties, tie_l4, dict(n_all=n_all, rows=len(rows), no_ladder=n_no_ladder, doji=n_doji,
                                        disc_n=int(disc.sum()), comp_n=int(comp.sum()),
                                        disc_base=100.0 * up[disc].mean(), comp_base=100.0 * up[comp].mean(),
                                        days=len(np.unique(days)))


if __name__ == "__main__":
    res, ties, tie_l4, meta = main()
    surv = [r for r in res if r["survivor"]]
    cand = [r for r in res if r["candidate"]]
    print("\nbaselines: disc %.2f%% up / comp %.2f%% up | day-blocks %d" % (meta["disc_base"], meta["comp_base"], meta["days"]))
    print("ties: %s | L4 %s" % ({k: round(v, 2) for k, v in ties.items()}, {k: round(v, 2) for k, v in tie_l4.items()}))
    print("survivors (discovery rule): %d/50" % len(surv))
    for r in surv:
        print("  %-22s disc n=%-5d lift %+6.2f CI[%s,%s] | comp n=%-4d lift %s -> %s"
              % (r["cell"], r["disc_n"], r["disc_lift"], r["disc_ci_lo"], r["disc_ci_hi"],
                 r["comp_n"], r["comp_lift"], "CANDIDATE" if r["candidate"] else "not confirmed"))
    print("CANDIDATES for the 1h/4h exam: %d" % len(cand))
