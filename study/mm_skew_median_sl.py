"""MM×Skew 1h — test SL at the MEDIAN (0.1% beyond it) vs the frozen SL at the candle extreme.
Two 'median' readings: vpmed = volume-profile median price (wickerplot median, 50% of traded volume);
hlmid = (high+low)/2. Entry/skew/spread/POC rules unchanged (study/MMXSKEW.md). A median stop can fall on
the WRONG side of the entry (median above close on a long) -> that trade is INVALID (skipped, counted).
SL-first, TP=RR*SL. Compare win%, avg SL distance, valid n, equity gross/net@0.08%.
Run:  python study/mm_skew_median_sl.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_strategy as S

RRS = [0.5, 0.7, 1.0, 1.5]
SL_BUF = 0.001


def vp_median(levels):
    items = sorted((float(p), float(v.get("b", 0.0)) + float(v.get("s", 0.0)))
                   for p, v in (levels or {}).items())
    tot = sum(w for _, w in items)
    if tot <= 0:
        return None
    cum = 0.0
    for p, w in items:
        cum += w
        if cum >= tot / 2.0:
            return p
    return items[-1][0]


def sim(M, i, side, rr, ref):
    """ref in {'extreme','vpmed','hlmid'}. Returns (outcome, retf, jclose) or None(invalid/skip)."""
    b = M[i]; entry = b["c"]
    if ref == "extreme":
        m = b["l"] if side > 0 else b["h"]
    elif ref == "vpmed":
        m = b["vpmed"]
    else:
        m = b["hlmid"]
    if m is None:
        return None
    if side > 0:
        sl = m * (1.0 - SL_BUF)
        if sl >= entry:
            return None                      # stop not below entry -> invalid
        sld = entry - sl; tp = entry + rr * sld
    else:
        sl = m * (1.0 + SL_BUF)
        if sl <= entry:
            return None
        sld = sl - entry; tp = entry - rr * sld
    slf = sld / entry
    for j in range(i + 1, len(M)):
        hi = M[j]["h"]; lo = M[j]["l"]
        if side > 0:
            htp = hi >= tp; hsl = lo <= sl
        else:
            htp = lo <= tp; hsl = hi >= sl
        if htp and hsl:
            return ("SL", -1.0 * slf, j)      # SL-first
        if htp:
            return ("TP", rr * slf, j)
        if hsl:
            return ("SL", -1.0 * slf, j)
    return ("OPEN", (M[-1]["c"] - entry) / entry * side, len(M) - 1)


def collect(M, rr, ref):
    out = []; invalid = 0
    for i in range(len(M) - 1):
        s = P.sig(M[i])
        if s == 0:
            continue
        res = sim(M, i, s, rr, ref)
        if res is None:
            invalid += 1; continue
        out.append(dict(side=s, win=(res[0] == "TP"), retf=res[1], slf=abs(res[1]) / (rr if res[0] == "TP" else 1.0),
                        jclose=res[2], i=i))
    return out, invalid


def equity(M, rr, ref, fee, side_only=0):
    bal = S.BAL0; i = 0; n = w = 0; peak = bal; dd = 0.0
    while i < len(M) - 1:
        s = P.sig(M[i])
        if s == 0 or (side_only and s != side_only):
            i += 1; continue
        res = sim(M, i, s, rr, ref)
        if res is None:
            i += 1; continue
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * res[1] - notional * fee
        n += 1; w += (1 if res[0] == "TP" else 0)
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = res[2] + 1
    return (bal / S.BAL0 - 1) * 100.0, n, dd * 100.0


def main():
    M, span = P.build()
    for i in range(len(M)):
        M[i]["vpmed"] = vp_median(M[i].get("levels"))
        M[i]["hlmid"] = (M[i]["h"] + M[i]["l"]) / 2.0
    print(f"mature 1h bars {len(M)}  span {span:.1f}d\n")

    for ref, name in (("extreme", "SL @ EXTREME (frozen baseline)"), ("vpmed", "SL @ VOLUME-PROFILE MEDIAN"),
                      ("hlmid", "SL @ H/L MIDPOINT")):
        print("=" * 96); print(name); print("=" * 96)
        print(f"  {'RR':>5} {'valid':>6} {'inval':>5} {'win%':>6} {'avgSL%':>7} | "
              f"{'eqGROSS':>8} {'eqNET':>7} | {'LONGnet':>8} {'SHORTnet':>9}")
        for rr in RRS:
            sigs, inv = collect(M, rr, ref)
            if not sigs:
                print(f"  1:{rr}  no valid trades"); continue
            win = 100.0 * sum(1 for x in sigs if x["win"]) / len(sigs)
            avgsl = 100.0 * statistics.mean(x["slf"] for x in sigs)
            gg, ng, dg = equity(M, rr, ref, 0.0); gn, _, _ = equity(M, rr, ref, 0.0008)
            gl, _, _ = equity(M, rr, ref, 0.0008, +1); gs, _, _ = equity(M, rr, ref, 0.0008, -1)
            print(f"  1:{rr:>3} {len(sigs):>6} {inv:>5} {win:>6.1f} {avgsl:>7.3f} | "
                  f"{gg:>+7.1f}% {gn:>+6.1f}% | {gl:>+7.1f}% {gs:>+8.1f}%")
        print()


if __name__ == "__main__":
    main()
