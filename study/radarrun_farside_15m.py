"""FAR-SIDE BIAS x RADAR RUNNER, 15m bucket — HONEST test (user 2026-09-03: "after 15:24Z take the
first RR that goes with the bias?"). Canonical harness basis: union fire sets (recon cached via
load_fires; daemon cached rr_union_b15m_daemon_m30.json), 1-MINUTE first-touch, non-overlap
taken(), fees 0.04% RT + 0.03% slip/leg; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): far-side bias per UTC day at cp = 15:24Z from 1m data
(NY session 13:00Z→cp: hi/lo/last close; far = LOW if close >= mid -> bias LONG, else SHORT —
identical to study/ny_farside_strategy_1m). CELLS: ALL (control) / WINDOW (fires with
15:24Z <= end_time < 21:00Z, any side) / ALIGNED (window & side == bias) / AGAINST (window &
side != bias). EXITS: canonical 0.2% net / 0.4% net / RR 1:0.5 / RR 1:1.
PREDICTION ON RECORD (from the dead session-drift family): ALIGNED <= AGAINST <= 0.
python study/radarrun_farside_15m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]
CP = 15 * 3600 + 24 * 60
S0 = 13 * 3600
SEND = 21 * 3600


def farside_by_day(rows_1m):
    """{utc_day_epoch: +1 (bias LONG, far=LOW) / -1} from 1m rows (t_end, high, low, close)."""
    from collections import defaultdict
    by_day = defaultdict(list)
    for t, h, l, c in rows_1m:
        sod = t % 86400
        if S0 < sod <= CP and h > 0 and l > 0 and c > 0:
            by_day[int(t - sod)].append((t, h, l, c))
    out = {}
    for d, v in by_day.items():
        if len(v) < 60:
            continue
        v.sort()
        hi = max(x[1] for x in v); lo = min(x[2] for x in v); last = v[-1][3]
        if hi > lo:
            out[d] = 1 if last >= (hi + lo) / 2.0 else -1
    return out


def features(fires, A, bias):
    from study.candle_bias_1h import _f
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        et = _f(A[b].get("end_time"))
        sod = et % 86400
        day = int(et - sod)
        in_win = CP <= sod < SEND
        bs = bias.get(day)
        recs.append(dict(f=tuple(f), s=s, win=in_win, bias=bs))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WINDOW", lambda r: r["win"]),
    ("ALIGNED", lambda r: r["win"] and r["bias"] is not None and r["s"] == r["bias"]),
    ("AGAINST", lambda r: r["win"] and r["bias"] is not None and r["s"] != r["bias"]),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-8s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("FAR-SIDE BIAS x RADAR RUNNER 15m — canonical harness | bias at 15:24Z (far=LOW -> LONG) | "
          "window 15:24-21:00Z | prediction: ALIGNED <= AGAINST <= 0\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    rows = [( _f(b.get("end_time")) or (_f(b.get("start_time")) + 60), _f(b.get("high")),
             _f(b.get("low")), _f(b.get("close", b.get("close_price")))) for b in A1]
    bias_recon = farside_by_day(rows)
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1])
    L1 = np.array([_f(b.get("low")) for b in A1])
    del A1, rows
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows) | bias days=%d" % len(bias_recon), flush=True)
    A = sorted(load_archive("15m", root="study/recon_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "15m"), A, bias_recon), T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 15m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    rows = [( _f(b.get("end_time")) or (_f(b.get("start_time")) + 60), _f(b.get("high")),
             _f(b.get("low")), _f(b.get("close", b.get("close_price")))) for b in Ad1]
    bias_d = farside_by_day(rows)
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1])
    Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1, rows
    print("bias days (daemon)=%d" % len(bias_d), flush=True)
    Ad = sorted(load_archive("15m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b15m_daemon_m30.json")))
    report(features(frd, Ad, bias_d), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
