"""PIVOT INDICATOR backtest — runs the LIVE indicator's exact rule set (app/pivot_detect.py: the frozen
S5j-r5 detection + WAIT-baseline-touch entry) over the local history tape and scores each setup with the
indicator's fixed exit (TP +0.5% / SL -0.3%, 6h cap, taker 0.10% net).

WHY a separate runner (vs study/s5j_final.py): s5j_final used a GLOBAL fire-search blackout. The shipped
indicator instead walks INDEPENDENT per-side sequential chains — a buy setup's entry gates only the next
BUY, a sell's only the next SELL (terminal _draw_pivot). This runner reproduces THAT selection exactly, off
the same detect_pivots() the terminal calls, so the CSV is the strategy as the indicator actually marks it.

CSV (study/out/pivot_backtest_episodes.csv) = every column s5j_episodes.csv had PLUS the six hover-stats-box
fields: leg5_N (bars back to the leg-5 reference candle), ref_to_det_pct (reference OPEN -> detection CLOSE),
det_to_entry_pct (detection CLOSE -> entry CLOSE), and the LARGE market-order (panel-8) net buy/sell VOLUME
over N=0..100 plus their spread-delta %. The room ratio the box shows is the existing profit_room_ratio column.

NOTE on the large-vol columns: the daemon's live p95 large cutoff is NOT stored per historical bucket, so
these use config.SIZE_DEFAULT_LARGE (the same value _largesmall_thresholds falls back to cold-start) as a
fixed, reproducible cutoff — a consistent measure across fires, but not the exact live p95 at each moment.
"""
import os, sys, csv, glob, json, math, time, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
from app.persistence import _bucket_from_dict           # noqa: E402
from app import pivot_detect as PD, config              # noqa: E402

OUT = os.path.join(REPO, "study", "out")
WIN = 3600.0; H_S = 6 * 3600.0; FEE = 0.10; FIRST = 100
LARGE_THR = config.SIZE_DEFAULT_LARGE                    # fixed large cutoff (daemon p95 not stored offline)


def load_local_tape():
    """Merge EVERY study/data/history_snapshot_*.db by per-tf bucket index (later/fresher db wins on dupes),
    the same scheme as m10_sweep_s5b.load_merged but self-contained + auto-globbing so a fresh pull is picked
    up without editing a hardcoded list. Returns (bids, raws, gaps)."""
    by_bid = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        raw = [json.loads(x[0]) for x in con.execute(
            "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
        tc = int(con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()[0])
        con.close()
        base = tc - len(raw)
        for j, d in enumerate(raw):
            by_bid[base + j + 1] = d
    bids = sorted(by_bid)
    gaps = [(a, b) for a, b in zip(bids, bids[1:]) if b != a + 1]
    return bids, [by_bid[b] for b in bids], gaps


def _has_size(s):
    """True iff a bucket carries POPULATED size histograms. The feature is wire-additive: early/pre-upgrade
    buckets carry an all-zero sz_vb/sz_vs, which is NO DATA (not a genuine zero) and must not be summed."""
    vb = s.get("sz_vb"); vs = s.get("sz_vs")
    return bool((vb and any(vb)) or (vs and any(vs)))


def hist_side(arr, thr=LARGE_THR):
    """Sum the LARGE side (qty >= thr) of a per-bucket size histogram, log-linear on the straddling bin —
    verbatim terminal._hist_side(arr, thr, above=True). Panel-8 large-order volume for one bucket."""
    if not arr:
        return 0.0
    edges = config.SIZE_HIST_EDGES; b = config.size_bin(thr); ne = len(edges)
    tot = sum(arr[b + 1:])
    if 0 < b < ne:
        lo_e, hi_e = edges[b - 1], edges[b]
        if hi_e > lo_e > 0.0 and hi_e > thr > 0.0:
            tot += min(1.0, max(0.0, math.log(hi_e / thr) / math.log(hi_e / lo_e))) * arr[b]
    return tot


def main():
    t0 = time.time()
    bids, raws, gaps = load_local_tape()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); op = np.array([b.open_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)
    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95    # moving POC baseline (5% EMA), for the `baseline` col

    def witness(b):
        """(first_red_N, first_green_N): smallest EXISTS witness each way in [50,100]. s5j_final verbatim."""
        red = green = ""; c = rcl[b]
        for N in range(50, 101):
            o = rop[b - N + 1]
            if red == "" and o > c:
                red = N
            if green == "" and o < c:
                green = N
            if red != "" and green != "":
                break
        return red, green

    def rooms(zhp, zlp, long):
        """profit-room / adverse-room from the entry to the leg-5 zone hi/lo, side-aware + ratio. s5j verbatim."""
        pr, ar = (zhp, -zlp) if long else (-zlp, zhp)
        rt = round(pr / ar, 3) if abs(ar) > 1e-9 else ""
        return round(pr, 4), round(ar, 4), rt

    def walk_fixed(j_e, entry, side):
        """Fixed TP+0.5 / SL-0.3 from the entry CLOSE, 6h cap -> UNRESOLVED. s5j_final verbatim (close entry)."""
        long = side == "long"
        sl_lvl = entry * (1 - 0.003) if long else entry * (1 + 0.003)
        tp_lvl = entry * (1 + 0.005) if long else entry * (1 - 0.005)
        t_e = float(et[j_e]); j = j_e + 1
        while j < n and st[j] <= t_e + H_S:
            a = (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl)
            b_ = (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl)
            if a or b_:
                m = (st[j] - t_e) / 60.0
                return ("SL" + ("*" if a else ""), -0.3, m) if b_ else ("TP", 0.5, m)
            j += 1
        return "UNRESOLVED", None, None

    # ---- detection + entry via the LIVE indicator engine, then the indicator's INDEPENDENT per-side walk ----
    fires = PD.detect_pivots(snaps)                     # {det_i, entry_i, side, wait_end_i, zref_i}
    fl = sorted(fires, key=lambda f: (f["det_i"], f["side"]))
    scan_from = {"long": 0, "short": 0}
    processed = []                                      # non-skipped fires, in order (setup or CANCELLED)
    for f in fl:
        side = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan_from[side]:
            continue                                    # overlaps a live setup on this side -> the walk skips it
        processed.append(f)
        scan_from[side] = (ent + 1) if ent is not None else f["wait_end_i"]

    rows = []
    for f in processed:
        b = f["det_i"]; side = f["side"]; zref = f["zref_i"]; j_e = f["entry_i"]
        long = side == "long"; c0 = float(cl[b])
        red, green = witness(b)
        zsl = slice(b - 99, b - 58)                     # leg-5 context zone: bars b-99..b-59 (N=60..100)
        zhi = float(np.max(hi[zsl])); zlo = float(np.min(lo_[zsl]))
        row = dict(fire_bid=int(bids[b]), side=side, outcome="", w_max="", w_min="",
                   zone_hi_pct="", zone_lo_pct="", zone_range="", profit_room="", adverse_room="",
                   profit_room_ratio="", pnl="", mins="", entry="", baseline=round(float(base[b]), 4),
                   route="", status="", delay="", t_max="", t_min="", first_red_N=red, first_green_N=green,
                   ts=round(float(et[b]), 3))
        if j_e is None:                                 # CANCELLED — no baseline touch within the hour
            row["route"] = "WAIT"; row["status"] = "CANCELLED"
            row["zone_hi_pct"] = round((zhi - c0) / c0 * 100.0, 4)      # fire-close reference
            row["zone_lo_pct"] = round((zlo - c0) / c0 * 100.0, 4)
            row["zone_range"] = round((zhi - zlo) / c0 * 100.0, 4)
            row["profit_room"], row["adverse_room"], row["profit_room_ratio"] = \
                rooms(row["zone_hi_pct"], row["zone_lo_pct"], long)
            j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
            if j1 > b + 1:                              # fire-referenced counterfactual excursion
                w = slice(b + 1, j1); k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
                row["w_max"] = round((float(np.max(hi[w])) - c0) / c0 * 100.0, 4)
                row["w_min"] = round((float(np.min(lo_[w])) - c0) / c0 * 100.0, 4)
                row["t_max"] = round((float(et[b + 1 + k_up]) - et[b]) / 60.0, 2)
                row["t_min"] = round((float(et[b + 1 + k_dn]) - et[b]) / 60.0, 2)
            entry = None
        else:
            if j_e == b:
                entry = c0; row["route"] = "MKT"; row["status"] = "MKT"; row["delay"] = 0.0
            else:
                entry = float(cl[j_e]); row["route"] = "WAIT"; row["status"] = "TOUCH"
                row["delay"] = round((et[j_e] - et[b]) / 60.0, 2)
            row["zone_hi_pct"] = round((zhi - entry) / entry * 100.0, 4)   # entry-referenced
            row["zone_lo_pct"] = round((zlo - entry) / entry * 100.0, 4)
            row["zone_range"] = round((zhi - zlo) / entry * 100.0, 4)
            row["profit_room"], row["adverse_room"], row["profit_room_ratio"] = \
                rooms(row["zone_hi_pct"], row["zone_lo_pct"], long)
            j1e = int(np.searchsorted(et, et[j_e] + WIN, side="right"))
            if j1e > j_e + 1:                           # entry-referenced 1h excursion
                w = slice(j_e + 1, j1e); k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
                row["w_max"] = round((float(np.max(hi[w])) - entry) / entry * 100.0, 4)
                row["w_min"] = round((float(np.min(lo_[w])) - entry) / entry * 100.0, 4)
                row["t_max"] = round((float(et[j_e + 1 + k_up]) - et[j_e]) / 60.0, 2)
                row["t_min"] = round((float(et[j_e + 1 + k_dn]) - et[j_e]) / 60.0, 2)
            row["entry"] = round(entry, 4)
            out, pnl, mins = walk_fixed(j_e, entry, side)
            row["outcome"] = out
            row["pnl"] = round(pnl, 4) if pnl is not None else ""
            row["mins"] = round(mins, 2) if mins is not None else ""

        # ---- the six hover-stats-box fields ----------------------------------------------------------
        row["leg5_N"] = int(b - zref)
        row["ref_to_det_pct"] = round((c0 - op[zref]) / op[zref] * 100.0, 4) if op[zref] else ""
        row["det_to_entry_pct"] = round((entry - c0) / c0 * 100.0, 4) if (entry is not None and c0) else ""
        lo0 = max(0, b - 100)                           # LARGE net volume over N=0..100 ([det-100, det])
        if all(_has_size(snaps[k]) for k in range(lo0, b + 1)):
            bvol = sum(hist_side(snaps[k].get("sz_vb")) for k in range(lo0, b + 1))
            svol = sum(hist_side(snaps[k].get("sz_vs")) for k in range(lo0, b + 1))
            tot = bvol + svol
            row["lg_buy_vol"] = round(bvol, 2); row["lg_sell_vol"] = round(svol, 2)
            row["lg_spread_delta_pct"] = round((bvol - svol) / tot * 100.0, 2) if tot > 1e-9 else 0.0
        else:                                           # size histograms not live across the whole window
            row["lg_buy_vol"] = row["lg_sell_vol"] = row["lg_spread_delta_pct"] = ""
        rows.append(row)

    cols = ["fire_bid", "side", "outcome", "w_max", "w_min", "zone_hi_pct", "zone_lo_pct", "zone_range",
            "profit_room", "adverse_room", "profit_room_ratio", "pnl", "mins", "entry", "baseline",
            "route", "status", "delay", "t_max", "t_min", "first_red_N", "first_green_N", "ts",
            "leg5_N", "ref_to_det_pct", "det_to_entry_pct", "lg_buy_vol", "lg_sell_vol",
            "lg_spread_delta_pct"]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "pivot_backtest_episodes.csv"), "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---- console summary ------------------------------------------------------------------------------
    def econ(side):
        e = [r for r in rows if r["side"] == side and r["status"] in ("MKT", "TOUCH")]
        ntp = sum(1 for r in e if r["outcome"] == "TP")
        nsl = sum(1 for r in e if str(r["outcome"]).startswith("SL"))
        nun = sum(1 for r in e if r["outcome"] == "UNRESOLVED")
        res = ntp + nsl
        pnls = [r["pnl"] for r in e if r["pnl"] != ""]
        tp = 100.0 * ntp / res if res else float("nan")
        net = (float(np.mean(pnls)) - FEE) if pnls else float("nan")
        canc = sum(1 for r in rows if r["side"] == side and r["status"] == "CANCELLED")
        return len(e), ntp, nsl, nun, tp, net, canc

    print("[%3.0fs] tape %d bars (%d gaps) | %d fires -> %d processed setups/rows"
          % (time.time() - t0, n, len(gaps), len(fires), len(rows)))
    for s in ("long", "short"):
        ne, ntp, nsl, nun, tp, net, canc = econ(s)
        print("  %-5s: %d filled (%d TP / %d SL / %d unres) | TP%% %.1f | exp net %+.3f%% | %d cancelled"
              % (s, ne, ntp, nsl, nun, tp, net, canc))
    n_nosize = sum(1 for r in rows if r["lg_buy_vol"] == "")
    print("  large-vol cols: %d/%d rows blank (size histograms not live pre-bid ~14271) | cutoff %.0f contracts"
          % (n_nosize, len(rows), LARGE_THR))
    print("  -> study/out/pivot_backtest_episodes.csv (%d cols)" % len(cols))


if __name__ == "__main__":
    main()
