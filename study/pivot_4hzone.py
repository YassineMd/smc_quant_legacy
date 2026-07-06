"""PIVOT 240x (4h) buy/sell-zone location filter.

Hypothesis (operator, in-sample supported p=0.006): a pivot is likelier to win if it sits in the buy/sell
zone of the LAST COMPLETED 240x (4h) whiskerplot bucket -- a BUY pivot in the buyer zone (lower wick, its
price <= vq_lo) / a SELL pivot in the seller zone (upper wick, price >= vq_hi). Zones come from the tf='4h'
stored buckets (== the live SSH stream; the narrow 5-level buckets ARE the 240x stream, verified). Pivots
detect + trade on the 1m via app.pivot_detect. Run: python study/pivot_4hzone.py

NOTE the 1m only reaches ~June 28 (10k rolling cap aged June 22 off); the 4h stream reaches June 20.
"""
import os, sys, csv, sqlite3, json, glob, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict          # noqa: E402
from app import pivot_detect as PD, bar_quantiles       # noqa: E402
from pivot_backtest import load_local_tape              # noqa: E402
from math import comb


def load_4h_zones():
    """(z_et sorted, zones=[(low, vq_lo, vq_hi, high)]) from the newest snapshot's 240x/4h buckets."""
    db = sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db")))[-1]
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    b4 = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='4h' ORDER BY id")]
    con.close()
    b4 = [b for b in b4 if b.get("levels")]
    z_et = [float(b["end_time"]) for b in b4]
    zones = [(float(b["low"]), *bar_quantiles.vq(b["levels"])[::2], float(b["high"])) for b in b4]
    return z_et, zones     # vq()[::2] = (vq_lo, vq_hi)


def fisher(a, b, c, d):
    N = a + b + c + d; r1 = a + b; r2 = c + d; c1 = a + c
    def pab(x): return comb(r1, x) * comb(r2, c1 - x) / comb(N, c1)
    p0 = pab(a); lo = max(0, c1 - r2); hi = min(r1, c1)
    return sum(pab(x) for x in range(lo, hi + 1) if pab(x) <= p0 + 1e-12)


def main():
    bids, raws, gaps = load_local_tape()
    bks = [_bucket_from_dict(d) for d in raws]
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    snaps = [b.full_snapshot() for b in bks]
    z_et, zones = load_4h_zones()
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; proc = []
    for f in fires:
        if f["det_i"] < scan[f["side"]]:
            continue
        proc.append(f); scan[f["side"]] = (f["entry_i"] + 1) if f["entry_i"] is not None else f["wait_end_i"]
    cr = {int(r["fire_bid"]): r for r in csv.DictReader(
        open(os.path.join(REPO, "study", "out", "pivot_backtest_episodes.csv"), encoding="utf-8"))}

    rows = []
    for f in proc:
        if f["entry_i"] is None:
            continue
        r = cr.get(int(bids[f["det_i"]]))
        if not r or (r["outcome"] != "TP" and not r["outcome"].startswith("SL")):
            continue
        d = f["det_i"]; buy = f["side"] == "long"
        i = bisect.bisect_right(z_et, et[d]) - 1          # last COMPLETED 240x/4h bucket at the pivot
        if i < 0:
            continue
        low, vlo, vhi, high = zones[i]
        inzone = (cl[d] <= vlo) if buy else (cl[d] >= vhi)   # buy->buyer wick / sell->seller wick
        rows.append(dict(tp=(r["outcome"] == "TP"), buy=buy, inzone=inzone))

    def rate(g): return 100.0 * sum(x["tp"] for x in g) / len(g) if g else float("nan")
    yes = [x for x in rows if x["inzone"]]; no = [x for x in rows if not x["inzone"]]
    a = sum(x["tp"] for x in yes); b = len(yes) - a; c = sum(x["tp"] for x in no); d = len(no) - c
    print("PIVOT-4HZONE: n=%d setups | baseline TP %.1f%%" % (len(rows), rate(rows)))
    print("  pivot IN its 240x buy/sell zone : YES n=%d TP %.1f%% | NO n=%d TP %.1f%% | Fisher p=%.4f"
          % (len(yes), rate(yes), len(no), rate(no), fisher(a, b, c, d)))
    for s, bl in (("long", True), ("short", False)):
        g = [x for x in rows if x["buy"] == bl]; gy = [x for x in g if x["inzone"]]
        print("  %-5s in-zone n=%d TP %.1f%% | out n=%d TP %.1f%%"
              % (s, len(gy), rate(gy), len(g) - len(gy), rate([x for x in g if not x["inzone"]])))


if __name__ == "__main__":
    main()
