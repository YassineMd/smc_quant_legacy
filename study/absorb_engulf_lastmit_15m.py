"""ENGULF breakout in the last-mitigation regime direction — 15m (simplified variant, no location filter, no c1 rule).

EXIT: SL 0.1% beyond the previous candle's extreme (c1 LOW long / HIGH short). TP 1:1.2 — bumped to 1:2 when the
  signal is AT a same-side S/R zone (LONG touching a support / SHORT touching a resistance).
BIAS (last mitigation only): last mitigated S/R zone = RESISTANCE -> long-regime ; = SUPPORT -> short-regime ; none -> flat.
GUARDS (S/R indicator): skip if a support zone overlaps a resistance zone; skip a LONG whose c2 touches a resistance
  zone / a SHORT whose c2 touches a support zone.
LONG (SHORT mirrors): bias==long AND c2 = bullish ENGULFING (body engulfs c1) that is non-doji, body > upper wick,
  absorption A<=0.75, close > prev candle HIGH, skew>0.  SHORT: bias==short, bearish engulf, body>lower wick, A<=0.75,
  close < prev candle LOW, skew<0.
No "at support/resistance" location requirement, no candle-1 condition, no VA, no confluence bump. Entry = c2 close.
Account $200k @10x, fee 0.08%.

CLI: python study/absorb_engulf_lastmit_15m.py [tf]
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.engulf_va_sr_1h as E          # ctx (S/R + absA + sk), _nd, _touches_sr, _sr_overlap
import study.mom_absorb_1h as MA           # walk, account, FEE, B0
from app import absorption as ABS          # absorption_halves -> A_h2 (second-half absorption = "r2")

AB2 = 0.75; SL_PAD = 0.001; RR = 1.2; GOLD_A = -2.0; VPIN_THR = 0.14; US_HOUR = 13
import datetime as _dtm
_R2 = {}


def _us(b):
    """US-tier pocket for a normal candle: VPIN>0.14 & OI-opening>0 & UTC hour>=13 (matches app/momentum_detect._us_pocket)."""
    bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0); vol = bv + sv
    if vol <= 0 or abs(bv - sv) / vol <= VPIN_THR:
        return False
    oi = (float(b.get("opL", 0) or 0) + float(b.get("opS", 0) or 0)) - (float(b.get("clL", 0) or 0) + float(b.get("clS", 0) or 0))
    if oi <= 0:
        return False
    st = float(b.get("start_time", 0) or 0)
    return st > 0 and _dtm.datetime.utcfromtimestamp(st).hour >= US_HOUR


def _r2_arr(tf, X):
    """A_h2 (second-half absorption, the 'R h1/h2' HALF, oriented so <0 = easy) per bar, cached."""
    if tf not in _R2:
        A = X["A"]; _R2[tf] = [ABS.absorption_halves(A, i)[1] for i in range(len(A))]
    return _R2[tf]


def lastmit_bias(X):
    """+1 long-regime after a RESISTANCE mitigation, -1 short-regime after a SUPPORT mitigation, 0 before any."""
    n = X["n"]; mits = []
    for x in X["RES"]:
        if x["i1"] is not None:
            mits.append((x["i1"], 1))
    for x in X["SUP"]:
        if x["i1"] is not None:
            mits.append((x["i1"], -1))
    mits.sort()
    bias = [0] * n; state = 0; ei = 0
    for i in range(n):
        while ei < len(mits) and mits[ei][0] <= i:
            state = mits[ei][1]; ei += 1
        bias[i] = state
    return bias


def gen(tf, wick_asym=False, use_skew=True, body_break=False, doji_exc=False, exit_exc=False, opp_guard=True, ab2=AB2, sk_thr=0.0, tier_skew=False, r2_relax=False, heavy=False, heavy_thr=0.3):
    """Independent knobs (all defaults = the SHIPPED live rule, parity-locked):
      wick_asym : LONG lower-wick>upper-wick / SHORT upper-wick>lower-wick (rejection tail).
      use_skew  : require LONG skew>0 / SHORT skew<0.
      body_break: breakout = close beyond prev candle's BODY (True) vs its WICK high/low (False=shipped).
      doji_exc  : allow a NON-engulfing candle when the prev candle is a doji.
      exit_exc  : allow a NON-engulfing candle exiting a same-side S/R zone (long out of support / short out of resistance).
      opp_guard : skip a LONG touching a resistance / a SHORT touching a support (True=shipped)."""
    X = E.ctx(tf); c = X["c"]; h = X["h"]; l = X["l"]; o = X["o"]; n = X["n"]
    absA = X["absA"]; sk = X["sk"]; bias = lastmit_bias(X); sigs = []
    R2 = _r2_arr(tf, X) if (r2_relax or heavy) else None; mmarr = X["F"]["movmag"]
    for i in range(1, n):
        rng = h[i] - l[i]
        if rng <= 0 or not E._nd(X, i) or E._sr_overlap(X, i):
            continue
        if heavy:                                             # HEAVY (absorbed) instead of easy: A>=thr OR (A_h2>0.5 AND A>0)
            r2v = R2[i]
            if not (absA[i] >= heavy_thr or (r2v is not None and r2v > 0.5 and absA[i] > 0)):
                continue
        elif r2_relax:                                        # A<=ab2 OR (A_h2 < -0.5 AND A < 0)
            r2v = R2[i]
            if not (absA[i] <= ab2 or (r2v is not None and r2v < -0.5 and absA[i] < 0)):
                continue
        elif absA[i] > ab2:
            continue
        body = abs(c[i] - o[i]); uw = h[i] - max(o[i], c[i]); lw = min(o[i], c[i]) - l[i]
        pbhi = max(o[i - 1], c[i - 1]); pblo = min(o[i - 1], c[i - 1]); doji1 = not E._nd(X, i - 1)
        # LONG
        if bias[i] == 1 and c[i] > o[i] and body > uw and (not wick_asym or lw > uw):
            brk = (c[i] > pbhi) if body_break else (c[i] > h[i - 1])
            eng = (o[i] <= pblo) or (doji_exc and doji1) or (exit_exc and E._at_sr(X, i, X["SUP"], True) is not None)
            guard = (not opp_guard) or (not E._touches_sr(X, i, X["RES"]))
            if brk and eng and guard:
                conf = bool(E._touches_sr(X, i, X["SUP"])); gold = bool(absA[i] <= GOLD_A)
                skew_ok = ((sk[i] > 0) if (gold or conf) else (sk[i] < 0)) if tier_skew else ((not use_skew) or sk[i] > sk_thr)
                if skew_ok:
                    sigs.append(dict(i=i, side=1, entry=float(c[i]), ext=float(l[i - 1]), sk=float(sk[i]), conf=conf, gold=gold,
                                     mm=float(mmarr[i]), mmp=float(mmarr[i - 1])))
        # SHORT
        elif bias[i] == -1 and c[i] < o[i] and body > lw and (not wick_asym or uw > lw):
            brk = (c[i] < pblo) if body_break else (c[i] < l[i - 1])
            eng = (o[i] >= pbhi) or (doji_exc and doji1) or (exit_exc and E._at_sr(X, i, X["RES"], False) is not None)
            guard = (not opp_guard) or (not E._touches_sr(X, i, X["SUP"]))
            if brk and eng and guard:
                conf = bool(E._touches_sr(X, i, X["RES"])); gold = bool(absA[i] <= GOLD_A)
                skew_ok = ((sk[i] < 0) if (gold or conf) else (sk[i] > 0)) if tier_skew else ((not use_skew) or sk[i] < -sk_thr)
                if skew_ok:
                    sigs.append(dict(i=i, side=-1, entry=float(c[i]), ext=float(h[i - 1]), sk=float(sk[i]), conf=conf, gold=gold,
                                     mm=float(mmarr[i]), mmp=float(mmarr[i - 1])))
    return sigs


def analyze(tf, **kw):
    X = E.ctx(tf); A = X["A"]; n = X["n"]; yr = X["yr"]; last = -1; rows = []
    for sg in gen(tf, **kw):
        i = sg["i"]
        if i <= last:
            continue
        e = sg["entry"]; s = sg["side"]; ext = sg["ext"]
        gold = bool(sg.get("gold")); conf = bool(sg.get("conf")); rr = 2.0 if (gold or conf) else RR
        if s > 0:
            sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = e * (1 + rr * dist)
        else:
            sl = ext * (1 + SL_PAD); dist = (sl - e) / e; tp = e * (1 - rr * dist)
        if dist <= 0:
            continue
        win, ej = MA.walk(A, i, s, sl, tp, n); last = ej
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, yr=int(yr[i]), dist=dist,
                         sk=sg.get("sk"), conf=conf, gold=gold, mm=sg.get("mm"), mmp=sg.get("mmp"),
                         tier=("gold" if gold else ("conf" if conf else ("us" if _us(A[i]) else "normal")))))
    return rows


def stats(rows):
    m = len(rows)
    if m == 0:
        return None
    nets = [r["net"] for r in rows]; w = sum(1 for x in nets if x > 0)
    eq = MA.B0; peak = MA.B0; mdd = 0.0
    for x in nets:
        eq *= 1 + x; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    gg = sum(x for x in nets if x > 0); ll = -sum(x for x in nets if x < 0); pf = (gg / ll) if ll > 0 else float("inf")
    return m, 100 * w / m, (eq / MA.B0 - 1) * 100, pf, 100 * np.mean([r["dist"] for r in rows]), 100 * mdd, eq - MA.B0


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    X = E.ctx(tf); rows = analyze(tf); be = (1 + MA.FEE) / (1 + RR)
    print("=" * 92)
    print("%s ENGULF breakout in last-mitigation regime (no location, no c1)  |  %d buckets, %d S/R levels" % (tf, X["n"], X["nlev"]))
    print("  RR 1:%.1f | SL 0.1%% off c1 | fee %.2f%% | break-even win %.1f%% | acct $%.0f | %d taken" % (RR, MA.FEE * 100, be * 100, MA.B0, len(rows)))
    print("=" * 92)
    print("  %-13s %4s %6s %7s %5s %6s %7s %11s" % ("cohort", "n", "win%", "net%", "PF", "avgSL", "maxDD%", "P&L $"))
    for lab, f in (("ALL", lambda r: True), ("LONG", lambda r: r["side"] > 0), ("SHORT", lambda r: r["side"] < 0),
                   ("LONG 2025", lambda r: r["side"] > 0 and r["yr"] == 2025), ("LONG 2026", lambda r: r["side"] > 0 and r["yr"] == 2026),
                   ("SHORT 2025", lambda r: r["side"] < 0 and r["yr"] == 2025), ("SHORT 2026", lambda r: r["side"] < 0 and r["yr"] == 2026),
                   ("2025 all", lambda r: r["yr"] == 2025), ("2026 all", lambda r: r["yr"] == 2026),
                   ("at-S/R conf 1:2", lambda r: r["conf"]), ("non-conf 1:1.2", lambda r: not r["conf"])):
        st = stats([r for r in rows if f(r)])
        if st is None:
            print("  %-13s   n=0" % lab); continue
        print("  %-13s %4d %5.1f%% %+6.1f%% %5.2f %5.2f%% %6.1f%% %+11.0f" % (lab, st[0], st[1], st[2], st[3], st[4], st[5], st[6]))


if __name__ == "__main__":
    main()
