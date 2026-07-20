"""Batch winner/loser separation on the MM/Skew strategy — the operator's parameter list.
All features CAUSAL (known at the signal candle's close). Per-side MW (the one real edge so far was
short-only), split-half on survivors. Also: does the 'close on the wick tail' entry rule actually help?

Primary scan at RR 1:1.0 (balanced W/L = best separation power); survivors checked across RRs + halves.
Run:  python study/mm_skew_features.py
"""
from __future__ import annotations
import os, sys, math, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app import region_state as R, config
from app.pivot_detect import _causal_share
from app.footprint_panel import profile_skewness
import study.mm_skew_strategy as S
import study.mm_skew_rr_sweep as RR
import study.mm_skew_winloss as WL

LW = config.LIVE_PANEL_WINDOW


def wick_median_pos(levels, lo, hi):
    if hi <= lo:
        return 0.5
    items = []
    for p, v in (levels or {}).items():
        try:
            items.append((float(p), float(v.get("b", 0.0)) + float(v.get("s", 0.0))))
        except (TypeError, ValueError):
            pass
    items.sort()
    tot = sum(w for _, w in items)
    if tot <= 0:
        return 0.5
    cum = 0.0
    for p, w in items:
        cum += w
        if cum >= tot / 2.0:
            return (p - lo) / (hi - lo)
    return 0.5


def build():
    _, raws, _ = load_archive("1h")
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r); d["open"] = o; d["close"] = c
        d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["tv"] = r.get("target_vol", 0.0) or 0.0
        d["sk"] = profile_skewness(r.get("levels")); d["up"] = c > o; d["dn"] = c < o
        A.append(d)
    n = len(A)
    ab, ar, sval = R.absorption_series(A, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, fval = R.eff_agg_from_absorption(A, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_lk = R.rolling_share(eb, er_, LW)          # centered = LOCKED (repaints, NON-causal)
    e_nl = _causal_share(eb, er_, LW)            # first-print = NON-LOCKED (causal)
    # per-bar attach
    vel = [0.0] * n; rets = [0.0] * n; cvd = 0.0; cur_day = None
    for i in range(n):
        b = A[i]; bv = float(b.get("buy_vol", 0.0)); sv = float(b.get("sell_vol", 0.0))
        cv = float(b.get("curr_vol", 0.0)) or (bv + sv)
        dur = max(1.0, float(b.get("end_time", 0.0)) - float(b.get("start_time", 0.0)))
        day = dt.datetime.utcfromtimestamp(b.get("start_time", 0.0)).date()
        if day != cur_day:
            cvd = 0.0; cur_day = day
        cvd += (bv - sv)
        vel[i] = (bv + sv) / dur
        rets[i] = (b["c"] - A[i - 1]["c"]) / A[i - 1]["c"] if i > 0 and A[i - 1]["c"] > 0 else 0.0
        rng = b["h"] - b["l"]
        b["elapsed"] = dur
        b["vdelta_pct"] = (bv - sv) / cv * 100.0 if cv > 0 else 0.0
        b["oi_delta"] = (float(b.get("opL", 0.0)) + float(b.get("opS", 0.0))) - (float(b.get("clL", 0.0)) + float(b.get("clS", 0.0)))
        b["absorp_bull"] = ab[i]; b["absorp_bear"] = ar[i]; b["absorp_net"] = ab[i] - ar[i]
        b["eff_bull"] = eb[i]; b["eff_bear"] = er_[i]; b["eff_net"] = eb[i] - er_[i]
        b["velocity"] = vel[i]
        b["vel30"] = vel[i] / (sum(vel[max(0, i - 30):i]) / max(1, min(30, i))) if i > 0 else 1.0
        b["day_vol"] = statistics.pstdev(rets[max(0, i - 40):i + 1]) * 100.0 if i >= 2 else 0.0
        b["cvd_day"] = cvd
        b["body_range"] = abs(b["c"] - b["o"]) / rng * 100.0 if rng > 0 else 0.0
        b["body_price"] = abs(b["c"] - b["o"]) / b["o"] * 100.0
        b["close_pos"] = (b["c"] - b["l"]) / rng if rng > 0 else 0.5
        b["wick_med"] = wick_median_pos(b.get("levels"), b["l"], b["h"])
        b["spread_nl"] = (2.0 * e_nl[i] - 1.0) * 100.0
        b["spread_lk"] = (2.0 * e_lk[i] - 1.0) * 100.0
    first = next((i for i in range(n) if A[i]["tv"] >= 100000.0
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, n))]) >= 100000.0), n)
    return A[first:]


FEATURES = ["elapsed", "vdelta_pct", "oi_delta", "absorp_bull", "absorp_bear", "absorp_net",
            "eff_bull", "eff_bear", "eff_net", "velocity", "vel30", "day_vol", "cvd_day",
            "body_range", "body_price", "close_pos", "wick_med", "spread_nl", "spread_lk"]


def collect(M, rr, cp=S.CP_THR):
    out = []
    for i in range(len(M) - 1):
        s = S.signal(M[i], cp)
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            continue
        rec = dict(side=s, win=(res[0] == "TP"), i=i)
        for f in FEATURES:
            rec[f] = M[i][f]
        out.append(rec)
    return out


def scan_side(sigs, side, sn):
    ss = [x for x in sigs if x["side"] == side]
    W = [x for x in ss if x["win"]]; L = [x for x in ss if not x["win"]]
    rows = []
    for f in FEATURES:
        p, _ = WL.mann_whitney([x[f] for x in W], [x[f] for x in L])
        rows.append((p, f, WL.q([x[f] for x in W], .5), WL.q([x[f] for x in L], .5)))
    rows.sort(key=lambda t: t[0])
    print(f"\n  {sn}  ({len(W)}W/{len(L)}L)   feature: WIN med vs LOSE med  (MW p, sorted)")
    for p, f, mw, ml in rows:
        star = " *" if p < 0.05 else ""
        tag = "  [LOCKED=non-causal]" if f == "spread_lk" else ""
        print(f"    {f:12s} W {mw:>10.3f}  L {ml:>10.3f}   p={p:.3f}{star}{tag}")
    return [f for p, f, mw, ml in rows if p < 0.05]


def split_half(M, side, feat, rr=1.0):
    """frozen-median split: subset = the winning-leaning half of `feat`, must beat base both halves."""
    sigs = [x for x in collect(M, rr) if x["side"] == side]
    W = [x[feat] for x in sigs if x["win"]]; L = [x[feat] for x in sigs if not x["win"]]
    hi_better = statistics.median(W) >= statistics.median(L)
    thr = statistics.median([x[feat] for x in sigs]); ss = sorted(sigs, key=lambda z: z["i"]); mid = len(ss) // 2
    out = []
    for sub in (ss[:mid], ss[mid:]):
        keep = [z for z in sub if (z[feat] >= thr if hi_better else z[feat] <= thr)]
        base = 100 * sum(1 for z in sub if z["win"]) / len(sub) if sub else float("nan")
        wr = 100 * sum(1 for z in keep if z["win"]) / len(keep) if keep else float("nan")
        out.append((wr, base, len(keep)))
    return hi_better, out


def main():
    M = build()
    print(f"mature 1h bars: {len(M)}   scan @ RR 1:1.0 (balanced W/L).  17 features x 2 sides.")
    sigs = collect(M, 1.0)
    surv = {}
    for side, sn in ((+1, "LONG"), (-1, "SHORT")):
        for f in scan_side(sigs, side, sn):
            surv.setdefault((side, sn), []).append(f)

    print("\n" + "=" * 92); print("SPLIT-HALF on the p<0.05 survivors (subset must beat base in BOTH halves)"); print("=" * 92)
    if not surv:
        print("  (no feature cleared p<0.05)")
    for (side, sn), fs in surv.items():
        for f in fs:
            hb, out = split_half(M, side, f)
            print(f"  {sn} {f}: {'high' if hb else 'low'}-subset  H1 {out[0][0]:.0f}/{out[0][1]:.0f}(n{out[0][2]})  "
                  f"H2 {out[1][0]:.0f}/{out[1][1]:.0f}(n{out[1][2]})")

    print("\n" + "=" * 92); print("CLOSE-ON-THE-WICK RULE — does requiring close in the tail help? (win% by cp_thr)"); print("=" * 92)
    for rr in (0.7, 1.0, 1.5):
        print(f"  RR 1:{rr}")
        for cp in (0.0, 0.5, 0.667, 0.8, 0.9):
            cc = collect(M, rr, cp)
            for side, sn in ((+1, "LONG "), (-1, "SHORT")):
                ss = [x for x in cc if x["side"] == side]
                if ss:
                    wr = 100 * sum(1 for x in ss if x["win"]) / len(ss)
                    print(f"    cp>={cp:.3f} {sn}: n={len(ss):<3} win={wr:.1f}%", end="  ")
            print()
        print()


if __name__ == "__main__":
    main()
