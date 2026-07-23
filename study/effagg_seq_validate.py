"""EFF-AGG SEQUENTIAL SETUPS 1 & 2 -- pre-specified spec, implemented VERBATIM. Nothing is swept.

    Universe    mature constant-volume 1h buckets (index >= FM.build()'s `first`), tradeable (o>0,c>0,h>l).
                A signal needs TWO CONSECUTIVE mature tradeable buckets: candle 1 = i-1, candle 2 = i.
    eff_agg     (2 * app.pivot_detect.eff_causal_share(W) - 1) * 100, range -100..+100.
                NON-LOCKED / first-print / CAUSAL variant ONLY. Computed ONCE over the FULL bucket list
                (prefix-stable, so the last value equals the full-history compute; no warm-up slicing).
    SETUP 1     c1 bullish AND eff_agg[i-1] > 0 ; c2 bullish AND eff_agg[i] < 0 ; close[i] > close[i-1].
    SETUP 2     c1 bearish AND eff_agg[i-1] > 0 ; c2 bearish AND eff_agg[i] > 0 ;
                eff_agg[i-1] < eff_agg[i] ; close[i-1] > close[i].
    Side        BOTH SETUPS ARE LONG.
    Entry       market at candle 2's close, bars[i]["c"].
    Exit        fixed 0.8% stop / 0.8% target off entry (1:1). Same-bar both-touched -> STOP.
                Unresolved at end of data -> DROPPED, never booked as a loss.
    Fee         flat 0.08% round trip.  Winner nets +0.72%, loser -0.88%.
                BREAK-EVEN WIN RATE = 55.0000%.  Driftless P(TP first) = 50.00%.
    Modes       (1) UNCHAINED, every qualifying signal independent, overlap allowed.
                (2) CHAINED non-overlap, one position at a time, ascending i, last=-1,
                    skip signals with i <= last, last = exit bar on resolve.

CONTROLS (the part that decides whether eff-agg is doing anything) -- same bracket, same mode:
    CONTROL 1   c1 bullish, c2 bullish, close[i] > close[i-1]        (eff-agg conditions REMOVED)
    CONTROL 2   c1 bearish, c2 bearish, close[i-1] > close[i]        (eff-agg conditions REMOVED)
    CONTROL 0   LONG at EVERY mature bucket close                    (broadest base rate)

REPORTING CONVENTIONS (project standing rules, see study/out/gemini_round3_reply.md):
  * At a fixed +TP/-SL bracket every trade nets exactly one of two values, so (n, wins) is a SUFFICIENT
    statistic. mean/sd/t, profit factor, iid bootstrap P(profit) and drop-best-N are that one number
    restated, so NONE of them is reported. The EXACT one-sided binomial p vs 55.00% is the whole story.
  * Split-half passes 93-99% of random rearrangements of the same wins; where it is shown, the
    conditional hypergeometric pass probability is printed beside it.
  * Cumulative threshold ladders manufacture gradients -> disjoint forms only.
  * SETUP n is nested INSIDE its control, so the two-proportion z between them is not a two-independent-
    sample test. The disjoint complement (control MINUS setup) row is printed beside it, which is.

HARNESS VALIDATION runs first and the script ABORTS if it fails. Two published cells on the published
926-bucket universe (0.8% SL / 1.0% TP, chained, entry at close) must reproduce to the printed digit:
    ride-all chained  n=156  33.3%  -0.2800
    fade-all chained  n=158  51.3%  +0.0428
That universe is NOT the universe the setups run on: it is gated on >=12 reconstructable 1m sub-buckets,
which these setups do not need. The setup universe is stated explicitly in the output.

Run:  python study/effagg_seq_validate.py
"""
from __future__ import annotations
import os, sys, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from study.archive_loader import load_archive              # noqa: E402
import study.mm_skew_feature_matrix as FM                  # noqa: E402
from app.pivot_detect import eff_causal_share              # noqa: E402

SL_PCT = 0.008        # FROZEN 0.8% stop
TP_PCT = 0.008        # FROZEN 0.8% target  (1:1)
FEE = 0.0008          # flat 0.08% round trip
BE = 100.0 * (FEE + SL_PCT) / (TP_PCT + SL_PCT)            # 55.0000% break-even win rate
P0 = BE / 100.0

OUT_MD = os.path.join(HERE, "out", "effagg_seq_results.md")
_LINES: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _LINES.append(s)


def _f(x, d=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


# --------------------------------------------------------------------------- stats primitives
def binom_p_ge(n: int, w: int, p0: float = P0) -> float:
    """EXACT one-sided binomial P(W >= w | n, p0). No normal approximation.
    Summed in log space (lgamma) so large n does not overflow; the terms are exact to float precision."""
    if n <= 0:
        return 1.0
    w = max(0, min(n, w))
    lg = math.lgamma
    lp = math.log(p0)
    lq = math.log(1.0 - p0)
    acc = 0.0
    for k in range(w, n + 1):
        acc += math.exp(lg(n + 1) - lg(k + 1) - lg(n - k + 1) + k * lp + (n - k) * lq)
    return min(1.0, acc)


def two_prop_z(n1: int, w1: int, n2: int, w2: int):
    """Pooled two-proportion z and one-sided p for p1 > p2."""
    if n1 <= 0 or n2 <= 0:
        return float("nan"), float("nan")
    p1 = w1 / n1
    p2 = w2 / n2
    pb = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pb * (1.0 - pb) * (1.0 / n1 + 1.0 / n2))
    if se <= 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    return z, 0.5 * math.erfc(z / math.sqrt(2.0))


def fragility(n: int, w: int) -> int:
    """Winner->loser flips needed to push the exact binomial p above 0.05. 0 = already above."""
    f = 0
    while w - f >= 0:
        if binom_p_ge(n, w - f) > 0.05:
            return f
        f += 1
    return f


def robustness(n: int, w: int):
    """Mirror of fragility for a cell that never reached p<0.05: loser->winner flips needed to pull p
    BELOW 0.05.  None if even n wins cannot."""
    for f in range(0, n - w + 1):
        if binom_p_ge(n, w + f) < 0.05:
            return f
    return None


def hyper_both_halves_positive(n: int, w: int) -> float:
    """P(a RANDOM rearrangement of the same w wins over n slots puts a POSITIVE net expectancy in BOTH
    halves). H1 = first n//2 trades. A half of size m with k wins is positive iff k > P0*m."""
    if n < 2:
        return float("nan")
    m = n // 2
    m2 = n - m
    tot = math.comb(n, m)
    acc = 0
    for k in range(max(0, w - m2), min(m, w) + 1):
        if k > P0 * m and (w - k) > P0 * m2:
            acc += math.comb(m, k) * math.comb(n - m, w - k)
    return acc / tot


def desc(v):
    """mean / median / min / max of a list, as a formatted string."""
    if len(v) == 0:
        return "n=0"
    a = np.asarray(v, float)
    return "mean %+7.2f  median %+7.2f  min %+7.2f  max %+7.2f" % (
        a.mean(), float(np.median(a)), a.min(), a.max())


# --------------------------------------------------------------------------- data
def build():
    """(bars, first, eff). bars = EVERY archive 1h bucket kept intact (the forward exit scan needs the
    whole series). eff = causal first-print eff-agg spread in -100..+100, one value per bucket, computed
    ONCE over the full list."""
    _, H, _ = load_archive("1h")
    _, first, _, _ = FM.build()
    bars = []
    for b in H:
        o = _f(b.get("open_price")); c = _f(b.get("close_price"))
        h = _f(b.get("high")); l = _f(b.get("low"))
        bars.append(dict(o=o, c=c, h=h, l=l, t=_f(b.get("start_time")),
                         ok=(o > 0 and c > 0 and h > l)))
    W = []
    for b in H:
        d = dict(b)
        d["open"] = b.get("open_price")
        d["close"] = b.get("close_price")
        W.append(d)
    eff = (2.0 * np.asarray(eff_causal_share(W), float) - 1.0) * 100.0
    return bars, first, eff


def pairs(bars, first):
    """Every (i-1, i) pair of CONSECUTIVE mature tradeable buckets. i is the signal bar (candle 2)."""
    out = []
    for i in range(first + 1, len(bars)):
        if bars[i]["ok"] and bars[i - 1]["ok"]:
            out.append(i)
    return out


# --------------------------------------------------------------------------- trade engine
def sim(bars, idxs, chained: bool, tp=TP_PCT, sl=SL_PCT):
    """idxs = ascending list of signal bar indices. ALL LONG. Entry = bars[i] close, market.
    CHAINED: last=-1; skip any signal with i <= last; last = exit bar on resolution.
    Same-bar TP+SL -> STOP.  Unresolved at end of data -> DROPPED (never booked as a loss)."""
    last = -1
    out = []
    for i in idxs:
        if chained and i <= last:
            continue
        e = bars[i]["c"]
        stop = e * (1.0 - sl)
        targ = e * (1.0 + tp)
        res = None
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            htp = bars[j]["h"] >= targ
            hsl = bars[j]["l"] <= stop
            if htp and hsl:
                res = (False, j); break
            if htp:
                res = (True, j); break
            if hsl:
                res = (False, j); break
        if res is None:
            continue
        out.append(dict(i=i, win=res[0], net=((tp if res[0] else -sl) - FEE) * 100.0))
        if chained:
            last = res[1]
    return out


def cell(T):
    n = len(T)
    w = sum(1 for x in T if x["win"])
    r = np.array([x["net"] for x in T], float)
    return n, w, (100.0 * w / n if n else float("nan")), (r.mean() if n else float("nan")), r


# --------------------------------------------------------------------------- harness validation
def validate_harness() -> bool:
    """Reproduce two published cells on the published 926-bucket universe, via the same core that
    produced them.  ABORT the run if either fails.  NOTE the anchors use 0.8% SL / 1.0% TP -- the
    anchors' bracket, not this study's."""
    sys.path.insert(0, os.path.join(HERE, "out"))
    try:
        import entry_norm_core as C                          # noqa: E402
    except Exception as exc:                                 # pragma: no cover
        say("HARNESS VALIDATION: CANNOT RUN -- %s" % exc)
        return False
    bars, U = C.build_universe()
    ok = True
    say("HARNESS VALIDATION (published 926-bucket universe, entry at close, chained, 0.8SL/1.0TP)")
    say("  universe n = %d   (published: 926)" % len(U))
    for name, sgn, exp_n, exp_win, exp_net in (("ride-all", +1, 156, 33.3, -0.2800),
                                               ("fade-all", -1, 158, 51.3, +0.0428)):
        o = C.sim(bars, [(r["i"], sgn * r["cdir"]) for r in U], C.entry_fn("E1"), chained=True)
        n = len(o); w = sum(1 for x in o if x["win"])
        wp = 100.0 * w / n; net = float(np.mean([x["net"] for x in o])) * 100.0
        good = (n == exp_n) and (abs(wp - exp_win) < 0.05) and (abs(net - exp_net) < 5e-5)
        ok = ok and good
        say("  %-8s  n=%-4d %5.1f%%  %+.4f      published  n=%-4d %5.1f%%  %+.4f   -> %s"
            % (name, n, wp, net, exp_n, exp_win, exp_net, "MATCH" if good else "MISMATCH"))
    say("  verdict: %s" % ("PASS -- harness reproduces both cells" if ok else "FAIL -- STOPPING"))
    say()
    return ok


# --------------------------------------------------------------------------- report helpers
def row(tag, n, w, wp, exp, extra=""):
    if n == 0:
        return "  %-28s n=0" % tag
    return "  %-28s n=%-5d W=%-5d %6.2f%%  %+.4f%%%s" % (tag, n, w, wp, exp, extra)


def block(tag, T):
    """Full required block for one cell."""
    n, w, wp, exp, r = cell(T)
    if n == 0:
        say("  %-28s n=0  (no resolved trades)" % tag)
        return
    bp = binom_p_ge(n, w)
    say("  %-28s n=%-5d W=%-5d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   "
        "binom p = %.5f" % (tag, n, w, wp, exp, BE, wp - BE, bp))


def main():
    say("# EFF-AGG SEQUENTIAL SETUPS 1 & 2 -- results")
    say()
    say("Pre-specified spec implemented verbatim; nothing swept, no parameter varied.")
    say("Both setups are LONG, entered market at candle 2's close.  Fixed %.1f%% stop / %.1f%% target"
        % (SL_PCT * 100, TP_PCT * 100))
    say("(1:1), flat %.2f%% round-trip fee.  Winner nets %+.2f%%, loser %+.2f%%." %
        (FEE * 100, (TP_PCT - FEE) * 100, (-SL_PCT - FEE) * 100))
    say("BREAK-EVEN WIN RATE = %.4f%%.  Driftless P(TP first) = 50.00%%." % BE)
    say("Same-bar both-touched -> STOP.  Unresolved at end of data -> DROPPED, never booked as a loss.")
    say("eff_agg = (2*eff_causal_share-1)*100, NON-LOCKED / first-print / CAUSAL, computed once over the")
    say("full bucket list.  At this fixed bracket (n, wins) is sufficient, so no t, no bootstrap, no")
    say("profit factor and no drop-best-N appear anywhere below.")
    say()
    say("```")
    if not validate_harness():
        say("```")
        _flush()
        raise SystemExit("HARNESS VALIDATION FAILED -- not proceeding.")

    bars, first, eff = build()
    P = pairs(bars, first)
    mature = [i for i in range(first, len(bars)) if bars[i]["ok"]]

    # ------------------------------------------------------------------ cohorts
    s1, s2, c1, c2 = [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        a_bull = b_bull = None
        a_bull = a["c"] > a["o"]
        a_bear = a["c"] < a["o"]
        b_bull = b["c"] > b["o"]
        b_bear = b["c"] < b["o"]
        # CONTROL 1 / SETUP 1 family
        if a_bull and b_bull and b["c"] > a["c"]:
            c1.append(i)
            if ea > 0 and eb < 0:
                s1.append(i)
        # CONTROL 2 / SETUP 2 family
        if a_bear and b_bear and a["c"] > b["c"]:
            c2.append(i)
            if ea > 0 and eb > 0 and ea < eb:
                s2.append(i)
    pooled = sorted(set(s1) | set(s2))
    cpool = sorted(set(c1) | set(c2))
    x1 = [i for i in c1 if i not in set(s1)]        # CONTROL 1 minus SETUP 1 (disjoint complement)
    x2 = [i for i in c2 if i not in set(s2)]

    say("=" * 104)
    say("UNIVERSE")
    say("=" * 104)
    say("  archive 1h buckets                                   %d" % len(bars))
    say("  maturity index (FM.build first)                      %d" % first)
    say("  MATURE buckets (i >= first, o>0 c>0 h>l)             %d   <- CONTROL 0 population" % len(mature))
    say("  consecutive mature PAIRS (i-1, i) both tradeable     %d   <- the setup population" % len(P))
    say()
    say("  This is NOT the 926-bucket universe the two harness anchors run on.  That universe additionally")
    say("  requires >= 12 reconstructable 1m sub-buckets per 1h bucket (it was built for half-bucket")
    say("  features).  These setups need no 1m data at all, so they run on the larger mature-bucket set.")
    say("  The anchors above are a HARNESS check on shared exit/chaining machinery, not a comparison set.")
    say()

    # ------------------------------------------------------------------ (a) signal counts
    say("=" * 104)
    say("(a) SIGNAL COUNTS")
    say("=" * 104)
    say("  %-28s %-10s %-10s %-10s" % ("cohort", "signals", "n unchain", "n chained"))
    counts = {}
    R = {}
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("SETUP 1+2 POOLED", pooled),
                      ("CONTROL 1", c1), ("CONTROL 2", c2), ("CONTROL 1+2 POOLED", cpool),
                      ("CONTROL 1 minus SETUP 1", x1), ("CONTROL 2 minus SETUP 2", x2),
                      ("CONTROL 0 (all mature)", mature)):
        R[(tag, "unchained")] = sim(bars, idxs, False)
        R[(tag, "chained")] = sim(bars, idxs, True)
        counts[tag] = len(idxs)
        say("  %-28s %-10d %-10d %-10d" % (tag, len(idxs), len(R[(tag, "unchained")]),
                                           len(R[(tag, "chained")])))
    say()
    say("  signals -> n unchained differs only by trades left UNRESOLVED at the end of data, which are")
    say("  DROPPED.  n chained is smaller because a signal landing at or before the prior exit bar is")
    say("  skipped entirely.")
    say()

    # ------------------------------------------------------------------ MAIN TABLE
    say("=" * 104)
    say("REQUIRED TABLE -- SETUPS, BOTH MODES")
    say("=" * 104)
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        for tag in ("SETUP 1", "SETUP 2", "SETUP 1+2 POOLED"):
            block(tag, R[(tag, mode)])
        say()

    # ------------------------------------------------------------------ MATCHED CONTROLS
    say("=" * 104)
    say("MATCHED CONTROLS -- SAME PRICE PATTERN, EFF-AGG CONDITIONS REMOVED")
    say("=" * 104)
    say("  Same bracket, same mode, same entry, same universe.  A setup that only matches its own price-")
    say("  pattern control is a price pattern, not an eff-agg finding.")
    say()
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        for stag, ctag, xtag in (("SETUP 1", "CONTROL 1", "CONTROL 1 minus SETUP 1"),
                                 ("SETUP 2", "CONTROL 2", "CONTROL 2 minus SETUP 2"),
                                 ("SETUP 1+2 POOLED", "CONTROL 1+2 POOLED", None)):
            ns, ws, wps, es, _ = cell(R[(stag, mode)])
            nc, wc, wpc, ec, _ = cell(R[(ctag, mode)])
            say(row(stag, ns, ws, wps, es, "   binom p %.5f" % binom_p_ge(ns, ws) if ns else ""))
            say(row(ctag + " (matched)", nc, wc, wpc, ec,
                    "   binom p %.5f" % binom_p_ge(nc, wc) if nc else ""))
            if xtag is not None:
                nx, wx, wpx, ex, _ = cell(R[(xtag, mode)])
                say(row(xtag, nx, wx, wpx, ex,
                        "   binom p %.5f" % binom_p_ge(nx, wx) if nx else ""))
            z, p = two_prop_z(ns, ws, nc, wc)
            say("    LIFT vs own price-pattern control   %+6.2f pp    two-proportion z = %+.3f   "
                "one-sided p = %.4f" % (wps - wpc, z, p))
            if xtag is not None:
                nx, wx, wpx, ex, _ = cell(R[(xtag, mode)])
                zx, px = two_prop_z(ns, ws, nx, wx)
                say("    LIFT vs DISJOINT complement         %+6.2f pp    two-proportion z = %+.3f   "
                    "one-sided p = %.4f" % (wps - wpx, zx, px))
                say("    (the setup is NESTED inside its control, so the first z is not a two-independent-")
                say("     sample test -- the complement row is the clean one.)")
            say()
        n0, w0, wp0, e0, _ = cell(R[("CONTROL 0 (all mature)", mode)])
        say(row("CONTROL 0 (all mature)", n0, w0, wp0, e0,
                "   binom p %.5f" % binom_p_ge(n0, w0) if n0 else ""))
        for stag in ("SETUP 1", "SETUP 2", "SETUP 1+2 POOLED"):
            ns, ws, wps, es, _ = cell(R[(stag, mode)])
            z, p = two_prop_z(ns, ws, n0, w0)
            say("    %-18s LIFT vs CONTROL 0   %+6.2f pp   z = %+.3f   one-sided p = %.4f"
                % (stag, wps - wp0, z, p))
        if mode == "chained":
            say("    CAVEAT: the CHAINED lift has a signed non-zero null -- a zero-information SPARSE")
            say("    cohort earns free win-rate against a DENSE chained base, because chaining costs the")
            say("    dense base and a sparse cohort keeps its unchained rate.  The UNCHAINED lift null is")
            say("    0.0 exactly at every cohort size.  Read the unchained lifts, not these.")
        say()

    # ------------------------------------------------------------------ (b) eff-agg distributions
    say("=" * 104)
    say("(b) EFF-AGG DISTRIBUTION WITHIN EACH COHORT (how binding the sign conditions actually are)")
    say("=" * 104)
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2),
                      ("CONTROL 1", c1), ("CONTROL 2", c2),
                      ("CONTROL 0 (all mature)", mature)):
        e1 = [float(eff[i - 1]) for i in idxs if i - 1 >= 0]
        e2 = [float(eff[i]) for i in idxs]
        say("  %s   (%d signals)" % (tag, len(idxs)))
        say("    candle 1 eff_agg   %s" % desc(e1))
        say("    candle 2 eff_agg   %s" % desc(e2))
    say()
    say("  Reference -- eff_agg over ALL %d mature buckets: %s" % (len(mature), desc([float(eff[i]) for i in mature])))
    say("  Share of mature buckets with eff_agg > 0: %.1f%%   < 0: %.1f%%   == 0: %.1f%%"
        % (100.0 * sum(1 for i in mature if eff[i] > 0) / len(mature),
           100.0 * sum(1 for i in mature if eff[i] < 0) / len(mature),
           100.0 * sum(1 for i in mature if eff[i] == 0) / len(mature)))
    say()
    say("  How many of each CONTROL cohort the eff-agg conditions actually remove:")
    say("    CONTROL 1 %d -> SETUP 1 %d   (kept %.1f%%)" %
        (len(c1), len(s1), 100.0 * len(s1) / len(c1) if c1 else float("nan")))
    say("    CONTROL 2 %d -> SETUP 2 %d   (kept %.1f%%)" %
        (len(c2), len(s2), 100.0 * len(s2) / len(c2) if c2 else float("nan")))
    say()

    # ------------------------------------------------------------------ (c) SETUP 2 spread
    say("=" * 104)
    say("(c) SETUP 2 -- IS IT JUST 'EFF-AGG RISING'?")
    say("=" * 104)
    red = [i for i in c2 if bars[i - 1]["c"] > bars[i]["c"] and eff[i - 1] > 0 and eff[i - 1] < eff[i]]
    say("  LOGICAL NOTE, verified against the data rather than asserted: the rule's clause eff_agg[i] > 0")
    say("  is REDUNDANT.  eff_agg[i-1] > 0 together with eff_agg[i-1] < eff_agg[i] already forces it.")
    say("  Dropping that clause gives %d signals against SETUP 2's %d -- identical set: %s."
        % (len(red), len(s2), set(red) == set(s2)))
    say("  So SETUP 2 is exactly: bearish pair, lower close, eff_agg POSITIVE on candle 1, and RISING.")
    say()
    d2 = [float(eff[i]) - float(eff[i - 1]) for i in s2]
    say("  spread eff_agg[i] - eff_agg[i-1] within SETUP 2 (%d signals, required > 0 by the rule)" % len(s2))
    say("    %s" % desc(d2))
    if d2:
        a = np.asarray(d2, float)
        for q in (10, 25, 50, 75, 90):
            say("    p%-3d %+7.2f" % (q, float(np.percentile(a, q))))
    say()
    d2c = [float(eff[i]) - float(eff[i - 1]) for i in c2]
    say("  the same spread over ALL of CONTROL 2 (%d signals, unconditioned)" % len(c2))
    say("    %s" % desc(d2c))
    rise_c2 = [i for i in c2 if eff[i] > eff[i - 1]]
    say("  CONTROL 2 members with eff_agg RISING (eff[i] > eff[i-1]):  %d / %d = %.1f%%"
        % (len(rise_c2), len(c2), 100.0 * len(rise_c2) / len(c2) if c2 else float("nan")))
    say("  of those, how many ALSO have both eff_agg values > 0 (= SETUP 2): %d / %d = %.1f%%"
        % (len(s2), len(rise_c2), 100.0 * len(s2) / len(rise_c2) if rise_c2 else float("nan")))
    say()
    say("  DECOMPOSITION -- rising-only vs the full SETUP 2 rule, same bracket, both modes.  This is a")
    say("  DIAGNOSTIC for question (c), not an additional candidate.")
    for mode, ch in (("unchained", False), ("chained", True)):
        say("  --- %s ---" % mode.upper())
        for tag, idxs in (("C2 + rising only", rise_c2),
                          ("C2 + rising + both>0 (=S2)", s2),
                          ("C2 + rising + NOT both>0", [i for i in rise_c2 if not (eff[i - 1] > 0 and eff[i] > 0)]),
                          ("C2 + NOT rising", [i for i in c2 if eff[i] <= eff[i - 1]])):
            n, w, wp, ex, _ = cell(sim(bars, idxs, ch))
            say(row(tag, n, w, wp, ex, "   binom p %.5f" % binom_p_ge(n, w) if n else ""))
        say()

    # ------------------------------------------------------------------ (d) fragility
    say("=" * 104)
    say("(d) FRAGILITY -- winner->loser flips needed to push the exact binomial p above 0.05")
    say("=" * 104)
    for mode in ("unchained", "chained"):
        say("  --- %s ---" % mode.upper())
        for tag in ("SETUP 1", "SETUP 2", "SETUP 1+2 POOLED", "CONTROL 1", "CONTROL 2",
                    "CONTROL 0 (all mature)"):
            n, w, wp, ex, _ = cell(R[(tag, mode)])
            if n == 0:
                say("  %-28s n=0" % tag); continue
            bp = binom_p_ge(n, w)
            fr = fragility(n, w)
            if fr == 0:
                rb = robustness(n, w)
                say("  %-28s p=%.5f  fragility 0 (p already ABOVE 0.05; %s loser->winner flip(s) would"
                    " be needed to pull it BELOW)" % (tag, bp, "no number of" if rb is None else str(rb)))
            else:
                say("  %-28s p=%.5f  fragility %d flip(s)" % (tag, bp, fr))
        say()
    say("  MINIMUM DETECTABLE EFFECT at each setup's own n -- the smallest win count (and win rate) that")
    say("  would reach exact binomial p < 0.05 vs %.2f%%.  This is what the cell is powered to see:" % BE)
    for mode in ("unchained", "chained"):
        for tag in ("SETUP 1", "SETUP 2", "SETUP 1+2 POOLED"):
            n, w, wp, ex, _ = cell(R[(tag, mode)])
            if n == 0:
                continue
            need = None
            for k in range(0, n + 1):
                if binom_p_ge(n, k) < 0.05:
                    need = k; break
            if need is None:
                say("    %-9s %-10s n=%-4d observed W=%-3d -- NO win count at this n reaches p<0.05"
                    % (mode, tag, n, w))
            else:
                say("    %-9s %-10s n=%-4d observed W=%-3d (%.2f%%) | need W>=%d (%.2f%%) for p<0.05"
                    % (mode, tag, n, w, wp, need, 100.0 * need / n))
    say()

    # ------------------------------------------------------------------ (e) overlap
    say("=" * 104)
    say("(e) COHORT OVERLAP (should be disjoint by construction -- verified, not assumed)")
    say("=" * 104)
    S1, S2 = set(s1), set(s2)
    say("  SETUP 1 signals                     %d" % len(S1))
    say("  SETUP 2 signals                     %d" % len(S2))
    say("  SETUP 1 INTERSECT SETUP 2           %d   %s" % (len(S1 & S2),
                                                           "DISJOINT" if not (S1 & S2) else "OVERLAP"))
    say("  SETUP 1 UNION SETUP 2 (= pooled)    %d   (%d + %d = %d, consistent)"
        % (len(S1 | S2), len(S1), len(S2), len(S1) + len(S2)))
    say("  Reason: candle 2 is bullish in SETUP 1 and bearish in SETUP 2, so no bar index can satisfy both.")
    say("  Same check on the controls: CONTROL 1 INTERSECT CONTROL 2 = %d" % len(set(c1) & set(c2)))
    say("  Nesting check: SETUP 1 subset of CONTROL 1 = %s ; SETUP 2 subset of CONTROL 2 = %s"
        % (S1 <= set(c1), S2 <= set(c2)))
    say()
    say("  NOTE ON TRADE-LEVEL OVERLAP: signal cohorts being disjoint does NOT make the POOLED unchained")
    say("  trade list independent -- unchained trades from nearby bars share future price path.  The")
    say("  chained mode is the one that removes that.  Both are reported.")
    say()

    # ------------------------------------------------------------------ split-half, with its own null
    say("=" * 104)
    say("SPLIT-HALF, WITH ITS CONDITIONAL HYPERGEOMETRIC PASS PROBABILITY BESIDE IT")
    say("=" * 104)
    say("  Reported only because the project's standing rule is: if you show split-half, show the")
    say("  probability a RANDOM rearrangement of the same wins passes it.  It is near-vacuous.")
    say()
    for mode in ("unchained", "chained"):
        say("  --- %s ---" % mode.upper())
        for tag in ("SETUP 1", "SETUP 2", "SETUP 1+2 POOLED"):
            n, w, wp, ex, r = cell(R[(tag, mode)])
            if n < 2:
                say("  %-28s n=%d -- not computable" % (tag, n)); continue
            mid = n // 2
            h1 = r[:mid].mean(); h2 = r[mid:].mean()
            hg = hyper_both_halves_positive(n, w)
            say("  %-28s H1 n=%d %+.4f%% | H2 n=%d %+.4f%%  -> %-13s  random rearrangement passes "
                "with p = %s" % (tag, mid, h1, n - mid, h2,
                                 "BOTH POSITIVE" if (h1 > 0 and h2 > 0) else "FAILS",
                                 ("%.4f" % hg) if hg > 0 else "0 EXACTLY"))
        say()

    say("=" * 104)
    say("WHAT WAS NOT DONE")
    say("=" * 104)
    say("  No threshold, bracket, window or condition was varied.  The only cells beyond the required")
    say("  table are the (c) decomposition diagnostic and the disjoint-complement control rows, both of")
    say("  which are mandated diagnostics rather than candidate rules.  No forward/out-of-window data was")
    say("  used and none of this is a forward test.")
    say("```")
    _flush()


def _flush():
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
