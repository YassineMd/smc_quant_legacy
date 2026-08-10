# -*- coding: utf-8 -*-
"""Does the WALL R:S bias filter improve the star trades? Rolling resistance-share of wall CREATIONS over the past W
bars -> bias (per memory: R-heavy => bearish/price-falls, S-heavy => bullish). Take a star trade ONLY when it aligns
with that bias. Compare vs taking every signal. 15m, both recon yr, non-overlapping, barrier first-passage, 0.04% RT."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW

W = 96                # rolling window (bars) for the R:S creation bias
HI, LO = 0.55, 0.45   # r_share thresholds -> bearish / bullish (neutral in between = skip)
FEE = 0.0004
print("loading + detecting ...", flush=True)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = [_f(b.get("close", b.get("close_price"))) for b in A]; Hh = [_f(b.get("high")) for b in A]; Ll = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)
events = [e for e in CW.detect(A, walls) if e["i"] + 1 < n]
# wall creations sorted by i0, with side
cre = sorted(((int(w["i0"]), w["side"]) for w in walls), key=lambda t: t[0])
ci0 = [c[0] for c in cre]


def bias_at(i):
    """+1 bullish / -1 bearish / 0 neutral from the resistance-share of walls CREATED in (i-W, i]."""
    lo = bisect.bisect_left(ci0, i - W); hi = bisect.bisect_right(ci0, i)
    r = s = 0
    for k in range(lo, hi):
        if cre[k][1] == "R": r += 1
        else: s += 1
    t = r + s
    if t < 4: return 0
    rs = r / t
    return -1 if rs > HI else (1 if rs < LO else 0)


def sim(direction_mode, rr, require_align=True, side_align=False):
    """direction_mode: 'bias' (trade the wall bias) | 'cont' (continuation of the star). require_align: only take when
    the trade dir == wall bias. side_align: also require the star wall_side to match the bias (R<->bearish,S<->bullish)."""
    ou = -1; res = []
    for e in events:
        i = e["i"]; ws = e["wall_side"]
        b = bias_at(i)
        rej = 1 if ws == "S" else -1
        cont = -rej
        d = b if direction_mode == "bias" else cont
        if d == 0:
            continue
        if require_align and b != 0 and d != b:
            continue
        if require_align and b == 0:
            continue
        if side_align:
            want = "R" if b < 0 else "S"     # bearish wants a resistance star, bullish a support star
            if ws != want:
                continue
        if i <= ou or i + 1 >= n:
            continue
        E = C[i]; tp = E * (1 + d * rr); sl = E * (1 - d * rr); out = None; xi = min(n - 1, i + 192)
        for k in range(i + 1, min(n, i + 193)):
            hs = (Ll[k] <= sl) if d > 0 else (Hh[k] >= sl); ht = (Hh[k] >= tp) if d > 0 else (Ll[k] <= tp)
            if hs: out = "L"; xi = k; break
            if ht: out = "W"; xi = k; break
        r = (rr - FEE) if out == "W" else ((-rr - FEE) if out == "L" else d * (C[xi] - E) / E - FEE)
        res.append((YR[i], r, out)); ou = xi
    return res


def report(tag, res):
    for yl, yf in (("BOTH", None), ("25", 2025), ("26", 2026)):
        r = [x for x in res if (yf is None or x[0] == yf)]
        if not r: continue
        N = len(r); w = sum(1 for x in r if x[2] == "W"); net = sum(x[1] for x in r) * 100
        bal = 1.0; pk = 1.0; mdd = 0.0
        for _, rv, _o in r: bal *= (1 + rv); pk = max(pk, bal); mdd = min(mdd, bal / pk - 1)
        print("   %-30s [%s] n=%4d win=%5.1f%% net=%+7.2f%% exp=%+.3f%% comp=%+6.1f%% DD=%.1f%%" % (
            tag, yl, N, 100 * w / N, net, net / N, (bal - 1) * 100, mdd * 100), flush=True)


print("bars=%d events=%d walls=%d" % (n, len(events), len(walls)), flush=True)
print("\n=== trade the WALL BIAS on a star trigger (aligned only), balanced RR ===", flush=True)
for rr in (0.005, 0.010):
    report("bias/align RR%.1f%%" % (rr * 100), sim("bias", rr, require_align=True))
print("\n=== + also require the star SIDE to match the bias (double-confirm) ===", flush=True)
for rr in (0.005, 0.010):
    report("bias/align+side RR%.1f%%" % (rr * 100), sim("bias", rr, require_align=True, side_align=True))
print("\n=== user's wide-SL structure, bias-aligned (TP0.3%/SL2.5%) ===", flush=True)
def sim_wide():
    ou = -1; res = []
    for e in events:
        i = e["i"]; ws = e["wall_side"]; b = bias_at(i)
        if b == 0: continue
        d = b
        if i <= ou or i + 1 >= n: continue
        E = C[i]; tp = E * (1 + d * 0.003); sl = E * (1 - d * 0.025); out = None; xi = min(n - 1, i + 192)
        for k in range(i + 1, min(n, i + 193)):
            hs = (Ll[k] <= sl) if d > 0 else (Hh[k] >= sl); ht = (Hh[k] >= tp) if d > 0 else (Ll[k] <= tp)
            if hs: out = "L"; xi = k; break
            if ht: out = "W"; xi = k; break
        r = (0.003 - FEE) if out == "W" else ((-0.025 - FEE) if out == "L" else d * (C[xi] - E) / E - FEE)
        res.append((YR[i], r, out)); ou = xi
    return res
report("bias/align TP0.3/SL2.5", sim_wide())
