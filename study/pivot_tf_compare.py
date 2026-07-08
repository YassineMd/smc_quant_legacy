"""Is PIVOT-E2-TIER better on 5m than 1m? Runs the SAME strategy (detect_pivots + independent walk + the
E2-tier router: cyan/green->E2, hollow->E-held-else-E2; fixed +0.5/-0.3, 6h cap, taker 0.10) on each tf's
FULL available tape, and reports net, win%, setup count, tape span, and setups/day. Also the raw pivot (all E
entries) for context. Run: python study/pivot_tf_compare.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


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


def analyze(tf):
    raws = load_tape(tf)
    if not raws:
        return None
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    days = (et[-1] - et[0]) / 86400.0

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, buy):
        entry = float(cl[j0]); sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                return -0.3
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return 0.5
        return None

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; strat = []; raw = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")
        raw.append((ent, buy))                                   # raw pivot = all E entries
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        if e_held:
            if tier == "hollow":
                strat.append((ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                strat.append((e2, buy))

    def econ(events):
        res = [walk(j0, buy) for j0, buy in events]
        res = [r for r in res if r is not None]
        if not res:
            return 0, float("nan"), float("nan")
        a = np.array(res)
        return len(res), 100.0 * np.mean(a > 0), a.mean() - FEE

    rn, rtp, rnet = econ(raw); sn, stp, snet = econ(strat)
    return dict(tf=tf, bars=n, days=days, raw_n=rn, raw_tp=rtp, raw_net=rnet,
                strat_n=sn, strat_tp=stp, strat_net=snet)


def main():
    print("PIVOT strategy: 1m vs 5m (each on its FULL available tape)\n")
    print("  tf | bars  | days | RAW pivot (E): n / TP%% / net      | E2-TIER strategy: n / TP%% / net / per-day")
    for tf in ("1m", "5m"):
        r = analyze(tf)
        if r is None:
            print("  %s | (no data)" % tf); continue
        print("  %2s | %5d | %4.1f | %3d / %4.1f%% / %+6.3f%%          | %3d / %4.1f%% / %+6.3f%% / %.2f"
              % (r["tf"], r["bars"], r["days"], r["raw_n"], r["raw_tp"], r["raw_net"],
                 r["strat_n"], r["strat_tp"], r["strat_net"], r["strat_n"] / r["days"] if r["days"] else 0))


if __name__ == "__main__":
    main()
