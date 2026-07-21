"""KINETIC-DELTA — PURE stair-step baseline on 50%-VOLUME bucket halves. No filters, no quantiles, no indicators.

Sequence:  C1.h1 -> C1.h2 -> C2.h1   ==>  enter at C2.h1 close, C1/C2 CONSECUTIVE 1h buckets.

SPLIT. Each constant-volume 1h bucket is cut at its 50%-VOLUME point (not wall-clock time), reconstructed from
the 1m archive by accumulating sub-buckets until cumulative volume reaches half the parent's total. The daemon's
own `delta_h1` field stamps the same 50%-volume mark, but it is absent from the GCS archive mirror (0/3843 rows
carry it there - the earlier backfill landed in the local history.db), so the 1m reconstruction is the only
route. It is the same method previously validated against delta_h1 at corr 0.997. Coverage: 922 of 3843 buckets
(24%) sit fully inside the 1m archive, each with 45-72 sub-buckets.

Per half:  delta = buy_vol - sell_vol   ·   V = total volume   (plus OHLC for the exits)

CONDITIONS (SIGNED deltas, exactly as specified - note these are signed, NOT magnitudes)
  BULL stair-step : d(C1.h1) <  d(C1.h2) <  d(C2.h1)  AND d(C2.h1) > 0   -> LONG  at C2.h1 close
  BEAR stair-step : d(C1.h1) >  d(C1.h2) >  d(C2.h1)  AND d(C2.h1) < 0   -> SHORT at C2.h1 close
  VOLUME escalation: V(C1.h1) < V(C1.h2) < V(C2.h1)                      -> side = sign(d(C2.h1))

  *** STRUCTURAL WARNING on the volume variant. *** The halves are defined by an equal-VOLUME cut, so within one
  bucket V_h1 ~= V_h2 by construction and V(C1.h1) < V(C1.h2) can only be true by the granularity of the
  straddling sub-bucket. The condition is therefore near-degenerate and mostly reduces to "C2 is a bigger bucket
  than C1". The run quantifies exactly how lopsided the halves actually are so this is not taken on faith.

EXITS
  1. C2.h2 close  - hold exactly one half-bar (through the second half of bucket 2).
  2. RR 1:1.0 / 1:1.5 - stop 0.1% beyond the C2.h1 extreme, TP = RR x stop, scanned forward over half-bars.

Execution: the family's declared non-overlap contract (convention A) - a signal on the half-bar in which the
prior trade exited is SKIPPED (`i <= last`). See study/MMXSKEW_NOPOC.md "Execution contract". Fee 0.08% fee-in.

Significance: permutation over the SELECTION - random entries of the same count and same side-mix drawn from the
same eligible pool, re-run through the same non-overlap filter. Null = "this rule picks no better than chance".

Run: python study/kinetic_delta_explore.py
"""
from __future__ import annotations
import os, sys, bisect
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive

FEE = 0.0008
SL_BUF = 0.001
SIMS = 20000


def build_halves():
    """Split every 1m-covered 1h bucket at its 50%-VOLUME point -> a contiguous series of half-bars."""
    _, H, _ = load_archive("1h")
    _, M, _ = load_archive("1m")
    mst = [float(b["start_time"]) for b in M]
    m_lo, m_hi = mst[0], float(M[-1]["end_time"])
    halves = []                       # each: dict(o,c,h,l,bv,sv,vol,delta,parent,half)
    lop = []                          # per-bucket diagnostics
    for pi, b in enumerate(H):
        st, et = float(b["start_time"]), float(b["end_time"])
        if st < m_lo or et > m_hi:
            continue
        a = bisect.bisect_left(mst, st); z = bisect.bisect_left(mst, et)
        subs = M[a:z]
        if len(subs) < 4:
            continue
        vols = [float(s.get("curr_vol", 0.0) or 0.0) for s in subs]
        tot = sum(vols)
        if tot <= 0:
            continue
        cum = 0.0; cut = len(subs) - 1
        for k, v in enumerate(vols):
            cum += v
            if cum >= 0.5 * tot:
                cut = k; break                        # h1 = subs[:cut+1], h2 = subs[cut+1:]
        parts = (subs[:cut + 1], subs[cut + 1:])
        if not parts[1]:
            continue                                  # degenerate: no second half
        made = []
        for hh, part in enumerate(parts, start=1):
            o = float(part[0].get("open_price"))
            c = float(part[-1].get("close_price"))
            hi = max(float(s.get("high")) for s in part)
            lo = min(float(s.get("low")) for s in part)
            bv = sum(float(s.get("buy_vol", 0.0) or 0.0) for s in part)
            sv = sum(float(s.get("sell_vol", 0.0) or 0.0) for s in part)
            vv = sum(float(s.get("curr_vol", 0.0) or 0.0) for s in part)
            made.append(dict(o=o, c=c, h=hi, l=lo, bv=bv, sv=sv, vol=vv,
                             delta=bv - sv, parent=pi, half=hh))
        halves.extend(made)
        lop.append(made[0]["vol"] / (made[0]["vol"] + made[1]["vol"]))
    return halves, np.array(lop)


def sequences(halves):
    """(C1.h1, C1.h2, C2.h1) triples where C1 and C2 are CONSECUTIVE parent buckets and C2.h2 exists."""
    out = []
    for k in range(2, len(halves) - 1):
        a, b, c, d = halves[k - 2], halves[k - 1], halves[k], halves[k + 1]
        if not (a["half"] == 1 and b["half"] == 2 and c["half"] == 1 and d["half"] == 2):
            continue
        if a["parent"] != b["parent"] or c["parent"] != d["parent"]:
            continue
        if c["parent"] != a["parent"] + 1:
            continue                                   # C2 must immediately follow C1
        out.append(dict(k=k, d1=a["delta"], d2=b["delta"], d3=c["delta"],
                        v1=a["vol"], v2=b["vol"], v3=c["vol"]))
    return out


def sim_rr(halves, i, side, rr):
    e = halves[i]["c"]
    if side > 0:
        sl = halves[i]["l"] * (1 - SL_BUF); sld = e - sl; tp = e + rr * sld
    else:
        sl = halves[i]["h"] * (1 + SL_BUF); sld = sl - e; tp = e - rr * sld
    if sld <= 0:
        return None
    slf = sld / e
    for j in range(i + 1, len(halves)):
        hi, lo = halves[j]["h"], halves[j]["l"]
        htp = (hi >= tp) if side > 0 else (lo <= tp)
        hsl = (lo <= sl) if side > 0 else (hi >= sl)
        if htp and hsl:
            return ("SL", -slf, j)                     # same-bar ambiguity -> assume the stop (conservative)
        if htp:
            return ("TP", rr * slf, j)
        if hsl:
            return ("SL", -slf, j)
    return None                                        # unresolved -> excluded, never booked as a loss


def sim_close(halves, i, side):
    if i + 1 >= len(halves):
        return None
    e = halves[i]["c"]; x = halves[i + 1]["c"]
    return ("CLOSE", side * (x - e) / e, i + 1)


def taken(halves, picks, mode, rr=None):
    last = -1; out = []
    for i, side in picks:
        if i <= last:
            continue
        res = sim_close(halves, i, side) if mode == "close" else sim_rr(halves, i, side, rr)
        if res is None:
            continue
        out.append(dict(side=side, net=res[1] - FEE, win=(res[1] - FEE) > 0)); last = res[2]
    return out


def stats(tr):
    if not tr:
        return dict(n=0, win=float("nan"), exp=0.0, net=0.0, L=0, S=0)
    r = np.array([t["net"] for t in tr]) * 100.0
    return dict(n=len(tr), win=100.0 * np.mean([t["win"] for t in tr]), exp=r.mean(), net=r.sum(),
                L=sum(1 for t in tr if t["side"] > 0), S=sum(1 for t in tr if t["side"] < 0))


def perm_p(halves, pool, picks, mode, rr, obs, rng):
    if not picks or len(pool) < len(picks):
        return float("nan")
    sides = [s for _, s in picks]; k = len(picks); hit = 0; done = 0
    for _ in range(SIMS):
        idx = rng.choice(len(pool), size=k, replace=False)
        cand = sorted(((pool[j], sides[m]) for m, j in enumerate(idx)), key=lambda z: z[0])
        st = stats(taken(halves, cand, mode, rr))
        if st["n"] == 0:
            continue
        done += 1; hit += (st["exp"] >= obs)
    return (hit / done) if done else float("nan")


def main():
    rng = np.random.default_rng(42)
    halves, lop = build_halves()
    seqs = sequences(halves)
    print("KINETIC-DELTA - pure stair-step on 50%-VOLUME bucket halves (NO filters)\n")
    print("  half-bars built: %d (from %d parent buckets)   valid C1->C2 sequences: %d"
          % (len(halves), len(halves) // 2, len(seqs)))
    print("  h1 share of bucket volume: p5=%.4f p50=%.4f p95=%.4f  (0.5 == a perfect equal-volume cut)"
          % (np.percentile(lop, 5), np.percentile(lop, 50), np.percentile(lop, 95)))
    nv = sum(1 for s in seqs if s["v1"] < s["v2"])
    print("  sequences where V(C1.h1) < V(C1.h2) *within* one bucket: %d/%d (%.1f%%) <- split granularity only"
          % (nv, len(seqs), 100.0 * nv / max(1, len(seqs))))

    bull = [(s["k"], 1) for s in seqs if s["d1"] < s["d2"] < s["d3"] and s["d3"] > 0]
    bear = [(s["k"], -1) for s in seqs if s["d1"] > s["d2"] > s["d3"] and s["d3"] < 0]
    vol = [(s["k"], (1 if s["d3"] > 0 else -1)) for s in seqs if s["v1"] < s["v2"] < s["v3"]]
    both = sorted(bull + bear, key=lambda z: z[0])
    pool = [s["k"] for s in seqs]

    HYP = [("BULL stair-step", bull), ("BEAR stair-step", bear), ("BULL+BEAR combined", both),
           ("VOLUME escalation", vol),
           ("BASELINE all-seq (C2.h1 delta side)", [(s["k"], 1 if s["d3"] > 0 else -1) for s in seqs])]
    MODES = [("C2.h2 close", "close", None), ("RR 1:1.0", "rr", 1.0), ("RR 1:1.5", "rr", 1.5)]

    print("\n## RESULTS  (non-overlap filter ON, fee-in 0.08%)")
    print("| condition | exit | n_raw | n_taken | L/S | win% | exp%/tr | net% | perm p |")
    print("|---|---|---|---|---|---|---|---|---|")
    for hname, picks in HYP:
        for mname, mode, rr in MODES:
            tr = taken(halves, picks, mode, rr); s = stats(tr)
            if s["n"] == 0:
                print("| %s | %s | %d | 0 | - | - | - | - | - |" % (hname, mname, len(picks))); continue
            p = float("nan") if hname.startswith("BASELINE") else perm_p(halves, pool, picks, mode, rr, s["exp"], rng)
            ps = "-" if np.isnan(p) else ("%.4f%s" % (p, " *" if p < 0.05 else ""))
            print("| %s | %s | %d | %d | %d/%d | %.1f | %+.4f | %+.2f | %s |"
                  % (hname, mname, len(picks), s["n"], s["L"], s["S"], s["win"], s["exp"], s["net"], ps))

    print("\n  Break-even: RR 1:1.0 needs >50% before fees, RR 1:1.5 needs >40%.")
    print("  perm p = P(a random same-size, same-side-mix selection beats this expectancy). * = p<0.05.")
    print("  BASELINE = take EVERY sequence in the C2.h1 delta direction: the bar any rule must clear.")


if __name__ == "__main__":
    main()
