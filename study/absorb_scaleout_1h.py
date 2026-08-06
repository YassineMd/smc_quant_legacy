"""FULL-STACK setup with a TWO-TARGET SCALE-OUT + break-even trail, 1h recon.
Setup (same as the best full stack): absorption badge (engulf1m) + ease vw%>=3 + absR<=-0.5 + swing-aligned +
developing-swing A<=0 & A4<=0 + RETRACEMENT-flip (retrace leg -> trade opposite). Enter candle side @ close.
EXIT: 50% at TP1 = +0.3%, 50% at TP2 = +1.0%. SL = 0.1% beyond the entry-candle extreme [B]. After TP1 fills,
      TRAIL the runner's stop to BREAK-EVEN = entry +/- 0.1%. Outcomes: SL (full loss) / TP1+BE / TP1+TP2.
fee 0.08%/rt (charged once; a scale-out turns over the same notional). Run: python study/absorb_scaleout_1h.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf1m_detect as E, structure, swing_lvn_detect as SW, region_state as RS, config as CFG

rng = np.random.default_rng(20260805)
SWING_ON = os.environ.get("SWING_OFF") != "1"                 # SWING_OFF=1 -> drop the Price&CVD swing filter (discretionary)
EFFAGG = os.environ.get("EFFAGG") == "1"                      # EFFAGG=1 -> signed eff-agg RISING for long / FALLING for short vs prev candle
EFFAGG_SPREAD = os.environ.get("EFFAGG_SPREAD") == "1"        # +EFFAGG_SPREAD=1 -> use the eff-agg SPREAD (causal buy-share) not VOL
ABS_OR = os.environ.get("ABS_OR") == "1"                      # ABS_OR=1 -> add the heavy branch (absR >= ABS_HI)
ABS_LO = float(os.environ.get("ABS_LO", "-0.5"))             # easy absorption cap: absR <= ABS_LO
ABS_HI = float(os.environ.get("ABS_HI", "1.0"))             # heavy absorption floor: absR >= ABS_HI (only if ABS_OR)
VW_MIN = float(os.environ.get("VW_MIN", "3"))                 # ease vw% floor
TF = os.environ.get("TF", "1h")                               # timeframe to port the stack onto (1h / 15m / 5m ...)
SWING_LB = int(os.environ.get("SWING_LB", "0"))               # >0 -> bound swing_lines lookback to last N bars (speed on big tf)
F = L.load_features(TF)
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in F["start"]])
FEE = MA.FEE; SL_PAD = 0.001; BE = 0.001                              # BE trail = entry +/- 0.1%
TP1 = float(os.environ.get("TP1", "0.3")) / 100.0                     # TP1 %, default 0.3
TP2 = float(os.environ.get("TP2", "1.0")) / 100.0                     # TP2 %, default 1.0
FULL_TP = os.environ.get("FULL_TP") == "1"                            # FULL_TP=1 -> take 100% at TP1 (no runner/scale-out)

Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur

# per-candle SIGNED eff-agg. SPREAD = causal buy-share (2*share-1)*100 [-100..100]; VOL = eff_bull - eff_bear (volume)
if EFFAGG and EFFAGG_SPREAD:
    from app import pivot_detect as _PV
    _sh = _PV.eff_causal_share(A)
    signed_eff = [(2.0 * float(_sh[k]) - 1.0) * 100.0 for k in range(n)]
elif EFFAGG:
    _eb, _es, _ = RS.eff_agg_series(A, 0, n - 1, CFG.ABSORP_VOL_WINDOW, CFG.EFF_AGG_FORCE_WINDOW)
    signed_eff = [float(_eb[k]) - float(_es[k]) for k in range(n)]
else:
    signed_eff = [0.0] * n


def vw_ok(i):
    ut = float(A[i].get("up_ticks", 0.0) or 0.0); dt = float(A[i].get("dn_ticks", 0.0) or 0.0)
    return min(ut, dt) > 0 and (max(ut, dt) / min(ut, dt) - 1.0) * 100.0 >= VW_MIN


SESSION = os.environ.get("SESSION", "").strip().upper()      # NY / LONDON / TOKYO -> that UTC session only + EXCLUDE weekends
_SESS_HRS = {"NY": (13, 21), "LONDON": (8, 13), "TOKYO": (0, 8)}
_start = F["start"]


def sess_ok(i):
    if not SESSION:
        return True
    t = datetime.fromtimestamp(float(_start[i]), tz=timezone.utc)
    if t.weekday() >= 5:                                      # Sat(5)/Sun(6) -> exclude weekend
        return False
    h0, h1 = _SESS_HRS.get(SESSION, (0, 24))
    return h0 <= t.hour < h1


BIAS_FILTER = os.environ.get("BIAS") == "1"                  # BIAS=1 -> take a signal only if it aligns with the swing Bias-badge dir
BIAS_CONF = float(os.environ.get("BIAS_CONF", "0"))          # optional: also require bias confidence >= this (0 = dir only)
SR_FILTER = os.environ.get("SR_FILTER") == "1"               # long only if entry closer to closest SUPPORT than resistance; mirror short
if SR_FILTER:
    from app import support_resistance as _SR
    _srlv = _SR.detect(A); _srK = _SR.SR_PIVOT_K              # causal: a level exists at i once i0+K<=i, until it breaks (i1)
    _sup = [(lv["i0"], lv["i1"], float(lv["price"])) for lv in _srlv if lv["kind"] == "S"]
    _res = [(lv["i0"], lv["i1"], float(lv["price"])) for lv in _srlv if lv["kind"] == "R"]
    print("S/R levels: %d support / %d resistance" % (len(_sup), len(_res)))


def sr_ok(i, side, e):
    """entry e at bar i CLOSER to the nearest ACTIVE support (long) / resistance (short) than the opposite side."""
    ds = min((abs(e - p) for (i0, i1, p) in _sup if i0 + _srK <= i and (i1 is None or i1 > i)), default=None)
    dr = min((abs(e - p) for (i0, i1, p) in _res if i0 + _srK <= i and (i1 is None or i1 > i)), default=None)
    if ds is None or dr is None:
        return False                                          # need both a support and a resistance to compare
    return (ds < dr) if side > 0 else (dr < ds)


ALL_CANDLES = os.environ.get("ALL_CANDLES") == "1"               # ALL_CANDLES=1 -> EVERY candle (ignore the Absorption badge)
GOLD_ONLY = os.environ.get("GOLD_ONLY") == "1"                   # GOLD_ONLY=1 -> only the GOLD-square tier (kind 'gd')
if ALL_CANDLES:
    cand = [(i, (1 if C[i] > O[i] else -1)) for i in range(0, n - 1) if C[i] != O[i]]   # candle side = bull/bear
else:
    marks = E.detect(A, skip_last=True, absorp=list(absA))
    if GOLD_ONLY:
        marks = [m for m in marks if m.get("kind") == "gd"]
    cand = [(m["i"], m["side"]) for m in marks]
print("candidate pool (pre-filter): %d %s" % (len(cand),
      "ALL candles" if ALL_CANDLES else ("GOLD squares" if GOLD_ONLY else "Absorption-badge candles")))
sigs = []
_seen = 0
for i, side in cand:
    _abs_ok = (absA[i] <= ABS_LO) or (ABS_OR and absA[i] >= ABS_HI)   # easy/momentum, + heavy/absorbed if ABS_OR
    if not vw_ok(i) or not _abs_ok or not sess_ok(i):                 # session + weekday gate
        continue
    if EFFAGG and (i < 1 or (signed_eff[i] > signed_eff[i - 1]) != (side > 0)):   # eff-agg RISING(long)/FALLING(short) vs prev
        continue
    _seen += 1
    if _seen % 1000 == 0:
        print("  ...swing-filtering %d" % _seen, file=sys.stderr)
    if SWING_ON:                                                     # Price&CVD swing filter (skip if SWING_OFF=1)
        if swing_dir[i] == 0:
            continue
        _lo = max(0, i - SWING_LB) if SWING_LB > 0 else 0     # bounded lookback: swing structure is local (dev leg + recent)
        legs = SW.swing_lines(A[_lo:i + 1])
        dev = next((lg for lg in reversed(legs) if lg.get("developing")), None)
        if dev is None:
            continue
        a = dev.get("A"); a4 = dev.get("A4")
        if (a is not None and a > 0) or (a4 is not None and a4 > 0):
            continue
        legdir = 1 if dev.get("ends_high") else -1
        eff = -legdir if dev.get("is_retr") else legdir
        if side != eff:
            continue
    if BIAS_FILTER:                                                  # signal must align with the swing Bias-badge dir
        _blo = max(0, i - SWING_LB) if SWING_LB > 0 else 0
        try:
            _bb = SW.bias(A[_blo:i + 1])
        except Exception:
            _bb = None
        _bd = _bb.get("dir") if _bb else None                       # "long" / "short" / None
        _bside = 1 if _bd == "long" else (-1 if _bd == "short" else None)
        if _bside is None or side != _bside or (BIAS_CONF > 0 and float(_bb.get("confidence", 0.0)) < BIAS_CONF):
            continue
    if SR_FILTER and not sr_ok(i, side, C[i]):                       # entry closer to support (long) / resistance (short)
        continue
    sigs.append((i, side, int(yr[i])))
sigs.sort()


def scaleout(i, side):
    """(net, exit_bar, outcome). 50% @ TP1, 50% runner @ TP2 with BE trail after TP1; else full loss at SL."""
    e = C[i]
    sl = Ll[i] * (1 - SL_PAD) if side > 0 else Hh[i] * (1 + SL_PAD)
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        return None
    dist = abs(e - sl) / e
    tp1 = e * (1 + TP1) if side > 0 else e * (1 - TP1)
    tp2 = e * (1 + TP2) if side > 0 else e * (1 - TP2)
    be = e * (1 + BE) if side > 0 else e * (1 - BE)
    tp1_bar = None
    for j in range(i + 1, n):
        hi = float(A[j]["h"]); lo = float(A[j]["l"])
        sl_hit = (lo <= sl) if side > 0 else (hi >= sl)          # SL adverse-first
        t1_hit = (hi >= tp1) if side > 0 else (lo <= tp1)
        if sl_hit:
            return (-dist - FEE), j, "SL"
        if t1_hit:
            tp1_bar = j; break
    if tp1_bar is None:
        return (-dist - FEE), n - 1, "SL"                       # unresolved before TP1 -> treat as full loss
    if FULL_TP:
        return (TP1 - FEE), tp1_bar, "TP1"                      # take 100% at TP1 (no runner)
    runner = BE; outc = "TP1+BE"; ke = tp1_bar
    for k in range(tp1_bar, n):
        hi = float(A[k]["h"]); lo = float(A[k]["l"])
        t2_hit = (hi >= tp2) if side > 0 else (lo <= tp2)
        if k == tp1_bar:                                        # TP1 bar: only a BIG bar also reaching TP2 counts here
            if t2_hit:
                runner = TP2; outc = "TP1+TP2"; ke = k; break
            continue
        be_hit = (lo <= be) if side > 0 else (hi >= be)         # runner: BE-first (conservative)
        if be_hit:
            runner = BE; outc = "TP1+BE"; ke = k; break
        if t2_hit:
            runner = TP2; outc = "TP1+TP2"; ke = k; break
        ke = k
    net = 0.5 * TP1 + 0.5 * runner - FEE
    return net, ke, outc


rows = []; last = -1
for (i, side, y) in sigs:
    if i <= last:
        continue
    r = scaleout(i, side)
    if r is None:
        continue
    net, ej, outc = r; last = ej
    rows.append(dict(net=net, side=side, yr=y, outc=outc, win=net > 0))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-10s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


from collections import Counter
oc = Counter(r["outc"] for r in rows)
print("=" * 100)
print("FULL STACK + SCALE-OUT (50%% TP1 0.3%% / 50%% TP2 1%% + BE trail) | %s recon | n=%d" % (TF, len(rows)))
print("  outcomes: SL %d (%.0f%%) | TP1+BE %d (%.0f%%) | TP1+TP2 %d (%.0f%%)"
      % (oc["SL"], 100 * oc["SL"] / max(1, len(rows)), oc["TP1+BE"], 100 * oc["TP1+BE"] / max(1, len(rows)),
         oc["TP1+TP2"], 100 * oc["TP1+TP2"] / max(1, len(rows))))
print("=" * 100)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
nt = np.array([r["net"] for r in rows])
m = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
lo, hi = np.percentile(m, [2.5, 97.5])
print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
      % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 100)
