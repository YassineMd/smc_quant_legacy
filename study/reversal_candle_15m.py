"""What defines a 15m REVERSAL candle (the candle AT a Price&CVD-Swings flip)?

15m sibling of study/reversal_candle_1h.py. The swing indicator's ZigZag (swing_lvn_detect._dev_leg, adaptive
volatility-scaled threshold — the SAME detector the Price&CVD-Swings overlay draws) marks each flip: a swing HIGH
(up-leg ends = TOP) or swing LOW (BOTTOM). The pivot bar IS the reversal candle. This compares the stats-box params
AT those pivots vs ordinary (non-pivot) candles to pinpoint what a 15m reversal candle looks like in order-flow terms.

DESCRIPTIVE, not predictive: the ZigZag confirms a pivot only after price retraces by the threshold -> look-ahead.
This is the anatomy in hindsight (a basis for a predictive rule, not itself a live signal). Reuses build_stats
(the full stats-box list) + a 2025/2026 split so a signature only counts if it holds BOTH years.

CLI: python study/reversal_candle_15m.py
"""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import swing_lvn_detect as SL

NAN = float("nan")


def mean(xs):
    xs = [x for x in xs if x == x]
    return (sum(xs) / len(xs)) if xs else NAN


def load_15m():
    _, rows, _ = load_archive("15m", root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price", 0.0)); b["close"] = _f(b.get("close_price", 0.0))
    return A


def main():
    A = load_15m()
    n = len(A)
    yrs = [datetime.fromtimestamp(_f(b.get("start_time", 0)), tz=timezone.utc).year for b in A]
    print("recon 15m buckets: %d  (2025 %d / 2026 %d)" % (n, yrs.count(2025), yrs.count(2026)))
    S, O, C = build_stats(A)

    r = SL._dev_leg(A)
    if not r:
        print("no swings"); return
    H, L, Cc, thr, piv, dev = r
    tops, bottoms = set(), set()
    for (bar, price, is_high, cbar) in piv:
        if 0 <= int(bar) < n:
            (tops if is_high else bottoms).add(int(bar))
    pivset = tops | bottoms
    excl = set()
    for b in pivset:
        excl.update((b - 1, b, b + 1))
    nonpiv = [i for i in range(2, n - 2) if i not in excl]
    print("ZigZag thr=%.2f%%  pivots=%d  (tops=%d, bottoms=%d)  non-pivot baseline=%d candles"
          % (thr * 100.0, len(pivset), len(tops), len(bottoms), len(nonpiv)))

    def dirmix(bars):
        L_ = sum(1 for i in bars if C[i] > O[i]); S_ = sum(1 for i in bars if C[i] < O[i])
        D_ = len(bars) - L_ - S_; m = max(1, len(bars))
        return "Long %.0f%% / Short %.0f%% / doji %.0f%%" % (100.0 * L_ / m, 100.0 * S_ / m, 100.0 * D_ / m)
    print("  TOP    reversal-candle body: %s" % dirmix(sorted(tops)))
    print("  BOTTOM reversal-candle body: %s" % dirmix(sorted(bottoms)))

    names = list(S.keys())

    def auc_year(pivbars, statname, yr):
        pv = [S[statname][i] for i in pivbars if yrs[i] == yr and S[statname][i] == S[statname][i]]
        nv = [S[statname][i] for i in nonpiv if yrs[i] == yr and S[statname][i] == S[statname][i]]
        if len(pv) < 10 or len(nv) < 40:
            return NAN
        return auc_p(pv, nv)[0]

    def sep_table(pivbars, tag):
        rows = []
        for st in names:
            pv = [S[st][i] for i in pivbars if S[st][i] == S[st][i]]
            nv = [S[st][i] for i in nonpiv if S[st][i] == S[st][i]]
            if len(pv) < 20 or len(nv) < 80:
                continue
            auc, p, _n1, _n0 = auc_p(pv, nv)
            a25 = auc_year(pivbars, st, 2025); a26 = auc_year(pivbars, st, 2026)
            rows.append((abs(auc - 0.5), st, auc, p, mean(pv), mean(nv), len(pv), a25, a26))
        rows.sort(reverse=True)
        print("\n=== %s reversal candle vs ordinary  (AUC>0.5 => stat HIGHER at reversal) ===" % tag)
        print("%-16s  %6s %8s  %10s %10s  %6s  %5s/%5s" % ("stat", "AUC", "p", "mean@piv", "mean@ord", "npiv", "25", "26"))
        print("-" * 78)
        for _, st, auc, p, mp, mo, npv, a25, a26 in rows[:18]:
            both = "" if (a25 != a25 or a26 != a26) else (" *" if ((a25 - 0.5) * (a26 - 0.5) > 0 and abs(auc - 0.5) >= 0.05 and p < 0.01) else "")
            print("%-16s  %6.3f %8.4f  %10.4f %10.4f  %6d  %5.2f/%5.2f%s" % (st, auc, p, mp, mo, npv, a25, a26, both))
        return {st: (auc, p, a25, a26) for _, st, auc, p, _, _, _, a25, a26 in rows}

    dt = sep_table(sorted(tops), "TOP  (up->down flip)")
    db = sep_table(sorted(bottoms), "BOTTOM (down->up flip)")

    print("\n=== WHAT DEFINES A 15m REVERSAL CANDLE (|AUC-0.5|>=0.05, p<0.01, BOTH YEARS same sign, top & bottom) ===")
    hits = []
    for st in names:
        if st in dt and st in db:
            at, pt, at25, at26 = dt[st]; ab, pb, ab25, ab26 = db[st]
            if any(v != v for v in (at25, at26, ab25, ab26)):
                continue
            st_t, st_b = at - 0.5, ab - 0.5
            strong = abs(st_t) >= 0.05 and abs(st_b) >= 0.05 and pt < 0.01 and pb < 0.01
            yr_ok = (at25 - 0.5) * (at26 - 0.5) > 0 and (ab25 - 0.5) * (ab26 - 0.5) > 0   # each side same sign both yrs
            mirror = st_t * st_b < 0; same = st_t * st_b > 0
            if strong and yr_ok and (mirror or same):
                hits.append((min(abs(st_t), abs(st_b)), st, at, ab, "MIRROR" if mirror else "SAME"))
    for _, st, at, ab, kind in sorted(hits, reverse=True):
        print("  %-16s  AUC top %.3f / bottom %.3f   [%s]" % (st, at, ab, kind))
    if not hits:
        print("  (none cleared the bar)")


if __name__ == "__main__":
    main()
