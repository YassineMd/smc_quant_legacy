"""MMXSKEW v1.3 — split-half + Monte Carlo validation of the ASYMMETRIC-Δ-accel candidate.

v1.3 = the v1.1 signal MINUS the POC filter, + mov_mag >= 39 + **raw da2 > 0** (ASYMMETRIC: a LONG passes when
buying ACCELERATES into the close, a SHORT passes when selling DECELERATES / is absorbed — both are raw da2 > 0;
the old side-aligned form was backwards for shorts). da2 = (buy_vol - sell_vol - 2*delta_h1)/curr_vol, using the
daemon `delta_h1` field when the bucket carries it, else reconstructed from the 1m archive (>=12 sub-buckets).

build() / GATE() / taken() are imported by study/mmxskew_v13_forward_audit.py so the audit uses IDENTICAL logic.
In-sample, mined, one 30-day regime, 1m-covered signals only — forward tape is the real test.

Run:  python study/mm_skew_v13_validate.py
"""
from __future__ import annotations
import os, sys, bisect
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR
from study.archive_loader import load_archive

FEE = 0.0008
MM_MIN = 39.0


def sig(b):
    """v1.1 signal MINUS the close-vs-POC-baseline condition (dir + skew + eff-agg spread + long delta<15)."""
    if b.get("sk") is None:
        return 0
    if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and b["delta"] < 15:
        return 1
    if b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
        return -1
    return 0


def GATE(sg):
    """v1.3 gate: mov_mag >= 39 AND raw da2 > 0 (asymmetric — same threshold both sides)."""
    return sg["mm"] >= MM_MIN and sg["da2"] is not None and sg["da2"] > 0


def _da2_recon(subs):
    """Reconstruct da2 = (H2-H1)/vol from the 1m sub-buckets (running delta at the 50%-vol mark)."""
    vols = [float(x.get("curr_vol", 0)) for x in subs]
    dels = [float(x.get("buy_vol", 0)) - float(x.get("sell_vol", 0)) for x in subs]
    tot = sum(vols)
    if tot <= 0 or len(subs) < 12:
        return None
    half = 0.5 * tot; cum = 0.0; run = 0.0
    for v, d in zip(vols, dels):
        run += d; cum += v
        if cum >= half:
            return (tot - 2 * run) / tot
    return (tot - 2 * run) / tot


def build():
    """(A, sigs) — every v1.3-base signal tagged with mov_mag + da2 (delta_h1 exact when present, else 1m recon)."""
    A, first, _, _ = FM.build()
    _, m1, _ = load_archive("1m")
    subs = sorted(m1, key=lambda r: float(r.get("start_time", 0))); ss = [float(r.get("start_time", 0)) for r in subs]

    def cons(s0, s1):
        j = bisect.bisect_left(ss, s0); out = []
        while j < len(subs) and ss[j] <= s1:
            out.append(subs[j]); j += 1
        return out

    sigs = []
    for i in range(first, len(A) - 1):
        s = sig(A[i])
        if s == 0:
            continue
        b = A[i]; ref = b["l"] if b["c"] > b["o"] else (b["h"] if b["c"] < b["o"] else b["o"])
        mm = ((((b["c"] * 100) / ref) - 100) ** 2) * 100 if ref > 0 else 0.0
        dh1 = b.get("delta_h1"); cv = float(b.get("curr_vol", 0) or 0)
        if dh1 is not None and cv > 0:      # exact, from the daemon field (post-deploy / backfilled buckets)
            d = ((float(b.get("buy_vol", 0)) - float(b.get("sell_vol", 0))) - 2 * float(dh1)) / cv
        else:                                # else reconstruct from the 1m archive
            d = _da2_recon(cons(b["start_time"], b["end_time"]))
        sigs.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), mm=mm, da2=d))
    return A, sigs


def taken(A, sigs, rr):
    """One-at-a-time taken trades for the v1.3 gate. Each: dict(side, win, net[fraction, fee-in], t)."""
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last or not GATE(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])); last = res[2]
    return sorted(out, key=lambda z: z["t"])


def _maxdd(path):
    peak = np.maximum.accumulate(path); return np.max((peak - path) / peak)


def main():
    np.random.seed(42); SIMS = 20000
    A, sigs = build()
    print("MMXSKEW v1.3  (v1.1 - POC + mov_mag>=39 + raw da2>0 asymmetric)  — split-half + Monte Carlo\n")
    for rr in (1.0, 1.5):
        T = taken(A, sigs, rr); n = len(T)
        if n == 0:
            print("RR 1:%s  n=0" % rr); continue
        r = np.array([t["net"] for t in T]); w = sum(t["win"] for t in T)
        L = [t for t in T if t["side"] > 0]; Sh = [t for t in T if t["side"] < 0]
        tot = (np.prod(1 + r) - 1) * 100; dd = _maxdd(np.cumprod(1 + r)) * 100
        print("=" * 84); print("RR 1:%s   n=%d (%dL/%dS)" % (rr, n, len(L), len(Sh))); print("=" * 84)
        print("  ACTUAL: win %.0f%% (L %.0f%% / S %.0f%%)  net %+.1f%%  mean/tr %+.3f%%  maxDD %.1f%%"
              % (100 * w / n, 100 * np.mean([t["win"] for t in L]) if L else 0,
                 100 * np.mean([t["win"] for t in Sh]) if Sh else 0, tot, r.mean() * 100, dd))
        mid = n // 2

        def hh(x):
            return "win %.0f%% net %+.1f%% (n%d)" % (100 * np.mean([1 if v > 0 else 0 for v in x]),
                                                     (np.prod(1 + x) - 1) * 100, len(x))
        print("  SPLIT-HALF: H1 %s   |   H2 %s" % (hh(r[:mid]), hh(r[mid:])))
        samp = r[np.random.randint(0, n, size=(SIMS, n))]
        fin = (np.prod(1 + samp, axis=1) - 1) * 100; means = samp.mean(axis=1) * 100
        print("  MC bootstrap: P(profit)=%.1f%%  mean/tr 95%%CI [%+.3f, %+.3f]%%  total p5/p50/p95 = %+.0f/%+.0f/%+.0f%%"
              % (100 * np.mean(fin > 0), np.percentile(means, 2.5), np.percentile(means, 97.5),
                 np.percentile(fin, 5), np.percentile(fin, 50), np.percentile(fin, 95)))
        perm = np.array([np.random.permutation(r) for _ in range(SIMS)])
        pdd = np.array([_maxdd(np.cumprod(1 + p)) for p in perm]) * 100
        print("  MC perm maxDD: p50 %.1f%% | p95 %.1f%% (actual %.1f%%)\n" % (np.percentile(pdd, 50), np.percentile(pdd, 95), dd))
    print("CAVEATS: in-sample, mined, one regime, 1m-covered only; the LONG cell is tiny (noise) — the powered")
    print("edge is the SHORT side. MC quantifies sampling luck, NOT regime/forward risk. Forward tape decides.")


if __name__ == "__main__":
    main()
