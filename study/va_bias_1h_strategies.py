"""Two-phase VA-trend bias (filter #4 + 3PM switch) applied to the 1h STRATEGIES — does it separate winners from
losers the way it (mildly) did on 15mReasy?  CAUSAL, honest aligned-vs-counter partition per strategy.

BIAS (Day 3 = trade day; UTC calendar days midnight..23:59):
  PHASE A (00:00->15:00 UTC) = filter #4 : full-day VA of D1 vs D2 (both closed).  D2>D1 both edges -> long ;
                                            D2<D1 -> short ; else both.
  PHASE B (>=15:00 UTC, 3PM) = switch     : full-day VA of D2 vs D3-SO-FAR (causal partial, midnight..entry bar).
                                            D3>D2 -> long ; D3<D2 -> short ; else both.
The daily value areas are built from the SAME 1h buckets the strategy trades (the 1h chart's prev-day VP).

STRATEGIES (each = its OWN frozen signal set + exit bracket; non-overlap taken() = the canonical basis):
  MMXSKEW v1.1  : gate() (parity 239/166/137/48), SL 0.1% beyond extreme, TP = RR x SL  (RR 1.0 and 1.5)
  DA2-REV v1.1  : detect() curr_vol>=100k quiet-tape,  fixed SL 0.8% / TP 1.0%
  Flow Flip     : detect() + pass_entry (wired don't-chase), structural stop / TP 0.5%
  Skew Diverg.  : detect() core (pass_dom & pass_climax), fixed SL 0.8% / TP 0.8%

For each strategy: baseline taken win%, then the HONEST aligned-vs-counter win% for filter#4 and filter#4+3PM,
with a Fisher exact p. A positive impact = aligned > counter (and ideally p shrinking).  Fee 0.08%, win = TP.

Run: python study/va_bias_1h_strategies.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.mm_skew_feature_matrix import va_poc
from study.mm_skew_v11_tf import build, gate
import study.mm_skew_rr_sweep as RR
from app import mmxskew_detect, da2_reversion_detect, flow_flip_detect, skew_divergence_detect
try:
    from scipy.stats import fisher_exact
except Exception:                                              # pragma: no cover
    fisher_exact = None

CUTOFF_HOUR = 15                                               # "3PM" = 15:00 UTC (day = UTC midnight..23:59)


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def daily_va(A):
    """Full-day 70% value area per UTC day + first bucket index per UTC day (from the 1h buckets)."""
    days = {}; first = {}
    for i, b in enumerate(A):
        d = _dtu(b.get("start_time", 0.0) or 0.0).date()
        days.setdefault(d, []).append(b)
        if d not in first:
            first[d] = i
    out = {}
    for d, bs in days.items():
        prof = {}
        for b in bs:
            for pr, v in (b.get("levels") or {}).items():
                try:
                    p = float(pr)
                except (TypeError, ValueError):
                    continue
                prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
        va = va_poc(prof)
        if va:
            out[d] = va
    return out, first


def partial_va(A, i, i0):
    prof = {}
    for k in range(i0, i + 1):
        for pr, v in (A[k].get("levels") or {}).items():
            try:
                p = float(pr)
            except (TypeError, ValueError):
                continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return va_poc(prof)


def _trend(a, b):
    if not a or not b:
        return "both"
    if b["vah"] > a["vah"] and b["val"] > a["val"]:
        return "long"
    if b["vah"] < a["vah"] and b["val"] < a["val"]:
        return "short"
    return "both"


def bias_f4(t, dayva):
    d3 = t.date()
    return _trend(dayva.get(d3 - dt.timedelta(days=2)), dayva.get(d3 - dt.timedelta(days=1)))


def bias_switch(A, i, t, dayva, first):
    if t.hour < CUTOFF_HOUR:
        return bias_f4(t, dayva)
    d3 = t.date()
    return _trend(dayva.get(d3 - dt.timedelta(days=1)), partial_va(A, i, first.get(d3, i)))


def attach_bias(A, sigs, dayva, first):
    for sg in sigs:
        t = _dtu(A[sg["i"]]["start_time"])
        sg["bias4"] = bias_f4(t, dayva)
        sg["biassw"] = bias_switch(A, sg["i"], t, dayva, first)
    return sigs


def _allowed(bias, s):
    return (bias == "long" and s > 0) or (bias == "short" and s < 0)   # bias must be directional to gate


def walk(A, i, side, sl, tp):
    """Fixed-bracket forward walk, adverse(SL)-first on a same-bar straddle. -> (is_win, exit_index)."""
    n = len(A)
    for j in range(i + 1, n):
        hi = A[j]["h"]; lo = A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (False, j)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return (True, j)
    return (False, n - 1)                                       # OPEN at data end -> non-win, blocks nothing after


def taken_rows(A, sigs, sim_fn):
    """Non-overlap taken() sequence. sim_fn(sg) -> (is_win, exit_index) or None. Returns per-trade dicts."""
    last = -1; rows = []
    for sg in sorted(sigs, key=lambda s: s["i"]):
        if sg["i"] <= last:
            continue
        r = sim_fn(sg)
        if r is None:
            continue
        is_win, ei = r; last = ei
        rows.append(dict(win=is_win, side=sg["side"], bias4=sg["bias4"], biassw=sg["biassw"]))
    return rows


def _wr(xs):
    return (100.0 * sum(xs) / len(xs)) if xs else 0.0


def report(name, rows, be):
    n = len(rows)
    w = sum(1 for r in rows if r["win"])
    print("-" * 100)
    print("%s   |   taken n=%d   baseline win %.1f%%  (fee-adj BE %.0f%%)" % (name, n, _wr([r["win"] for r in rows]), be * 100))
    if n == 0:
        print("  (no signals)"); return
    for key, lbl in (("bias4", "filter#4       "), ("biassw", "filter#4 + 3PM ")):
        al = [r["win"] for r in rows if _allowed(r[key], r["side"])]
        ct = [r["win"] for r in rows if r[key] in ("long", "short") and not _allowed(r[key], r["side"])]
        p = None
        if fisher_exact is not None and al and ct:
            p = fisher_exact([[sum(al), len(al) - sum(al)], [sum(ct), len(ct) - sum(ct)]])[1]
        gap = _wr(al) - _wr(ct)
        print("  %s aligned n=%2d win %5.1f%%   counter n=%2d win %5.1f%%   gap %+5.1f   Fisher p=%s"
              % (lbl, len(al), _wr(al), len(ct), _wr(ct), gap, ("%.3f" % p) if p is not None else "n/a"))


def main():
    A, first_mat, floor = build("1h")
    dayva, dfirst = daily_va(A)
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    print("=" * 100)
    print("VA-trend bias (filter#4 + 3PM switch) on the 1h STRATEGIES   |   %d 1h buckets, %.0f days   |   3PM = 15:00 UTC"
          % (len(A), span))
    print("=" * 100)

    # ---- MMXSKEW v1.1 (RR 1.0 and 1.5), via the parity-checked gate + RR.simulate_rr ----
    mmx, _ = gate(A, first_mat)
    attach_bias(A, mmx, dayva, dfirst)
    for rr in (1.0, 1.5):
        def sim_mmx(sg, _rr=rr):
            res = RR.simulate_rr(A, sg["i"], sg["side"], _rr, "sl")
            return None if res is None else (res[0] == "TP", res[2])
        report("MMXSKEW v1.1  (RR 1:%.1f)" % rr, taken_rows(A, mmx, sim_mmx), 1.0 / (1 + rr))

    # ---- fixed-bracket strategies ----
    da2 = attach_bias(A, da2_reversion_detect.detect(A), dayva, dfirst)
    report("DA2-REVERSION v1.1  (SL0.8%/TP1.0%)", taken_rows(A, da2, lambda sg: walk(A, sg["i"], sg["side"], sg["sl"], sg["tp"])),
           0.8 / (0.8 + 1.0))

    ff = [s for s in flow_flip_detect.detect(A) if s.get("pass_entry")]
    attach_bias(A, ff, dayva, dfirst)
    report("Flow Flip  (+pass_entry, struct SL / TP0.5%)", taken_rows(A, ff, lambda sg: walk(A, sg["i"], sg["side"], sg["sl"], sg["tp"])),
           0.5)

    skd = [s for s in skew_divergence_detect.detect(A) if s.get("pass_dom") and s.get("pass_climax")]
    attach_bias(A, skd, dayva, dfirst)
    report("Skew Divergence  (core dom&climax, SL0.8%/TP0.8%)", taken_rows(A, skd, lambda sg: walk(A, sg["i"], sg["side"], sg["sl"], sg["tp"])),
           0.5)

    print("-" * 100)
    print("CAVEAT: aligned>counter = bias helps. Each strategy is sparse -> low power; nothing here is a freeze.")
    print("        Daily VAs from 1h buckets. Causal (D1/D2 closed, D3 partial up to entry). One 34-day regime.")


if __name__ == "__main__":
    main()
