"""PREDICTIVE test: does a retracement's CVD / absorb-A signature forecast the NEXT (resumption) leg?

For each RETRACEMENT leg L (|dP| < preceding leg), the next leg L+1 is the trend-resumption direction (ZigZag
alternates). OUTCOME = does L+1 make a NEW trend extreme beyond the pre-retracement extreme (p0 of L)?
    up-retracement (bounce in a downtrend): CONTINUE = next low  <  p0   (new low)
    down-retracement (dip in an uptrend)  : CONTINUE = next high >  p0   (new high)
CONTINUE = the trend resumed; else = the resumption FAILED (a lower-high / higher-low -> reversal risk).

Conditions on the retracement's features (all known at its completion -> causal): CVD divergence (net delta sign vs
price direction), whole absorb-A, last-quarter A4, and direction. Reports continuation rate + binomial p vs the base
rate + a 2025/2026 split for durability.

CLI: python study/retracement_predict.py [tf] [thr_pct]
"""
import gzip, json, glob, os, sys, math, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S
from app import swing_lvn_detect as SL

W_LEGS = 30


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


def _z_p(k, n, p0):
    """two-sided p that k/n differs from p0 (normal approx w/ continuity correction; fine for these n)."""
    if n == 0 or p0 <= 0 or p0 >= 1:
        return 1.0
    mu = n * p0; sd = math.sqrt(n * p0 * (1 - p0))
    if sd == 0:
        return 1.0
    z = (abs(k - mu) - 0.5) / sd
    return math.erfc(z / math.sqrt(2))          # 2 * (1 - Phi(z))


def line(rows, title, base=None):
    n = len(rows)
    if n == 0:
        print("  %-30s n=0" % title); return None
    k = sum(1 for r in rows if r["cont"])
    rate = k / n
    extra = ""
    if base is not None:
        extra = "  d=%+.1fpp  p=%.3f" % ((rate - base) * 100, _z_p(k, n, base))
    print("  %-30s n=%-4d  continue=%.1f%%%s" % (title, n, 100 * rate, extra))
    return rate


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 2.0) / 100.0
    buck = load_tf(tf); n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in buck]
    dlt = [float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0) for b in buck]
    cum = [0.0] * (n + 1)
    for i in range(n):
        cum[i + 1] = cum[i] + dlt[i]
    piv = S._zigzag_confirmed(H, L, thr)
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2])
            for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    st_year = [dt.datetime.utcfromtimestamp(float(buck[lg[2]]["start_time"])).year for lg in legs]
    print("%s thr=%.2f%%  |  %d legs %s..%s" % (
        tf, thr * 100, len(legs),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)

    feats = []
    for (b0, p0, b1, p1, eh) in legs:
        wp, _, _ = SL._leg_segments(cum, C, b0, p0, b1, p1, 1)
        qp, _, _ = SL._leg_segments(cum, C, b0, p0, b1, p1, 4)
        feats.append((wp[0], qp))

    rows = []
    for m in range(1, len(legs) - 1):                       # need preceding (m-1) AND next (m+1)
        wpair, qpairs = feats[m]
        dP, dV = wpair
        if abs(dP) >= abs(feats[m - 1][0][0]):              # not a retracement
            continue
        b0, p0, b1, p1, eh = legs[m]
        p_next = legs[m + 1][3]                             # extreme the resumption leg reaches
        cont = (p_next < p0) if eh else (p_next > p0)       # up-retr -> new low ; down-retr -> new high
        prior = range(max(0, m - W_LEGS), m)
        A = SL._swing_A([feats[j][0] for j in prior], wpair)
        A4 = None
        if len(qpairs) >= 4:
            base4 = [feats[j][1][3] for j in prior if len(feats[j][1]) >= 4]
            A4 = SL._swing_A(base4, qpairs[3])
        rows.append(dict(cont=bool(cont), eh=eh, dV=dV, dP=dP, A=A, A4=A4,
                         agree=((dV > 0) == (dP > 0)), year=st_year[m]))

    if len(rows) < 50:
        print("  too few retracements"); return
    base = line(rows, "BASE (all retracements)")
    print("\n  --- CVD divergence (net delta sign vs the retracement's price direction) ---")
    line([r for r in rows if r["agree"]], "flow CONFIRMS the move", base)
    line([r for r in rows if not r["agree"]], "flow DIVERGES (unpaid move)", base)
    print("\n  --- by direction x divergence ---")
    line([r for r in rows if r["eh"] and not r["agree"]], "UP-retr (bounce) + CVD sells", base)
    line([r for r in rows if r["eh"] and r["agree"]], "UP-retr (bounce) + CVD buys", base)
    line([r for r in rows if not r["eh"] and not r["agree"]], "DOWN-retr (dip) + CVD buys", base)
    line([r for r in rows if not r["eh"] and r["agree"]], "DOWN-retr (dip) + CVD sells", base)
    print("\n  --- whole-swing absorb-A of the retracement ---")
    line([r for r in rows if r["A"] is not None and r["A"] <= -0.5], "A <= -0.5  (EASY retrace)", base)
    line([r for r in rows if r["A"] is not None and -0.5 < r["A"] < 0.5], "-0.5 < A < 0.5  (proportional)", base)
    line([r for r in rows if r["A"] is not None and r["A"] >= 0.5], "A >= +0.5  (ABSORBED retrace)", base)
    print("\n  --- last-quarter A4 (does the retrace lose steam at its end) ---")
    line([r for r in rows if r["A4"] is not None and r["A4"] >= 0.5], "A4 >= +0.5 (absorbed tail)", base)
    line([r for r in rows if r["A4"] is not None and r["A4"] <= -0.5], "A4 <= -0.5 (easy tail)", base)
    print("\n  --- durability of the DIVERGENCE cut (2025 vs 2026) ---")
    for y in (2025, 2026):
        line([r for r in rows if not r["agree"] and r["year"] == y], "flow DIVERGES · %d" % y, base)


if __name__ == "__main__":
    main()
