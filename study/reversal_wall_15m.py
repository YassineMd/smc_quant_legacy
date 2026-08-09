# -*- coding: utf-8 -*-
"""REVERSAL POINT entry at a wall/radar, structural stop, FIXED 0.5% NET TP. Take a reversal_detect signal (bottom=long
/ top=short) only if it fires AT a same-side wall (support for long / resistance for short — candle range overlaps the
wall's radar). SL = 0.1% beyond the radar's far extreme (long: below P-3band / short: above P+3band). TP = fixed 0.5%
NET (gross 0.6% to cover the 0.1% round-trip fee). Report win%/net-avg%/net-PF both years, for bias-MATCH (reversal
resumes the trend), INVERTED bias (counter-trend fade), and no-bias; plus strong/gold tiers. Wall filter first, slow
biases on survivors."""
import os, sys, time
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import reversal_detect, absorption_level_detect as AL, swing_lvn_detect as SW, wall_regime_detect as WR

W, WIN, HORIZON, SLPAD, TP_NET, FEE = 96, 600, 192, 0.001, 0.5, 0.10
print("loading 15m ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
O = np.array([b["open"] for b in A]); C = np.array([b["close"] for b in A])
H = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

print("reversal_detect + walls ...", flush=True); t0 = time.time()
revs = reversal_detect.detect(A)
walls = AL.detect(A)
print("   %d reversal marks, %d walls in %.0fs" % (len(revs), len(walls), time.time() - t0), flush=True)
wp = np.array([w["price"] for w in walls]); wb = np.array([w["band"] for w in walls])
wi0 = np.array([w["i0"] for w in walls]); wi1 = np.array([w["i1"] for w in walls]); wsd = np.array([0 if w["side"] == "R" else 1 for w in walls])
lo3 = wp - 3.0 * wb; hi3 = wp + 3.0 * wb        # radar extremes; wsd: R=0(resistance) S=1(support)
cr = np.array(sorted((w["i0"], 1 if w["side"] == "R" else 0) for w in walls)); cbars = cr[:, 0]; cisR = cr[:, 1]


def at_wall(i, side):
    """same-side wall active at i whose radar the reversal candle overlaps: long->support / short->resistance."""
    want = 1 if side > 0 else 0                                  # long wants support(1), short resistance(0)
    m = (wsd == want) & (wi0 < i) & (wi1 >= i) & ~((H[i] < lo3) | (Lo[i] > hi3))
    if not m.any():
        return None
    idx = np.where(m)[0]
    j = idx[np.argmin(np.abs(wp[idx] - C[i]))]
    return float(lo3[j]), float(hi3[j])


def wall_bias_dir(i):
    a = np.searchsorted(cbars, i - W); b = np.searchsorted(cbars, i)
    if b - a < 3:
        return 0
    rc = float(cisR[a:b].mean())
    return 1 if rc <= WR.RC_UP else (-1 if rc >= WR.RC_DOWN else 0)


def outcome(i, side, e, sl, tp):
    for k in range(i + 1, min(n, i + 1 + HORIZON)):
        hit_sl = (Lo[k] <= sl) if side > 0 else (H[k] >= sl)
        hit_tp = (H[k] >= tp) if side > 0 else (Lo[k] <= tp)
        if hit_sl:
            return 0
        if hit_tp:
            return 1
    return None


surv = []
for r in revs:
    i = r["i"]; side = 1 if r["side"] == "bottom" else -1
    if i - WIN < 0 or i + 2 >= n:
        continue
    z = at_wall(i, side)
    if z is None:
        continue
    surv.append((r, side, z))
print("   reversals at a same-side wall: %d of %d\n" % (len(surv), len(revs)), flush=True)

rec = []; t0 = time.time()
for r, side, (zl, zh) in surv:
    i = r["i"]; e = C[i]
    sl = (zl * (1 - SLPAD)) if side > 0 else (zh * (1 + SLPAD))    # 0.1% beyond the far radar extreme
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        continue
    tp = e * (1 + (TP_NET + FEE) / 100.0) if side > 0 else e * (1 - (TP_NET + FEE) / 100.0)   # gross 0.6% -> net 0.5%
    oc = outcome(i, side, e, sl, tp)
    if oc is None:
        continue
    sld = abs(e - sl) / e * 100.0
    net = TP_NET if oc else -(sld + FEE)                          # net % P&L
    sw = SW.bias(A[i - WIN:i]); sw_dir = 1 if sw["dir"] == "long" else (-1 if sw["dir"] == "short" else 0)
    wl_dir = wall_bias_dir(i)
    rec.append({"yr": YR[i], "win": oc, "net": net, "sld": sld, "strong": r["strong"], "gold": r["gold"],
                "bm": (sw_dir == side and wl_dir == side), "inv": (sw_dir == -side and wl_dir == -side)})
print("   evaluated %d in %.0fs\n" % (len(rec), time.time() - t0), flush=True)


def stats(sel):
    if not sel:
        return "n=0"
    nw = sum(x["win"] for x in sel); n = len(sel)
    gw = sum(x["net"] for x in sel if x["net"] > 0); gl = -sum(x["net"] for x in sel if x["net"] < 0)
    pf = gw / gl if gl > 0 else float("inf")
    return "n=%3d win %4.1f%% NET-PF %4.2f net/tr %+.3f%% (SL %.2f%%)" % (
        n, 100 * nw / n, pf, sum(x["net"] for x in sel) / n, np.mean([x["sld"] for x in sel]))


def line(name, sel):
    s25 = [x for x in sel if x["yr"] == 2025]; s26 = [x for x in sel if x["yr"] == 2026]
    print("   %-26s %s | 25: %s | 26: %s" % (name, stats(sel), stats(s25), stats(s26)), flush=True)


print("=== REVERSAL @ wall/radar | SL=radar extreme, TP=0.5%% NET fixed | both yr ===", flush=True)
line("all rev@wall", rec)
line("  + bias-match (resume)", [x for x in rec if x["bm"]])
line("  + inverted bias (fade)", [x for x in rec if x["inv"]])
line("  strong + bias-match", [x for x in rec if x["bm"] and x["strong"]])
line("  gold + bias-match", [x for x in rec if x["bm"] and x["gold"]])
line("  strong + inverted", [x for x in rec if x["inv"] and x["strong"]])
print("   [win needs > ~%.0f%% to beat the RR: TP 0.5%% vs SL ~%.2f%%]" % (
    100 * np.mean([x["sld"] for x in rec]) / (0.5 + np.mean([x["sld"] for x in rec])), np.mean([x["sld"] for x in rec])), flush=True)
