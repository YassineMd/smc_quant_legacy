"""WHAT SEPARATES WINNERS FROM LOSERS for the setup:
  1h absorption-badge candles, ease vw%>=3, absorption R<=-0.8, SWING-aligned, enter candle SIDE, TP 0.3% / SL 0.1%
  beyond the entry-candle extreme [reading B]. For every taken trade compute the Candle-stats-box metrics at the
  ENTRY candle, then rank each stat by how well it separates WINS (hit TP) from LOSSES (hit the wide SL) — AUC +
  Mann-Whitney p + mean(win)/mean(loss). Directional stats are SIDE-ADJUSTED (+ = in the trade's favour).
  ⚠ Descriptive / in-sample / multiple-comparison: hypothesis-generating, NOT a validated filter.
Run: python study/absorb_winloss_1h.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf1m_detect as E, structure, swing_lvn_detect as SW, absorption as ABS, config

F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
delta = F["delta"]; sk = F["sk"]; spread = F["spread"]; movmag = F["movmag"]; climax = F["climax"]; runpos = F["runpos"]
hour = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).hour for t in F["start"]])
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in F["start"]])
TP = 0.003; SL_PAD = 0.001; TICK = config.TICK_SIZE

# swing direction (causal), same as the strategy
Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur

# per-bucket cheap arrays
vw = np.zeros(n); rB = np.zeros(n); body = np.zeros(n); rng = np.zeros(n); uw = np.zeros(n); lw = np.zeros(n)
kerB = np.zeros(n); kerS = np.zeros(n); vel = np.zeros(n); ber = np.zeros(n); ser = np.zeros(n)
for i, b in enumerate(A):
    o = O[i]; c = C[i]; h = Hh[i]; l = Ll[i]
    ut = float(b.get("up_ticks", 0.0) or 0.0); dt = float(b.get("dn_ticks", 0.0) or 0.0); tot = ut + dt
    rB[i] = (ut / tot) if tot > 0 else 0.5
    vw[i] = ((max(ut, dt) / min(ut, dt) - 1.0) * 100.0) if min(ut, dt) > 0 else 0.0
    if o > 0:
        body[i] = abs(c - o) / o * 100; rng[i] = (h - l) / o * 100
        uw[i] = (h - max(o, c)) / o * 100; lw[i] = (min(o, c) - l) / o * 100
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    dur = max(1.0, float(b.get("end_time", 0.0)) - float(b.get("start_time", 0.0)))
    vel[i] = (bv + sv) / dur
    dp = (c - o) / TICK; vd = bv - sv
    ob = bv / ut if ut > 0 else 0.0; osd = sv / dt if dt > 0 else 0.0; vb = bv / dur; vs = sv / dur
    Fb = max(0.0, vd) * vb; Fs = max(0.0, -vd) * vs; Wb = max(0.0, dp) * ob; Ws = max(0.0, -dp) * osd
    kerB[i] = (Wb / Fb) if Fb > 0 else (9999.0 if Wb > 0 else 0.0)
    kerS[i] = (Ws / Fs) if Fs > 0 else (9999.0 if Ws > 0 else 0.0)
    ber[i] = float(b.get("buyer_er", 0.0) or 0.0); ser[i] = float(b.get("seller_er", 0.0) or 0.0)
velr = np.zeros(n)                                             # velocity vs trailing-30 mean (VEL ratio)
for i in range(n):
    w = vel[max(0, i - 30):i]; m = w.mean() if len(w) else 0.0
    velr[i] = (vel[i] / m) if m > 0 else 1.0

# setup signals
marks = E.detect(A, skip_last=True, absorp=list(absA))
sigs = []
for m in marks:
    i = m["i"]
    if vw[i] < 3.0 or not (absA[i] <= -0.8):
        continue
    if swing_dir[i] != m["side"]:
        continue
    sigs.append((i, m["side"]))
sigs.sort()

# take (non-overlap) + label win/loss (reading B)
trades = []; last = -1
for (i, side) in sigs:
    if i <= last:
        continue
    e = C[i]; sl = Ll[i] * (1 - SL_PAD) if side > 0 else Hh[i] * (1 + SL_PAD); tp = e * (1 + TP) if side > 0 else e * (1 - TP)
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        continue
    win, ej = MA.walk(A, i, side, sl, tp, n); last = ej
    trades.append((i, side, bool(win)))


def feats(i, s):
    return {
        "absR": absA[i], "|absR|": abs(absA[i]), "vw%": vw[i], "movmag": movmag[i],
        "body%": body[i], "range%": rng[i], "upperWick%": uw[i], "lowerWick%": lw[i],
        "VELratio": velr[i], "hour": float(hour[i]), "runpos": float(runpos[i]),
        "delta_fav": delta[i] * s, "effagg_fav": spread[i] * s, "skew_fav": sk[i] * s,
        "climax_fav": (climax[i] if s > 0 else 1.0 - climax[i]),
        "tickTravel_fav": (rB[i] if s > 0 else 1.0 - rB[i]) * 100.0,
        "KER_fav": (kerB[i] if s > 0 else kerS[i]), "KER_against": (kerS[i] if s > 0 else kerB[i]),
        "ER_fav": (ber[i] if s > 0 else ser[i]), "ER_against": (ser[i] if s > 0 else ber[i]),
        "wick_fav%": (lw[i] if s > 0 else uw[i]), "wick_against%": (uw[i] if s > 0 else lw[i]),
    }


rows = [(feats(i, s), w) for (i, s, w) in trades]
KEYS = list(rows[0][0].keys()) if rows else []
nw = sum(1 for _, w in rows if w); nl = len(rows) - nw
print("=" * 104)
print("WIN/LOSS DISCRIMINANT | %d trades: %d WIN (%.0f%%) / %d LOSS | reading B (SL beyond candle, TP 0.3%%)"
      % (len(rows), nw, 100.0 * nw / max(1, len(rows)), nl))
print("  side-adjusted stats (+ = in the trade's favour). AUC = P(win's stat > loss's stat); |AUC-.5| = separation.")
print("=" * 104)


def auc_mw(wv, lv):
    wv = np.asarray(wv, float); lv = np.asarray(lv, float)
    na, nb = len(wv), len(lv)
    if na == 0 or nb == 0:
        return 0.5, 1.0
    allv = np.concatenate([wv, lv]); r = allv.argsort().argsort() + 1.0
    Uw = r[:na].sum() - na * (na + 1) / 2.0
    auc = Uw / (na * nb)
    mu = na * nb / 2.0; sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    p = math.erfc(abs((Uw - mu) / sd) / math.sqrt(2.0)) if sd > 0 else 1.0
    return auc, p


res = []
for k in KEYS:
    wv = [f[k] for f, w in rows if w]; lv = [f[k] for f, w in rows if not w]
    auc, p = auc_mw(wv, lv)
    res.append((k, auc, p, np.mean(wv), np.mean(lv)))
res.sort(key=lambda t: -abs(t[1] - 0.5))
print("  %-16s  AUC    |sep|   p        mean(WIN)   mean(LOSS)   -> higher in" % "stat")
for k, auc, p, mw, ml in res:
    star = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else ""))
    print("  %-16s  %.3f  %.3f   %.4f  %+9.3f   %+9.3f   %-5s %s"
          % (k, auc, abs(auc - 0.5), p, mw, ml, ("WIN" if auc > 0.5 else "LOSS"), star))
print("=" * 104)
print("*/**/*** = MW p<.05/.01/.001 (uncorrected; ~%d tests -> expect ~1 false * by chance). In-sample, descriptive." % len(KEYS))
