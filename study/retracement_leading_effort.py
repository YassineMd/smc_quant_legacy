"""HYPOTHESIS: near the END of a retracement (approaching the pivot for continuation), the side LEADING the
retracement makes MORE EFFORT for LESS RESULT -> the retrace is being absorbed -> continuation.

Leading side = the aggressor in the retrace's PRICE direction:
    up-retrace (bounce in a downtrend, ends_high) -> BUYERS lead -> effort = buy_vol
    down-retrace (dip in an uptrend)              -> SELLERS lead -> effort = sell_vol
For each retracement leg we split its bars into BODY (early) and TAIL (last `tail_frac`, approaching the pivot) and
measure, per bar:
    effort = leading-side volume / bar
    result = price progress in the retrace direction / bar   (close-to-close, signed so + = advancing the retrace)
SIGNATURE ("absorbing tail") = effort ROSE (tail > body) AND result FELL (tail < body) into the pivot.
efficiency = result/effort (progress per unit leading effort); eff_drop = efficiency fell (tail < body).

OUTCOME (causal, known at the retrace's completion): does the NEXT leg make a NEW trend extreme beyond the
pre-retracement extreme (p0)?  up-retr -> new low < p0 ; down-retr -> new high > p0  (== the validated predict test).

Reports continuation rate vs base, component-wise, in DISJOINT efficiency-ratio bands, crossed with CVD divergence,
and a 2025/2026 durability split. p = exact two-sided binomial vs base (log-space, any n).

CLI: python study/retracement_leading_effort.py [tf] [thr_pct] [tail_frac] [min_bars]
"""
import gzip, json, glob, os, sys, math, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S


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


def _log_pmf(i, n, p):
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_p(k, n, p):
    """Exact two-sided binomial p that k/n differs from p (sum of outcomes no more likely than the observed)."""
    if n == 0 or p <= 0.0 or p >= 1.0:
        return 1.0
    lpk = _log_pmf(k, n, p)
    tot = 0.0
    for i in range(n + 1):
        if _log_pmf(i, n, p) <= lpk + 1e-9:
            tot += math.exp(_log_pmf(i, n, p))
    return min(1.0, tot)


def line(rows, title, base=None):
    n = len(rows)
    if n == 0:
        print("  %-32s n=0" % title); return None
    k = sum(1 for r in rows if r["cont"])
    rate = k / n
    extra = ""
    if base is not None:
        extra = "  d=%+5.1fpp  p=%.3f" % ((rate - base) * 100, binom_p(k, n, base))
    print("  %-32s n=%-5d continue=%.1f%%%s" % (title, n, 100 * rate, extra))
    return rate


def seg_rates(buck, C, a, b, lead_key, dir_):
    """Per-bar (effort, result) over buckets (a, b]: effort = mean leading-side volume, result = signed price
    progress in the retrace direction per bar."""
    nb = b - a
    if nb <= 0:
        return None
    eff = sum(float(buck[i].get(lead_key, 0.0) or 0.0) for i in range(a + 1, b + 1)) / nb
    res = (C[b] - C[a]) * dir_ / nb
    return eff, res


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 2.0) / 100.0
    tail_frac = float(sys.argv[3]) if len(sys.argv) > 3 else 0.34
    min_bars = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    buck = load_tf(tf); n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in buck]
    dV = [float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0) for b in buck]
    piv = S._zigzag_confirmed(H, L, thr)
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2])
            for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    print("%s thr=%.2f%% tail=%.0f%% min_bars=%d  |  %d legs %s..%s" % (
        tf, thr * 100, tail_frac * 100, min_bars, len(legs),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)

    dP = [(p1 - p0) / p0 * 100.0 if p0 > 0 else 0.0 for (b0, p0, b1, p1, eh) in legs]
    rows = []
    for m in range(1, len(legs) - 1):                       # need preceding (m-1) AND next (m+1)
        if abs(dP[m]) >= abs(dP[m - 1]) or abs(dP[m]) < 1e-9:
            continue                                        # not a retracement
        b0, p0, b1, p1, eh = legs[m]
        nb = b1 - b0
        if nb < min_bars:
            continue                                        # too few bars to split body/tail meaningfully
        dir_ = 1.0 if eh else -1.0                          # up-retr advances up ; down-retr advances down
        lead_key = "buy_vol" if eh else "sell_vol"          # the side aggressing in the retrace's price direction
        nt = max(1, int(round(nb * tail_frac)))
        bt = b1 - nt                                        # tail = (bt, b1] ; body = (b0, bt]
        if bt <= b0:
            continue
        body = seg_rates(buck, C, b0, bt, lead_key, dir_)
        tail = seg_rates(buck, C, bt, b1, lead_key, dir_)
        if body is None or tail is None or body[0] <= 0.0:
            continue
        eff_b, res_b = body; eff_t, res_t = tail
        eff_ratio = eff_t / eff_b                           # >1 = leading effort ROSE into the tail
        effort_up = eff_t > eff_b
        result_down = res_t < res_b
        signature = effort_up and result_down               # more effort, less result at the tail
        eff_drop = (res_t / eff_t if eff_t > 0 else 0.0) < (res_b / eff_b)   # efficiency fell (progress/effort)
        p_next = legs[m + 1][3]
        cont = (p_next < p0) if eh else (p_next > p0)
        netV = sum(dV[i] for i in range(b0 + 1, b1 + 1))    # whole-retrace net delta (for the divergence cross)
        diverge = netV != 0 and ((netV > 0) != (dP[m] > 0))
        rows.append(dict(cont=bool(cont), eh=eh, effort_up=effort_up, result_down=result_down,
                         signature=signature, eff_drop=eff_drop, eff_ratio=eff_ratio,
                         diverge=diverge, year=dt.datetime.utcfromtimestamp(float(buck[b1]["start_time"])).year))

    if len(rows) < 50:
        print("  too few analyzable retracements (%d)" % len(rows)); return
    base = line(rows, "BASE (analyzable retracements)")
    print("\n  --- the HYPOTHESIS: absorbing tail (leading effort UP + price result DOWN into the pivot) ---")
    line([r for r in rows if r["signature"]], "SIGNATURE present", base)
    line([r for r in rows if not r["signature"]], "signature absent", base)
    print("\n  --- components (disjoint) ---")
    line([r for r in rows if r["effort_up"] and r["result_down"]], "effort UP & result DOWN", base)
    line([r for r in rows if r["effort_up"] and not r["result_down"]], "effort UP & result UP", base)
    line([r for r in rows if not r["effort_up"] and r["result_down"]], "effort DOWN & result DOWN", base)
    line([r for r in rows if not r["effort_up"] and not r["result_down"]], "effort DOWN & result UP", base)
    print("\n  --- efficiency (progress per unit leading effort) fell into the tail ---")
    line([r for r in rows if r["eff_drop"]], "eff_drop (tail < body)", base)
    line([r for r in rows if not r["eff_drop"]], "eff_rise (tail >= body)", base)
    print("\n  --- DISJOINT bands of eff_ratio = tail effort / body effort ---")
    bands = [(-1e9, 0.7), (0.7, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, 1e9)]
    for lo, hi in bands:
        lab = "eff_ratio [%.1f,%s)" % (lo if lo > -1e8 else 0.0, ("%.1f" % hi if hi < 1e8 else "inf"))
        line([r for r in rows if lo <= r["eff_ratio"] < hi], lab, base)
    print("\n  --- signature CROSSED with whole-retrace CVD divergence ---")
    line([r for r in rows if r["signature"] and r["diverge"]], "SIGNATURE & divergent", base)
    line([r for r in rows if r["signature"] and not r["diverge"]], "SIGNATURE & confirmed", base)
    line([r for r in rows if not r["signature"] and r["diverge"]], "no-sig & divergent", base)
    print("\n  --- by direction ---")
    line([r for r in rows if r["signature"] and r["eh"]], "SIGNATURE & up-retr (bounce)", base)
    line([r for r in rows if r["signature"] and not r["eh"]], "SIGNATURE & down-retr (dip)", base)
    print("\n  --- durability of the SIGNATURE (2025 vs 2026) ---")
    for y in (2025, 2026):
        line([r for r in rows if r["signature"] and r["year"] == y], "SIGNATURE · %d" % y, base)


if __name__ == "__main__":
    main()
