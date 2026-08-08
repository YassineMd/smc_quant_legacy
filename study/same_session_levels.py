# -*- coding: utf-8 -*-
"""SAME-SESSION intraday levels: do levels formed EARLIER in the SAME session act as S/R when revisited?

Causal: at test candle i (session S, after M-candle warm-up), the levels come ONLY from the session's candles
BEFORE i. Two level families + controls:
  STRUCT  : running session HIGH (resistance) / LOW (support) / POC (by side)
  NODE_AL : prior-in-session aggression NODES aligned  (sell-cluster=resistance, buy-cluster=support)
  NODE_AN : same nodes DIRECTION-SHUFFLED             (sell=support, buy=resistance)   <- isolates directional content
  PLAC    : random in the session-so-far range                                          <- geometry control
Test = price reaches a level (EPS 0.15%), first-passage REJECT vs BREAK (+/-0.4%, LF8). Sessions Tokyo/London/NY.
"""
import os, sys, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
random.seed(42)
from datetime import datetime, timezone
from collections import defaultdict
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

EPS, R, LF, BThr, K, SEP, M = 0.0015, 0.004, 8, 15.0, 3, 0.002, 8


def top_nodes(agg):
    picked = []
    for p, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        if v <= 0: break
        if all(abs(p - q) > p * SEP for q in picked): picked.append(p)
        if len(picked) >= K: break
    return picked


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

    res = []

    def best(i, pc, cand, group, seen):
        b = None; bd = 1e18
        for kind, lvl in cand:
            if lvl <= 0: continue
            eps = lvl * EPS
            asR = kind == "R" or (kind == "A" and lvl > pc)
            asS = kind == "S" or (kind == "A" and lvl < pc)
            if asR and lvl > pc and H[i] >= lvl - eps:
                d = abs(H[i] - lvl)
                if d < bd and (group, round(lvl, 2)) not in seen: b = (lvl, True); bd = d
            elif asS and lvl < pc and L[i] <= lvl + eps:
                d = abs(L[i] - lvl)
                if d < bd and (group, round(lvl, 2)) not in seen: b = (lvl, False); bd = d
        return b

    def rec(i, group, sess, hit, seen):
        lvl, rd = hit; seen.add((group, round(lvl, 2)))
        oc = passage(i, lvl, rd)
        conf = (DP[i] <= -BThr) if rd else (DP[i] >= BThr)
        res.append({"sess": sess, "group": group, "reject": (1 if oc == 1 else (-1 if oc == -1 else 0)),
                    "confirm": conf, "yr": yr[i]})

    for d, idxs in byday.items():
        if not idxs or wd[idxs[0]] >= 5: continue
        for sess, h0, h1 in (("Tokyo", 0, 8), ("London", 8, 16), ("NY", 13, 21)):
            sidx = [i for i in idxs if h0 <= hr[i] < h1]
            if len(sidx) < M + 4: continue
            ba = defaultdict(float); sa = defaultdict(float); rhi = -1e18; rlo = 1e18
            seen = set()
            for c, i in enumerate(sidx):
                if c >= M and rhi > -1e17 and i > 0:
                    pc = C[i - 1]
                    tot = defaultdict(float)
                    for p, v in ba.items(): tot[p] += v
                    for p, v in sa.items(): tot[p] += v
                    poc = max(tot.items(), key=lambda kv: kv[1])[0] if tot else (rhi + rlo) / 2
                    sn = top_nodes(sa); bn = top_nodes(ba)
                    rnd = [random.uniform(rlo, rhi) for _ in range(2 * K)]
                    for group, cand in (("STRUCT", [("R", rhi), ("S", rlo), ("A", poc)]),
                                        ("NODE_AL", [("R", p) for p in sn] + [("S", p) for p in bn]),
                                        ("NODE_AN", [("S", p) for p in sn] + [("R", p) for p in bn]),
                                        ("PLAC", [("A", p) for p in rnd])):
                        hit = best(i, pc, cand, group, seen)
                        if hit: rec(i, group, sess, hit, seen)
                for ps, vv in (A[i].get("levels") or {}).items():
                    try: p = float(ps)
                    except (TypeError, ValueError): continue
                    ba[p] += _f(vv.get("b")); sa[p] += _f(vv.get("s"))
                rhi = max(rhi, H[i]); rlo = min(rlo, L[i])

    def line(tag, sel):
        base = [r for r in sel if r["reject"] != 0]
        if not base: print("     %-14s n=0" % tag); return
        nn = len(base); rj = sum(1 for r in base if r["reject"] == 1)
        conf = [r for r in base if r["confirm"]]; cn = len(conf); cr = sum(1 for r in conf if r["reject"] == 1)
        j25 = [r for r in base if r["yr"] == 2025]; j26 = [r for r in base if r["yr"] == 2026]
        r25 = (sum(1 for r in j25 if r["reject"] == 1) / len(j25) * 100) if j25 else 0
        r26 = (sum(1 for r in j26 if r["reject"] == 1) / len(j26) * 100) if j26 else 0
        print("     %-14s n=%4d  REJ %4.1f%%  (25:%4.1f/26:%4.1f) | +bubble n=%3d REJ %4.1f%%" % (
            tag, nn, 100 * rj / nn, r25, r26, cn, 100 * cr / max(1, cn)))

    print("\n=== %s === SAME-SESSION intraday levels | warmup %d, K=%d, EPS %.2f%% R %.1f%% LF %d" % (tf, M, K, EPS * 100, R * 100, LF))
    for sess in ("Tokyo", "London", "NY"):
        print(" -- %s --" % sess)
        for g in ("STRUCT", "NODE_AL", "NODE_AN", "PLAC"):
            line(g, [r for r in res if r["sess"] == sess and r["group"] == g])


run_test("15m")
