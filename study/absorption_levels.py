# -*- coding: utf-8 -*-
"""ABSORPTION-DISCOVERED S/R levels (the user's actual concept).

A level is NOT pre-defined (session H/L/POC) — it is DISCOVERED where absorption repeats: a candle with strong
one-sided aggression but NO price progress (aggressor absorbed). buy-absorbed (net-buy delta, tiny body) -> the HIGH
becomes RESISTANCE; sell-absorbed -> the LOW becomes SUPPORT. Nearby same-side events CLUSTER (count++) = a stronger level.

Causal: a level is TESTED only on revisits >=MIN_AGE bars after it formed, within its L_LIFE lifetime. First-passage
REJECT (turn 0.4% off the level) vs BREAK (push 0.4% through), LF bars. Controls:
  ALIGNED : buy-absorbed=resistance, sell-absorbed=support  (the hypothesis)
  ANTI    : direction shuffled                              (isolates directional content)
  PLACEBO : random levels in the trailing range             (isolates level location)
Reported for ALL aligned levels + the CLUSTERED (count>=2) subset (the user's "repeated absorption") + both years.
"""
import os, sys, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
random.seed(42)
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
try:
    from scipy.stats import binomtest
    def pv(k, n, p): return binomtest(k, n, p, alternative="two-sided").pvalue if n else 1.0
except Exception:
    def pv(k, n, p): return 1.0

T, BODY, EPS, R, LF, L_LIFE, MIN_AGE = 20.0, 0.35, 0.0015, 0.004, 8, 96, 3


def run_test(tf, R=R):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

    def passage(i, lvl, rd):
        rj = lvl * (1 - R) if rd else lvl * (1 + R); bk = lvl * (1 + R) if rd else lvl * (1 - R)
        for k in range(i + 1, min(n, i + 1 + LF)):
            if rd:
                if L[k] <= rj: return 1
                if H[k] >= bk: return -1
            else:
                if H[k] >= rj: return 1
                if L[k] <= bk: return -1
        return 0

    res = {g: [] for g in ("AL", "AL2", "AN", "PL")}   # AL2 = aligned clustered(count>=2)
    active = []                                          # {price, side'R'/'S', count, born, last_test}

    for i in range(1, n - LF - 1):
        pc = C[i - 1]
        active = [lv for lv in active if i - lv["born"] <= L_LIFE]
        # trailing range for placebo
        w0 = max(0, i - L_LIFE); rlo = min(L[w0:i]); rhi = max(H[w0:i])
        # --- test revisits (aligned + anti) ---
        for ctrl in ("AL", "AN"):
            best = None; bd = 1e18
            for lv in active:
                if i - lv["born"] < MIN_AGE or i - lv["last_test"] < LF:
                    continue
                side = lv["side"] if ctrl == "AL" else ("S" if lv["side"] == "R" else "R")
                p = lv["price"]; eps = p * EPS
                if side == "R" and p > pc and H[i] >= p - eps:
                    d = abs(H[i] - p)
                    if d < bd: best = (lv, p, True); bd = d
                elif side == "S" and p < pc and L[i] <= p + eps:
                    d = abs(L[i] - p)
                    if d < bd: best = (lv, p, False); bd = d
            if best:
                lv, p, rd = best
                if ctrl == "AL": lv["last_test"] = i
                oc = passage(i, p, rd)
                if oc != 0:
                    res[ctrl].append((1 if oc == 1 else 0, YR[i]))
                    if ctrl == "AL" and lv["count"] >= 2:
                        res["AL2"].append((1 if oc == 1 else 0, YR[i]))
        # --- placebo: 2 random in-range levels ---
        for p in (random.uniform(rlo, rhi), random.uniform(rlo, rhi)):
            eps = p * EPS
            if p > pc and H[i] >= p - eps:
                oc = passage(i, p, True)
            elif p < pc and L[i] <= p + eps:
                oc = passage(i, p, False)
            else:
                oc = None
            if oc is not None and oc != 0:
                res["PL"].append((1 if oc == 1 else 0, YR[i]))
        # --- add absorption level from candle i ---
        rng = H[i] - L[i]
        if rng > 0 and abs(DP[i]) >= T and abs(C[i] - O[i]) / rng <= BODY:
            if DP[i] > 0:
                price, side = H[i], "R"                  # buyers absorbed at the high -> resistance
            else:
                price, side = L[i], "S"                  # sellers absorbed at the low -> support
            merged = False
            for lv in active:
                if lv["side"] == side and abs(lv["price"] - price) <= price * EPS * 2:
                    lv["count"] += 1; lv["price"] = (lv["price"] + price) / 2.0; merged = True; break
            if not merged:
                active.append({"price": price, "side": side, "count": 1, "born": i, "last_test": -999})

    def line(tag, sel):
        if not sel: print("   %-22s n=0" % tag); return
        nn = len(sel); rj = sum(1 for r, _ in sel if r)
        j25 = [r for r, y in sel if y == 2025]; j26 = [r for r, y in sel if y == 2026]
        r25 = (sum(j25) / len(j25) * 100) if j25 else 0; r26 = (sum(j26) / len(j26) * 100) if j26 else 0
        print("   %-22s n=%5d  REJ %4.1f%%  (25:%4.1f/26:%4.1f)" % (tag, nn, 100 * rj / nn, r25, r26))

    print("\n=== %s === absorption-discovered S/R | T=%.0f body<=%.2f EPS %.2f%% R %.1f%% LF %d life %d" % (
        tf, T, BODY, EPS * 100, R * 100, LF, L_LIFE))
    line("ALIGNED (all)", res["AL"])
    line("ALIGNED clustered>=2", res["AL2"])
    line("ANTI (shuffled)", res["AN"])
    line("PLACEBO (random)", res["PL"])
    a = res["AL"]; p = res["PL"]
    if a and p:
        ra = sum(r for r, _ in a); rp = sum(r for r, _ in p)
        print("   aligned-vs-placebo p=%.4f" % pv(ra, len(a), rp / len(p)))


for tf in ("15m",):
    run_test(tf, R=0.004)
