"""HYPOTHESIS (reward CROSSOVER): during a retracement, EARLY the LEADING side is rewarded per effort while the
OPPOSING side is not; APPROACHING the pivot the leading side stops being rewarded AND the opposing side starts being
rewarded -> that crossover is where it flips -> continuation.

  downtrend + up-retrace (bounce, ends_high): leading = BUYERS, opposing = SELLERS
  uptrend   + down-retrace (dip)             : leading = SELLERS, opposing = BUYERS

REWARD-PER-EFFORT of a side over a set of bars = price moved in that side's favour / that side's aggressive volume:
    buy_eff = sum(max(0, dClose)) / sum(buy_vol)      (buyers rewarded by up-moves)
    sell_eff= sum(max(0,-dClose)) / sum(sell_vol)     (sellers rewarded by down-moves)
We split each retracement into BODY (early) and TAIL (last `tail_frac`, approaching the pivot).
  lead_eff / opp_eff = the leading- / opposing-side reward-per-effort in a segment.
SIGNATURE ("reward flip") = lead_eff FELL (tail<body) AND opp_eff ROSE (tail>body) into the pivot.
crossover = (opp_eff_tail - opp_eff_body) - (lead_eff_tail - lead_eff_body)   (>0 = opposing gaining as leading loses).

OUTCOME (causal, known at the retrace's completion): does the NEXT leg make a NEW trend extreme beyond p0?
  up-retr -> new low < p0 ; down-retr -> new high > p0   (== the validated predict test).
p = exact two-sided binomial vs base. 2025/2026 durability split.

CLI: python study/retracement_reward_flip.py [tf] [thr_pct] [tail_frac] [min_bars]
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


def eff_over(bars, up_rew, dn_rew, buy, sell):
    """(buy_eff, sell_eff) over a set of bar indices: reward in each side's favour / that side's aggressive volume."""
    ur = sum(up_rew[i] for i in bars); dr = sum(dn_rew[i] for i in bars)
    bv = sum(buy[i] for i in bars); sv = sum(sell[i] for i in bars)
    return (ur / bv if bv > 0 else 0.0, dr / sv if sv > 0 else 0.0)


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 2.0) / 100.0
    tail_frac = float(sys.argv[3]) if len(sys.argv) > 3 else 0.34
    min_bars = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    buck = load_tf(tf); n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in buck]
    buy = [float(b.get("buy_vol", 0.0) or 0.0) for b in buck]
    sell = [float(b.get("sell_vol", 0.0) or 0.0) for b in buck]
    up_rew = [0.0] * n; dn_rew = [0.0] * n                  # per-bar reward = signed close-to-close move, split by side
    for i in range(1, n):
        dp = C[i] - C[i - 1]
        up_rew[i] = dp if dp > 0 else 0.0
        dn_rew[i] = -dp if dp < 0 else 0.0
    piv = S._zigzag_confirmed(H, L, thr)
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2])
            for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    print("%s thr=%.2f%% tail=%.0f%% min_bars=%d  |  %d legs %s..%s" % (
        tf, thr * 100, tail_frac * 100, min_bars, len(legs),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)

    dP = [(p1 - p0) / p0 * 100.0 if p0 > 0 else 0.0 for (b0, p0, b1, p1, eh) in legs]
    rows = []
    for m in range(1, len(legs) - 1):
        if abs(dP[m]) >= abs(dP[m - 1]) or abs(dP[m]) < 1e-9:
            continue
        b0, p0, b1, p1, eh = legs[m]
        nb = b1 - b0
        if nb < min_bars:
            continue
        nt = max(1, int(round(nb * tail_frac)))
        ts = b1 - nt + 1                                    # first tail bar
        if ts <= b0 + 1:
            continue
        body = range(b0 + 1, ts); tail = range(ts, b1 + 1)
        be_b, se_b = eff_over(body, up_rew, dn_rew, buy, sell)
        be_t, se_t = eff_over(tail, up_rew, dn_rew, buy, sell)
        if eh:                                              # bounce: leading = buyers, opposing = sellers
            lead_b, opp_b, lead_t, opp_t = be_b, se_b, be_t, se_t
        else:                                               # dip: leading = sellers, opposing = buyers
            lead_b, opp_b, lead_t, opp_t = se_b, be_b, se_t, be_t
        lead_fell = lead_t < lead_b
        opp_rose = opp_t > opp_b
        signature = lead_fell and opp_rose
        crossover = (opp_t - opp_b) - (lead_t - lead_b)
        p_next = legs[m + 1][3]
        cont = (p_next < p0) if eh else (p_next > p0)
        netV = sum(buy[i] - sell[i] for i in range(b0 + 1, b1 + 1))
        diverge = netV != 0 and ((netV > 0) != (dP[m] > 0))
        rows.append(dict(cont=bool(cont), eh=eh, lead_fell=lead_fell, opp_rose=opp_rose,
                         signature=signature, crossover=crossover, diverge=diverge,
                         year=dt.datetime.utcfromtimestamp(float(buck[b1]["start_time"])).year))

    if len(rows) < 50:
        print("  too few analyzable retracements (%d)" % len(rows)); return
    base = line(rows, "BASE (analyzable retracements)")
    print("\n  --- the HYPOTHESIS: reward FLIP (leading reward-per-effort FELL + opposing ROSE into the pivot) ---")
    line([r for r in rows if r["signature"]], "SIGNATURE present", base)
    line([r for r in rows if not r["signature"]], "signature absent", base)
    print("\n  --- the two sides (disjoint 2x2) ---")
    line([r for r in rows if r["lead_fell"] and r["opp_rose"]], "lead FELL & opp ROSE", base)
    line([r for r in rows if r["lead_fell"] and not r["opp_rose"]], "lead FELL & opp fell", base)
    line([r for r in rows if not r["lead_fell"] and r["opp_rose"]], "lead rose & opp ROSE", base)
    line([r for r in rows if not r["lead_fell"] and not r["opp_rose"]], "lead rose & opp fell", base)
    print("\n  --- each side alone ---")
    line([r for r in rows if r["lead_fell"]], "leading reward FELL into tail", base)
    line([r for r in rows if r["opp_rose"]], "opposing reward ROSE into tail", base)
    print("\n  --- DISJOINT bands of crossover strength (opp gain minus lead gain) ---")
    xs = sorted(r["crossover"] for r in rows)
    qs = [xs[int(len(xs) * f)] for f in (0.2, 0.4, 0.6, 0.8)]
    edges = [-1e18] + qs + [1e18]
    for a, b in zip(edges[:-1], edges[1:]):
        line([r for r in rows if a <= r["crossover"] < b],
             "crossover [%s,%s)" % ("%.2g" % a if a > -1e17 else "-inf", "%.2g" % b if b < 1e17 else "inf"), base)
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
