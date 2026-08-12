"""Backtest the Wall Strategy (5m). Entry = signal-candle CLOSE. SL = radar edge ± 0.1% (below r_lo for long / above
r_hi for short). TP = 1.2 × risk (RR 1:1.2). Barrier first-passage (SL-first on tie), non-overlapping, both recon
years, 0.04% RT fee. Detector OOMs on full history (EG/PA.from_walls O(walls×window)) -> slide over the series in
overlapping chunks, map local->global indices, dedup signals by global bar."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, wall_strategy_detect as WS

FEE = 0.0004; RR = 1.2; SL_PAD = 0.001; HORIZON = 288      # 288 5m bars = 1 day cap
ENTRY_ABSORBR = "egpa" not in sys.argv                     # `... egpa` -> Easy Gold / Pure Aggression entry ONLY (no A<-1)
COND_OR = "and" not in sys.argv                            # `... and` -> (1) AND (2) instead of OR
DEF_FADE = "deffade" in sys.argv                           # `... deffade` -> require the defender tape to have decreased
A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

print("bars=%d  detecting in windows..." % n, flush=True)
CH, STEP = 6000, 5000                                      # 6000-bar chunks, 1000 overlap
sigs = {}                                                  # global bar -> (side, r_lo, r_hi), first-seen wins
c0 = 0
while c0 < n:
    c1 = min(n, c0 + CH); S = A[c0:c1]
    for s in WS.detect(S, AL.detect(S), entry_absorbr=ENTRY_ABSORBR, cond_or=COND_OR, require_def_fade=DEF_FADE):
        gi = int(s["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (s["side"], float(s["r_lo"]), float(s["r_hi"]))
    if c1 >= n:
        break
    c0 += STEP
order = sorted(sigs)
print("signals=%d" % len(order), flush=True)

res = []                                                   # (year, side, r, outcome)
ou = -1
for gi in order:
    if gi <= ou or gi + 1 >= n:
        continue
    side, r_lo, r_hi = sigs[gi]; entry = C[gi]
    if entry <= 0:
        continue
    if side == "long":
        sl = r_lo * (1.0 - SL_PAD); risk = entry - sl; sgn = 1.0
        if risk <= 0:
            continue
        tp = entry + RR * risk
    else:
        sl = r_hi * (1.0 + SL_PAD); risk = sl - entry; sgn = -1.0
        if risk <= 0:
            continue
        tp = entry - RR * risk
    out = None; xi = min(n - 1, gi + HORIZON)
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hit_sl = (L[k] <= sl) if side == "long" else (H[k] >= sl)
        hit_tp = (H[k] >= tp) if side == "long" else (L[k] <= tp)
        if hit_sl:
            out = "L"; xi = k; break
        if hit_tp:
            out = "W"; xi = k; break
    exitp = (sl if out == "L" else (tp if out == "W" else C[xi]))
    res.append((int(YR[gi]), side, sgn * (exitp - entry) / entry - FEE, out if out else "T")); ou = xi


def rep(tag, R):
    if not R:
        print("  %-7s n=0" % tag, flush=True); return
    N = len(R); w = sum(1 for x in R if x[3] == "W"); l = sum(1 for x in R if x[3] == "L"); t = sum(1 for x in R if x[3] == "T")
    net = sum(x[2] for x in R) * 100
    bal = 1.0; pk = 1.0; mdd = 0.0
    for x in R:
        bal *= (1 + x[2]); pk = max(pk, bal); mdd = min(mdd, bal / pk - 1)
    print("  %-7s n=%-4d win=%.1f%% (W%d/L%d/timeout%d)  net=%+.1f%%  exp=%+.3f%%  comp=%+.1f%%  maxDD=%.1f%%"
          % (tag, N, 100 * w / max(1, w + l), w, l, t, net, net / N, (bal - 1) * 100, mdd * 100), flush=True)


print("\nWALL STRATEGY  5m  cond=%s  entry=%s%s  SL=radar±0.1%%  TP=1:1.2  (barrier, non-overlap, 0.04%% RT):"
      % ("(1)OR(2)" if COND_OR else "(1)AND(2)", "EG/PA+A<-1" if ENTRY_ABSORBR else "EG/PA",
         " +defTapeFade" if DEF_FADE else ""), flush=True)
for tag, yf in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
    rep(tag, [x for x in res if (yf is None or x[0] == yf)])
print("  --- by side (both yr) ---", flush=True)
rep("long", [x for x in res if x[1] == "long"])
rep("short", [x for x in res if x[1] == "short"])
