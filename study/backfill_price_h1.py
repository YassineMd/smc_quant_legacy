"""price_h1 BACKFILL — stamp the 50%-volume-mark PRICE onto historical buckets so the terminal's per-half
absorption residual (R h1/h2) works on the past. Going forward the daemon writes price_h1 live (since the
2026-07-22 13:40 UTC restart); this fills everything before it, ONCE, so nothing recomputes from 1m per frame.

Mirrors study/mmxskew_backfill_delta_h1.py exactly — same 50%-volume crossing, same two-step protocol, same
"only where currently missing" rule. price_h1 is the CLOSE of the 1m sub-bucket at that crossing, i.e. the
price at the same instant delta_h1 is measured, so the two together give h1 and h2 each an (effort, result)
pair. Coverage is bounded by 1m availability: the LOCAL archive has ~24 days, the VM DB far less — so run
COMPUTE locally and APPLY on the VM.

TWO steps (never touch the live DB while the daemon is writing):
  1. LOCAL:  python study/backfill_price_h1.py compute study/out/price_h1_backfill.json
  2. ON VM (daemon STOPPED):
             python backfill_price_h1.py apply <history.db> price_h1_backfill.json
     Then restart the daemon: rehydrate reads price_h1 back -> permanent.

REQUIRES the persistence fix (price_h1 in _bucket_to_dict/_from_dict/bucket_from_snapshot) deployed FIRST,
else the daemon drops the field on the next rehydrate and the backfill is silently lost. That shipped in
commit 1831ea0 and is live.

ALL TIMEFRAMES, not just 1h: the terminal can chart 1m/5m/15m/1h/4h and the readout should work on each.
A bucket is keyed by (tf, start_time) since start_time alone is not unique across timeframes.
"""
import os, sys, json


def _split_price(subs):
    """(delta_h1, price_h1) at the first crossing of 50% cumulative volume, or (None, None).

    price_h1 = close of the sub-bucket that crosses the mark — the price at the same instant delta_h1 is taken.
    Requires >= 12 sub-buckets (matching the delta_h1 backfill) so the crossing is not an artefact of 2-3 bars."""
    if len(subs) < 12:
        return None, None
    vols = [float(x.get("curr_vol", 0.0) or 0.0) for x in subs]
    tot = sum(vols)
    if tot <= 0:
        return None, None
    half = 0.5 * tot
    cum = 0.0
    run = 0.0
    for x, v in zip(subs, vols):
        run += float(x.get("buy_vol", 0.0) or 0.0) - float(x.get("sell_vol", 0.0) or 0.0)
        cum += v
        if cum >= half:
            px = float(x.get("close_price", 0.0) or 0.0)
            return (run, px) if px > 0 else (None, None)
    return None, None


def compute(out_path):
    import bisect
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from study.archive_loader import load_archive

    _, M, _ = load_archive("1m")
    mst = [float(x.get("start_time", 0.0)) for x in M]
    out = {}
    # 1m has no finer stream to split it with. 5m is EXCLUDED BY DESIGN: a 5m volume bucket holds only ~5
    # one-minute buckets, so a 50%-volume crossing would rest on ~5 samples — too coarse to call a "half"
    # (measured: only 2 of 15499 clear the >=12 sub-bucket floor, i.e. it never really worked there anyway).
    for tf in ("15m", "1h", "4h"):
        try:
            _, H, _ = load_archive(tf)
        except Exception:
            continue
        made = 0
        for b in H:
            st = float(b.get("start_time", 0.0) or 0.0)
            et = float(b.get("end_time", 0.0) or 0.0)
            if st <= 0 or et <= st:
                continue
            a = bisect.bisect_left(mst, st)
            z = bisect.bisect_left(mst, et)
            d1, px = _split_price(M[a:z])
            if px is None:
                continue
            out["%s|%.3f" % (tf, st)] = [d1, px]
            made += 1
        print("  %-4s %d/%d buckets reconstructable" % (tf, made, len(H)))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    json.dump(out, open(out_path, "w"), separators=(",", ":"))
    print("COMPUTED: %d (tf,start_time) -> [delta_h1, price_h1] pairs -> %s" % (len(out), out_path))


def apply(db_path, vals_path, dry=False):
    import sqlite3
    vals = json.load(open(vals_path))
    c = sqlite3.connect(db_path)
    rows = c.execute("SELECT id, tf, start_time, data FROM closed_buckets").fetchall()
    upd = had = miss = dmis = 0
    if not dry:
        c.execute("BEGIN")
    for _id, tf, st, data in rows:
        v = vals.get("%s|%.3f" % (tf, float(st)))
        if v is None:
            miss += 1
            continue
        d = json.loads(data) if isinstance(data, str) else data
        if d.get("price_h1") is not None:
            had += 1
            continue
        d["price_h1"] = v[1]
        if d.get("delta_h1") is None and v[0] is not None:
            d["delta_h1"] = v[0]                       # fill delta_h1 too when it is also missing
            dmis += 1
        if not dry:
            c.execute("UPDATE closed_buckets SET data=? WHERE id=?",
                      (json.dumps(d, separators=(",", ":")), _id))
        upd += 1
    if dry:
        c.close()
        print("DRY RUN: would backfill %d rows (%d already had price_h1, %d rows with no computed value)."
              % (upd, had, miss))
        return
    c.commit(); c.close()
    print("APPLIED: %d rows backfilled (%d also got delta_h1), %d already had price_h1, %d untouched."
          % (upd, dmis, had, miss))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "compute":
        compute(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "apply":
        apply(sys.argv[2], sys.argv[3], dry=("--dry" in sys.argv))
    else:
        print(__doc__)
        print("usage:\n  compute <out.json>\n  apply <history.db> <values.json> [--dry]")
        sys.exit(1)


if __name__ == "__main__":
    main()
