"""Backfill 30m closed-bucket history by volume-accumulating the stored 15m buckets (the native construction validated
in study/radarrun_30m_native.py). Faithfully aggregates every field a QuantBucket carries EXCEPT the truly path-dependent
ones (cvd wick / per-tick E/R / delta_h1), which _bucket_from_dict reads with graceful defaults ("pre-feature row" ->
plain body / '--'). So historical 30m renders candles + footprint + Radar Runner correctly.

MODES:
  test              -> build 30m from recon_archive 15m, round-trip through app.persistence._bucket_from_dict, run the
                       wall detector, and report (NO writes). Proves the aggregation + the daemon loader accept it.
  run  <history.db> -> read 15m from closed_buckets, build 30m, BACKUP the db, then (idempotently) replace tf='30m'
                       rows + set meta total_closed_30m. DAEMON MUST BE STOPPED. Reversible via the .bak or
                       DELETE FROM closed_buckets WHERE tf='30m'.
Usage: python study/backfill_30m_history.py test   |   python study/backfill_30m_history.py run /path/to/history.db"""
import os, sys, json, sqlite3, shutil, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # for `test` mode's app/ imports

_ADDITIVE =("buy_vol", "sell_vol", "curr_vol", "churn", "opL", "opS", "clL", "clS", "up_ticks", "dn_ticks")
_SZ = ("sz_cb", "sz_cs", "sz_vb", "sz_vs")


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _fresh(b):
    a = {k: _f(b.get(k)) for k in _ADDITIVE}
    a["open_price"] = _f(b.get("open_price")); a["close_price"] = _f(b.get("close_price"))
    a["high"] = b.get("high"); a["low"] = b.get("low")
    a["target_vol"] = _f(b.get("target_vol"))
    a["start_time"] = b.get("start_time"); a["end_time"] = b.get("end_time")
    a["levels"] = {}
    a["liquidations"] = list(b.get("liquidations") or [])
    a["_sz"] = {k: list(b.get(k)) if isinstance(b.get(k), list) else None for k in _SZ}
    _merge_levels(a, b)
    return a


def _merge_levels(a, b):
    for p, vv in (b.get("levels") or {}).items():
        e = a["levels"].get(p)
        bb = _f(vv.get("b")); ss = _f(vv.get("s"))
        if e is None:
            a["levels"][p] = {"b": bb, "s": ss}
        else:
            e["b"] += bb; e["s"] += ss


def _merge(a, b):
    for k in _ADDITIVE:
        a[k] += _f(b.get(k))
    a["close_price"] = _f(b.get("close_price"))
    hb = b.get("high"); lb = b.get("low")
    if hb is not None:
        a["high"] = hb if a["high"] is None else max(a["high"], hb)
    if lb is not None:
        a["low"] = lb if a["low"] is None else min(a["low"], lb)
    a["target_vol"] += _f(b.get("target_vol"))
    a["end_time"] = b.get("end_time")
    a["liquidations"].extend(b.get("liquidations") or [])
    for k in _SZ:
        v = b.get(k)
        if isinstance(v, list):
            if a["_sz"][k] is None:
                a["_sz"][k] = list(v)
            elif len(a["_sz"][k]) == len(v):
                a["_sz"][k] = [x + y for x, y in zip(a["_sz"][k], v)]
    _merge_levels(a, b)


def _finalize(a):
    poc = None
    if a["levels"]:
        poc = float(max(a["levels"].items(), key=lambda kv: kv[1]["b"] + kv[1]["s"])[0])
    d = {"target_vol": a["target_vol"], "start_time": a["start_time"], "end_time": a["end_time"],
         "high": a["high"], "low": a["low"], "open_price": a["open_price"], "close_price": a["close_price"],
         "levels": a["levels"], "liquidations": a["liquidations"], "poc_price": poc,
         # path-dependent -> graceful defaults (render plain / '--')
         "cvd_hi": 0.0, "cvd_lo": 0.0, "delta_h1": None, "price_h1": None, "vel_ratio": 1.0,
         "buyer_er": 1.0, "seller_er": 1.0}
    for k in _ADDITIVE:
        d[k] = a[k]
    for k in _SZ:
        if isinstance(a["_sz"][k], list):
            d[k] = a["_sz"][k]
    return d


def build_30m(src):
    src = sorted(src, key=lambda b: _f(b.get("start_time")))
    tvs = [_f(b.get("target_vol")) for b in src if _f(b.get("target_vol")) > 0]
    T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in src]))
    out = []; acc = None
    for b in src:
        acc = _fresh(b) if acc is None else (_merge(acc, b) or acc)
        if acc["curr_vol"] >= T:
            out.append(_finalize(acc)); acc = None
    if acc is not None:
        out.append(_finalize(acc))
    return out, T


def test():
    from study.archive_loader import load_archive
    from app import absorption_level_detect as AL
    from app.persistence import _bucket_from_dict
    _, A15, _ = load_archive("15m", root="study/recon_archive")
    d30, T = build_30m(A15)
    print("built %d 30m buckets from %d 15m (T=%.0f = 2x median 15m target)" % (len(d30), len(A15), T))
    # round-trip through the daemon's own loader
    rt = [_bucket_from_dict(d) for d in d30]
    print("round-trip via _bucket_from_dict OK: %d QuantBuckets; sample close=%.4f high=%.4f levels=%d"
          % (len(rt), rt[-1].close_price, rt[-1].high, len(rt[-1].levels)))
    # detector must find walls + radar runs on the derived 30m (chunked like the studies)
    walls = 0; runs = 0; n = len(d30); c0 = 0
    while c0 < n:
        for w in AL.detect(d30[c0:min(n, c0 + 6000)], skip_last=False):
            walls += 1; runs += len(w.get("radar_runs", ()))
        if c0 + 6000 >= n:
            break
        c0 += 5000
    print("detector on derived 30m: walls=%d  radar_runs=%d  -> Radar Runner has structure to fire on. OK" % (walls, runs))


def run(db_path):
    if not os.path.exists(db_path):
        raise SystemExit("no db at %s" % db_path)
    bak = db_path + ".bak30m"
    shutil.copy(db_path, bak); print("backup -> %s" % bak)
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT data FROM closed_buckets WHERE tf='15m' ORDER BY id ASC").fetchall()
    src = [json.loads(r[0]) for r in rows]
    print("read %d stored 15m buckets" % len(src))
    if len(src) < 4:
        raise SystemExit("too few 15m buckets to build 30m; aborting")
    d30, T = build_30m(src)
    print("built %d 30m buckets (T=%.0f)" % (len(d30), T))
    with con:
        con.execute("DELETE FROM closed_buckets WHERE tf='30m'")     # idempotent
        con.executemany("INSERT INTO closed_buckets(tf,start_time,end_time,data) VALUES('30m',?,?,?)",
                        [(d.get("start_time"), d.get("end_time"), json.dumps(d)) for d in d30])
        con.execute("INSERT INTO meta(key,value) VALUES('total_closed_30m',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(len(d30)),))
    con.close()
    print("WROTE %d tf='30m' rows + meta total_closed_30m=%d. Restart the daemon; rehydrate should arm them."
          % (len(d30), len(d30)))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    if mode == "test":
        test()
    elif mode == "run":
        run(sys.argv[2])
    else:
        print(__doc__)
