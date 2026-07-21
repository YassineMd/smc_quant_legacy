"""DELTA-ACCEL v1.0 — EXPLORATORY: sub-candle delta trajectory over 30-minute halves.

Sequence per hour C2:   C1.h1 -> C1.h2 -> C2.h1   ==> trade at C2.h1 close, predict C2.h2.

DATA NOTE (important). The "1h" and "1m" archives are CONSTANT-VOLUME bucket streams, not time candles —
1h buckets run 8s..95min (median ~33min), so they have no "minutes 0-30 / 30-60" halves to split. The 1m
stream DOES tile time contiguously with 0 gaps (median 21s/bucket), so this study builds TRUE WALL-CLOCK
30-MINUTE BARS by aggregating 1m buckets, then pairs them into hours (h1 = :00-:30, h2 = :30-:00).
That is the only way to honour the 30-minute-halves spec on this tape. Coverage is therefore bounded by the
1m archive (~22 days), not the 30 days of 1h data.

This is also a DIFFERENT quantity from the existing `delta_h1` / da2 feature on 1h buckets: that splits a
bucket at its 50%-VOLUME mark, whereas this splits wall-clock TIME.

Per 30m bar:  delta = buy_vol - sell_vol   ·   eff = |close-open| / (high-low)   [price efficiency ratio]
              delta_accel = delta(C2.h1) - delta(C1.h2)

HYPOTHESIS A (momentum acceleration / continuation)
    |d(C1.h1)| < |d(C1.h2)| < |d(C2.h1)|, all three the SAME sign, and eff(C2.h1) >= trailing median
    -> enter at C2.h1 close WITH the delta push.

HYPOTHESIS B (delta absorption / reversal trap)
    |delta_accel| >= trailing Q3 AND eff(C2.h1) <= trailing Q1 (price stalls despite the delta spike)
    -> FADE at C2.h1 close, against the delta.

All thresholds are TRAILING (computed from strictly prior bars only) — a full-sample quantile would be
look-ahead. Exits: (i) C2 full close = hold one bar to C2.h2 close; (ii) RR 1:1.0 / 1:1.5 with stop 0.1%
beyond the entry bar's extreme, matching the MMXSKEW family. Fee 0.08% round-trip, fee-in everywhere.

Execution accounting uses the family's DECLARED non-overlap contract (convention A): a signal on the bar in
which the prior trade exited is SKIPPED (`i <= last`). See study/MMXSKEW_NOPOC.md "Execution contract".

Significance: permutation over the SELECTION, not the returns — random entries of the same count and same
side-mix drawn from the same eligible-bar pool, re-run through the same non-overlap filter. That is the
null "this rule picks no better than chance", which is the claim under test.

Run: python study/delta_accel_explore.py
"""
from __future__ import annotations
import os, sys
import datetime as dt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive

FEE = 0.0008          # round-trip, as a fraction
SL_BUF = 0.001        # stop 0.1% beyond the entry bar's extreme
HALF = 1800           # 30 minutes
MIN_HIST = 100        # bars of trailing history before a quantile threshold is trusted
SIMS = 20000


# ----------------------------------------------------------------------------- data
def build_30m():
    """True wall-clock 30m bars aggregated from the contiguous 1m volume-bucket stream."""
    _, raw, _ = load_archive("1m")
    buckets = {}
    for b in raw:
        st = float(b.get("start_time", 0.0) or 0.0)
        o = b.get("open_price"); c = b.get("close_price")
        h = b.get("high"); l = b.get("low")
        if not st or o is None or c is None or h is None or l is None:
            continue
        k = int(st // HALF) * HALF                       # assign by START time (bars tile contiguously)
        d = buckets.get(k)
        if d is None:
            buckets[k] = dict(t=k, o=float(o), c=float(c), h=float(h), l=float(l),
                              bv=float(b.get("buy_vol", 0.0) or 0.0),
                              sv=float(b.get("sell_vol", 0.0) or 0.0), n=1)
        else:
            d["c"] = float(c); d["h"] = max(d["h"], float(h)); d["l"] = min(d["l"], float(l))
            d["bv"] += float(b.get("buy_vol", 0.0) or 0.0)
            d["sv"] += float(b.get("sell_vol", 0.0) or 0.0); d["n"] += 1
    bars = [buckets[k] for k in sorted(buckets)]
    for x in bars:
        rng = x["h"] - x["l"]
        x["delta"] = x["bv"] - x["sv"]
        x["eff"] = (abs(x["c"] - x["o"]) / rng) if rng > 0 else 0.0
        x["half"] = 1 if (x["t"] % 3600) == 0 else 2      # h1 = :00-:30, h2 = :30-:00
    return bars


# ----------------------------------------------------------------------------- exits
def sim_rr(bars, i, side, rr):
    """Stop 0.1% beyond bar i's extreme; TP = rr * stop. Returns (outcome, net_fraction, exit_idx)."""
    e = bars[i]["c"]
    if side > 0:
        sl = bars[i]["l"] * (1 - SL_BUF); sld = e - sl; tp = e + rr * sld
    else:
        sl = bars[i]["h"] * (1 + SL_BUF); sld = sl - e; tp = e - rr * sld
    if sld <= 0:
        return None
    slf = sld / e
    for j in range(i + 1, len(bars)):
        hi, lo = bars[j]["h"], bars[j]["l"]
        htp = (hi >= tp) if side > 0 else (lo <= tp)
        hsl = (lo <= sl) if side > 0 else (hi >= sl)
        if htp and hsl:
            return ("SL", -1.0 * slf, j)                  # same-bar ambiguity -> assume the stop (conservative)
        if htp:
            return ("TP", rr * slf, j)
        if hsl:
            return ("SL", -1.0 * slf, j)
    return None                                           # unresolved -> excluded, never booked as a loss


def sim_close(bars, i, side):
    """Hold exactly one bar: enter at C2.h1 close, exit at C2.h2 close."""
    if i + 1 >= len(bars):
        return None
    e = bars[i]["c"]; x = bars[i + 1]["c"]
    return ("CLOSE", side * (x - e) / e, i + 1)


# ----------------------------------------------------------------------------- signals
def sequences(bars):
    """Every (C1.h1, C1.h2, C2.h1) triple that is contiguous in time and correctly half-aligned."""
    out = []
    for i in range(2, len(bars) - 1):
        a, b, c = bars[i - 2], bars[i - 1], bars[i]
        if not (a["half"] == 1 and b["half"] == 2 and c["half"] == 1):
            continue                                       # must be C1.h1 -> C1.h2 -> C2.h1
        if b["t"] - a["t"] != HALF or c["t"] - b["t"] != HALF:
            continue                                       # no missing 30m slot
        out.append(dict(i=i, d1=a["delta"], d2=b["delta"], d3=c["delta"],
                        accel=c["delta"] - b["delta"], eff=c["eff"], t=c["t"]))
    return out


def trailing_q(vals, q, min_hist=MIN_HIST):
    """q-quantile of strictly PRIOR values (expanding window) — no look-ahead. NaN until min_hist."""
    out = np.full(len(vals), np.nan)
    for k in range(min_hist, len(vals)):
        out[k] = np.quantile(vals[:k], q)
    return out


def label(seqs):
    """Tag each sequence with hypothesis A / B triggers using TRAILING thresholds only."""
    eff = np.array([s["eff"] for s in seqs], float)
    aac = np.array([abs(s["accel"]) for s in seqs], float)
    med_eff = trailing_q(eff, 0.50); q1_eff = trailing_q(eff, 0.25); q3_acc = trailing_q(aac, 0.75)
    for k, s in enumerate(seqs):
        d1, d2, d3 = s["d1"], s["d2"], s["d3"]
        same = (d1 > 0 and d2 > 0 and d3 > 0) or (d1 < 0 and d2 < 0 and d3 < 0)
        compounding = abs(d1) < abs(d2) < abs(d3)
        s["A"] = bool(same and compounding and not np.isnan(med_eff[k]) and s["eff"] >= med_eff[k])
        s["A_side"] = 1 if d3 > 0 else -1
        s["B"] = bool((not np.isnan(q3_acc[k])) and (not np.isnan(q1_eff[k]))
                      and abs(s["accel"]) >= q3_acc[k] and s["eff"] <= q1_eff[k])
        s["B_side"] = -1 if s["accel"] > 0 else 1          # FADE the delta spike
        s["elig"] = not np.isnan(med_eff[k])
    return seqs


# ----------------------------------------------------------------------------- execution
def taken(bars, picks, mode, rr=None):
    """Family non-overlap contract A: skip a signal on the bar where the prior trade exited (i <= last)."""
    last = -1; out = []
    for i, side in picks:
        if i <= last:
            continue
        res = sim_close(bars, i, side) if mode == "close" else sim_rr(bars, i, side, rr)
        if res is None:
            continue
        out.append(dict(i=i, side=side, win=(res[1] - FEE) > 0, net=res[1] - FEE)); last = res[2]
    return out


def stats(tr):
    if not tr:
        return dict(n=0, win=float("nan"), exp=0.0, net=0.0, L=0, S=0)
    r = np.array([t["net"] for t in tr]) * 100.0
    return dict(n=len(tr), win=100.0 * np.mean([t["win"] for t in tr]), exp=r.mean(), net=r.sum(),
                L=sum(1 for t in tr if t["side"] > 0), S=sum(1 for t in tr if t["side"] < 0))


def perm_p(bars, seqs, picks, mode, rr, obs_exp, rng):
    """Null: pick the same COUNT with the same SIDE MIX at random from eligible bars, same non-overlap filter."""
    pool = [s["i"] for s in seqs if s["elig"]]
    if not picks or len(pool) < len(picks):
        return float("nan")
    sides = [sd for _, sd in picks]; k = len(picks); hits = 0; done = 0
    for _ in range(SIMS):
        idx = rng.choice(len(pool), size=k, replace=False)
        cand = sorted(((pool[j], sides[m]) for m, j in enumerate(idx)), key=lambda z: z[0])
        st = stats(taken(bars, cand, mode, rr))
        if st["n"] == 0:
            continue
        done += 1; hits += (st["exp"] >= obs_exp)
    return (hits / done) if done else float("nan")


def main():
    rng = np.random.default_rng(42)
    bars = build_30m()
    seqs = label(sequences(bars))
    elig = [s for s in seqs if s["elig"]]
    t0 = dt.datetime.utcfromtimestamp(bars[0]["t"]).strftime("%Y-%m-%d %H:%M")
    t1 = dt.datetime.utcfromtimestamp(bars[-1]["t"]).strftime("%Y-%m-%d %H:%M")
    print("DELTA-ACCEL v1.0 - sub-candle delta trajectory over TRUE 30m wall-clock bars\n")
    print("  30m bars built from the 1m stream: %d  (%s -> %s UTC)" % (len(bars), t0, t1))
    print("  valid C1.h1->C1.h2->C2.h1 sequences: %d   (eligible after %d-bar threshold warmup: %d)"
          % (len(seqs), MIN_HIST, len(elig)))
    print("  raw triggers: A(momentum)=%d   B(absorption)=%d   overlap=%d"
          % (sum(1 for s in elig if s["A"]), sum(1 for s in elig if s["B"]),
             sum(1 for s in elig if s["A"] and s["B"])))

    HYP = [("A momentum", [(s["i"], s["A_side"]) for s in elig if s["A"]]),
           ("B absorption", [(s["i"], s["B_side"]) for s in elig if s["B"]]),
           ("BASELINE all-elig long", [(s["i"], 1) for s in elig]),
           ("BASELINE all-elig w/ A-side", [(s["i"], (1 if s["d3"] > 0 else -1)) for s in elig])]
    MODES = [("C2 close", "close", None), ("RR 1:1.0", "rr", 1.0), ("RR 1:1.5", "rr", 1.5)]

    print("\n## RESULTS  (non-overlap filter ON, fee-in 0.08%)")
    print("| hypothesis | exit | n | L/S | win% | exp%/tr | net% | perm p |")
    print("|---|---|---|---|---|---|---|---|")
    for hname, picks in HYP:
        for mname, mode, rr in MODES:
            tr = taken(bars, picks, mode, rr); s = stats(tr)
            if s["n"] == 0:
                print("| %s | %s | 0 | - | - | - | - | - |" % (hname, mname)); continue
            p = perm_p(bars, seqs, picks, mode, rr, s["exp"], rng) if not hname.startswith("BASELINE") else float("nan")
            ps = "-" if np.isnan(p) else ("%.4f%s" % (p, " *" if p < 0.05 else ""))
            print("| %s | %s | %d | %d/%d | %.1f | %+.4f | %+.2f | %s |"
                  % (hname, mname, s["n"], s["L"], s["S"], s["win"], s["exp"], s["net"], ps))

    print("\n  Break-even win rates for reference: RR 1:1.0 needs >50% before fees; RR 1:1.5 needs >40%.")
    print("  perm p = P(a random selection of the same size and side-mix beats this expectancy). * = p<0.05.")
    print("  BASELINE rows show what taking EVERY eligible sequence returns - the bar any rule must clear.")


if __name__ == "__main__":
    main()
