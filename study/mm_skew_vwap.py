"""MMXSKEW v1.1 — swap the POC-baseline filter for a DAILY-ANCHORED VWAP (resets each UTC day). Everything
else identical (dir + skew + panel-2 spread ±35 + long delta<15); only the location filter changes:
  POC:  long close > POC-baseline (5% EMA) ;  short close < POC-baseline
  VWAP: long close > VWAP          (daily) ;  short close < VWAP
VWAP = cumulative(typical_price·vol)/cumulative(vol) within the UTC day, typical = (H+L+C)/3, CAUSAL.
Compares win% + equity net per RR, per side. Frozen strategy unchanged — this is a what-if.
Run:  python study/mm_skew_vwap.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

RRS = [0.5, 0.7, 1.0, 1.5]


def build():
    M, span = P.build()      # has spread, base (POC 5% EMA), sk, o/c/h/l, start_time
    cum_pv = cum_v = 0.0; cur = None
    for b in M:
        cv = float(b.get("curr_vol", 0.0)) or 1.0
        b["delta"] = (float(b.get("buy_vol", 0.0)) - float(b.get("sell_vol", 0.0))) / cv * 100.0
        day = dt.datetime.utcfromtimestamp(float(b.get("start_time", 0.0))).date()
        if day != cur:
            cum_pv = cum_v = 0.0; cur = day          # daily reset
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        v = float(b.get("curr_vol", 0.0)) or 0.0
        cum_pv += tp * v; cum_v += v
        b["vwap"] = cum_pv / cum_v if cum_v > 0 else b["c"]
    # close EMAs — continuous (no daily reset). alpha = 2/(N+1).
    for N, key in ((20, "ema20"), (9, "ema9"), (50, "ema50")):
        _a = 2.0 / (N + 1); _prev = None
        for b in M:
            _prev = b["c"] if _prev is None else b["c"] * _a + _prev * (1 - _a)
            b[key] = _prev
    return M, span


def sig(b, mode):
    """mode 'poc' or 'vwap' — v1.1 rule with the chosen location filter."""
    if b["sk"] is None:
        return 0
    ref = {"poc": b["base"], "vwap": b["vwap"], "ema20": b["ema20"], "ema9": b["ema9"], "ema50": b["ema50"]}[mode]
    if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and b["c"] > ref and b["delta"] < 15:
        return 1
    if b["dn"] and b["sk"] < 0 and b["spread"] <= -35 and b["c"] < ref:
        return -1
    return 0


def collect(M, rr, mode):
    out = []
    for i in range(len(M) - 1):
        s = sig(M[i], mode)
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            continue
        out.append(dict(side=s, win=(res[0] == "TP")))
    return out


def equity(M, rr, mode, fee, side_only=0):
    bal = S.BAL0; i = 0; n = w = 0; peak = bal; dd = 0.0
    while i < len(M) - 1:
        s = sig(M[i], mode)
        if s == 0 or (side_only and s != side_only):
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        notl = S.POS_FRAC * bal * S.LEV; bal += notl * res[1] - notl * fee
        n += 1; w += (1 if res[0] == "TP" else 0)
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = res[2] + 1
    return (bal / S.BAL0 - 1) * 100.0, n, (100 * w / n if n else float("nan")), dd * 100.0


def wl(sigs, side=None):
    ss = sigs if side is None else [x for x in sigs if x["side"] == side]
    return (100.0 * sum(1 for x in ss if x["win"]) / len(ss), len(ss)) if ss else (float("nan"), 0)


def main():
    M, span = build()
    print(f"mature 1h bars {len(M)}  span {span:.1f}d   POC-baseline vs DAILY VWAP (UTC), v1.1 otherwise identical\n")
    for mode in ("poc", "vwap"):
        lbl = "POC-baseline (FROZEN)" if mode == "poc" else "DAILY VWAP (what-if)"
        print("=" * 92); print(lbl); print("=" * 92)
        print(f"  {'RR':>5} | {'ALL n/win':>13} | {'LONG n/win':>13} | {'SHORT n/win':>14} | {'net gross/0.08%':>16} {'DD':>5}")
        for rr in RRS:
            s = collect(M, rr, mode)
            a = wl(s); lo = wl(s, 1); sh = wl(s, -1)
            gg, ng, _, dd = equity(M, rr, mode, 0.0); gn, _, _, _ = equity(M, rr, mode, 0.0008)
            print(f"  1:{rr:>3} | {a[1]:>4} {a[0]:>6.1f}% | {lo[1]:>4} {lo[0]:>6.1f}% | {sh[1]:>4} {sh[0]:>6.1f}% | "
                  f"{gg:>+6.1f}%/{gn:>+6.1f}% {dd:>4.0f}%")
        print()
    print("=" * 92); print("DIRECT DELTA (VWAP − POC) on net equity @0.08%"); print("=" * 92)
    for rr in RRS:
        _, _, _, _ = 0, 0, 0, 0
        pn = equity(M, rr, "poc", 0.0008)[0]; vn = equity(M, rr, "vwap", 0.0008)[0]
        pl = equity(M, rr, "poc", 0.0008, 1)[0]; vl = equity(M, rr, "vwap", 0.0008, 1)[0]
        ps = equity(M, rr, "poc", 0.0008, -1)[0]; vs = equity(M, rr, "vwap", 0.0008, -1)[0]
        print(f"  1:{rr}: ALL {pn:+.1f}->{vn:+.1f}% ({vn-pn:+.1f})  | LONG {pl:+.1f}->{vl:+.1f}% ({vl-pl:+.1f})  | "
              f"SHORT {ps:+.1f}->{vs:+.1f}% ({vs-ps:+.1f})")


if __name__ == "__main__":
    main()
