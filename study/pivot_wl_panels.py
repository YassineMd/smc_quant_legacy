"""What separates a WINNER from a LOSER on the PIVOT-E2-TIER strategy? Track each panel's ALIGNED value
(positive = supports the trade) from entry to the exit bar (TP or SL hit), on the 1m tape.

Panels (all oriented so +ve = bullish, then * side sign): P0 = smoothed composite (sum0), P1 = absorption
lean (1-2*a_sh), P2 = eff-agg (2*e_sh-1), P3 = E/R (2*r_sh-1), P4 = exhaustion (s4). Strategy trades only
(cyan/orange->E2, green/red->E2, hollow->E-held-else-E2). Fixed +0.5/-0.3, 6h cap. Reports, W vs L: the panel
value AT ENTRY, its MEAN over the hold, and its MIN over the hold -> the biggest W-L gap = the discriminator.

Run: python study/pivot_wl_panels.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
LW = config.LIVE_PANEL_WINDOW


def load_tape(tf):
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_%s" % tf,)).fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,))]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    bids = sorted(by)
    return [by[b] for b in bids]


def main():
    raws = load_tape("1m")
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    a_sh, e_sh, r_sh, sum0 = PD._p9_global(snaps)
    ex = R.trailing_exhaustion(snaps, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    s4 = np.zeros(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        s4[k] = hold
    P = {"P0": np.asarray(sum0), "P1": (1 - 2 * np.asarray(a_sh)) * 100.0,
         "P2": (2 * np.asarray(e_sh) - 1) * 100.0, "P3": (2 * np.asarray(r_sh) - 1) * 100.0, "P4": s4}
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, entry, buy):
        sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995
        te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            slh = (lo[j] <= sl) if buy else (hi[j] >= sl)
            tph = (hi[j] >= tp) if buy else (lo[j] <= tp)
            if slh:
                return "SL", j
            if tph:
                return "TP", j
        return "UNRES", None

    # strategy trades: (entry_bar, buy)
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"
        p2d = spr(det, buy)
        tier = "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        if e_held:
            if tier == "hollow":
                trades.append((ent, buy))          # hollow -> E-held
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((e2, buy))            # any tier -> E2

    # per outcome, collect {panel: [at_entry, mean_hold, min_hold]}
    agg = {"TP": {k: {"ent": [], "mean": [], "min": []} for k in P},
           "SL": {k: {"ent": [], "mean": [], "min": []} for k in P}}
    for j0, buy in trades:
        out, jx = walk(j0, float(cl[j0]), buy)
        if out not in ("TP", "SL") or jx is None:
            continue
        sg = 1.0 if buy else -1.0
        for k, arr in P.items():
            seg = sg * arr[j0:jx + 1]               # entry .. exit inclusive, aligned
            agg[out][k]["ent"].append(sg * float(arr[j0]))
            agg[out][k]["mean"].append(float(np.mean(seg)))
            agg[out][k]["min"].append(float(np.min(seg)))

    ntp = len(agg["TP"]["P2"]["mean"]); nsl = len(agg["SL"]["P2"]["mean"])
    print("PIVOT-E2-TIER W/L panel diagnostic (1m) | winners=%d  losers=%d" % (ntp, nsl))
    print("aligned panel value (+ = supports the trade); avg across trades")
    print("%-4s | %-22s | %-22s | %-22s" % ("", "AT ENTRY  (W | L | dW-L)", "MEAN hold (W | L | d)", "MIN hold (W | L | d)"))
    for k in ("P0", "P1", "P2", "P3", "P4"):
        cells = []
        for m in ("ent", "mean", "min"):
            w = np.mean(agg["TP"][k][m]); l = np.mean(agg["SL"][k][m])
            cells.append("%+6.1f | %+6.1f | %+6.1f" % (w, l, w - l))
        print("%-4s | %s | %s | %s" % (k, cells[0], cells[1], cells[2]))


if __name__ == "__main__":
    main()
