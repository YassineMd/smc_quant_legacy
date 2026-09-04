"""DAEMON OOS — the weekday-NY 1m-confirm + E/E/C zone candidate on TRULY UNSEEN data (Jul-Aug 2026).

Faithful extension of study/radarrun_confirm_1m.py, same mechanics, virgin data:
  * PARENT = 30m VOLUME BUCKET union-persist replay on the DAEMON archive (study/archive_data/30m),
    Jul-Aug 2026 only — same scale as the recon canonical parent, built here (per-close trailing
    W=2000 detect, first-appearance freeze).
  * CHILD confirm + E/E/C zone + RESOLUTION = the RECONSTRUCTED 1m CLOCK candles for the same period
    (study/clock_daemon_oos/1m), built by study/clock_recon_daemon_oos.py from raw Binance aggTrades
    through the production ClockEngine — the SAME faithful pipeline that made the recon clock archive.
  * NY weekday sessions (13-21 UTC, Mon-Fri); parent entry + parent badge SL; exits 0.2/0.4 fix +
    RR 1/1.5/2; 1m first-touch ties-against; fees 0.04% RT + 0.03% slip/leg; non-overlap taken().
PRE-REGISTERED CELLS (frozen from the full-run report): CONFIRMED, C+Z-HYP (short@expensive /
long@cheap), C+Z-ANTI, UNCONF. Grades the ABSOLUTE edge (was selection-inflated on recon) AND the
RELATIVE HYP-vs-ANTI separation (survived the recon holdout). This is the decisive gate.
python study/radarrun_confirm_daemon_oos.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, W1, SLBUF, EXITS, OUT
from study.radarrun_confirm_1m import eec_zones

OOS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clock_daemon_oos")
CONF_CAP = 600
JUL1 = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
SEP1 = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()   # Aug end (Sep not built)


def build_parent_union(A30, keep_from):
    """30m daemon-bucket union-persist fires (per-close W=2000 detect, first-appearance). The FULL
    daemon series (incl. the ~June 20 warmup) drives detection so early-July walls are causally
    warm; only fires whose bar starts at/after keep_from are returned."""
    from app import config, radar_breakout_detect as RB
    seen = {}
    for k in range(1, len(A30)):
        lo = max(0, k - 2000)
        for g in RB.detect(A30[lo:k + 1], skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"])
            key = (b, g["side"])
            if key not in seen and _f(A30[b].get("start_time")) >= keep_from:
                seen[key] = (b, _f(A30[b].get("end_time")), int(g["side"]),
                             float(g["entry"]), float(g["sl_trade"]))
        if k % 500 == 0:
            print("  parent union: %d/%d closes, %d badges" % (k, len(A30), len(seen)), flush=True)
    byet = {}                                          # terminal persists keyed by end_time
    for rec in sorted(seen.values(), key=lambda r: (r[0],)):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    return sorted(byet.values())


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    from app import config, radar_breakout_detect as RB
    t0 = time.time()
    print("DAEMON OOS — weekday-NY 1m-confirm + E/E/C zone | Jul-Aug 2026 (virgin) | parent 30m "
          "daemon bucket, child reconstructed 1m clock\n", flush=True)

    # 1m clock OOS arrays (child detect + zone + resolution)
    A1 = sorted(load_archive("1m", root=OOS_ROOT, drop_degenerate=False)[1],
                key=lambda b: _f(b.get("start_time", 0)))
    A1 = [b for b in A1 if JUL1 <= _f(b.get("start_time")) < SEP1]
    T1S = np.array([_f(b.get("start_time")) for b in A1])
    H1 = np.array([_f(b.get("high")) for b in A1])
    L1 = np.array([_f(b.get("low")) for b in A1])
    C1 = np.array([_f(b.get("close", b.get("close_price"))) for b in A1])
    print("1m clock OOS bars: %d  (%s -> %s)" % (len(A1),
          datetime.fromtimestamp(T1S[0], tz=timezone.utc).strftime("%Y-%m-%d"),
          datetime.fromtimestamp(T1S[-1], tz=timezone.utc).strftime("%Y-%m-%d")), flush=True)
    zone1m = eec_zones(C1, H1, L1)

    # 30m daemon-bucket parents: FULL daemon series for warm walls, keep only Jul-Aug fires
    A30 = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    A30 = [b for b in A30 if _f(b.get("start_time")) < SEP1]        # keep the June warmup, drop Sep+
    fires = build_parent_union(A30, JUL1)
    starts = {int(b): _f(A30[b].get("start_time")) for (b, et, s, e, sl) in fires}
    print("parent union badges: %d" % len(fires), flush=True)

    def ny_weekday(et):
        d = datetime.fromtimestamp(et, tz=timezone.utc)
        return d.weekday() < 5 and 13 * 3600 <= (et % 86400) < 21 * 3600
    fires = [f for f in fires if ny_weekday(f[1])]
    print("weekday-NY parents: %d\n" % len(fires), flush=True)

    A1d = list(A1)
    del A1
    trades = []
    nconf = 0
    for pi, (b, et, s, e, sl) in enumerate(fires):
        st = starts[int(b)]
        j0 = int(np.searchsorted(T1S, st - 0.5))
        confirmed = False
        cz = -1
        seen = set()
        for j in range(j0, min(len(T1S), j0 + CONF_CAP)):
            if T1S[j] + 60.0 > et + 1e-6:
                break
            lo = max(0, j - W1)
            hits = []
            for g in RB.detect(A1d[lo:j + 1], skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
                bb = lo + int(g["i"])
                key = (bb, g["side"])
                if key in seen or bb < j0 or bb > j:
                    continue
                seen.add(key)
                if g["side"] == s:
                    hits.append(bb)
            if hits:
                confirmed = True
                cz = int(zone1m[min(hits)])
                break
        nconf += int(confirmed)
        trades.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), conf=confirmed, cz=cz))
        if pi % 100 == 0:
            print("  confirm replay %d/%d (%.0fs)" % (pi, len(fires), time.time() - t0), flush=True)
    del A1d
    json.dump(trades, open(os.path.join(OUT, "rr_confirm_daemon_oos_trades.json"), "w"))
    print("\nconfirmed: %d/%d (%.0f%%)\n" % (nconf, len(trades), 100 * nconf / max(1, len(trades))),
          flush=True)

    zhyp = lambda x: x["conf"] and ((x["s"] < 0 and x["cz"] == 3) or (x["s"] > 0 and x["cz"] == 1))
    anti = lambda x: x["conf"] and ((x["s"] > 0 and x["cz"] in (3, 4)) or (x["s"] < 0 and x["cz"] in (0, 1)))
    print("=" * 132, flush=True)
    print("DAEMON OOS RESULT (Jul-Aug 2026, virgin) — parent bracket, weekday NY", flush=True)
    for tag, sel in (("ALL", lambda x: True), ("CONFIRMED", lambda x: x["conf"]),
                     ("C+Z-HYP", zhyp), ("C+Z-ANTI", anti), ("UNCONF", lambda x: not x["conf"])):
        sub = [x for x in trades if sel(x)]
        for ename, kind, val in EXITS:
            report_cell("OOS %s" % tag, ename, sub, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
