"""Does the B/S tape + DELTA ACCELERATION (da1, da2) predict the NEXT 1h bar?

Per bar (all from the daemon/recon fields, exact formulas from app/da2_reversion_detect.py + mm_skew_subbucket.py):
  vol     = buy_vol + sell_vol  (== curr_vol)
  delta   = buy_vol - sell_vol
  da1     = delta / vol                              signed aggression LEVEL (1st-order; == normalized tape imbalance)
  da2     = (delta - 2*delta_h1) / vol               intra-bar delta ACCELERATION, (dH2-dH1)/vol  (code's delta_accel_2)
            delta_h1 = running net delta at the 50%-VOLUME mark.
  s       = sign(close-open)                         this candle's direction
  da1_dir = da1 * s   da2_dir = da2 * s              directionalized: >0 = aggression aligned-with / accelerating-INTO the move

Next bar: ndir = sign(next open->close); continuation = ndir==s. Tradeable return = enter this close, exit next close.
All framings: disjoint bands, exact-binomial two-sided p vs the relevant baseline, 2025/2026 split, net of 0.08% fee.

Run: python study/tape_accel_nextbar.py [tf]
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008


def binom_p(k, n, p):
    if n <= 0:
        return float("nan")
    k = int(round(k))
    if n <= 400:
        pv = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    else:
        mu = n * p; sd = math.sqrt(n * p * (1 - p)); pv = 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))
    return min(pv, 1 - pv) * 2                                 # two-sided


def build(tf):
    _, rows, _ = load_archive(tf, root=RECON)
    recs = []
    for i in range(len(rows) - 1):
        b = rows[i]; o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
        bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0)
        dh1 = b.get("delta_h1"); vol = bv + sv
        on = float(rows[i + 1].get("open_price", 0) or 0); cn = float(rows[i + 1].get("close_price", 0) or 0)
        if o <= 0 or c <= 0 or cn <= 0 or on <= 0 or c == o or vol <= 0 or dh1 is None:
            continue
        s = 1 if c > o else -1; delta = bv - sv
        da1 = delta / vol; da2 = (delta - 2 * float(dh1)) / vol
        recs.append(dict(s=s, bs=bv / vol, da1=da1, da2=da2, da1d=da1 * s, da2d=da2 * s,
                         ndir=1 if cn > on else (-1 if cn < on else 0), nup=int(cn > on),
                         cont=int((1 if cn > on else (-1 if cn < on else 0)) == s),
                         ret_c=(cn - c) / c * s, ret_L=(cn - c) / c,
                         yr=dt.datetime.utcfromtimestamp(float(b["start_time"])).year))
    return recs


def _a(recs, k):
    return np.array([r[k] for r in recs], float)


def deciles(recs, vkey, hkey, rkey, base, title, hlab):
    if not recs:
        print("  (no rows)"); return
    arr = [recs[i] for i in np.argsort(_a(recs, vkey), kind="mergesort")]
    print("\n%s   baseline %s=%.1f%%  (n=%d)" % (title, hlab, base * 100, len(recs)))
    print("  %-4s %15s %6s %8s %9s %9s   %-9s %-9s %6s" % ("dec", "band", "n", hlab, "ret%", "net%", "2025", "2026", "p"))
    for j, ch in enumerate(np.array_split(arr, 10)):
        if len(ch) == 0:
            continue
        vv = _a(ch, vkey); hh = _a(ch, hkey); rr = _a(ch, rkey)
        c25 = [r for r in ch if r["yr"] == 2025]; c26 = [r for r in ch if r["yr"] == 2026]
        print("  %-4d %6.3f-%6.3f %6d %7.1f%% %+8.4f%% %+8.4f%%   %-9s %-9s %6.3f" %
              (j + 1, vv.min(), vv.max(), len(ch), hh.mean() * 100, rr.mean() * 100, rr.mean() * 100 - FEE * 100,
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c25]) * 100, len(c25))) if c25 else "-",
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c26]) * 100, len(c26))) if c26 else "-",
               binom_p(hh.sum(), len(ch), base)))


def buckets(recs, name, groups, base):
    print("\n%s   (continuation baseline %.1f%%)" % (name, base * 100))
    for lab, sel in groups:
        g = [r for r in recs if sel(r)]
        if not g:
            print("  %-26s n=0" % lab); continue
        cont = np.mean([r["cont"] for r in g]); ret = np.mean([r["ret_c"] for r in g]) * 100
        c25 = [r for r in g if r["yr"] == 2025]; c26 = [r for r in g if r["yr"] == 2026]
        print("  %-26s n=%5d  cont %5.1f%%  ret %+.4f%%  net %+.4f%%  25:%s 26:%s  p=%.3f" %
              (lab, len(g), cont * 100, ret, ret - FEE * 100,
               ("%.0f%%" % (np.mean([r["cont"] for r in c25]) * 100)) if c25 else "-",
               ("%.0f%%" % (np.mean([r["cont"] for r in c26]) * 100)) if c26 else "-",
               binom_p(sum(r["cont"] for r in g), len(g), base)))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    recs = build(tf)
    bull = [r for r in recs if r["s"] == 1]; bear = [r for r in recs if r["s"] == -1]
    b_up = np.mean([r["nup"] for r in recs]); b_cb = np.mean([r["cont"] for r in bull]); b_cs = np.mean([r["cont"] for r in bear])
    b_all = np.mean([r["cont"] for r in recs])
    print("=" * 110)
    print("TAPE + DELTA-ACCEL -> next-bar edge  |  %s recon, %d bars (delta_h1 present)" % (tf, len(recs)))
    print("  P(next UP)=%.1f%% | all-continue %.1f%% | bull-cont %.1f%% | bear-cont %.1f%% | fee %.2f%%" %
          (b_up * 100, b_all * 100, b_cb * 100, b_cs * 100, FEE * 100))
    print("=" * 110)

    # da1 = aggression LEVEL
    deciles(recs, "da1d", "cont", "ret_c", b_all, "[1] da1_dir (aggression aligned w/ candle) -> P(continue), ALL", "cont%")
    deciles(recs, "da1", "nup", "ret_L", b_up, "[2] da1 raw (signed) -> P(next UP), return of LONG next bar", "nextUP%")
    # da2 = ACCELERATION into the close
    deciles(recs, "da2d", "cont", "ret_c", b_all, "[3] da2_dir (accel INTO the move) -> P(continue), ALL", "cont%")
    deciles(bull, "da2d", "cont", "ret_c", b_cb, "[3b] BULL: da2_dir -> P(continue up)", "cont%")
    deciles(bear, "da2d", "cont", "ret_c", b_cs, "[3c] BEAR: da2_dir -> P(continue down)", "cont%")
    deciles(recs, "da2", "nup", "ret_L", b_up, "[4] da2 raw (signed) -> P(next UP)", "nextUP%")
    # combinations: sustained (da1) + accelerating (da2) aggression
    buckets(recs, "[5] da1_dir x da2_dir quadrants (aggression level x acceleration)", [
        ("both>0 (aligned+accel)", lambda r: r["da1d"] > 0 and r["da2d"] > 0),
        ("da1>0,da2<0 (align+decel)", lambda r: r["da1d"] > 0 and r["da2d"] <= 0),
        ("da1<0,da2>0 (opp+accel)", lambda r: r["da1d"] <= 0 and r["da2d"] > 0),
        ("both<=0 (opp+decel)", lambda r: r["da1d"] <= 0 and r["da2d"] <= 0)], b_all)
    # MMXSKEW-style: top-tercile "accelerate into close" both sides
    q2b = np.percentile([r["da2d"] for r in bull], 66.6); q2s = np.percentile([r["da2d"] for r in bear], 66.6)
    buckets(recs, "[6] top-tercile da2_dir ('accelerate into close') vs rest, per side", [
        ("BULL da2_dir top-tercile", lambda r: r["s"] == 1 and r["da2d"] > q2b),
        ("BULL da2_dir rest", lambda r: r["s"] == 1 and r["da2d"] <= q2b),
        ("BEAR da2_dir top-tercile", lambda r: r["s"] == -1 and r["da2d"] > q2s),
        ("BEAR da2_dir rest", lambda r: r["s"] == -1 and r["da2d"] <= q2s)], b_all)
    # tape + accel: strong buy tape AND accelerating
    q1abs = np.percentile([abs(x["da1"]) for x in recs], 66)
    buckets(recs, "[7] tape x accel: strong aligned tape (|da1|>p66) AND accelerating (da2_dir>0)", [
        ("strong-tape + accel", lambda r: abs(r["da1"]) > q1abs and r["da2d"] > 0),
        ("strong-tape + decel", lambda r: abs(r["da1"]) > q1abs and r["da2d"] <= 0)], b_all)
    print("\nnet%% = mean 1-bar return minus 0.08%% fee. cont% RISING with da1_dir/da2_dir = momentum edge; FALLING = fade/exhaustion.")


if __name__ == "__main__":
    main()
