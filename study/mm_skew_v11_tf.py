"""MMXSKEW v1.1 (current frozen gate) on ANY timeframe — parity-checked against the 1h baseline.

Gate (mirrors app/mmxskew_detect.detect's v11 flag):
  BASE  close>open & skew>0 & spread>=+35   |  + DELTA 0<d<=15  |  + MOMENTUM spread(i)>spread(i-1)  |  + A_h2<0
  (SHORT = mirror; delta<0; spread(i)<spread(i-1))
  exit: SL 0.1% beyond the bucket extreme, TP = RR x SL, SL-first on a same-bar tie, fee 0.08%.

The harness is a LEAN tf-generalised rebuild of study.mm_skew_feature_matrix.build() (which hardcodes 1h and a
1h-calibrated maturity floor). Because a rebuilt harness can silently differ, `python study/mm_skew_v11_tf.py 1h`
PARITY-CHECKS itself against study/mm_skew_v11_stats.py's numbers — run that first and only trust the 15m output
if 1h reproduces.

MATURITY FLOOR scales with the timeframe: target_vol[tf] = anchor * tf_seconds/60, so the 1h floor of 100000
becomes 100000 * (tf_secs/3600). Early buckets sit at target_vol=5000 (pre-rescale) and are excluded either way.

Run:  python study/mm_skew_v11_tf.py 15m     (or 1h / 4h)
"""
from __future__ import annotations
import os, sys, math, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS, config
from app import pivot_detect as PD
from app.footprint_panel import profile_skewness
from study.archive_loader import load_archive
from app import archive
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

RRS = (1.0, 1.5)
FEE = 0.0008
MATURE_1H = 100000.0


def build(tf: str):
    """Lean per-tf matrix: OHLC + skew + delta + causal eff-agg spread + the half-fields needed by A_h2."""
    _, raws, _ = load_archive(tf)
    ov = archive._load_overlay()
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r)
        d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["open"] = o; d["close"] = c
        d["tv"] = float(r.get("target_vol", 0.0) or 0.0)
        d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o
        cv = float(r.get("curr_vol", 0.0)) or 1.0
        d["delta"] = (float(r.get("buy_vol", 0.0)) - float(r.get("sell_vol", 0.0))) / cv * 100.0
        pair = ov.get("%s|%.3f" % (tf, float(r.get("start_time", 0.0) or 0.0)))
        if pair:
            if d.get("delta_h1") is None and pair[0] is not None:
                d["delta_h1"] = pair[0]
            if d.get("price_h1") is None and pair[1] is not None:
                d["price_h1"] = pair[1]
        A.append(d)
    n = len(A)
    spr = (2.0 * np.asarray(PD.eff_causal_share(A), float) - 1.0) * 100.0
    for i in range(n):
        A[i]["spread"] = float(spr[i])
    floor = MATURE_1H * (config.TF_SECONDS.get(tf, 3600) / 3600.0)
    first = next((i for i in range(n) if A[i]["tv"] >= floor
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, n))]) >= floor), n)
    return A, first, floor


def gate(A, first):
    """The frozen v1.1 gate; returns (signals, funnel counts)."""
    base = dlt = mom = 0; out = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35:
            s = 1
        elif b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
            s = -1
        else:
            continue
        base += 1
        d = b["delta"]
        if not ((0.0 < d <= 15.0) if s > 0 else (d < 0.0)):
            continue
        dlt += 1
        if not ((b["spread"] - A[i - 1]["spread"]) * s > 0.0):
            continue
        mom += 1
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0))))
    return out, (base, dlt, mom, len(out))


def taken(A, sigs, rr):
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"]))
        last = res[2]
    return sorted(out, key=lambda z: z["t"])


def binom_ge(k, n, p):
    """One-sided P(X >= k) for X~Bin(n,p). Exact for small n; for large n the exact sum overflows float, so a
    normal approximation with continuity correction is used (accurate to <0.001 at these sizes)."""
    if not n:
        return float("nan")
    if n <= 300:
        return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    mu = n * p; sd = math.sqrt(n * p * (1 - p))
    if sd <= 0:
        return 1.0 if k <= mu else 0.0
    z = (k - 0.5 - mu) / sd
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def block(rows, be, label):
    n = len(rows)
    if n == 0:
        print("  %-14s n=0" % label); return
    w = sum(1 for r in rows if r["win"])
    net = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + net) - 1) * 100
    g = net[net > 0].sum(); ls = -net[net < 0].sum()
    pf = (g / ls) if ls > 0 else float("inf")
    path = np.cumprod(1 + net); peak = np.maximum.accumulate(path)
    dd = float(np.max((peak - path) / peak)) * 100
    print("  %-14s n=%3d  W/L %3d/%-3d  win %5.1f%% (BE %.0f%%)  net %+7.1f%%  mean %+.3f%%  PF %5.2f  maxDD %4.1f%%  P=%.3f"
          % (label, n, w, n - w, 100.0 * w / n, be * 100, tot, net.mean() * 100, pf, dd, binom_ge(w, n, be)))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    rrs = [float(x) for x in sys.argv[2:]] or list(RRS)     # e.g. `... 4h 0.5 1.0 1.5`
    A, first, floor = build(tf)
    sigs, (nb, nd, nm, nf) = gate(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    span = (A[-1]["start_time"] - A[first]["start_time"]) / 86400.0 if first < len(A) else 0.0
    print("=" * 108)
    print("MMXSKEW v1.1 — CURRENT FROZEN GATE on %s" % tf.upper())
    print("=" * 108)
    print("data: %d mature %s buckets of %d, %.1f days (%s -> %s UTC)   [maturity floor target_vol >= %.0f]"
          % (len(A) - first, tf, len(A), span,
             dt.datetime.utcfromtimestamp(A[first]["start_time"]).strftime("%Y-%m-%d"),
             dt.datetime.utcfromtimestamp(A[-1]["start_time"]).strftime("%Y-%m-%d"), floor))
    print("funnel: base(spread) %d -> +delta %d -> +momentum %d -> +A_h2<0 %d   (%dL / %dS)"
          % (nb, nd, nm, nf, nl, nf - nl))
    print()
    for rr in rrs:
        be = 1.0 / (1 + rr)
        T = taken(A, sigs, rr)
        print("-" * 108)
        print("RR 1:%.1f" % rr)
        print("-" * 108)
        block(T, be, "ALL")
        block([r for r in T if r["side"] > 0], be, "LONG")
        block([r for r in T if r["side"] < 0], be, "SHORT")
        if len(T) >= 4:
            mid = len(T) // 2
            block(T[:mid], be, "  H1 (older)")
            block(T[mid:], be, "  H2 (recent)")
        bal = S.BAL0
        for r in T:
            bal += S.POS_FRAC * bal * S.LEV * r["net"]
        print("  account: $%s -> $%s  (%+.1f%%)" % (f"{S.BAL0:,.0f}", f"{bal:,.0f}", (bal / S.BAL0 - 1) * 100))
        print()
    if tf == "1h":
        print("PARITY CHECK vs study/mm_skew_v11_stats.py -> expect funnel 239/166/137/48 (17L/31S)")
        print("  got %d/%d/%d/%d (%dL/%dS)  ->  %s" % (nb, nd, nm, nf, nl, nf - nl,
              "MATCH" if (nb, nd, nm, nf, nl) == (239, 166, 137, 48, 17) else "*** MISMATCH — do not trust other tfs ***"))


if __name__ == "__main__":
    main()
