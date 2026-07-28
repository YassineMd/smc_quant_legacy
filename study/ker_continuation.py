"""Does a bar's KER (Kinetic Efficiency Ratio) predict CONTINUATION of the next candle?

For each 1h recon bar: directional KER = KER_buy on a bull bar / KER_sell on a bear bar (the side that moved price;
the other side's KER is 0 by construction). Then look at the NEXT bar: does it continue (same direction) and what is
the 1-bar continuation return (enter at close, hold 1 bar in the bar's direction, exit next close)?

Bucket bars into DISJOINT KER bands and report, per band: n, P(next bar continues), mean 1-bar continuation return
(gross + net of an 0.08% round-trip fee), split by 2025/2026. A rising continuation rate/return with KER = a
continuation edge; a falling one = a reversal/exhaustion edge. Baselines (unconditional continuation) printed first.

Run: python study/ker_continuation.py [tf]
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import config
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
BANDS = [("0", 0.0, 0.0), ("(0,2e-4]", 0.0, 2e-4), ("(2e-4,6e-4]", 2e-4, 6e-4), ("(6e-4,1.5e-3]", 6e-4, 1.5e-3),
         ("(1.5e-3,7e-3]", 1.5e-3, 7e-3), ("(7e-3,3e-2]", 7e-3, 3e-2), ("(3e-2,hi]", 3e-2, 9998.0),
         ("vacuum(inf)", 9999.0, 9999.0)]


def ker(b):
    o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
    bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0)
    ut = float(b.get("up_ticks", 0) or 0); dtk = float(b.get("dn_ticks", 0) or 0)
    dur = max(1.0, float(b.get("end_time", 0) or 0) - float(b.get("start_time", 0) or 0))
    dp = (c - o) / config.TICK_SIZE; vd = bv - sv
    ob = (bv / ut) if ut > 0 else 0.0; osl = (sv / dtk) if dtk > 0 else 0.0
    vb = bv / dur; vs = sv / dur
    Fb = max(0.0, vd) * vb; Fs = max(0.0, -vd) * vs
    Wb = max(0.0, dp) * ob; Ws = max(0.0, -dp) * osl
    kb = (Wb / Fb) if Fb > 0 else (9999.0 if Wb > 0 else 0.0)
    ks = (Ws / Fs) if Fs > 0 else (9999.0 if Ws > 0 else 0.0)
    return kb, ks


def binom_p(k, n, p):                                     # two-sided-ish: one-sided P(X>=k) under Binom(n,p)
    if n <= 0:
        return float("nan")
    if n <= 400:
        return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    mu = n * p; sd = math.sqrt(n * p * (1 - p))
    return 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))


def band_of(k):
    if k == 9999.0:
        return "vacuum(inf)"
    for name, lo, hi in BANDS[:-1]:
        if name == "0":
            if k == 0.0:
                return "0"
        elif lo < k <= hi:
            return name
    return "(3e-2,hi]" if k > 3e-2 else None


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    _, rows, _ = load_archive(tf, root=RECON)
    recs = []                                             # (dirn, kdir, cont, ret, year)
    for i in range(len(rows) - 1):
        o = float(rows[i].get("open_price", 0) or 0); c = float(rows[i].get("close_price", 0) or 0)
        cn = float(rows[i + 1].get("close_price", 0) or 0)
        if o <= 0 or c <= 0 or cn <= 0 or c == o:
            continue
        s = 1 if c > o else -1                             # this bar's direction (bull/bear)
        on = float(rows[i + 1].get("open_price", 0) or 0)
        ndir = 1 if cn > on else (-1 if cn < on else 0)
        kb, ks = ker(rows[i]); kdir = kb if s > 0 else ks   # KER of the side that moved price
        cont = int(ndir == s)                             # next bar SAME direction = continuation
        ret = (cn - c) / c * s                             # 1-bar continuation bet return
        yr = dt.datetime.utcfromtimestamp(float(rows[i]["start_time"])).year
        recs.append((s, kdir, cont, ret, yr))
    A = np.array([(r[0], r[1], r[2], r[3], r[4]) for r in recs], float)

    def report(side, label):
        sub = A[A[:, 0] == side]
        n = len(sub); base_c = sub[:, 2].mean() * 100; base_r = sub[:, 3].mean() * 100
        print("\n=== %s bars (n=%d) | baseline: continue %.1f%%  mean 1-bar ret %+.4f%% (net %+.4f%%) ==="
              % (label, n, base_c, base_r, base_r - FEE * 100))
        print("  %-14s %6s %8s %10s %10s   %-9s %-9s" % ("KER band", "n", "cont%", "ret%", "net%", "2025 c%", "2026 c%"))
        for name, lo, hi in BANDS:
            if name == "0":
                m = sub[:, 1] == 0.0
            elif name == "vacuum(inf)":
                m = sub[:, 1] == 9999.0
            elif name == "(3e-2,hi]":
                m = (sub[:, 1] > 3e-2) & (sub[:, 1] < 9999.0)
            else:
                m = (sub[:, 1] > lo) & (sub[:, 1] <= hi)
            b = sub[m]; nb = len(b)
            if nb == 0:
                continue
            cpct = b[:, 2].mean() * 100; rpct = b[:, 3].mean() * 100
            k = int(b[:, 2].sum()); p = binom_p(k, nb, sub[:, 2].mean())
            c25 = b[b[:, 4] == 2025]; c26 = b[b[:, 4] == 2026]
            print("  %-14s %6d %7.1f%% %+9.4f%% %+9.4f%%   %-9s %-9s  (p=%.3f)"
                  % (name, nb, cpct, rpct, rpct - FEE * 100,
                     ("%.0f%% n%d" % (c25[:, 2].mean() * 100, len(c25))) if len(c25) else "-",
                     ("%.0f%% n%d" % (c26[:, 2].mean() * 100, len(c26))) if len(c26) else "-", p))

    print("=" * 100)
    print("KER -> next-candle CONTINUATION study  |  %s recon, %d bars  (directional KER: KER_buy on bull / KER_sell on bear)" % (tf, len(A)))
    print("=" * 100)
    report(1, "BULL")
    report(-1, "BEAR")
    print("\nRead: cont%% RISING with KER = continuation edge (easy moves continue); FALLING = reversal/exhaustion edge.")
    print("net%% = mean 1-bar continuation return minus 0.08%% round-trip fee. p vs that side's own baseline continuation.")


if __name__ == "__main__":
    main()
