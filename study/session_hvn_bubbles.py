# -*- coding: utf-8 -*-
"""SESSION HVN / BUBBLE-NODE study — the faithful "levels where the bubbles appeared" variant.

Instead of structural H/L/POC, the levels are the prior session's DIRECTIONAL aggression NODES:
  sell-node = price level where aggressive SELLING clustered (a red-bubble node)  -> hypothesised RESISTANCE
  buy-node  = price level where aggressive BUYING  clustered (a green-bubble node) -> hypothesised SUPPORT
Top-K per side, EPS-separated. London ctx = Tokyo[0,8); NY ctx = Tokyo + pre-NY London[8,13) (merged profiles).

Test: during the session, price REACHES a node (EPS 0.15%), first-passage REJECT vs BREAK (+/-0.4%, LF8).
THREE groups (the whole point — controls):
  ALIGNED   : sell-node as resistance, buy-node as support   (the hypothesis)
  ANTI      : sell-node as support,   buy-node as resistance (direction shuffled — isolates directional content)
  PLACEBO   : random in-range levels                         (geometry control)
If ALIGNED > ANTI and ALIGNED > PLACEBO -> prior aggression nodes carry real directional S/R. Else null.
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

EPS = 0.0015
R = 0.004
LF = 8
BThr = 15.0
K = 3           # top-K nodes per side
SEP = 0.002     # nodes must be >=0.2% apart


def profile(cands):
    """(buy_agg, sell_agg) price->vol across the footprint of these candles."""
    ba = defaultdict(float); sa = defaultdict(float)
    for b in cands:
        for ps, vv in (b.get("levels") or {}).items():
            try: p = float(ps)
            except (TypeError, ValueError): continue
            ba[p] += _f(vv.get("b")); sa[p] += _f(vv.get("s"))
    return ba, sa


def top_nodes(agg, k=K, sep=SEP):
    picked = []
    for p, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        if v <= 0: break
        if all(abs(p - q) > p * sep for q in picked):
            picked.append(p)
        if len(picked) >= k: break
    return picked


def merge(a, b):
    out = defaultdict(float)
    for d in (a, b):
        for p, v in d.items(): out[p] += v
    return out


def run_test(tf="15m"):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0
    dtl = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc) for b in A]
    day = [d.toordinal() for d in dtl]; hr = [d.hour for d in dtl]; wd = [d.weekday() for d in dtl]; yr = [d.year for d in dtl]
    byday = defaultdict(list)
    for i in range(n): byday[day[i]].append(i)

    def passage(i, lvl, reject_down):
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

    res = []

    def scan(idxs, res_lvls, sup_lvls, sess):
        """res_lvls tested as resistance (reject down), sup_lvls as support (reject up)."""
        seen = set()
        cand = [("R", p) for p in res_lvls] + [("S", p) for p in sup_lvls]
        for i in idxs:
            if i == 0: continue
            pc = C[i - 1]; best = None; bestd = 1e18
            for kind, lvl in cand:
                if lvl <= 0: continue
                eps = lvl * EPS
                if kind == "R" and lvl > pc and H[i] >= lvl - eps:
                    d = abs(H[i] - lvl)
                    if d < bestd and ("R", round(lvl, 2)) not in seen: best = ("R", lvl, True); bestd = d
                elif kind == "S" and lvl < pc and L[i] <= lvl + eps:
                    d = abs(L[i] - lvl)
                    if d < bestd and ("S", round(lvl, 2)) not in seen: best = ("S", lvl, False); bestd = d
            if best is None: continue
            kind, lvl, reject_down = best; seen.add((kind, round(lvl, 2)))
            oc = passage(i, lvl, reject_down)
            confirm = (DP[i] <= -BThr) if reject_down else (DP[i] >= BThr)
            oppose = (DP[i] >= BThr) if reject_down else (DP[i] <= -BThr)
            res.append({"sess": sess, "reject": (1 if oc == 1 else (-1 if oc == -1 else 0)),
                        "confirm": confirm, "oppose": oppose, "yr": yr[i]})

    for d, idxs in byday.items():
        if not idxs or wd[idxs[0]] >= 5: continue
        tok = [i for i in idxs if 0 <= hr[i] < 8]
        lon = [i for i in idxs if 8 <= hr[i] < 16]
        lon_pre = [i for i in idxs if 8 <= hr[i] < 13]
        ny = [i for i in idxs if 13 <= hr[i] < 21]
        if not tok: continue
        tb, ts = profile([A[i] for i in tok])
        t_sell = top_nodes(ts); t_buy = top_nodes(tb)              # Tokyo nodes for London
        lo_r = min(list(tb) + list(ts)); hi_r = max(list(tb) + list(ts))
        rnd = [random.uniform(lo_r, hi_r) for _ in range(2 * K)]
        scan(lon, t_sell, t_buy, "London_ALIGN")                    # sell=res, buy=sup
        scan(lon, t_buy, t_sell, "London_ANTI")                    # shuffled
        scan(lon, rnd[:K], rnd[K:], "London_PLAC")                 # random
        if lon_pre:
            lb, ls = profile([A[i] for i in lon_pre])
            ab = merge(tb, lb); asl = merge(ts, ls)                # Tokyo + pre-NY London merged
            n_sell = top_nodes(asl); n_buy = top_nodes(ab)
            lo_r = min(list(ab) + list(asl)); hi_r = max(list(ab) + list(asl))
            rnd = [random.uniform(lo_r, hi_r) for _ in range(2 * K)]
            scan(ny, n_sell, n_buy, "NY_ALIGN")
            scan(ny, n_buy, n_sell, "NY_ANTI")
            scan(ny, rnd[:K], rnd[K:], "NY_PLAC")

    def line(tag, sel):
        base = [r for r in sel if r["reject"] != 0]
        if not base: print("   %-16s n=0" % tag); return
        nn = len(base); rj = sum(1 for r in base if r["reject"] == 1)
        conf = [r for r in base if r["confirm"]]; cn = len(conf); cr = sum(1 for r in conf if r["reject"] == 1)
        r25 = [r for r in base if r["yr"] == 2025]; r26 = [r for r in base if r["yr"] == 2026]
        j25 = (sum(1 for r in r25 if r["reject"] == 1) / len(r25) * 100) if r25 else 0
        j26 = (sum(1 for r in r26 if r["reject"] == 1) / len(r26) * 100) if r26 else 0
        print("   %-16s n=%4d  REJ %4.1f%%  (25:%4.1f/26:%4.1f)  | +bubble n=%3d REJ %4.1f%%" % (
            tag, nn, 100 * rj / nn, j25, j26, cn, 100 * cr / max(1, cn)))

    print("\n=== %s === prior-session AGGRESSION-NODE tests | K=%d/side EPS %.2f%% R %.1f%% LF %d" % (tf, K, EPS * 100, R * 100, LF))
    for s in ("London", "NY"):
        print(" -- %s --" % s)
        line("ALIGNED", [r for r in res if r["sess"] == s + "_ALIGN"])
        line("ANTI (shuffled)", [r for r in res if r["sess"] == s + "_ANTI"])
        line("PLACEBO (random)", [r for r in res if r["sess"] == s + "_PLAC"])


run_test("15m")
