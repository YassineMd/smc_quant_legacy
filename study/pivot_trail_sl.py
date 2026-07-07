"""PIVOT-E2-TIER trailing-stop test: once price reaches +TRIG% favorable, move the SL up to +LOCK% (profit
lock); TP stays +0.5%, initial SL -0.3%, 6h cap m2m. Test TRIG = 0.2 and 0.3, LOCK = 0.1. Hypothesis: fewer
losers. Exit precedence = stop-first (pessimistic, matches the fixed baseline); the lock arms from the NEXT
bar (avoids intrabar path ambiguity). Fee 0.10. Run: python study/pivot_trail_sl.py
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


def main():
    raws = load_tape("1m")
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, buy, trig=None, lock=0.1):
        entry = float(cl[j0]); sl0 = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995
        tl = entry * (1 + trig / 100.0) if (trig and buy) else (entry * (1 - trig / 100.0) if trig else None)
        lk = entry * (1 + lock / 100.0) if buy else entry * (1 - lock / 100.0)
        sg = 1.0 if buy else -1.0; te = float(et[j0]); jl = j0; armed = False
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            jl = j
            stop = lk if armed else sl0
            if (lo[j] <= stop) if buy else (hi[j] >= stop):          # stop-first (pessimistic)
                return (lock, "LOCK") if armed else (-0.3, "SL")
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return 0.5, "TP"
            if trig and not armed and ((hi[j] >= tl) if buy else (lo[j] <= tl)):
                armed = True                                          # locks from the NEXT bar
        return sg * (cl[jl] - entry) / entry * 100.0, "CAP"

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []
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
                trades.append((ent, buy, tier))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((e2, buy, tier))

    def run(trig, lock=0.1):
        g = []; kinds = {"TP": 0, "LOCK": 0, "SL": 0, "CAP": 0}
        for j0, buy, _t in trades:
            gg, k = walk(j0, buy, trig, lock); g.append(gg); kinds[k] += 1
        a = np.array(g)
        return a.mean() - FEE, 100.0 * np.mean(a > 0), kinds

    print("PIVOT-E2-TIER, %d trades | trail @ +0.35%%: lock +0.1%% vs breakeven 0%% (TP+0.5/init SL-0.3)\n" % len(trades))
    net0, win0, k0 = run(None)
    print("  FIXED (no trail)         : net %+6.3f%% | win %4.1f%% | TP %d SL %d" % (net0, win0, k0["TP"], k0["SL"]))
    for lk in (0.10, 0.00):
        net, win, kk = run(0.35, lk)
        print("  trail@+0.35 lock +%.2f%%   : net %+6.3f%% | win %4.1f%% | TP %d LOCK %d SL %d | vs fixed %+.3f pp"
              % (lk, net, win, kk["TP"], kk["LOCK"], kk["SL"], net - net0))

    def subnet(sub, trig, lk):
        g = [walk(j0, buy, trig, lk)[0] for j0, buy in sub]
        return (np.mean(g) - FEE) if g else float("nan")
    def cnt(sub, trig, lk):
        c = {"TP": 0, "LOCK": 0, "SL": 0, "CAP": 0}
        for j0, buy in sub:
            c[walk(j0, buy, trig, lk)[1]] += 1
        return c

    print("\n  NET by D state @ +0.35%% trigger:")
    print("   tier         | n  | fixed  | lock+0.1 | BE 0%%   | LOCK/SL: lock+0.1  |  BE 0%%")
    for tname, lbl in (("cyan", "cyan/orange"), ("green", "green/red"), ("hollow", "hollow")):
        sub = [(j0, buy) for j0, buy, t in trades if t == tname]
        c1 = cnt(sub, 0.35, 0.1); c0 = cnt(sub, 0.35, 0.0)
        print("   %-12s | %2d | %+6.3f | %+6.3f  | %+6.3f | %d / %d           |  %d / %d"
              % (lbl, len(sub), subnet(sub, None, 0.1), subnet(sub, 0.35, 0.1), subnet(sub, 0.35, 0.0),
                 c1["LOCK"], c1["SL"], c0["LOCK"], c0["SL"]))

    # tier-conditional: hollow -> +0.35%/+0.1% trail; green/red + cyan -> plain fixed
    def cond(j0, buy, t):
        return walk(j0, buy, 0.35 if t == "hollow" else None, 0.1)
    gc = [cond(j0, buy, t)[0] for j0, buy, t in trades]
    ck = {"TP": 0, "LOCK": 0, "SL": 0, "CAP": 0}
    for j0, buy, t in trades:
        ck[cond(j0, buy, t)[1]] += 1
    ac = np.array(gc); cnet = ac.mean() - FEE
    trailall = run(0.35)[0]
    print("\n  STRATEGIES (all 106 trades):")
    print("    all fixed                          : net %+6.3f%%" % net0)
    print("    all trail @ +0.35/+0.1             : net %+6.3f%%  (%+.3f vs fixed)" % (trailall, trailall - net0))
    print("    CONDITIONAL hollow-trail, rest fixed: net %+6.3f%%  (%+.3f vs fixed) | win %4.1f%% | TP %d LOCK %d SL %d"
          % (cnet, cnet - net0, 100.0 * np.mean(ac > 0), ck["TP"], ck["LOCK"], ck["SL"]))


if __name__ == "__main__":
    main()
