"""SESSION ONE-SIDE FIX — 15m bucket, DESCRIPTIVE follow-up to session_range_fix_15m (user 2026-08-25: "can we
get a high or low that gets respected EARLIER in the session?").

PRE-REGISTERED (frozen; same data/sessions/nulls as the parent study):
  FIRST-SIDE FIX = elapsed fraction at which the FIRST of the two final session extremes is set
                   (min of last-new-high time, last-new-low time) — vs the 20-shuffle null.
  FAR-SIDE RULE (CAUSAL, the actionable readout): at checkpoint f in {0.15,0.20,0.25,0.30,0.40,0.50}, the
                   candidate fixed side = the extreme FARTHER from the current close (close@f above the midpoint
                   of the range-so-far -> candidate = LOW, else HIGH). Metric = P(candidate side holds to the
                   session close), STRICT and with a 10%-of-range-so-far wick tolerance — real vs the SAME rule
                   evaluated on the shuffled sessions (isolates timing structure beyond drift+geometry).
  References: P(low-so-far holds | f) and P(high-so-far holds | f) unconditionally.
  Eras: recon 2025 / recon 2026 / daemon. NO tradeability claim. python study/session_side_fix_15m.py"""
import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.session_range_fix_15m import SESSIONS, _f, load, instances

CHECKS = (0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
N_SHUF = 20


def first_fix(rows):
    hi = -1e18; lo = 1e18; t_hi = t_lo = 0.0
    for (t0, t1, o, h, l, c) in rows:
        if h > hi:
            hi = h; t_hi = t1
        if l < lo:
            lo = l; t_lo = t1
    return min(t_hi, t_lo)


def side_eval(rows, f, tol_frac):
    """(far_side_holds, low_holds, high_holds) at checkpoint f, or None if no buckets before/after f."""
    hi = -1e18; lo = 1e18; close_f = None; seen_after = False
    for (t0, t1, o, h, l, c) in rows:
        if t1 <= f:
            hi = max(hi, h); lo = min(lo, l); close_f = c
        else:
            seen_after = True
    if close_f is None or hi <= lo or not seen_after:
        return None
    tol = (hi - lo) * tol_frac
    low_holds = all(l >= lo - tol for (t0, t1, o, h, l, c) in rows if t1 > f)
    high_holds = all(h <= hi + tol for (t0, t1, o, h, l, c) in rows if t1 > f)
    far_is_low = close_f >= 0.5 * (hi + lo)
    return (low_holds if far_is_low else high_holds), low_holds, high_holds


def shuffle_rows(rows, rng):
    """Same candles, shuffled order, re-chained; uniform time spacing (rows-shaped for side_eval/first_fix)."""
    rel = [(h - o, l - o, c - o) for (_, _, o, h, l, c) in rows]
    order = list(range(len(rel))); rng.shuffle(order)
    px = rows[0][2]; out = []
    n = len(order)
    for j, idx in enumerate(order):
        dh, dl, dc = rel[idx]
        out.append((j / n, (j + 1) / n, px, px + dh, px + dl, px + dc))
        px = px + dc
    return out


def report(insts):
    rng = random.Random(23)
    by = {}
    for (d, name), rows in insts.items():
        by.setdefault(name, []).append(rows)
    for name, _h0, _h1 in SESSIONS:
        lst = by.get(name, [])
        if len(lst) < 20:
            print("  %-7s n=%d — too few" % (name, len(lst)), flush=True)
            continue
        span_h = _h1 - _h0

        def clk(fr):
            m = int(round(fr * span_h * 60))
            return "%02d:%02d" % (_h0 + m // 60, m % 60)
        ff = np.array([first_fix(r) for r in lst])
        sh_ff = []
        shuf = [[shuffle_rows(r, rng) for _ in range(N_SHUF)] for r in lst]
        for group in shuf:
            sh_ff += [first_fix(s) for s in group]
        sh_ff = np.array(sh_ff)
        print("  %-7s n=%d | FIRST-side fix p25/med/p75: %.2f/%.2f/%.2f (=%s/%s/%s) | null med %.2f -> real-null %+.2f"
              % (name, len(lst), *np.percentile(ff, (25, 50, 75)), clk(np.percentile(ff, 25)),
                 clk(np.percentile(ff, 50)), clk(np.percentile(ff, 75)), np.median(sh_ff),
                 np.median(ff) - np.median(sh_ff)), flush=True)
        for tol, tname in ((0.10, "tol10%"), (0.0, "strict")):
            rr = []; nn = []; lows = []; highs = []
            for f in CHECKS:
                rv = [side_eval(r, f, tol) for r in lst]
                rv = [x for x in rv if x is not None]
                rr.append(100 * np.mean([x[0] for x in rv]) if rv else float("nan"))
                lows.append(100 * np.mean([x[1] for x in rv]) if rv else float("nan"))
                highs.append(100 * np.mean([x[2] for x in rv]) if rv else float("nan"))
                sv = []
                for group in shuf:
                    for s in group:
                        x = side_eval(s, f, tol)
                        if x is not None:
                            sv.append(x[0])
                nn.append(100 * np.mean(sv) if sv else float("nan"))
            print("    FAR-SIDE holds %-6s @ %s : real %s | null %s" % (
                tname, "/".join("%.0f" % (100 * f) for f in CHECKS),
                " ".join("%3.0f" % v for v in rr), " ".join("%3.0f" % v for v in nn)), flush=True)
            if tol == 0.10:
                print("      (ref lowholds %s | highholds %s)" % (
                    " ".join("%3.0f" % v for v in lows), " ".join("%3.0f" % v for v in highs)), flush=True)


def main():
    print("SESSION ONE-SIDE FIX — 15m bucket | far-side-from-price rule vs shuffle null | pre-registered (header)\n", flush=True)
    t0 = time.time()
    A = load("recon")
    for era, lo, hi in (("RECON 2025", "2025-01-01", "2026-01-01"), ("RECON 2026", "2026-01-01", "2026-06-20")):
        loT = datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp()
        hiT = datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp()
        sub = [b for b in A if loT <= _f(b, "start_time") < hiT]
        print("=" * 110, flush=True)
        print(era, flush=True)
        report(instances(sub))
    del A
    print("=" * 110, flush=True)
    print("DAEMON (2026-06-20 ..)", flush=True)
    report(instances(load("daemon")))
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
