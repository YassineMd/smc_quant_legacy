# -*- coding: utf-8 -*-
"""EVOLUTION study: as price sits inside a wall's radar, does the TRAJECTORY of the flow predict RESIST vs BREAK?
Per visit (multi-candle only), over the candles [k0, ko) BEFORE resolution: oriented delta series, box-volume series,
body series -> trend / build / acceleration features. AUC -> BREAK, both years. All oriented to the break direction."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import absorption_level_detect as AL

LF = 24
print("loading 15m + build_stats ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
S, _O, _C = build_stats(A)
walls = AL.detect(A)


def box_vol(bucket, r_lo, r_hi):
    v = 0.0
    for ps, vv in (bucket.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            v += _f(vv.get("b")) + _f(vv.get("s"))
    return v


def outcome_bar(k0, r_lo, r_hi, side):
    for k in range(k0, min(n, k0 + LF)):
        if side == "R":
            if C[k] > r_hi: return k, 0
            if C[k] < r_lo: return k, 1
        else:
            if C[k] < r_lo: return k, 0
            if C[k] > r_hi: return k, 1
    return None, None


def slope(y):
    m = len(y)
    if m < 2:
        return float("nan")
    x = np.arange(m, dtype=float)
    return float(np.polyfit(x, y, 1)[0])


V = []
for w in walls:
    if w["strength"] < 0.12:
        continue
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for (k0, k1) in w["radar_runs"]:
        ko, oc = outcome_bar(k0, r_lo, r_hi, side)
        if oc is None or ko <= k0 or ko >= n:
            continue
        dur = ko - k0
        if dur < 3:                                            # need a trajectory AFTER excluding the last candle
            continue
        end = ko - 1                                           # EXCLUDE the candle immediately before resolution (anti-look-ahead)
        bd = 1.0 if side == "R" else -1.0
        dser = [(_f(S["delta_pct"][k]) * bd) for k in range(k0, end)]
        vser = [box_vol(A[k], r_lo, r_hi) for k in range(k0, end)]
        bser = [(_f(S["body_pct"][k]) * bd) for k in range(k0, end)]
        f = {
            "delta_build": dser[-1] - dser[0],                 # is break-dir delta stronger by the end?
            "delta_last": dser[-1],                            # most-recent pressure
            "delta_cum": sum(dser),                            # total break-dir pressure
            "delta_slope": slope(dser),
            "vol_build": (vser[-1] - vser[0]),                 # volume rising as price sits?
            "vol_ratio": (vser[-1] / vser[0]) if vser[0] > 0 else float("nan"),
            "vol_slope": slope(vser),
            "body_accel": bser[-1] - bser[0],                  # candles getting more decisive toward break?
            "body_last": bser[-1],
        }
        V.append({"brk": 1 - oc, "yr": YR[k0], "dur": dur, "f": f})

base = sum(v["brk"] for v in V) / len(V)
print("\n=== multi-candle radar visits (dur>=2): %d | base BREAK %.1f%% | avg dur %.1f ===" % (
    len(V), 100 * base, np.mean([v["dur"] for v in V])), flush=True)


def auc(feat):
    a = [v["f"][feat] for v in V if v["brk"] and v["f"][feat] == v["f"][feat]]
    b = [v["f"][feat] for v in V if not v["brk"] and v["f"][feat] == v["f"][feat]]
    if len(a) < 30 or len(b) < 30:
        return None
    g = auc_p(a, b)[0]
    a25 = auc_p([v["f"][feat] for v in V if v["brk"] and v["yr"] == 2025 and v["f"][feat] == v["f"][feat]],
                [v["f"][feat] for v in V if not v["brk"] and v["yr"] == 2025 and v["f"][feat] == v["f"][feat]])[0]
    a26 = auc_p([v["f"][feat] for v in V if v["brk"] and v["yr"] == 2026 and v["f"][feat] == v["f"][feat]],
                [v["f"][feat] for v in V if not v["brk"] and v["yr"] == 2026 and v["f"][feat] == v["f"][feat]])[0]
    return g, a25, a26


print("   evolution feature   AUC   (25 / 26)   [>0.5 -> higher -> BREAK]", flush=True)
rows = []
for feat in ("delta_build", "delta_last", "delta_cum", "delta_slope", "vol_build", "vol_ratio", "vol_slope", "body_accel", "body_last"):
    r = auc(feat)
    if r:
        rows.append((abs(r[0] - 0.5), feat, r[0], r[1], r[2]))
rows.sort(reverse=True)
for _, feat, g, a25, a26 in rows:
    flag = "  <-- both-yr" if (g - 0.5) * (a25 - 0.5) > 0 and (g - 0.5) * (a26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("   %-14s %.3f  (%.2f/%.2f)%s" % (feat, g, a25, a26, flag), flush=True)
