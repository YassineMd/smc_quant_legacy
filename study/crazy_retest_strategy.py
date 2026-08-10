# -*- coding: utf-8 -*-
"""Crazy-Wall star RETEST strategy backtest (15m, both recon years). Trade the rejection direction off a crazy-
absorption event (support star -> LONG, resistance star -> SHORT), wide SL (<=2.5%), tiny TP. Reports the win rate BUT
ALSO the true per-trade expectancy after fees and — the metric that actually decides a prop challenge — the equity
curve's max drawdown vs the profit reached. Non-overlapping (taken). Barrier first-passage, SL checked first on a
tie (conservative). Fee model: maker limit entry+TP (0.02%/side) but taker stop on the SL exit (0.05%)."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW

HORIZON = 192              # first-passage cap (bars) — 48h on 15m
FEE_MK = 0.0002            # maker (limit entry, limit TP)
FEE_TK = 0.0005            # taker (stop SL)
print("loading + detecting ...", flush=True)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = [_f(b.get("close", b.get("close_price"))) for b in A]
H = [_f(b.get("high")) for b in A]
L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)
events = [e for e in CW.detect(A, walls) if e["i"] + 1 < n]
print("bars=%d  star events=%d" % (n, len(events)), flush=True)


def sim(entry_mode, direction, tp, sl):
    """entry_mode: 'now'(star close) | 'retest'(limit at bubble P). direction: 'reject'|'cont'. tp/sl in fractions.
    Returns per-year dict of (n, wins, net_frac_sum, results[list of (yr, r_frac)])."""
    open_until = -1
    res = []
    for e in events:
        i = e["i"]; P = e["price"]; ws = e["wall_side"]
        rej = 1 if ws == "S" else -1                      # rejection dir: support->long / resistance->short
        d = rej if direction == "reject" else -rej
        if entry_mode == "now":
            ei = i; entry = C[i]
        else:                                             # retest: first bar touching P
            ei = None
            for j in range(i + 1, min(n, i + 1 + 24)):
                if (ws == "S" and L[j] <= P) or (ws == "R" and H[j] >= P):
                    ei = j; entry = P; break
            if ei is None:
                continue
        if ei <= open_until or ei + 1 >= n or entry <= 0:
            continue
        tp_px = entry * (1 + d * tp); sl_px = entry * (1 - d * sl)
        outcome = None; exit_i = min(n - 1, ei + HORIZON)
        for k in range(ei + 1, min(n, ei + 1 + HORIZON)):
            hit_sl = (L[k] <= sl_px) if d > 0 else (H[k] >= sl_px)
            hit_tp = (H[k] >= tp_px) if d > 0 else (L[k] <= tp_px)
            if hit_sl:                                    # SL first on a tie (conservative)
                outcome = "L"; exit_i = k; break
            if hit_tp:
                outcome = "W"; exit_i = k; break
        if outcome == "W":
            r = tp - 2 * FEE_MK                           # maker entry + maker TP
        elif outcome == "L":
            r = -sl - FEE_MK - FEE_TK                     # maker entry + taker stop
        else:
            r = d * (C[exit_i] - entry) / entry - 2 * FEE_MK
        res.append((YR[ei], r, outcome))
        open_until = exit_i
    return res


def report(tag, res):
    for ylabel, yf in (("BOTH", None), ("25", 2025), ("26", 2026)):
        r = [x for x in res if (yf is None or x[0] == yf)]
        if not r:
            continue
        N = len(r); w = sum(1 for x in r if x[2] == "W"); net = sum(x[1] for x in r) * 100
        exp = net / N if N else 0
        # equity curve on fixed 10% risk-of-balance-per-trade? -> simpler: cumulative compounding of r on 100% (unit)
        bal = 1.0; peak = 1.0; mdd = 0.0
        for _yr, rr, _o in r:
            bal *= (1 + rr); peak = max(peak, bal); mdd = min(mdd, bal / peak - 1)
        print("   %-26s [%s] n=%4d win=%5.1f%%  net=%+7.2f%%  exp/trade=%+.3f%%  compounded=%+6.1f%%  maxDD=%.1f%%" % (
            tag, ylabel, N, 100 * w / N, net, exp, (bal - 1) * 100, mdd * 100), flush=True)


print("\n=== REJECTION direction (support->long / resistance->short), SL=2.5%, entry at star close ===", flush=True)
for tp in (0.001, 0.002, 0.003, 0.005):
    report("now/reject tp%.1f%%/sl2.5%%" % (tp * 100), sim("now", "reject", tp, 0.025))
print("\n=== REJECTION, entry on the RETEST (limit at bubble), SL=2.5% ===", flush=True)
for tp in (0.001, 0.002, 0.003, 0.005):
    report("retest/reject tp%.1f%%" % (tp * 100), sim("retest", "reject", tp, 0.025))
print("\n=== CONTINUATION direction (the opposite) as a control, entry at star close, SL=2.5% ===", flush=True)
for tp in (0.001, 0.003):
    report("now/cont tp%.1f%%" % (tp * 100), sim("now", "cont", tp, 0.025))
print("\n=== tighter SL variants (rejection, entry now) ===", flush=True)
for sl in (0.010, 0.015):
    for tp in (0.002, 0.005):
        report("now/reject tp%.1f%%/sl%.1f%%" % (tp * 100, sl * 100), sim("now", "reject", tp, sl))
