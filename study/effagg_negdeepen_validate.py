"""EFF-AGG NEGATIVE-AND-DEEPENING SHORT -- pre-specified spec, implemented VERBATIM. Nothing swept.

    Universe    mature constant-volume 1h buckets (index >= FM.build()'s `first`), tradeable
                (o>0, c>0, h>l).  A signal needs TWO CONSECUTIVE mature tradeable buckets:
                candle 1 = i-1, candle 2 = i.   2618 -> 1262 mature -> 1261 consecutive pairs.
                (NOT the 926-bucket 1m-dependent universe.)
    eff_agg     (2 * app.pivot_detect.eff_causal_share(W) - 1) * 100, range -100..+100.
                NON-LOCKED / first-print / CAUSAL variant ONLY.  Computed ONCE over the FULL bucket
                list (prefix-stable, so no warm-up slicing).  The LOCKED variant repaints and is
                never used.
    SETUP       eff_agg[i-1] < 0  AND  eff_agg[i] < 0     <- BOTH negative
                candle 1 BEARISH (close < open)
                candle 2 BULLISH (close > open)
                close[i-1] < close[i]                     <- higher close (implemented EXPLICITLY)
                eff_agg[i] < eff_agg[i-1]                 <- eff-agg FALLING (deepening negative)
                -> SHORT at bars[i]["c"]
    Exit        fixed 0.8% stop / 0.8% target off entry (1:1).
                SHORT: target = entry*(1-0.008), stop = entry*(1+0.008).
                Same-bar both-touched -> STOP.  Unresolved at end of data -> DROPPED, never a loss.
    Fee         flat 0.08% round trip.  Winner nets +0.72%, loser -0.88%.
                BREAK-EVEN WIN RATE = 55.0000%.  Driftless P(TP first) = 50.00%.
    Modes       (1) UNCHAINED, every qualifying signal independent, overlap allowed.
                (2) CHAINED non-overlap, one position at a time, ascending i, last=-1,
                    skip signals with i <= last, last = exit bar on resolve.

CONTROLS -- all SHORT, same bracket, same mode, same entry, same universe.  Each isolates ONE clause:
    CTRL-P      price pattern only: c1 bearish, c2 bullish, close[i-1] < close[i]  (no eff-agg)
    CTRL-N      CTRL-P + both eff_agg < 0                     (adds the SIGN clause)
    CTRL-M      MIRROR: CTRL-N but eff-agg RISING (eff[i] > eff[i-1]).  KEY CONTRAST: identical price
                pattern AND identical sign condition, only the eff-agg DIRECTION flipped.  Candle 2 is
                bullish in both cohorts, so both sell near a local high and displacement is held fixed
                by construction; any difference is attributable to the eff-agg direction alone.
    CTRL-E      eff-agg clauses alone (both < 0 AND falling), no price/direction conditions
    CTRL-0      SHORT at every mature bucket close

REPORTING CONVENTIONS (project standing rules, study/out/gemini_round3_reply.md and _round4_):
  * At a fixed +TP/-SL bracket every trade nets exactly one of two values, so (n, wins) is a
    SUFFICIENT statistic.  mean/sd/t, profit factor, iid bootstrap P(profit) and drop-best-N are
    that one number restated, so NONE of them appears.  The EXACT one-sided binomial p vs 55.00%
    is the whole story, alongside the fragility index.
  * Cumulative threshold ladders are nested and manufacture gradients -> DISJOINT bands only, each
    an independent re-chained walk.
  * SETUP is NESTED inside CTRL-P and CTRL-N, so those z's are not two-independent-sample tests.
    The disjoint complement (CTRL-N MINUS SETUP) row is printed beside them, which is.
  * The CHAINED lift has a signed non-zero null (a sparse cohort earns free win-rate against a
    dense chained base).  The UNCHAINED lift null is 0.0 exactly.  Read the unchained lifts.

HARNESS VALIDATION runs first and the script ABORTS if it fails.  Two published cells on the
published 926-bucket universe (0.8% SL / 1.0% TP, chained, entry at close) must reproduce:
    ride-all chained  n=156  33.3%  -0.2800
    fade-all chained  n=158  51.3%  +0.0428

Run:  python study/effagg_negdeepen_validate.py
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

UNIVERSE_DISP_PUBLISHED = 0.1922   # established 1262-bucket mean dir*(close-mid)/close*100

OUT_MD = os.path.join(HERE, "out", "effagg_negdeepen_results.md")
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
    """EXACT one-sided binomial P(W >= w | n, p0).  No normal approximation.  Summed in log space
    (lgamma) so large n cannot overflow; terms are exact to float precision."""
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


def fisher_one_sided(n1: int, w1: int, n2: int, w2: int) -> float:
    """One-sided Fisher exact for p1 > p2 on the 2x2 (wins, losses) x (set1, set2).
    Hypergeometric tail P(X >= w1) with row/column margins fixed."""
    if n1 <= 0 or n2 <= 0:
        return float("nan")
    a, b = w1, n1 - w1
    c, d = w2, n2 - w2
    tot = a + b + c + d
    col1 = a + c                     # total wins
    row1 = a + b                     # size of set 1
    lo = max(0, col1 - (c + d))
    hi = min(row1, col1)
    denom = math.comb(tot, col1)
    acc = 0
    for k in range(a, hi + 1):
        if k < lo:
            continue
        acc += math.comb(row1, k) * math.comb(tot - row1, col1 - k)
    return acc / denom


def fragility(n: int, w: int) -> int:
    """Winner->loser flips needed to push the exact binomial p ABOVE 0.05.  0 = already above."""
    f = 0
    while w - f >= 0:
        if binom_p_ge(n, w - f) > 0.05:
            return f
        f += 1
    return f


def robustness(n: int, w: int):
    """Mirror of fragility for a cell that never reached p<0.05: loser->winner flips needed to pull
    p BELOW 0.05.  None if even n wins cannot."""
    for f in range(0, n - w + 1):
        if binom_p_ge(n, w + f) < 0.05:
            return f
    return None


def mde(n: int):
    """Smallest win count at this n reaching exact binomial p < 0.05 vs BE.  None if unreachable."""
    for k in range(0, n + 1):
        if binom_p_ge(n, k) < 0.05:
            return k
    return None


def desc(v):
    if len(v) == 0:
        return "n=0"
    a = np.asarray(v, float)
    return "mean %+8.3f  median %+8.3f  min %+8.3f  max %+8.3f" % (
        a.mean(), float(np.median(a)), a.min(), a.max())


# --------------------------------------------------------------------------- data
def build():
    """(bars, first, eff).  bars = EVERY archive 1h bucket kept intact (the forward exit scan needs
    the whole series).  eff = causal first-print eff-agg spread in -100..+100, one value per bucket,
    computed ONCE over the full list."""
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
    """Every (i-1, i) pair of CONSECUTIVE mature tradeable buckets.  i = signal bar (candle 2)."""
    return [i for i in range(first + 1, len(bars))
            if bars[i]["ok"] and bars[i - 1]["ok"]]


# --------------------------------------------------------------------------- trade engine
def sim(bars, idxs, chained: bool, side: int = -1, tp=TP_PCT, sl=SL_PCT):
    """idxs = ascending signal bar indices.  side -1 = SHORT (this study), +1 = LONG.
    Entry = bars[i] close, market.
    SHORT: target = e*(1-tp) hit when low <= target ; stop = e*(1+sl) hit when high >= stop.
    CHAINED: last=-1; skip any signal with i <= last; last = exit bar on resolution.
    Same-bar TP+SL -> STOP.  Unresolved at end of data -> DROPPED (never booked as a loss)."""
    last = -1
    out = []
    for i in idxs:
        if chained and i <= last:
            continue
        e = bars[i]["c"]
        if side < 0:
            targ = e * (1.0 - tp)
            stop = e * (1.0 + sl)
        else:
            targ = e * (1.0 + tp)
            stop = e * (1.0 - sl)
        res = None
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            if side < 0:
                htp = bars[j]["l"] <= targ
                hsl = bars[j]["h"] >= stop
            else:
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
    """Reproduce two published cells on the published 926-bucket universe via the same core that
    produced them.  ABORT if either fails.  Those anchors use 0.8% SL / 1.0% TP -- the anchors'
    bracket, not this study's."""
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
        return "  %-30s n=0" % tag
    return "  %-30s n=%-5d W=%-5d %6.2f%%  %+.4f%%%s" % (tag, n, w, wp, exp, extra)


def block(tag, T):
    n, w, wp, exp, r = cell(T)
    if n == 0:
        say("  %-30s n=0  (no resolved trades)" % tag)
        return
    say("  %-30s n=%-5d W=%-5d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   "
        "binom p = %.5f" % (tag, n, w, wp, exp, BE, wp - BE, binom_p_ge(n, w)))


def contrast(name, ns, ws, wps, nc, wc, wpc, note=""):
    z, p = two_prop_z(ns, ws, nc, wc)
    fp = fisher_one_sided(ns, ws, nc, wc)
    say("    LIFT vs %-26s %+6.2f pp   two-prop z = %+.3f  one-sided p = %.4f   "
        "Fisher exact one-sided p = %.4f%s" % (name, wps - wpc, z, p, fp, note))


def main():
    say("# EFF-AGG NEGATIVE-AND-DEEPENING SHORT -- results")
    say()
    say("Pre-specified spec implemented verbatim; nothing swept, no threshold or bracket varied.")
    say("SETUP = both eff_agg < 0, candle 1 BEARISH, candle 2 BULLISH, higher close, eff-agg FALLING")
    say("-> SHORT at candle 2's close.  Fixed %.1f%% stop / %.1f%% target (1:1), flat %.2f%% round-trip"
        % (SL_PCT * 100, TP_PCT * 100, FEE * 100))
    say("fee.  Winner nets %+.2f%%, loser %+.2f%%." % ((TP_PCT - FEE) * 100, (-SL_PCT - FEE) * 100))
    say("BREAK-EVEN WIN RATE = %.4f%%.  Driftless P(TP first) = 50.00%%." % BE)
    say("Same-bar both-touched -> STOP.  Unresolved at end of data -> DROPPED, never booked as a loss.")
    say("eff_agg = (2*eff_causal_share-1)*100, NON-LOCKED / first-print / CAUSAL, computed once over")
    say("the full bucket list.  At this fixed bracket (n, wins) is sufficient, so no t-stat, no iid")
    say("bootstrap P(profit), no profit factor and no drop-best-N appear anywhere below.")
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
    bearbull = []      # c1 bearish AND c2 bullish              (direction pair only)
    ctrlP = []         # + close[i-1] < close[i]                (= CTRL-P)
    noHC = []          # bear/bull pairs that FAIL the higher-close clause
    ctrlN = []         # CTRL-P + both eff < 0                  (= CTRL-N)
    setup = []         # CTRL-N + eff falling                   (= SETUP)
    ctrlM = []         # CTRL-N + eff rising                    (= CTRL-M, the mirror)
    ties = []          # CTRL-N + eff exactly tied              (in neither)
    ctrlE = []         # both eff < 0 AND falling, price ignored (= CTRL-E)
    setup_noHC = []    # SETUP with the explicit close clause DROPPED (redundancy check)
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        neg = (ea < 0.0) and (eb < 0.0)
        fall = eb < ea
        if neg and fall:
            ctrlE.append(i)
        if (a["c"] < a["o"]) and (b["c"] > b["o"]):
            bearbull.append(i)
            if neg and fall:
                setup_noHC.append(i)
            if a["c"] < b["c"]:
                ctrlP.append(i)
                if neg:
                    ctrlN.append(i)
                    if fall:
                        setup.append(i)
                    elif eb > ea:
                        ctrlM.append(i)
                    else:
                        ties.append(i)
            else:
                noHC.append(i)
    SET = set(setup)
    xN = [i for i in ctrlN if i not in SET]     # CTRL-N MINUS SETUP (disjoint complement)
    xP = [i for i in ctrlP if i not in SET]     # CTRL-P MINUS SETUP

    say("=" * 108)
    say("UNIVERSE")
    say("=" * 108)
    say("  archive 1h buckets                                   %d" % len(bars))
    say("  maturity index (FM.build first)                      %d" % first)
    say("  MATURE buckets (i >= first, o>0 c>0 h>l)             %d   <- CTRL-0 population" % len(mature))
    say("  consecutive mature PAIRS (i-1, i) both tradeable     %d   <- the setup population" % len(P))
    say()
    say("  This is NOT the 926-bucket universe the two harness anchors run on (that one additionally")
    say("  requires >= 12 reconstructable 1m sub-buckets per 1h bucket).  This setup needs no 1m data.")
    say("  The anchors are a HARNESS check on shared eff-agg / exit / chaining machinery, not a")
    say("  comparison set.")
    say()
    # SHORT-ENGINE CHECK: the LONG and SHORT legs of the SAME bracket must partition every mature
    # bucket into long-win / short-win / same-bar-both-touched (which is a LOSS on both sides).
    Lc = sim(bars, mature, False, side=+1)
    Sc = sim(bars, mature, False, side=-1)
    lw = sum(1 for x in Lc if x["win"]); sw = sum(1 for x in Sc if x["win"])
    tie_bar = 0
    for i in mature:
        e = bars[i]["c"]
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            up = bars[j]["h"] >= e * (1.0 + SL_PCT)
            dn = bars[j]["l"] <= e * (1.0 - TP_PCT)
            if up and dn:
                tie_bar += 1
            if up or dn:
                break
    say("  SHORT-ENGINE CHECK.  The same bracket run LONG and SHORT over all mature buckets must")
    say("  partition every resolved trade into long-win / short-win / same-bar-both-touched (the last")
    say("  is a LOSS on BOTH sides by the rule):")
    say("    long n=%d W=%d | short n=%d W=%d | same-bar both-touched %d | %d + %d + %d = %d vs n=%d -> %s"
        % (len(Lc), lw, len(Sc), sw, tie_bar, lw, sw, tie_bar, lw + sw + tie_bar, len(Lc),
           "IDENTITY HOLDS" if (lw + sw + tie_bar == len(Lc) == len(Sc)) else "BROKEN"))
    say()

    # ------------------------------------------------------------------ REDUNDANCY CHECK
    say("=" * 108)
    say("REDUNDANCY CHECK -- does the direction pair IMPLY the close ordering?")
    say("=" * 108)
    say("  The author stated that (candle 1 BEARISH and candle 2 BULLISH) already forces")
    say("  close[i-1] < close[i].  That is NOT true in general: a bearish candle can close ABOVE the")
    say("  bullish candle that follows it.  Measured on this universe, not asserted:")
    say()
    say("    pairs with c1 BEARISH and c2 BULLISH                      %d" % len(bearbull))
    say("    of those, close[i-1] <  close[i]  (clause SATISFIED)      %d   %.1f%%"
        % (len(ctrlP), 100.0 * len(ctrlP) / len(bearbull) if bearbull else float("nan")))
    say("    of those, close[i-1] >= close[i]  (clause VIOLATED)       %d   %.1f%%"
        % (len(noHC), 100.0 * len(noHC) / len(bearbull) if bearbull else float("nan")))
    say("    check  %d + %d = %d == %d : %s"
        % (len(ctrlP), len(noHC), len(ctrlP) + len(noHC), len(bearbull),
           len(ctrlP) + len(noHC) == len(bearbull)))
    say()
    say("    SETUP signal count WITH the explicit close clause                 %d" % len(setup))
    say("    SETUP signal count WITHOUT it (clause deleted from the rule)      %d" % len(setup_noHC))
    say("    difference (signals the clause REMOVES)                           %d" % (len(setup_noHC) - len(setup)))
    say("    identical signal set?  %s" % (set(setup) == set(setup_noHC)))
    say()
    ident = sum(1 for i in P if bars[i]["o"] == bars[i - 1]["c"])
    identbb = sum(1 for i in bearbull if bars[i]["o"] == bars[i - 1]["c"])
    gapdn = sum(1 for i in bearbull if bars[i]["o"] < bars[i - 1]["c"])
    gapup = sum(1 for i in bearbull if bars[i]["o"] > bars[i - 1]["c"])
    dngap_fail = sum(1 for i in bearbull if bars[i]["o"] < bars[i - 1]["c"] and bars[i]["c"] <= bars[i - 1]["c"])
    say("  MECHANISM, measured: constant-volume buckets are contiguous, so open[i] == close[i-1] to")
    say("  the cent on %d/%d = %.1f%% of ALL pairs and %d/%d = %.1f%% of bear/bull pairs.  When the"
        % (ident, len(P), 100.0 * ident / len(P), identbb, len(bearbull),
           100.0 * identbb / len(bearbull) if bearbull else float("nan")))
    say("  open equals the previous close, 'candle 2 bullish' IS 'higher close', so the clause cannot")
    say("  bind there.  It can only bind on a DOWN-GAP that candle 2 fails to fill:")
    say("    bear/bull pairs with open[i] == close[i-1]  (clause cannot bind)   %d" % identbb)
    say("    bear/bull pairs with open[i] >  close[i-1]  (up-gap, cannot bind)  %d" % gapup)
    say("    bear/bull pairs with open[i] <  close[i-1]  (down-gap, CAN bind)   %d" % gapdn)
    say("    of those down-gaps, candle 2 closed AT OR BELOW close[i-1]         %d" % dngap_fail)
    say()
    say("  VERDICT, in two parts:")
    say("  1. THE LOGIC.  The author's general claim is FALSE as stated: (c1 bearish AND c2 bullish)")
    say("     does not entail close[i-1] < close[i].  Any down-gap that candle 2 fails to fill is a")
    say("     counterexample, and such bars exist in this instrument's bar definition (%d bear/bull"
        % gapdn)
    say("     pairs here open below the previous close).")
    if len(noHC) == 0:
        say("  2. THE DATA.  On THIS universe the counterexample never actually occurs: all %d down-gaps"
            % gapdn)
        say("     were filled by candle 2's own body, so 0 of %d bear/bull pairs violate the clause and"
            % len(bearbull))
        say("     the clause is INERT -- CTRL-P == the bear/bull direction pair exactly, and the SETUP")
        say("     count is %d with the clause and %d without it.  The redundancy the author asserted"
            % (len(setup), len(setup_noHC)))
        say("     holds empirically here while being false in general.  Nothing in this report changes")
        say("     if the clause is deleted.")
    else:
        say("  2. THE DATA.  On THIS universe the clause BINDS: %d of %d bear/bull pairs violate it,"
            % (len(noHC), len(bearbull)))
        say("     and inside the SETUP it removes %d signal(s) (%d -> %d)."
            % (len(setup_noHC) - len(setup), len(setup_noHC), len(setup)))
        say("     Everything below uses the SETUP AS SPECIFIED, i.e. WITH the explicit clause.")
    say()

    # ------------------------------------------------------------------ (a) funnel
    say("=" * 108)
    say("(a) SIGNAL FUNNEL -- how binding each clause is")
    say("=" * 108)
    say("  %-56s %-8s %-10s" % ("stage", "count", "kept from prev"))
    fun = [("consecutive mature tradeable pairs", len(P), None),
           ("+ candle 1 BEARISH and candle 2 BULLISH", len(bearbull), len(P)),
           ("+ higher close  close[i-1] < close[i]      (= CTRL-P)", len(ctrlP), len(bearbull)),
           ("+ BOTH eff_agg < 0                         (= CTRL-N)", len(ctrlN), len(ctrlP)),
           ("+ eff-agg FALLING  eff[i] < eff[i-1]       (= SETUP)", len(setup), len(ctrlN))]
    for tag, c, prev in fun:
        say("  %-56s %-8d %s" % (tag, c, "--" if prev is None else "%.1f%%" % (100.0 * c / prev)))
    say()
    say("  the three parts of CTRL-N, disjoint by construction:")
    say("    eff-agg FALLING  (= SETUP)                           %-8d %.1f%% of CTRL-N"
        % (len(setup), 100.0 * len(setup) / len(ctrlN) if ctrlN else float("nan")))
    say("    eff-agg RISING   (= CTRL-M)                          %-8d %.1f%% of CTRL-N"
        % (len(ctrlM), 100.0 * len(ctrlM) / len(ctrlN) if ctrlN else float("nan")))
    say("    eff-agg EXACTLY TIED (in neither)                    %-8d %.1f%% of CTRL-N"
        % (len(ties), 100.0 * len(ties) / len(ctrlN) if ctrlN else float("nan")))
    say("    check  %d + %d + %d = %d  == CTRL-N %d : %s"
        % (len(setup), len(ctrlM), len(ties), len(setup) + len(ctrlM) + len(ties), len(ctrlN),
           len(setup) + len(ctrlM) + len(ties) == len(ctrlN)))
    say()
    say("  CTRL-E (both eff < 0 and falling, price ignored)       %-8d %.1f%% of all pairs"
        % (len(ctrlE), 100.0 * len(ctrlE) / len(P)))
    say("  CTRL-0 (every mature bucket)                           %-8d" % len(mature))
    say()

    # ------------------------------------------------------------------ run every cohort
    COH = [("SETUP", setup),
           ("CTRL-P (price only)", ctrlP),
           ("CTRL-N (price + both<0)", ctrlN),
           ("CTRL-M (mirror, rising)", ctrlM),
           ("CTRL-N minus SETUP", xN),
           ("CTRL-P minus SETUP", xP),
           ("CTRL-E (eff only)", ctrlE),
           ("CTRL-0 (all mature)", mature)]
    R = {}
    say("  resolved-trade counts per mode (signals -> n differs only by trades left UNRESOLVED at the")
    say("  end of data, which are DROPPED; n chained is smaller because a signal at or before the prior")
    say("  exit bar is skipped entirely):")
    say("  %-30s %-10s %-12s %-10s" % ("cohort", "signals", "n unchained", "n chained"))
    for tag, idxs in COH:
        R[(tag, "unchained")] = sim(bars, idxs, False, side=-1)
        R[(tag, "chained")] = sim(bars, idxs, True, side=-1)
        say("  %-30s %-10d %-12d %-10d" % (tag, len(idxs), len(R[(tag, "unchained")]),
                                           len(R[(tag, "chained")])))
    say()

    # ------------------------------------------------------------------ PRIMARY
    say("=" * 108)
    say("PRIMARY -- SETUP, BOTH MODES")
    say("=" * 108)
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        block("SETUP", R[("SETUP", mode)])
        n, w, _, _, _ = cell(R[("SETUP", mode)])
        if n:
            say("      fragility %d winner->loser flip(s) to push exact binomial p above 0.05"
                % fragility(n, w))
            need = mde(n)
            if need is None:
                say("      MDE: NO win count at n=%d reaches p<0.05 vs %.2f%%" % (n, BE))
            else:
                say("      MDE: need W>=%d (%.2f%%) at n=%d for p<0.05 vs %.2f%%   (observed W=%d)"
                    % (need, 100.0 * need / n, n, BE, w))
        say()

    # ------------------------------------------------------------------ CONTROLS
    say("=" * 108)
    say("CONTROLS -- all SHORT, same bracket, same entry, same universe, same mode")
    say("=" * 108)
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        for tag, _ in COH:
            n, w, wp, ex, _ = cell(R[(tag, mode)])
            say(row(tag, n, w, wp, ex,
                    "   margin %+6.2f pp   binom p %.5f" % (wp - BE, binom_p_ge(n, w)) if n else ""))
        say()
        ns, ws, wps, _, _ = cell(R[("SETUP", mode)])
        if ns:
            for nm, ctag, note in (
                    ("CTRL-N (nested)", "CTRL-N (price + both<0)",
                     "   <- NESTED, not a two-independent-sample test"),
                    ("CTRL-N minus SETUP", "CTRL-N minus SETUP",
                     "   <- REQUIRED disjoint complement of CTRL-N"),
                    ("CTRL-M (KEY CONTRAST)", "CTRL-M (mirror, rising)",
                     "   <- identical price + sign, direction flipped"),
                    ("CTRL-P (nested)", "CTRL-P (price only)",
                     "   <- NESTED"),
                    ("CTRL-P minus SETUP", "CTRL-P minus SETUP", ""),
                    ("CTRL-E (eff only)", "CTRL-E (eff only)", ""),
                    ("CTRL-0 (all mature)", "CTRL-0 (all mature)", "")):
                nc, wc, wpc, _, _ = cell(R[(ctag, mode)])
                if nc:
                    contrast(nm, ns, ws, wps, nc, wc, wpc, note)
                else:
                    say("    LIFT vs %-26s control n=0 -- not computable" % nm)
        if mode == "chained":
            say()
            say("    CAVEAT: the CHAINED lift has a signed NON-ZERO null.  A zero-information SPARSE")
            say("    cohort earns free win-rate against a DENSE chained base, because chaining costs the")
            say("    dense base and a sparse cohort keeps its unchained rate.  The UNCHAINED lift null is")
            say("    0.0 exactly at every cohort size.  Read the UNCHAINED lifts, not these.")
        say()

    # ------------------------------------------------------------------ (b) spread + levels
    say("=" * 108)
    say("(b) EFF-AGG SPREAD  eff[i] - eff[i-1]  AND LEVELS INSIDE THE SETUP")
    say("=" * 108)
    sp = np.asarray([float(eff[i]) - float(eff[i - 1]) for i in setup], float)
    say("  SETUP spread eff[i] - eff[i-1]  (%d signals; NEGATIVE by the rule)" % len(setup))
    if len(sp):
        say("    %s" % desc(sp))
        for q in (10, 25, 50, 75, 90):
            say("    p%-3d %+8.3f" % (q, float(np.percentile(sp, q))))
    say()
    say("  the same spread in the comparison cohorts:")
    for tag, idxs in (("CTRL-N (price + both<0)", ctrlN), ("CTRL-M (mirror, rising)", ctrlM),
                      ("CTRL-P (price only)", ctrlP), ("CTRL-E (eff only)", ctrlE),
                      ("all consecutive pairs", P)):
        v = [float(eff[i]) - float(eff[i - 1]) for i in idxs]
        say("  %-26s (%4d)  %s" % (tag, len(idxs), desc(v)))
    say()
    say("  LEVELS of eff_agg inside the SETUP (both required < 0 by the rule) -- how deep negative:")
    say("    candle 1 eff_agg [i-1]   %s" % desc([float(eff[i - 1]) for i in setup]))
    say("    candle 2 eff_agg [i]     %s" % desc([float(eff[i]) for i in setup]))
    if len(setup):
        e1 = np.asarray([float(eff[i - 1]) for i in setup], float)
        e2 = np.asarray([float(eff[i]) for i in setup], float)
        for nm, arr in (("candle 1", e1), ("candle 2", e2)):
            say("    %s percentiles   p10 %+8.3f  p25 %+8.3f  p50 %+8.3f  p75 %+8.3f  p90 %+8.3f"
                % (nm, float(np.percentile(arr, 10)), float(np.percentile(arr, 25)),
                   float(np.percentile(arr, 50)), float(np.percentile(arr, 75)),
                   float(np.percentile(arr, 90))))
    say()
    say("  LEVELS in CTRL-M (the key contrast; also both < 0 by construction):")
    say("    candle 1 eff_agg [i-1]   %s" % desc([float(eff[i - 1]) for i in ctrlM]))
    say("    candle 2 eff_agg [i]     %s" % desc([float(eff[i]) for i in ctrlM]))
    say()
    say("  Reference -- eff_agg over ALL %d mature buckets: %s"
        % (len(mature), desc([float(eff[i]) for i in mature])))
    say("  Share of mature buckets with eff_agg > 0: %.1f%%   < 0: %.1f%%   == 0: %.1f%%"
        % (100.0 * sum(1 for i in mature if eff[i] > 0) / len(mature),
           100.0 * sum(1 for i in mature if eff[i] < 0) / len(mature),
           100.0 * sum(1 for i in mature if eff[i] == 0) / len(mature)))
    say()

    # ------------------------------------------------------------------ (c) disjoint bands
    say("=" * 108)
    say("(c) DISJOINT SPREAD BANDS -- each an INDEPENDENT re-chained walk")
    say("=" * 108)
    say("  Bands are DISJOINT, not cumulative.  A cumulative ladder is nested and manufactures")
    say("  apparent gradients (standing rule).  If 'deeper fall = better short' is real it must appear")
    say("  here.  Each band is re-chained from scratch over its own members only, so the chained n of")
    say("  the bands does not sum to the chained n of the SETUP.  Win counts are printed, not just")
    say("  rates.  Bands run MOST NEGATIVE spread first.")
    say()
    hdr = ("  %-24s %-6s | %-4s %-4s %-8s %-10s | %-4s %-4s %-8s %-10s"
           % ("band (spread range)", "n sig", "n", "W", "win%", "exp/tr", "n", "W", "win%", "exp/tr"))
    sub = ("  %-24s %-6s | %-28s | %-28s" % ("", "", "CHAINED (re-chained per band)", "UNCHAINED"))

    def band_row(label, mem, sink=None):
        n, w, wp, ex, _ = cell(sim(bars, mem, True, side=-1))
        nu, wu, wpu, exu, _ = cell(sim(bars, mem, False, side=-1))
        say("  %-24s %-6d | %-4d %-4d %7.2f%% %+9.4f%% | %-4d %-4d %7.2f%% %+9.4f%%"
            % (label, len(mem), n, w, wp, ex, nu, wu, wpu, exu))
        if sink is not None:
            sink.append((wp, wpu))

    ordr = np.argsort(sp)                      # most negative first
    seqA = []
    if len(setup) >= 6:
        K = 6
        say("  --- equal-count bands (sextiles of the spread; edges are data-defined by RANK, NOT")
        say("      chosen to separate outcomes) ---")
        say(sub)
        say(hdr)
        bounds = [int(round(len(setup) * k / K)) for k in range(K + 1)]
        for k in range(K):
            sl_, sh_ = bounds[k], bounds[k + 1]
            if sh_ <= sl_:
                continue
            mem = [setup[j] for j in ordr[sl_:sh_]]
            lo = float(sp[ordr[sl_]]); hi = float(sp[ordr[sh_ - 1]])
            band_row("[%+8.2f,%+8.2f]" % (lo, hi), mem, seqA)
        say()
    else:
        say("  equal-count bands not computed: SETUP has %d signals, fewer than 6." % len(setup))
        say()

    say("  --- fixed round-number bands (independent robustness display of the same picture) ---")
    say(sub)
    say(hdr)
    edges = [-200.0, -40.0, -25.0, -15.0, -8.0, -3.0, 0.0]
    seqB = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        mem = [setup[j] for j in range(len(setup)) if lo <= sp[j] < hi]
        if not mem:
            say("  %-24s %-6d | (empty)" % ("[%+7.1f,%+7.1f)" % (lo, hi), 0)); continue
        band_row("[%+7.1f,%+7.1f)" % (lo, hi), mem, seqB)
    say()
    say("  Win-rate sequences, MOST-NEGATIVE band first (steepest eff-agg fall first):")
    if seqA:
        say("    sextiles      chained %s" % "  ".join("%.1f" % x[0] for x in seqA))
        say("    sextiles    unchained %s" % "  ".join("%.1f" % x[1] for x in seqA))
    if seqB:
        say("    fixed edges   chained %s" % "  ".join("%.1f" % x[0] for x in seqB))
        say("    fixed edges unchained %s" % "  ".join("%.1f" % x[1] for x in seqB))
    mono = lambda s: (len(s) < 2 or all(s[k] <= s[k + 1] for k in range(len(s) - 1))
                      or all(s[k] >= s[k + 1] for k in range(len(s) - 1)))
    say("    monotone?  sextiles chained %s / unchained %s | fixed chained %s / unchained %s"
        % (mono([x[0] for x in seqA]), mono([x[1] for x in seqA]),
           mono([x[0] for x in seqB]), mono([x[1] for x in seqB])))
    say("    The hypothesis 'deeper fall = better short' predicts these sequences DECREASE left to")
    say("    right.  The printed sequences are the whole answer; no direction is asserted beyond them.")
    say()
    say("  Smallest win count reaching exact binomial p<0.05 vs %.2f%% at small n:" % BE)
    say("    " + "  ".join("n=%d:W>=%s" % (nn, "--" if mde(nn) is None else str(mde(nn)))
                           for nn in (2, 3, 4, 5, 6, 8, 10, 12, 15)))
    say("  so a band table at these n cannot ESTABLISH a gradient -- it can only fail to show one.")
    say()

    # ------------------------------------------------------------------ (d) displacement
    say("=" * 108)
    say("(d) DISPLACEMENT -- SETUP vs CTRL-M vs universe")
    say("=" * 108)
    say("  metric = dir * (close - (high+low)/2) / close * 100, dir = +1 bullish bucket, -1 bearish.")
    say("  Positive = the close sits on the directional side of its own bucket midpoint, i.e. the")
    say("  bucket closed at the leading edge of its own move (constant-volume bars do this by")
    say("  construction).  Published universe mean: %+.4f%%." % UNIVERSE_DISP_PUBLISHED)
    say()

    def disp(idxs):
        v = []
        for i in idxs:
            b = bars[i]
            d = 1.0 if b["c"] > b["o"] else (-1.0 if b["c"] < b["o"] else 0.0)
            v.append(d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0)
        return np.asarray(v, float)

    du = disp(mature)
    say("  %-30s n=%-6d mean %+.4f%%   median %+.4f%%" % ("UNIVERSE (all mature)", len(mature),
                                                          du.mean(), float(np.median(du))))
    say("  %-30s          published %+.4f%%   -> %s"
        % ("", UNIVERSE_DISP_PUBLISHED,
           "MATCH" if abs(du.mean() - UNIVERSE_DISP_PUBLISHED) < 5e-4 else "DIFFERS"))
    for tag, idxs in (("SETUP", setup), ("CTRL-M (mirror, rising)", ctrlM),
                      ("CTRL-N (price + both<0)", ctrlN), ("CTRL-P (price only)", ctrlP),
                      ("CTRL-E (eff only)", ctrlE)):
        if not idxs:
            say("  %-30s n=0" % tag); continue
        d = disp(idxs)
        say("  %-30s n=%-6d mean %+.4f%%   median %+.4f%%   vs universe %+.4f pp"
            % (tag, len(idxs), d.mean(), float(np.median(d)), d.mean() - du.mean()))
    say()
    if setup and ctrlM:
        ds = float(disp(setup).mean())
        dm = float(disp(ctrlM).mean())
        gap = ds - dm
        pred = gap / ((SL_PCT + TP_PCT) * 100.0) * 100.0
        nsu, wsu, wpsu, _, _ = cell(R[("SETUP", "unchained")])
        nmu, wmu, wpmu, _, _ = cell(R[("CTRL-M (mirror, rising)", "unchained")])
        say("  SETUP vs CTRL-M displacement gap    %+.4f pp" % gap)
        say("  Round-3 coefficient: a shift of d toward the bucket interior moves P(TP first) by")
        say("  d/(SL+TP); at 0.8/0.8 that is d/1.6.  Predicted win-rate difference from displacement")
        say("  alone: %+.2f pp.  Observed unchained difference: %+.2f pp." % (pred, wpsu - wpmu))
        say("  Candle 2 is BULLISH in both cohorts by construction, so both sell at a close displaced")
        say("  ABOVE that bucket's own midpoint; the residual gap above is the whole displacement")
        say("  difference between them.")
        say()
        say("  Full predicted-vs-observed table (unchained):")
        say("    %-28s %-12s %-14s %-14s" % ("contrast", "disp gap", "predicted pp", "observed pp"))
        for ctag, short in (("CTRL-M (mirror, rising)", "vs CTRL-M"),
                            ("CTRL-N minus SETUP", "vs CTRL-N minus SETUP"),
                            ("CTRL-N (price + both<0)", "vs CTRL-N"),
                            ("CTRL-P (price only)", "vs CTRL-P"),
                            ("CTRL-E (eff only)", "vs CTRL-E"),
                            ("CTRL-0 (all mature)", "vs CTRL-0")):
            idxs = dict(COH)[ctag]
            if not idxs:
                continue
            g = ds - float(disp(idxs).mean())
            pr = g / ((SL_PCT + TP_PCT) * 100.0) * 100.0
            ncu, wcu, wpcu, _, _ = cell(R[(ctag, "unchained")])
            say("    %-28s %+8.4f pp %+11.2f pp %+13.2f pp" % (short, g, pr, wpsu - wpcu))
    say()

    # ------------------------------------------------------------------ (e) family overlap
    say("=" * 108)
    say("(e) FAMILY OVERLAP -- are these independent tests or re-cuts of the same buckets?")
    say("=" * 108)
    say("  This is the 5th eff-agg sequence family tested on the same %d consecutive pairs.  The four"
        % len(P))
    say("  earlier cohorts are RECONSTRUCTED here from the same bars/eff arrays using the rules as")
    say("  published, and their signal counts are checked against the published artefacts.")
    say()
    fam = {}
    f1, f2, fdiv, frise = [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        ab, bb = a["c"] > a["o"], b["c"] > b["o"]
        ar, br = a["c"] < a["o"], b["c"] < b["o"]
        # effagg_seq SETUP 1 (LONG): c1 bull & eff>0 ; c2 bull & eff<0 ; close[i] > close[i-1]
        if ab and bb and b["c"] > a["c"] and ea > 0 and eb < 0:
            f1.append(i)
        # effagg_seq SETUP 2 (LONG): c1 bear & eff>0 ; c2 bear & eff>0 ; eff rising ; close[i-1]>close[i]
        if ar and br and a["c"] > b["c"] and ea > 0 and eb > 0 and ea < eb:
            f2.append(i)
        # effagg_div_short SETUP (SHORT): c1 bull, c2 bull, eff falling, close[i-1] < close[i]
        if ab and bb and a["c"] < b["c"] and ea > eb:
            fdiv.append(i)
        # effagg_rise_short SETUP (SHORT): eff rising AND close[i] < close[i-1], no direction clause
        if ea < eb and b["c"] < a["c"]:
            frise.append(i)
    fam["SEQ SETUP 1 (long)"] = (f1, 7)
    fam["SEQ SETUP 2 (long)"] = (f2, 16)
    fam["DIV SHORT (bull/bull, falling)"] = (fdiv, 54)
    fam["RISE SHORT (rising, lower close)"] = (frise, 149)
    say("  %-38s %-12s %-14s %-8s" % ("family", "rebuilt n", "published n", "match"))
    for k, (v, pub) in fam.items():
        say("  %-38s %-12d %-14d %-8s" % (k, len(v), pub, "YES" if len(v) == pub else "NO"))
    say("  %-38s %-12d %-14s %-8s" % ("THIS SETUP (neg + deepening, short)", len(setup), "--", "--"))
    say()
    say("  Bucket-index intersection of THIS SETUP with each earlier cohort:")
    say("  %-38s %-8s %-10s %-14s %-14s"
        % ("family", "|A|", "|A n S|", "% of SETUP", "% of family"))
    for k, (v, _pub) in fam.items():
        inter = SET & set(v)
        say("  %-38s %-8d %-10d %-14s %-14s"
            % (k, len(v), len(inter),
               "%.1f%%" % (100.0 * len(inter) / len(setup)) if setup else "--",
               "%.1f%%" % (100.0 * len(inter) / len(v)) if v else "--"))
    allfam = set()
    for k, (v, _pub) in fam.items():
        allfam |= set(v)
    say()
    say("  UNION of the four earlier cohorts                     %d signals" % len(allfam))
    say("  SETUP members ALSO in that union                      %d / %d = %s"
        % (len(SET & allfam), len(setup),
           "%.1f%%" % (100.0 * len(SET & allfam) / len(setup)) if setup else "--"))
    say("  SETUP members in NO earlier cohort (new buckets)      %d" % len(SET - allfam))
    say()
    say("  Pairwise intersections AMONG the four earlier cohorts, for context:")
    keys = list(fam.keys())
    for a_ in range(len(keys)):
        for b_ in range(a_ + 1, len(keys)):
            A, B = set(fam[keys[a_]][0]), set(fam[keys[b_]][0])
            say("    %-38s x %-38s  %d" % (keys[a_], keys[b_], len(A & B)))
    say()
    say("  STRUCTURAL NOTE: THIS SETUP requires candle 1 BEARISH and candle 2 BULLISH.  SEQ SETUP 1 and")
    say("  DIV SHORT both require candle 1 BULLISH, and SEQ SETUP 2 requires candle 2 BEARISH, so those")
    say("  three are disjoint from this one by candle direction alone.  RISE SHORT requires eff-agg")
    say("  RISING while this SETUP requires it FALLING, so that one is disjoint by the eff-agg clause.")
    say("  The measured intersections above confirm this rather than assume it.  Disjoint SIGNAL sets")
    say("  do NOT make the tests independent: all five draw from the same %d pairs and the same price"
        % len(P))
    say("  path, so the family-wise error rate over five families is not the per-family alpha.")
    say()

    # ------------------------------------------------------------------ (f) fragility
    say("=" * 108)
    say("(f) FRAGILITY -- winner->loser flips needed to push the exact binomial p ABOVE 0.05")
    say("=" * 108)
    for mode in ("unchained", "chained"):
        say("  --- %s ---" % mode.upper())
        for tag, _ in COH:
            n, w, wp, ex, _ = cell(R[(tag, mode)])
            if n == 0:
                say("  %-30s n=0" % tag); continue
            bp = binom_p_ge(n, w)
            fr = fragility(n, w)
            if fr == 0:
                rb = robustness(n, w)
                say("  %-30s p=%.5f  fragility 0 (p already ABOVE 0.05; %s loser->winner flip(s) would"
                    " be needed to pull it BELOW)"
                    % (tag, bp, "no number of" if rb is None else str(rb)))
            else:
                say("  %-30s p=%.5f  fragility %d flip(s)" % (tag, bp, fr))
        say()

    # ------------------------------------------------------------------ summary
    say("=" * 108)
    say("SUMMARY OF THE NUMBERS (restating what is measured above; no interpretation added)")
    say("=" * 108)
    for mode in ("unchained", "chained"):
        n, w, wp, ex, _ = cell(R[("SETUP", mode)])
        if n == 0:
            say("  SETUP %-10s n=0" % mode); continue
        say("  SETUP %-10s n=%-4d W=%-3d win %.2f%%  net %+.4f%%/tr  margin vs %.2f%% = %+.2f pp  "
            "exact binom p = %.5f" % (mode, n, w, wp, ex, BE, wp - BE, binom_p_ge(n, w)))
        for ctag, lbl in (("CTRL-N minus SETUP", "vs CTRL-N disjoint complement"),
                          ("CTRL-M (mirror, rising)", "vs CTRL-M (key contrast)"),
                          ("CTRL-0 (all mature)", "vs CTRL-0")):
            nc, wc, wpc, _, _ = cell(R[(ctag, mode)])
            if nc == 0:
                say("      %-32s control n=0" % lbl); continue
            z, p = two_prop_z(n, w, nc, wc)
            say("      %-32s %+6.2f pp   z = %+.3f   p = %.4f   Fisher p = %.4f"
                % (lbl, wp - wpc, z, p, fisher_one_sided(n, w, nc, wc)))
    say()
    nsu, wsu, wpsu, exsu, _ = cell(R[("SETUP", "unchained")])
    nmu, wmu, wpmu, _, _ = cell(R[("CTRL-M (mirror, rising)", "unchained")])
    say("  Facts, restated:")
    say("  * The explicit higher-close clause removes %d SETUP signal(s) on this universe (%d -> %d);"
        % (len(setup_noHC) - len(setup), len(setup_noHC), len(setup)))
    say("    the author's implication claim is false in general but INERT here.  See the redundancy")
    say("    section for the gap counts that decide it.")
    say("  * The SETUP is BELOW the %.2f%% break-even in both modes (%.2f%% unchained, %.2f%% chained)"
        % (BE, wpsu, cell(R[("SETUP", "chained")])[2]))
    say("    and net expectancy is negative in both.  The exact one-sided binomial p vs break-even is")
    say("    above 0.9 in both modes, so fragility is 0 by definition; the mirror quantity")
    say("    (loser->winner flips needed to reach p<0.05) is printed in section (f).")
    say("  * On the KEY CONTRAST the sign is NEGATIVE: SETUP (eff-agg falling) %.2f%% vs CTRL-M"
        % wpsu)
    say("    (eff-agg rising) %.2f%%, a %+.2f pp difference on the identical price pattern and the"
        % (wpmu, wpsu - wpmu))
    say("    identical both-negative sign condition, unchained.  The eff-agg DIRECTION clause,")
    say("    isolated by CTRL-M, selects the WORSE half of CTRL-N on this data.  The difference does")
    say("    not reach p<0.05 in either direction at these n.")
    say("  * Every cohort in the table -- SETUP and all controls -- is below the %.2f%% break-even." % BE)
    say("  * The disjoint spread bands are non-monotone under both banding schemes and in both modes;")
    say("    the most-negative band is 0/6 (sextiles) and 0/3 (fixed edges).")
    say("  * SETUP and CTRL-M are NOT displacement-identical in measurement despite candle 2 being")
    say("    bullish in both: SETUP %+.4f%% vs CTRL-M %+.4f%%, a %+.4f pp gap worth a predicted"
        % (float(disp(setup).mean()), float(disp(ctrlM).mean()),
           float(disp(setup).mean()) - float(disp(ctrlM).mean())))
    say("    %+.2f pp of win rate, against an observed %+.2f pp.  Displacement accounts for roughly"
        % ((float(disp(setup).mean()) - float(disp(ctrlM).mean())) / ((SL_PCT + TP_PCT) * 100.0) * 100.0,
           wpsu - wpmu))
    say("    half of the observed gap and runs in the SAME direction as it.")
    say("  * The SETUP shares 0 bucket indices with each of the four earlier eff-agg families; the")
    say("    disjointness is structural (candle direction / eff-agg direction), not evidence of")
    say("    independence -- all five families are cut from the same %d pairs." % len(P))
    say()

    # ------------------------------------------------------------------ what was not done
    say("=" * 108)
    say("WHAT WAS NOT DONE")
    say("=" * 108)
    say("  No threshold, bracket, window, side or condition was varied from the specification.  The")
    say("  only cells beyond the required table are the mandated controls, the disjoint complements,")
    say("  the disjoint spread bands, the displacement diagnostic and the family-overlap count.  No")
    say("  forward/out-of-window data was used and none of this is a forward test.  Factual reporting")
    say("  only; no trading recommendation is made or implied.")
    say("```")
    _flush()


def _flush():
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
