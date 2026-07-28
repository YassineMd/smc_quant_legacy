"""Adversarial verification of the 1h engulfing-at-S/R edge: look-ahead kill-test, filter decomposition,
S/R-definition + PROX robustness, and exact-binomial significance. Nothing here should be believed unless it
survives ALL of these."""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from study.va_bias_1h_strategies import daily_va, partial_va

F = L.load_features("1h")
A = F["A"]; d = F["dir"]; mm = F["movmag"]; o = F["o"]; c = F["c"]; h = F["h"]; l = F["l"]; n = F["n"]; yr = F["year"]
dayva, dfirst = daily_va(A)


def nd(i):
    b = abs(c[i] - o[i]); return b > (h[i] - max(o[i], c[i])) and b > (min(o[i], c[i]) - l[i])


def sr_twophase(i, causal=False):
    t = L._dtu(A[i]["start_time"]); d3 = t.date()
    if t.hour < 15:
        va = dayva.get(d3 - dt.timedelta(days=1))
    else:
        va = partial_va(A, i - 1 if causal else i, dfirst.get(d3, i))   # causal -> exclude the entry bar
    return (va["vah"], va["val"]) if va else (None, None)


def sr_prevday(i):
    va = dayva.get(L._dtu(A[i]["start_time"]).date() - dt.timedelta(days=1))   # strictly causal: yesterday's closed VA
    return (va["vah"], va["val"]) if va else (None, None)


def binom_ge(k, N, p):
    if N <= 0:
        return 1.0
    if N <= 400:
        return sum(math.comb(N, j) * p ** j * (1 - p) ** (N - j) for j in range(k, N + 1))
    mu = N * p; sd = (N * p * (1 - p)) ** .5
    return 0.5 * math.erfc((k - .5 - mu) / (sd * 2 ** .5))


def run(pred, prox, srfn):
    sigs = []
    for i in range(1, n):
        if mm[i] <= mm[i - 1] or not (nd(i) and nd(i - 1)):
            continue
        vah, val = srfn(i)
        s = pred(i, vah, val, prox)
        if s == 0:
            continue
        sigs.append(dict(i=i, side=s, entry=float(c[i]), ext=float(l[i]) if s > 0 else float(h[i]), yr=int(yr[i])))
    rows = MA.taken(F, sigs)
    if not rows:
        return dict(n=0)
    nt = np.array([r["net"] for r in rows]); N = len(nt); w = int((nt > 0).sum())
    tot = (np.prod(1 + nt) - 1) * 100
    slm = np.mean([r["dist"] for r in rows]); be = 0.5 + MA.FEE / (2 * slm)       # fee-adjusted BE win rate
    p = binom_ge(w, N, be)
    y25 = [r for r in rows if r["yr"] == 2025]; y26 = [r for r in rows if r["yr"] == 2026]
    return dict(n=N, win=100 * w / N, net=tot, be=be * 100, p=p,
                y25=(len(y25), 100 * sum(r["net"] > 0 for r in y25) / max(1, len(y25))),
                y26=(len(y26), 100 * sum(r["net"] > 0 for r in y26) / max(1, len(y26))))


def fmt(t, r):
    if r.get("n", 0) == 0:
        print("  %-40s n=0" % t); return
    print("  %-40s n=%3d  win %5.1f%% (BE %.1f%%)  net %+6.1f%%  p=%.3f  2025 %d@%.0f%%  2026 %d@%.0f%%"
          % (t, r["n"], r["win"], r["be"], r["net"], r["p"], r["y25"][0], r["y25"][1], r["y26"][0], r["y26"][1]))


# full engulf-at-S/R predicate
def full(i, vah, val, prox):
    if d[i - 1] == -1 and d[i] == 1 and c[i] > h[i - 1] and val is not None and abs(o[i] - val) <= prox * o[i]:
        return 1
    if d[i - 1] == 1 and d[i] == -1 and c[i] < o[i - 1] and vah is not None and abs(o[i] - vah) <= prox * o[i]:
        return -1
    return 0


# engulf pattern WITHOUT the S/R gate (does S/R add anything?)
def nosr(i, vah, val, prox):
    if d[i - 1] == -1 and d[i] == 1 and c[i] > h[i - 1]:
        return 1
    if d[i - 1] == 1 and d[i] == -1 and c[i] < o[i - 1]:
        return -1
    return 0


print("=" * 96)
print("VERIFY 1h engulf-at-S/R  (recon 18mo, a -63% downtrend)")
print("=" * 96)
print("A) LOOK-AHEAD KILL-TEST — same rule, different S/R source (edge must survive the strictly-causal ones):")
fmt("two-phase (orig, partial incl. bar i)", run(full, 0.0015, sr_twophase))
fmt("two-phase CAUSAL (partial excl. bar i)", run(full, 0.0015, lambda i: sr_twophase(i, causal=True)))
fmt("prev-day full VA only (strictly causal)", run(full, 0.0015, sr_prevday))
print("\nB) DOES THE S/R GATE ADD ANYTHING? (engulf+movmag+non-doji, no S/R):")
fmt("engulf pattern only, NO S/R gate", run(nosr, 0.0015, sr_prevday))
print("\nC) PROX ROBUSTNESS (prev-day causal S/R):")
for p in (0.0008, 0.0015, 0.0025, 0.005, 0.01):
    fmt("prev-day S/R  PROX %.2f%%" % (p * 100), run(full, p, sr_prevday))
