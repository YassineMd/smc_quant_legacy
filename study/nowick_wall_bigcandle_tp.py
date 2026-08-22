"""FOLLOW-UP to nowick_wall_winloss: the ONLY winner/loser separator is candle SIZE (bigger no-wick candle -> higher
continuation win%), a pure momentum effect (all order-flow stats flat). At RR 0.5 even the biggest tercile stays
net-negative. IF the effect is momentum, the right monetization is a BIGGER target, not 0.5R. So: restrict to BIG
no-wick candles (top range tercile, per substrate) and sweep the TP ratio. SL = full candle length (unchanged). Reported
pooled IS(2025)/OOS(2026) + prop-MC. If no (size-cut, TP) cell is net-positive in BOTH years, the size edge is not
monetizable and the No-Wick Wall is a confirmed no-trade. python study/nowick_wall_bigcandle_tp.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks

FEE, SLIP, WICK_TOL, HOLD = 0.0004, 0.0003, 0.001, 48
SUBS = [("study/clock_archive", tf) for tf in ("15m", "30m", "1h")] + \
       [("study/recon_archive", tf) for tf in ("15m", "30m", "1h")]
TPRS = [0.5, 1.0, 1.5, 2.0, 3.0]
CACHE = {}


def load(root, tf):
    if (root, tf) in CACHE:
        return CACHE[(root, tf)]
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    CACHE[(root, tf)] = (O, C, Hi, Lo, ST, n)
    return CACHE[(root, tf)]


def sig_rngpct(root, tf):
    """range% of every no-wick signal bar (for the per-substrate size tercile cut)."""
    O, C, Hi, Lo, ST, n = load(root, tf); out = []
    for i in range(1, n - 1):
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            continue
        if (C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng) or (C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng):
            out.append(rng / C[i] * 100.0)
    return np.array(out)


def trades(root, tf, tpr, rng_min):
    """no-wick momentum entry at close, TP=tpr*range, SL=full range; only signals with range% >= rng_min."""
    O, C, Hi, Lo, ST, n = load(root, tf); rows = []; i = 1
    while i < n - 1:
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            i += 1; continue
        side = 0
        if C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng:
            side = 1
        elif C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng:
            side = -1
        if side == 0:
            i += 1; continue
        entry = C[i]; big = (rng / entry * 100.0) >= rng_min
        sl = entry - side * rng; tp = entry + side * tpr * rng; sld = rng / entry
        net = None; rj = i
        for j in range(i + 1, min(i + 1 + HOLD, n)):
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            rj = j
            if adverse:
                net = side * (sl - entry) / entry - FEE - 2 * SLIP; break
            if favor:
                net = side * (tp - entry) / entry - FEE - SLIP; break
        if net is None:
            net = side * (C[rj] - entry) / entry - FEE - 2 * SLIP
        if big:                                                      # non-overlap regardless, but only keep big-candle trades
            rows.append((ST[i], datetime.fromtimestamp(ST[i], tz=timezone.utc).year, net, sld))
        i = rj + 1
    return rows


def stat(tr):
    if not tr:
        return "n=0"
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-4d win%4.1f%% exp%+.3f%% R%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def main():
    print("NO-WICK WALL — BIG-candle (top range tercile) momentum, TP sweep | SL=full candle | pooled clk+bkt 15m/30m/1h\n", flush=True)
    # per-substrate top-tercile range% cutoff
    cut = {}
    for root, tf in SUBS:
        rp = sig_rngpct(root, tf); cut[(root, tf)] = np.quantile(rp, 2 / 3) if len(rp) else 0.0
    for cutname, qfrac in (("ALL sizes", 0.0), ("BIG=top-tercile", 2 / 3), ("HUGE=top-decile", 0.9)):
        print("== size cut: %s ==" % cutname, flush=True)
        for tpr in TPRS:
            pool = []
            for root, tf in SUBS:
                rp = sig_rngpct(root, tf); rmin = np.quantile(rp, qfrac) if (qfrac > 0 and len(rp)) else 0.0
                pool += trades(root, tf, tpr, rmin)
            m = mc(day_blocks([(t[0], t[2] / t[3]) for t in pool])[0]) if pool else dict(p=0)
            print("  TP=%.1fx | ALL %s | IS %s | OOS %s | prop %.1f%%"
                  % (tpr, stat(pool), stat([t for t in pool if t[1] == 2025]), stat([t for t in pool if t[1] == 2026]), m["p"]), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
