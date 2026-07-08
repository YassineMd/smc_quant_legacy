"""Hypothesis: a PIVOT-E2-TIER trade that fires AFTER an aligned liquidity sweep is a top-tier setup.
Aligned = a Tier-A sweep in [D-W, D] whose harvest direction matches the pivot side (downside sweep 'B'
before a LONG, upside 'S' before a SHORT). Split the strategy trades by has-aligned-sweep and compare
TP%/net (fixed +0.5/-0.3 exit, taker 0.10). Sweep the lookback W. Run: python study/pivot_after_sweep.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, liq_detect            # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


def load_1m():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    return [by[b] for b in sorted(by)]


def main():
    raws = load_1m()
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    # Tier-A sweeps on the 1m tape, split by harvest side
    sweeps = [e for e in liq_detect.detect_sweeps(raws) if e["tier"] == "A"]
    buy_sw = np.array(sorted(e["i"] for e in sweeps if e["side"] == "B"))    # downside sweep -> bullish
    sell_sw = np.array(sorted(e["i"] for e in sweeps if e["side"] == "S"))   # upside sweep -> bearish
    print("1m tape %d bars | Tier-A sweeps: %d bullish(B) / %d bearish(S)" % (n, len(buy_sw), len(sell_sw)))

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

    # PIVOT-E2-TIER trades: (det, entry_bar, buy)
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
                trades.append((det, ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((det, e2, buy))

    def has_sweep(det, buy, W):
        arr = buy_sw if buy else sell_sw
        if len(arr) == 0:
            return False
        lo_i = np.searchsorted(arr, det - W, "left"); hi_i = np.searchsorted(arr, det, "right")
        return hi_i > lo_i

    def econ(events):
        res = [walk(j0, buy) for _d, j0, buy in events]
        res = [r for r in res if r is not None]
        if not res:
            return 0, float("nan"), float("nan")
        a = np.array(res)
        return len(res), 100.0 * np.mean(a > 0), a.mean() - FEE

    print("\n  W(bars) | AFTER aligned sweep  (n / TP%% / net)  | NO sweep  (n / TP%% / net)")
    for W in (15, 30, 60, 120, 240):
        aft = [t for t in trades if has_sweep(t[0], t[2], W)]
        non = [t for t in trades if not has_sweep(t[0], t[2], W)]
        na, ta, nea = econ(aft); nn, tn, nen = econ(non)
        print("   %4d    | %2d / %5.1f / %+6.3f%%          | %3d / %5.1f / %+6.3f%%"
              % (W, na, ta, nea, nn, tn, nen))
    print("  (baseline all trades: pivot E2-tier = +0.128%%, n~106)")


if __name__ == "__main__":
    main()
