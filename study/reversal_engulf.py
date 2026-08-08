# -*- coding: utf-8 -*-
"""Does requiring candle 3 to ENGULF the prior candle improve the Reversal Point detector?

Test the user's claim: "the engulfing hammers on this detector are the best." Take the SHIPPED detector
flags (fresh LB-bar extreme + hammer CIR/WICK/bull + delta-shift DS) and overlay three engulf definitions,
measuring incremental precision vs the plain hammer AND vs the current STRONG tier (run_down<=2 & sellconc>=40).

  body   : classic engulf - prior candle opposite-coloured, c3 BODY swallows prior body.
  range  : c3 HIGH-LOW swallows the prior candle's whole range (wicks included).
  reclaim: loose - c3 closes back beyond the prior candle's open (reclaim close).

Reported on a NON-OVERLAP (taken) set + exact binomial p vs the fresh-extreme base rate, split by year, both sides.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
try:
    from scipy.stats import binomtest
    def pval(k, n, p):
        return binomtest(k, n, p, alternative="greater").pvalue if n else 1.0
except Exception:
    from math import comb
    def pval(k, n, p):
        return sum(comb(n, j) * p**j * (1 - p)**(n - j) for j in range(k, n + 1)) if n else 1.0

# detector thresholds (identical across tf) ------------------------------------------------
LB, CIR, WICK, DS, RUN_MAX, SELLCONC, RUN_CAP = 6, 0.55, 0.25, 3.0, 2, 40.0, 6

def sellconc(b, hi, lo, down):
    lv = b.get("levels") or {}; rng = hi - lo
    if not lv or rng <= 0: return 0.0
    thr = (lo + rng / 3.0) if down else (hi - rng / 3.0); seg = tot = 0.0
    for ps, vv in lv.items():
        try: p = float(ps)
        except (TypeError, ValueError): continue
        v = _f(vv.get("s")) if down else _f(vv.get("b")); tot += v
        if (p <= thr) if down else (p >= thr): seg += v
    return (seg / tot * 100.0) if tot > 0 else 0.0


def run_test(tf, R, LF=6):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

    def run(i, down):
        r = 0
        for k in range(i - 1, max(-1, i - 1 - RUN_CAP), -1):
            if (C[k] < O[k]) if down else (C[k] > O[k]): r += 1
            else: break
        return r
    def rev(i, down):
        if down: return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
        return max(H[i + 1:i + 1 + LF]) <= H[i] and (H[i] - min(L[i + 1:i + 1 + LF])) / H[i] >= R

    # base rate over ALL fresh-extreme candidates (both sides)
    fresh = []
    for i in range(LB, n - LF - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        if L[i] <= min(L[i - LB:i]): fresh.append((i, True))
        if H[i] >= max(H[i - LB:i]): fresh.append((i, False))
    base = sum(rev(i, d) for i, d in fresh) / len(fresh)

    # detector core flags (both sides) + engulf tags
    flags = []
    for i in range(LB, n - LF - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0
        cir = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng
        cir_t = (H[i] - C[i]) / rng; uw = (H[i] - max(O[i], C[i])) / rng
        down = None
        if L[i] <= min(L[i - LB:i]) and cir >= CIR and lw >= WICK and C[i] > O[i] and ds >= DS: down = True
        elif H[i] >= max(H[i - LB:i]) and cir_t >= CIR and uw >= WICK and C[i] < O[i] and ds <= -DS: down = False
        if down is None: continue
        if down:
            body = (C[i - 1] < O[i - 1]) and O[i] <= C[i - 1] and C[i] >= O[i - 1]
            rangef = L[i] <= L[i - 1] and H[i] >= H[i - 1]
            reclaim = C[i] >= O[i - 1]
        else:
            body = (C[i - 1] > O[i - 1]) and O[i] >= C[i - 1] and C[i] <= O[i - 1]
            rangef = H[i] >= H[i - 1] and L[i] <= L[i - 1]
            reclaim = C[i] <= O[i - 1]
        strong = run(i, down) <= RUN_MAX and sellconc(A[i], H[i], L[i], down) >= SELLCONC
        flags.append({"i": i, "down": down, "hit": rev(i, down), "yr": YR[i],
                      "body": body, "range": rangef, "reclaim": reclaim, "strong": strong})

    def line(name, sel):
        # non-overlap greedy on the SUBSET (forward windows LF apart) -> the taken() basis
        taken = []; last = -10**9
        for f in sel:
            if f["i"] > last + LF: taken.append(f); last = f["i"]
        n_t = len(taken); h_t = sum(f["hit"] for f in taken)
        n_r = len(sel); h_r = sum(f["hit"] for f in sel)
        h25 = sum(f["hit"] for f in taken if f["yr"] == 2025); n25 = sum(1 for f in taken if f["yr"] == 2025)
        h26 = sum(f["hit"] for f in taken if f["yr"] == 2026); n26 = sum(1 for f in taken if f["yr"] == 2026)
        p = pval(h_t, n_t, base) if n_t else 1.0
        print("   %-26s raw %4d/%.0f%%  | taken n=%3d %5.1f%% (25:%.0f%% 26:%.0f%%)  p=%.4f" % (
            name, n_r, 100 * h_r / max(1, n_r), n_t, 100 * h_t / max(1, n_t),
            100 * h25 / max(1, n25), 100 * h26 / max(1, n26), p))

    print("\n=== %s ===  %d buckets | fresh-extreme base rate %.1f%% (R=%.1f%%/%d bars) | detector flags %d" % (
        tf, n, 100 * base, R * 100, LF, len(flags)))
    line("HAMMER (shipped core)", flags)
    line("+ engulf: body (classic)", [f for f in flags if f["body"]])
    line("+ engulf: range (whole)", [f for f in flags if f["range"]])
    line("+ engulf: reclaim (loose)", [f for f in flags if f["reclaim"]])
    line("+ STRONG (run+sellconc)", [f for f in flags if f["strong"]])
    line("+ body & STRONG", [f for f in flags if f["body"] and f["strong"]])
    line("+ body OR STRONG", [f for f in flags if f["body"] or f["strong"]])
    hh = sum(f["hit"] for f in flags); print("   [hammer overlap-inflated precision %.1f%%; engulf hit-rate of the %d hammers: body %.0f%% range %.0f%% reclaim %.0f%%]" % (
        100 * hh / max(1, len(flags)), len(flags),
        100 * sum(1 for f in flags if f["body"]) / max(1, len(flags)),
        100 * sum(1 for f in flags if f["range"]) / max(1, len(flags)),
        100 * sum(1 for f in flags if f["reclaim"]) / max(1, len(flags))))


for tf, R in (("15m", 0.004), ("1h", 0.006), ("4h", 0.010)):
    run_test(tf, R)
