"""The 4 frozen 1h strategies' ENTRY signals, re-exited with the 15mReasy exit the user set:
   SL = 0.1% BEYOND the entry candle's extreme, capped at 0.9% from entry ; TP = fixed 0.3% ; filled on the 1m.
Entry = the 1h signal bar's close. Non-overlap taken() by exit TIME (can't hold two 100%-notional trades at once).
$200k account at 10% margin x10 lev (= 100% of balance notional/trade, compounded), fee 0.08% RT. win = net>0.

Run: python study/oos_1h_account_newexit.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import mmxskew_detect, da2_reversion_detect, flow_flip_detect, skew_divergence_detect
from study.mm_skew_v11_tf import gate
from study.archive_loader import load_archive
from study.oos_1h_account import build_from_rows, year
from study.r15easy_1m_trail import load_1m_ohlc

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
SL_PAD = 0.001            # stop 0.1% beyond the entry candle's extreme
SL_MAX = 0.009            # ... capped at 0.9% from entry
TP_FIXED = 0.003          # fixed 0.3% target
B0 = 200_000.0
MARGIN_FRAC = 0.10
LEVERAGE = 10.0


def _bracket(entry, side, ext):
    if side > 0:
        dist = min((entry - ext) / entry + SL_PAD, SL_MAX)
        return entry * (1 - dist), entry * (1 + TP_FIXED)
    dist = min((ext - entry) / entry + SL_PAD, SL_MAX)
    return entry * (1 + dist), entry * (1 - TP_FIXED)


def follow_1m(t1, h1, l1, entry, side, ext, start_t):
    """Structural stop / fixed 0.3% TP resolved on the 1m buckets, adverse(SL)-first. -> (net_gross, exit_time)."""
    sl, tp = _bracket(entry, side, ext)
    j = int(np.searchsorted(t1, start_t)); n = len(t1)
    while j < n:
        hi = float(h1[j]); lo = float(l1[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (sl - entry) / entry * side, float(t1[j])
        if (hi >= tp) if side > 0 else (lo <= tp):
            return (tp - entry) / entry * side, float(t1[j])
        j += 1
    mark = float(l1[-1]) if side > 0 else float(h1[-1])
    return (mark - entry) / entry * side, float(t1[-1])


def taken_1m(A, sigs, t1, h1, l1):
    """Uniform exit + non-overlap by exit time. sigs need 'i','side'. Entry = 1h close, ext = that bar's low/high."""
    items = []
    for sg in sigs:
        i = sg["i"]; side = sg["side"]
        entry = float(A[i]["c"]); ext = float(A[i]["l"]) if side > 0 else float(A[i]["h"])
        items.append((float(A[i]["end_time"]), i, side, entry, ext))
    items.sort()
    last_exit = -1.0; rows = []
    for et, i, side, entry, ext in items:
        if et < last_exit:
            continue
        gross, exit_t = follow_1m(t1, h1, l1, entry, side, ext, et)
        last_exit = exit_t
        rows.append(dict(net=gross - FEE, side=side, yr=year(A, i)))
    return rows


def account(nets):
    bal = B0
    for r in nets:
        bal += (MARGIN_FRAC * bal * LEVERAGE) * r           # notional = 100% of running balance
        if bal <= 0:
            return 0.0
    return bal


def report(name, A, sigs, t1, h1, l1):
    rows = taken_1m(A, sigs, t1, h1, l1)
    n = len(rows)
    print("-" * 98)
    if n == 0:
        print("%-34s  taken n=0" % name); return
    nets = [r["net"] for r in rows]
    w = 100.0 * sum(1 for r in rows if r["net"] > 0) / n
    tot = (np.prod([1 + x for x in nets]) - 1) * 100
    end = account(nets); pnl = end - B0
    print("%-34s  n=%4d  win %5.1f%%   net %+6.1f%%   END $%9.0f   P&L $%+9.0f (%+.1f%%)"
          % (name, n, w, tot, end, pnl, pnl / B0 * 100))


def main():
    _, rows, _ = load_archive("1h", root=RECON)
    A, first = build_from_rows(rows)
    print("loading recon 1m ...", flush=True)
    t1, h1, l1 = load_1m_ohlc()
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    print("=" * 98)
    print("1h STRATEGIES re-exited with the 15mReasy exit  |  %d 1h + %dk 1m, %.0f days (%s -> %s)"
          % (len(A), len(t1) // 1000, span, dt.datetime.utcfromtimestamp(A[0]["start_time"]).strftime("%Y-%m-%d"),
             dt.datetime.utcfromtimestamp(A[-1]["start_time"]).strftime("%Y-%m-%d")))
    print("Exit: SL 0.1%% beyond entry-bar extreme (cap 0.9%%) / TP fixed 0.3%% / 1m fill.  "
          "Account $%.0f, 10%%x10 = 100%% notional, fee %.2f%%." % (B0, FEE * 100))
    print("=" * 98)

    mmx, funnel = gate(A, first)
    print("MMXSKEW v1.1 funnel base->delta->mom->A2:", funnel)
    report("MMXSKEW v1.1", A, mmx, t1, h1, l1)
    report("DA2-REVERSION v1.1", A, da2_reversion_detect.detect(A), t1, h1, l1)
    report("Flow Flip (+pass_entry)", A, [s for s in flow_flip_detect.detect(A) if s.get("pass_entry")], t1, h1, l1)
    report("Skew Divergence (core)", A,
           [s for s in skew_divergence_detect.detect(A) if s.get("pass_dom") and s.get("pass_climax")], t1, h1, l1)

    print("-" * 98)
    print("Uniform exit = SL 0.1%% beyond extreme cap 0.9%% / TP 0.3%% / 1m fill. Non-overlap taken() by exit time.")
    print("CAVEAT: reconstructed buckets (independent bucketing, OI approximate, liquidations empty). 1h footprint ~7%.")


if __name__ == "__main__":
    main()
