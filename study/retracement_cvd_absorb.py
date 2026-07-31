"""What makes a RETRACEMENT a retracement — in CVD + swing absorb-A terms.

Retracement (user definition): a ZigZag leg that is OPPOSITE its previous swing (automatic — legs alternate) AND
whose |dP| < |dP| of the PRECEDING leg. So among the alternating legs, a retracement is a counter-move SMALLER than
the move before it; the complement (|dP| >= the preceding leg) is an IMPULSE / EXPANSION.

For each leg we measure:
    dP   price swing (%),  dV = CVD swing (net buy-sell delta over the leg)
    A    whole-swing absorb-A (Z(dP) - rho*Z(dV) vs the trailing legs; + = ABSORBED, - = EASY, ~0 = proportional)
    A1..A4  the leg split into 4 even-by-bar QUARTERS, each quarter's absorb-A vs prior legs' matching quarter.
Then compares the RETRACEMENT vs IMPULSE cohorts across all of it. Descriptive, causal (baseline = prior legs only).

CLI: python study/retracement_cvd_absorb.py [tf] [thr_pct]
"""
import gzip, json, glob, os, sys, math, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S
from app import swing_lvn_detect as SL          # reuse _leg_segments (N-quarter split) + _swing_A (absorb-A)

W_LEGS = 30          # trailing legs for the absorb-A z-score baseline


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


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else float("nan")


def _band(a):
    if a is None:
        return "--"
    return ("ABS" if a >= 1.5 else "heavy") if a >= 0.75 else (("EASY" if a <= -1.5 else "light") if a <= -0.75 else "prop")


def rep(rows, title):
    n = len(rows)
    if n == 0:
        print("  %-12s n=0" % title); return
    dPs = [r["dP"] for r in rows]; dVs = [r["dV"] for r in rows]
    agree = sum(1 for r in rows if (r["dV"] > 0) == (r["dP"] > 0))
    ratio = [abs(r["dV"]) / abs(r["dP"]) for r in rows if abs(r["dP"]) > 1e-9]
    A = [r["A"] for r in rows if r["A"] is not None]
    A1 = [r["As"][0] if len(r["As"]) > 0 else None for r in rows]
    A2 = [r["As"][1] if len(r["As"]) > 1 else None for r in rows]
    A3 = [r["As"][2] if len(r["As"]) > 2 else None for r in rows]
    A4 = [r["As"][3] if len(r["As"]) > 3 else None for r in rows]
    print("  %-12s n=%-4d |dP|=%.2f%%  |dV|=%.0f  |dV|/|dP|=%.0f  flow-agrees=%.0f%%  A=%+.2f  A1..A4=%+.2f/%+.2f/%+.2f/%+.2f" % (
        title, n, st.median([abs(x) for x in dPs]), st.median([abs(x) for x in dVs]),
        st.median(ratio) if ratio else 0, 100.0 * agree / n, _mean(A), _mean(A1), _mean(A2), _mean(A3), _mean(A4)))


def rep_bands(rows, title):
    A = [r["A"] for r in rows if r["A"] is not None]
    if not A:
        return
    from collections import Counter
    c = Counter(_band(a) for a in A); m = len(A)
    order = ["EASY", "light", "prop", "heavy", "ABS"]
    print("  %-12s A-bands: %s" % (title, "  ".join("%s %.0f%%" % (b, 100.0 * c.get(b, 0) / m) for b in order)))


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
    print("%s thr=%.2f%%  |  %d buckets %s..%s  |  %d legs" % (
        tf, thr * 100, n,
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date(), len(legs)), flush=True)
    if len(legs) < W_LEGS + 5:
        print("  too few legs"); return

    feats = []                                              # per leg: (whole (dP,dV), [q1..q4 (dP,dV)])
    for (b0, p0, b1, p1, eh) in legs:
        wp, _, _ = SL._leg_segments(cum, C, b0, p0, b1, p1, 1)
        qp, _, _ = SL._leg_segments(cum, C, b0, p0, b1, p1, 4)
        feats.append((wp[0], qp))

    rows = []
    for m in range(1, len(legs)):                           # need the PRECEDING leg
        wpair, qpairs = feats[m]
        dP, dV = wpair
        is_retr = abs(dP) < abs(feats[m - 1][0][0])
        prior = range(max(0, m - W_LEGS), m)
        A = SL._swing_A([feats[j][0] for j in prior], wpair)
        As = []
        for k in range(len(qpairs)):
            base = [feats[j][1][k] for j in prior if k < len(feats[j][1])]
            As.append(SL._swing_A(base, qpairs[k]))
        rows.append(dict(is_retr=is_retr, dP=dP, dV=dV, eh=legs[m][4], A=A, As=As))

    retr = [r for r in rows if r["is_retr"]]; imp = [r for r in rows if not r["is_retr"]]
    print("\n  retracements = %d (%.0f%%)   impulses = %d (%.0f%%)   (of %d legs w/ a predecessor)" % (
        len(retr), 100.0 * len(retr) / len(rows), len(imp), 100.0 * len(imp) / len(rows), len(rows)))
    print("\n  (flow-agrees = %% of legs whose net CVD sign matches the price-move sign)")
    rep(rows, "ALL")
    rep(retr, "RETRACEMENT")
    rep(imp, "IMPULSE")
    print()
    rep_bands(retr, "RETRACEMENT")
    rep_bands(imp, "IMPULSE")
    # retracements split by direction of the retrace
    print()
    rep([r for r in retr if r["eh"]], "  retr UP")
    rep([r for r in retr if not r["eh"]], "  retr DOWN")


if __name__ == "__main__":
    main()
