"""LONG WICK x SESSION DRIFT — 30m bucket HONEST test (user 2026-08-26): the wall-bound Long Wick signal
(shipped v2 geometry, trailing-2000 walls) conditioned on the CORRECTED first-bar session drift tag.

PRE-REGISTERED: drift per fire = the session containing the fire bar (Tokyo 00-08 / London 08-13 / NY 13-21
UTC; 21-24 -> no tag, excluded): first session bar's POC vs THAT BAR's own high/low — LOW drift when
(POC - low1) > (high1 - POC), HIGH when mirror (fixed from bar 1's close, causal). WITH-DRIFT = SHORT in LOW
drift / LONG in HIGH drift; AGAINST = opposite. Cells: ALL / WITH / AGAINST x the full TP ladder
(0.2/0.25/0.3/0.4/0.5% net + RR 1:1). Harness identical to longwick_30mbkt_honest (entry close, SL 0.1%
beyond the extreme, 1m first-touch, non-overlap, fees+slip, prop MC); recon per-year + DAEMON OOS.
python study/longwick_sessdrift_30m.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.longwick_30mbkt_honest import signals, TPS

SESSIONS = ((0, 8), (8, 13), (13, 21))


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def drift_map(A):
    """Per-bucket drift tag via the corrected FIRST-BAR rule (None outside sessions)."""
    out = [None] * len(A)
    cur_key = None; cur_tag = None
    for i, b in enumerate(A):
        st = _f(b, "start_time")
        d = datetime.fromtimestamp(st, timezone.utc)
        win = next(((h0, h1) for h0, h1 in SESSIONS if h0 <= d.hour < h1), None)
        if win is None:
            cur_key = None
            continue
        key = (d.date(), win)
        if key != cur_key:                          # first bucket of this session -> fix the tag from THIS bar
            h1v = _f(b, "high"); l1v = _f(b, "low")
            p1 = _f(b, "poc_price") or (0.5 * (h1v + l1v))
            if h1v > l1v > 0 and p1 > 0:
                dl = p1 - l1v; dh = h1v - p1
                cur_tag = "LOW" if dl > dh else ("HIGH" if dh > dl else None)
            else:
                cur_tag = None
            cur_key = key
        out[i] = cur_tag
    return out


def report(fires, A, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    dm = drift_map(A)
    recs = [dict(f=f, s=int(f[2]), dr=dm[int(f[0])]) for f in fires]
    nno = sum(1 for r in recs if r["dr"] is None)
    print("  (no session / tie: %d fires excluded from conditioned cells)" % nno, flush=True)
    cells = [("ALL", lambda r: True),
             ("WITH-DRIFT", lambda r: (r["s"] < 0 and r["dr"] == "LOW") or (r["s"] > 0 and r["dr"] == "HIGH")),
             ("AGAINST-DRIFT", lambda r: (r["s"] > 0 and r["dr"] == "LOW") or (r["s"] < 0 and r["dr"] == "HIGH"))]
    for name, keep in cells:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-14s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f as _ff
    print("LONG WICK x SESSION DRIFT — 30m BUCKET | first-bar drift (corrected) | canonical harness | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _ff(b.get("start_time", 0)))
    T1 = np.array([_ff(b.get("start_time")) for b in A1]); H1 = np.array([_ff(b.get("high")) for b in A1]); L1 = np.array([_ff(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _ff(b.get("start_time", 0)))
    report(signals(daemon=False), A, T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _ff(b.get("start_time", 0)))
    Td = np.array([_ff(b.get("start_time")) for b in Ad1]); Hd = np.array([_ff(b.get("high")) for b in Ad1]); Ld = np.array([_ff(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _ff(b.get("start_time", 0)))
    report(signals(daemon=True), Ad, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
