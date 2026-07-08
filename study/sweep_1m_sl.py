"""1m LIQUIDITY-SWEEP trade + SL/TP optimization. Frozen Tier-A detector (app.liq_detect.detect_sweeps) run
on the 1m tape; enter the reversal at the sweep bar's close (S=upside->SHORT, B=downside->LONG), manage on 1m.
Grid-sweep SL x TP (6h cap, taker 0.10) to find the optimum, then compare net to PIVOT-E2-TIER (+0.128%/trade).

Run: python study/sweep_1m_sl.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app import liq_detect                                  # frozen detector

H_S = 6 * 3600.0; FEE = 0.10


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
    bids = sorted(by)
    return bids, [by[b] for b in bids]


def main():
    bids, raws = load_1m()
    n = len(raws)
    cl = np.array([float(d.get("close", d.get("close_price", 0.0))) for d in raws])
    hi = np.array([float(d.get("high", 0.0)) for d in raws]); lo = np.array([float(d.get("low", 0.0)) for d in raws])
    et = np.array([float(d.get("end_time", 0.0)) for d in raws]); st = np.array([float(d.get("start_time", 0.0)) for d in raws])
    evs = liq_detect.detect_sweeps(raws)
    A = [e for e in evs if e["tier"] == "A"]; AB = [e for e in evs if e["tier"] in ("A", "B")]
    print("1m tape %d buckets (Idx %d..%d) | sweeps %d (A %d / B %d)"
          % (n, bids[0], bids[-1], len(evs), len(A), len(AB) - len(A)))

    def walk(i, long, slp, tpp):
        entry = float(cl[i]); sl = entry * (1 - slp / 100.0) if long else entry * (1 + slp / 100.0)
        tp = entry * (1 + tpp / 100.0) if long else entry * (1 - tpp / 100.0); te = float(et[i])
        for j in range(i + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if long else (hi[j] >= sl):
                return -slp
            if (hi[j] >= tp) if long else (lo[j] <= tp):
                return tpp
        return None            # unresolved (excluded)

    def net_grid(events, slp, tpp):
        res = [walk(e["i"], e["side"] == "B", slp, tpp) for e in events]
        res = [r for r in res if r is not None]
        if not res:
            return float("nan"), 0, 0.0
        a = np.array(res)
        return a.mean() - FEE, len(res), 100.0 * np.mean(a > 0)

    SLS = [0.2, 0.3, 0.4, 0.5, 0.7, 1.0]; TPS = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
    for tag, events in (("Tier-A", A), ("Tier-A+B", AB)):
        print("\n########## %s (n=%d) — net%% grid [rows=SL, cols=TP] ##########" % (tag, len(events)))
        print("   SL\\TP |" + "".join("  %+4.1f " % t for t in TPS))
        best = (-9, None)
        for slp in SLS:
            cells = []
            for tpp in TPS:
                net, nn, win = net_grid(events, slp, tpp)
                cells.append("%+5.2f" % net)
                if net > best[0]:
                    best = (net, (slp, tpp, nn, win))
            print("   %4.1f  |" % slp + " ".join(" %s" % c for c in cells))
        bn, (bs, bt, bnn, bw) = best
        print("  >>> OPTIMUM: SL -%.1f%% / TP +%.1f%% -> net %+.3f%% | resolved %d | win %.1f%%  (pivot = +0.128%%)"
              % (bs, bt, bn, bnn, bw))


if __name__ == "__main__":
    main()
