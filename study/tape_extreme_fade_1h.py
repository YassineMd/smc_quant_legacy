"""REVERSAL/EXHAUSTION at tape extremes (1h recon). Does an EXTREME delta push exhaust and fade?

Signal bar features (exact daemon/recon fields):
  vol=buy+sell ; delta=buy-sell ; da1=delta/vol (aggression LEVEL) ;
  da2=(delta-2*delta_h1)/vol (intra-bar accel) ; s=sign(close-open) ;
  da1d=da1*s , da2d=da2*s (directionalised: >0 = aggression pushing INTO the candle).

EXTREME push = top decile of da1d (and separately da2d): aggression strongly aligned w/ the candle.
Two trade directions tested on those extreme bars, both with a REAL bracket:
  FADE  = enter OPPOSITE the candle at close (exhaustion bet)
  WITH  = enter WITH the candle at close (momentum bet)
Bracket: SL 0.1% beyond the signal bar extreme, TP at RR 1:1 and 1:1.5. 1h-bar walk, SL-first tie-break,
non-overlap taken(). Report win% vs fee-adjusted BE, net%, PF, both sides, 2025/26, exact-binomial p.

Run: python study/tape_extreme_fade_1h.py
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
SL_PAD = 0.001


def binom_p(k, n, p):
    if n <= 0:
        return float("nan")
    k = int(round(k))
    if n <= 500:
        pv = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    else:
        mu = n * p; sd = math.sqrt(n * p * (1 - p)); pv = 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))
    return min(pv, 1 - pv) * 2


def build(tf="1h"):
    _, rows, _ = load_archive(tf, root=RECON)
    R = []
    for i in range(len(rows)):
        b = rows[i]
        o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
        h = float(b.get("high", 0) or 0); l = float(b.get("low", 0) or 0)
        bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0)
        dh1 = b.get("delta_h1"); vol = bv + sv
        if o <= 0 or c <= 0 or h <= 0 or l <= 0 or c == o or vol <= 0 or dh1 is None:
            R.append(None); continue
        s = 1 if c > o else -1; delta = bv - sv
        da1 = delta / vol; da2 = (delta - 2 * float(dh1)) / vol
        R.append(dict(i=i, o=o, c=c, h=h, l=l, s=s, da1d=da1 * s, da2d=da2 * s,
                      yr=dt.datetime.utcfromtimestamp(float(b["start_time"])).year))
    return R, rows


def walk(rows, i, side, sl, tp):
    n = len(rows)
    for j in range(i + 1, n):
        b = rows[j]
        hi = float(b.get("high", 0) or 0); lo = float(b.get("low", 0) or 0)
        if hi <= 0 or lo <= 0:
            continue
        if (lo <= sl) if side > 0 else (hi >= sl):
            return False, j
        if (hi >= tp) if side > 0 else (lo <= tp):
            return True, j
    return False, n - 1


def bracket(entry, side, ext):
    # side = trade direction (+1 long / -1 short); ext = signal bar extreme used for stop
    if side > 0:
        sl = ext * (1 - SL_PAD); dist = (entry - sl) / entry
        return sl, entry * (1 + RR * dist), dist
    sl = ext * (1 + SL_PAD); dist = (sl - entry) / entry
    return sl, entry * (1 - RR * dist), dist


def run_cell(R, rows, mask, mode):
    """mode='fade' -> trade opposite candle; 'with' -> trade with candle. Non-overlap taken()."""
    last = -1; out = []
    for r in R:
        if r is None or not mask[r["i"]]:
            continue
        i = r["i"]
        if i <= last:
            continue
        tside = (-r["s"]) if mode == "fade" else r["s"]
        # stop sits beyond the signal-bar extreme in the ADVERSE direction of the trade
        ext = r["l"] if tside > 0 else r["h"]
        sl, tp, dist = bracket(r["c"], tside, ext)
        win, ej = walk(rows, i, tside, sl, tp); last = ej
        net = (RR * dist if win else -dist) - FEE
        out.append(dict(win=win, net=net, dist=dist, tside=tside, yr=r["yr"]))
    return out


def report(lab, rows):
    n = len(rows)
    if n == 0:
        print("  %-22s n=0" % lab); return
    w = sum(r["win"] for r in rows); winr = w / n
    nets = np.array([r["net"] for r in rows])
    meandist = np.mean([r["dist"] for r in rows])
    be = (meandist + FEE) / (meandist * (1 + RR))      # fee-adjusted BE win rate
    net_mean = nets.mean() * 100
    tot = (np.prod(1 + nets) - 1) * 100
    gg = nets[nets > 0].sum(); ll = -nets[nets < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    r25 = [r for r in rows if r["yr"] == 2025]; r26 = [r for r in rows if r["yr"] == 2026]
    p = binom_p(w, n, be)
    print("  %-22s n=%4d win %5.1f%% (BE %4.1f%%) meanNet %+.4f%% tot %+6.1f%% PF %.2f  25:%s 26:%s  p=%.3f" %
          (lab, n, winr * 100, be * 100, net_mean, tot, pf,
           ("%.0f%%n%d" % (100 * np.mean([r["win"] for r in r25]), len(r25))) if r25 else "-",
           ("%.0f%%n%d" % (100 * np.mean([r["win"] for r in r26]), len(r26))) if r26 else "-", p))


def deciles_ext(R, rows, vkey):
    valid = [r for r in R if r is not None]
    arr = np.array([r[vkey] for r in valid])
    print("  %s deciles (min..max, n): " % vkey, end="")
    qs = np.percentile(arr, np.arange(0, 101, 10))
    print(" ".join("%.3f" % q for q in qs))
    return valid, arr


def cell_report(R, rows, vkey, pct, top=True):
    valid = [r for r in R if r is not None]
    arr = np.array([r[vkey] for r in valid])
    if top:
        thr = np.percentile(arr, 100 - pct); mask_ids = {r["i"] for r in valid if r[vkey] >= thr}
        lab = "%s TOP%d%% (>=%.3f)" % (vkey, pct, thr)
    else:
        thr = np.percentile(arr, pct); mask_ids = {r["i"] for r in valid if r[vkey] <= thr}
        lab = "%s BOT%d%% (<=%.3f)" % (vkey, pct, thr)
    n = len(rows); mask = np.zeros(n, bool)
    for k in mask_ids:
        mask[k] = True
    print("\n== %s   (n_bars=%d) ==" % (lab, len(mask_ids)))
    for mode in ("fade", "with"):
        rr_rows = run_cell(R, rows, mask, mode)
        report("%-4s ALL" % mode, rr_rows)
        report("%-4s BULLsig(->%s)" % (mode, "SHORT" if mode == "fade" else "LONG"),
               [r for r in rr_rows if (r["tside"] < 0) == (mode == "fade")])
        # split by trade side explicitly
    return mask


def main():
    global RR
    R, rows = build("1h")
    valid = [r for r in R if r is not None]
    print("=" * 116)
    print("TAPE-EXTREME FADE vs WITH (1h recon) | %d usable signal bars | fee %.2f%% RT | SL 0.1%% beyond bar extreme"
          % (len(valid), FEE * 100))
    y = [r["yr"] for r in valid]
    print("  years:", {yy: y.count(yy) for yy in sorted(set(y))})
    print("=" * 116)
    for vk in ("da1d", "da2d"):
        deciles_ext(R, rows, vk)
    for RR in (1.0, 1.5):
        print("\n" + "#" * 60 + "  RR = 1:%.1f  " % RR + "#" * 40)
        for vk in ("da1d", "da2d"):
            for pct in (10, 20):
                for top in (True, False):
                    cell_report(R, rows, vk, pct, top=top)


if __name__ == "__main__":
    main()
