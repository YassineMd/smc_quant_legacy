"""Generate study/recon_archive/30m/ so the terminal can REPLAY the 30m timeframe. The recon reconstruction shipped
1m/5m/15m/1h/4h but no 30m; this builds it by NATIVE volume-accumulation from the recon 15m (the exact construction
validated in study/radarrun_30m_native.py — cut a 30m bucket each time cumulative curr_vol >= 2x the 15m target),
faithfully aggregating every field, and writes it in the recon_archive wire format (jsonl.gz chunks of {"bid","data"}
that app.recon_replay loads via _bucket_from_dict().full_snapshot()). Chunked by ~1000 buckets so windowed replay loads
stay cheap. Idempotent (rewrites the 30m/ dir). recon_archive is git-ignored, so the data isn't committed.
Usage: python study/build_recon_30m.py"""
import os, sys, json, gzip, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from study.backfill_30m_history import build_30m       # native 15m->30m volume-accumulation (all fields faithful)

CHUNK = 1000
OUT = "study/recon_archive/30m"


def main():
    print("loading recon 15m ...", flush=True)
    A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: float(b.get("start_time", 0) or 0))
    print("  %d 15m buckets  %s -> %s" % (len(A15),
          A15[0].get("start_time"), A15[-1].get("start_time")), flush=True)
    d30, T = build_30m(A15)
    print("built %d native-30m buckets (T=%.0f = 2x median 15m target)" % (len(d30), T), flush=True)

    os.makedirs(OUT, exist_ok=True)
    for old in glob.glob(os.path.join(OUT, "30m_*.jsonl.gz")):        # idempotent: clear any prior 30m chunks
        os.remove(old)
    bid = 0; nch = 0
    for ci in range(0, len(d30), CHUNK):
        path = os.path.join(OUT, "30m_%04d.jsonl.gz" % nch); nch += 1
        with gzip.open(path, "wt", encoding="utf-8") as gz:
            for b in d30[ci:ci + CHUNK]:
                gz.write(json.dumps({"bid": bid, "data": b}, separators=(",", ":")) + "\n"); bid += 1
    print("wrote %d buckets in %d chunks -> %s/" % (len(d30), nch, OUT), flush=True)

    # ---- verify: load_archive + recon_replay availability + full round-trip through the replay loader ----
    bids, raws, gaps = load_archive("30m", root="study/recon_archive")
    from app import recon_replay
    from app.persistence import _bucket_from_dict
    recon_replay.invalidate()
    snap = _bucket_from_dict(raws[len(raws) // 2]).full_snapshot()
    e = recon_replay.earliest_start("30m"); l = recon_replay.latest_start("30m")
    from datetime import datetime, timezone
    print("\nVERIFY:")
    print("  load_archive('30m'): %d buckets, %d gaps" % (len(raws), len(gaps)))
    print("  recon_replay.available('30m') = %s   earliest=%s  latest=%s" % (
        recon_replay.available("30m"),
        datetime.fromtimestamp(e, tz=timezone.utc) if e else None,
        datetime.fromtimestamp(l, tz=timezone.utc) if l else None))
    print("  sample bucket full_snapshot() OK — keys: open=%s close=%s high=%s levels=%d" % (
        snap.get("open"), snap.get("close"), snap.get("high"), len(snap.get("levels") or {})))


if __name__ == "__main__":
    main()
