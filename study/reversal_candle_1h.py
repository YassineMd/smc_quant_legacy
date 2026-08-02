"""What defines a 1h REVERSAL candle (the candle AT a Price&CVD-Swings flip)?

The swing indicator's ZigZag (swing_lvn_detect._dev_leg -> confirmed pivots) marks each flip: a swing HIGH (up-leg
ends, price flips down = TOP) or a swing LOW (BOTTOM). The pivot bar IS the reversal candle = the last candle of the
ending leg, at the extreme. This study compares the candle stats AT those pivots vs ordinary (non-pivot) candles to
pinpoint what a reversal candle looks like.

DESCRIPTIVE, not predictive: the ZigZag confirms a pivot only after price retraces by the threshold, so the pivot set
uses look-ahead. This characterises reversals in hindsight (the anatomy) — a starting point for a predictive rule, not
itself a live signal. Reuses study/candle_bias_1h.build_stats on recon 1h; pivots via the SAME detector the indicator uses.

Metric per stat: AUC = P(stat at reversal candle > stat at ordinary candle), + mean at pivot vs non-pivot, + MW p.
TOP and BOTTOM reported separately (mirror images). Non-pivot excludes +/-1 around every pivot (clean contrast).

CLI: python study/reversal_candle_1h.py
"""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.candle_bias_1h import load, build_stats, auc_p
from app import swing_lvn_detect as SL

NAN = float("nan")


def mean(xs):
    xs = [x for x in xs if x == x]
    return (sum(xs) / len(xs)) if xs else NAN


def main():
    A = load()
    n = len(A)
    print("recon 1h buckets: %d" % n)
    S, O, C = build_stats(A)

    r = SL._dev_leg(A)
    if not r:
        print("no swings"); return
    H, L, Cc, thr, piv, dev = r
    tops, bottoms = set(), set()
    for (bar, price, is_high, cbar) in piv:
        if 0 <= bar < n:
            (tops if is_high else bottoms).add(int(bar))
    pivset = tops | bottoms
    # non-pivot = candles NOT within +/-1 of any pivot (clean baseline)
    excl = set()
    for b in pivset:
        excl.update((b - 1, b, b + 1))
    nonpiv = [i for i in range(2, n - 2) if i not in excl]
    print("ZigZag thr=%.2f%%  pivots=%d  (tops=%d, bottoms=%d)  non-pivot baseline=%d candles"
          % (thr * 100.0, len(pivset), len(tops), len(bottoms), len(nonpiv)))

    # pivot-candle direction mix (Long/Short/doji) at tops and bottoms
    def dirmix(bars):
        L_ = sum(1 for i in bars if C[i] > O[i]); S_ = sum(1 for i in bars if C[i] < O[i])
        D_ = len(bars) - L_ - S_
        m = max(1, len(bars))
        return "Long %.0f%% / Short %.0f%% / doji %.0f%%" % (100.0 * L_ / m, 100.0 * S_ / m, 100.0 * D_ / m)
    print("  TOP    reversal-candle body: %s" % dirmix(sorted(tops)))
    print("  BOTTOM reversal-candle body: %s" % dirmix(sorted(bottoms)))

    names = list(S.keys())

    def sep_table(pivbars, tag):
        base_idx = nonpiv
        rows = []
        for st in names:
            pv = [S[st][i] for i in pivbars if S[st][i] == S[st][i]]
            nv = [S[st][i] for i in base_idx if S[st][i] == S[st][i]]
            if len(pv) < 15 or len(nv) < 50:
                continue
            auc, p, _n1, _n0 = auc_p(pv, nv)          # AUC = P(pivot > non-pivot)
            rows.append((abs(auc - 0.5), st, auc, p, mean(pv), mean(nv), len(pv)))
        rows.sort(reverse=True)
        print("\n=== %s reversal candle vs ordinary candle  (AUC>0.5 => stat HIGHER at reversal) ===" % tag)
        print("%-16s  %6s %8s  %11s %11s  %5s" % ("stat", "AUC", "p", "mean@piv", "mean@ord", "npiv"))
        print("-" * 68)
        for _, st, auc, p, mp, mo, npv in rows:
            star = " *" if (p < 0.01 and abs(auc - 0.5) >= 0.05) else ""
            print("%-16s  %6.3f %8.4f  %11.4f %11.4f  %5d%s" % (st, auc, p, mp, mo, npv, star))
        return rows

    rt = sep_table(sorted(tops), "TOP  (up->down flip)")
    rb = sep_table(sorted(bottoms), "BOTTOM (down->up flip)")

    print("\n=== WHAT DEFINES A REVERSAL CANDLE (|AUC-0.5|>=0.05, p<0.01, MIRRORED top vs bottom) ===")
    dt = {st: (auc, p) for _, st, auc, p, _, _, _ in rt}
    db = {st: (auc, p) for _, st, auc, p, _, _, _ in rb}
    hits = []
    for st in names:
        if st in dt and st in db:
            at, pt = dt[st]; ab, pb = db[st]
            st_t, st_b = at - 0.5, ab - 0.5
            strong = abs(st_t) >= 0.05 and abs(st_b) >= 0.05 and pt < 0.01 and pb < 0.01
            mirror = st_t * st_b < 0            # opposite sign at tops vs bottoms = a directional signature
            same = st_t * st_b > 0              # same sign = magnitude signature (e.g. wick size, absorption)
            if strong and (mirror or same):
                hits.append((min(abs(st_t), abs(st_b)), st, at, ab, "MIRROR" if mirror else "SAME"))
    for _, st, at, ab, kind in sorted(hits, reverse=True):
        print("  %-16s  AUC top %.3f / bottom %.3f   [%s]" % (st, at, ab, kind))
    if not hits:
        print("  (none cleared the bar)")


if __name__ == "__main__":
    main()
