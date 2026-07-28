"""The 4 frozen 1h strategies on the 18-month PRE-DAEMON reconstruction (study/recon_archive) — win% and the
$200k account outcome at 10% margin x10 leverage (= 100% of balance notional per trade, compounded), the SAME
account model used for the 15mReasy test. Each strategy = its OWN frozen signal set + exit bracket, non-overlap
taken(). Fee 0.08% RT. win = net>0.

Run: python study/oos_1h_account.py
"""
from __future__ import annotations
import os, sys, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app.footprint_panel import profile_skewness
from app import pivot_detect as PD
from app import mmxskew_detect, da2_reversion_detect, flow_flip_detect, skew_divergence_detect
from study.mm_skew_v11_tf import gate
import study.mm_skew_rr_sweep as RR
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
B0 = 200_000.0                     # starting balance
MARGIN_FRAC = 0.10                 # 10% of balance as margin ...
LEVERAGE = 10.0                    # ... x10 leverage  ->  notional = 100% of balance per trade


def build_from_rows(rows):
    """Mirror mm_skew_v11_tf.build on the reconstructed rows (they already carry delta_h1/price_h1)."""
    A = []
    for r in rows:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r)
        d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["open"] = o; d["close"] = c
        d["tv"] = float(r.get("target_vol", 0.0) or 0.0)
        d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o
        cv = float(r.get("curr_vol", 0.0)) or 1.0
        d["delta"] = (float(r.get("buy_vol", 0.0)) - float(r.get("sell_vol", 0.0))) / cv * 100.0
        A.append(d)
    n = len(A)
    spr = (2.0 * np.asarray(PD.eff_causal_share(A), float) - 1.0) * 100.0
    for i in range(n):
        A[i]["spread"] = float(spr[i])
    floor = 100000.0
    first = next((i for i in range(n) if A[i]["tv"] >= floor
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, n))]) >= floor), n)
    return A, first


def walk(A, i, side, sl, tp):
    n = len(A)
    for j in range(i + 1, n):
        hi = A[j]["h"]; lo = A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (False, j)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return (True, j)
    return (False, n - 1)


def sim_bracket(A):
    def s(sg):
        e = float(sg["entry"]); sl = float(sg["sl"]); tp = float(sg["tp"]); side = sg["side"]
        slf = abs(e - sl) / e; tpf = abs(tp - e) / e
        if slf <= 0:
            return None
        w, ei = walk(A, sg["i"], side, sl, tp)
        return (w, ei, (tpf if w else -slf) - FEE)
    return s


def year(A, i):
    return dt.datetime.utcfromtimestamp(float(A[i]["start_time"])).year


def taken(A, sigs, sim_fn):
    last = -1; rows = []
    for sg in sorted(sigs, key=lambda s: s["i"]):
        if sg["i"] <= last:
            continue
        r = sim_fn(sg)
        if r is None:
            continue
        is_win, ei, net = r; last = ei
        rows.append(dict(win=(net > 0), side=sg["side"], yr=year(A, sg["i"]), net=net))
    return rows


def account(nets):
    """$200k, 10% margin x10 lev -> 100% of the running balance as notional each trade, compounded."""
    bal = B0
    for r in nets:
        notion = MARGIN_FRAC * bal * LEVERAGE          # = bal
        bal += notion * r
        if bal <= 0:
            return 0.0                                 # blown up
    return bal


def report(name, A, sigs, sim_fn):
    rows = taken(A, sigs, sim_fn)
    n = len(rows)
    print("-" * 96)
    if n == 0:
        print("%-34s  taken n=0  (no signals)" % name); return
    nets = [r["net"] for r in rows]
    w = 100.0 * sum(1 for r in rows if r["net"] > 0) / n
    tot = (np.prod([1 + x for x in nets]) - 1) * 100
    end = account(nets); pnl = end - B0
    print("%-34s  n=%4d  win %5.1f%%   net %+6.1f%%   END $%9.0f   P&L $%+9.0f (%+.1f%%)"
          % (name, n, w, tot, end, pnl, pnl / B0 * 100))


def main():
    _, rows, _ = load_archive("1h", root=RECON)
    A, first = build_from_rows(rows)
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    print("=" * 96)
    print("The 1h STRATEGIES on the 18-month reconstruction  |  %d 1h buckets, %.0f days (%s -> %s)"
          % (len(A), span, dt.datetime.utcfromtimestamp(A[0]["start_time"]).strftime("%Y-%m-%d"),
             dt.datetime.utcfromtimestamp(A[-1]["start_time"]).strftime("%Y-%m-%d")))
    print("Account: start $%.0f, %.0f%% margin x%.0f lev = 100%% of balance notional/trade, compounded, fee %.2f%% RT"
          % (B0, MARGIN_FRAC * 100, LEVERAGE, FEE * 100))
    print("=" * 96)

    mmx, funnel = gate(A, first)
    print("MMXSKEW v1.1 funnel base->delta->mom->A2:", funnel)
    for rr in (1.0, 1.5):
        def sim_mmx(sg, _rr=rr):
            res = RR.simulate_rr(A, sg["i"], sg["side"], _rr, "sl")
            return None if res is None else (res[0] == "TP", res[2], res[1] - FEE)
        report("MMXSKEW v1.1  (SL0.1%%ext, RR 1:%.1f)" % rr, A, mmx, sim_mmx)

    sb = sim_bracket(A)
    report("DA2-REVERSION v1.1 (SL0.8%/TP1.0%)", A, da2_reversion_detect.detect(A), sb)
    ff = [s for s in flow_flip_detect.detect(A) if s.get("pass_entry")]
    report("Flow Flip (+pass_entry, struct/TP0.5%)", A, ff, sb)
    skd = [s for s in skew_divergence_detect.detect(A) if s.get("pass_dom") and s.get("pass_climax")]
    report("Skew Divergence (core, SL0.8%/TP0.8%)", A, skd, sb)

    print("-" * 96)
    print("Non-overlap taken(). Structural stops -> variable R:R, so NET (not win%) is the arbiter. Fee 0.08% RT.")
    print("CAVEAT: reconstructed buckets (independent bucketing, OI approximate, liquidations empty). 1h footprint fidelity ~7%.")


if __name__ == "__main__":
    main()
