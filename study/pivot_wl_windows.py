"""W/L characterization in the PRE-ENTRY windows: does panel behavior during setup FORMATION predict the
eventual winner/loser? For each PIVOT-E2-TIER trade, track the aligned panels (P0 composite, P1 absorption,
P2 eff-agg, P3 E/R, P4 exhaustion; + = supports the trade) over four windows and split by outcome:
  D->E   (all trades)       E->E2 (E2 trades)   D->E2 (E2 trades)   D->E for hollow E-held
Reports mean [and min] aligned value over each window, W vs L -> the biggest gap = the pre-entry tell.
Run: python study/pivot_wl_windows.py
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
    return [by[b] for b in sorted(by)]


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

    def walk(j0, buy):
        entry = float(cl[j0]); sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                return "SL"
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return "TP"
        return "UNRES"

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []      # (det, ent, e2|None, tier, entry_bar, buy)
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        if e_held:
            if tier == "hollow":
                trades.append((det, ent, None, tier, ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((det, ent, e2, tier, e2, buy))

    def win_stats(sel, i0f, i1f):
        """sel(trade)->bool filter; window [i0f(t), i1f(t)]. Returns {panel:(Wmean,Lmean,Wmin,Lmin)} + (nW,nL)."""
        acc = {k: {"TP": {"m": [], "mn": []}, "SL": {"m": [], "mn": []}} for k in P}
        nw = nl = 0
        for t in trades:
            det, ent, e2, tier, eb, buy = t
            if not sel(t):
                continue
            out = walk(eb, buy)
            if out not in ("TP", "SL"):
                continue
            i0, i1 = i0f(t), i1f(t)
            if i1 < i0:
                continue
            nw += out == "TP"; nl += out == "SL"; sg = 1.0 if buy else -1.0
            for k, arr in P.items():
                seg = sg * arr[i0:i1 + 1]
                acc[k][out]["m"].append(float(np.mean(seg))); acc[k][out]["mn"].append(float(np.min(seg)))
        return acc, nw, nl

    def report(title, sel, i0f, i1f):
        acc, nw, nl = win_stats(sel, i0f, i1f)
        print("\n== %s ==  (winners=%d losers=%d)" % (title, nw, nl))
        if nw == 0 or nl == 0:
            print("   (insufficient split)"); return
        print("   panel |  MEAN  W | L | dW-L   |   MIN  W | L | dW-L")
        for k in ("P0", "P1", "P2", "P3", "P4"):
            wm, lm = np.mean(acc[k]["TP"]["m"]), np.mean(acc[k]["SL"]["m"])
            wn, ln = np.mean(acc[k]["TP"]["mn"]), np.mean(acc[k]["SL"]["mn"])
            print("   %-4s | %+6.1f | %+6.1f | %+6.1f  | %+6.1f | %+6.1f | %+6.1f"
                  % (k, wm, lm, wm - lm, wn, ln, wn - ln))

    report("D -> E   (all trades)", lambda t: True, lambda t: t[0], lambda t: t[1])
    report("E -> E2  (E2 trades)", lambda t: t[2] is not None, lambda t: t[1], lambda t: t[2])
    report("D -> E2  (E2 trades)", lambda t: t[2] is not None, lambda t: t[0], lambda t: t[2])
    report("D -> E   hollow E-held", lambda t: t[3] == "hollow" and t[2] is None, lambda t: t[0], lambda t: t[1])


if __name__ == "__main__":
    main()
