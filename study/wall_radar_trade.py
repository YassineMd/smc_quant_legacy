# -*- coding: utf-8 -*-
"""TRADEABILITY of the radar box-volume -> break/hold signal. Does it clear the fee wall?

At each radar entry: entry-bar WALL-LEVEL volume, regime-normalized (/ rolling-median curr_vol) -> terciles.
  HI vol -> bet BREAK (trade the wall's break direction) ;  LO vol -> bet HOLD (trade the rejection).
Direction by side: R break=up / R hold=down ; S break=down / S hold=up. Entry = close of the entry bar.
Two exits: (A) to the RADAR edges (natural target/stop) ; (B) symmetric +/-D. GROSS + NET (fee 0.08%/side), both years.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

HZ, FEE, RT = 24, 0.0008, 0.0016


def run(tf="15m"):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    n = len(A)
    C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    CV = np.array([_f(b.get("curr_vol")) for b in A])
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    print("loaded %s: %d — detecting walls..." % (tf, n))
    walls = AL.detect(A)

    E = []
    for w in walls:
        if w["strength"] < 0.12:
            continue
        P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
        for (k0, k1) in w["radar_runs"]:
            bv = 0.0
            for ps, vv in (A[k0].get("levels") or {}).items():
                try:
                    p = float(ps)
                except (TypeError, ValueError):
                    continue
                if r_lo <= p <= r_hi:
                    bv += _f(vv.get("b")) + _f(vv.get("s"))
            if bv <= 0 or k0 + HZ >= n:
                continue
            rm = float(np.median(CV[max(0, k0 - 200):k0])) if k0 > 5 else CV[k0]
            E.append({"k0": k0, "side": side, "r_lo": r_lo, "r_hi": r_hi, "vr": bv / rm if rm > 0 else 0.0, "yr": YR[k0]})

    vrs = sorted(e["vr"] for e in E); t1 = vrs[len(vrs) // 3]; t2 = vrs[2 * len(vrs) // 3]
    for e in E:
        e["tier"] = "HI" if e["vr"] >= t2 else ("LO" if e["vr"] < t1 else "MID")

    def dir_of(side, break_bet):
        if side == "R":
            return 1 if break_bet else -1        # R break=up / R hold=down
        return -1 if break_bet else 1            # S break=down / S hold=up

    def edge_trade(e, break_bet):
        k0 = e["k0"]; P0 = C[k0]; side = e["side"]; d = dir_of(side, break_bet)
        # target = the edge in the bet direction; stop = the opposite edge
        tgt = e["r_hi"] if d > 0 else e["r_lo"]; stp = e["r_lo"] if d > 0 else e["r_hi"]
        for k in range(k0 + 1, min(n, k0 + 1 + HZ)):
            if d > 0:
                if H[k] >= tgt: return (tgt - P0) / P0
                if L[k] <= stp: return (stp - P0) / P0
            else:
                if L[k] <= tgt: return (P0 - tgt) / P0
                if H[k] >= stp: return (P0 - stp) / P0
        return d * (C[min(n - 1, k0 + HZ)] - P0) / P0

    def sym_trade(e, break_bet, D):
        k0 = e["k0"]; P0 = C[k0]; d = dir_of(e["side"], break_bet)
        tgt = P0 * (1 + d * D); stp = P0 * (1 - d * D)
        for k in range(k0 + 1, min(n, k0 + 1 + HZ)):
            if d > 0:
                if H[k] >= tgt: return D
                if L[k] <= stp: return -D
            else:
                if L[k] <= tgt: return D
                if H[k] >= stp: return -D
        return d * (C[min(n - 1, k0 + HZ)] - P0) / P0

    def report(name, sel, fn):
        if len(sel) < 20:
            print("   %-34s n=%d (few)" % (name, len(sel))); return
        g = np.array([fn(e) for e in sel]); net = g - RT
        y25 = [i for i, e in enumerate(sel) if e["yr"] == 2025]; y26 = [i for i, e in enumerate(sel) if e["yr"] == 2026]
        print("   %-34s n=%4d win %4.1f%% GROSS/tr %+.4f%% NET/tr %+.4f%% (g25:%+.3f/g26:%+.3f)" % (
            name, len(sel), 100 * np.mean(g > 0), 100 * g.mean(), 100 * net.mean(),
            100 * (g[y25].mean() if y25 else 0), 100 * (g[y26].mean() if y26 else 0)))

    hi = [e for e in E if e["tier"] == "HI"]; lo = [e for e in E if e["tier"] == "LO"]
    print("\n=== %s === radar entries %d | HI-vol %d / LO-vol %d (terciles)" % (tf, len(E), len(hi), len(lo)))
    print("   [A] RADAR-EDGE exits (target = edge in bet dir, stop = opposite edge)")
    report("HI-vol -> BREAK bet (edge)", hi, lambda e: edge_trade(e, True))
    report("LO-vol -> HOLD bet (edge)", lo, lambda e: edge_trade(e, False))
    report("ALL entries -> HOLD bet (edge)", E, lambda e: edge_trade(e, False))     # base: walls mostly hold
    for D in (0.004, 0.006):
        print("   [B] SYMMETRIC +/-%.1f%% exits" % (D * 100))
        report("HI-vol -> BREAK bet", hi, lambda e: sym_trade(e, True, D))
        report("LO-vol -> HOLD bet", lo, lambda e: sym_trade(e, False, D))


run("15m")
