# -*- coding: utf-8 -*-
"""SESSION-LEVEL bubbles (V1): do PRIOR-SESSION price levels act as S/R during London/NY, and does an aggressive
order-flow BUBBLE at the level confirm the rejection? Per-day, causal prior context.

Framing (user): study the LEVELS where bubbles appear relative to WHAT HAPPENED BEFORE.
  London  context = TOKYO      [00,08) {high, low, POC}
  NY      context = TOKYO + pre-NY LONDON [08,13) {high, low, POC}   (both fully precede NY [13,21))

Test: during the session, when a candle REACHES a prior level (within EPS), first-passage REJECT (turn 0.4% away from
the level) vs BREAK (push 0.4% through). Bubble = net-delta at the test candle confirming the rejection side
(sellers @ resistance / buyers @ support). Compare reject-rate: bare touch vs bubble-confirmed vs opposed.
Sessions UTC: Tokyo[0,8) London[8,16) NY[13,21). Weekdays only. Dedup: first test of each (day, level).
"""
import os, sys, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
random.seed(42)
from datetime import datetime, timezone
from collections import defaultdict
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
try:
    from scipy.stats import binomtest
    def pval(k, n, p): return binomtest(k, n, p, alternative="two-sided").pvalue if n else 1.0
except Exception:
    def pval(k, n, p): return 1.0

EPS = 0.0015      # level proximity (0.15%)
R = 0.004         # first-passage reject/break distance from the level (0.4% on 15m)
LF = 8            # forward bars for the first-passage
BThr = 15.0       # net-delta% magnitude for a confirming "bubble" (>=57.5/42.5 split)


def poc(cands):
    """Volume-by-price POC from the footprint across candles; fallback = mid of hi/lo."""
    agg = defaultdict(float); hi = -1e18; lo = 1e18
    for b in cands:
        H = _f(b.get("high")); L = _f(b.get("low")); hi = max(hi, H); lo = min(lo, L)
        for ps, vv in (b.get("levels") or {}).items():
            try: p = float(ps)
            except (TypeError, ValueError): continue
            agg[p] += _f(vv.get("b")) + _f(vv.get("s"))
    if agg: pc = max(agg.items(), key=lambda kv: kv[1])[0]
    else: pc = (hi + lo) / 2.0 if hi > -1e17 else 0.0
    return hi, lo, pc


def run_test(tf="15m"):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0
    dt = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc) for b in A]
    day = [d.toordinal() for d in dt]; hr = [d.hour for d in dt]; wd = [d.weekday() for d in dt]; yr = [d.year for d in dt]

    # index candles by day
    byday = defaultdict(list)
    for i in range(n):
        byday[day[i]].append(i)

    def passage(i, lvl, reject_down):
        """first-passage from i+1..i+LF: +1 REJECT (reach lvl*(1-/+R) on reject side), -1 BREAK (through), 0 neither."""
        rj = lvl * (1 - R) if reject_down else lvl * (1 + R)
        bk = lvl * (1 + R) if reject_down else lvl * (1 - R)
        for k in range(i + 1, min(n, i + 1 + LF)):
            if reject_down:
                if L[k] <= rj: return 1
                if H[k] >= bk: return -1
            else:
                if H[k] >= rj: return 1
                if L[k] <= bk: return -1
        return 0

    res = []   # {sess, ltype, reject, confirm, oppose, yr}
    for d, idxs in byday.items():
        if not idxs or wd[idxs[0]] >= 5: continue                    # weekdays only
        tok = [i for i in idxs if 0 <= hr[i] < 8]
        lon = [i for i in idxs if 8 <= hr[i] < 16]
        lon_pre = [i for i in idxs if 8 <= hr[i] < 13]
        ny = [i for i in idxs if 13 <= hr[i] < 21]
        if not tok: continue
        tkh, tkl, tkp = poc([A[i] for i in tok])
        _scan(lon, [("TK_high", tkh), ("TK_low", tkl), ("TK_poc", tkp)], "London", C, H, L, DP, passage, res, yr)
        lo_r, hi_r = min(tkl, tkh), max(tkl, tkh)                     # PLACEBO: random in-range levels (geometry control)
        _scan(lon, [("P", random.uniform(lo_r, hi_r)) for _ in range(3)], "London_P", C, H, L, DP, passage, res, yr)
        if lon_pre:
            lnh, lnl, lnp = poc([A[i] for i in lon_pre])
            _scan(ny, [("TK_high", tkh), ("TK_low", tkl), ("TK_poc", tkp),
                       ("LN_high", lnh), ("LN_low", lnl), ("LN_poc", lnp)], "NY", C, H, L, DP, passage, res, yr)
            lo_r, hi_r = min(tkl, lnl), max(tkh, lnh)
            _scan(ny, [("P", random.uniform(lo_r, hi_r)) for _ in range(6)], "NY_P", C, H, L, DP, passage, res, yr)

    def summary(tag, sel):
        sel = [r for r in sel if r["reject"] != 0]                    # resolved only (ignore 'neither')
        if not sel: print("   %-28s n=0" % tag); return
        nn = len(sel); rj = sum(1 for r in sel if r["reject"] == 1)
        print("   %-28s n=%4d  REJECT %4.1f%%" % (tag, nn, 100 * rj / nn))
    def split(tag, sel):
        base = [r for r in sel if r["reject"] != 0]
        if not base: print("   %-14s n=0" % tag); return
        alln = len(base); allr = sum(1 for r in base if r["reject"] == 1)
        conf = [r for r in base if r["confirm"]]; opp = [r for r in base if r["oppose"]]
        cn = len(conf); cr = sum(1 for r in conf if r["reject"] == 1)
        on = len(opp); orj = sum(1 for r in opp if r["reject"] == 1)
        print("   %-8s touch n=%4d REJ %4.1f%% | +bubble n=%4d REJ %4.1f%% | opp-bubble n=%4d REJ %4.1f%%  (p_conf-vs-opp=%.4f)" % (
            tag, alln, 100 * allr / alln, cn, 100 * cr / max(1, cn), on, 100 * orj / max(1, on),
            pval(cr, cn, (orj / on) if on else 0.5)))

    print("\n=== %s === prior-session-level tests | EPS %.2f%% R %.1f%% LF %d | bubble |dP|>=%.0f" % (tf, EPS * 100, R * 100, LF, BThr))
    for sess in ("London", "NY"):
        ss = [r for r in res if r["sess"] == sess]
        pl = [r for r in res if r["sess"] == sess + "_P"]
        print(" -- %s (n=%d resolved touches) --" % (sess, sum(1 for r in ss if r["reject"] != 0)))
        split("ALL", ss)
        split("PLACEBO(rand lvl)", pl)                                # geometry control: random in-range levels
        for lt in ("TK_high", "TK_low", "TK_poc", "LN_high", "LN_low", "LN_poc"):
            sub = [r for r in ss if r["ltype"] == lt]
            if sub: split(lt, sub)


def _scan(idxs, levels, sess, C, H, L, DP, passage, res, yr):
    seen = set()
    for i in idxs:
        if i == 0: continue
        pc = C[i - 1]
        best = None; bestd = 1e18
        for lt, lvl in levels:
            if lvl <= 0: continue
            eps = lvl * EPS
            if lvl > pc and H[i] >= lvl - eps:                       # resistance from below
                d = abs(H[i] - lvl)
                if d < bestd and (lt, round(lvl, 2)) not in seen: best = (lt, lvl, True); bestd = d
            elif lvl < pc and L[i] <= lvl + eps:                     # support from above
                d = abs(L[i] - lvl)
                if d < bestd and (lt, round(lvl, 2)) not in seen: best = (lt, lvl, False); bestd = d
        if best is None: continue
        lt, lvl, reject_down = best
        seen.add((lt, round(lvl, 2)))
        oc = passage(i, lvl, reject_down)
        confirm = (DP[i] <= -BThr) if reject_down else (DP[i] >= BThr)   # sellers @ resistance / buyers @ support
        oppose = (DP[i] >= BThr) if reject_down else (DP[i] <= -BThr)
        res.append({"sess": sess, "ltype": lt, "reject": (1 if oc == 1 else (-1 if oc == -1 else 0)),
                    "confirm": confirm, "oppose": oppose, "yr": yr[i]})


run_test("15m")
