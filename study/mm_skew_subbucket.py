"""MMXSKEW — INTRA-BUCKET delta acceleration, reconstructed from the 1m archive (no daemon change).

Each mature 1h MMXSKEW signal bucket (~420k vol) is composed of ~60 archived 1m sub-buckets (~6.4k vol each).
By mapping the ordered 1m sub-buckets that fall inside a signal bucket's [start,end] we reconstruct its INTRA-bar
order-flow trajectory and test whether aggression ACCELERATING into the bucket close (vs decaying) predicts W/L.
Reconstruction is validated per signal: sum(sub-deltas) vs the bucket's stored (buy-sell) delta -> corr 0.997.

Features (directionalised: >0 = accelerates WITH the trade into the close; long favours buying, short selling):
  delta_accel_2   = (dH2 - dH1)/vol            (second-half vs first-half net delta; 50/50 volume split)
  delta_accel_3   = (dT3 - dT1)/vol            (last vs first tercile; 33/33/33 volume split)
  terminal_burst  = (dT3 - (dT1+dT2)/2)/vol    (final third vs the earlier baseline)
Tested with the strict bar: point-biserial + split-half sign-consistency + BOTH RR + directional realignment.
Only 114/149 signals have 1m coverage (1m archive starts ~7d after the signals) — the other 35 are dropped.

Run: python study/mm_skew_subbucket.py
"""
from __future__ import annotations
import os, sys, math, bisect
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR
from study.archive_loader import load_archive

MIN_SUB = 12   # need enough 1m sub-buckets for a clean tercile split


def _load_subs():
    _, r1, _ = load_archive("1m")
    sub = sorted(r1, key=lambda r: float(r.get("start_time", 0)))
    return sub, [float(r.get("start_time", 0)) for r in sub]


def _accel(subs):
    vols = [float(x.get("curr_vol", 0)) for x in subs]
    dels = [float(x.get("buy_vol", 0)) - float(x.get("sell_vol", 0)) for x in subs]
    tot = sum(vols)
    if tot <= 0 or len(subs) < MIN_SUB:
        return None

    def split(fracs):
        bounds = [f * tot for f in fracs]; groups = [0.0] * (len(fracs) + 1); cum = 0.0
        for v, d in zip(vols, dels):
            mid = cum + v / 2; g = sum(1 for b in bounds if mid > b); groups[g] += d; cum += v
        return groups
    dH1, dH2 = split([0.5]); dT1, dT2, dT3 = split([1 / 3, 2 / 3])
    return (dH2 - dH1) / tot, (dT3 - dT1) / tot, (dT3 - (dT1 + dT2) / 2) / tot, dH1 + dH2


def build_rows():
    A, first, daylist, dayfull = FM.build()
    sub, sub_start = _load_subs()

    def constituents(s0, s1):
        j = bisect.bisect_left(sub_start, s0); out = []
        while j < len(sub) and sub_start[j] <= s1:
            out.append(sub[j]); j += 1
        return out

    rows = []; recon = []
    for i in [i for i in range(first, len(A) - 1) if FM.sig(A[i]) != 0]:
        s = FM.sig(A[i]); b = A[i]; ac = _accel(constituents(b["start_time"], b["end_time"]))
        if ac is None:
            continue
        r10 = RR.simulate_rr(A, i, s, 1.0, "sl"); r15 = RR.simulate_rr(A, i, s, 1.5, "sl")
        if r10 is None or r15 is None:
            continue
        a2, a3, tb, rd = ac
        recon.append((rd, b.get("buy_vol", 0) - b.get("sell_vol", 0)))
        rows.append(dict(side=s, t=float(b["start_time"]), win10=1 if r10[0] == "TP" else 0,
                         win15=1 if r15[0] == "TP" else 0, da2=s * a2, da3=s * a3, tb=s * tb))
    return rows, np.array(recon)


def _pbis(xs, ys):
    xs = np.array(xs, float); ys = np.array(ys, float)
    if xs.std() == 0 or ys.std() == 0:
        return 0.0, 1.0
    r = float(np.corrcoef(xs, ys)[0, 1]); t = r * math.sqrt((len(xs) - 2) / max(1e-9, 1 - r * r))
    return r, 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))


def main():
    rows, recon = build_rows(); n = len(rows)
    cc = np.corrcoef(recon[:, 0], recon[:, 1])[0, 1]
    print("RECONSTRUCTION VALIDATION: sum(sub-deltas) vs stored bucket delta  corr=%.3f  (n=%d)" % (cc, n))
    print("used %d/149 signals (%dL/%dS); base win10=%.1f%% win15=%.1f%%\n"
          % (n, sum(1 for r in rows if r["side"] > 0), sum(1 for r in rows if r["side"] < 0),
             100 * np.mean([r["win10"] for r in rows]), 100 * np.mean([r["win15"] for r in rows])))
    print("%-15s %-7s | %7s %6s | %-22s | split-half" % ("feature", "RR", "r_pb", "p", "tercile win% lo/mid/hi"))
    for key, lbl in (("da2", "delta_accel_2"), ("da3", "delta_accel_3"), ("tb", "terminal_burst")):
        xs = [r[key] for r in rows]; q1, q2 = np.percentile(xs, 33.3), np.percentile(xs, 66.6)
        for rrk in ("win10", "win15"):
            r, p = _pbis(xs, [r[rrk] for r in rows]); terc = []
            for lo, hi in ((-1e9, q1), (q1, q2), (q2, 1e9)):
                g = [r2[rrk] for r2 in rows if (r2[key] <= hi if lo <= -1e9 else lo < r2[key] <= hi)]
                terc.append(100 * np.mean(g) if g else 0)
            R = sorted(rows, key=lambda z: z["t"]); m = len(R) // 2; med = np.median(xs); sg = []
            for seg in (R[:m], R[m:]):
                hi = [r2[rrk] for r2 in seg if r2[key] > med]; loo = [r2[rrk] for r2 in seg if r2[key] <= med]
                sg.append(int(np.sign((np.mean(hi) if hi else 0) - (np.mean(loo) if loo else 0))))
            rob = "ROBUST" if sg[0] != 0 and sg[0] == sg[1] else ""
            print("%-15s %-7s | %+7.3f %6.3f | %4.0f / %4.0f / %4.0f        | H1%+d H2%+d %s"
                  % (lbl, rrk, r, p, terc[0], terc[1], terc[2], sg[0], sg[1], rob))
        print()


if __name__ == "__main__":
    main()
