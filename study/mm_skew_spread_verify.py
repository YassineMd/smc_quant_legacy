"""Harden the panel-2 non-locked spread edge (aligned>=35 separates W/L) — the checks the killed
subagents were meant to run, done directly. Threshold plateau, overlap-corrected significance, per-side,
redundancy vs body, net-of-fees, and design-perturbation robustness.
Run:  python study/mm_skew_spread_verify.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_spread as SP
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

RRS = [0.5, 0.7, 1.0, 1.5]
M = SP.load_with_spread()


def twoprop(k1, n1, k2, n2):
    if n1 < 5 or n2 < 5:
        return float("nan"), float("nan")
    p1 = k1 / n1; p2 = k2 / n2; p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)); z = (p1 - p2) / se if se > 0 else 0.0
    return z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def collect2(M, rr, cp=S.CP_THR, both="sl", slbuf=None):
    old = RR.SL_BUF
    if slbuf is not None:
        RR.SL_BUF = slbuf
    out = []
    for i in range(len(M) - 1):
        s = S.signal(M[i], cp)
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, both)
        if res is None:
            continue
        out.append(dict(side=s, win=(res[0] == "TP"), aligned=M[i]["spread"] * s, i=i,
                        body=abs(M[i]["c"] - M[i]["o"]) / M[i]["o"] * 100.0))
    RR.SL_BUF = old
    return out


def gap(sigs, thr=35.0):
    hi = [x for x in sigs if x["aligned"] >= thr]; lo = [x for x in sigs if x["aligned"] < thr]
    wh = sum(1 for x in hi if x["win"]); wl = sum(1 for x in lo if x["win"])
    z, p = twoprop(wh, len(hi), wl, len(lo)) if hi and lo else (float("nan"), float("nan"))
    rh = 100 * wh / len(hi) if hi else float("nan"); rl = 100 * wl / len(lo) if lo else float("nan")
    return rh, rl, rh - rl, len(hi), len(lo), p


def taken(M, rr, both="sl"):
    i = 0; out = []
    while i < len(M) - 1:
        s = S.signal(M[i], S.CP_THR)
        if s == 0:
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, both)
        if res is None:
            i += 1; continue
        out.append(dict(aligned=M[i]["spread"] * s, win=(res[0] == "TP")))
        i = res[2] + 1
    return out


def equity_f(M, rr, thr, fee):
    bal = S.BAL0; i = 0; n = w = 0
    while i < len(M) - 1:
        s = S.signal(M[i], S.CP_THR)
        if s == 0:
            i += 1; continue
        if thr is not None and M[i]["spread"] * s < thr:
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * res[1] - notional * fee
        n += 1; w += (1 if res[0] == "TP" else 0)
        i = res[2] + 1
    return (bal / S.BAL0 - 1) * 100.0, n, (100 * w / n if n else float("nan"))


print("=" * 92); print("1. THRESHOLD PLATEAU  (win% of aligned>=thr subset; is 35 special or a plateau?)"); print("=" * 92)
for rr in RRS:
    sigs = collect2(M, rr)
    base = 100 * sum(1 for x in sigs if x["win"]) / len(sigs)
    row = []
    for thr in range(0, 85, 10):
        sub = [x for x in sigs if x["aligned"] >= thr]
        row.append(f"{thr:>2}:{(100*sum(1 for x in sub if x['win'])/len(sub) if sub else 0):.0f}%(n{len(sub)})")
    print(f"  1:{rr} base {base:.0f}% | " + " ".join(row))

print("\n" + "=" * 92); print("2. OVERLAP-CORRECTED  (NON-overlapping taken trades only; aligned>=35 vs <35)"); print("=" * 92)
for rr in RRS:
    t = taken(M, rr)
    rh, rl, g, nh, nl, p = gap(t, 35)
    print(f"  1:{rr}  taken={len(t)}  >=35: {rh:.1f}%(n{nh})  <35: {rl:.1f}%(n{nl})  gap={g:+.1f}pp  2-prop p={p:.3f}")

print("\n" + "=" * 92); print("3. PER-SIDE  (does the edge exist on LONGS alone AND SHORTS alone?)"); print("=" * 92)
for rr in RRS:
    sigs = collect2(M, rr)
    for side, sn in ((+1, "LONG "), (-1, "SHORT")):
        ss = [x for x in sigs if x["side"] == side]
        rh, rl, g, nh, nl, p = gap(ss, 35)
        print(f"  1:{rr} {sn}: >=35 {rh:.1f}%(n{nh}) / <35 {rl:.1f}%(n{nl})  gap={g:+.1f}pp p={p:.3f}")
    print()

print("=" * 92); print("4. REDUNDANCY vs BODY  (is >=35 just re-selecting small-body longs?)"); print("=" * 92)
sigs = collect2(M, 1.0)
lb = [x for x in sigs if x["side"] == +1]
med_body = statistics.median(x["body"] for x in lb)
import statistics as _st
xs = [x["aligned"] for x in lb]; ys = [x["body"] for x in lb]
mx = _st.mean(xs); my = _st.mean(ys)
cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)); den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
print(f"  corr(aligned, body) on longs = {cov/den:+.3f}  (near 0 => independent of body)")
for lbl, keep in (("small-body", lambda x: x["body"] <= med_body), ("large-body", lambda x: x["body"] > med_body)):
    sub = [x for x in lb if keep(x)]
    rh, rl, g, nh, nl, p = gap(sub, 35)
    print(f"  {lbl} longs: >=35 {rh:.1f}%(n{nh}) / <35 {rl:.1f}%(n{nl})  gap={g:+.1f}pp  (>=35 helps in BOTH => not redundant)")

print("\n" + "=" * 92); print("5. NET OF FEES  (filtered aligned>=35 vs unfiltered; equity return %, 0.08% rt)"); print("=" * 92)
for rr in RRS:
    gu, nu, wu = equity_f(M, rr, None, 0.0008); gf, nf, wf = equity_f(M, rr, 35, 0.0008)
    guG, _, _ = equity_f(M, rr, None, 0.0); gfG, _, _ = equity_f(M, rr, 35, 0.0)
    print(f"  1:{rr}  UNfiltered net {gu:+.1f}% (n{nu}, gross {guG:+.1f}%)  |  >=35 filtered net {gf:+.1f}% (n{nf}, win{wf:.0f}%, gross {gfG:+.1f}%)")

print("\n" + "=" * 92); print("6. ROBUSTNESS  (aligned>=35 gap under design perturbations; sign should hold)"); print("=" * 92)
perts = [("baseline", dict()), ("TP-first", dict(both="tp")), ("cp=0.50", dict(cp=0.50)),
         ("cp=0.85", dict(cp=0.85)), ("SLbuf=0", dict(slbuf=0.0)), ("SLbuf=0.2%", dict(slbuf=0.002))]
for name, kw in perts:
    line = []
    for rr in RRS:
        _, _, g, _, _, _ = gap(collect2(M, rr, **kw), 35)
        line.append(f"1:{rr} {g:+.0f}")
    print(f"  {name:12s}: " + "  ".join(line) + "  (pp gap >=35 vs <35)")
# stricter mature cut
M2 = [m for m in M if m["tv"] >= 200000.0]
print(f"  mature>=200k (n{len(M2)}): " + "  ".join(
    f"1:{rr} {gap(collect2(M2, rr),35)[2]:+.0f}" for rr in RRS))
# calendar halves
mid = len(M) // 2
for half, sub in (("H1", M[:mid]), ("H2", M[mid:])):
    print(f"  {half}: " + "  ".join(f"1:{rr} {gap(collect2(sub, rr),35)[2]:+.0f}" for rr in RRS))
