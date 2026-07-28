"""The 4 frozen 1h strategies with their OWN terminal SL/TP, PLUS the two-phase VA-trend bias filter:
  #1 (00:00-15:00 UTC): full-day VP of D1 vs D2. D2>D1 both edges -> long only; D2<D1 -> short only; else both.
  #2 (>=15:00 UTC)    : full-day VP of D2 vs D3-so-far (causal). D3>D2 -> long only; D3<D2 -> short only; else both.
The bias gates each signal's DIRECTION (drop signals whose side the day's bias forbids). Daily VPs from the 1h buckets.
Own exits: MMXSKEW SL0.1%ext/TP=RRxSL ; DA2 0.8/1.0 ; Flow Flip structural/0.5% ; Skew Div 0.8/0.8. 1h-bar fill.
$200k account @ 10% margin x10 lev (= 100% of balance notional/trade, compounded), fee 0.08%. win = net>0.

Run: python study/oos_1h_account_bias.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import da2_reversion_detect, flow_flip_detect, skew_divergence_detect
from study.mm_skew_v11_tf import gate
import study.mm_skew_rr_sweep as RR
from study.archive_loader import load_archive
from study.oos_1h_account import build_from_rows, sim_bracket, year, taken, B0, MARGIN_FRAC, LEVERAGE, FEE
from study.va_bias_1h_strategies import daily_va, bias_switch, _allowed, partial_va

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
PROX = 0.0015             # filter #3 "close to" band: within 0.15% of the reference VAH/VAL


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def _ref_va(A, i, t, dayva, dfirst):
    """Filter #3 reference value area: Day-2 (full, closed) before 15:00 UTC; Day-3 (partial, causal) from 15:00."""
    d3 = t.date()
    if t.hour < 15:
        va = dayva.get(d3 - dt.timedelta(days=1))
    else:
        va = partial_va(A, i, dfirst.get(d3, i))
    return (va["vah"], va["val"]) if va else (None, None)


def bias_filter(A, sigs, dayva, dfirst, prox=False):
    """#1+#2 VA-trend bias gate (direction). prox=True also applies #3: no long within PROX of the reference VAH,
    no short within PROX of the reference VAL (don't chase into the value-area edge)."""
    out = []
    for sg in sigs:
        i = sg["i"]; side = sg["side"]; t = _dtu(A[i]["start_time"])
        b = bias_switch(A, i, t, dayva, dfirst)
        if not (b == "both" or _allowed(b, side)):
            continue
        if prox:
            vah, val = _ref_va(A, i, t, dayva, dfirst)
            entry = float(A[i]["c"]); band = PROX * entry
            if side > 0 and vah is not None and abs(entry - vah) <= band:
                continue                                    # don't buy into the VAH
            if side < 0 and val is not None and abs(entry - val) <= band:
                continue                                    # don't sell into the VAL
        out.append(sg)
    return out


def account(nets):
    bal = B0
    for r in nets:
        bal += (MARGIN_FRAC * bal * LEVERAGE) * r
        if bal <= 0:
            return 0.0
    return bal


def line(label, rows):
    n = len(rows)
    if n == 0:
        print("    %-26s n=0" % label); return
    nets = [r["net"] for r in rows]
    w = 100.0 * sum(1 for r in rows if r["net"] > 0) / n
    tot = (np.prod([1 + x for x in nets]) - 1) * 100
    end = account(nets); pnl = end - B0
    print("    %-26s n=%4d  win %5.1f%%   net %+6.1f%%   END $%9.0f   P&L $%+9.0f (%+.1f%%)"
          % (label, n, w, tot, end, pnl, pnl / B0 * 100))


def run(name, A, sigs, sim_fn, dayva, dfirst):
    print("-" * 100)
    print(name)
    line("raw", taken(A, sigs, sim_fn))
    line("+ bias (#1+#2)", taken(A, bias_filter(A, sigs, dayva, dfirst), sim_fn))
    line("+ bias + no-chase (#1+#2+#3)", taken(A, bias_filter(A, sigs, dayva, dfirst, prox=True), sim_fn))


def main():
    _, rows, _ = load_archive("1h", root=RECON)
    A, first = build_from_rows(rows)
    dayva, dfirst = daily_va(A)
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    print("=" * 100)
    print("The 1h STRATEGIES (own terminal SL/TP) + two-phase VA-bias filter  |  %d 1h buckets, %.0f days (%s -> %s)"
          % (len(A), span, _dtu(A[0]["start_time"]).strftime("%Y-%m-%d"), _dtu(A[-1]["start_time"]).strftime("%Y-%m-%d")))
    print("Account $%.0f, 10%%x10 = 100%% notional/trade compounded, fee %.2f%% RT. 1h-bar fill, non-overlap taken()."
          % (B0, FEE * 100))
    print("=" * 100)

    mmx, funnel = gate(A, first)
    print("MMXSKEW v1.1 funnel base->delta->mom->A2:", funnel)
    for rr in (1.0, 1.5):
        def sim_mmx(sg, _rr=rr):
            res = RR.simulate_rr(A, sg["i"], sg["side"], _rr, "sl")
            return None if res is None else (res[0] == "TP", res[2], res[1] - FEE)
        run("MMXSKEW v1.1  (SL0.1%%ext, RR 1:%.1f)" % rr, A, mmx, sim_mmx, dayva, dfirst)

    sb = sim_bracket(A)
    run("DA2-REVERSION v1.1 (SL0.8%/TP1.0%)", A, da2_reversion_detect.detect(A), sb, dayva, dfirst)
    run("Flow Flip (+pass_entry, struct/TP0.5%)", A,
        [s for s in flow_flip_detect.detect(A) if s.get("pass_entry")], sb, dayva, dfirst)
    run("Skew Divergence (core, SL0.8%/TP0.8%)", A,
        [s for s in skew_divergence_detect.detect(A) if s.get("pass_dom") and s.get("pass_climax")], sb, dayva, dfirst)

    print("-" * 100)
    print("Bias gates DIRECTION (long-only / short-only / both days). Daily VPs from 1h buckets, causal (D3 partial to entry).")
    print("CAVEAT: reconstructed buckets (independent bucketing, OI approximate, liquidations empty). 1h footprint ~7%. NET is the arbiter.")


if __name__ == "__main__":
    main()
