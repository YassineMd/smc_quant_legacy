"""SWING CVD symmetry / asymmetry — the swing-level analog of absorption R (app/absorption.py), but the UNIT is a
whole ZigZag leg instead of one candle.

For each CONFIRMED swing leg [b0..b1] (structure._zigzag_confirmed, the same legs the Recent-Swing-LVA indicator uses):
    dP = (p1 - p0) / p0 * 100            RESULT  — the price swing (signed %, up-leg + / down-leg -)
    dV = Σ (buy_vol - sell_vol) over b0+1..b1   EFFORT — the CVD swing (signed net contracts)
Both are z-scored against the trailing W_LEGS PRIOR legs (causal), and
    R_swing = Z(dP) - rho * Z(dV)        rho = corr(dP, dV) over those prior legs
    A_swing = -R_swing if dV>0 else R_swing   (oriented like absorption: + = ABSORBED, - = EASY, ~0 = proportional)

Reads: (1) is the CVD swing PROPORTIONAL to the price swing? -> corr(dV,dP) + OLS slope + sign agreement.
       (2) how often is a swing SYMMETRIC (proportional) vs ASYMMETRIC (absorbed / easy)? -> A_swing distribution.

CLI: python study/swing_cvd_symmetry.py [tf] [thr_pct]
"""
import gzip, json, glob, os, sys, math, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S

W_LEGS = 30          # trailing legs for the z-score baseline (matches absorption WINDOW=30)
MIN_OBS = 20         # need this many prior legs before R is trustworthy


def load_tf(tf):
    by_bid = {}
    for fn in sorted(glob.glob(os.path.join(ROOT, "study", "recon_archive", tf, "%s_*.jsonl.gz" % tf))):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                by_bid[int(r["bid"])] = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
    return [by_bid[b] for b in sorted(by_bid)]


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs)); sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx <= 0 or sy <= 0:
        return None
    return sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / (sx * sy)


def _ols(xs, ys):
    """slope, intercept, R^2 of ys ~ xs."""
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); sxy = sum((xs[k] - mx) * (ys[k] - my) for k in range(n))
    if sxx <= 0:
        return None
    b = sxy / sxx; a = my - b * mx
    ss_res = sum((ys[k] - (a + b * xs[k])) ** 2 for k in range(n))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return b, a, r2


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 0.5) / 100.0
    buck = load_tf(tf)
    n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    dlt = [float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0) for b in buck]
    cum = [0.0] * (n + 1)
    for i in range(n):
        cum[i + 1] = cum[i] + dlt[i]
    piv = S._zigzag_confirmed(H, L, thr)
    print("%s  thr=%.2f%%  |  %d buckets, span %s..%s  |  %d pivots -> %d legs" % (
        tf, thr * 100, n,
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date(),
        len(piv), max(0, len(piv) - 1)), flush=True)

    legs = []                                   # (dP, dV, ends_high, dur)
    for k in range(len(piv) - 1):
        b0, p0, _ih0, _cb0 = piv[k]
        b1, p1, ih1, _cb1 = piv[k + 1]
        if b1 <= b0 or p0 <= 0:
            continue
        dP = (p1 - p0) / p0 * 100.0
        dV = cum[b1 + 1] - cum[b0 + 1]          # net delta over b0+1..b1 (the flow that drove p0->p1)
        legs.append((dP, dV, ih1, b1 - b0))
    if len(legs) < MIN_OBS + 5:
        print("  too few legs (%d) — loosen thr" % len(legs)); return

    dur = [x[3] for x in legs]
    print("  legs: %d  median dur=%d bars  median |dP|=%.2f%%  median |dV|=%.0f" % (
        len(legs), int(st.median(dur)), st.median([abs(x[0]) for x in legs]), st.median([abs(x[1]) for x in legs])))

    # (1) RAW PROPORTIONALITY: is the CVD swing ∝ the price swing?
    dPs = [x[0] for x in legs]; dVs = [x[1] for x in legs]
    up = [x for x in legs if x[2]]; dn = [x for x in legs if not x[2]]
    print("\n=== (1) is the CVD swing PROPORTIONAL to the price swing? ===")
    print("  corr(dV, dP)  ALL = %+.3f   up-legs = %+.3f (n=%d)   down-legs = %+.3f (n=%d)" % (
        _corr(dVs, dPs) or 0, _corr([x[1] for x in up], [x[0] for x in up]) or 0, len(up),
        _corr([x[1] for x in dn], [x[0] for x in dn]) or 0, len(dn)))
    ols = _ols(dPs, dVs)
    if ols:
        print("  OLS  dV = %.0f %+.0f*dP   (=> ~%.0f net contracts per +1%% swing)   R^2=%.3f" % (ols[1], ols[0], ols[0], ols[2]))
    agree = sum(1 for x in legs if (x[1] > 0) == (x[0] > 0))
    print("  sign agreement (up-leg net-BUY / down-leg net-SELL): %.1f%%  (%d/%d)" % (100.0 * agree / len(legs), agree, len(legs)))

    # (2) SWING ABSORPTION R (z-scored vs trailing W_LEGS) -> symmetric vs asymmetric distribution
    print("\n=== (2) swing absorption A  (z-scored vs trailing %d legs; + = ABSORBED, - = EASY, ~0 = proportional) ===" % W_LEGS)
    A = []
    for m in range(len(legs)):
        win = legs[max(0, m - W_LEGS):m]
        if len(win) < MIN_OBS:
            continue
        wv = [w[1] for w in win]; wp = [w[0] for w in win]
        nw = float(len(win)); mv = sum(wv) / nw; mp = sum(wp) / nw
        sv = math.sqrt(sum((v - mv) ** 2 for v in wv) / (nw - 1)); sp = math.sqrt(sum((p - mp) ** 2 for p in wp) / (nw - 1))
        if sv <= 0 or sp <= 0:
            continue
        cov = sum((wv[k] - mv) * (wp[k] - mp) for k in range(len(win))) / (nw - 1)
        rho = max(-1.0, min(1.0, cov / (sv * sp)))
        dP, dV = legs[m][0], legs[m][1]
        R = (dP - mp) / sp - rho * (dV - mv) / sv
        a = 0.0 if dV == 0 else (-R if dV > 0 else R)
        A.append((a, legs[m][2]))
    if not A:
        print("  (no R — warm-up too long)"); return
    vals = [a for a, _ in A]
    bands = [("EASY   (A<=-1.5) price ran on little flow", lambda a: a <= -1.5),
             ("light  (-1.5,-0.75]", lambda a: -1.5 < a <= -0.75),
             ("PROPORTIONAL (-0.75,0.75)  << symmetric", lambda a: -0.75 < a < 0.75),
             ("heavy  [0.75,1.5)", lambda a: 0.75 <= a < 1.5),
             ("ABSORBED (A>=1.5) flow lagged by price", lambda a: a >= 1.5)]
    for lab, f in bands:
        c = sum(1 for a in vals if f(a))
        print("  %-44s %4d  (%.1f%%)" % (lab, c, 100.0 * c / len(vals)))
    print("  mean |A| = %.3f   (0 = perfectly proportional; higher = more asymmetric)   n=%d" % (
        st.mean(abs(a) for a in vals), len(vals)))


if __name__ == "__main__":
    main()
