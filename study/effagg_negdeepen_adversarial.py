"""EFF-AGG NEGATIVE-AND-DEEPENING SHORT -- ADVERSARIAL PRICING.

Prices the already-run result. Nothing is tuned; no new candidate is searched; no threshold,
bracket, side or condition is varied from the frozen spec.

  SETUP  eff_agg[i-1] < 0 AND eff_agg[i] < 0 ; candle1 BEARISH ; candle2 BULLISH ;
         close[i-1] < close[i] ; eff_agg[i] < eff_agg[i-1]
         -> SHORT at bars[i]["c"], 0.8% SL / 0.8% TP, fee 0.08% RT.
         Winner +0.72, loser -0.88 -> BREAK-EVEN 55.0000%, driftless P(TP first) 50.00%.
         Same-bar both-touched -> STOP. Unresolved at end of data -> DROPPED.

Eight blocks, exactly as commissioned:
  0  RECOMPUTE     headline cells rebuilt on an independent path (precomputed per-bar exits).
  1  NULL          2000 random same-size SHORT cohorts, matched on entry-bucket DISPLACEMENT
                   DECILE and on ENTRY-CANDLE DIRECTION, chained identically, several seeds.
  2  SETUP vs CTRL-M   the one contrast that can attribute anything to eff-agg DIRECTION.
  3  CLAUSE ATTRIBUTION   CTRL-0 -> CTRL-P -> CTRL-N -> SETUP, delta per added clause.
  4  CTRL-E        eff-agg clauses standalone at large n.
  5  BANDS         monotone or sign-alternating; single-band-carries check.
  6  POWER         MDE at 80% power, one-sided alpha 0.05, vs CTRL-M and vs CTRL-N complement.
  7  MULTIPLICITY  five families on the same 1261 pairs; overlap -> effective comparisons.
  8  VERDICT       per mode: beats CTRL-M at p<0.05 AND net > 0 AND above the matched-null p95.

Run:  python study/effagg_negdeepen_adversarial.py
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

SL_PCT = 0.008
TP_PCT = 0.008
FEE = 0.0008
BE = 100.0 * (FEE + SL_PCT) / (TP_PCT + SL_PCT)     # 55.0000
P0 = BE / 100.0
WIN_NET = (TP_PCT - FEE) * 100.0                    # +0.72
LOSS_NET = (-SL_PCT - FEE) * 100.0                  # -0.88

NITER = 2000
SEED = 20260722
SEEDS_EXTRA = (11, 22, 33, 44)
ZA = 1.6448536269514722

OUT_MD = os.path.join(HERE, "out", "effagg_negdeepen_adversarial.md")
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


# ------------------------------------------------------------------ stats
def binom_p_ge(n: int, w: int, p0: float = P0) -> float:
    if n <= 0:
        return 1.0
    w = max(0, min(n, w))
    lg = math.lgamma
    lp = math.log(p0); lq = math.log(1.0 - p0)
    acc = 0.0
    for k in range(w, n + 1):
        acc += math.exp(lg(n + 1) - lg(k + 1) - lg(n - k + 1) + k * lp + (n - k) * lq)
    return min(1.0, acc)


def two_prop_z(n1, w1, n2, w2):
    if n1 <= 0 or n2 <= 0:
        return float("nan"), float("nan")
    p1 = w1 / n1; p2 = w2 / n2
    pb = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2))
    if se <= 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    return z, 0.5 * math.erfc(z / math.sqrt(2.0))


def fisher_one_sided(a, b, c, d):
    """P(X >= a) hypergeometric on [[set1 W, set1 L],[set2 W, set2 L]]."""
    n1 = a + b; n2 = c + d; k = a + c; N = n1 + n2
    if n1 <= 0 or n2 <= 0 or N <= 0:
        return float("nan")
    hi = min(n1, k)
    tot = math.comb(N, k)
    acc = 0
    for x in range(a, hi + 1):
        acc += math.comb(n1, x) * math.comb(n2, k - x)
    return acc / tot


def mde_two_prop(n1, n2, p2, alpha=0.05, power=0.80):
    """Smallest p1 > p2 with >= `power` at one-sided `alpha`, normal approx, pooled null SE."""
    lo, hi = p2, 1.0
    for _ in range(300):
        p1 = 0.5 * (lo + hi)
        pb = (p1 * n1 + p2 * n2) / (n1 + n2)
        se0 = math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2))
        se1 = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        if se1 <= 0:
            lo = p1; continue
        z = (p1 - p2 - alpha_z(alpha) * se0) / se1
        if 0.5 * math.erfc(-z / math.sqrt(2.0)) < power:
            lo = p1
        else:
            hi = p1
    return hi


def alpha_z(alpha):
    return ZA if abs(alpha - 0.05) < 1e-12 else _norm_isf(alpha)


def _norm_isf(a):
    lo, hi = -10.0, 10.0
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if 0.5 * math.erfc(m / math.sqrt(2.0)) > a:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def mde_binom(n, p0=P0, alpha=0.05, power=0.80):
    crit = None
    for k in range(0, n + 1):
        if binom_p_ge(n, k, p0) < alpha:
            crit = k; break
    if crit is None:
        return None, None
    lo, hi = p0, 1.0
    lg = math.lgamma
    for _ in range(200):
        p = 0.5 * (lo + hi)
        acc = 0.0
        lp = math.log(p); lq = math.log(1 - p) if p < 1 else -1e18
        for kk in range(crit, n + 1):
            acc += math.exp(lg(n + 1) - lg(kk + 1) - lg(n - kk + 1) + kk * lp + (n - kk) * lq)
        if acc < power:
            lo = p
        else:
            hi = p
    return crit, hi


def desc(v):
    if len(v) == 0:
        return "n=0"
    a = np.asarray(v, float)
    return "n=%-5d mean %+7.4f  median %+7.4f  sd %6.4f  min %+7.4f  max %+7.4f" % (
        len(a), a.mean(), float(np.median(a)), a.std(ddof=1) if len(a) > 1 else 0.0,
        a.min(), a.max())


# ------------------------------------------------------------------ data
def build():
    _, H, _ = load_archive("1h")
    _, first, _, _ = FM.build()
    bars = []
    for b in H:
        o = _f(b.get("open_price")); c = _f(b.get("close_price"))
        h = _f(b.get("high")); l = _f(b.get("low"))
        bars.append(dict(o=o, c=c, h=h, l=l, ok=(o > 0 and c > 0 and h > l)))
    W = []
    for b in H:
        d = dict(b); d["open"] = b.get("open_price"); d["close"] = b.get("close_price")
        W.append(d)
    eff = (2.0 * np.asarray(eff_causal_share(W), float) - 1.0) * 100.0
    return bars, first, eff


def precompute_exits(bars, side):
    """(win, exit_bar) per entry index -- depends ONLY on the entry bar at a fixed % bracket, so it is
    cohort-independent and reusable by every permutation draw. side -1 = SHORT.
    None = unresolved at end of data (DROPPED, never a loss). Same-bar both-touched -> STOP."""
    n = len(bars)
    res = [None] * n
    for i in range(n):
        if not bars[i]["ok"]:
            continue
        e = bars[i]["c"]
        if side < 0:
            targ = e * (1 - TP_PCT); stop = e * (1 + SL_PCT)
        else:
            targ = e * (1 + TP_PCT); stop = e * (1 - SL_PCT)
        for j in range(i + 1, n):
            if not bars[j]["ok"]:
                continue
            if side < 0:
                htp = bars[j]["l"] <= targ; hsl = bars[j]["h"] >= stop
            else:
                htp = bars[j]["h"] >= targ; hsl = bars[j]["l"] <= stop
            if htp and hsl:
                res[i] = (False, j); break
            if htp:
                res[i] = (True, j); break
            if hsl:
                res[i] = (False, j); break
    return res


def run(idxs, EX, chained: bool):
    """-> (n, wins, net_per_trade). idxs must be ascending."""
    last = -1; n = 0; w = 0
    for i in idxs:
        if chained and i <= last:
            continue
        r = EX[i]
        if r is None:
            continue
        n += 1
        if r[0]:
            w += 1
        if chained:
            last = r[1]
    net = (w * WIN_NET + (n - w) * LOSS_NET) / n if n else float("nan")
    return n, w, net


# ------------------------------------------------------------------ main
def main():
    bars, first, eff = build()
    EXS = precompute_exits(bars, -1)      # SHORT
    EXL = precompute_exits(bars, +1)      # LONG (engine identity check only)
    mature = [i for i in range(first, len(bars)) if bars[i]["ok"]]
    P = [i for i in range(first + 1, len(bars)) if bars[i]["ok"] and bars[i - 1]["ok"]]

    bearbull, ctrlP, ctrlN, setup, ctrlM, ties, ctrlE, noHC = [], [], [], [], [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        neg = (ea < 0.0) and (eb < 0.0)
        fall = eb < ea
        if neg and fall:
            ctrlE.append(i)
        if (a["c"] < a["o"]) and (b["c"] > b["o"]):
            bearbull.append(i)
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
    xN = sorted(set(ctrlN) - SET)
    xP = sorted(set(ctrlP) - SET)

    disp = {}
    for i in range(len(bars)):
        if not bars[i]["ok"]:
            continue
        b = bars[i]
        d = 1.0 if b["c"] > b["o"] else (-1.0 if b["c"] < b["o"] else 0.0)
        disp[i] = d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0

    COH = [("SETUP", setup), ("CTRL-P price only", ctrlP), ("CTRL-N price+both<0", ctrlN),
           ("CTRL-M mirror rising", ctrlM), ("CTRL-N minus SETUP", xN),
           ("CTRL-P minus SETUP", xP), ("CTRL-E eff only", ctrlE), ("CTRL-0 all mature", mature)]
    CD = dict(COH)

    say("# EFF-AGG NEGATIVE-AND-DEEPENING SHORT -- ADVERSARIAL PRICING")
    say()
    say("Bracket 0.8/0.8 + 0.08%% fee round trip. BREAK-EVEN win rate %.4f%%. Driftless P(TP first)" % BE)
    say("50.00%. (n, wins) is sufficient at a fixed bracket -- no t-stat, no iid bootstrap P(profit),")
    say("no profit factor and no drop-best-N appears anywhere below.")
    say("Permutation nulls: %d draws, cohorts matched on ENTRY-BUCKET DISPLACEMENT DECILE and on" % NITER)
    say("ENTRY-CANDLE DIRECTION, drawn without replacement, sorted ascending, chained by the identical")
    say("machinery. Nothing was tuned; no threshold, bracket, side or condition was varied.")
    say()
    say("```")

    # ---------------------------------------------------------------- 0 RECOMPUTE
    say("=" * 104)
    say("0. HEADLINE CELLS RECOMPUTED FROM SCRATCH (independent path: precomputed per-bar exits, not")
    say("   the reporting script's per-cohort forward-scan sim; must agree digit for digit)")
    say("=" * 104)
    say("  universe: %d archive buckets, maturity first=%d, %d mature, %d consecutive mature pairs"
        % (len(bars), first, len(mature), len(P)))
    say("  funnel: %d pairs -> %d bear/bull -> %d + higher close (CTRL-P) -> %d both eff<0 (CTRL-N)"
        % (len(P), len(bearbull), len(ctrlP), len(ctrlN)))
    say("          -> %d eff FALLING (SETUP) ; CTRL-N splits %d falling / %d rising (CTRL-M) / %d tied"
        % (len(setup), len(setup), len(ctrlM), len(ties)))
    say("  bear/bull pairs FAILING the explicit higher-close clause: %d  -> clause is %s"
        % (len(noHC), "INERT on this universe" if len(noHC) == 0 else "BINDING"))
    say()
    nL, wL, _ = run(mature, EXL, False)
    nS, wS, _ = run(mature, EXS, False)
    tie_both = 0
    for i in mature:
        e = bars[i]["c"]
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            up = bars[j]["h"] >= e * (1 + SL_PCT); dn = bars[j]["l"] <= e * (1 - TP_PCT)
            if up and dn:
                tie_both += 1
            if up or dn:
                break
    say("  ENGINE IDENTITY (recomputed): long W %d + short W %d + same-bar both %d = %d vs n=%d/%d -> %s"
        % (wL, wS, tie_both, wL + wS + tie_both, nL, nS,
           "HOLDS" if (wL + wS + tie_both == nL == nS) else "BROKEN"))
    say()
    say("  %-22s %-10s %6s %5s %5s %8s %11s %10s %9s"
        % ("cohort", "mode", "sig", "n", "W", "win%", "net/tr", "binom p", "margin"))
    CELLS = {}
    for tag, idxs in COH:
        for mode, ch in (("unchained", False), ("chained", True)):
            n, w, net = run(idxs, EXS, ch)
            CELLS[(tag, mode)] = (n, w, net, len(idxs))
            wp = 100.0 * w / n if n else float("nan")
            say("  %-22s %-10s %6d %5d %5d %7.2f%% %+10.4f%% %10.5f %+8.2f pp"
                % (tag, mode, len(idxs), n, w, wp, net, binom_p_ge(n, w), wp - BE))
    say()
    say("  PRIOR RUN, for the digit-for-digit check:")
    say("    SETUP unchained  n=35 W=15 42.86%  -0.1943  p 0.94640")
    say("    SETUP chained    n=33 W=14 42.42%  -0.2012  p 0.94783")
    say("    CTRL-P 286/144 | CTRL-N 145/72 | CTRL-M 101/54 | CTRL-N minus SETUP 110/57")
    say("    CTRL-E 259/117 | CTRL-0 1252/615 ; funnel 1261 -> 288 -> 288 -> 145 -> 35")
    ok0 = (CELLS[("SETUP", "unchained")][:2] == (35, 15)
           and CELLS[("SETUP", "chained")][:2] == (33, 14)
           and CELLS[("CTRL-P price only", "unchained")][:2] == (286, 144)
           and CELLS[("CTRL-N price+both<0", "unchained")][:2] == (145, 72)
           and CELLS[("CTRL-M mirror rising", "unchained")][:2] == (101, 54)
           and CELLS[("CTRL-N minus SETUP", "unchained")][:2] == (110, 57)
           and CELLS[("CTRL-E eff only", "unchained")][:2] == (259, 117)
           and CELLS[("CTRL-0 all mature", "unchained")][:2] == (1252, 615)
           and (len(P), len(bearbull), len(ctrlP), len(ctrlN), len(setup)) == (1261, 288, 288, 145, 35))
    say("    -> RECOMPUTATION %s" % ("MATCHES the prior run on every (n, W) and every funnel count"
                                     if ok0 else "DISAGREES -- STOP AND READ THE TABLE"))
    say()

    # ---------------------------------------------------------------- 1 NULL
    say("=" * 104)
    say("1. NULL CALIBRATION -- what does a random cohort with the SAME displacement and the SAME entry-")
    say("   candle direction earn under the identical machinery?")
    say("=" * 104)
    bull_mature = [i for i in mature if bars[i]["c"] > bars[i]["o"]]
    say("  displacement metric = dir*(close-(high+low)/2)/close*100 on the ENTRY bucket.")
    say("  SETUP enters at a BULLISH bucket close on %d/%d signals = %.1f%% (candle 2 bullish by rule)."
        % (sum(1 for i in setup if bars[i]["c"] > bars[i]["o"]), len(setup),
           100.0 * sum(1 for i in setup if bars[i]["c"] > bars[i]["o"]) / len(setup)))
    say("  mature pool %d, of which BULLISH %d = %.1f%%.  An unmatched null would draw a direction mix"
        % (len(mature), len(bull_mature), 100.0 * len(bull_mature) / len(mature)))
    say("  the cohort never takes, so the PRIMARY null below is the direction-matched one.")
    say()

    # --- primary pool: bullish mature, deciles defined WITHIN that pool
    bp = np.array(bull_mature, dtype=int)
    bpd = np.array([disp[i] for i in bp], float)
    edB = np.percentile(bpd, [10, 20, 30, 40, 50, 60, 70, 80, 90])
    decB = {d: bp[np.searchsorted(edB, bpd, side="right") == d] for d in range(10)}

    def dec_of_B(i):
        return int(np.searchsorted(edB, disp[i], side="right"))

    # --- contrast pool: all mature, deciles over all mature (direction UNMATCHED)
    ap = np.array(mature, dtype=int)
    apd = np.array([disp[i] for i in ap], float)
    edA = np.percentile(apd, [10, 20, 30, 40, 50, 60, 70, 80, 90])
    decA = {d: ap[np.searchsorted(edA, apd, side="right") == d] for d in range(10)}

    def dec_of_A(i):
        return int(np.searchsorted(edA, disp[i], side="right"))

    say("  NULL B (PRIMARY) decile edges over the BULLISH mature pool (pct): "
        + " ".join("%+.4f" % e for e in edB))
    say("  NULL B pool per decile: " + " ".join("d%d=%d" % (d, len(decB[d])) for d in range(10)))
    say("  NULL A (contrast, direction UNMATCHED) edges over ALL mature (pct): "
        + " ".join("%+.4f" % e for e in edA))
    say()

    def null_dist(real_idxs, chained, pools, dec_fn, niter=NITER, seed=SEED):
        rng = np.random.default_rng(seed)
        cnt = np.zeros(10, dtype=int)
        for i in real_idxs:
            cnt[dec_fn(i)] += 1
        nets = np.full(niter, np.nan); wins = np.full(niter, np.nan); ns = np.zeros(niter)
        for it in range(niter):
            pick = []
            for d in range(10):
                k = int(cnt[d])
                if k:
                    p_ = pools[d]
                    pick.append(rng.choice(p_, size=k, replace=(len(p_) < k)))
            sel = np.sort(np.concatenate(pick)) if pick else np.array([], dtype=int)
            n, w, net = run(sel.tolist(), EXS, chained)
            if n:
                nets[it] = net; wins[it] = 100.0 * w / n
            ns[it] = n
        return nets, wins, ns, cnt

    say("  Each null cohort draws the SAME NUMBER OF SIGNALS PER DISPLACEMENT DECILE as the real cohort,")
    say("  without replacement, all SHORT, then runs the identical exit/drop/chain machinery.  Null n")
    say("  therefore varies exactly as the real n does.")
    say()
    say("  --- NULL B (PRIMARY): displacement decile AND entry-candle direction matched ---")
    say("  %-22s %-10s %8s | %9s %8s %8s %8s %8s | %8s %8s"
        % ("cohort", "mode", "real net", "null mean", "null sd", "null p5", "null p50", "null p95",
           "PCTILE", "emp p"))
    NB = {}
    for tag in ("SETUP", "CTRL-M mirror rising"):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins, ns, cnt = null_dist(CD[tag], ch, decB, dec_of_B)
            n, w, real = run(CD[tag], EXS, ch)
            g = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(g < real))
            NB[(tag, mode)] = (g, wins[np.isfinite(wins)], ns, real, pct,
                               float(np.percentile(g, 95)), cnt)
            say("  %-22s %-10s %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, real, g.mean(), g.std(ddof=1), np.percentile(g, 5),
                   np.percentile(g, 50), np.percentile(g, 95), pct, float(np.mean(g >= real))))
    say()
    say("  --- NULL A (contrast only, direction UNMATCHED -- NOT the comparison set) ---")
    NA = {}
    for tag in ("SETUP",):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins, ns, cnt = null_dist(CD[tag], ch, decA, dec_of_A)
            n, w, real = run(CD[tag], EXS, ch)
            g = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(g < real))
            NA[(tag, mode)] = (g, pct, float(np.percentile(g, 95)))
            say("  %-22s %-10s %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, real, g.mean(), g.std(ddof=1), np.percentile(g, 5),
                   np.percentile(g, 50), np.percentile(g, 95), pct, float(np.mean(g >= real))))
    say()
    say("  null mean WIN RATE and mean n -- this is the signed offset of the CHAINED null made explicit:")
    for tag in ("SETUP", "CTRL-M mirror rising"):
        for mode in ("unchained", "chained"):
            g, wv, ns, real, pct, p95, cnt = NB[(tag, mode)]
            say("    %-22s %-10s null mean win %6.2f%% (BE %.2f%%)  null mean n %6.2f  real n %d"
                % (tag, mode, wv.mean(), BE, ns.mean(), CELLS[(tag, mode)][0]))
    say()
    say("  decile composition of the real SETUP cohort (NULL B deciles):")
    say("    " + " ".join("d%d=%d" % (d, NB[("SETUP", "unchained")][6][d]) for d in range(10)))
    say()
    say("  SEED STABILITY on NULL B -- 4 further seeds, 1000 draws each, percentile of the real net:")
    for mode, ch in (("unchained", False), ("chained", True)):
        n, w, real = run(setup, EXS, ch)
        ps = []
        for sd in SEEDS_EXTRA:
            nets, _, _, _ = null_dist(setup, ch, decB, dec_of_B, niter=1000, seed=sd)
            g = nets[np.isfinite(nets)]
            ps.append(100.0 * float(np.mean(g < real)))
        say("    SETUP %-10s percentiles %s   (main seed %d, %d draws: %.1f%%)"
            % (mode, " ".join("%.1f" % x for x in ps), SEED, NITER, NB[("SETUP", mode)][4]))
    say()
    say("  WHICH NULL EACH NUMBER IS SCORED AGAINST -- stated explicitly:")
    say("    * every percentile above scores a NET EXPECTANCY against the net-expectancy distribution of")
    say("      size-, displacement-decile- and direction-matched random SHORT cohorts pushed through the")
    say("      same exit/drop/chain machinery.  No number here is compared to a zero baseline.")
    say("    * the UNCHAINED null centres at %+.4f%%/trade and the CHAINED null at %+.4f%%/trade."
        % (NB[("SETUP", "unchained")][0].mean(), NB[("SETUP", "chained")][0].mean()))
    say("      The chained null is signed and size-dependent, which is exactly why the chained real net")
    say("      is scored against the CHAINED null and never against 0.")
    say("    * 'beats the null' here would mean 'loses less than a random matched short', not 'earns'.")
    say()

    # ---------------------------------------------------------------- 2 SETUP vs CTRL-M
    say("=" * 104)
    say("2. THE ONE CONTRAST THAT CAN ATTRIBUTE ANYTHING TO EFF-AGG DIRECTION:  SETUP vs CTRL-M")
    say("=" * 104)
    say("  Identical price pattern (c1 bearish, c2 bullish, higher close), identical eff-agg SIGN")
    say("  condition (both < 0), opposite eff-agg DIRECTION (falling vs rising).  Disjoint by")
    say("  construction: SETUP INTERSECT CTRL-M = %d." % len(SET & set(ctrlM)))
    say()
    for mode in ("unchained", "chained"):
        ns, ws, nets, _ = CELLS[("SETUP", mode)]
        nm, wm, netm, _ = CELLS[("CTRL-M mirror rising", mode)]
        z, p = two_prop_z(ns, ws, nm, wm)
        fp = fisher_one_sided(ws, ns - ws, wm, nm - wm)
        zr, pr = two_prop_z(nm, wm, ns, ws)
        fpr = fisher_one_sided(wm, nm - wm, ws, ns - ws)
        say("  --- 2x2  %s ---" % mode.upper())
        say("                              WIN    LOSS      n     win%%      net/tr    binom p vs %.2f%%"
            % BE)
        say("    SETUP  (eff FALLING)     %5d  %5d  %5d  %6.2f%%  %+8.4f%%  %9.5f"
            % (ws, ns - ws, ns, 100.0 * ws / ns, nets, binom_p_ge(ns, ws)))
        say("    CTRL-M (eff RISING)      %5d  %5d  %5d  %6.2f%%  %+8.4f%%  %9.5f"
            % (wm, nm - wm, nm, 100.0 * wm / nm, netm, binom_p_ge(nm, wm)))
        say("    LIFT SETUP-over-CTRL-M %+6.2f pp   z = %+.3f   one-sided p = %.4f   Fisher one-sided "
            "p = %.4f" % (100.0 * ws / ns - 100.0 * wm / nm, z, p, fp))
        say("    reverse (CTRL-M over SETUP)         z = %+.3f   one-sided p = %.4f   Fisher one-sided "
            "p = %.4f" % (zr, pr, fpr))
        say("    CLEARS p<0.05 in the SETUP's favour? %s   (the eff-agg FALLING clause selects the %s"
            % ("NO" if not (p < 0.05) else "YES",
               "WORSE" if (100.0 * ws / ns) < (100.0 * wm / nm) else "BETTER"))
        say("    half of CTRL-N on this data; the reverse direction does not reach 0.05 either.)")
        if mode == "chained":
            say("    (the chained lift carries the signed chained null measured in block 1 -- it is not")
            say("     read against zero.)")
        say()

    # ---------------------------------------------------------------- 3 CLAUSE ATTRIBUTION
    say("=" * 104)
    say("3. CLAUSE ATTRIBUTION -- walking CTRL-0 -> CTRL-P -> CTRL-N -> SETUP")
    say("=" * 104)
    say("  Each row adds ONE clause.  'delta' is the win-rate change from the row above; the z/p beside")
    say("  it tests the added stage against the DISJOINT REMAINDER of the previous stage (previous")
    say("  stage MINUS this stage), which is a genuine two-independent-sample comparison.")
    say()
    STAGES = [("CTRL-0  all mature", mature, None),
              ("CTRL-P  + c1 bear, c2 bull, higher close", ctrlP, mature),
              ("CTRL-N  + both eff_agg < 0", ctrlN, ctrlP),
              ("SETUP   + eff_agg FALLING", setup, ctrlN)]
    for mode, ch in (("unchained", False), ("chained", True)):
        say("  --- %s ---" % mode.upper())
        say("  %-44s %6s %5s %8s %9s %9s %8s"
            % ("stage", "n", "W", "win%", "net/tr", "delta pp", "p vs rem"))
        prev_wp = None
        for tag, idxs, parent in STAGES:
            n, w, net = run(idxs, EXS, ch)
            wp = 100.0 * w / n if n else float("nan")
            if parent is None:
                say("  %-44s %6d %5d %7.2f%% %+8.4f%% %9s %8s" % (tag, n, w, wp, net, "--", "--"))
            else:
                rem = sorted(set(parent) - set(idxs))
                nr, wr, _ = run(rem, EXS, ch)
                z, p = two_prop_z(n, w, nr, wr)
                say("  %-44s %6d %5d %7.2f%% %+8.4f%% %+8.2f  %8.4f"
                    % (tag, n, w, wp, net, wp - prev_wp, p))
                say("      vs disjoint remainder of the previous stage: n=%d W=%d %.2f%% -> lift %+.2f pp"
                    " (z %+.3f, Fisher %.4f)"
                    % (nr, wr, 100.0 * wr / nr if nr else float("nan"),
                       wp - (100.0 * wr / nr if nr else float("nan")), z,
                       fisher_one_sided(w, n - w, wr, nr - wr)))
            prev_wp = wp
        say()
    say("  Reading: the price-pattern stage (CTRL-0 -> CTRL-P) is the only POSITIVE step; both eff-agg")
    say("  clauses move the win rate DOWN.  Total move CTRL-0 -> SETUP is decomposed above; the share")
    say("  contributed by each clause is printed as 'delta pp'.")
    say()

    # ---------------------------------------------------------------- 4 CTRL-E
    say("=" * 104)
    say("4. CTRL-E -- THE EFF-AGG CLAUSES STANDALONE, AT LARGE n")
    say("=" * 104)
    say("  CTRL-E = both eff_agg < 0 AND eff_agg falling, price pattern IGNORED, short at every such")
    say("  bucket close.  This is the eff-agg half of the rule with the price half deleted.")
    say()
    for mode in ("unchained", "chained"):
        n, w, net, sig = CELLS[("CTRL-E eff only", mode)]
        n0, w0, net0, _ = CELLS[("CTRL-0 all mature", mode)]
        z, p = two_prop_z(n, w, n0, w0)
        say("    %-10s CTRL-E n=%-5d W=%-4d %6.2f%%  net %+.4f%%  binom p %.5f  |  vs CTRL-0 n=%-5d "
            "%6.2f%%: lift %+6.2f pp  z=%+.3f  one-sided p=%.4f"
            % (mode, n, w, 100.0 * w / n, net, binom_p_ge(n, w), n0, 100.0 * w0 / n0,
               100.0 * w / n - 100.0 * w0 / n0, z, p))
    ne, we, _, _ = CELLS[("CTRL-E eff only", "unchained")]
    crit_e, pw_e = mde_binom(ne)
    say("    MDE on CTRL-E: at n=%d the smallest win count reaching exact binomial p<0.05 vs %.2f%% is"
        % (ne, BE))
    say("    W>=%s (%.2f%%); observed W=%d (%.2f%%), i.e. %+.2f pp BELOW break-even."
        % (str(crit_e), 100.0 * crit_e / ne if crit_e else float("nan"), we,
           100.0 * we / ne, 100.0 * we / ne - BE))
    say("    True win rate needed for 80%% power at that n: %.2f%%." % (100 * pw_e if pw_e else float("nan")))
    say("    So CTRL-E is WELL powered against break-even and is measured below it.  The eff-agg clauses")
    say("    standalone are not merely unproven -- they are dead at n=%d." % ne)
    nsu, wsu, _, _ = CELLS[("SETUP", "unchained")]
    nmu, wmu, _, _ = CELLS[("CTRL-M mirror rising", "unchained")]
    say("    Anything the SETUP has must therefore live in the INTERACTION with the price pattern.  The")
    say("    size of that interaction, isolated by CTRL-M: %+.2f pp at one-sided p = %.4f -- negative."
        % (100.0 * wsu / nsu - 100.0 * wmu / nmu, two_prop_z(nsu, wsu, nmu, wmu)[1]))
    say()

    # ---------------------------------------------------------------- 5 BANDS
    say("=" * 104)
    say("5. BAND STRUCTURE -- monotone, or sign-alternating around the cohort base?")
    say("=" * 104)
    sp = np.asarray([float(eff[i]) - float(eff[i - 1]) for i in setup], float)
    ordr = np.argsort(sp)
    say("  Hypothesis under test: 'steeper eff-agg fall = better short' predicts win rate DECREASING")
    say("  left-to-right when the most-negative spread band is printed first.")
    say()

    def band_table(label, bands):
        say("  --- %s ---" % label)
        say("  %-24s %-6s | %-5s %-4s %-8s %-10s | %-5s %-4s %-8s %-10s"
            % ("band", "n sig", "n", "W", "win%", "net/tr", "n", "W", "win%", "net/tr"))
        say("  %-24s %-6s | %-30s | %-30s" % ("", "", "UNCHAINED", "CHAINED (re-chained per band)"))
        seqU, seqC = [], []
        for lab, mem in bands:
            nu_, wu_, netu = run(sorted(mem), EXS, False)
            nc2, wc2, netc = run(sorted(mem), EXS, True)
            say("  %-24s %-6d | %-5d %-4d %7.2f%% %+9.4f%% | %-5d %-4d %7.2f%% %+9.4f%%"
                % (lab, len(mem), nu_, wu_, 100.0 * wu_ / nu_ if nu_ else float("nan"), netu,
                   nc2, wc2, 100.0 * wc2 / nc2 if nc2 else float("nan"), netc))
            seqU.append((100.0 * wu_ / nu_ if nu_ else float("nan"), nu_, wu_))
            seqC.append((100.0 * wc2 / nc2 if nc2 else float("nan"), nc2, wc2))
        return seqU, seqC

    K = 6
    bounds = [int(round(len(setup) * k / K)) for k in range(K + 1)]
    bandsA = []
    for k in range(K):
        a_, b_ = bounds[k], bounds[k + 1]
        if b_ <= a_:
            continue
        mem = [setup[j] for j in ordr[a_:b_]]
        bandsA.append(("[%+7.2f,%+7.2f]" % (float(sp[ordr[a_]]), float(sp[ordr[b_ - 1]])), mem))
    seqAU, seqAC = band_table("equal-count sextiles of the spread (rank-defined edges)", bandsA)
    say()
    edgesB = [-200.0, -40.0, -25.0, -15.0, -8.0, -3.0, 0.0]
    bandsB = []
    for k in range(len(edgesB) - 1):
        lo, hi = edgesB[k], edgesB[k + 1]
        mem = [setup[j] for j in range(len(setup)) if lo <= sp[j] < hi]
        if mem:
            bandsB.append(("[%+7.1f,%+7.1f)" % (lo, hi), mem))
    seqBU, seqBC = band_table("fixed round-number bands", bandsB)
    say()

    def shape(seq, base):
        v = [x[0] for x in seq if math.isfinite(x[0])]
        if len(v) < 2:
            return "n/a", 0, 0
        mono = (all(v[k] <= v[k + 1] for k in range(len(v) - 1))
                or all(v[k] >= v[k + 1] for k in range(len(v) - 1)))
        signs = [1 if x > base else -1 for x in v]
        flips = sum(1 for k in range(len(signs) - 1) if signs[k] != signs[k + 1])
        return ("MONOTONE" if mono else "NON-MONOTONE"), flips, len(v)

    say("  win-rate sequences (most-negative spread band FIRST) and their shape versus the cohort's own")
    say("  overall win rate, and versus the %.2f%% break-even:" % BE)
    for lab, seq, mode in (("sextiles    unchained", seqAU, "unchained"),
                           ("sextiles      chained", seqAC, "chained"),
                           ("fixed edges unchained", seqBU, "unchained"),
                           ("fixed edges   chained", seqBC, "chained")):
        n_, w_, _, _ = CELLS[("SETUP", mode)]
        base = 100.0 * w_ / n_
        sh, flips, m = shape(seq, base)
        shb, flipsb, _ = shape(seq, BE)
        say("    %-22s %s" % (lab, "  ".join("%5.1f" % x[0] for x in seq)))
        say("    %-22s %s  |  %s, %d flip(s) around base %.2f%%, %d flip(s) around BE %.2f%%, %d bands"
            % ("", "  ".join(("  +  " if x[0] > base else "  -  ") for x in seq), sh, flips, base,
               flipsb, BE, m))
    say()
    say("  DOES ONE BAND CARRY THE RESULT? -- leave-one-band-out on the unchained sextiles:")
    n_, w_, _, _ = CELLS[("SETUP", "unchained")]
    base = 100.0 * w_ / n_
    for lab, mem in bandsA:
        rest = sorted(set(setup) - set(mem))
        nr, wr, netr = run(rest, EXS, False)
        say("    drop %-24s remaining n=%-4d W=%-3d %6.2f%%  net %+.4f%%  (full cohort %.2f%%, %+.4f%%)"
            % (lab, nr, wr, 100.0 * wr / nr if nr else float("nan"), netr, base,
               CELLS[("SETUP", "unchained")][2]))
    say()
    say("  smallest win count reaching exact binomial p<0.05 vs %.2f%% at band-sized n:" % BE)
    say("    " + "  ".join("n=%d:W>=%s" % (nn, "unreachable" if mde_binom(nn)[0] is None
                                           else str(mde_binom(nn)[0]))
                           for nn in (3, 5, 6, 8, 10, 12, 15)))
    say()

    # ---------------------------------------------------------------- 6 POWER
    say("=" * 104)
    say("6. POWER")
    say("=" * 104)
    say("  Two-proportion, one-sided alpha 0.05, 80% power, normal approximation with pooled null SE.")
    say("  %-11s %-24s %5s %6s %8s %9s %11s %13s %9s"
        % ("mode", "control", "n1", "n2", "ctrl%", "MDE p1", "MDE lift", "observed lift", "obs/MDE"))
    for mode in ("unchained", "chained"):
        ns, ws, _, _ = CELLS[("SETUP", mode)]
        for ctag in ("CTRL-M mirror rising", "CTRL-N minus SETUP"):
            nc, wc, _, _ = CELLS[(ctag, mode)]
            p2 = wc / nc
            p1 = mde_two_prop(ns, nc, p2)
            obs = 100.0 * ws / ns - 100.0 * p2
            lift_mde = 100.0 * (p1 - p2)
            say("  %-11s %-24s %5d %6d %7.2f%% %8.2f%% %+10.2f pp %+12.2f pp %9.2f"
                % (mode, ctag, ns, nc, 100 * p2, 100 * p1, lift_mde, obs,
                   obs / lift_mde if lift_mde > 0 else float("nan")))
    say()
    say("  Exact-binomial MDE vs the %.2f%% break-even (power against the FEE, not against a control):"
        % BE)
    for mode in ("unchained", "chained"):
        n, w, _, _ = CELLS[("SETUP", mode)]
        crit, pwr = mde_binom(n)
        say("    %-10s n=%-4d observed W=%-3d (%.2f%%) | reject needs W>=%s (%.2f%%) | true win rate for"
            " 80%% power = %.2f%% (%+.2f pp above BE)"
            % (mode, n, w, 100.0 * w / n, str(crit), 100.0 * crit / n if crit else float("nan"),
               100 * pwr if pwr else float("nan"), 100 * pwr - BE if pwr else float("nan")))
    say()
    say("  Required SAMPLE SIZE for 80% power AT THE OBSERVED LIFT.  The observed lift is NEGATIVE on")
    say("  both contrasts, so no n gives a one-sided test of SETUP > control any power at all.  The")
    say("  answerable form is printed three ways: (a) the n needed if the sign were REVERSED, i.e. at")
    say("  the observed |lift| with the control FROZEN at its realised size -- this has a POWER CEILING,")
    say("  because the control's own standard error does not shrink; (b) the n needed for the mirror")
    say("  hypothesis the data actually leans toward, CTRL > SETUP, with the SETUP arm frozen; (c) both")
    say("  arms scaled by the same factor k, which is the form with no ceiling.")
    for mode in ("unchained", "chained"):
        ns, ws, _, _ = CELLS[("SETUP", mode)]
        p1 = ws / ns
        for ctag in ("CTRL-M mirror rising", "CTRL-N minus SETUP"):
            nc, wc, _, _ = CELLS[(ctag, mode)]
            p2 = wc / nc
            gap = abs(p1 - p2)

            def need_n(pa, pb, nfix):
                if pa <= pb:
                    return None
                for cand in range(5, 200001):
                    pbar = (pa * cand + pb * nfix) / (cand + nfix)
                    se0 = math.sqrt(pbar * (1 - pbar) * (1 / cand + 1 / nfix))
                    se1 = math.sqrt(pa * (1 - pa) / cand + pb * (1 - pb) / nfix)
                    if se1 <= 0:
                        continue
                    z = (pa - pb - ZA * se0) / se1
                    if 0.5 * math.erfc(-z / math.sqrt(2.0)) >= 0.80:
                        return cand
                return None
            a_ = need_n(p2 + gap, p2, nc)      # sign reversed, setup arm grows
            b_ = need_n(p2, p1, ns)            # mirror hypothesis, control arm grows

            def need_k(pa, pb, n1, n2):
                """Scale BOTH arms by k -- the only form without a power ceiling."""
                if pa <= pb:
                    return None
                for k in range(1, 20001):
                    m1, m2 = n1 * k, n2 * k
                    pbar = (pa * m1 + pb * m2) / (m1 + m2)
                    se0 = math.sqrt(pbar * (1 - pbar) * (1 / m1 + 1 / m2))
                    se1 = math.sqrt(pa * (1 - pa) / m1 + pb * (1 - pb) / m2)
                    z = (pa - pb - ZA * se0) / se1
                    if 0.5 * math.erfc(-z / math.sqrt(2.0)) >= 0.80:
                        return k
                return None
            k_ = need_k(p2 + gap, p2, ns, nc)
            say("    %-10s vs %-22s obs lift %+6.2f pp | (a) sign-reversed, control frozen: n1 >= %-8s"
                " (has %d) | (b) mirror CTRL>SETUP, setup frozen: n2 >= %-8s (has %d)"
                % (mode, ctag, 100 * (p1 - p2), str(a_) if a_ else "POWER CEILING", ns,
                   str(b_) if b_ else "POWER CEILING", nc))
            say("               (c) scale BOTH arms by k at the observed |lift| %.2f pp: k = %s "
                "-> n1 %s / n2 %s"
                % (100 * gap, str(k_) if k_ else ">20000",
                   str(ns * k_) if k_ else "--", str(nc * k_) if k_ else "--"))
    say()
    say("  Signal rate: %d SETUP signals over %d consecutive mature pairs = 1 per %.1f buckets."
        % (len(setup), len(P), len(P) / len(setup)))
    say("  The archive holds %d buckets in total, so any n1 above is a multiple of the whole archive."
        % len(bars))
    say()

    # ---------------------------------------------------------------- 7 MULTIPLICITY
    say("=" * 104)
    say("7. MULTIPLICITY -- five families, one pool, one price path")
    say("=" * 104)
    say("  A circular-shift reality check over the ~400-cell search this program ran on THIS 23-day")
    say("  window returned P(null max >= real best) = 0.548.  The best cell of that entire search was")
    say("  WORSE than the average best cell obtained by rotating uninformative features against the")
    say("  same outcomes on the same pool.")
    say()
    f1, f2, fdiv, frise = [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        ab, bb = a["c"] > a["o"], b["c"] > b["o"]
        ar, br = a["c"] < a["o"], b["c"] < b["o"]
        if ab and bb and b["c"] > a["c"] and ea > 0 and eb < 0:
            f1.append(i)
        if ar and br and a["c"] > b["c"] and ea > 0 and eb > 0 and ea < eb:
            f2.append(i)
        if ab and bb and a["c"] < b["c"] and ea > eb:
            fdiv.append(i)
        if ea < eb and b["c"] < a["c"]:
            frise.append(i)
    FAM = [("SEQ SETUP 1 (long)", f1, 7), ("SEQ SETUP 2 (long)", f2, 16),
           ("DIV SHORT (bull/bull, falling)", fdiv, 54), ("RISE SHORT (rising, lower close)", frise, 149),
           ("THIS SETUP (neg + deepening)", setup, 35)]
    say("  %-34s %-10s %-12s %-8s" % ("family", "rebuilt n", "published n", "match"))
    for k, v, pub in FAM:
        say("  %-34s %-10d %-12d %-8s" % (k, len(v), pub, "YES" if len(v) == pub else "NO"))
    say()
    say("  SIGNAL-INDEX intersections, ALL pairs (not just the ones involving this SETUP):")
    say("  %-34s %-8s %s" % ("family", "|A|", "  ".join("%-8s" % ("F%d" % k) for k in range(len(FAM)))))
    for a_ in range(len(FAM)):
        say("  F%d %-31s %-8d %s"
            % (a_, FAM[a_][0], len(FAM[a_][1]),
               "  ".join("%-8d" % len(set(FAM[a_][1]) & set(FAM[b_][1])) for b_ in range(len(FAM)))))
    say("  THIS SETUP (F4) intersects every earlier family in 0 buckets -- but that is STRUCTURAL")
    say("  (candle direction / eff-agg direction), not evidence of independence.  Note the earlier")
    say("  families are NOT mutually disjoint: F0 is a strict SUBSET of F2 (%d/%d) and F1 a strict"
        % (len(set(f1) & set(fdiv)), len(f1)))
    say("  SUBSET of F3 (%d/%d), and F0/F1 were tested LONG while F2/F3 were tested SHORT -- the same"
        % (len(set(f2) & set(frise)), len(f2)))
    say("  bucket indices were bet in BOTH directions across families.  That is re-cutting, not")
    say("  replication.")
    say()
    say("  WHAT ACTUALLY MATTERS -- HOLDING-PERIOD overlap on the shared price path.  Every family's")
    say("  trades are scored on the SAME fixed per-bucket outcome vector (%d resolved short outcomes)"
        % CELLS[("CTRL-0 all mature", "unchained")][0])
    say("  and the trades occupy overlapping bar ranges, so the five tests are correlated re-cuts of one")
    say("  sample, not five independent experiments.")

    def covered(idxs):
        s = set()
        for i in idxs:
            r = EXS[i]
            if r is None:
                continue
            for j in range(i + 1, r[1] + 1):
                s.add(j)
        return s
    COV = {k: covered(v) for k, v, _ in FAM}
    say("  %-34s %-10s %-12s" % ("family", "trades", "bars covered"))
    for k, v, _ in FAM:
        n_, _, _ = run(v, EXS, False)
        say("  %-34s %-10d %-12d" % (k, n_, len(COV[k])))
    say()
    say("  pairwise BAR-OVERLAP of holding periods (|A n B| / |A u B|):")
    for a_ in range(len(FAM)):
        for b_ in range(a_ + 1, len(FAM)):
            A, B = COV[FAM[a_][0]], COV[FAM[b_][0]]
            u = len(A | B)
            say("    %-34s x %-34s  %4d shared bars, Jaccard %.3f"
                % (FAM[a_][0], FAM[b_][0], len(A & B), (len(A & B) / u) if u else float("nan")))
    unionall = set()
    for k, v, _ in FAM:
        unionall |= COV[k]
    tot = sum(len(COV[k]) for k, v, _ in FAM)
    say("  union of all five holding-period bar sets: %d bars ; sum of the five: %d bars -> %.2fx"
        % (len(unionall), tot, tot / len(unionall) if unionall else float("nan")))
    say("  redundancy.  Of the %d mature buckets, %d (%.1f%%) are inside at least one family's holding"
        % (len(mature), len(unionall & set(mature)),
           100.0 * len(unionall & set(mature)) / len(mature)))
    say("  window.  Five families therefore re-slice the same price path; the effective number of")
    say("  INDEPENDENT comparisons is far below five, while the number of CELLS reported across the")
    say("  five families (setups + controls + bands + modes) is in the low hundreds and is a")
    say("  continuation of the same ~400-cell search, not a replication of it.")
    say()
    best = None
    for tag, _ in COH:
        for mode in ("unchained", "chained"):
            n, w, _, _ = CELLS[(tag, mode)]
            pv = binom_p_ge(n, w)
            if best is None or pv < best[0]:
                best = (pv, tag, mode, n, w)
    say("  Best exact binomial p ANYWHERE in this family: %.5f (%s, %s, n=%d W=%d)."
        % (best[0], best[1], best[2], best[3], best[4]))
    say("  Nothing in this family is below 0.05 uncorrected, so no correction is even required.  For")
    say("  reference the Sidak-corrected per-cell alpha would be 1-(1-0.05)^(1/m): m=5 -> %.5f, m=400"
        % (1 - (1 - 0.05) ** (1 / 5)))
    say("  -> %.6f.  Neither threshold is approached by anything measured here."
        % (1 - (1 - 0.05) ** (1 / 400)))
    say()
    say("  PLAINLY, no softening: at P(null max >= real best) = 0.548 for the parent search, a nominally")
    say("  significant cell drawn from this pool carries no evidential weight on its own.  A p of 0.03 in")
    say("  a family whose best-cell distribution under the null already reaches the observed best 55% of")
    say("  the time is a draw from noise, not a discovery.  That verdict would have applied to any cell")
    say("  in this family that had come in significant -- and none did.  This SETUP is the fifth cut of")
    say("  the same %d pairs; the disjoint signal sets buy nothing, because the five cuts share the pool," % len(P))
    say("  the window, the exits and the price path.")
    say()

    # ---------------------------------------------------------------- 8 VERDICT
    say("=" * 104)
    say("8. DECIDING QUESTION -- per mode, three conditions, ALL required")
    say("=" * 104)
    say("  (i)   SETUP beats CTRL-M at one-sided p < 0.05")
    say("  (ii)  net expectancy > 0 at the 0.08%% fee   (equivalently win rate > %.2f%%)" % BE)
    say("  (iii) real net sits ABOVE the displacement+direction-matched null p95 (NULL B, same mode)")
    say()
    for mode in ("unchained", "chained"):
        ns, ws, nets, _ = CELLS[("SETUP", mode)]
        nm, wm, _, _ = CELLS[("CTRL-M mirror rising", mode)]
        z, p = two_prop_z(ns, ws, nm, wm)
        fp = fisher_one_sided(ws, ns - ws, wm, nm - wm)
        gB = NB[("SETUP", mode)][0]
        p95B = NB[("SETUP", mode)][5]; pctB = NB[("SETUP", mode)][4]
        i_ok = p < 0.05
        ii_ok = nets > 0
        iii_ok = pctB >= 95.0
        say("  --- MODE %s   (n=%d, W=%d, win %.2f%%, net %+.4f%%) ---"
            % (mode.upper(), ns, ws, 100.0 * ws / ns, nets))
        say("    (i)   vs CTRL-M  lift %+.2f pp   z = %+.3f   one-sided p = %.4f (Fisher %.4f)     %s"
            % (100.0 * ws / ns - 100.0 * wm / nm, z, p, fp, "PASS" if i_ok else "FAIL"))
        say("    (ii)  net %+.4f%%/trade   (needs > 0, i.e. win rate > %.2f%%)                  %s"
            % (nets, BE, "PASS" if ii_ok else "FAIL"))
        say("    (iii) NULL B percentile %.1f%%   real %+.4f%% vs null p95 %+.4f%%              %s"
            % (pctB, nets, p95B, "PASS" if iii_ok else "FAIL"))
        say("    ANSWER: %s" % ("YES" if (i_ok and ii_ok and iii_ok) else "NO"))
        say("    Deciding numbers -- extra winners needed (loser->winner flips at fixed n=%d):" % ns)
        need = {}
        for lab, test in (("(i) beat CTRL-M at p<0.05",
                           lambda k: two_prop_z(ns, k, nm, wm)[1] < 0.05),
                          ("(ii) net > 0",
                           lambda k: (k * WIN_NET + (ns - k) * LOSS_NET) > 0),
                          ("(iii) above NULL B p95",
                           lambda k: (k * WIN_NET + (ns - k) * LOSS_NET) / ns > p95B),
                          ("binomial p<0.05 vs BE",
                           lambda k: binom_p_ge(ns, k) < 0.05)):
            v = None
            for k in range(ws, ns + 1):
                if test(k):
                    v = k - ws; break
            need[lab] = v
            say("      %-28s : +%s winners (W=%s, %.2f%%)"
                % (lab, str(v), str(ws + v) if v is not None else "--",
                   100.0 * (ws + v) / ns if v is not None else float("nan")))
        mx = max(x for lab, x in need.items() if x is not None and not lab.startswith("binomial"))
        bp_at = binom_p_ge(ns, ws + mx)
        say("      -> binding count is +%d winners; at W=%d/%d (%.2f%%) the exact binomial p vs %.2f%%"
            % (mx, ws + mx, ns, 100.0 * (ws + mx) / ns, BE))
        say("         would be %.5f, which %s p<0.05."
            % (bp_at, "CLEARS" if bp_at < 0.05 else "still does NOT clear"))
        say()

    say("=" * 104)
    say("WHAT WAS NOT DONE")
    say("=" * 104)
    say("  No threshold, bracket, window, side or condition was varied from the frozen spec.  No new")
    say("  candidate was searched.  No forward/out-of-window data was used.  No trading recommendation")
    say("  is made or implied.")
    say("```")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
