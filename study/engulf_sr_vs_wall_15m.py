# -*- coding: utf-8 -*-
"""15m Engulfing strategy: which LEVEL-CONFLUENCE source is best — S/R indicator, WALL indicator, or BOTH — and does
requiring the SWING bias AND WALL bias to match the trade side help? Runs the shipped momentum_detect for trades, then
slices cohorts by confluence source x bias-match and reports win%/PF/avg-R (in risk units), both years. Causal biases
(swing on trailing 600 bars; wall from creation-side window). Gross R (SL~0.1% -> fees are large; RELATIVE compare)."""
import os, sys, time
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import momentum_detect, absorption_level_detect as AL, swing_lvn_detect as SW, wall_regime_detect as WR

W, WIN, HORIZON = 96, 600, 192
print("loading 15m ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
H = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); C = np.array([b["close"] for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

print("running momentum_detect over full series ...", flush=True)
t0 = time.time()
trades = momentum_detect.detect(A)
print("   %d trades in %.1fs" % (len(trades), time.time() - t0), flush=True)

walls = AL.detect(A)
WS = [(w["price"], w["side"], w["i0"], w["i1"]) for w in walls]           # (price, side, i0, i1)
wp = np.array([w[0] for w in WS]); wside = np.array([1 if w[1] == "R" else 0 for w in WS])
wi0 = np.array([w[2] for w in WS]); wi1 = np.array([w[3] for w in WS])
cr = np.array(sorted((w["i0"], 1 if w["side"] == "R" else 0) for w in walls))
cbars = cr[:, 0]; cisR = cr[:, 1]


def at_wall(i, side):
    """engulf touches a SAME-SIDE wall active at i: long -> a SUPPORT wall (side S) / short -> a RESISTANCE wall."""
    tol = C[i] * 0.001
    want_R = 0 if side > 0 else 1                                          # long wants support(S=0), short wants resistance(R=1)
    m = (wside == want_R) & (wi0 <= i) & (wi1 >= i) & (wp >= Lo[i] - tol) & (wp <= H[i] + tol)
    return bool(m.any())


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
        if hit_sl:                                                        # SL-first on a same-bar tie (conservative)
            return 0
        if hit_tp:
            return 1
    return None


print("evaluating trades (outcome + at_wall + biases) ...", flush=True)
rec = []
t0 = time.time()
for t in trades:
    i = t["i"]; side = t["side"]
    if i - WIN < 0 or i + 2 >= n:
        continue
    oc = outcome(i, side, t["entry"], t["sl"], t["tp"])
    if oc is None:
        continue
    rr = abs(t["tp"] - t["entry"]) / max(1e-9, abs(t["entry"] - t["sl"]))
    sw = SW.bias(A[i - WIN:i]); sw_dir = 1 if sw["dir"] == "long" else (-1 if sw["dir"] == "short" else 0)
    rec.append({"yr": YR[i], "side": side, "win": oc, "R": (rr if oc else -1.0), "rr": rr,
                "at_sr": t.get("src") == "SR", "at_wall": at_wall(i, side),
                "sw_match": sw_dir == side, "wl_match": wall_bias_dir(i) == side,
                "tier": t.get("tier", "")})
print("   %d resolved trades in %.1fs" % (len(rec), time.time() - t0), flush=True)


def stats(sel):
    if not sel:
        return "n=0"
    nw = sum(x["win"] for x in sel); n = len(sel)
    gw = sum(x["R"] for x in sel if x["win"]); gl = -sum(x["R"] for x in sel if not x["win"])
    pf = gw / gl if gl > 0 else float("inf")
    return "n=%3d win %4.1f%% PF %4.2f avgR %+.3f" % (n, 100 * nw / n, pf, sum(x["R"] for x in sel) / n)


def line(name, base):
    s25 = [x for x in base if x["yr"] == 2025]; s26 = [x for x in base if x["yr"] == 2026]
    print("   %-26s %s   | 25: %s | 26: %s" % (name, stats(base), stats(s25), stats(s26)), flush=True)


bm = lambda x: x["sw_match"] and x["wl_match"]                            # BOTH biases match the side
print("\n=== 15m engulf: confluence source x bias-match (both yr | 2025 | 2026) ===", flush=True)
print("   [bias-match = swing bias AND wall bias agree with the trade side]\n", flush=True)
line("ALL trades", rec)
line("  + bias-match", [x for x in rec if bm(x)])
print("", flush=True)
line("S/R confluence", [x for x in rec if x["at_sr"]])
line("  + bias-match", [x for x in rec if x["at_sr"] and bm(x)])
line("WALL confluence", [x for x in rec if x["at_wall"]])
line("  + bias-match", [x for x in rec if x["at_wall"] and bm(x)])
line("BOTH (S/R & wall)", [x for x in rec if x["at_sr"] and x["at_wall"]])
line("  + bias-match", [x for x in rec if x["at_sr"] and x["at_wall"] and bm(x)])
print("\n   -- bias-match components alone --", flush=True)
line("swing-match only", [x for x in rec if x["sw_match"]])
line("wall-match only", [x for x in rec if x["wl_match"]])
line("either confluence", [x for x in rec if x["at_sr"] or x["at_wall"]])
line("  + bias-match", [x for x in rec if (x["at_sr"] or x["at_wall"]) and bm(x)])
