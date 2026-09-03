"""PIVOT HAZARD by E/E/C zone (follow-up to study/pivot_eec_15m.py, user 2026-09-03).

The zone study measured P(zone | pivot). THIS measures the actionable inverse: given a leg's
CLOSE first enters zone Z (what a trader can see at bar close), what is P(the leg's extreme —
the pivot — happens within the next N bars)? Plus: how much FURTHER does the leg run after the
touch (the continuation an early fade would eat), and how often the extreme was already behind.

DESIGN (descriptive; 15m clock, same archive; eras separate):
  - Legs/band/zones exactly as pivot_eec_15m (EMA20/50 cross legs; band = last completed bull
    HIGH / bear LOW, thirds; causal).
  - EVENT = FIRST close of the leg inside zone Z. Bull legs: equilib / expensive / beyond-up.
    Bear legs mirrored (equilib / cheap / beyond-dn).
  - Outcomes from the touch bar t: pivot bar p = argmax(H) (bull) / argmin(L) (bear) over the leg.
      * topped-before% = p < t (the extreme already printed when the zone was entered)
      * P(pivot within N bars) = p >= t and p - t <= N, N in {4, 8, 16, 32}   (1h..8h)
      * further-run% = (max H[t..end] - C[t]) / C[t] for bulls (mirror for bears) — the adverse
        continuation a fade AT the touch would sit through (median + p90)
  - BASELINE = every in-leg bar (band valid) as a pseudo-event: the unconditional in-leg hazard.
No trade sim, no verdict — the honest gates own any tradeability claim.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

NS = (4, 8, 16, 32)


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    a = 2.0 / (n + 1.0)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = e + a * (vals[i] - e)
        out[i] = e
    return out


def classify(p, lo, hi):
    if hi <= lo:
        return "inverted"
    if p < lo:
        return "beyond-dn"
    if p > hi:
        return "beyond-up"
    t = (p - lo) / (hi - lo)
    return "cheap" if t < 1.0 / 3.0 else ("equilib" if t < 2.0 / 3.0 else "expensive")


def pct(x):
    return "%5.1f%%" % x


def med(v):
    if not v:
        return float("nan")
    s = sorted(v)
    return s[len(s) // 2]


def p90(v):
    if not v:
        return float("nan")
    s = sorted(v)
    return s[min(len(s) - 1, int(0.9 * len(s)))]


def study(bars, label):
    n = len(bars)
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n
    for i, b in enumerate(bars):
        O[i], C[i], H[i], L[i] = _ohlc(b)
    e20 = ema(C, 20); e50 = ema(C, 50)
    legs = []
    cur = None; st = None
    for i in range(60, n):
        if e20[i] is None or e50[i] is None or e20[i] == e50[i]:
            continue
        d = 1 if e20[i] > e50[i] else -1
        if cur is None:
            cur, st = d, i
        elif d != cur:
            legs.append((cur, st, i - 1))
            cur, st = d, i

    events = {}   # (dir, zone) -> list of (t, p_bar, further_run_pct)
    base = {1: [], -1: []}                       # dir -> same tuples for EVERY in-leg bar
    lb_hi = None; lb_lo = None
    for d, a, b in legs:
        if lb_hi is not None and lb_lo is not None and lb_hi > lb_lo:
            if d > 0:
                p_bar = max(range(a, b + 1), key=lambda i: H[i])
            else:
                p_bar = min(range(a, b + 1), key=lambda i: L[i])
            seen = set()
            for i in range(a, b + 1):
                z = classify(C[i], lb_lo, lb_hi)
                if d > 0:
                    run = (max(H[i:b + 1]) - C[i]) / C[i] * 100.0
                else:
                    run = (C[i] - min(L[i:b + 1])) / C[i] * 100.0
                base[d].append((i, p_bar, run))
                if z not in seen:
                    seen.add(z)
                    events.setdefault((d, z), []).append((i, p_bar, run))
        ext = max(H[a:b + 1]) if d > 0 else min(L[a:b + 1])
        if d > 0:
            lb_hi = ext
        else:
            lb_lo = ext

    def row(name, evs):
        m = len(evs)
        if not m:
            print("%-30s n=    0" % name)
            return
        before = sum(1 for t, p, _r in evs if p < t)
        line = "%-30s n=%5d  extreme-already-passed %s" % (name, m, pct(before / m * 100.0))
        for N in NS:
            k = sum(1 for t, p, _r in evs if t <= p <= t + N)
            line += "  P(piv<=%2db) %s" % (N, pct(k / m * 100.0))
        runs = [r for _t, _p, r in evs]
        line += "  | further-run med %+.3f%% p90 %+.3f%%" % (med(runs), p90(runs))
        print(line)

    print("\n=== %s ===  legs=%d" % (label, len(legs)))
    for d, tag, zones in ((1, "BULL", ("equilib", "expensive", "beyond-up")),
                          (-1, "BEAR", ("equilib", "cheap", "beyond-dn"))):
        row("%s baseline (all in-leg bars)" % tag, base[d])
        for z in zones:
            row("%s first close in %s" % (tag, z.upper()), events.get((d, z), []))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _bids, raws, gaps = load_archive("15m", root=os.path.join(here, "clock_archive"),
                                     drop_degenerate=False)
    assert not gaps
    split = next(k for k, r in enumerate(raws) if float(r.get("start_time", 0)) >= 1767225600.0)
    study(raws[:split], "RECON 2025 (Jan-Dec)")
    study(raws[split:], "RECON 2026 H1 (Jan-Jun)")


if __name__ == "__main__":
    main()
