# -*- coding: utf-8 -*-
"""User strategy: after a BIG/CRAZY Wall-Absorption event, take a later candle UNDER THE SAME RADAR that is a
tape/candle DIVERGENCE, trade in the wall-absorption direction. 15m, both recon yr.
  dir: support wall -> LONG, resistance wall -> SHORT.
  entry candle (bar j after the event, same radar, j <= wi1):
     absR(j) < ABSR_MAX (-0.75)  AND  tape contradicts the candle:
        LONG:  bearish candle (close<open) AND Tape-B > Tape-S     (buyers active while price falls)
        SHORT: bullish candle (close>open) AND Tape-S > Tape-B
  entry = close of j;  SL = 0.1% BELOW the radar (long) / ABOVE (short);  TP = 0.2% from entry.
Non-overlapping, barrier first-passage (SL-first tie), 0.04% RT fee. Reports win/net/both-yr/DD + max-drawdown."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW, absorption as ABS

ABSR_MAX = -0.75       # entry candle absorption-R must be BELOW this ("light/easy" per absorption.label)
TP = 0.002; SL_PAD = 0.001; K = 24; HORIZON = 192; FEE = 0.0004
print("loading + detecting ...", flush=True)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open", b.get("open_price"))) for b in A]; C = [_f(b.get("close", b.get("close_price"))) for b in A]
Hh = [_f(b.get("high")) for b in A]; Ll = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
print("computing absR + tape ...", flush=True)
absR = [ABS.absorption(A, i)[0] for i in range(n)]     # A: positive=absorbed


def tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    tb = sum(b.get("sz_cb") or []) / dur; ts = sum(b.get("sz_cs") or []) / dur
    return tb, ts


walls = AL.detect(A)
events = [e for e in CW.detect(A, walls) if e["i"] + 2 < n]
print("bars=%d  events=%d (of which big/crazy carry radar)" % (n, len(events)), flush=True)

ou = -1; res = []; scanned = 0
for e in events:
    ei = e["i"]; ws = e["wall_side"]; wlo = e.get("wlo"); whi = e.get("whi"); wi1 = int(e.get("wi1", ei))
    if wlo is None or whi is None:
        continue
    d = 1 if ws == "S" else -1                            # support -> long / resistance -> short
    # find the FIRST qualifying entry candle after the event, still in the wall's life + radar
    ej = None
    for j in range(ei + 1, min(n, ei + 1 + K, wi1 + 1)):
        if j <= ou:
            continue
        if not (wlo <= C[j] <= whi):                     # entry candle closes UNDER THE SAME RADAR
            continue
        aR = absR[j]
        if aR is None or aR >= ABSR_MAX:                 # candle must be "light/easy" absorption-R
            continue
        tb, ts = tape(A[j])
        bear = C[j] < O[j]; bull = C[j] > O[j]
        ok = (bear and tb > ts) if d > 0 else (bull and ts > tb)   # tape CONTRADICTS the candle direction
        if not ok:
            continue
        ej = j; break
    if ej is None or ej + 1 >= n:
        continue
    scanned += 1
    entry = C[ej]
    sl_px = wlo * (1 - SL_PAD) if d > 0 else whi * (1 + SL_PAD)
    tp_px = entry * (1 + d * TP)
    if (d > 0 and sl_px >= entry) or (d < 0 and sl_px <= entry):   # degenerate (entry already past SL)
        continue
    out = None; xi = min(n - 1, ej + HORIZON)
    for k in range(ej + 1, min(n, ej + 1 + HORIZON)):
        hs = (Ll[k] <= sl_px) if d > 0 else (Hh[k] >= sl_px)
        ht = (Hh[k] >= tp_px) if d > 0 else (Ll[k] <= tp_px)
        if hs:
            out = "L"; xi = k; break
        if ht:
            out = "W"; xi = k; break
    if out == "W":
        r = TP - FEE
    elif out == "L":
        r = -(abs(entry - sl_px) / entry) - FEE
    else:
        r = d * (C[xi] - entry) / entry - FEE
    res.append((YR[ej], r, out)); ou = xi

print("qualifying trades: %d" % len(res), flush=True)
for yl, yf in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
    r = [x for x in res if (yf is None or x[0] == yf)]
    if not r:
        continue
    N = len(r); w = sum(1 for x in r if x[2] == "W"); L = sum(1 for x in r if x[2] == "L")
    net = sum(x[1] for x in r) * 100
    bal = 1.0; pk = 1.0; mdd = 0.0
    for _, rv, _o in r:
        bal *= (1 + rv); pk = max(pk, bal); mdd = min(mdd, bal / pk - 1)
    print("   [%s] n=%3d  win=%.1f%% (W%d/L%d)  net=%+.2f%%  exp=%+.3f%%  comp=%+.1f%%  DD=%.1f%%" % (
        yl, N, 100 * w / max(1, w + L), w, L, net, net / N, (bal - 1) * 100, mdd * 100), flush=True)
