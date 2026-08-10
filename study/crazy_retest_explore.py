# -*- coding: utf-8 -*-
"""Crazy-Wall star RETEST exploration (15m, both recon years). Validate the user's hint: after a crazy-absorption
event (support star = price closed ABOVE the bubble; resistance star = closed BELOW), does price come back and RETEST
the bubble price P? And once it retests, does the level HOLD (bounce in the rejection direction) — the basis for a
high-win-rate / wide-SL prop-challenge trade? Pure measurement, causal (event at i, outcome from >i)."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW

K = 24                     # retest / outcome window (bars) — 6h on 15m
print("loading 15m + detecting walls + star events ...", flush=True)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open", b.get("open_price"))) for b in A]
C = [_f(b.get("close", b.get("close_price"))) for b in A]
H = [_f(b.get("high")) for b in A]
L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)
events = CW.detect(A, walls)
print("bars=%d  walls=%d  star events=%d" % (n, len(walls), len(events)), flush=True)


def retest_bar(i, P, ws):
    """First bar in (i, i+K] that touches P from the correct side (support: dips back down to P; resistance:
    pushes back up to P). Returns (bar, bars_after) or (None, None)."""
    for j in range(i + 1, min(n, i + 1 + K)):
        if ws == "S" and L[j] <= P:          # came back DOWN to the support/bubble
            return j, j - i
        if ws == "R" and H[j] >= P:          # came back UP to the resistance/bubble
            return j, j - i
    return None, None


for ylabel, yf in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
    ev = [e for e in events if (yf is None or YR[e["i"]] == yf)]
    ev = [e for e in ev if e["i"] + K < n]
    if not ev:
        continue
    ret = 0; times = []; hold = 0; held_denom = 0
    for e in ev:
        i = e["i"]; P = e["price"]; ws = e["wall_side"]; d = 1 if ws == "S" else -1
        j, dt = retest_bar(i, P, ws)
        if j is None:
            continue
        ret += 1; times.append(dt)
        # After the retest bar j, does the level HOLD -> price move in the rejection dir by >=0.20% before
        # closing 0.20% through P (a simple hold test)?
        held_denom += 1
        fav = 0.0; adv = 0.0; decided = None
        for k in range(j, min(n, j + K)):
            up = (H[k] - P) / P; dn = (P - L[k]) / P
            if d > 0:                        # long: fav = up move, adverse = down through support
                if dn >= 0.0020: decided = "break"; break
                if up >= 0.0020: decided = "hold"; break
            else:
                if up >= 0.0020: decided = "break"; break
                if dn >= 0.0020: decided = "hold"; break
        if decided == "hold":
            hold += 1
    print("\n  [%s] n=%d  retest<=%db: %d (%.0f%%)  median bars-to-retest=%s  |  of retests, level HELD (+0.2%% before -0.2%%): %d/%d (%.0f%%)" % (
        ylabel, len(ev), K, ret, 100 * ret / len(ev),
        sorted(times)[len(times) // 2] if times else "-", hold, held_denom, 100 * hold / max(1, held_denom)), flush=True)
