"""DA2-REVERSION v1.1 — CANDIDATE (registered 2026-07-21). v1.0 + a QUIET-TAPE gate. Forward-test only.

v1.0 UNCHANGED and still separately frozen (study/da2_reversion_validate.py). v1.1 runs BESIDE it, not instead
of it: both accumulate forward tape from their own freeze, and the comparison decides whether the extra gate is
real. That is deliberate — the in-sample evidence below is NOT strong enough to justify re-freezing v1.0, whose
forward clock had already started.

    v1.1  =  v1.0 signal  AND  |eff-agg spread| <= SPREAD_MAX

`eff-agg spread` = (2 * pivot_detect.eff_causal_share - 1) * 100, range -100..+100. NON-LOCKED / first-print /
causal. **NEVER use the LOCKED (settled/centered) variant** — it repaints off future data and is exactly what
invalidated the whole pre-V3 PIVOT line (v1 +$234 -> -$89 under causal replay).

WHY A *QUIET* TAPE. Fading a candle only works when that candle had no conviction behind it. A large |spread|
means one side overwhelmingly controlled the aggressor flow — that is a real move, not a failed probe, and
fading it is how you get run over. A near-zero spread means the candle moved without the flow backing it, which
is the same "no conviction" story da2 detects INSIDE the bucket, confirmed independently ACROSS the bucket.

THREE DEAD ENDS — measured, do NOT retry (all monotone, so these are trustworthy negatives):
  1. spread LEVEL aligned with the TRADE (long spr>=T / short spr<=-T): monotonically destructive.
     T=0 -0.054 · T=20 -0.155 · T=50 -0.257 · T=65 -0.700 (10% win, n=10).
  2. spread MOMENTUM aligned with the trade, ds = spr[i]-spr[i-1] (long ds>=T / short ds<=-T): same shape.
     T=0 -0.030 · T=5 -0.188 · T=20 -0.326 (30.8% win). Every cell fails split-half.
     The signals naturally lean the OTHER way — LONG median ds -10.5, SHORT median ds +11.3.
  3. Inverting (2) is no better than baseline (+0.137 vs +0.147) and fails split-half.
  Reading: this edge lives where the tape is NOT confirming. Any form of eff-agg agreement is harmful; only the
  ABSENCE of strong agreement helps.

⚠ THE HONEST CASE AGAINST v1.1, recorded deliberately:
  * It is a RE-CHAINING, not a subtraction. 128 -> 99 trades is 45 out / 25 in, because filtering a signal frees
    later ones through the non-overlap chain. The 45 removed average -0.0000% at 49% win — dead neutral, NOT
    losers. So "it cuts the bad trades" is false; it trades a different set.
  * Permutation vs a random same-size subset gave p=0.021, which controls for subset SIZE but not for the
    reshuffle above, and not for the ~150 in-sample cells evaluated across the session that produced it.
  * SPREAD_MAX=50 was grid-selected. The 50/65/80 band was a plateau (+0.193/+0.203/+0.169), which is why 50 was
    taken rather than the single best cell — but a plateau found by sweeping is still a swept parameter.

WHAT IT GENUINELY BUYS (the reason it is registered at all): it attacks v1.0's worst property. v1.0's result is
76% concentrated in ONE week and week 2 is negative. v1.1 spreads it out — **all four weeks positive**, and
week 3's share falls 76% -> 50%. That is a ROBUSTNESS improvement, which matters more than the expectancy bump.

Run: python study/da2_reversion_v11_validate.py
"""
from __future__ import annotations
import os, sys, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app.pivot_detect import eff_causal_share
import study.da2_reversion_validate as V10

SPREAD_MAX = 50.0                      # FROZEN — |eff-agg spread| ceiling (non-locked, causal)
SL_PCT = V10.SL_PCT                    # 0.008, inherited unchanged from v1.0
TP_PCT = V10.TP_PCT                    # 0.010, inherited unchanged from v1.0
FEE = V10.FEE
SIMS = 20000


def build():
    """(bars, sigs) with the v1.0 signal set, each tagged with its causal eff-agg spread."""
    bars, sigs = V10.build()
    _, H, _ = load_archive("1h")
    W = []
    for b in H:
        d = dict(b); d["open"] = b.get("open_price"); d["close"] = b.get("close_price"); W.append(d)
    spr = (2.0 * np.asarray(eff_causal_share(W), float) - 1.0) * 100.0
    for s in sigs:
        s["spr"] = float(spr[s["i"]]) if s["i"] < len(spr) else 0.0
    return bars, sigs


def GATE(sg):
    """v1.1 gate: the v1.0 side must exist AND the tape must be QUIET."""
    return sg["side"] != 0 and abs(sg.get("spr", 0.0)) <= SPREAD_MAX


def taken(bars, sigs, tp=TP_PCT, sl=SL_PCT):
    """Same exit machinery and non-overlap contract as v1.0; only the gate differs."""
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


def main():
    np.random.seed(42)
    bars, sigs = build()
    T = taken(bars, sigs); r = np.array([t["net"] for t in T]) * 100.0
    n = len(r); w = sum(1 for t in T if t["win"])
    base = V10.taken(bars, sigs); rb = np.array([t["net"] for t in base]) * 100.0
    print("DA2-REVERSION v1.1  (v1.0 + |eff-agg spread| <= %.0f)  SL %.1f%% / TP %.1f%%\n"
          % (SPREAD_MAX, SL_PCT * 100, TP_PCT * 100))
    print("  v1.0 baseline : n=%d win %.1f%% exp %+.4f%% total %+.1f%%" %
          (len(rb), 100 * np.mean([t["win"] for t in base]), rb.mean(), rb.sum()))
    print("  v1.1          : n=%d win %.1f%% exp %+.4f%% total %+.1f%%  (break-even %.1f%%)"
          % (n, 100.0 * w / n, r.mean(), r.sum(), breakeven()))
    mid = n // 2
    print("  SPLIT-HALF: H1 %+.4f%% | H2 %+.4f%%  -> %s"
          % (r[:mid].mean(), r[mid:].mean(), "BOTH POSITIVE" if r[:mid].mean() > 0 and r[mid:].mean() > 0 else "FAILS"))
    s = np.sort(r)
    print("  drop-best: 1 %+.4f%%  3 %+.4f%%  5 %+.4f%%" % (s[:-1].mean(), s[:-3].mean(), s[:-5].mean()))
    L = [t for t in T if t["side"] > 0]; S = [t for t in T if t["side"] < 0]
    print("  by side  : LONG n=%d %+.4f%% | SHORT n=%d %+.4f%%"
          % (len(L), np.mean([t["net"] for t in L]) * 100 if L else 0,
             len(S), np.mean([t["net"] for t in S]) * 100 if S else 0))
    samp = r[np.random.randint(0, n, size=(SIMS, n))]; means = samp.mean(axis=1)
    sd = r.std(ddof=1)
    print("  MC bootstrap: P(profit)=%.1f%%  95%%CI [%+.4f, %+.4f]%%   t=%+.2f"
          % (100 * np.mean(means > 0), np.percentile(means, 2.5), np.percentile(means, 97.5),
             r.mean() / (sd / math.sqrt(n)) if sd > 0 else 0.0))
    import datetime as dt, collections
    days = sorted({dt.datetime.utcfromtimestamp(t["t"]).strftime("%m-%d") for t in T})
    wk = {d: k // 7 for k, d in enumerate(days)}; o = collections.defaultdict(list)
    for t in T:
        o[wk[dt.datetime.utcfromtimestamp(t["t"]).strftime("%m-%d")]].append(t["net"] * 100)
    tot = sum(sum(v) for v in o.values())
    print("  WEEKLY (v1.0 was 76%% in week 3, week 2 NEGATIVE):")
    for k in sorted(o):
        print("    week %d: n=%-3d %+6.2f%%  = %5.1f%% of total" % (k + 1, len(o[k]), sum(o[k]), 100 * sum(o[k]) / tot))
    print("\nCAVEAT: SPREAD_MAX grid-selected; 128->99 is a RE-CHAINING (45 out / 25 in), and the removed trades")
    print("average -0.0000%% at 49%% win — NOT losers. v1.0 stays frozen and runs in parallel. Forward decides.")


if __name__ == "__main__":
    main()
