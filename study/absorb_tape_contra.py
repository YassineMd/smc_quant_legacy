# -*- coding: utf-8 -*-
"""User strategy: after a BIG/CRAZY Wall-Absorption, take a later candle UNDER THE SAME RADAR that is a tape/candle
DIVERGENCE, trade in the wall-absorption direction (support->LONG, resistance->SHORT). Entry candle j (>event, same
radar, j<=wi1): absR(j) < -0.75 AND tape contradicts the candle (LONG: bearish & TapeB>TapeS ; SHORT: bullish &
TapeS>TapeB). Entry = close of j. 15m, both recon yr. The ENTRY CANDIDATES are fixed; we SWEEP SL/TP to see if the
edge monetises (the radar-edge SL was too wide vs the 0.2% TP). Non-overlapping, barrier first-passage, 0.04% RT."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW, absorption as ABS

ABSR_MAX = -0.75; K = 24; HORIZON = 192; FEE = 0.0004; SL_PAD = 0.001
print("loading + detecting + absR ...", flush=True)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open", b.get("open_price"))) for b in A]; C = [_f(b.get("close", b.get("close_price"))) for b in A]
Hh = [_f(b.get("high")) for b in A]; Ll = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
absR = [ABS.absorption(A, i)[0] for i in range(n)]


def tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


walls = AL.detect(A)
events = [e for e in CW.detect(A, walls) if e["i"] + 2 < n]
# entry candidates (independent of SL/TP): first qualifying candle per event, dedup by bar
cands = []; seen = set()
for e in events:
    ei = e["i"]; ws = e["wall_side"]; wlo = e.get("wlo"); whi = e.get("whi"); wi1 = int(e.get("wi1", ei))
    if wlo is None:
        continue
    d = 1 if ws == "S" else -1
    for j in range(ei + 1, min(n, ei + 1 + K, wi1 + 1)):
        if not (wlo <= C[j] <= whi):
            continue
        aR = absR[j]
        if aR is None or aR >= ABSR_MAX:
            continue
        tb, ts = tape(A[j])
        ok = (C[j] < O[j] and tb > ts) if d > 0 else (C[j] > O[j] and ts > tb)
        if not ok:
            continue
        if j not in seen:
            cands.append((j, d, wlo, whi)); seen.add(j)
        break
cands.sort()
print("bars=%d  events=%d  entry-candidates=%d" % (n, len(events), len(cands)), flush=True)


def sim(tp, sl_spec):
    """sl_spec: 'radar' (edge +-0.1%) or a float fraction (fixed % SL)."""
    ou = -1; res = []
    for j, d, wlo, whi in cands:
        if j <= ou or j + 1 >= n:
            continue
        entry = C[j]
        sl_px = (wlo * (1 - SL_PAD) if d > 0 else whi * (1 + SL_PAD)) if sl_spec == "radar" else entry * (1 - d * sl_spec)
        if (d > 0 and sl_px >= entry) or (d < 0 and sl_px <= entry):
            continue
        tp_px = entry * (1 + d * tp)
        out = None; xi = min(n - 1, j + HORIZON)
        for k in range(j + 1, min(n, j + 1 + HORIZON)):
            hs = (Ll[k] <= sl_px) if d > 0 else (Hh[k] >= sl_px)
            ht = (Hh[k] >= tp_px) if d > 0 else (Ll[k] <= tp_px)
            if hs:
                out = "L"; xi = k; break
            if ht:
                out = "W"; xi = k; break
        r = (tp - FEE) if out == "W" else ((-(abs(entry - sl_px) / entry) - FEE) if out == "L" else d * (C[xi] - entry) / entry - FEE)
        res.append((YR[j], r, out)); ou = xi
    return res


def rep(tag, res):
    for yl, yf in (("BOTH", None), ("25", 2025), ("26", 2026)):
        r = [x for x in res if (yf is None or x[0] == yf)]
        if not r:
            continue
        N = len(r); w = sum(1 for x in r if x[2] == "W"); L = sum(1 for x in r if x[2] == "L")
        net = sum(x[1] for x in r) * 100
        bal = 1.0; pk = 1.0; mdd = 0.0
        for _, rv, _o in r:
            bal *= (1 + rv); pk = max(pk, bal); mdd = min(mdd, bal / pk - 1)
        print("   %-22s [%s] n=%3d win=%.1f%% net=%+.2f%% exp=%+.3f%% comp=%+.1f%% DD=%.1f%%" % (
            tag, yl, N, 100 * w / max(1, w + L), net, net / N, (bal - 1) * 100, mdd * 100), flush=True)


print("=== original: TP0.2% / SL radar-edge ===", flush=True)
rep("radar TP0.2", sim(0.002, "radar"))
print("=== fixed SL sweep (same entries) ===", flush=True)
for tp, sl in ((0.002, 0.0015), (0.002, 0.002), (0.003, 0.003), (0.002, 0.001), (0.0025, 0.0015)):
    rep("TP%.2f/SL%.2f" % (tp * 100, sl * 100), sim(tp, sl))
