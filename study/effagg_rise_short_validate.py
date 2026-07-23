"""EFF-AGG RISING + LOWER CLOSE -> SHORT.  Pre-specified spec, implemented VERBATIM. Nothing swept.

    Universe    mature constant-volume 1h buckets (index >= FM.build()'s `first`), tradeable
                (o>0, c>0, h>l).  A signal needs TWO CONSECUTIVE mature tradeable buckets:
                candle 1 = i-1, candle 2 = i.   2618 -> 1262 mature -> 1261 consecutive pairs.
                (NOT the 926-bucket 1m-dependent universe.)
    eff_agg     (2 * app.pivot_detect.eff_causal_share(W) - 1) * 100, range -100..+100.
                NON-LOCKED / first-print / CAUSAL variant ONLY.  Computed ONCE over the FULL bucket
                list (prefix-stable, so no warm-up slicing).
    SETUP       eff_agg[i-1] < eff_agg[i]      <- eff-agg RISING (no sign requirement on either)
                close[i]     < close[i-1]      <- LOWER close
                NO candle-direction condition on either candle.  None is added anywhere.
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
    CTRL-P      price only:  close[i] < close[i-1]                   (NO eff-agg condition)
    CTRL-M      MIRROR:      close[i] < close[i-1] AND eff-agg FALLING (eff[i-1] > eff[i])
    CTRL-E      eff-agg alone: eff_agg[i-1] < eff_agg[i]             (NO price condition)
    CTRL-0      SHORT at every mature bucket close

REPORTING CONVENTIONS (project standing rules; study/out/gemini_round3_reply.md, _round4_reply.md):
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

Run:  python study/effagg_rise_short_validate.py
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

OUT_MD = os.path.join(HERE, "out", "effagg_rise_short_results.md")
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
        return "  %-32s n=0" % tag
    return "  %-32s n=%-5d W=%-5d %6.2f%%  %+.4f%%%s" % (tag, n, w, wp, exp, extra)


def full_row(tag, T):
    n, w, wp, exp, r = cell(T)
    if n == 0:
        say("  %-32s n=0  (no resolved trades)" % tag)
        return
    say("  %-32s n=%-5d W=%-5d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   "
        "binom p = %.5f" % (tag, n, w, wp, exp, BE, wp - BE, binom_p_ge(n, w)))


def contrast(name, ns, ws, wps, nc, wc, wpc, note=""):
    z, p = two_prop_z(ns, ws, nc, wc)
    fp = fisher_one_sided(ns, ws, nc, wc)
    say("    LIFT vs %-26s %+6.2f pp   two-prop z = %+.3f  one-sided p = %.4f   "
        "Fisher exact one-sided p = %.4f%s" % (name, wps - wpc, z, p, fp, note))


def main():
    say("# EFF-AGG RISING + LOWER CLOSE -> SHORT -- results")
    say()
    say("Pre-specified spec implemented verbatim; nothing swept, no threshold or bracket varied.")
    say("SETUP = eff_agg[i-1] < eff_agg[i] (RISING) AND close[i] < close[i-1] (LOWER close),")
    say("SHORT at candle 2's close.  THERE IS NO CANDLE-DIRECTION CONDITION and none was added.")
    say("Fixed %.1f%% stop / %.1f%% target (1:1), flat %.2f%% round-trip fee.  Winner nets %+.2f%%,"
        % (SL_PCT * 100, TP_PCT * 100, FEE * 100, (TP_PCT - FEE) * 100))
    say("loser %+.2f%%.  BREAK-EVEN WIN RATE = %.4f%%.  Driftless P(TP first) = 50.00%%."
        % ((-SL_PCT - FEE) * 100, BE))
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
    lc, setup, ctrlM, ties, ctrlE = [], [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        if ea < eb:
            ctrlE.append(i)                        # = CTRL-E (eff-agg rising, price ignored)
        if b["c"] < a["c"]:
            lc.append(i)                           # = CTRL-P (lower close)
            if ea < eb:
                setup.append(i)                    # = SETUP  (eff rising)
            elif ea > eb:
                ctrlM.append(i)                    # = CTRL-M (eff falling)
            else:
                ties.append(i)                     # exact tie -> in neither
    ctrlP = lc
    SET = set(setup)
    xP = [i for i in ctrlP if i not in SET]        # CTRL-P MINUS SETUP (disjoint complement)

    def c2dir(i):
        b = bars[i]
        return 1 if b["c"] > b["o"] else (-1 if b["c"] < b["o"] else 0)

    bull = [i for i in setup if c2dir(i) > 0]
    bear = [i for i in setup if c2dir(i) < 0]
    flat = [i for i in setup if c2dir(i) == 0]

    say("=" * 110)
    say("UNIVERSE")
    say("=" * 110)
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
    Lc = sim(bars, mature, False, side=+1)
    Sc = sim(bars, mature, False, side=-1)
    lw = sum(1 for x in Lc if x["win"]); sw = sum(1 for x in Sc if x["win"])
    tie_both = 0
    for i in mature:
        e = bars[i]["c"]
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            up = bars[j]["h"] >= e * (1.0 + SL_PCT)
            dn = bars[j]["l"] <= e * (1.0 - TP_PCT)
            if up and dn:
                tie_both += 1
            if up or dn:
                break
    say("  SHORT-ENGINE CHECK.  The same bracket run LONG and SHORT over all mature buckets must")
    say("  partition every resolved trade into long-win / short-win / same-bar-both-touched (the last")
    say("  is a LOSS on BOTH sides by the rule):")
    say("    long n=%d W=%d | short n=%d W=%d | same-bar both-touched %d | %d + %d + %d = %d vs n=%d"
        " -> %s"
        % (len(Lc), lw, len(Sc), sw, tie_both, lw, sw, tie_both, lw + sw + tie_both, len(Lc),
           "IDENTITY HOLDS" if (lw + sw + tie_both == len(Lc) == len(Sc)) else "BROKEN"))
    say()

    # ------------------------------------------------------------------ run every cohort
    COH = [("SETUP", setup),
           ("SETUP c2 BULLISH", bull),
           ("SETUP c2 BEARISH", bear),
           ("SETUP c2 FLAT", flat),
           ("CTRL-P (price only)", ctrlP),
           ("CTRL-P c2 BULLISH", [i for i in ctrlP if c2dir(i) > 0]),
           ("CTRL-P c2 BEARISH", [i for i in ctrlP if c2dir(i) < 0]),
           ("CTRL-M (mirror, falling)", ctrlM),
           ("CTRL-M c2 BULLISH", [i for i in ctrlM if c2dir(i) > 0]),
           ("CTRL-M c2 BEARISH", [i for i in ctrlM if c2dir(i) < 0]),
           ("CTRL-P minus SETUP", xP),
           ("CTRL-E (eff only)", ctrlE),
           ("CTRL-E c2 BULLISH", [i for i in ctrlE if c2dir(i) > 0]),
           ("CTRL-E c2 BEARISH", [i for i in ctrlE if c2dir(i) < 0]),
           ("CTRL-0 (all mature)", mature)]
    CD = dict(COH)
    R = {}
    for tag, idxs in COH:
        R[(tag, "unchained")] = sim(bars, idxs, False, side=-1)
        R[(tag, "chained")] = sim(bars, idxs, True, side=-1)

    # ------------------------------------------------------------------ displacement helper
    def disp(idxs):
        """dir-signed: dir * (close - midpoint)/close * 100, dir = +1 bullish, -1 bearish, 0 flat.
        Positive = the bucket closed at the leading edge of its OWN move."""
        v = []
        for i in idxs:
            b = bars[i]
            d = 1.0 if b["c"] > b["o"] else (-1.0 if b["c"] < b["o"] else 0.0)
            v.append(d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0)
        return np.asarray(v, float)

    def disp_short(idxs):
        """SHORT-signed: (close - midpoint)/close * 100.  Positive = selling ABOVE the bucket's own
        midpoint = FAVOURABLE for a short.  Negative = selling below it = ADVERSE."""
        v = []
        for i in idxs:
            b = bars[i]
            v.append((b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0)
        return np.asarray(v, float)

    du = disp(mature)

    # =================================================================== THE SPLIT (FIRST)
    say("=" * 110)
    say("*** THE SPLIT THAT MATTERS MOST -- SETUP BY CANDLE-2 DIRECTION ***")
    say("=" * 110)
    say("  The rule places NO condition on candle 2's direction, so the cohort MIXES two structurally")
    say("  different trades:")
    say("    SHORT after a BULLISH candle 2 -> sells near a local HIGH -> FAVOURABLE side of the")
    say("                                      close displacement")
    say("    SHORT after a BEARISH candle 2 -> sells near a local LOW  -> ADVERSE side")
    say()
    say("  MIX of the %d SETUP signals:" % len(setup))
    for tg, mem in (("candle 2 BULLISH (c > o)", bull), ("candle 2 BEARISH (c < o)", bear),
                    ("candle 2 FLAT    (c == o)", flat)):
        say("    %-28s %-6d %6.2f%% of the cohort"
            % (tg, len(mem), 100.0 * len(mem) / len(setup) if setup else float("nan")))
    say("    check  %d + %d + %d = %d == %d : %s"
        % (len(bull), len(bear), len(flat), len(bull) + len(bear) + len(flat), len(setup),
           len(bull) + len(bear) + len(flat) == len(setup)))
    say()
    ident = sum(1 for i in P if bars[i]["o"] == bars[i - 1]["c"])
    identl = sum(1 for i in ctrlP if bars[i]["o"] == bars[i - 1]["c"])
    say("  MECHANISM OF THE MIX, measured not asserted.  Constant-volume buckets are contiguous, so")
    say("  open[i] == close[i-1] to the cent on %d/%d = %.1f%% of all pairs and %d/%d = %.1f%% of"
        % (ident, len(P), 100.0 * ident / len(P), identl, len(ctrlP),
           100.0 * identl / len(ctrlP)))
    say("  lower-close pairs.  When the open equals the previous close, 'lower close' is IDENTICAL to")
    say("  'candle 2 bearish'.  A bullish candle 2 with a lower close can therefore only arise from a")
    say("  DOWN-gap that the candle failed to fill.  That is why the mix is lopsided.")
    say()
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        for tg in ("SETUP", "SETUP c2 BULLISH", "SETUP c2 BEARISH", "SETUP c2 FLAT"):
            full_row(tg, R[(tg, mode)])
        nb, wb, wpb, _, _ = cell(R[("SETUP c2 BULLISH", mode)])
        nr, wr, wpr, _, _ = cell(R[("SETUP c2 BEARISH", mode)])
        if nb and nr:
            z, p = two_prop_z(nb, wb, nr, wr)
            fp = fisher_one_sided(nb, wb, nr, wr)
            say("    BULLISH-c2 minus BEARISH-c2  %+6.2f pp   two-prop z = %+.3f  one-sided p = %.4f"
                "   Fisher exact one-sided p = %.4f" % (wpb - wpr, z, p, fp))
        say()
    say("  DISPLACEMENT OF EACH SUB-COHORT")
    say("  metric 1 (dir-signed, comparable to the published universe figure):")
    say("      dir * (close - (high+low)/2) / close * 100,  dir = +1 bullish, -1 bearish, 0 flat.")
    say("      Published 1262-bucket universe mean: %+.4f%%." % UNIVERSE_DISP_PUBLISHED)
    say("  metric 2 (SHORT-signed, the one that decides favourable vs adverse for THIS trade):")
    say("      (close - (high+low)/2) / close * 100.  Positive = sold ABOVE the bucket midpoint.")
    say()
    say("  %-26s %-7s %-24s %-24s" % ("cohort", "n sig", "metric 1 dir-signed", "metric 2 short-signed"))
    say("  %-26s %-7s %-24s %-24s" % ("", "", "mean      vs universe", "mean      implied pp"))
    say("  %-26s %-7d %+9.4f%% %+11.4f pp %+9.4f%% %+11.2f pp"
        % ("UNIVERSE (all mature)", len(mature), du.mean(), du.mean() - UNIVERSE_DISP_PUBLISHED,
           float(disp_short(mature).mean()), float(disp_short(mature).mean()) / 1.6 * 100.0))
    say("  %-26s          published %+.4f%% -> %s"
        % ("", UNIVERSE_DISP_PUBLISHED,
           "MATCH" if abs(du.mean() - UNIVERSE_DISP_PUBLISHED) < 5e-4 else "DIFFERS"))
    for tg, mem in (("SETUP (all)", setup), ("SETUP c2 BULLISH", bull),
                    ("SETUP c2 BEARISH", bear), ("SETUP c2 FLAT", flat),
                    ("CTRL-P (price only)", ctrlP), ("CTRL-M (mirror, falling)", ctrlM)):
        if not mem:
            say("  %-26s %-7d (empty)" % (tg, 0)); continue
        d1 = disp(mem); d2 = disp_short(mem)
        say("  %-26s %-7d %+9.4f%% %+11.4f pp %+9.4f%% %+11.2f pp"
            % (tg, len(mem), d1.mean(), d1.mean() - UNIVERSE_DISP_PUBLISHED,
               d2.mean(), d2.mean() / 1.6 * 100.0))
    say()
    say("  'implied pp' uses the Round-3 coefficient: a shift of d percent toward the bucket interior")
    say("  moves P(TP first) by d/(SL+TP); at 0.8/0.8 that is d/1.6, i.e. d/1.6*100 percentage points.")
    say("  It is an arithmetic conversion of the measured displacement, not a fitted quantity.")
    if bull and bear:
        gap = float(disp_short(bull).mean() - disp_short(bear).mean())
        say("  SHORT-signed displacement GAP between the two halves: %+.4f%% -> %+.2f pp implied."
            % (gap, gap / 1.6 * 100.0))
    say()
    nbu, wbu, wpbu, exbu, _ = cell(R[("SETUP c2 BULLISH", "unchained")])
    nru, wru, wpru, exru, _ = cell(R[("SETUP c2 BEARISH", "unchained")])
    nbc, wbc, wpbc, exbc, _ = cell(R[("SETUP c2 BULLISH", "chained")])
    nrc, wrc, wprc, exrc, _ = cell(R[("SETUP c2 BEARISH", "chained")])
    say("  READING (numbers only):")
    if nbu and nru:
        say("    unchained  BULLISH-c2 %d/%d = %.2f%% (net %+.4f%%/tr)   BEARISH-c2 %d/%d = %.2f%% "
            "(net %+.4f%%/tr)   difference %+.2f pp"
            % (wbu, nbu, wpbu, exbu, wru, nru, wpru, exru, wpbu - wpru))
        say("    chained    BULLISH-c2 %d/%d = %.2f%% (net %+.4f%%/tr)   BEARISH-c2 %d/%d = %.2f%% "
            "(net %+.4f%%/tr)   difference %+.2f pp"
            % (wbc, nbc, wpbc, exbc, wrc, nrc, wprc, exrc, wpbc - wprc))
        big = abs(wpbu - wpru) >= 10.0
        say("    The two halves differ by %+.2f pp unchained and %+.2f pp chained.  %s"
            % (wpbu - wpru, wpbc - wprc,
               "This is a MATERIAL difference (>= 10 pp): the aggregate SETUP row is a BLEND of two"
               if big else
               "This is a difference below 10 pp; the aggregate is still a blend of two"))
        say("    structurally different trades and must be read as such, weighted %.1f%% / %.1f%%"
            % (100.0 * nbu / (nbu + nru), 100.0 * nru / (nbu + nru)))
        say("    (unchained resolved-trade weights).  A single headline win rate for the SETUP is a")
        say("    weighted average of the two rows above and is not the win rate of either trade.")
    say()

    # =================================================================== PRIMARY
    say("=" * 110)
    say("PRIMARY -- SETUP, BOTH MODES")
    say("=" * 110)
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        full_row("SETUP", R[("SETUP", mode)])
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

    # =================================================================== CONTROLS
    say("=" * 110)
    say("CONTROLS -- all SHORT, same bracket, same entry, same universe, same mode")
    say("=" * 110)
    say("  resolved-trade counts (signals -> n differs only by trades left UNRESOLVED at the end of")
    say("  data, which are DROPPED; n chained is smaller because a signal at or before the prior exit")
    say("  bar is skipped entirely):")
    say("  %-32s %-10s %-12s %-10s" % ("cohort", "signals", "n unchained", "n chained"))
    for tag, idxs in COH:
        say("  %-32s %-10d %-12d %-10d" % (tag, len(idxs), len(R[(tag, "unchained")]),
                                           len(R[(tag, "chained")])))
    say()
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
                    ("CTRL-P (nested)", "CTRL-P (price only)",
                     "   <- NESTED, not a two-independent-sample test"),
                    ("CTRL-P minus SETUP", "CTRL-P minus SETUP",
                     "   <- REQUIRED disjoint complement of CTRL-P"),
                    ("CTRL-M (key contrast)", "CTRL-M (mirror, falling)", ""),
                    ("CTRL-E (eff only)", "CTRL-E (eff only)", ""),
                    ("CTRL-0 (all mature)", "CTRL-0 (all mature)", "")):
                nc, wc, wpc, _, _ = cell(R[(ctag, mode)])
                if nc:
                    contrast(nm, ns, ws, wps, nc, wc, wpc, note)
        say()
        say("    LIKE-FOR-LIKE ON DISPLACEMENT -- the same contrasts computed WITHIN each candle-2")
        say("    direction, so both sides of every comparison enter at the same kind of close:")
        for suf in ("BULLISH", "BEARISH"):
            nss, wss, wpss, _, _ = cell(R[("SETUP c2 %s" % suf, mode)])
            if not nss:
                say("      c2 %s: SETUP n=0 -- no contrast computable" % suf); continue
            for ctag in ("CTRL-P c2 %s" % suf, "CTRL-M c2 %s" % suf):
                nc, wc, wpc, _, _ = cell(R[(ctag, mode)])
                if not nc:
                    say("      c2 %-7s SETUP vs %-22s control n=0" % (suf, ctag)); continue
                z, p = two_prop_z(nss, wss, nc, wc)
                fp = fisher_one_sided(nss, wss, nc, wc)
                say("      c2 %-7s SETUP %5.2f%% (n=%-4d) vs %-22s %5.2f%% (n=%-4d)  %+6.2f pp  "
                    "z=%+.3f p=%.4f  Fisher p=%.4f"
                    % (suf, wpss, nss, ctag, wpc, nc, wpss - wpc, z, p, fp))
        if mode == "chained":
            say()
            say("    CAVEAT: the CHAINED lift has a signed NON-ZERO null.  A zero-information SPARSE")
            say("    cohort earns free win-rate against a DENSE chained base, because chaining costs")
            say("    the dense base and a sparse cohort keeps its unchained rate.  The UNCHAINED lift")
            say("    null is 0.0 exactly at every cohort size.  Read the UNCHAINED lifts, not these.")
        say()

    # =================================================================== (a) funnel
    say("=" * 110)
    say("(a) SIGNAL FUNNEL -- how binding each clause is")
    say("=" * 110)
    say("  %-52s %-8s %-10s" % ("stage", "count", "kept from prev"))
    fun = [("consecutive mature tradeable pairs", len(P), None),
           ("+ LOWER close  close[i] < close[i-1]    (= CTRL-P)", len(ctrlP), len(P)),
           ("+ eff-agg RISING  eff[i-1] < eff[i]     (= SETUP)", len(setup), len(ctrlP))]
    for tag, c, prev in fun:
        say("  %-52s %-8d %s" % (tag, c, "--" if prev is None else "%.1f%%" % (100.0 * c / prev)))
    say()
    say("  the three parts of CTRL-P, disjoint by construction:")
    say("    eff-agg RISING   (= SETUP)                         %-8d %.1f%% of CTRL-P"
        % (len(setup), 100.0 * len(setup) / len(ctrlP)))
    say("    eff-agg FALLING  (= CTRL-M)                        %-8d %.1f%% of CTRL-P"
        % (len(ctrlM), 100.0 * len(ctrlM) / len(ctrlP)))
    say("    eff-agg EXACTLY TIED (in neither)                  %-8d %.1f%% of CTRL-P"
        % (len(ties), 100.0 * len(ties) / len(ctrlP)))
    say("    check  %d + %d + %d = %d  == CTRL-P %d : %s"
        % (len(setup), len(ctrlM), len(ties), len(setup) + len(ctrlM) + len(ties), len(ctrlP),
           len(setup) + len(ctrlM) + len(ties) == len(ctrlP)))
    say("    NOTE: CTRL-P MINUS SETUP (%d) = CTRL-M (%d) + ties (%d): %s"
        % (len(xP), len(ctrlM), len(ties), len(xP) == len(ctrlM) + len(ties)))
    say()
    say("  CTRL-E (eff-agg rising, price ignored)               %-8d %.1f%% of all pairs"
        % (len(ctrlE), 100.0 * len(ctrlE) / len(P)))
    say("  CTRL-0 (every mature bucket)                         %-8d" % len(mature))
    say()
    say("  candle-2 direction inside each cohort (the split the rule does not make):")
    say("  %-28s %-8s %-8s %-8s" % ("cohort", "bull c2", "bear c2", "flat c2"))
    for tg, mem in (("all consecutive pairs", P), ("CTRL-P (lower close)", ctrlP),
                    ("SETUP", setup), ("CTRL-M", ctrlM), ("CTRL-E", ctrlE),
                    ("CTRL-0 (all mature)", mature)):
        nb = sum(1 for i in mem if c2dir(i) > 0)
        nr = sum(1 for i in mem if c2dir(i) < 0)
        nf = sum(1 for i in mem if c2dir(i) == 0)
        say("  %-28s %-8d %-8d %-8d" % (tg, nb, nr, nf))
    say()

    # =================================================================== (b) spread distribution
    say("=" * 110)
    say("(b) EFF-AGG SPREAD  eff[i] - eff[i-1]  INSIDE THE SETUP  (positive by the rule)")
    say("=" * 110)
    sp = np.asarray([float(eff[i]) - float(eff[i - 1]) for i in setup], float)
    say("  SETUP (%d signals)" % len(setup))
    say("    %s" % desc(sp))
    for q in (10, 25, 50, 75, 90):
        say("    p%-3d %+8.3f" % (q, float(np.percentile(sp, q))))
    say()
    for tag, idxs in (("SETUP c2 BULLISH", bull), ("SETUP c2 BEARISH", bear),
                      ("CTRL-P (price only)", ctrlP), ("CTRL-M (mirror, falling)", ctrlM),
                      ("all consecutive pairs", P)):
        v = [float(eff[i]) - float(eff[i - 1]) for i in idxs]
        say("  %-26s (%4d)  %s" % (tag, len(idxs), desc(v)))
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

    # =================================================================== (c) disjoint bands
    say("=" * 110)
    say("(c) DISJOINT SPREAD BANDS -- each an INDEPENDENT re-chained walk")
    say("=" * 110)
    say("  Bands are DISJOINT, not cumulative.  A cumulative ladder is nested and manufactures")
    say("  apparent gradients (standing rule).  If 'steeper rise = better short' is real it must")
    say("  appear here.  Each band is re-chained from scratch over its own members only, so the")
    say("  chained n of the bands does not sum to the chained n of the SETUP.")
    say("  Order is LEAST positive spread first, MOST positive last.")
    say()
    ordr = np.argsort(sp)                      # least positive first
    K = 6
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
    edges = [0.0, 2.0, 6.0, 12.0, 20.0, 30.0, 1000.0]
    seqB = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        mem = [setup[j] for j in range(len(setup)) if lo <= sp[j] < hi]
        lab = "[%+6.1f,%+6.1f)" % (lo, hi) if hi < 999 else "[%+6.1f,   inf)" % lo
        if not mem:
            say("  %-24s %-6d | (empty)" % (lab, 0)); continue
        band_row(lab, mem, seqB)
    say()
    say("  Win-rate sequences, LEAST positive band first (steepest eff-agg rise LAST):")
    say("    sextiles      chained %s" % "  ".join("%.1f" % x[0] for x in seqA))
    say("    sextiles    unchained %s" % "  ".join("%.1f" % x[1] for x in seqA))
    say("    fixed edges   chained %s" % "  ".join("%.1f" % x[0] for x in seqB))
    say("    fixed edges unchained %s" % "  ".join("%.1f" % x[1] for x in seqB))
    mono = lambda s: (all(s[k] <= s[k + 1] for k in range(len(s) - 1))
                      or all(s[k] >= s[k + 1] for k in range(len(s) - 1)))
    say("    monotone?  sextiles chained %s / unchained %s | fixed chained %s / unchained %s"
        % (mono([x[0] for x in seqA]), mono([x[1] for x in seqA]),
           mono([x[0] for x in seqB]), mono([x[1] for x in seqB])))
    say("    The hypothesis 'steeper rise = better short' predicts the sequences INCREASE left to")
    say("    right.  Read the printed sequences; no direction is asserted here beyond the flags.")
    say()
    say("  Smallest win count reaching exact binomial p<0.05 vs %.2f%% at band-sized n:" % BE)
    say("    " + "  ".join("n=%d:W>=%s" % (nn, "--" if mde(nn) is None else str(mde(nn)))
                           for nn in (3, 5, 9, 10, 15, 20, 30, 40)))
    say("  so a band table of this size cannot ESTABLISH a gradient -- it can only fail to show one.")
    say()

    # =================================================================== (d) prior study relation
    say("=" * 110)
    say("(d) RELATION TO THE PRIOR STUDY (effagg_seq_validate.py SETUP 2)")
    say("=" * 110)
    say("  PRIOR SETUP 2, restated exactly as it was specified:")
    say("      candle 1 BEARISH and eff_agg[i-1] > 0")
    say("      candle 2 BEARISH and eff_agg[i]   > 0")
    say("      eff_agg[i-1] < eff_agg[i]   (rising)")
    say("      close[i-1]   > close[i]     (lower close)")
    say("      -> taken LONG.  Published: n=15 unchained, 9 wins, 60.00%.")
    say("  PRESENT SETUP: eff-agg rising + lower close, taken SHORT, no candle-direction and no")
    say("  eff-agg sign requirement.  The prior rule is the present rule PLUS four extra clauses.")
    say()
    s2 = []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        if (a["c"] < a["o"]) and (b["c"] < b["o"]) and ea > 0 and eb > 0 and ea < eb and a["c"] > b["c"]:
            s2.append(i)
    S2 = set(s2)
    inter = sorted(SET & S2)
    say("  bucket-index set sizes")
    say("    PRESENT SETUP signals                    %d" % len(SET))
    say("    PRIOR SETUP 2 signals (reconstructed)    %d   (prior study printed 16 signals -> %s)"
        % (len(S2), "MATCH" if len(S2) == 16 else "DIFFERS"))
    say("    INTERSECTION                             %d" % len(inter))
    say("    PRIOR SETUP 2 is a SUBSET of PRESENT SETUP: %s" % (S2 <= SET))
    say("    PRESENT-only signals                     %d" % len(SET - S2))
    say()
    say("  PRESENT SETUP restricted to the intersection, taken SHORT (the present rule's side):")
    for mode, ch in (("unchained", False), ("chained", True)):
        n, w, wp, ex, _ = cell(sim(bars, inter, ch, side=-1))
        say("    %-10s n=%-4d W=%-3d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   "
            "binom p = %.5f" % (mode, n, w, wp, ex, BE, wp - BE, binom_p_ge(n, w)))
    say("  The SAME intersection taken LONG (the prior rule's side), for the direct comparison:")
    for mode, ch in (("unchained", False), ("chained", True)):
        n, w, wp, ex, _ = cell(sim(bars, inter, ch, side=+1))
        say("    %-10s n=%-4d W=%-3d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   "
            "binom p = %.5f" % (mode, n, w, wp, ex, BE, wp - BE, binom_p_ge(n, w)))
    nL, wL, wpL, _, _ = cell(sim(bars, inter, False, side=+1))
    nS, wS, wpS, _, _ = cell(sim(bars, inter, False, side=-1))
    tie_i = 0
    for i in inter:
        e = bars[i]["c"]
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            up = bars[j]["h"] >= e * (1.0 + TP_PCT)
            dn = bars[j]["l"] <= e * (1.0 - TP_PCT)
            if up and dn:
                tie_i += 1
            if up or dn:
                break
    say()
    say("  MIRROR IDENTITY on the intersection (0.8/0.8 is symmetric, so LONG and SHORT partition the")
    say("  same resolved trades): long W=%d + short W=%d + same-bar both-touched %d = %d vs n=%d -> %s"
        % (wL, wS, tie_i, wL + wS + tie_i, nL, "HOLDS" if wL + wS + tie_i == nL else "BROKEN"))
    say()
    say("  CONSISTENCY STATEMENT (arithmetic, not interpretation): on a symmetric bracket the short")
    say("  win rate on a given signal set is 100%% minus the long win rate minus the same-bar-both")
    say("  share.  The prior study's %d/%d = %.2f%% LONG on this set is therefore the SAME measurement"
        % (wL, nL, wpL))
    say("  as %d/%d = %.2f%% SHORT on this set, not an independent one.  Whatever the present SETUP"
        % (wS, nS, wpS))
    say("  earns overall, on these %d shared signals the two studies are in direct opposition by"
        % len(inter))
    say("  construction: a LONG edge there is a SHORT anti-edge of the same size.")
    say()
    n_pres, w_pres, wp_pres, _, _ = cell(R[("SETUP", "unchained")])
    rest = sorted(SET - S2)
    nr2, wr2, wpr2, exr2, _ = cell(sim(bars, rest, False, side=-1))
    say("  PRESENT SETUP with the prior SETUP 2 signals REMOVED (unchained, SHORT):")
    say("    n=%-4d W=%-3d win %6.2f%%   net %+.4f%%/tr   margin vs %.2f%% = %+6.2f pp   binom p = %.5f"
        % (nr2, wr2, wpr2, exr2, BE, wpr2 - BE, binom_p_ge(nr2, wr2)))
    say("    (whole-SETUP unchained for comparison: n=%d W=%d win %.2f%%)" % (n_pres, w_pres, wp_pres))
    say()

    # =================================================================== (e) fragility
    say("=" * 110)
    say("(e) FRAGILITY -- winner->loser flips needed to push the exact binomial p ABOVE 0.05")
    say("=" * 110)
    for mode in ("unchained", "chained"):
        say("  --- %s ---" % mode.upper())
        for tag, _ in COH:
            n, w, wp, ex, _ = cell(R[(tag, mode)])
            if n == 0:
                say("  %-32s n=0" % tag); continue
            bp = binom_p_ge(n, w)
            fr = fragility(n, w)
            if fr == 0:
                rb = robustness(n, w)
                say("  %-32s p=%.5f  fragility 0 (p already ABOVE 0.05; %s loser->winner flip(s)"
                    " would be needed to pull it BELOW)"
                    % (tag, bp, "no number of" if rb is None else str(rb)))
            else:
                say("  %-32s p=%.5f  fragility %d flip(s)" % (tag, bp, fr))
        say()

    # =================================================================== summary
    say("=" * 110)
    say("SUMMARY OF THE NUMBERS (restating what is measured above; no interpretation added)")
    say("=" * 110)
    for mode in ("unchained", "chained"):
        n, w, wp, ex, _ = cell(R[("SETUP", mode)])
        nm, wm, wpm, exm, _ = cell(R[("CTRL-M (mirror, falling)", mode)])
        nx, wx, wpx, exx, _ = cell(R[("CTRL-P minus SETUP", mode)])
        n0, w0, wp0, ex0, _ = cell(R[("CTRL-0 (all mature)", mode)])
        say("  SETUP %-10s n=%-4d W=%-3d win %.2f%%  net %+.4f%%/tr  margin vs %.2f%% = %+.2f pp  "
            "exact binom p = %.5f" % (mode, n, w, wp, ex, BE, wp - BE, binom_p_ge(n, w)))
        for nm_, nn, ww, pp_ in (("CTRL-P minus SETUP", nx, wx, wpx),
                                 ("CTRL-M", nm, wm, wpm),
                                 ("CTRL-0", n0, w0, wp0)):
            z, p = two_prop_z(n, w, nn, ww)
            say("      vs %-20s %+6.2f pp   z = %+.3f  p = %.4f   Fisher p = %.4f"
                % (nm_, wp - pp_, z, p, fisher_one_sided(n, w, nn, ww)))
    say()
    say("  SPLIT (unchained): BULLISH-c2 %d/%d = %.2f%% vs BEARISH-c2 %d/%d = %.2f%%, difference "
        "%+.2f pp." % (wbu, nbu, wpbu, wru, nru, wpru, wpbu - wpru))
    say("  Mix %.1f%% / %.1f%% of the resolved unchained cohort.  The aggregate SETUP win rate is the"
        % (100.0 * nbu / max(1, nbu + nru), 100.0 * nru / max(1, nbu + nru)))
    say("  n-weighted average of those two rows.")
    say()

    say("=" * 110)
    say("WHAT WAS NOT DONE")
    say("=" * 110)
    say("  No threshold, bracket, window, side or condition was varied from the specification.  No")
    say("  candle-direction clause was added to the SETUP; the candle-2 direction appears ONLY as a")
    say("  post-hoc split of the same cohort, never as a filter that defines it.  The only cells")
    say("  beyond the required table are the mandated controls, the disjoint complement, the")
    say("  like-for-like within-direction contrasts, the disjoint spread bands, the displacement")
    say("  diagnostic and the prior-study intersection.  No forward/out-of-window data was used and")
    say("  none of this is a forward test.  Factual reporting only; no trading recommendation is made")
    say("  or implied.")
    say("```")
    _flush()


def _flush():
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
