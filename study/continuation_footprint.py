# -*- coding: utf-8 -*-
"""CONTINUATION study — the OTHER half of the bubbles concept (mirror of the reversal work).

Reversal = aggressors ABSORBED at the extreme (defenders show up) -> price turns. CONTINUATION = aggressors WIN,
a big single-level bubble (spike, AUC 0.47 in the reversal study = inverted) punches THROUGH the level -> price extends.

Candidate = a MOMENTUM / breakout candle: fresh LB-extreme that CLOSES AT the extreme (break direction), NOT a
rejection/hammer. up = fresh high + bullish + closes upper CIR of range; down = mirror.
Outcome (first-passage within LF bars): CONT = extends R% beyond the extreme BEFORE breaking back past the candle's
opposite end; FAIL = breaks back first. Report cont rate + cont-vs-fail, and which footprint reads predict CONT.

Footprint features (causal, from candle-3's own b.levels):
  spike   : biggest single price level's share of the candle's total volume (the lone dominant bubble).
  agg     : break-direction taker share (up: total buy / total; down: total sell / total) -- one-sided aggression.
  push    : break-side volume transacted in the extreme third / that side's total (buyers AT the high pushing through).
  defend  : opposite side's share in the extreme third (the absorber; should be LOW for a clean continuation).
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
    def pval(k, n, p): return 1.0

LB, CIR, WICK, DS = 6, 0.55, 0.25, 3.0


def foot(b, hi, lo, up):
    """Return (spike, agg, push, defend) oriented to the BREAK direction. up=breakout up."""
    lv = b.get("levels") or {}; rng = hi - lo
    if not lv or rng <= 0: return None
    thr = (hi - rng / 3.0) if up else (lo + rng / 3.0)          # extreme third = top (up) / bottom (down)
    ts = tb = tot = 0.0; brk_ext = opp_ext = 0.0; mx = 0.0
    for ps, vv in lv.items():
        try: p = float(ps)
        except (TypeError, ValueError): continue
        s = _f(vv.get("s")); bu = _f(vv.get("b")); t = s + bu
        ts += s; tb += bu; tot += t
        if t > mx: mx = t
        in_ext = (p >= thr) if up else (p <= thr)
        if in_ext:
            brk_ext += (bu if up else s); opp_ext += (s if up else bu)
    if tot <= 0: return None
    brk_tot = tb if up else ts; opp_tot = ts if up else tb
    agg = (brk_tot / tot * 100.0)
    push = (brk_ext / brk_tot * 100.0) if brk_tot > 0 else 0.0
    defend = (opp_ext / opp_tot * 100.0) if opp_tot > 0 else 0.0
    return mx / tot * 100.0, agg, push, defend


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

    def outcome(i, up):
        """+1 continuation (extend R% beyond extreme first), -1 fail (break back past candle's opposite end first), 0 neither."""
        if up:
            cont = H[i] * (1 + R); inval = L[i]
            for k in range(i + 1, min(n, i + 1 + LF)):
                if H[k] >= cont: return 1
                if L[k] <= inval: return -1
        else:
            cont = L[i] * (1 - R); inval = H[i]
            for k in range(i + 1, min(n, i + 1 + LF)):
                if L[k] <= cont: return 1
                if H[k] >= inval: return -1
        return 0

    cand = []
    for i in range(LB, n - LF - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        up = None
        if H[i] >= max(H[i - LB:i]) and C[i] > O[i] and (C[i] - L[i]) / rng >= CIR: up = True     # breakout up (closes at high)
        elif L[i] <= min(L[i - LB:i]) and C[i] < O[i] and (H[i] - C[i]) / rng >= CIR: up = False  # breakdown (closes at low)
        if up is None: continue
        ff = foot(A[i], H[i], L[i], up)
        if ff is None: continue
        oc = outcome(i, up)
        cand.append({"i": i, "up": up, "oc": oc, "cont": 1 if oc == 1 else 0, "yr": YR[i],
                     "spike": ff[0], "agg": ff[1], "push": ff[2], "defend": ff[3]})

    ncont = sum(c["cont"] for c in cand); nrev = sum(1 for c in cand if c["oc"] == -1)
    base = ncont / len(cand)
    print("\n=== %s ===  %d buckets | breakout candidates %d | CONT %.1f%% | cont-vs-fail %.1f%% (R=%.1f%%/%d)" % (
        tf, n, len(cand), 100 * base, 100 * ncont / max(1, ncont + nrev), R * 100, LF))
    for k in ("spike", "agg", "push", "defend"):
        cv = [c[k] for c in cand if c["cont"]]; nv = [c[k] for c in cand if not c["cont"]]
        a = auc_p(cv, nv)[0]
        a25 = auc_p([c[k] for c in cand if c["cont"] and c["yr"] == 2025], [c[k] for c in cand if not c["cont"] and c["yr"] == 2025])[0]
        a26 = auc_p([c[k] for c in cand if c["cont"] and c["yr"] == 2026], [c[k] for c in cand if not c["cont"] and c["yr"] == 2026])[0]
        print("   AUC %-7s %.3f (25:%.2f 26:%.2f)" % (k, a, a25, a26))

    def taken(sel):
        t = []; last = -10**9
        for c in sel:
            if c["i"] > last + LF: t.append(c); last = c["i"]
        return t
    def line(name, sel):
        t = taken(sel); nt = len(t); ht = sum(c["cont"] for c in t)
        h25 = sum(c["cont"] for c in t if c["yr"] == 2025); n25 = sum(1 for c in t if c["yr"] == 2025)
        h26 = sum(c["cont"] for c in t if c["yr"] == 2026); n26 = sum(1 for c in t if c["yr"] == 2026)
        print("   %-26s raw %4d/%2.0f%%  | taken n=%3d %5.1f%% (25:%2.0f%% 26:%2.0f%%)  p=%.4f" % (
            name, len(sel), 100 * sum(c["cont"] for c in sel) / max(1, len(sel)), nt,
            100 * ht / max(1, nt), 100 * h25 / max(1, n25), 100 * h26 / max(1, n26), pval(ht, nt, base)))
    print("   -- continuation precision on breakout candidates --")
    line("BREAKOUT (all momentum)", cand)
    line("+ spike>=25 (dominant bubble)", [c for c in cand if c["spike"] >= 25])
    line("+ agg>=60 (one-sided)", [c for c in cand if c["agg"] >= 60])
    line("+ push>=40 (push@extreme)", [c for c in cand if c["push"] >= 40])
    line("+ defend<=25 (no absorber)", [c for c in cand if c["defend"] <= 25])
    line("+ spike>=25 & agg>=60", [c for c in cand if c["spike"] >= 25 and c["agg"] >= 60])
    line("+ agg>=60 & defend<=25", [c for c in cand if c["agg"] >= 60 and c["defend"] <= 25])


for tf, R in (("15m", 0.004), ("1h", 0.006)):
    run_test(tf, R)
