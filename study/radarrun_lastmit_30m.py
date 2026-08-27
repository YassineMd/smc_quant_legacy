"""LAST-MITIGATED S/R x RADAR RUNNER, 30m bucket — HONEST test (user 2026-08-27: current-TF S/R only;
if the LAST MITIGATED level is a RESISTANCE take only LONG fires, if a SUPPORT take only SHORT fires).
Canonical harness: cached union fire sets, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT +
0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): app/support_resistance.detect on the SAME 30m series the fires
index into (k=8 fractal pivots, strict close-through mitigation, zone_mitigation=False — the shipped 30m
indicator's geometry; ALL levels, no display truncation). CAUSAL: a break can only occur AFTER the pivot's
k-bar confirmation (a close above hp inside p+1..p+k would contradict the fractal), so every mitigation
event (i1, kind) is known at bar i1's close. Tag at fire b = kind of the mitigation event with the largest
i1 <= b (no recency cutoff; i1 == b counts — both are close events); if the latest bar mitigated BOTH kinds
at once, or no mitigation exists yet, tag = NONE (excluded from conditioned cells).
RULE = LONG iff tag == R / SHORT iff tag == S. CELLS: ALL control / WITH-MIT (the rule) / AGAINST-MIT /
WITH-LONG / WITH-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_lastmit_30m.py"""
import os, sys, json, time, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def features(fires, A):
    from app import support_resistance as srm
    levels = srm.detect(A)                                   # full-series, k=8, strict mitigation
    by_i1 = {}
    for lv in levels:
        if lv["i1"] is not None:
            by_i1.setdefault(int(lv["i1"]), set()).add(lv["kind"])
    idxs = sorted(by_i1)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        j = bisect.bisect_right(idxs, b) - 1
        tag = None
        if j >= 0:
            kinds = by_i1[idxs[j]]
            tag = next(iter(kinds)) if len(kinds) == 1 else None   # both kinds broken same bar -> ambiguous
        recs.append(dict(f=tuple(f), s=s,
                         with_mit=(tag == "R" and s > 0) or (tag == "S" and s < 0),
                         against=(tag == "S" and s > 0) or (tag == "R" and s < 0)))
    nno = sum(1 for r in recs if not r["with_mit"] and not r["against"])
    print("  (no mitigation yet / ambiguous: %d fires excluded from conditioned cells)" % nno, flush=True)
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WITH-MIT", lambda r: r["with_mit"]),
    ("AGAINST-MIT", lambda r: r["against"]),
    ("WITH-LONG", lambda r: r["with_mit"] and r["s"] > 0),
    ("WITH-SHORT", lambda r: r["with_mit"] and r["s"] < 0),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-12s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("LAST-MITIGATED S/R x RADAR RUNNER 30m BUCKET — canonical harness | broken R -> long only / broken S -> short only | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
