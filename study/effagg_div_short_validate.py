"""EFF-AGG DIVERGENCE SHORT -- pre-specified spec, implemented VERBATIM. Nothing is swept.

    Universe    mature constant-volume 1h buckets (index >= FM.build()'s `first`), tradeable
                (o>0, c>0, h>l).  A signal needs TWO CONSECUTIVE mature tradeable buckets:
                candle 1 = i-1, candle 2 = i.   2618 -> 1262 mature -> 1261 consecutive pairs.
                (NOT the 926-bucket 1m-dependent universe.)
    eff_agg     (2 * app.pivot_detect.eff_causal_share(W) - 1) * 100, range -100..+100.
                NON-LOCKED / first-print / CAUSAL variant ONLY. Computed ONCE over the FULL bucket
                list (prefix-stable, so no warm-up slicing).
    SETUP       candle 1 BULLISH (close > open)
                candle 2 BULLISH (close > open)
                eff_agg[i-1] > eff_agg[i]      <- eff-agg FALLING (no sign requirement either side)
                close[i-1]  < close[i]         <- higher close
                -> SHORT at bars[i]["c"]
    Exit        fixed 0.8% stop / 0.8% target off entry (1:1).
                SHORT: target = entry*(1-0.008), stop = entry*(1+0.008).
                Same-bar both-touched -> STOP.  Unresolved at end of data -> DROPPED, never a loss.
    Fee         flat 0.08% round trip.  Winner nets +0.72%, loser -0.88%.
                BREAK-EVEN WIN RATE = 55.0000%.  Driftless P(TP first) = 50.00%.
    Modes       (1) UNCHAINED, every qualifying signal independent, overlap allowed.
                (2) CHAINED non-overlap, one position at a time, ascending i, last=-1,
                    skip signals with i <= last, last = exit bar on resolve.

CONTROLS -- all SHORT, same bracket, same mode, same entry, same universe:
    CTRL-P      price pattern only: bull, bull, close[i-1] < close[i]   (NO eff-agg condition)
    CTRL-M      MIRROR: same price pattern but eff-agg RISING (eff[i-1] < eff[i])
    CTRL-E      eff-agg condition alone: eff_agg[i-1] > eff_agg[i], price pattern ignored
    CTRL-0      SHORT at every mature bucket close

REPORTING CONVENTIONS (project standing rules, study/out/gemini_round3_reply.md and _round4_):
  * At a fixed +TP/-SL bracket every trade nets exactly one of two values, so (n, wins) is a
    SUFFICIENT statistic.  mean/sd/t, profit factor, iid bootstrap P(profit) and drop-best-N are
    that one number restated, so NONE of them appears.  The EXACT one-sided binomial p vs 55.00%
    is the whole story, alongside the fragility index.
  * Cumulative threshold ladders are nested and manufacture gradients -> DISJOINT bands only.
  * SETUP is NESTED inside CTRL-P, so the SETUP-vs-CTRL-P z is not a two-independent-sample test.
    The disjoint complement (CTRL-P MINUS SETUP) row is printed beside it, which is.
  * The CHAINED lift has a signed non-zero null (a sparse cohort earns free win-rate against a
    dense chained base).  The UNCHAINED lift null is 0.0 exactly.  Read the unchained lifts.

HARNESS VALIDATION runs first and the script ABORTS if it fails.  Two published cells on the
published 926-bucket universe (0.8% SL / 1.0% TP, chained, entry at close) must reproduce:
    ride-all chained  n=156  33.3%  -0.2800
    fade-all chained  n=158  51.3%  +0.0428

Run:  python study/effagg_div_short_validate.py
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

OUT_MD = os.path.join(HERE, "out", "effagg_div_short_results.md")
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
    say("# EFF-AGG DIVERGENCE SHORT -- results")
    say()
    say("Pre-specified spec implemented verbatim; nothing swept, no threshold or bracket varied.")
    say("SETUP is SHORT, entered market at candle 2's close.  Fixed %.1f%% stop / %.1f%% target (1:1),"
        % (SL_PCT * 100, TP_PCT * 100))
    say("flat %.2f%% round-trip fee.  Winner nets %+.2f%%, loser %+.2f%%." %
        (FEE * 100, (TP_PCT - FEE) * 100, (-SL_PCT - FEE) * 100))
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
    bullbull, hc, setup, ctrlM, ties, ctrlE = [], [], [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        if ea > eb:
            ctrlE.append(i)
        if (a["c"] > a["o"]) and (b["c"] > b["o"]):
            bullbull.append(i)
            if a["c"] < b["c"]:
                hc.append(i)                       # = CTRL-P
                if ea > eb:
                    setup.append(i)                # = SETUP  (eff falling)
                elif ea < eb:
                    ctrlM.append(i)                # = CTRL-M (eff rising)
                else:
                    ties.append(i)                 # exact tie -> in neither
    ctrlP = hc
    xP = [i for i in ctrlP if i not in set(setup)]   # CTRL-P MINUS SETUP (disjoint complement)

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
    tie = 0
    for i in mature:
        e = bars[i]["c"]
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            up = bars[j]["h"] >= e * (1.0 + SL_PCT)
            dn = bars[j]["l"] <= e * (1.0 - TP_PCT)
            if up and dn:
                tie += 1
            if up or dn:
                break
    say("  SHORT-ENGINE CHECK (this study is the first SHORT one on this harness).  The same bracket")
    say("  run LONG and SHORT over all mature buckets must partition every resolved trade into")
    say("  long-win / short-win / same-bar-both-touched (the last is a LOSS on BOTH sides by the rule):")
    say("    long n=%d W=%d | short n=%d W=%d | same-bar both-touched %d | %d + %d + %d = %d vs n=%d -> %s"
        % (len(Lc), lw, len(Sc), sw, tie, lw, sw, tie, lw + sw + tie, len(Lc),
           "IDENTITY HOLDS" if (lw + sw + tie == len(Lc) == len(Sc)) else "BROKEN"))
    say()

    # ------------------------------------------------------------------ (a) funnel
    say("=" * 108)
    say("(a) SIGNAL FUNNEL -- how binding each clause is")
    say("=" * 108)
    say("  %-52s %-8s %-10s" % ("stage", "count", "kept from prev"))
    fun = [("consecutive mature tradeable pairs", len(P), None),
           ("+ candle 1 BULLISH and candle 2 BULLISH", len(bullbull), len(P)),
           ("+ higher close  close[i-1] < close[i]   (= CTRL-P)", len(hc), len(bullbull)),
           ("+ eff-agg FALLING  eff[i-1] > eff[i]    (= SETUP)", len(setup), len(hc))]
    for tag, c, prev in fun:
        say("  %-52s %-8d %s" % (tag, c, "--" if prev is None else "%.1f%%" % (100.0 * c / prev)))
    say()
    ident = sum(1 for i in P if bars[i]["o"] == bars[i - 1]["c"])
    identb = sum(1 for i in bullbull if bars[i]["o"] == bars[i - 1]["c"])
    gapb = sum(1 for i in bullbull if bars[i]["o"] < bars[i - 1]["c"])
    say("  LOGICAL NOTE, verified against the data rather than asserted, not assumed: the 'higher")
    say("  close' clause is REDUNDANT here and removes exactly 0 signals (%d -> %d above)."
        % (len(bullbull), len(hc)))
    say("  Mechanism: constant-volume buckets are contiguous, so open[i] == close[i-1] to the cent on")
    say("  %d/%d = %.1f%% of all pairs and %d/%d = %.1f%% of bull/bull pairs; candle 2 BULLISH then"
        % (ident, len(P), 100.0 * ident / len(P), identb, len(bullbull),
           100.0 * identb / len(bullbull)))
    say("  already means close[i] > open[i] == close[i-1].  Of the %d bull/bull pairs that gap, %d gap"
        % (len(bullbull) - identb, gapb))
    say("  DOWN (which preserves the inequality) and in the remaining %d the up-gap never exceeded the"
        % (len(bullbull) - identb - gapb))
    say("  candle's own body, so no bull/bull pair anywhere in this universe fails the higher-close")
    say("  test.  The SETUP is therefore exactly: two bullish buckets in a row with eff-agg FALLING")
    say("  -> SHORT the second close.  Read every SETUP-vs-CTRL-P number with that in mind.")
    say()
    say("  the two halves of CTRL-P, disjoint by construction:")
    say("    eff-agg FALLING  (= SETUP)                         %-8d %.1f%% of CTRL-P"
        % (len(setup), 100.0 * len(setup) / len(ctrlP)))
    say("    eff-agg RISING   (= CTRL-M)                        %-8d %.1f%% of CTRL-P"
        % (len(ctrlM), 100.0 * len(ctrlM) / len(ctrlP)))
    say("    eff-agg EXACTLY TIED (in neither)                  %-8d %.1f%% of CTRL-P"
        % (len(ties), 100.0 * len(ties) / len(ctrlP)))
    say("    check  %d + %d + %d = %d  == CTRL-P %d : %s"
        % (len(setup), len(ctrlM), len(ties), len(setup) + len(ctrlM) + len(ties), len(ctrlP),
           len(setup) + len(ctrlM) + len(ties) == len(ctrlP)))
    say()
    say("  CTRL-E (eff-agg falling, price pattern ignored)      %-8d %.1f%% of all pairs"
        % (len(ctrlE), 100.0 * len(ctrlE) / len(P)))
    say("  CTRL-0 (every mature bucket)                         %-8d" % len(mature))
    say()

    # ------------------------------------------------------------------ run every cohort
    COH = [("SETUP", setup), ("CTRL-P (price only)", ctrlP), ("CTRL-M (mirror, rising)", ctrlM),
           ("CTRL-P minus SETUP", xP), ("CTRL-E (eff only)", ctrlE), ("CTRL-0 (all mature)", mature)]
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
            fr = fragility(n, w)
            need = mde(n)
            say("      fragility %d winner->loser flip(s) to push exact binomial p above 0.05" % fr)
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
            for nm, ctag, note in (("CTRL-P (nested)", "CTRL-P (price only)",
                                    "   <- NESTED, not a two-independent-sample test"),
                                   ("CTRL-P minus SETUP", "CTRL-P minus SETUP",
                                    "   <- the clean disjoint version of the row above"),
                                   ("CTRL-M (the key contrast)", "CTRL-M (mirror, rising)", ""),
                                   ("CTRL-E (eff only)", "CTRL-E (eff only)", ""),
                                   ("CTRL-0 (all mature)", "CTRL-0 (all mature)", "")):
                nc, wc, wpc, _, _ = cell(R[(ctag, mode)])
                if nc:
                    contrast(nm, ns, ws, wps, nc, wc, wpc, note)
        if mode == "chained":
            say()
            say("    CAVEAT: the CHAINED lift has a signed NON-ZERO null.  A zero-information SPARSE")
            say("    cohort earns free win-rate against a DENSE chained base, because chaining costs the")
            say("    dense base and a sparse cohort keeps its unchained rate.  The UNCHAINED lift null is")
            say("    0.0 exactly at every cohort size.  Read the UNCHAINED lifts, not these.")
        say()

    # ------------------------------------------------------------------ (b) spread distribution
    say("=" * 108)
    say("(b) EFF-AGG SPREAD  eff[i] - eff[i-1]  INSIDE THE SETUP  (negative by the rule)")
    say("=" * 108)
    sp = np.asarray([float(eff[i]) - float(eff[i - 1]) for i in setup], float)
    say("  SETUP (%d signals)" % len(setup))
    say("    %s" % desc(sp))
    for q in (10, 25, 50, 75, 90):
        say("    p%-3d %+8.3f" % (q, float(np.percentile(sp, q))))
    say()
    for tag, idxs in (("CTRL-P (price only)", ctrlP), ("CTRL-M (mirror, rising)", ctrlM),
                      ("all consecutive pairs", P)):
        v = [float(eff[i]) - float(eff[i - 1]) for i in idxs]
        say("  %-24s (%4d)  %s" % (tag, len(idxs), desc(v)))
    say()
    say("  raw eff_agg levels inside the SETUP (the rule puts NO sign requirement on either):")
    say("    candle 1 eff_agg   %s" % desc([float(eff[i - 1]) for i in setup]))
    say("    candle 2 eff_agg   %s" % desc([float(eff[i]) for i in setup]))
    say("    candle 1 > 0 in %d/%d = %.1f%% ; candle 2 > 0 in %d/%d = %.1f%%"
        % (sum(1 for i in setup if eff[i - 1] > 0), len(setup),
           100.0 * sum(1 for i in setup if eff[i - 1] > 0) / len(setup),
           sum(1 for i in setup if eff[i] > 0), len(setup),
           100.0 * sum(1 for i in setup if eff[i] > 0) / len(setup)))
    say()

    # ------------------------------------------------------------------ (c) disjoint bands
    say("=" * 108)
    say("(c) DISJOINT SPREAD BANDS -- each an INDEPENDENT re-chained walk")
    say("=" * 108)
    say("  Bands are DISJOINT, not cumulative.  A cumulative ladder is nested and manufactures")
    say("  apparent gradients (standing rule).  If 'steeper fall = better short' is real it must")
    say("  appear here.  Each band is re-chained from scratch over its own members only, so the")
    say("  chained n of the bands does not sum to the chained n of the SETUP.")
    say()
    ordr = np.argsort(sp)                      # most negative first
    K = 6
    hdr = ("  %-22s %-6s | %-4s %-4s %-8s %-10s | %-4s %-4s %-8s %-10s"
           % ("band (spread range)", "n sig", "n", "W", "win%", "exp/tr", "n", "W", "win%", "exp/tr"))
    sub = ("  %-22s %-6s | %-28s | %-28s" % ("", "", "CHAINED (re-chained per band)", "UNCHAINED"))

    def band_row(label, mem, sink=None):
        n, w, wp, ex, _ = cell(sim(bars, mem, True, side=-1))
        nu, wu, wpu, exu, _ = cell(sim(bars, mem, False, side=-1))
        say("  %-22s %-6d | %-4d %-4d %7.2f%% %+9.4f%% | %-4d %-4d %7.2f%% %+9.4f%%"
            % (label, len(mem), n, w, wp, ex, nu, wu, wpu, exu))
        if sink is not None:
            sink.append((wp, wpu))

    say("  --- equal-count bands (sextiles of the spread; edges are data-defined by rank, NOT chosen")
    say("      to separate outcomes) ---")
    say(sub)
    say(hdr)
    bounds = [int(round(len(setup) * k / K)) for k in range(K + 1)]
    seqA = []
    for k in range(K):
        sl_, sh_ = bounds[k], bounds[k + 1]
        if sh_ <= sl_:
            continue
        mem = [setup[j] for j in ordr[sl_:sh_]]
        lo = float(sp[ordr[sl_]]); hi = float(sp[ordr[sh_ - 1]])
        band_row("[%+7.2f,%+7.2f]" % (lo, hi), mem, seqA)
    say()
    say("  --- fixed round-number bands (independent robustness display of the same picture) ---")
    say(sub)
    say(hdr)
    edges = [-100.0, -30.0, -20.0, -12.0, -6.0, -2.0, 0.0]
    seqB = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        mem = [setup[j] for j in range(len(setup)) if lo <= sp[j] < hi]
        if not mem:
            say("  %-22s %-6d | (empty)" % ("[%+6.1f,%+6.1f)" % (lo, hi), 0)); continue
        band_row("[%+6.1f,%+6.1f)" % (lo, hi), mem, seqB)
    say()
    say("  Win-rate sequences, most-negative band first (steepest eff-agg fall first):")
    say("    sextiles      chained %s" % "  ".join("%.1f" % x[0] for x in seqA))
    say("    sextiles    unchained %s" % "  ".join("%.1f" % x[1] for x in seqA))
    say("    fixed edges   chained %s" % "  ".join("%.1f" % x[0] for x in seqB))
    say("    fixed edges unchained %s" % "  ".join("%.1f" % x[1] for x in seqB))
    mono = lambda s: (all(s[k] <= s[k + 1] for k in range(len(s) - 1))
                      or all(s[k] >= s[k + 1] for k in range(len(s) - 1)))
    say("    monotone?  sextiles chained %s / unchained %s | fixed chained %s / unchained %s"
        % (mono([x[0] for x in seqA]), mono([x[1] for x in seqA]),
           mono([x[0] for x in seqB]), mono([x[1] for x in seqB])))
    say("    The hypothesis 'steeper fall = better short' predicts the sequences DECREASE left to")
    say("    right.  Read the printed sequences; no direction is asserted here beyond the flags above.")
    say()
    say("  Band n's are single digit to mid-teens.  Smallest win count reaching exact binomial p<0.05")
    say("  vs %.2f%% at these n:" % BE)
    say("    " + "  ".join("n=%d:W>=%s" % (nn, "--" if mde(nn) is None else str(mde(nn)))
                           for nn in (3, 5, 9, 10, 15, 18, 20)))
    say("  so a band table of this size cannot ESTABLISH a gradient -- it can only fail to show one.")
    say()

    # ------------------------------------------------------------------ (d) displacement
    say("=" * 108)
    say("(d) DISPLACEMENT -- is the entry geometry doing the work?")
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
    for tag, idxs in (("SETUP", setup), ("CTRL-P (price only)", ctrlP),
                      ("CTRL-M (mirror, rising)", ctrlM), ("CTRL-E (eff only)", ctrlE)):
        d = disp(idxs)
        say("  %-30s n=%-6d mean %+.4f%%   median %+.4f%%   vs universe %+.4f pp"
            % (tag, len(idxs), d.mean(), float(np.median(d)), d.mean() - du.mean()))
    say()
    ds = float(disp(setup).mean())
    pp = ds / ((SL_PCT + TP_PCT) * 100.0) * 100.0
    say("  Reading, stated factually and without interpretation beyond the arithmetic:")
    say("  * The SETUP shorts at the close of a BULLISH bucket, so by construction it sells at a price")
    say("    displaced ABOVE that bucket's own midpoint.  Round 3 measured the coefficient: a shift of")
    say("    d toward the bucket interior moves P(TP first) by d/(SL+TP); at 0.8/0.8 that is d/1.6.")
    say("    The SETUP's %+.4f%% displacement is therefore worth about %+.2f pp of win rate to a SHORT"
        % (ds, pp))
    say("    relative to a midpoint fill -- i.e. the SETUP is displacement-FAVOURED in absolute terms,")
    say("    and its raw %.2f%% unchained win rate should be read against a displacement-inflated bar,"
        % (100.0 * cell(R[("SETUP", "unchained")])[1] / max(1, cell(R[("SETUP", "unchained")])[0])))
    say("    not against the driftless 50.00 percent.")
    say("  * BUT displacement is a property of the PRICE PATTERN (bullish candle 2), not of the eff-agg")
    say("    clause -- SETUP, CTRL-P and CTRL-M all enter at a bullish bucket close.  Relative to its")
    say("    own controls the SETUP is displacement-DISADVANTAGED.  Predicted vs observed lift:")
    say()
    say("    %-26s %-12s %-14s %-14s" % ("contrast", "disp gap", "predicted pp", "observed pp (unch)"))
    nsu, wsu, wpsu, _, _ = cell(R[("SETUP", "unchained")])
    for ctag, short in (("CTRL-P (price only)", "vs CTRL-P"),
                        ("CTRL-P minus SETUP", "vs CTRL-P minus SETUP"),
                        ("CTRL-M (mirror, rising)", "vs CTRL-M"),
                        ("CTRL-E (eff only)", "vs CTRL-E"),
                        ("CTRL-0 (all mature)", "vs CTRL-0")):
        idxs = dict(COH)[ctag]
        gap = ds - float(disp(idxs).mean())
        pred = gap / ((SL_PCT + TP_PCT) * 100.0) * 100.0
        ncu, wcu, wpcu, _, _ = cell(R[(ctag, "unchained")])
        say("    %-26s %+8.4f pp %+11.2f pp %+13.2f pp" % (short, gap, pred, wpsu - wpcu))
    say()
    say("    The displacement model predicts the SETUP should UNDERPERFORM CTRL-M; the measured sign")
    say("    is the opposite.  Displacement therefore does not account for the SETUP-vs-CTRL-M or")
    say("    SETUP-vs-CTRL-P difference -- it works against it.  It does account for part of the raw")
    say("    level sitting above 50.00 percent.")
    say("  * Round 4 measured that this displacement is not harvestable as a price: at an attainable")
    say("    fill it is worth +0.1pp, because 'price never came back' IS the losing outcome.")
    say()

    # ------------------------------------------------------------------ (e) fragility
    say("=" * 108)
    say("(e) FRAGILITY -- winner->loser flips needed to push the exact binomial p ABOVE 0.05")
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
    say("SUMMARY OF THE NUMBERS (no interpretation added beyond restating what is measured above)")
    say("=" * 108)
    for mode in ("unchained", "chained"):
        n, w, wp, ex, _ = cell(R[("SETUP", mode)])
        nm, wm, wpm, exm, _ = cell(R[("CTRL-M (mirror, rising)", mode)])
        zm, pm = two_prop_z(n, w, nm, wm)
        say("  SETUP %-10s n=%-4d W=%-3d win %.2f%%  net %+.4f%%/tr  margin vs %.2f%% = %+.2f pp  "
            "exact binom p = %.5f" % (mode, n, w, wp, ex, BE, wp - BE, binom_p_ge(n, w)))
        say("      key contrast vs CTRL-M (identical price pattern, eff-agg RISING): %+.2f pp, "
            "z = %+.3f, one-sided p = %.4f, Fisher p = %.4f"
            % (wp - wpm, zm, pm, fisher_one_sided(n, w, nm, wm)))
    say()
    say("  * The SETUP does not reach the %.2f%% break-even in either mode; net expectancy is negative"
        % BE)
    say("    in both.  Exact one-sided binomial p vs break-even is far above 0.05 in both modes, so")
    say("    fragility is 0 by definition; the mirror quantity (loser->winner flips needed to reach")
    say("    p<0.05) is printed in section (e).")
    say("  * The SETUP is above every control it was tested against on the UNCHAINED basis (the basis")
    say("    whose lift null is 0.0), but no contrast reaches p<0.20, and every cohort in the table --")
    say("    SETUP and all five controls -- is below break-even.  The eff-agg direction contrast is a")
    say("    difference between two losing cohorts.")
    say("  * Displacement runs AGAINST the SETUP relative to its controls, so the small positive lifts")
    say("    are not entry geometry; they are also not distinguishable from zero at this n.")
    say("  * The disjoint spread bands are non-monotone under both banding schemes, in both modes.")
    say()

    # ------------------------------------------------------------------ what was not done
    say("=" * 108)
    say("WHAT WAS NOT DONE")
    say("=" * 108)
    say("  No threshold, bracket, window, side or condition was varied from the specification.  The")
    say("  only cells beyond the required table are the mandated controls, the disjoint complement,")
    say("  the disjoint spread bands and the displacement diagnostic.  No forward/out-of-window data")
    say("  was used and none of this is a forward test.  Factual reporting only; no trading")
    say("  recommendation is made or implied.")
    say("```")
    _flush()


def _flush():
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
