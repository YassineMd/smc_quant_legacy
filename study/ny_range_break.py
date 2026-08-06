"""NY-session RANGE-BREAK. Weekdays only. A range forms 2-5pm Morocco (= 13-16 UTC, NY open -> 5pm):
  range HIGH = highest 1h BODY CLOSE in 13-16 UTC ; range LOW = lowest 1h BODY CLOSE.
After 5pm (>=16 UTC, within the NY session <21 UTC) the FIRST candle to CLOSE beyond the range triggers ONE trade:
  close > range_high -> LONG ; close < range_low -> SHORT. Entry = that candle's close.
  SL: LONG -> below the LOWEST wick (min low) of 13-16 UTC ; SHORT -> above the HIGHEST wick (max high). TP = +/-0.5%.
1 trade/day; walk to TP/SL (cap ~2 days). fee 0.08%/rt.
Run: TP=0.5 python study/ny_range_break.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import swing_lvn_detect as SVL

rng = np.random.default_rng(20260806)
ENTRY_TF = os.environ.get("ENTRY_TF", "1h")                  # timeframe of the breakout ENTRY + exit walk (range is always 1h)
FR = L.load_features("1h")                                    # RANGE definition (1h body closes / wicks)
RC = FR["c"]; RO = FR["o"]; RH = FR["h"]; RL = FR["l"]; Rstart = [float(t) for t in FR["start"]]
FE = FR if ENTRY_TF == "1h" else L.load_features(ENTRY_TF)    # entry + walk (may be finer, e.g. 15m)
n = FE["n"]; O = FE["o"]; C = FE["c"]; H = FE["h"]; Lo = FE["l"]; start = [float(t) for t in FE["start"]]
Abk = FE["A"]                                                 # entry-tf raw buckets (carry the footprint 'levels' for the VA)
FEE = MA.FEE
R0, R1 = 13, 16                                               # range window [13,16) UTC = 2-5pm Morocco
BRK_END = 21                                                  # breakout must trigger before 9pm UTC (NY session close)
TP = float(os.environ.get("TP", "0.5")) / 100.0
TP_RANGE = float(os.environ.get("TP_RANGE", "0"))            # >0 -> TP = this * (high-low of the 2-5pm range), overrides TP
SL_PAD = float(os.environ.get("SL_PAD", "0.1")) / 100.0       # SL sits this far BEYOND the range wick
SL_MID = os.environ.get("SL_MID") == "1"                      # SL_MID=1 -> SL at the MIDDLE of the 2-5pm range (~1:1 RR)
SL_VA = os.environ.get("SL_VA") == "1"                        # SL_VA=1 -> SL at the CAUSAL NY-session VAL(long)/VAH(short) up to entry
MID_ENTRY = os.environ.get("MID_ENTRY") == "1"                # MID_ENTRY=1 -> break sets DIRECTION, enter on the pullback to
#                                                               the MIDPOINT, SL beyond the range extreme, TP = the WHOLE range (2:1)
TRAIL = os.environ.get("TRAIL") == "1"                        # TRAIL=1 -> enter at break, SL at range extreme, then TRAIL the SL
TRAIL_PAD = float(os.environ.get("TRAIL_PAD", "0.1")) / 100.0  # 0.1% behind each 1h candle low(long)/high(short); no fixed TP
CAP_TRAIL = int(os.environ.get("CAP_TRAIL", "150"))           # max buckets a trailed trade can run before marking to close
CAP = int(os.environ.get("CAP", "48" if ENTRY_TF == "1h" else "192"))   # forward buckets to resolve TP/SL (~2 days)


RANGE_BODY = os.environ.get("RANGE_BODY") == "1"             # 1 -> range = highest/lowest BODY edge (max/min of open&close), else CLOSE
WEEKEND = os.environ.get("WEEKEND") == "1"                    # WEEKEND=1 -> keep ONLY Sat/Sun (placebo: no NY session)
BETA = os.environ.get("BETA") == "1"                          # BETA=1 -> short-beta check vs random-timed shorts (same brackets)


def _daymap(starts, count):
    m = defaultdict(list)
    for i in range(count):
        t = datetime.fromtimestamp(float(starts[i]), tz=timezone.utc)
        if (t.weekday() >= 5) != WEEKEND:                     # weekdays by default; WEEKEND=1 -> weekends only
            continue
        m[t.date()].append((t.hour, i))
    return m


days_r = _daymap(Rstart, FR["n"])                             # per-day 1h buckets (range)
days_e = _daymap(start, n)                                    # per-day entry-tf buckets (breakout + walk)


def walk(j, side, entry, sl, tp_price):
    dist = abs(entry - sl) / entry
    tp_ret = abs(tp_price - entry) / entry
    for k in range(j + 1, min(n, j + 1 + CAP)):
        hi = float(H[k]); lo = float(Lo[k])
        sl_hit = (lo <= sl) if side > 0 else (hi >= sl)      # SL adverse-first
        tp_hit = (hi >= tp_price) if side > 0 else (lo <= tp_price)
        if sl_hit:
            return (-dist - FEE), "SL", k, dist
        if tp_hit:
            return (tp_ret - FEE), "TP", k, tp_ret
    ke = min(n - 1, j + CAP)
    return (side * (C[ke] / entry - 1.0) - FEE), "OPEN", ke, tp_ret   # unresolved -> mark to the last close


def trail_walk(j, side, entry, sl):
    """Ride to a per-candle TRAILING stop (0.1% behind each 1h low/high), ratcheting toward price. No fixed TP."""
    for k in range(j + 1, min(n, j + 1 + CAP_TRAIL)):
        hi = float(H[k]); lo = float(Lo[k])
        if (lo <= sl) if side > 0 else (hi >= sl):           # current trailing stop hit -> exit there
            return (side * (sl / entry - 1.0) - FEE), "STOP", k
        ns = lo * (1 - TRAIL_PAD) if side > 0 else hi * (1 + TRAIL_PAD)   # ratchet the stop toward price
        if (ns > sl) if side > 0 else (ns < sl):
            sl = ns
    ke = min(n - 1, j + CAP_TRAIL)
    return (side * (C[ke] / entry - 1.0) - FEE), "OPEN", ke  # still running at the cap -> mark to close


rows = []; short_brk = []                                     # short_brk: (entry_idx, sl_pct, tp_pct) for the beta check
for d, rbks in sorted(days_r.items()):
    rbks.sort(key=lambda b: b[1])
    rng_c = [b for b in rbks if R0 <= b[0] < R1]                          # 2-5pm 1h candles define the range
    if len(rng_c) < 2:
        continue
    idxs = [b[1] for b in rng_c]
    if RANGE_BODY:
        rhi = max(max(RO[k], RC[k]) for k in idxs); rlo = min(min(RO[k], RC[k]) for k in idxs)   # body edges (open|close)
    else:
        rhi = max(RC[k] for k in idxs); rlo = min(RC[k] for k in idxs)     # body-close range (1h)
    whi = max(RH[k] for k in idxs); wlo = min(RL[k] for k in idxs)         # wick extremes (1h, for the stop)
    ebks = sorted(days_e.get(d, []), key=lambda b: b[1])                   # ENTRY-tf candles for the breakout + walk
    brk = [b for b in ebks if R1 <= b[0] < BRK_END]                        # breakout candles (after 5pm, in NY session)
    mid = (whi + wlo) / 2.0; rangeh = whi - wlo                            # range midpoint + full range distance
    bside = 0; bbar = None                                                 # the break sets the DIRECTION
    for _h, j in brk:
        c = C[j]
        if c > rhi:
            bside = +1; bbar = j; break
        if c < rlo:
            bside = -1; bbar = j; break
    if bside == 0:
        continue
    side = bside
    if TRAIL:                                                              # enter at break, trail the SL, no fixed TP
        entry = C[bbar]; sl0 = wlo * (1 - TRAIL_PAD) if side > 0 else whi * (1 + TRAIL_PAD)
        if (side > 0 and sl0 >= entry) or (side < 0 and sl0 <= entry):
            continue
        net, outc, ke = trail_walk(bbar, side, entry, sl0); _rr = 0.0
    elif MID_ENTRY:                                                        # enter on the PULLBACK to the midpoint
        ek = None
        for k in range(bbar + 1, min(n, bbar + 1 + CAP)):
            if (float(Lo[k]) <= mid) if side > 0 else (float(H[k]) >= mid):
                ek = k; break
        if ek is None:
            continue                                                       # never retested the mid -> no trade
        entry = mid; sl = wlo if side > 0 else whi                         # SL beyond the range extreme
        tp_price = mid + side * rangeh                                        # TP = the WHOLE range distance
        if (side > 0 and sl >= entry) or (side < 0 and sl <= entry):
            continue
        net, outc, ke, _ = walk(ek, side, entry, sl, tp_price); _rr = (abs(tp_price - entry) / entry) / (abs(entry - sl) / entry)
    else:                                                                  # breakout entry at the break close
        entry = C[bbar]
        if SL_VA:                                                          # SL at the CAUSAL NY-session VAL/VAH (up to entry)
            prof = {}
            for (_hh, k) in ebks:                                          # aggregate the footprint NY-open(13UTC) -> entry, inclusive
                if k > bbar or _hh < R0:
                    continue
                for ps, vv in (Abk[k].get("levels") or {}).items():
                    try:
                        p = float(ps)
                    except (TypeError, ValueError):
                        continue
                    r = prof.get(p)
                    if r is None:
                        r = {"b": 0.0, "s": 0.0}; prof[p] = r
                    r["b"] += float(vv.get("b", 0.0) or 0.0); r["s"] += float(vv.get("s", 0.0) or 0.0)
            va = None
            if len(prof) >= 3:
                try:
                    va = SVL.va_lines_from_profile(prof)
                except Exception:
                    va = None
            if not va or va.get("val") is None or va.get("vah") is None:
                continue
            sl = float(va["val"]) if side > 0 else float(va["vah"])
        else:
            sl = (mid if SL_MID else (wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)))
        tp_price = (entry + side * (TP_RANGE * rangeh)) if TP_RANGE > 0 else (entry * (1 + side * TP))
        if (side > 0 and sl >= entry) or (side < 0 and sl <= entry):
            continue
        net, outc, ke, _ = walk(bbar, side, entry, sl, tp_price); _rr = (abs(tp_price - entry) / entry) / (abs(entry - sl) / entry)
        if side < 0:
            short_brk.append((bbar, abs(entry - sl) / entry, abs(tp_price - entry) / entry))   # (entry_idx, SL%, TP%)
    rows.append(dict(net=net, side=side, yr=datetime.fromtimestamp(start[bbar], tz=timezone.utc).year,
                     win=net > 0, outc=outc, rr=_rr))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt)); rrm = np.mean([r["rr"] for r in rs])
    print("  %-10s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  RR %.2f  mean %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, rrm, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


from collections import Counter
oc = Counter(r["outc"] for r in rows); K = max(1, len(rows))
print("=" * 104)
_tpdesc = ("TRAIL %.1f%%/candle (no TP)" % (TRAIL_PAD * 100)) if TRAIL else \
    (("TP %.2f x range" % TP_RANGE) if TP_RANGE > 0 else ("TP %.1f%%" % (TP * 100)))
_sldesc = "SL at range extreme" + (" (trailed)" if TRAIL else "")
print("NY RANGE-BREAK | range 13-16UTC body-close, first close-break 16-21UTC, %s, %s | weekdays | n=%d"
      % (_sldesc, _tpdesc, len(rows)))
print("  outcomes: " + " | ".join("%s %d (%.0f%%)" % (kk, vv, 100 * vv / K) for kk, vv in oc.most_common()))
print("=" * 104)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 104)

if BETA and short_brk:
    snt = np.array([r["net"] for r in rows if r["side"] < 0])
    strat_tot = (np.prod(1 + snt) - 1) * 100                  # strategy short compounded net
    pool = np.array([j for _d, bks in days_e.items() for (_h, j) in bks if R0 <= _h < BRK_END and j + CAP < n])
    slps = np.array([b[1] for b in short_brk]); tpps = np.array([b[2] for b in short_brk]); NS = len(short_brk)

    def rshort(idx, slp, tpp):                                # one random-timed SHORT with the given bracket
        e = float(C[idx]); sl = e * (1 + slp); tp = e * (1 - tpp)
        for k in range(idx + 1, min(n, idx + 1 + CAP)):
            hi = float(H[k]); lo = float(Lo[k])
            if hi >= sl:                                       # adverse (up) first
                return -slp - FEE
            if lo <= tp:
                return tpp - FEE
        return -(float(C[min(n - 1, idx + CAP)]) / e - 1.0) - FEE   # unresolved -> mark to close
    R = 3000; rand_tot = np.empty(R)
    for _r in range(R):
        ridx = rng.choice(pool, size=NS, replace=True)
        nets = np.array([rshort(int(ridx[m]), float(slps[m]), float(tpps[m])) for m in range(NS)])
        rand_tot[_r] = (np.prod(1 + nets) - 1) * 100
    p_ge = float((rand_tot >= strat_tot).mean()); drift = (float(C[-1]) / float(C[0]) - 1.0) * 100.0
    print("SHORT-BETA CHECK | %d strategy shorts vs random-timed shorts (SAME brackets), %d resamples" % (NS, R))
    print("  market drift buy&hold %+.1f%% (short tailwind ~%+.1f%%)" % (drift, -drift))
    print("  STRATEGY short compounded net   %+.1f%%" % strat_tot)
    print("  RANDOM-timed short (same brackets)  mean %+.1f%%  5th %+.1f%%  95th %+.1f%%"
          % (rand_tot.mean(), np.percentile(rand_tot, 5), np.percentile(rand_tot, 95)))
    print("  P(random >= strategy) = %.3f  ->  %s" % (p_ge,
          "ALPHA: the breakdown TIMING beats random shorting" if p_ge < 0.05
          else "NOT distinguishable from random-timed shorting = BETA"))
    print("=" * 104)
