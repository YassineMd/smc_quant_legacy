"""1h MM/Skew colour-alignment strategy — equity backtest.

ENTRY (per the operator's colour rule):
  LONG  when the bucket's Mov.Magnitude, Skew, and MM*Skew are ALL green, i.e.
        bull candle (close>open)  AND  skew>0 (volume mass HIGH)  AND  close in the TOP tail (close near high).
  SHORT when all three are red, i.e.
        bear candle (close<open)  AND  skew<0 (volume mass LOW)   AND  close in the BOTTOM tail (close near low).
  (MM green == bull; both Skew green and MM*Skew green reduce to skew>0 since MM>=0. "close in top/bottom
   tail" = close-position within the H-L range >= cp_thr for long / <= 1-cp_thr for short.)

EXIT:
  entry at the signal candle's CLOSE (market).
  SL  = 0.1% BELOW the low (long) / ABOVE the high (short).
  TP  = 1:0.5 -> TP distance = 0.5 * SL distance.
  first of {SL, TP} touched on later bars' high/low wins (SL-first when a bar spans both = pessimistic;
  TP-first reported as the optimistic bound).

SIZING:  balance $200k; each trade's margin = 10% of balance, x10 leverage -> NOTIONAL = balance (compounding,
  one position at a time, flat-to-flat). Leverage x10 with a ~1% stop never reaches liquidation, so it only
  sets notional. P&L per trade = notional * signed_return( - fees ).

Mature 1h region only (target_vol >= 100k). CAUSAL: all features known at the signal candle's close.
Run:  python study/mm_skew_strategy.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app.footprint_panel import profile_skewness

MATURE_TV = 100000.0
BAL0 = 200000.0
POS_FRAC = 0.10
LEV = 10.0
SL_BUF = 0.001          # 0.1% beyond the high/low
RR = 0.5
CP_THR = 2.0 / 3.0      # "top/bottom tail": close in the top (long) / bottom (short) third of the range
FEE_RT = 0.0008         # realistic round-trip taker on notional (0.04%/side) — for the NET column


def mov_mag(o, h, l, c):
    ref = l if c > o else (h if c < o else o)
    return (((c * 100.0 / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def load():
    _, raws, _ = load_archive("1h")
    B = []
    for r in raws:
        o = r.get("open_price"); h = r.get("high"); l = r.get("low"); c = r.get("close_price")
        tv = r.get("target_vol", 0.0) or 0.0
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        B.append(dict(o=o, h=h, l=l, c=c, tv=tv, sk=profile_skewness(r.get("levels")),
                      up=(c > o), dn=(c < o)))
    first = next((i for i in range(len(B)) if B[i]["tv"] >= MATURE_TV
                  and statistics.median([b["tv"] for b in B[i:i + 30]]) >= MATURE_TV), len(B))
    return B[first:]


def signal(b, cp_thr):
    """+1 long / -1 short / 0 none."""
    if b["sk"] is None:
        return 0
    rng = b["h"] - b["l"]
    if rng <= 0:
        return 0
    cp = (b["c"] - b["l"]) / rng          # close position in the range: 1=at high, 0=at low
    if b["up"] and b["sk"] > 0 and cp >= cp_thr:
        return +1
    if b["dn"] and b["sk"] < 0 and cp <= (1.0 - cp_thr):
        return -1
    return 0


def simulate(B, i, side, both):
    """Return (outcome, signed_return_frac, close_bar_index)."""
    entry = B[i]["c"]
    if side > 0:
        sl = B[i]["l"] * (1.0 - SL_BUF); sl_dist = entry - sl; tp = entry + RR * sl_dist
    else:
        sl = B[i]["h"] * (1.0 + SL_BUF); sl_dist = sl - entry; tp = entry - RR * sl_dist
    if sl_dist <= 0:
        return None
    slf = sl_dist / entry
    for j in range(i + 1, len(B)):
        hi = B[j]["h"]; lo = B[j]["l"]
        if side > 0:
            htp = hi >= tp; hsl = lo <= sl
        else:
            htp = lo <= tp; hsl = hi >= sl
        if htp and hsl:
            win = (both == "tp"); return ("TP" if win else "SL", (RR if win else -1.0) * slf, j)
        if htp:
            return ("TP", RR * slf, j)
        if hsl:
            return ("SL", -1.0 * slf, j)
    return ("OPEN", (B[-1]["c"] - entry) / entry * side, len(B) - 1)


def all_signals(B, cp_thr, both):
    out = []
    for i in range(len(B) - 1):
        s = signal(B[i], cp_thr)
        if s == 0:
            continue
        res = simulate(B, i, s, both)
        if res:
            out.append((res[0], res[1], s, res[2] - i))
    return out


def equity(B, cp_thr, both, fee_rt):
    """One position at a time, flat-to-flat, notional = current balance (10% x 10x)."""
    bal = BAL0; peak = bal; maxdd = 0.0; i = 0; n = wins = 0
    while i < len(B) - 1:
        s = signal(B[i], cp_thr)
        if s == 0:
            i += 1; continue
        res = simulate(B, i, s, both)
        if res is None:
            i += 1; continue
        outcome, retf, jclose = res
        notional = POS_FRAC * bal * LEV
        bal += notional * retf - notional * fee_rt
        n += 1; wins += (1 if outcome == "TP" else 0)
        peak = max(peak, bal); maxdd = max(maxdd, (peak - bal) / peak if peak > 0 else 0.0)
        i = jclose + 1
    return bal, n, wins, maxdd


def report_pool(B, cp_thr, both):
    sig = all_signals(B, cp_thr, both)
    if not sig:
        print("  no signals"); return
    tp = sum(1 for o, *_ in sig if o[0] == "T"); sl = sum(1 for o, *_ in sig if o[0] == "S")
    op = sum(1 for o, *_ in sig if o[0] == "O")
    lng = sum(1 for o, rf, s, h in sig if s > 0); sht = len(sig) - lng
    resolved = tp + sl
    win = 100.0 * tp / resolved if resolved else float("nan")
    slf = statistics.mean(abs(rf) for o, rf, s, h in sig if o != "TP" or True) if sig else 0
    # avg SL distance in %: from the losers/winners' retf (win=0.5*slf, loss=slf) -> back out slf
    slfs = [abs(rf) / (RR if o == "TP" else 1.0) for o, rf, s, h in sig if o in ("TP", "SL")]
    avgsl = 100.0 * statistics.mean(slfs) if slfs else 0.0
    hold = statistics.median(h for *_, h in sig)
    p = binom_p(tp, resolved, 2.0 / 3.0)
    print(f"    n={len(sig)} (L{lng}/S{sht})  TP={tp} SL={sl} OPEN={op}  win={win:.1f}%  "
          f"avgSL={avgsl:.2f}%  medHold={hold:.0f}bar  p(vs66.7%)={p:.3f}")


def binom_p(k, n, p0):
    if n < 10:
        return float("nan")
    se = math.sqrt(p0 * (1 - p0) / n); z = (k / n - p0) / se if se > 0 else 0.0
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def main():
    B = load()
    span = (B[-1].get("tv") is not None)
    print(f"mature 1h bars: {len(B)}   ('all-green'=bull&skew>0&close-top-tail / 'all-red'=bear&skew<0&close-bottom-tail)")
    print(f"sizing: balance ${BAL0:,.0f}, margin 10% x10 lev -> notional = balance (compounding, 1 position at a time)\n")

    print("=" * 96)
    print(f"SIGNAL POOL  (close-tail threshold cp>={CP_THR:.2f})   [break-even win @1:0.5 = 66.7%]")
    print("=" * 96)
    print("  SL-first (pessimistic):"); report_pool(B, CP_THR, "sl")
    print("  TP-first (optimistic): "); report_pool(B, CP_THR, "tp")

    print("\n" + "=" * 96)
    print("EQUITY  (one position at a time, flat-to-flat)")
    print("=" * 96)
    for both, blab in (("sl", "SL-first"), ("tp", "TP-first")):
        for fee, flab in ((0.0, "GROSS"), (FEE_RT, f"NET(fee {FEE_RT*100:.2f}%rt)")):
            bal, n, wins, dd = equity(B, CP_THR, both, fee)
            ret = (bal / BAL0 - 1.0) * 100.0
            wr = 100.0 * wins / n if n else float("nan")
            print(f"  {blab:8s} {flab:16s}: trades={n:<3} win={wr:4.1f}%  final=${bal:,.0f}  "
                  f"return={ret:+.1f}%  maxDD={dd*100:.1f}%")

    print("\n" + "=" * 96)
    print("CLOSE-TAIL THRESHOLD SENSITIVITY  (SL-first, GROSS equity)")
    print("=" * 96)
    for thr in (0.50, 0.60, 0.667, 0.75, 0.85):
        sig = all_signals(B, thr, "sl"); res = sum(1 for o, *_ in sig if o[0] in ("T", "S"))
        w = sum(1 for o, *_ in sig if o[0] == "T")
        bal, n, wins, dd = equity(B, thr, "sl", 0.0)
        wr = 100.0 * w / res if res else float("nan")
        print(f"  cp>={thr:.2f}: signals={len(sig):<4} win={wr:4.1f}%  trades={n:<3} "
              f"return={(bal/BAL0-1)*100:+.1f}%  maxDD={dd*100:.1f}%")

    print("\n" + "=" * 96)
    print("SPLIT STABILITY  (halves, SL-first, GROSS)")
    print("=" * 96)
    mid = len(B) // 2
    for lbl, sub in (("1st half", B[:mid]), ("2nd half", B[mid:])):
        bal, n, wins, dd = equity(sub, CP_THR, "sl", 0.0)
        wr = 100.0 * wins / n if n else float("nan")
        print(f"  {lbl}: trades={n:<3} win={wr:4.1f}%  return={(bal/BAL0-1)*100:+.1f}%  maxDD={dd*100:.1f}%")


if __name__ == "__main__":
    main()
