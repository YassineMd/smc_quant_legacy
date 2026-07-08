"""15m LIQUIDITY-SWEEP trade backtest vs the pivot. Frozen Tier-A rule (app.liq_detect.detect_sweeps): enter
the REVERSAL at the sweep bar's close — side 'S' (upside sweep) -> SHORT, 'B' (downside) -> LONG. Same fixed
exit as the pivot (+0.5 / -0.3, 6h cap, taker 0.10) for apples-to-apples, plus MFE/MAE over 2h/6h to see the
natural room (15m bars move more than 1m). Reports Tier-A and Tier-A+B. Compare net vs PIVOT-E2-TIER +0.128%.

Run: python study/sweep_backtest.py   NOTE: 15m + Tier-A = a TINY sample (~dozens) -> anecdote, not proof.
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app import liq_detect                                  # the frozen detector the terminal runs live

H_S = 6 * 3600.0; FEE = 0.10; SL = 0.003


def load_15m():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_15m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf='15m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    bids = sorted(by)
    return bids, [by[b] for b in bids]


def main():
    bids, raws = load_15m()
    n = len(raws)
    cl = np.array([float(d.get("close", d.get("close_price", 0.0))) for d in raws])
    hi = np.array([float(d.get("high", 0.0)) for d in raws]); lo = np.array([float(d.get("low", 0.0)) for d in raws])
    et = np.array([float(d.get("end_time", 0.0)) for d in raws]); st = np.array([float(d.get("start_time", 0.0)) for d in raws])
    evs = liq_detect.detect_sweeps(raws)
    print("loaded %d 15m buckets (Idx %d..%d) | sweeps: %d total (A %d / B %d)"
          % (n, bids[0], bids[-1], len(evs), sum(e["tier"] == "A" for e in evs), sum(e["tier"] == "B" for e in evs)))

    def walk_fixed(i, entry, long):
        sl = entry * (1 - SL) if long else entry * (1 + SL)
        tp = entry * 1.005 if long else entry * 0.995
        te = float(et[i])
        for j in range(i + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if long else (hi[j] >= sl):
                return -0.3
            if (hi[j] >= tp) if long else (lo[j] <= tp):
                return 0.5
        return None

    def excursion(i, entry, long, hz):
        te = float(et[i]); mx = -1e18; mn = 1e18; any_ = False
        for j in range(i + 1, n):
            if st[j] > te + hz:
                break
            mx = max(mx, float(hi[j])); mn = min(mn, float(lo[j])); any_ = True
        if not any_:
            return None, None
        return ((mx - entry) / entry * 100.0, (mn - entry) / entry * 100.0) if long \
            else ((entry - mn) / entry * 100.0, (entry - mx) / entry * 100.0)

    def report(tag, events):
        res = []; mfe1 = []; mae1 = []; mfe6 = []
        for e in events:
            i = e["i"]; long = e["side"] == "B"; entry = float(cl[i])
            r = walk_fixed(i, entry, long)
            if r is not None:
                res.append(r)
            f1, a1 = excursion(i, entry, long, 3600.0); f6, _ = excursion(i, entry, long, H_S)
            if f1 is not None:
                mfe1.append(f1); mae1.append(a1)
            if f6 is not None:
                mfe6.append(f6)
        ntp = sum(1 for r in res if r > 0); nsl = len(res) - ntp
        net = (float(np.mean(res)) - FEE) if res else float("nan")
        print("\n== %s (n=%d) ==" % (tag, len(events)))
        if res:
            print("  FIXED +0.5/-0.3 : %d TP / %d SL | TP%% %.1f | net %+.3f%%" % (ntp, nsl, 100.0*ntp/len(res), net))
        if mfe1:
            print("  room: 1h MFE %+.3f%% / MAE %+.3f%% | 6h MFE %+.3f%% (avg)"
                  % (float(np.mean(mfe1)), float(np.mean(mae1)), float(np.mean(mfe6))))

    A = [e for e in evs if e["tier"] == "A"]
    AB = [e for e in evs if e["tier"] in ("A", "B")]
    report("Tier-A only", A)
    report("Tier-A + B", AB)
    print("\n  (compare: PIVOT-E2-TIER fixed exit = +0.128%%/trade, n=106, 1m)")


if __name__ == "__main__":
    main()
