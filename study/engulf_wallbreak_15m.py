# -*- coding: utf-8 -*-
"""15m engulfing, WALL/RADAR-BREAKOUT variant. Take a shipped momentum_detect signal ONLY if: (1) both biases match
(swing_lvn_detect.bias + wall_regime creation-side agree with side); (2) the signal candle BREAKS OUT of a wall/radar
area -> OPEN inside [P-3band, P+3band] and CLOSE outside it on the trade side; (3) SL at the radar extreme +/-0.1%.
SL tested two ways: FAR (structural, opposite extreme) and NEAR (broken edge). TP = RR*SL_dist (gold 2.0 else 1.2).
Win%/PF/avg-R both years, gross. Breakout geometry filtered first (fast); slow biases only on survivors."""
import os, sys, time
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import momentum_detect, absorption_level_detect as AL, swing_lvn_detect as SW, wall_regime_detect as WR

W, WIN, HORIZON, SLPAD = 96, 600, 384, 0.001
print("loading 15m ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
O = np.array([b["open"] for b in A]); C = np.array([b["close"] for b in A])
H = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

print("momentum_detect ...", flush=True); t0 = time.time()
trades = momentum_detect.detect(A)
print("   %d trades in %.0fs" % (len(trades), time.time() - t0), flush=True)
walls = AL.detect(A)
wp = np.array([w["price"] for w in walls]); wb = np.array([w["band"] for w in walls])
wi0 = np.array([w["i0"] for w in walls]); wi1 = np.array([w["i1"] for w in walls])
lo3 = wp - 3.0 * wb; hi3 = wp + 3.0 * wb
cr = np.array(sorted((w["i0"], 1 if w["side"] == "R" else 0) for w in walls)); cbars = cr[:, 0]; cisR = cr[:, 1]


def breakout(i, side):
    """nearest wall active at i whose radar the candle OPENS inside and CLOSES beyond (trade dir). -> (lo3,hi3) or None"""
    m = (wi0 < i) & (wi1 >= i) & (lo3 <= O[i]) & (O[i] <= hi3)
    m &= (C[i] > hi3) if side > 0 else (C[i] < lo3)
    if not m.any():
        return None
    idx = np.where(m)[0]
    j = idx[np.argmin(np.abs(wp[idx] - O[i]))]                 # the wall the candle sat in
    return float(lo3[j]), float(hi3[j])


def wall_bias_dir(i):
    a = np.searchsorted(cbars, i - W); b = np.searchsorted(cbars, i)
    if b - a < 3:
        return 0
    rc = float(cisR[a:b].mean())
    return 1 if rc <= WR.RC_UP else (-1 if rc >= WR.RC_DOWN else 0)


def outcome(i, side, entry, sl, tp):
    for k in range(i + 1, min(n, i + 1 + HORIZON)):
        hit_sl = (Lo[k] <= sl) if side > 0 else (H[k] >= sl)
        hit_tp = (H[k] >= tp) if side > 0 else (Lo[k] <= tp)
        if hit_sl:
            return 0
        if hit_tp:
            return 1
    return None


# stage 1: breakout geometry (fast)
surv = []
for t in trades:
    i = t["i"]; side = t["side"]
    if i - WIN < 0 or i + 2 >= n:
        continue
    z = breakout(i, side)
    if z is None:
        continue
    surv.append((t, z))
print("   wall/radar breakouts: %d of %d trades" % (len(surv), len(trades)), flush=True)

# stage 2: biases (slow) only on survivors
rec = []; t0 = time.time()
for t, (zl, zh) in surv:
    i = t["i"]; side = t["side"]; rr = 2.0 if t.get("tier") == "gold" else 1.2
    sw = SW.bias(A[i - WIN:i]); sw_dir = 1 if sw["dir"] == "long" else (-1 if sw["dir"] == "short" else 0)
    bm = (sw_dir == side) and (wall_bias_dir(i) == side)
    e = C[i]
    sl_far = (zl * (1 - SLPAD)) if side > 0 else (zh * (1 + SLPAD))     # opposite radar extreme (structural)
    sl_near = (zh * (1 - SLPAD)) if side > 0 else (zl * (1 + SLPAD))    # broken edge (retest)
    row = {"yr": YR[i], "side": side, "rr": rr, "bm": bm}
    for tag, sl in (("far", sl_far), ("near", sl_near)):
        if (side > 0 and sl >= e) or (side < 0 and sl <= e):
            row[tag] = None; continue
        d = abs(e - sl); tp = e + rr * d * side
        oc = outcome(i, side, e, sl, tp)
        row[tag] = None if oc is None else {"win": oc, "R": (rr if oc else -1.0), "sld_pct": d / e * 100.0}
    rec.append(row)
print("   evaluated in %.0fs\n" % (time.time() - t0), flush=True)


FEE = 0.10                                                    # round-trip fee in PRICE % (0.05%/side)


def stats(sel, tag):
    v = [x[tag] for x in sel if x.get(tag)]
    if not v:
        return "n=0"
    nw = sum(z["win"] for z in v); n = len(v)
    gross = [z["R"] for z in v]; net = [z["R"] - FEE / z["sld_pct"] for z in v]     # fee in R = 0.10% / SLdist%
    gw = sum(g for g in gross if g > 0); gl = -sum(g for g in gross if g < 0)
    nwp = sum(x for x in net if x > 0); nlp = -sum(x for x in net if x < 0)
    pf = gw / gl if gl > 0 else float("inf"); pfn = nwp / nlp if nlp > 0 else float("inf")
    return "n=%3d win %4.1f%% | GROSS PF %.2f avgR %+.3f | NET PF %.2f avgR %+.3f (SL %.2f%%)" % (
        n, 100 * nw / n, pf, sum(gross) / n, pfn, sum(net) / n, np.mean([z["sld_pct"] for z in v]))


def line(name, sel, tag):
    s25 = [x for x in sel if x["yr"] == 2025]; s26 = [x for x in sel if x["yr"] == 2026]
    print("   %-22s %s | 25: %s | 26: %s" % (name, stats(sel, tag), stats(s25, tag), stats(s26, tag)), flush=True)


print("=== 15m engulf WALL/RADAR-BREAKOUT (open in area, close out) | gross R, both yr ===", flush=True)
bmrec = [x for x in rec if x["bm"]]
print("   survivors %d | both-bias-match %d\n" % (len(rec), len(bmrec)), flush=True)
for tag, lbl in (("far", "SL = FAR radar extreme (structural)"), ("near", "SL = NEAR broken edge (retest)")):
    print("   -- %s --" % lbl, flush=True)
    line("breakout (all)", rec, tag)
    line("breakout + bias-match", bmrec, tag)
    print("", flush=True)
