"""WHERE do EMA20/50 trend PIVOTS happen relative to the Expensive/Equilibrium/Cheap band?
(user question 2026-09-03; 15m CLOCK candles, same archive as the Big Body study)

DEFINITIONS (causal, terminal-consistent):
  - Trend legs = EMA20 vs EMA50 cross segments on 15m closes (plain cross->cross; the terminal's
    Stack-Flip adds qualification rules — simplification noted).
  - A PIVOT ends a leg. Two prices per pivot: the leg's EXTREME (max high of a bull leg / min low
    of a bear leg — the true turn) and the CROSS close (confirmation moment).
  - The E/E/C band AT the pivot = the trend-extreme lines live during that leg: last COMPLETED
    bull leg's HIGH + last COMPLETED bear leg's LOW (the current leg's own extreme is never in
    its band). Thirds of [L, H]: Cheap / Equilibrium / Expensive; plus BEYOND-UP / BEYOND-DOWN /
    INVERTED (band H <= L after a big directional run).
  - BASELINE: share of ALL closes sitting in each zone (time spent) — a pivot concentration only
    means something relative to where price lives anyway.
DESCRIPTIVE ONLY — distribution tables per era, prev-leg BULL vs BEAR. No trade sim, no verdict.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

ZONES = ("beyond-dn", "cheap", "equilib", "expensive", "beyond-up", "inverted")


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


def study(bars, label):
    n = len(bars)
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n
    for i, b in enumerate(bars):
        O[i], C[i], H[i], L[i] = _ohlc(b)
    e20 = ema(C, 20); e50 = ema(C, 50)

    # legs: (dir +1/-1, start i, end i) — end = last bar BEFORE the cross confirms the flip
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
    # walk legs keeping the causally-live band: last completed bull HIGH / bear LOW
    last_bull_hi = None; last_bear_lo = None
    rows = []                                    # (prev_dir, extreme_zone, cross_zone)
    for j, (d, a, b) in enumerate(legs):
        ext = max(H[a:b + 1]) if d > 0 else min(L[a:b + 1])
        cross_i = min(b + 1, n - 1)              # the flip-confirming bar
        if last_bull_hi is not None and last_bear_lo is not None:
            zx = classify(ext, last_bear_lo, last_bull_hi)
            zc = classify(C[cross_i], last_bear_lo, last_bull_hi)
            rows.append((d, zx, zc, a, b))
        if d > 0:
            last_bull_hi = ext
        else:
            last_bear_lo = ext
    # time-spent baseline: every close vs the band live at that bar (rebuild the walk per bar)
    base = {z: 0 for z in ZONES}
    lb_hi = None; lb_lo = None; li = 0
    for j, (d, a, b) in enumerate(legs):
        if lb_hi is not None and lb_lo is not None:
            for i in range(a, min(b + 1, n)):
                base[classify(C[i], lb_lo, lb_hi)] += 1
        ext = max(H[a:b + 1]) if d > 0 else min(L[a:b + 1])
        if d > 0:
            lb_hi = ext
        else:
            lb_lo = ext
    nb = sum(base.values()) or 1

    def table(name, sel, col):
        cnt = {z: 0 for z in ZONES}
        for r in sel:
            cnt[r[col]] += 1
        tot = sum(cnt.values()) or 1
        line = "%-34s n=%4d | " % (name, tot)
        line += "  ".join("%s %4.1f%%" % (z, cnt[z] / tot * 100.0) for z in ZONES)
        print(line)

    bulls = [r for r in rows if r[0] > 0]
    bears = [r for r in rows if r[0] < 0]
    print("\n=== %s ===  legs=%d  usable pivots=%d (bull %d / bear %d)"
          % (label, len(legs), len(rows), len(bulls), len(bears)))
    print("%-34s n=%4d | " % ("BASELINE time spent (closes)", nb)
          + "  ".join("%s %4.1f%%" % (z, base[z] / nb * 100.0) for z in ZONES))
    table("BULL leg pivot (TOP, extreme)", bulls, 1)
    table("BULL leg pivot (cross close)", bulls, 2)
    table("BEAR leg pivot (BOTTOM, extreme)", bears, 1)
    table("BEAR leg pivot (cross close)", bears, 2)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _bids, raws, gaps = load_archive("15m", root=os.path.join(here, "clock_archive"),
                                     drop_degenerate=False)
    assert not gaps
    split = next(k for k, r in enumerate(raws) if float(r.get("start_time", 0)) >= 1767225600.0)
    study(raws[:split], "RECON 2025 (Jan-Dec)")
    study(raws[split:], "RECON 2026 H1 (Jan-Jun)")
    study(raws, "FULL 18 MONTHS")


if __name__ == "__main__":
    main()
