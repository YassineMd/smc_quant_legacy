"""QUIET-DRIFT CONTINUATION v1.0 -- pre-specified external-reviewer spec, implemented VERBATIM.

    Universe   mature constant-volume 1h buckets only (index >= FM.build()'s `first`).
    Z_dV       standard score of the SIGNED WHOLE-BUCKET taker delta (buy_vol - sell_vol) over a
               TRAILING 30-bucket rolling window.  WHOLE bucket, NOT the h2 half.  Strictly prior
               buckets (causal): buckets[i-30:i], never bucket i itself.
    Gate       abs(Z_dV) <= 0.5
    Direction  bullish candle (c > o) AND gate -> LONG ; bearish (c < o) AND gate -> SHORT ; doji -> none.
    Entry      market at the bucket close.
    Exit       fixed 0.8% stop / 1.0% target off entry.  Same-bar both-touched -> STOP.
               Unresolved at end of data -> DROPPED, never booked as a loss.
    Fee        flat 0.08% round trip.

NOTHING here is swept.  No parameter, threshold or bracket is varied, and no signal variant is added.
The disjoint |Z_dV| bands in section (b) are a REQUIRED diagnostic, not a search: the frozen cell is and
remains abs(Z_dV) <= 0.5.

HARNESS VALIDATION (runs first; the script ABORTS if it fails).  Two previously published cells on the
published 926-bucket universe must reproduce to the printed digit:
    ride-all chained  n=156  33.3%  -0.2800
    fade-all chained  n=158  51.3%  +0.0428

WHY THE ADDITIONS.  This project's standing findings make several of the spec's own outputs
uninterpretable on their own, so each is reported WITH its control:
  * At a fixed +TP/-SL bracket every trade nets exactly +0.92 or -0.88, so (n, wins) is a SUFFICIENT
    statistic.  mean, sd, t, profit factor and an iid bootstrap are one number restated; the iid
    bootstrap of a two-valued vector IS a binomial draw.  The EXACT one-sided binomial p is therefore
    the only honest significance statement, and no bootstrap P(profit) or drop-best-N is reported.
  * Split-half is near-vacuous, so the conditional hypergeometric probability that a RANDOM
    rearrangement of the same wins passes it is printed beside it.
  * A gate that does nothing still inherits the ride base rate, so the direction- and entry-matched
    ride-all base rate over the SAME universe / bracket / mode is reported, with the lift.
  * The chained basis has a signed non-zero null for a sparse cohort (+10.2pp free on the ride side);
    the unchained lift null is 0.0 exactly.  Both are shown, with that caveat attached.
  * Cumulative threshold ladders manufacture gradients, so DISJOINT bands are the primary form.

Run:  python study/quiet_drift_validate.py
"""
from __future__ import annotations
import os, sys, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from study.archive_loader import load_archive              # noqa: E402
import study.mm_skew_feature_matrix as FM                  # noqa: E402

SL_PCT = 0.008        # FROZEN 0.8% stop
TP_PCT = 0.010        # FROZEN 1.0% target
FEE = 0.0008          # flat 0.08% round trip
WIN = 30              # trailing rolling window, buckets
ZGATE = 0.5           # abs(Z_dV) <= 0.5
BE = 100.0 * (FEE + SL_PCT) / (TP_PCT + SL_PCT)            # 48.8889% break-even win rate
P0 = BE / 100.0
BANDS = [(0.00, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.00), (1.00, 1.50), (1.50, float("inf"))]

OUT_MD = os.path.join(HERE, "out", "quiet_drift_results.md")
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
    """EXACT one-sided binomial P(W >= w | n, p0).  No normal approximation."""
    if n <= 0:
        return 1.0
    w = max(0, min(n, w))
    return float(sum(math.comb(n, k) * p0 ** k * (1.0 - p0) ** (n - k) for k in range(w, n + 1)))


def hyper_both_halves_positive(n: int, w: int) -> float:
    """P(a RANDOM rearrangement of the same w wins over n slots puts a POSITIVE net expectancy in BOTH
    halves).  H1 = first n//2 trades.  A half of size m with k wins is positive iff k > P0*m."""
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
    """How many winner->loser flips are needed to push the exact binomial p above 0.05.
    0 means it is already above."""
    f = 0
    while w - f >= 0:
        if binom_p_ge(n, w - f) > 0.05:
            return f
        f += 1
    return f


def robustness(n: int, w: int):
    """The mirror of `fragility`, for a cell that never reached p < 0.05: how many loser->winner flips
    would be needed to pull the exact binomial p BELOW 0.05.  None if even n wins cannot."""
    for f in range(0, n - w + 1):
        if binom_p_ge(n, w + f) < 0.05:
            return f
    return None


def min_wins_two_positive_halves(n: int):
    """Smallest total win count for which ANY arrangement puts a positive net in both halves."""
    m = n // 2
    return int(math.floor(P0 * m) + 1) + int(math.floor(P0 * (n - m)) + 1)


# --------------------------------------------------------------------------- data
def build():
    """(bars, first).  bars = EVERY archive 1h bucket kept intact (the forward exit scan needs the whole
    series); each carries o/c/h/l/t, `ok`, whole-bucket taker delta `dv` and `cv`."""
    _, H, _ = load_archive("1h")
    _, first, _, _ = FM.build()                            # maturity index (target_vol >= 100k regime)
    bars = []
    for b in H:
        o = _f(b.get("open_price")); c = _f(b.get("close_price"))
        h = _f(b.get("high")); l = _f(b.get("low"))
        bars.append(dict(o=o, c=c, h=h, l=l, t=_f(b.get("start_time")),
                         dv=_f(b.get("buy_vol")) - _f(b.get("sell_vol")),
                         cv=_f(b.get("curr_vol")),
                         ok=(o > 0 and c > 0 and h > l)))
    return bars, first


def zdv_series(bars, lo: int):
    """Causal trailing-30 standard score of the WHOLE-BUCKET signed taker delta.  Window = bars[i-30:i],
    strictly prior, drawn from the bucket series starting at `lo`."""
    d = np.array([b["dv"] for b in bars], float)
    z = [None] * len(bars)
    for i in range(lo + WIN, len(bars)):
        w = d[i - WIN:i]
        s = w.std(ddof=1)
        if s > 0:
            z[i] = float((d[i] - w.mean()) / s)
    return z


def universe(bars, first, z):
    """The spec universe: mature, tradeable bucket, computable Z_dV, non-doji (a doji has no direction
    under the rule and cannot enter the direction-matched ride base either)."""
    U = []
    for i in range(first, len(bars)):
        b = bars[i]
        if not b["ok"] or z[i] is None or b["c"] == b["o"]:
            continue
        U.append(dict(i=i, t=b["t"], z=z[i], cdir=(1 if b["c"] > b["o"] else -1)))
    return U


# --------------------------------------------------------------------------- trade engine
def sim(bars, sigs, chained: bool, tp=TP_PCT, sl=SL_PCT):
    """sigs = [(i, side)] ascending in i.  Entry = bars[i] close, market.
    CHAINED: last = -1; skip any signal with i <= last; last = exit bar on resolution.
    Same-bar TP+SL -> STOP.  Unresolved at end of data -> DROPPED."""
    last = -1
    out = []
    for i, s in sigs:
        if chained and i <= last:
            continue
        e = bars[i]["c"]
        stop = e * (1 - sl) if s > 0 else e * (1 + sl)
        targ = e * (1 + tp) if s > 0 else e * (1 - tp)
        res = None
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            htp = (bars[j]["h"] >= targ) if s > 0 else (bars[j]["l"] <= targ)
            hsl = (bars[j]["l"] <= stop) if s > 0 else (bars[j]["h"] >= stop)
            if htp and hsl:
                res = (False, j); break
            if htp:
                res = (True, j); break
            if hsl:
                res = (False, j); break
        if res is None:
            continue                                        # never booked as a loss
        out.append(dict(i=i, side=s, win=res[0], net=((tp if res[0] else -sl) - FEE) * 100.0))
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
    produced them.  ABORT the run if either fails."""
    sys.path.insert(0, os.path.join(HERE, "out"))
    try:
        import entry_norm_core as C                          # noqa: E402
    except Exception as exc:                                 # pragma: no cover
        say("HARNESS VALIDATION: CANNOT RUN -- %s" % exc)
        return False
    bars, U = C.build_universe()
    ok = True
    say("HARNESS VALIDATION (published 926-bucket universe, entry at close, chained)")
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


# --------------------------------------------------------------------------- report
def line(tag, n, w, wp, exp, extra=""):
    return "  %-26s n=%-5d W=%-5d %6.2f%%  %+.4f%%%s" % (tag, n, w, wp, exp, extra)


def main():
    say("# QUIET-DRIFT CONTINUATION v1.0 -- results")
    say()
    say("Pre-specified external spec, implemented verbatim; nothing swept.")
    say("Universe mature 1h constant-volume buckets | Z_dV = trailing-30 causal z of the WHOLE-BUCKET")
    say("signed taker delta | gate abs(Z_dV) <= %.1f | bull->LONG, bear->SHORT, doji->none | entry at" % ZGATE)
    say("bucket close | fixed %.1f%% stop / %.1f%% target | same-bar both -> STOP | unresolved -> DROPPED"
        % (SL_PCT * 100, TP_PCT * 100))
    say("| flat %.2f%% round-trip fee.  Break-even win rate = %.4f%%.  Driftless P(TP first) = 44.44%%."
        % (FEE * 100, BE))
    say("Every trade nets exactly %+.2f%% or %+.2f%%, so (n, wins) is a sufficient statistic."
        % ((TP_PCT - FEE) * 100, (-SL_PCT - FEE) * 100))
    say()
    say("```")
    if not validate_harness():
        say("```")
        _flush()
        raise SystemExit("HARNESS VALIDATION FAILED -- not proceeding.")

    bars, first = build()
    z = zdv_series(bars, first)                     # window drawn from the MATURE series (see note below)
    U = universe(bars, first, z)

    say("UNIVERSE")
    say("  archive 1h buckets                 %d" % len(bars))
    say("  maturity index (FM.build first)    %d   -> %d mature buckets" % (first, len(bars) - first))
    say("  mature, tradeable, Z_dV computable, non-doji   %d   <- the spec universe" % len(U))
    say("  of those, gate abs(Z_dV) <= %.1f            %d" % (ZGATE, sum(1 for r in U if abs(r["z"]) <= ZGATE)))
    say()

    qd = [(r["i"], r["cdir"]) for r in U if abs(r["z"]) <= ZGATE]
    ride = [(r["i"], r["cdir"]) for r in U]                      # direction- and entry-MATCHED base
    anti = [(r["i"], r["cdir"]) for r in U if abs(r["z"]) > ZGATE]   # the gated-OUT complement

    res = {}
    for mode, ch in (("unchained", False), ("chained", True)):
        res[("qd", mode)] = sim(bars, qd, ch)
        res[("ride", mode)] = sim(bars, ride, ch)
        res[("anti", mode)] = sim(bars, anti, ch)

    # ---------------------------------------------------------------- MAIN TABLE
    say("=" * 96)
    say("REQUIRED TABLE -- QUIET-DRIFT CONTINUATION v1.0")
    say("=" * 96)
    say("  qualifying signals (gate passed, before any chaining):  %d" % len(qd))
    say()
    for mode in ("unchained", "chained"):
        T = res[("qd", mode)]
        n, w, wp, exp, r = cell(T)
        say("  --- MODE: %s ---" % mode.upper())
        if n == 0:
            say("    no resolved trades"); continue
        sd = r.std(ddof=1) if n > 1 else 0.0
        t = (r.mean() / (sd / math.sqrt(n))) if sd > 0 else float("nan")
        bp = binom_p_ge(n, w)
        say("    n%-10s          %d" % ("_raw" if mode == "unchained" else "_chained", n))
        say("    win%%                   %.2f%%   (break-even %.4f%%, driftless 44.44%%)" % (wp, BE))
        say("    net expectancy / trade %+.4f%%   (total %+.2f%%)" % (exp, r.sum()))
        for tag, sgn in (("LONG", +1), ("SHORT", -1)):
            S = [x for x in T if x["side"] == sgn]
            if S:
                sw = sum(1 for x in S if x["win"])
                say("    %-5s                  n=%-4d W=%-4d %6.2f%%   exp %+.4f%%"
                    % (tag, len(S), sw, 100.0 * sw / len(S),
                       float(np.mean([x["net"] for x in S]))))
            else:
                say("    %-5s                  n=0" % tag)
        say("    t-statistic            %+.3f" % t)
        say("    EXACT one-sided binomial p vs %.3f%%   p = %.5f   (P(W >= %d | n=%d))" % (BE, bp, w, n))
        mid = n // 2
        h1 = r[:mid].mean(); h2 = r[mid:].mean()
        hg = hyper_both_halves_positive(n, w)
        say("    split-half             H1 n=%d exp %+.4f%%  |  H2 n=%d exp %+.4f%%  -> %s"
            % (mid, h1, n - mid, h2, "BOTH POSITIVE" if (h1 > 0 and h2 > 0) else "FAILS"))
        say("      [ADDITION c] conditional hypergeometric: a RANDOM rearrangement of these same %d"
            % w)
        say("      wins over the same %d slots passes 'both halves positive' with probability %s."
            % (n, ("%.4f" % hg) if hg > 0 else "0 EXACTLY"))
        if hg <= 0:
            say("      It is 0 because two positive halves need at least %d wins here and there are %d;"
                % (min_wins_two_positive_halves(n), w))
            say("      the split-half FAILURE is forced by the win count alone and adds no information.")
        else:
            say("      Read the split-half line against THAT number, not against 0.5.")
        fr = fragility(n, w)
        say("    [ADDITION d] fragility: %d winner->loser flip(s) push the exact binomial p above 0.05"
            % fr)
        if fr == 0:
            rb = robustness(n, w)
            say("      (p is already above 0.05 -- the cell never reached significance.  The mirror")
            say("      quantity: %s loser->winner flip(s) would be needed to pull p BELOW 0.05.)"
                % ("no number of" if rb is None else str(rb)))
        say()

    # ---------------------------------------------------------------- (a) matched base rate
    say("=" * 96)
    say("[ADDITION a] DIRECTION- AND ENTRY-MATCHED BASE RATE")
    say("=" * 96)
    say("  ride-all-buckets over the SAME universe, SAME bracket, SAME mode.  A gate that does nothing")
    say("  still inherits this rate, so it -- not break-even -- is the zero for the gate's own effect.")
    say()
    for mode in ("unchained", "chained"):
        nq, wq, wpq, eq, _ = cell(res[("qd", mode)])
        nb, wb, wpb, eb, _ = cell(res[("ride", mode)])
        na, wa, wpa, ea, _ = cell(res[("anti", mode)])
        zb, pb = two_prop_z(nq, wq, nb, wb)
        za, pa = two_prop_z(nq, wq, na, wa)
        say("  --- %s ---" % mode.upper())
        say(line("QUIET-DRIFT (gate)", nq, wq, wpq, eq))
        say(line("ride-all (matched base)", nb, wb, wpb, eb))
        say(line("gated-OUT complement", na, wa, wpa, ea))
        say("    LIFT vs matched base   %+.2f pp   two-proportion z = %+.3f   one-sided p = %.4f"
            % (wpq - wpb, zb, pb))
        say("    LIFT vs complement     %+.2f pp   two-proportion z = %+.3f   one-sided p = %.4f"
            % (wpq - wpa, za, pa))
        if mode == "chained":
            say("    CAVEAT: the chained lift is scored against a base whose null is NOT zero -- a")
            say("    zero-information SPARSE cohort earns about +10pp for free on the ride side, because")
            say("    chaining costs the dense base (43.5 -> 33.3) while a sparse cohort keeps its")
            say("    unchained rate.  The unchained lift null is 0.0 exactly; read that row, not this one.")
        else:
            say("    (the unchained lift null is 0.0 exactly at every cohort size)")
        say()

    # ---------------------------------------------------------------- (b) disjoint bands
    say("=" * 96)
    say("[ADDITION b] DISJOINT abs(Z_dV) BANDS -- each an INDEPENDENT re-chained walk")
    say("=" * 96)
    say("  Disjoint, not cumulative: a cumulative ladder over nested sets manufactures apparent")
    say("  gradients.  The frozen cell is the union of the first two rows.")
    say()
    for mode, ch in (("chained (spec-mandated)", True), ("unchained (context)", False)):
        say("  --- %s ---" % mode)
        for lo, hi in BANDS:
            sg = [(r["i"], r["cdir"]) for r in U if lo <= abs(r["z"]) < hi]
            T = sim(bars, sg, ch)
            n, w, wp, exp, _ = cell(T)
            tag = "[%.2f, %s)" % (lo, ("inf" if math.isinf(hi) else "%.2f" % hi))
            if n == 0:
                say("  %-26s n=0" % tag); continue
            say(line(tag, n, w, wp, exp, "   binom p %.4f" % binom_p_ge(n, w)))
        say()
    say("  cumulative ladder (shown ONLY so it can be compared with the disjoint form above):")
    for thr in (0.25, 0.50, 0.75, 1.00, 1.50):
        sg = [(r["i"], r["cdir"]) for r in U if abs(r["z"]) < thr]
        n, w, wp, exp, _ = cell(sim(bars, sg, True))
        if n:
            say(line("abs(Z_dV) < %.2f" % thr, n, w, wp, exp))
    say()

    # ---------------------------------------------------------------- convention sensitivity
    say("=" * 96)
    say("CONVENTION SENSITIVITY OF THE ROLLING WINDOW (reported unprompted)")
    say("=" * 96)
    say("  The spec fixes the window as buckets[i-30:i] but the universe as mature buckets only.  Those")
    say("  two clauses disagree for the first 30 mature buckets, whose window would otherwise reach back")
    say("  into the immature prefix, where curr_vol runs as low as 5,000 against a mature minimum of")
    say("  118,391 -- a z-score across a ~100x volume-scale break.  PRIMARY above = window drawn from")
    say("  the MATURE series (i-30 >= first).  The literal whole-archive window is shown here.")
    say()
    z2 = zdv_series(bars, 0)
    U2 = universe(bars, first, z2)
    for mode, ch in (("chained", True), ("unchained", False)):
        sg = [(r["i"], r["cdir"]) for r in U2 if abs(r["z"]) <= ZGATE]
        n, w, wp, exp, _ = cell(sim(bars, sg, ch))
        say(line("literal window, %s" % mode, n, w, wp, exp, "   binom p %.4f" % binom_p_ge(n, w)))
    say()

    # ---------------------------------------------------------------- multi-asset
    say("=" * 96)
    say("MULTI-ASSET (BTC / ETH): NO SUCH DATA EXISTS IN THIS REPO -- NOT RUN")
    say("=" * 96)
    say("  app/config.py            SYMBOL = \"SOLUSDT\", hardcoded; every REST/WS endpoint is built from it.")
    say("  ops/archive_buckets.py   GCS_DEFAULT = \"gs://smc-quant-archive/solusdt\" -- one symbol, one prefix.")
    say("  study/archive_data/      only 1m/5m/15m/1h/4h subdirs; archived rows carry no symbol field at all.")
    say("  data/                    live daemon artefacts for the same single symbol.")
    say("  The ONLY repo-wide occurrence of the strings BTCUSDT / ETHUSDT is one line of")
    say("  study/out/oow_contract_v2_audit.md naming them as a PROPOSED out-of-window family.  No BTC or")
    say("  ETH bucket data is present anywhere.  The multi-asset run is therefore not performed and no")
    say("  multi-asset numbers are reported.")
    say("```")
    _flush()


def _flush():
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
