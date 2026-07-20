"""Do candle TYPES (delta x direction x Mov.Magnitude) carry a DIRECTIONAL bias on the 1m
constant-volume bars?  (causal, in-sample)

HYPOTHESIS (operator): looking at DELTA and MOV.MAGNITUDE, is there a candle type whose next move
is predictable?

METHOD — the rules this study lives or dies by:
1. CAUSAL. Every feature (delta, direction, Mov.Magnitude) is known at the bar's CLOSE. The forward
   return is measured strictly AFTER, close[i] -> close[i+h]. No centered windows, no peeking.
2. MARGINAL vs BASE RATE. "Up bars are followed by up moves" can be pure drift. A type only has bias
   if it BEATS the all-bars base rate; the headline is EDGE = cohort - base, never the cohort alone.
3. SIGN STABILITY + MULTIPLE COMPARISONS. A real bias holds its sign across horizons and clears
   significance; with ~dozens of cells, ~5% look significant by chance, so lone stars are discounted.

Bars are CONSTANT-VOLUME, so 'horizon' is in BARS (each ~equal volume), not minutes.

Run:  python study/candle_bias_study.py     Data: study/out/study_1m.csv
"""
from __future__ import annotations
import csv, math, os, statistics

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "study_1m.csv")
HORIZONS = (1, 3, 5, 10, 20)          # bars forward, from the bar's close


def mov_mag(o, h, l, c):
    """Production Mov.Magnitude: bull -> from LOW, bear -> from HIGH, doji -> open. ((c*100/ref-100)^2)*100."""
    ref = l if c > o else (h if c < o else o)
    return (((c * 100.0 / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def load():
    rows = []
    for r in csv.DictReader(open(CSV)):
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        bv, sv = float(r["buy_vol"]), float(r["sell_vol"])
        if o <= 0:
            continue
        rows.append(dict(o=o, h=h, l=l, c=c, delta=bv - sv, mm=mov_mag(o, h, l, c),
                         up=(c > o), dn=(c < o)))
    return rows


def fwd(rows, i, h):
    if i + h >= len(rows):
        return None
    c0 = rows[i]["c"]
    return None if c0 <= 0 else (rows[i + h]["c"] - c0) / c0 * 100.0


def cohort(rows, pred, h):
    return [v for i in range(len(rows)) if pred(rows[i]) for v in (fwd(rows, i, h),) if v is not None]


def summ(vals):
    if not vals:
        return 0, float("nan"), float("nan")
    up = sum(1 for v in vals if v > 0)
    return len(vals), 100.0 * up / len(vals), statistics.mean(vals)


def ztest(coh, base):
    """z of cohort UP-rate vs base UP-rate (two-proportion). Returns (z, p)."""
    if len(coh) < 10:
        return 0.0, 1.0
    p1 = sum(1 for v in coh if v > 0) / len(coh)
    p2 = sum(1 for v in base if v > 0) / len(base)
    n1, n2 = len(coh), len(base)
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    return z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def main():
    rows = load()
    n = len(rows)
    mms = sorted(r["mm"] for r in rows)
    dls = sorted(r["delta"] for r in rows)
    mm_hi = mms[int(0.667 * n)]; mm_lo = mms[int(0.333 * n)]        # Mov.Magnitude terciles
    print(f"1m constant-volume bars: {n}")
    print(f"Mov.Magnitude  median={statistics.median([r['mm'] for r in rows]):.4f}  terciles: <{mm_lo:.3f} / >{mm_hi:.3f}")
    print(f"delta          median={statistics.median([r['delta'] for r in rows]):+.0f}")
    up_share = 100.0 * sum(1 for r in rows if r["up"]) / n
    print(f"bar direction  {up_share:.1f}% bull / {100-up_share:.1f}% bear-or-doji\n")

    # --- base rate: ALL bars, per horizon (this is the drift every cohort must beat) ---
    base = {h: cohort(rows, lambda r: True, h) for h in HORIZONS}
    print("BASE RATE (all bars) — the drift each type must beat:")
    print("  " + "  ".join(f"h{h}: {summ(base[h])[1]:.1f}%up {summ(base[h])[2]:+.3f}%" for h in HORIZONS))

    # --- the candle taxonomy: direction x delta-agreement, then split by Mov.Magnitude ---
    TYPES = [
        ("TREND bull   (c>o, dlt>0)",   lambda r: r["up"] and r["delta"] > 0),
        ("ABSORB bull  (c>o, dlt<0)",   lambda r: r["up"] and r["delta"] < 0),
        ("TREND bear   (c<o, dlt<0)",   lambda r: r["dn"] and r["delta"] < 0),
        ("ABSORB bear  (c<o, dlt>0)",   lambda r: r["dn"] and r["delta"] > 0),
    ]
    MM_SPLIT = [("", lambda r: True), (" [MM hi]", lambda r: r["mm"] >= mm_hi), (" [MM lo]", lambda r: r["mm"] <= mm_lo)]

    print("\n" + "=" * 118)
    print("CANDLE TYPE  x  HORIZON   —   EDGE = cohort - base (up% and mean%);  * = p<0.05 vs base up-rate")
    print("=" * 118)
    sig = []
    for tname, tpred in TYPES:
        for msuf, mpred in MM_SPLIT:
            pred = lambda r, a=tpred, b=mpred: a(r) and b(r)
            cn = len(cohort(rows, pred, 1))
            if cn < 30:
                continue
            print(f"\n{tname}{msuf}")
            print(f"  {'h':>3} | {'n':>5} {'up%':>6} {'mean%':>8} | {'base up%':>8} {'base m%':>8} | "
                  f"{'EDGE up':>7} {'EDGE m':>8} {'p':>7}")
            print("  " + "-" * 100)
            for h in HORIZONS:
                coh = cohort(rows, pred, h)
                cn2, cu, cm = summ(coh)
                bn, bu, bm = summ(base[h])
                z, p = ztest(coh, base[h])
                star = " *" if p < 0.05 else ""
                if p < 0.05:
                    sig.append((tname + msuf, h, cu - bu, cm - bm, p))
                print(f"  {h:>3} | {cn2:>5} {cu:>6.1f} {cm:>+8.3f} | {bu:>8.1f} {bm:>+8.3f} | "
                      f"{cu-bu:>+7.1f} {cm-bm:>+8.3f} {p:>7.3f}{star}")

    ncells = sum(1 for tn, tp in TYPES for ms, mp in MM_SPLIT
                 if len(cohort(rows, lambda r, a=tp, b=mp: a(r) and b(r), 1)) >= 30) * len(HORIZONS)
    print("\n" + "=" * 118)
    print(f"cells tested: {ncells}   |   significant at p<0.05: {len(sig)}   |   expected by CHANCE: ~{ncells*0.05:.1f}")
    if sig:
        print("\nsurvivors (sorted by |edge up%|):")
        for name, h, eu, em, p in sorted(sig, key=lambda x: -abs(x[2])):
            print(f"   {name:26s} h{h:<2}  edge_up={eu:+5.1f}pp  edge_mean={em:+.3f}%  p={p:.3f}")
    # SIGN STABILITY: for each type, does edge_mean hold ONE sign across all horizons?
    print("\nSIGN STABILITY (edge_mean sign across horizons — a real bias should not flip):")
    for tname, tpred in TYPES:
        signs = []
        for h in HORIZONS:
            coh = cohort(rows, tpred, h); _, _, cm = summ(coh); _, _, bm = summ(base[h])
            signs.append("+" if (cm - bm) > 0 else "-")
        print(f"   {tname:26s} {' '.join(signs)}   {'STABLE' if len(set(signs))==1 else 'FLIPS'}")


if __name__ == "__main__":
    main()
