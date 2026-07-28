"""Does the B/S aggressor TAPE on a candle predict the NEXT bar? (1h recon)

Tape per bar: bv=buy_vol, sv=sell_vol, vol=bv+sv, buy_share=bv/vol, delta=bv-sv, imb=delta/vol (=2*buy_share-1).
This bar's direction s = sign(close-open) (bull/bear). Next bar: ndir = sign(next open->close); a "continuation" =
next candle same direction as this one. Tradeable return = enter at this bar's close, hold one bar, exit next close.

Framings (all disjoint bands, exact-binomial p vs the relevant baseline, split 2025/2026, net of 0.08% round-trip fee):
  [1] ABSOLUTE  buy_share -> P(next candle UP)          + return of going LONG next bar   (does the tape predict raw direction?)
  [2] BULL      buy_share -> P(next continues up)                                          (aggressive buying -> follow-through?)
  [3] BEAR      buy_share -> P(next continues down)                                        (aggressive selling -> follow-through?)
  [4] DIVERGENCE  bull delta<0 / bear delta>0 (tape opposes candle) vs aligned -> cont%    (classic delta-divergence reversal)
  [5] |imb| magnitude bands -> cont%                                                       (one-sided tape -> continuation?)

Run: python study/tape_nextbar.py [tf]
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008


def binom_p(k, n, p):                                     # one-sided P(X>=k) under Binom(n,p); normal approx for big n
    if n <= 0:
        return float("nan")
    k = int(round(k))
    if n <= 400:
        return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    mu = n * p; sd = math.sqrt(n * p * (1 - p))
    return 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))


def build(tf):
    _, rows, _ = load_archive(tf, root=RECON)
    recs = []
    for i in range(len(rows) - 1):
        o = float(rows[i].get("open_price", 0) or 0); c = float(rows[i].get("close_price", 0) or 0)
        bv = float(rows[i].get("buy_vol", 0) or 0); sv = float(rows[i].get("sell_vol", 0) or 0)
        on = float(rows[i + 1].get("open_price", 0) or 0); cn = float(rows[i + 1].get("close_price", 0) or 0)
        vol = bv + sv
        if o <= 0 or c <= 0 or cn <= 0 or on <= 0 or c == o or vol <= 0:
            continue
        s = 1 if c > o else -1
        ndir = 1 if cn > on else (-1 if cn < on else 0)
        bs = bv / vol                                     # buy share in [0,1]
        rec = dict(s=s, bs=bs, imb=2 * bs - 1, delta=bv - sv, ndir=ndir,
                   cont=int(ndir == s), nup=int(cn > on),
                   ret_c=(cn - c) / c * s,                # continuation-bet return (long if bull, short if bear)
                   ret_L=(cn - c) / c,                    # go-LONG-next-bar return
                   yr=dt.datetime.utcfromtimestamp(float(rows[i]["start_time"])).year)
        recs.append(rec)
    return recs


def _p(recs, key):
    return np.array([r[key] for r in recs], float)


def decile_table(recs, val_key, hit_key, ret_key, baseline, title, hitlabel):
    """Sort recs by val_key, split into 10 equal bins, report hit% (vs baseline) + mean ret (gross/net) + 2025/26."""
    if not recs:
        print("  (no rows)"); return
    v = _p(recs, val_key); order = np.argsort(v, kind="mergesort")
    arr = [recs[i] for i in order]
    print("\n%s   baseline %s = %.1f%%  (n=%d)" % (title, hitlabel, baseline * 100, len(recs)))
    print("  %-4s %14s %6s %8s %9s %9s   %-11s %-11s %6s" %
          ("dec", "band", "n", hitlabel, "ret%", "net%", "2025", "2026", "p"))
    for j, chunk in enumerate(np.array_split(arr, 10)):
        n = len(chunk)
        if n == 0:
            continue
        vv = _p(chunk, val_key); hh = _p(chunk, hit_key); rr = _p(chunk, ret_key)
        h = hh.mean(); ret = rr.mean() * 100
        c25 = [r for r in chunk if r["yr"] == 2025]; c26 = [r for r in chunk if r["yr"] == 2026]
        pval = binom_p(hh.sum(), n, baseline)
        pval = min(pval, 1 - pval) * 2 if not math.isnan(pval) else pval        # two-sided
        print("  %-4d %6.3f-%6.3f %6d %7.1f%% %+8.4f%% %+8.4f%%   %-11s %-11s %6.3f" %
              (j + 1, vv.min(), vv.max(), n, h * 100, ret, ret - FEE * 100,
               ("%.0f%% n%d" % (np.mean([r[hit_key] for r in c25]) * 100, len(c25))) if c25 else "-",
               ("%.0f%% n%d" % (np.mean([r[hit_key] for r in c26]) * 100, len(c26))) if c26 else "-", pval))


def two_way(recs, name, cond, cont_base):
    """Split recs into cond-True vs cond-False, report continuation% + net return each."""
    grp = {"tape OPPOSES candle": [r for r in recs if cond(r)],
           "tape ALIGNS candle": [r for r in recs if not cond(r)]}
    print("\n%s   (continuation baseline %.1f%%)" % (name, cont_base * 100))
    for lab, g in grp.items():
        if not g:
            print("  %-22s n=0" % lab); continue
        cont = np.mean([r["cont"] for r in g]); ret = np.mean([r["ret_c"] for r in g]) * 100
        p = binom_p(sum(r["cont"] for r in g), len(g), cont_base); p = min(p, 1 - p) * 2
        c25 = [r for r in g if r["yr"] == 2025]; c26 = [r for r in g if r["yr"] == 2026]
        print("  %-22s n=%5d  cont %5.1f%%  ret %+.4f%%  net %+.4f%%  25:%s 26:%s  p=%.3f" %
              (lab, len(g), cont * 100, ret, ret - FEE * 100,
               ("%.0f%%" % (np.mean([r["cont"] for r in c25]) * 100)) if c25 else "-",
               ("%.0f%%" % (np.mean([r["cont"] for r in c26]) * 100)) if c26 else "-", p))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    recs = build(tf)
    bull = [r for r in recs if r["s"] == 1]; bear = [r for r in recs if r["s"] == -1]
    base_up = np.mean([r["nup"] for r in recs])
    base_cont_bull = np.mean([r["cont"] for r in bull]); base_cont_bear = np.mean([r["cont"] for r in bear])
    print("=" * 108)
    print("B/S TAPE -> next-bar edge  |  %s recon, %d bars  (buy_share = buy_vol/total; imb = 2*bs-1)" % (tf, len(recs)))
    print("  P(next candle UP) baseline = %.1f%% | bull-continue %.1f%% | bear-continue %.1f%% | fee %.2f%%" %
          (base_up * 100, base_cont_bull * 100, base_cont_bear * 100, FEE * 100))
    print("=" * 108)

    # [1] absolute: does buy_share predict the RAW next-bar direction?
    decile_table(recs, "bs", "nup", "ret_L", base_up,
                 "[1] ALL bars: buy_share -> P(next candle UP), return of LONG next bar", "nextUP%")
    # [2]/[3] conditional continuation
    decile_table(bull, "bs", "cont", "ret_c", base_cont_bull,
                 "[2] BULL candles: buy_share -> P(next continues UP)", "cont%")
    decile_table(bear, "bs", "cont", "ret_c", base_cont_bear,
                 "[3] BEAR candles: buy_share -> P(next continues DOWN)", "cont%")
    # [4] delta divergence (tape opposes the candle)
    two_way(bull, "[4a] BULL candles: does a SELLING tape (delta<0) fade the move?",
            lambda r: r["delta"] < 0, base_cont_bull)
    two_way(bear, "[4b] BEAR candles: does a BUYING tape (delta>0) fade the move?",
            lambda r: r["delta"] > 0, base_cont_bear)
    # [5] |imb| magnitude -> continuation (one-sided tape)
    for side, lab, base in ((bull, "[5a] BULL", base_cont_bull), (bear, "[5b] BEAR", base_cont_bear)):
        decile_table(side, "imb", "cont", "ret_c", base,
                     "%s candles: signed-imbalance decile -> P(continue)" % lab, "cont%")
    print("\nnet%% = mean 1-bar return minus 0.08%% fee. cont% RISING with buy_share = tape momentum edge; FALLING = fade edge.")


if __name__ == "__main__":
    main()
