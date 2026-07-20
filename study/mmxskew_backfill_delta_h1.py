"""MMXSKEW delta_h1 BACKFILL — stamp the sub-bucket first-half delta onto HISTORICAL 1h buckets so the
terminal's da2 gate works on the past (going forward, the daemon writes delta_h1 live).

delta_h1 = the running (buy_vol - sell_vol) at the 50%-volume mark of a bucket. For a historical 1h bucket we
reconstruct it from the 1m buckets inside its [start,end], exactly matching study/mm_skew_subbucket.py. Coverage
is bounded by 1m availability: the LOCAL archive (study/archive_data/1m) has ~22 days; the VM DB has only ~4.
So run COMPUTE locally (archive) then APPLY on the VM.

TWO steps (never touch the live DB while the daemon runs):
  1. LOCAL:  python study/mmxskew_backfill_delta_h1.py compute study/out/delta_h1_backfill.json
             -> reads the local 1h + 1m archive, writes {start_time: delta_h1} for every reconstructable bucket.
  2. ON VM (daemon STOPPED):
             python mmxskew_backfill_delta_h1.py apply <history.db> delta_h1_backfill.json
             -> UPDATEs each 1h closed_buckets row's data JSON to add delta_h1 (only where currently missing).
     Then restart the daemon: rehydrate reads delta_h1 back (persistence fix required) -> permanent.

REQUIRES the persistence fix (delta_h1 in _bucket_to_dict/_from_dict) to be deployed FIRST, else the daemon
drops delta_h1 on the next rehydrate and the backfill is lost.
"""
import os, sys, json


def _delta_h1(subs):
    """Running (buy-sell) at the first crossing of 50% cumulative volume. None if too few sub-buckets."""
    vols = [float(x.get("curr_vol", 0.0)) for x in subs]
    dels = [float(x.get("buy_vol", 0.0)) - float(x.get("sell_vol", 0.0)) for x in subs]
    tot = sum(vols)
    if tot <= 0 or len(subs) < 12:
        return None
    half = 0.5 * tot; cum = 0.0; run = 0.0
    for v, d in zip(vols, dels):
        run += d; cum += v
        if cum >= half:
            return run
    return run


def compute(out_path):
    import bisect
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from study.archive_loader import load_archive
    _, h1, _ = load_archive("1h")
    _, m1, _ = load_archive("1m")
    subs = sorted(m1, key=lambda r: float(r.get("start_time", 0.0)))
    ss = [float(r.get("start_time", 0.0)) for r in subs]
    vals = {}; done = skip = 0
    for b in h1:
        s0 = float(b.get("start_time", 0.0)); s1 = float(b.get("end_time", 0.0))
        j = bisect.bisect_left(ss, s0); cons = []
        while j < len(subs) and ss[j] <= s1:
            cons.append(subs[j]); j += 1
        dh1 = _delta_h1(cons)
        if dh1 is None:
            skip += 1; continue
        vals["%.3f" % s0] = dh1; done += 1
    json.dump(vals, open(out_path, "w"))
    print("computed delta_h1 for %d 1h buckets (%d skipped: no/insufficient 1m coverage) -> %s"
          % (done, skip, out_path))


def apply(db_path, vals_path):
    import sqlite3
    vals = json.load(open(vals_path))
    c = sqlite3.connect(db_path)
    rows = c.execute("SELECT id, start_time, data FROM closed_buckets WHERE tf='1h'").fetchall()
    upd = had = miss = 0
    c.execute("BEGIN")
    for _id, st, data in rows:
        key = "%.3f" % float(st)
        if key not in vals:
            miss += 1; continue
        d = json.loads(data)
        if d.get("delta_h1") is not None:
            had += 1; continue
        d["delta_h1"] = vals[key]
        c.execute("UPDATE closed_buckets SET data=? WHERE id=?", (json.dumps(d, separators=(",", ":")), _id))
        upd += 1
    c.commit(); c.close()
    print("APPLIED: %d rows backfilled, %d already had delta_h1, %d 1h rows with no computed value (untouched)."
          % (upd, had, miss))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "compute":
        compute(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "apply":
        apply(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        print("usage:\n  compute <out.json>\n  apply <history.db> <values.json>")
        sys.exit(1)


if __name__ == "__main__":
    main()
