"""DA2-REVERSION v1.0 — CANDIDATE (registered 2026-07-21, forward-test only, NOT live).

A MEAN-REVERSION line, independent of the MMXSKEW family (no skew, no eff-agg spread, no run_pos, no POC).
The whole rule is one condition plus a fixed-percentage bracket:

    SIGNAL (mature 1h volume buckets only, i >= FM.build()'s `first`):
        da2 OPPOSED to the candle's direction
            bearish candle (close<open) AND da2 > 0  ->  LONG   (buying accelerated into a decline = absorbed)
            bullish candle (close>open) AND da2 < 0  ->  SHORT  (selling accelerated into a rally  = absorbed)
        doji (close==open) never fires.
    EXIT: FIXED percentages off the entry (NOT bucket-extreme-derived): stop SL_PCT, target TP_PCT.
    ENTRY: the signal bucket's close.

    da2 = (buy_vol - sell_vol - 2*delta_h1) / curr_vol   — second-half delta acceleration, where delta_h1 is
    the running (buy-sell) at the bucket's 50%-VOLUME mark. The daemon stamps `delta_h1` live; the GCS archive
    mirror does NOT carry it yet (0/3843 rows), so build() reconstructs it from the 1m stream and prefers the
    stored field when present. THE 1m DEPENDENCY IS THE BINDING CONSTRAINT on how far back this can be scored.

WHY 0.8/1.0 AND NOT THE TIGHTER BRACKETS THE IDEA STARTED WITH. Win rate rises MONOTONICALLY as the target
tightens — 49.1% @TP1.2 -> 78.8% @TP0.3, seven cells ordering perfectly — but the break-even bar rises FASTER
(80.0% needed at TP0.3). So the tight-target versions lose while winning 4 of 5. Net expectancy peaks at
TP 1.0% and the whole 0.6-1.2% band is positive; 0.8/1.0 sits mid-plateau rather than on an edge.

⚠ SELECTION CAVEAT, recorded deliberately. SL and TP were chosen by sweeping a 2-D grid on THIS tape (~110
in-sample cells were evaluated across the session that produced this). The permutation p below controls for
WHICH BUCKETS were selected, never for HOW MANY CONFIGURATIONS were tried. In-sample magnitude will regress.
Five other p<0.05 cells from the same session dissolved under split-half; this one did not — that is the only
reason it is registered rather than filed. Forward tape is the sole honest test.

Run: python study/da2_reversion_validate.py
"""
from __future__ import annotations
import os, sys, bisect, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
import study.mm_skew_feature_matrix as FM

SL_PCT = 0.008        # FROZEN: fixed 0.8% stop
TP_PCT = 0.010        # FROZEN: fixed 1.0% target  (RR 1:1.25)
FEE = 0.0008          # taker/taker round trip, fee-in everywhere
SIMS = 20000


def _da2(b, M, mst):
    """Second-half delta acceleration. Prefers the daemon's stored delta_h1; else reconstructs from 1m."""
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    cv = float(b.get("curr_vol", 0.0) or 0.0)
    if cv <= 0:
        return None
    dh = b.get("delta_h1")
    if dh is None:
        a = bisect.bisect_left(mst, float(b.get("start_time", 0.0)))
        z = bisect.bisect_left(mst, float(b.get("end_time", 0.0)))
        subs = M[a:z]
        if len(subs) < 4:
            return None
        vols = [float(s.get("curr_vol", 0.0) or 0.0) for s in subs]
        tot = sum(vols)
        if tot <= 0:
            return None
        cum = 0.0; run = 0.0
        for s, v in zip(subs, vols):
            run += float(s.get("buy_vol", 0.0) or 0.0) - float(s.get("sell_vol", 0.0) or 0.0)
            cum += v
            if cum >= 0.5 * tot:
                dh = run; break
        if dh is None:
            return None
    return (bv - sv - 2.0 * float(dh)) / cv


def build():
    """(bars, sigs). bars = every archive bucket (needed intact for the forward exit scan); sigs = mature
    buckets carrying a valid da2, tagged with the side the rule would take."""
    _, H, _ = load_archive("1h")
    _, M, _ = load_archive("1m")
    mst = [float(x.get("start_time", 0.0)) for x in M]
    _, first, _, _ = FM.build()
    bars = [dict(o=float(b.get("open_price") or 0.0), c=float(b.get("close_price") or 0.0),
                 h=float(b.get("high") or 0.0), l=float(b.get("low") or 0.0),
                 t=float(b.get("start_time", 0.0))) for b in H]
    for x in bars:
        x["ok"] = x["o"] > 0 and x["c"] > 0 and x["h"] > x["l"]
    sigs = []
    for i in range(first, len(H)):
        if not bars[i]["ok"]:
            continue
        da2 = _da2(H[i], M, mst)
        if da2 is None:
            continue
        o, c = bars[i]["o"], bars[i]["c"]
        side = 0
        if c < o and da2 > 0:
            side = 1                                   # bearish candle, buying accelerating -> fade UP
        elif c > o and da2 < 0:
            side = -1                                  # bullish candle, selling accelerating -> fade DOWN
        sigs.append(dict(i=i, side=side, da2=da2, t=bars[i]["t"]))
    return bars, sigs


def GATE(sg):
    """DA2-REVERSION gate: the da2/candle opposition already resolved into a side."""
    return sg["side"] != 0


def taken(bars, sigs, tp=TP_PCT, sl=SL_PCT):
    """One-at-a-time taken trades under the family's DECLARED non-overlap contract (convention A): a signal on
    the bar in which the prior trade exited is SKIPPED. Net as a FRACTION, fee-in. Same-bar TP+SL ambiguity
    resolves to the STOP (conservative). Unresolved trades are DROPPED, never booked as losses."""
    last = -1; out = []
    for sg in sigs:
        i = sg["i"]
        if i <= last or not GATE(sg):
            continue
        s = sg["side"]; e = bars[i]["c"]
        stop = e * (1 - sl) if s > 0 else e * (1 + sl)
        targ = e * (1 + tp) if s > 0 else e * (1 - tp)
        res = None
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            htp = (bars[j]["h"] >= targ) if s > 0 else (bars[j]["l"] <= targ)
            hsl = (bars[j]["l"] <= stop) if s > 0 else (bars[j]["h"] >= stop)
            if htp and hsl:
                res = (False, -sl, j); break
            if htp:
                res = (True, tp, j); break
            if hsl:
                res = (False, -sl, j); break
        if res is None:
            continue
        out.append(dict(side=s, win=res[0], net=res[1] - FEE, t=sg["t"])); last = res[2]
    return sorted(out, key=lambda z: z["t"])


def breakeven(tp=TP_PCT, sl=SL_PCT):
    return 100.0 * (FEE + sl) / (tp + sl)


def _maxdd(path):
    peak = np.maximum.accumulate(path); return np.max((peak - path) / peak)


def main():
    np.random.seed(42)
    bars, sigs = build()
    T = taken(bars, sigs)
    r = np.array([t["net"] for t in T]) * 100.0
    n = len(r); w = sum(1 for t in T if t["win"])
    print("DA2-REVERSION v1.0  (da2 OPPOSED to candle · fixed SL %.1f%% / TP %.1f%%)\n" % (SL_PCT * 100, TP_PCT * 100))
    print("  raw signals with a side: %d   taken after non-overlap: %d" % (sum(1 for s in sigs if s["side"]), n))
    if n == 0:
        return
    print("=" * 88)
    print("  n=%d  win %.1f%%  (BREAK-EVEN %.1f%%)  net/tr %+.4f%%  total %+.1f%%  maxDD %.1f%%"
          % (n, 100.0 * w / n, breakeven(), r.mean(), r.sum(), _maxdd(np.cumprod(1 + r / 100.0)) * 100))
    L = [t for t in T if t["side"] > 0]; S = [t for t in T if t["side"] < 0]
    print("  by side: LONG n=%d %+.4f%%  |  SHORT n=%d %+.4f%%"
          % (len(L), np.mean([t["net"] for t in L]) * 100 if L else 0,
             len(S), np.mean([t["net"] for t in S]) * 100 if S else 0))
    mid = n // 2
    print("  SPLIT-HALF: H1 n=%d %+.4f%% | H2 n=%d %+.4f%%  -> %s"
          % (mid, r[:mid].mean(), n - mid, r[mid:].mean(),
             "BOTH POSITIVE" if r[:mid].mean() > 0 and r[mid:].mean() > 0 else "FAILS"))
    s = np.sort(r)
    print("  drop-best: 1 %+.4f%%  3 %+.4f%%  5 %+.4f%%" % (s[:-1].mean(), s[:-3].mean(), s[:-5].mean()))
    samp = r[np.random.randint(0, n, size=(SIMS, n))]
    means = samp.mean(axis=1)
    print("  MC bootstrap: P(profit)=%.1f%%  95%%CI [%+.4f, %+.4f]%%"
          % (100 * np.mean(means > 0), np.percentile(means, 2.5), np.percentile(means, 97.5)))
    sd = r.std(ddof=1)
    print("  t = %+.2f" % (r.mean() / (sd / math.sqrt(n)) if sd > 0 else 0.0))
    print("\n  TP curve at the frozen SL (context only — the frozen cell is TP %.1f%%):" % (TP_PCT * 100))
    for tp in (0.004, 0.006, 0.008, 0.010, 0.012):
        rr = np.array([t["net"] for t in taken(bars, sigs, tp=tp)]) * 100.0
        if len(rr) == 0:
            continue
        print("    TP %.1f%%  n=%-3d  win %.1f%%  (need %.1f%%)  net %+.4f%%"
              % (tp * 100, len(rr), 100 * np.mean(rr > 0), breakeven(tp=tp), rr.mean()))
    print("\nCAVEAT: SL/TP were grid-selected on this tape (~110 cells that session). Magnitude WILL regress.")
    print("Forward audit: python study/da2_reversion_forward_audit.py")


if __name__ == "__main__":
    main()
