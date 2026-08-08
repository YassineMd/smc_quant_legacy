# -*- coding: utf-8 -*-
"""AGGRESSION-vs-ABSORPTION footprint study (the "bubbles" concept).

User's read of the heatmap trade-bubbles: aggressive volume concentrates AT a price level; sometimes the aggressors
SUCCEED (drive price), sometimes they FAIL because a passive counterparty absorbs them -> reversal. Test whether the
CANDLE FOOTPRINT (per-level aggressive buy/sell = b.levels) captures that and adds to the hammer / engulf detector.

Per reversal candidate (fresh LB-extreme) compute, from candle-3's OWN footprint (causal):
  ext3_lose : the TRAPPED aggressors' share dumped into the extreme third  (bottom: sell in bottom3 / total sell;
              top: buy in top3 / total buy)  -- the losing side flushing INTO the low/high.
  ext3_win  : the DEFENDERS' aggressive share at the extreme third          (bottom: buy in bottom3 / total buy;
              top: sell in top3 / total sell) -- the counterparty stepping in AT the extreme (the visible absorber).
  battle    : total (both-side) volume share transacted in the extreme third -- how much fighting happened AT the level.
  spike     : the single hottest price level's share of the candle's total volume -- the biggest "bubble".
  absorb    : effort/result = ext3_lose gated on a hammer close (dumped hard AND rejected = passive absorption).

Outcome (score only) = holds + reverses R within LF bars. AUC among fresh candidates + incremental precision on the
shipped hammer flags (taken/non-overlap basis, exact-binomial p vs base, split by year, both sides) vs the engulf tier.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
try:
    from scipy.stats import binomtest
    def pval(k, n, p): return binomtest(k, n, p, alternative="greater").pvalue if n else 1.0
except Exception:
    from math import comb
    def pval(k, n, p): return sum(comb(n, j) * p**j * (1 - p)**(n - j) for j in range(k, n + 1)) if n else 1.0

LB, CIR, WICK, DS = 6, 0.55, 0.25, 3.0


def foot(b, hi, lo, down):
    """Return (ext3_lose, ext3_win, battle, spike) from the footprint, oriented to the reversal side. down=bottom."""
    lv = b.get("levels") or {}; rng = hi - lo
    if not lv or rng <= 0:
        return None
    thr = (lo + rng / 3.0) if down else (hi - rng / 3.0)
    ts = tb = tot = 0.0; s_ext = b_ext = all_ext = 0.0; mx = 0.0
    for ps, vv in lv.items():
        try: p = float(ps)
        except (TypeError, ValueError): continue
        s = _f(vv.get("s")); bu = _f(vv.get("b")); t = s + bu
        ts += s; tb += bu; tot += t
        if t > mx: mx = t
        in_ext = (p <= thr) if down else (p >= thr)
        if in_ext:
            s_ext += s; b_ext += bu; all_ext += t
    if tot <= 0:
        return None
    if down:                        # reversal UP: losers=sellers, winners=buyers
        lose = (s_ext / ts * 100.0) if ts > 0 else 0.0
        win = (b_ext / tb * 100.0) if tb > 0 else 0.0
    else:                           # reversal DOWN: losers=buyers, winners=sellers
        lose = (b_ext / tb * 100.0) if tb > 0 else 0.0
        win = (s_ext / ts * 100.0) if ts > 0 else 0.0
    return lose, win, all_ext / tot * 100.0, mx / tot * 100.0


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
    def eng(i, down):
        if down: return C[i - 1] < O[i - 1] and O[i] <= C[i - 1] and C[i] >= O[i - 1]
        return C[i - 1] > O[i - 1] and O[i] >= C[i - 1] and C[i] <= O[i - 1]
    def rev(i, down):
        if down: return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
        return max(H[i + 1:i + 1 + LF]) <= H[i] and (H[i] - min(L[i + 1:i + 1 + LF])) / H[i] >= R

    # ---- fresh-extreme candidates + footprint features (predictive AUC) ----
    fresh = []
    for i in range(LB, n - LF - 1):
        if H[i] - L[i] <= 0 or O[i] <= 0: continue
        for down in ((True,) if L[i] <= min(L[i - LB:i]) else ()) + ((False,) if H[i] >= max(H[i - LB:i]) else ()):
            ff = foot(A[i], H[i], L[i], down)
            if ff is None: continue
            fresh.append({"i": i, "down": down, "hit": rev(i, down), "yr": YR[i],
                          "lose": ff[0], "win": ff[1], "battle": ff[2], "spike": ff[3]})
    base = sum(f["hit"] for f in fresh) / len(fresh)
    print("\n=== %s ===  %d buckets | fresh-extreme (w/ footprint) %d | base %.1f%% (R=%.1f%%/%d)" % (
        tf, n, len(fresh), 100 * base, R * 100, LF))
    for k in ("lose", "win", "battle", "spike"):
        rv = [f[k] for f in fresh if f["hit"]]; cv = [f[k] for f in fresh if not f["hit"]]
        a = auc_p(rv, cv)[0]
        a25 = auc_p([f[k] for f in fresh if f["hit"] and f["yr"] == 2025], [f[k] for f in fresh if not f["hit"] and f["yr"] == 2025])[0]
        a26 = auc_p([f[k] for f in fresh if f["hit"] and f["yr"] == 2026], [f[k] for f in fresh if not f["hit"] and f["yr"] == 2026])[0]
        print("   AUC %-7s %.3f (25:%.2f 26:%.2f)" % (k, a, a25, a26))

    # ---- incremental precision on shipped HAMMER flags ----
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
        ff = foot(A[i], H[i], L[i], down)
        flags.append({"i": i, "down": down, "hit": rev(i, down), "yr": YR[i], "eng": eng(i, down),
                      "lose": ff[0] if ff else 0.0, "win": ff[1] if ff else 0.0,
                      "battle": ff[2] if ff else 0.0, "spike": ff[3] if ff else 0.0, "has": ff is not None})

    def line(name, sel):
        taken = []; last = -10**9
        for f in sel:
            if f["i"] > last + LF: taken.append(f); last = f["i"]
        nt = len(taken); ht = sum(f["hit"] for f in taken)
        h25 = sum(f["hit"] for f in taken if f["yr"] == 2025); n25 = sum(1 for f in taken if f["yr"] == 2025)
        h26 = sum(f["hit"] for f in taken if f["yr"] == 2026); n26 = sum(1 for f in taken if f["yr"] == 2026)
        print("   %-28s raw %4d/%2.0f%%  | taken n=%3d %5.1f%% (25:%2.0f%% 26:%2.0f%%)  p=%.4f" % (
            name, len(sel), 100 * sum(f["hit"] for f in sel) / max(1, len(sel)), nt,
            100 * ht / max(1, nt), 100 * h25 / max(1, n25), 100 * h26 / max(1, n26), pval(ht, nt, base)))
    fh = [f for f in flags if f["has"]]
    print("   -- incremental on HAMMER flags (%d w/ footprint) --" % len(fh))
    line("HAMMER", flags)
    line("+ ext3_lose>=45 (dump@ext)", [f for f in fh if f["lose"] >= 45])
    line("+ ext3_win>=45 (defend@ext)", [f for f in fh if f["win"] >= 45])
    line("+ battle>=45 (fight@ext)", [f for f in fh if f["battle"] >= 45])
    line("+ spike>=20 (big bubble)", [f for f in fh if f["spike"] >= 20])
    line("+ absorb: lose>=45 & win>=40", [f for f in fh if f["lose"] >= 45 and f["win"] >= 40])
    line("+ engulf (current strong)", [f for f in flags if f["eng"]])
    line("+ engulf & win>=45", [f for f in fh if f["eng"] and f["win"] >= 45])
    line("+ engulf & battle>=45", [f for f in fh if f["eng"] and f["battle"] >= 45])


for tf, R in (("15m", 0.004), ("1h", 0.006)):
    run_test(tf, R)
