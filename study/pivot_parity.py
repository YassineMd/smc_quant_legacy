"""Parity regression for app/pivot_detect.py — asserts the live PIVOT detector reproduces study/s5j_final.py's
S5j-r5 fire set on the frozen study tape. Run: python study/pivot_parity.py

Checks: 147 long fires (exact), the 3 regression anchors (20977 fires long / 14873 fails leg1 / 14876 fails
leg2), and short in {74,75}. The one-bar short slack is bid 12808 — a phase-argmax coin-flip (STARTDUR 48.27
vs END ~48.2); pivot_detect recomputes the posteriors and lands END-dominant, the frozen m10 parquet stored
STARTDUR-dominant. Not a logic divergence: long + anchors are bit-exact."""
import os, sys, json, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
from app import pivot_detect as PD                      # noqa: E402

DBS = ("study/data/history_snapshot_20260702.db", "study/data/history_snapshot_20260703.db")


def load_tape():
    by = {}
    for db in DBS:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(os.path.dirname(HERE), db), uri=True)
        raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
        tc = int(con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()[0]); con.close()
        base = tc - len(raw)
        for j, d in enumerate(raw):
            by[base + j + 1] = d
    bids = sorted(by)
    snaps = [_bucket_from_dict(by[b]).full_snapshot() for b in bids]
    return bids, snaps


def main():
    bids, snaps = load_tape()
    bid_i = {b: i for i, b in enumerate(bids)}
    fires = PD.detect_pivots(snaps)
    nL = sum(1 for f in fires if f["side"] == "long")
    nS = sum(1 for f in fires if f["side"] == "short")

    def fl(bkid):
        return any(f["det_i"] == bid_i[bkid] and f["side"] == "long" for f in fires)

    print("pivot_detect on %d bars: %d long, %d short fires" % (len(snaps), nL, nS))
    assert nL == 147, "long fires must be 147 (got %d)" % nL
    assert nS in (74, 75), "short fires must be 74/75 (got %d)" % nS
    assert fl(20977), "anchor 20977 must fire long"
    assert not fl(14873), "anchor 14873 must NOT fire long (fails leg1)"
    assert not fl(14876), "anchor 14876 must NOT fire long (fails leg2)"
    print("PARITY OK — 147 long exact, %d short, anchors 20977/14873/14876 all correct" % nS)


if __name__ == "__main__":
    main()
