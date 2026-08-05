"""ABSORPTION-BADGE + EASE-VW% strategy on 1h recon. Weekends INCLUDED (not asked to exclude).
  * candle has an ABSORPTION badge = an app/engulf1m_detect mark (gd/cm/ob/rg tiers), on the 1h buckets.
  * AND ease vw% >= 3%   where vw% = (max(up_ticks,dn_ticks)/min(up_ticks,dn_ticks) - 1)*100  (the stats-box "+% vw"
    in the Ease row = volume-weighted directional-conviction of the candle's tick travel; needs both sides > 0).
  * ENTER on the candle's SIDE (bull badge -> long / bear badge -> short) at the candle CLOSE.
  * TP 0.3%; SL 0.1% beyond the entry-candle extreme [B] (also 0.1% from entry price [A]).  non-overlap; fee 0.08%/rt.
Run: python study/absorb_ease_vw_1h.py [vw_min]   (default 3.0)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf1m_detect as E
from app import structure, swing_lvn_detect as SW

rng = np.random.default_rng(20260804)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in F["start"]])
FEE = MA.FEE; SL_PAD = 0.001
TP_PCT = (float(sys.argv[3]) / 100.0) if len(sys.argv) > 3 else 0.003   # argv[3] = TP in %, default 0.3
VW_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
ABS_MAX = float(sys.argv[2]) if len(sys.argv) > 2 else None   # e.g. -1.0 -> require absorption R (A) <= -1 (easy/momentum)
FADE = os.environ.get("FADE") == "1"                          # FADE=1 -> enter OPPOSITE the candle's side
SWING = os.environ.get("SWING") == "1"                        # SWING=1 -> only take trades WITH the Price&CVD swing leg
BODY_MIN = float(os.environ["BODY_MIN"]) if os.environ.get("BODY_MIN") else None   # min candle body% (winners were bigger)
LEADWICK_MAX = float(os.environ["LEADWICK_MAX"]) if os.environ.get("LEADWICK_MAX") else None   # max LEADING-wick / range
#   leading wick = upper wick (long) / lower wick (short) -> small = closed hard toward the target, no rejection
SWINGA = os.environ.get("SWINGA") == "1"                      # skip if the DEVELOPING swing leg's A>0 or A4>0 (being absorbed)
RETR = os.environ.get("RETR") == "1"                          # if the developing swing leg is a RETRACEMENT, trade OPPOSITE it

# CAUSAL swing direction per bar from the Price&CVD Swings ZigZag: last CONFIRMED pivot (by confirm_bar<=i) -> a high
# means a down-leg (bearish, -1), a low means an up-leg (bullish, +1). thr = the indicator's volatility-adaptive size.
Harr = [float(b.get("high", 0.0) or 0.0) for b in A]
Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))   # single global structural thr (mild param look-ahead only)
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])   # (pbar, price, is_high, confirm_bar)
swing_dir = [0] * n
_pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1                      # confirmed HIGH -> down-leg / LOW -> up-leg
        _pi += 1
    swing_dir[_i] = _cur

# sanity: recon buckets carry up_ticks/dn_ticks?
have_ticks = sum(1 for b in A[:2000] if (b.get("up_ticks") or b.get("dn_ticks")))
print("up/dn_ticks present in %d/2000 sampled buckets" % have_ticks)


def vw_pct(b):
    ut = float(b.get("up_ticks", 0.0) or 0.0); dt = float(b.get("dn_ticks", 0.0) or 0.0)
    mn = min(ut, dt); mx = max(ut, dt)
    return ((mx / mn - 1.0) * 100.0) if mn > 0 else None


marks = E.detect(A, skip_last=True, absorp=list(absA))
sigs = []
for m in marks:
    i = m["i"]; vw = vw_pct(A[i])
    if vw is None or vw < VW_MIN:
        continue
    if ABS_MAX is not None and not (absA[i] <= ABS_MAX):   # absorption R (A) <= threshold, e.g. <= -1
        continue
    side = -m["side"] if FADE else m["side"]               # FADE -> enter opposite the candle
    if (SWING or SWINGA) and swing_dir[i] == 0:            # no confirmed swing yet -> skip (cheap pre-filter)
        continue
    if SWING or SWINGA:                                    # Price&CVD developing swing leg, recomputed CAUSALLY per bar
        _legs = SW.swing_lines(A[:i + 1])
        _dev = next((lg for lg in reversed(_legs) if lg.get("developing")), None)
        if _dev is None:
            continue
        if SWINGA:                                         # skip if the swing itself is being absorbed (A>0 or A4>0)
            _a = _dev.get("A"); _a4 = _dev.get("A4")
            if (_a is not None and _a > 0) or (_a4 is not None and _a4 > 0):
                continue
        if SWING:                                          # trade WITH the swing leg; on a RETRACEMENT leg, OPPOSITE it
            _legdir = 1 if _dev.get("ends_high") else -1
            _eff = -_legdir if (RETR and _dev.get("is_retr")) else _legdir
            if side != _eff:
                continue
    if BODY_MIN is not None and (abs(C[i] - O[i]) / O[i] * 100.0) < BODY_MIN:   # winners were bigger-bodied candles
        continue
    if LEADWICK_MAX is not None:                            # leading wick (toward target) must be small
        _rng = Hh[i] - Ll[i]
        _lead = (Hh[i] - max(O[i], C[i])) if side > 0 else (min(O[i], C[i]) - Ll[i])
        if _rng <= 0 or (_lead / _rng) > LEADWICK_MAX:
            continue
    sigs.append((i, side, int(yr[i]), m["kind"], vw))
sigs.sort()


def run(sl_mode, rows_sigs):
    rows = []; last = -1
    for (i, side, y, kind, vw) in rows_sigs:
        if i <= last:
            continue
        e = C[i]
        sl = (Ll[i] * (1 - SL_PAD) if side > 0 else Hh[i] * (1 + SL_PAD)) if sl_mode == "candle" \
            else (e * (1 - SL_PAD) if side > 0 else e * (1 + SL_PAD))
        tp = e * (1 + TP_PCT) if side > 0 else e * (1 - TP_PCT)
        if (side > 0 and sl >= e) or (side < 0 and sl <= e):
            continue
        win, ej = MA.walk(A, i, side, sl, tp, n); last = ej
        dist = abs(e - sl) / e; tpret = abs(tp - e) / e
        rows.append(dict(net=(tpret if win else -dist) - FEE, side=side, yr=y, kind=kind, win=bool(win), dist=dist, rr=tpret / dist))
    return rows


def rep(label, rows):
    k = len(rows)
    if k == 0:
        print("  %-16s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * sum(r["win"] for r in rows) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf")
    dm = np.mean([r["dist"] for r in rows]) * 100; rrm = np.mean([r["rr"] for r in rows]); bal = MA.account(list(nt))
    print("  %-16s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  RR %.2f  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, dm, rrm, bal, (bal - MA.B0) / MA.B0 * 100))


def boot(rows):
    if not rows:
        return
    nt = np.array([r["net"] for r in rows])
    m = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(m, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else "INCLUDES 0"))


nl = sum(1 for s in sigs if s[1] > 0)
print("=" * 112)
print("ABSORPTION badge + ease-vw%% >= %.0f%%%s | 1h recon | enter %s, TP %.1f%%, SL 0.1%% | %d marks -> %d signals (%dL/%dS)"
      % (VW_MIN, ("" if ABS_MAX is None else " + absR<=%.1f" % ABS_MAX)
         + (" + SWING-aligned" if SWING else ""), ("FADE (opposite candle)" if FADE else "candle SIDE"),
         TP_PCT * 100, len(marks), len(sigs), nl, len(sigs) - nl))
print("=" * 112)
for mode, name in (("candle", "SL = 0.1% beyond entry-candle extreme [B]"), ("entry", "SL = 0.1% from entry price (3:1) [A]")):
    rows = run(mode, sigs)
    print("\n--- %s ---" % name)
    rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
    rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
    boot(rows)
print("\n--- vw%% threshold sensitivity (SL beyond candle [B]) ---")
for vmin in (3, 10, 25, 50, 100):
    ss = [s for s in sigs if s[4] >= vmin]
    rep("vw>=%d%%" % vmin, run("candle", ss))
print("=" * 112)
