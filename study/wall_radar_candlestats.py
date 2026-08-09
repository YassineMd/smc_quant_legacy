# -*- coding: utf-8 -*-
"""Do the CANDLE STATS (stats-box list) of the candle entering a wall's radar predict RESIST vs BREAK?
Entry candle k0 of each 15m radar visit -> build_stats columns, oriented to the break direction. AUC->BREAK, both years."""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import absorption_level_detect as AL

LF = 24
print("loading 15m + build_stats (per-candle) ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
S, _O, _C = build_stats(A)
print("stats built — detecting walls ...", flush=True)
walls = AL.detect(A)


def outcome_bar(k0, r_lo, r_hi, side):
    for k in range(k0, min(n, k0 + LF)):
        if side == "R":
            if C[k] > r_hi: return k, 0
            if C[k] < r_lo: return k, 1
        else:
            if C[k] < r_lo: return k, 0
            if C[k] > r_hi: return k, 1
    return None, None


DIR = ("body_pct", "delta_pct", "da2", "dP_t", "dP_1", "dP_2", "delta_up", "delta_dn", "skew", "mmxskew", "effagg_sp")
MAG = ("range_pct", "mov_mag", "body_frac", "vw", "absorb_R", "absorb_h1", "absorb_h2",
       "ker_buy", "ker_sell", "ber", "ser", "ber30", "ser30", "cost_tick_ratio", "vel_ratio", "tape_ratio")

V = []
for w in walls:
    if w["strength"] < 0.12:
        continue
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for (k0, k1) in w["radar_runs"]:
        ko, oc = outcome_bar(k0, r_lo, r_hi, side)
        if oc is None or ko <= k0 or ko >= n:
            continue
        bd = 1.0 if side == "R" else -1.0
        f = {}
        for k in DIR:
            v = S[k][k0]
            f[k] = v * bd if v == v else float("nan")
        for k in MAG:
            f[k] = S[k][k0]
        cir = S["close_in_range"][k0]; f["cir_dir"] = (cir - 0.5) * bd if cir == cir else float("nan")
        rB = S["rB"][k0]; f["rB_dir"] = (rB - 50.0) * bd if rB == rB else float("nan")
        hu = S["halfdom_up"][k0]; f["hdup_dir"] = (hu - 50.0) * bd if hu == hu else float("nan")
        uw = S["upper_wick"][k0]; lw = S["lower_wick"][k0]
        f["reject_wick"] = (uw if side == "R" else lw)          # wick rejecting AT the wall (expect -> RESIST)
        f["break_wick"] = (lw if side == "R" else uw)
        V.append({"brk": 1 - oc, "yr": YR[k0], "f": f})

base = sum(v["brk"] for v in V) / len(V)
print("\n=== 15m radar entries: %d | base BREAK %.1f%% ===" % (len(V), 100 * base), flush=True)


def auc(feat):
    a = [v["f"][feat] for v in V if v["brk"] and v["f"].get(feat) == v["f"].get(feat)]
    b = [v["f"][feat] for v in V if not v["brk"] and v["f"].get(feat) == v["f"].get(feat)]
    if len(a) < 30 or len(b) < 30:
        return None
    g = auc_p(a, b)[0]
    a25 = auc_p([v["f"][feat] for v in V if v["brk"] and v["yr"] == 2025 and v["f"].get(feat) == v["f"].get(feat)],
                [v["f"][feat] for v in V if not v["brk"] and v["yr"] == 2025 and v["f"].get(feat) == v["f"].get(feat)])[0]
    a26 = auc_p([v["f"][feat] for v in V if v["brk"] and v["yr"] == 2026 and v["f"].get(feat) == v["f"].get(feat)],
                [v["f"][feat] for v in V if not v["brk"] and v["yr"] == 2026 and v["f"].get(feat) == v["f"].get(feat)])[0]
    return g, a25, a26


allf = list(DIR) + list(MAG) + ["cir_dir", "rB_dir", "hdup_dir", "reject_wick", "break_wick"]
rows = []
for feat in allf:
    r = auc(feat)
    if r:
        rows.append((abs(r[0] - 0.5), feat, r[0], r[1], r[2]))
rows.sort(reverse=True)
print("   stat            AUC   (25 / 26)   [oriented: >0.5 -> higher stat predicts BREAK]", flush=True)
for _, feat, g, a25, a26 in rows:
    flag = "  <-- both-yr stable" if (g - 0.5) * (a25 - 0.5) > 0 and (g - 0.5) * (a26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("   %-14s %.3f  (%.2f/%.2f)%s" % (feat, g, a25, a26, flag), flush=True)
