"""Quantify a retracement: how DEEP does it pull back, and how FAR does the next move extend.

For each RETRACEMENT leg L (|dP_L| < |dP| of the preceding leg L-1):
    retrace depth  = |dP_L|   / |dP_{L-1}| * 100%   (100% = retrace equals the prior swing; <100% by definition)
    follow-through = |dP_{L+1}| / |dP_L|   * 100%   (150% = the next move's dP is 50% bigger than the retrace)
Reports median (= "generally expect", robust to outliers), mean, and the p25..p75 spread — for ALL retracements
and, primarily, for the CVD-DIVERGENT ("unpaid") ones (net delta opposes the retrace's price direction).

CLI: python study/retracement_measure.py [tf] [thr_pct]
"""
import gzip, json, glob, os, sys, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S
from app import swing_lvn_detect as SL


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


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs); k = (len(s) - 1) * p / 100.0; f = int(k)
    return s[f] if f + 1 >= len(s) else s[f] + (s[f + 1] - s[f]) * (k - f)


def rep(sub, title):
    if not sub:
        print("  %-26s n=0" % title); return
    d = [r["depth"] for r in sub]; e = [r["ext"] for r in sub]
    print("  %-26s n=%-4d  DEPTH med=%3.0f%% mean=%3.0f%% [%3.0f..%3.0f]   FOLLOW med=%3.0f%% mean=%3.0f%% [%3.0f..%3.0f]" % (
        title, len(sub),
        st.median(d), st.mean(d), _pct(d, 25), _pct(d, 75),
        st.median(e), st.mean(e), _pct(e, 25), _pct(e, 75)))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "5m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 1.0) / 100.0
    buck = load_tf(tf); n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    dlt = [float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0) for b in buck]
    cum = [0.0] * (n + 1)
    for i in range(n):
        cum[i + 1] = cum[i] + dlt[i]
    piv = S._zigzag_confirmed(H, L, thr)
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2])
            for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    print("%s thr=%.2f%%  |  %d legs %s..%s" % (
        tf, thr * 100, len(legs),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)
    dP = []; dV = []
    for (b0, p0, b1, p1, eh) in legs:
        dP.append((p1 - p0) / p0 * 100.0 if p0 > 0 else 0.0)
        dV.append(cum[b1 + 1] - cum[b0 + 1])

    rows = []
    for m in range(1, len(legs) - 1):
        if abs(dP[m]) >= abs(dP[m - 1]) or abs(dP[m - 1]) < 1e-6 or abs(dP[m]) < 1e-6:
            continue                                            # not a retracement / degenerate
        rows.append(dict(depth=abs(dP[m]) / abs(dP[m - 1]) * 100.0,
                         ext=abs(dP[m + 1]) / abs(dP[m]) * 100.0,
                         diverge=(dV[m] != 0 and ((dV[m] > 0) != (dP[m] > 0))),
                         eh=legs[m][4]))
    print("  (DEPTH = retrace / prior swing; FOLLOW = next move / retrace; median in bold-ish, [p25..p75])\n")
    rep(rows, "ALL retracements")
    rep([r for r in rows if r["diverge"]], "CVD-DIVERGENT (unpaid)")
    rep([r for r in rows if not r["diverge"]], "CVD-confirmed")
    rep([r for r in rows if r["diverge"] and r["eh"]], "  divergent UP (bounce)")
    rep([r for r in rows if r["diverge"] and not r["eh"]], "  divergent DOWN (dip)")


if __name__ == "__main__":
    main()
