"""MMXSKEW v1.1 — does adding a delta>=0 FLOOR to the LONG filter help?

Current long gate: close>open & skew>0 & spread>=+35 & delta<15  (delta = (buy-sell)/curr_vol*100).
`delta<15` is a one-sided cap, so it accepts NEGATIVE-delta longs. Proposed: 0 <= delta < 15 (drop delta<0 longs).

Because 0<=delta<15 is a strict SUBSET of delta<15, this is a DISJOINT-BAND question (memory: disjoint-bands-not-
cumulative-ladders): split the taken LONGS into [delta<0] and [0<=delta<15] and compare. Stats ONLY from taken()
non-overlap (memory: canonical-taken-basis). Exact binomial vs break-even + Fisher between bands (memory:
split-half-gate-is-near-vacuous). Shorts are untouched; the floor re-chains the non-overlap filter, so we also
report the realistic "apply the floor" effect (taken re-run with the floor).

Run: python study/mm_skew_long_delta_floor.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR

RRS = (1.0, 1.5)
FEE = 0.0008


def base_sigs(A, first, long_cap=True):
    """v1.1-NP base (no POC, no v1.2 gate). long_cap=True applies the current delta<15 long gate; long_cap=False
    yields the FULL long population (up & sk>0 & spread>=+35, ANY delta) so the currently-EXCLUDED delta>=15 band
    can be studied. Shorts carry no delta condition either way."""
    out = []
    for i in range(first, len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and (not long_cap or b["delta"] < 15):
            s = 1
        elif b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
            s = -1
        else:
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), delta=float(b["delta"])))
    return out


def taken(A, sigs, rr, keep=None):
    """One-at-a-time non-overlap chain. `keep` = optional {side: predicate(delta)->bool}: a signal whose side has a
    predicate it FAILS is dropped *before* it claims the slot, so the non-overlap filter re-chains exactly as it
    would live (a dropped signal never updates `last`)."""
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        pred = keep.get(sg["side"]) if keep else None
        if pred is not None and not pred(sg["delta"]):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE,
                        delta=sg["delta"], t=sg["t"])); last = res[2]
    return sorted(out, key=lambda z: z["t"])


def binom_ge(k, n, p):
    """One-sided exact P(X >= k) under Binomial(n, p) — 'win rate beats break-even p by chance?'."""
    if n == 0:
        return float("nan")
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def fisher_2x2(a, b, c, d):
    """Two-sided Fisher exact p for [[a,b],[c,d]] (band1 win/loss vs band2 win/loss)."""
    n = a + b + c + d
    if n == 0:
        return float("nan")
    r1, r2, c1 = a + b, c + d, a + c

    def hp(x):   # P(top-left = x) hypergeometric
        return (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    p_obs = hp(a); tot = 0.0
    lo = max(0, r1 - (n - c1)); hi = min(r1, c1)
    for x in range(lo, hi + 1):
        px = hp(x)
        if px <= p_obs + 1e-12:
            tot += px
    return min(1.0, tot)


def wl(rows):
    n = len(rows); w = sum(1 for r in rows if r["win"])
    net = 1.0
    for r in rows:
        net *= (1 + r["net"])
    return n, w, (100.0 * w / n if n else float("nan")), (net - 1) * 100.0


def bands_report(rows, be, band_defs):
    """Print disjoint delta-bands for one side's taken rows + Fisher between the KEEP band and each REMOVE band."""
    keep_rows = None
    for lbl, pred, is_keep in band_defs:
        band = [t for t in rows if pred(t["delta"])]
        n, w, wr, net = wl(band)
        tag = "KEEP  " if is_keep else "REMOVE"
        p = binom_ge(w, n, be) if n else float("nan")
        print("    [%s] %-16s : n=%2d  win %5.1f%%  net %+6.1f%%  P(>=win|BE)=%s"
              % (tag, lbl, n, wr, net, ("%.3f" % p) if n else "--"))
        if is_keep:
            keep_rows = band
    # Fisher: KEEP band vs each REMOVE band
    if keep_rows:
        kw, kl = sum(t["win"] for t in keep_rows), len(keep_rows) - sum(t["win"] for t in keep_rows)
        for lbl, pred, is_keep in band_defs:
            if is_keep:
                continue
            band = [t for t in rows if pred(t["delta"])]
            if not band:
                continue
            bw, bl = sum(t["win"] for t in band), len(band) - sum(t["win"] for t in band)
            print("      Fisher KEEP vs [%s]: p=%.3f" % (lbl, fisher_2x2(kw, kl, bw, bl)))


def side_stats(rows, side):
    return wl([t for t in rows if t["side"] == side])


DROP_SHORTS = {-1: lambda d: False}   # long-only chains: isolate the long delta dimension


def main():
    A, first, _, _ = FM.build()
    sigs = base_sigs(A, first, long_cap=False)        # FULL long population (ANY delta) + all shorts
    L = [s for s in sigs if s["side"] > 0]; S = [s for s in sigs if s["side"] < 0]
    b_neg = sum(d["delta"] < 0 for d in L); b_mid = sum(0 <= d["delta"] < 15 for d in L)
    b_hi = sum(d["delta"] >= 15 for d in L)
    print("FULL long population (up & skew>0 & spread>=+35, ANY delta): %d longs | %d shorts   [span 34d]" %
          (len(L), len(S)))
    print("  long delta bands (raw):  %d delta<0 | %d 0<=delta<15 | %d delta>=15" % (b_neg, b_mid, b_hi))
    print("  current gate keeps delta<15 (= %d); you're testing the EXCLUDED delta>=15 band (= %d)\n"
          % (b_neg + b_mid, b_hi))

    LONG_BANDS = [("delta<0", lambda d: d < 0, "excl-floor"),
                  ("0<=delta<15", lambda d: 0 <= d < 15, "GATE keeps"),
                  ("delta>=15", lambda d: d >= 15, ">>> TEST")]

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 94); print("RR 1:%.1f   break-even win = %.0f%%" % (rr, be * 100)); print("=" * 94)

        # LONG disjoint bands on a LONG-ONLY non-overlap chain (shorts dropped to isolate the delta dimension)
        Lchain = taken(A, sigs, rr, keep=DROP_SHORTS)
        print("  LONG disjoint bands (long-only non-overlap chain):")
        hi = mid = None
        for lbl, pred, tag in LONG_BANDS:
            band = [t for t in Lchain if pred(t["delta"])]
            n, w, wr, net = wl(band)
            p = binom_ge(w, n, be) if n else float("nan")
            print("    [%-10s] %-12s: n=%2d win %5.1f%% net %+6.1f%%  P(>=win|BE)=%s"
                  % (tag, lbl, n, wr, net, ("%.3f" % p) if n else "--"))
            if lbl == "delta>=15":
                hi = band
            if lbl == "0<=delta<15":
                mid = band
        if hi and mid:
            hw, hl = sum(t["win"] for t in hi), len(hi) - sum(t["win"] for t in hi)
            mw, ml = sum(t["win"] for t in mid), len(mid) - sum(t["win"] for t in mid)
            print("    Fisher [delta>=15] vs [0<=delta<15 (gate)]: p=%.3f" % fisher_2x2(hw, hl, mw, ml))

        # each candidate LONG filter as its OWN standalone non-overlap chain (what that gate would actually trade)
        print("  standalone LONG-only chains (each gate's own non-overlap set):")
        for lbl, pred in (("current  delta<15", lambda d: d < 15),
                          (">>> TEST delta>=15", lambda d: d >= 15),
                          ("0<=delta<15       ", lambda d: 0 <= d < 15),
                          ("all longs (no cap)", lambda d: True)):
            ch = taken(A, sigs, rr, keep={1: pred, -1: lambda d: False})
            n, w, wr, net = wl(ch)
            print("    %s: n=%2d win %5.1f%% net %+6.1f%%" % (lbl, n, wr, net))
        print()


if __name__ == "__main__":
    main()
