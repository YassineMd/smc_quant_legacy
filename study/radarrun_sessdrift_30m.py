"""SESSION DRIFT x RADAR RUNNER, 30m bucket — HONEST test (user 2026-08-25: take only RadarRun positions that
GO WITH the session drift tag). Canonical harness: cached union fire sets, 1m first-touch, non-overlap,
fees+slip, prop MC; recon per-year + DAEMON OOS decisive. Exits: 0.5% net + RR 1:1.

PRE-REGISTERED (frozen; no iteration):
  DRIFT AS OF THE FIRE BAR (causal, = what the session tag shows at that moment): session = canonical window
  containing the fire bar's start (Tokyo 00-08 / London 08-13 / NY 13-21 UTC; 21-24 -> NO session, excluded);
  p1 = the FIRST session bar's POC (poc_price, fallback bar midpoint); hi/lo = session range from the session
  start UP TO AND INCLUDING the fire bar. LOW drift when (p1 - lo) > (hi - p1); HIGH when mirror; tie -> none.
  WITH-DRIFT = SHORT during LOW drift / LONG during HIGH drift; AGAINST = opposite; none/no-session excluded.
  CELLS: ALL control / WITH / AGAINST / WITH-LONG / WITH-SHORT. python study/radarrun_sessdrift_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
SESSIONS = ((0, 8), (8, 13), (13, 21))


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def drift_at(A, b_idx):
    """'LOW'/'HIGH'/None as the session tag reads at bucket b_idx's close (causal range-so-far)."""
    st = _f(A[b_idx], "start_time")
    d = datetime.fromtimestamp(st, timezone.utc)
    win = next(((h0, h1) for h0, h1 in SESSIONS if h0 <= d.hour < h1), None)
    if win is None:
        return None
    s0 = d.replace(hour=win[0], minute=0, second=0, microsecond=0).timestamp()
    j = b_idx
    while j > 0 and _f(A[j - 1], "start_time") >= s0:
        j -= 1
    seg = A[j:b_idx + 1]
    if not seg:
        return None
    p1 = _f(seg[0], "poc_price")
    if p1 <= 0:
        p1 = 0.5 * (_f(seg[0], "high") + _f(seg[0], "low"))
    hi = max(_f(x, "high") for x in seg); lo = min(_f(x, "low") for x in seg)
    if p1 <= 0 or hi <= lo:
        return None
    dl = p1 - lo; dh = hi - p1
    return "LOW" if dl > dh else ("HIGH" if dh > dl else None)


def features(fires, A):
    recs = []
    for f in fires:
        b = int(f[0])
        dr = drift_at(A, b)
        recs.append(dict(f=tuple(f), s=int(f[2]), dr=dr))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WITH-DRIFT", lambda r: (r["s"] < 0 and r["dr"] == "LOW") or (r["s"] > 0 and r["dr"] == "HIGH")),
    ("AGAINST-DRIFT", lambda r: (r["s"] > 0 and r["dr"] == "LOW") or (r["s"] < 0 and r["dr"] == "HIGH")),
    ("WITH-LONG", lambda r: r["s"] > 0 and r["dr"] == "HIGH"),
    ("WITH-SHORT", lambda r: r["s"] < 0 and r["dr"] == "LOW"),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    nno = sum(1 for r in recs if r["dr"] is None)
    print("  (no session / tie: %d fires excluded from conditioned cells)" % nno, flush=True)
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-14s %-8s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f as _ff
    from study.radarrun_honest_deltapct_tp import load_fires
    print("SESSION DRIFT x RADAR RUNNER 30m BUCKET — canonical harness | drift causal at the fire bar | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _ff(b.get("start_time", 0)))
    T1 = np.array([_ff(b.get("start_time")) for b in A1]); H1 = np.array([_ff(b.get("high")) for b in A1]); L1 = np.array([_ff(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _ff(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _ff(b.get("start_time", 0)))
    Td = np.array([_ff(b.get("start_time")) for b in Ad1]); Hd = np.array([_ff(b.get("high")) for b in Ad1]); Ld = np.array([_ff(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _ff(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
