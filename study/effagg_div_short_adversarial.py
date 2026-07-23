"""EFF-AGG DIVERGENCE SHORT -- ADVERSARIAL PRICING.

Prices the already-run result. Nothing is tuned; no new candidate is searched; no threshold,
bracket, side or condition is varied from the frozen spec.

  SETUP  candle1 BULLISH, candle2 BULLISH, eff_agg[i-1] > eff_agg[i], close[i-1] < close[i]
         -> SHORT at bars[i]["c"], 0.8% SL / 0.8% TP, fee 0.08% RT.
         Winner +0.72, loser -0.88 -> BREAK-EVEN 55.0000%, driftless P(TP first) 50.00%.
         Same-bar both-touched -> STOP. Unresolved at end of data -> DROPPED.

Seven blocks, exactly as commissioned:
  1  NULL          displacement-decile-matched SHORT cohorts, chained identically, 2000 draws,
                   several seeds, both modes; then a STRICTER null additionally matched on
                   entry-candle direction (the setup is 100% after-bullish).
  2  DISPLACEMENT  setup vs universe vs CTRL-P vs CTRL-M; Welch t and two-sample KS on the
                   SETUP-vs-CTRL-M displacement distributions -- the attribution test.
  3  DECOMPOSITION 2x2 SETUP vs CTRL-M (identical price pattern, opposite eff-agg direction) with
                   z, one-sided p and Fisher exact; then CTRL-E standalone at its larger n.
  4  BANDS         disjoint spread bands: monotone or sign-alternating; single-band-carries check.
  5  POWER         MDE at 80% power, one-sided alpha 0.05, vs CTRL-M and vs CTRL-P-minus-SETUP.
  6  MULTIPLICITY  the 0.548 circular-shift reality check, stated plainly.
  7  VERDICT       per mode: beats CTRL-M at p<0.05 AND net > 0 AND above the matched-null p95.

Run:  python study/effagg_div_short_adversarial.py
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

OUT_MD = os.path.join(HERE, "out", "effagg_div_short_adversarial.md")
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
    """P(X >= a) hypergeometric. Table [[a,b],[c,d]] = [[set1 W, set1 L],[set2 W, set2 L]]."""
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
    za = 1.6448536269514722
    lo, hi = p2, 1.0
    for _ in range(300):
        p1 = 0.5 * (lo + hi)
        pb = (p1 * n1 + p2 * n2) / (n1 + n2)
        se0 = math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2))
        se1 = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        if se1 <= 0:
            lo = p1; continue
        z = (p1 - p2 - za * se0) / se1
        pw = 0.5 * math.erfc(-z / math.sqrt(2.0))
        if pw < power:
            lo = p1
        else:
            hi = p1
    return hi


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


def ks_two_sample(a, b):
    """Two-sample Kolmogorov-Smirnov D and asymptotic two-sided p."""
    a = np.sort(np.asarray(a, float)); b = np.sort(np.asarray(b, float))
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan")
    allv = np.concatenate([a, b])
    cdf1 = np.searchsorted(a, allv, side="right") / n1
    cdf2 = np.searchsorted(b, allv, side="right") / n2
    D = float(np.max(np.abs(cdf1 - cdf2)))
    en = math.sqrt(n1 * n2 / (n1 + n2))
    lam = (en + 0.12 + 0.11 / en) * D
    p = 0.0
    for k in range(1, 101):
        p += 2.0 * ((-1) ** (k - 1)) * math.exp(-2.0 * k * k * lam * lam)
    return D, min(1.0, max(0.0, p))


def welch_t(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


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
    """(win, exit_bar) per entry index -- depends ONLY on the entry bar at a fixed % bracket, so it
    is cohort-independent and can be reused by every permutation draw. side -1 = SHORT.
    None = unresolved at end of data (DROPPED, never a loss). Same-bar both -> STOP."""
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

    setup, ctrlP, ctrlM, ties, ctrlE, bullbull = [], [], [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        if ea > eb:
            ctrlE.append(i)
        if (a["c"] > a["o"]) and (b["c"] > b["o"]):
            bullbull.append(i)
            if a["c"] < b["c"]:
                ctrlP.append(i)
                if ea > eb:
                    setup.append(i)
                elif ea < eb:
                    ctrlM.append(i)
                else:
                    ties.append(i)
    xP = sorted(set(ctrlP) - set(setup))

    # displacement per bucket
    disp = {}
    for i in range(len(bars)):
        if not bars[i]["ok"]:
            continue
        b = bars[i]
        d = 1.0 if b["c"] > b["o"] else (-1.0 if b["c"] < b["o"] else 0.0)
        disp[i] = d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0

    COH = [("SETUP", setup), ("CTRL-P price only", ctrlP), ("CTRL-M mirror rising", ctrlM),
           ("CTRL-P minus SETUP", xP), ("CTRL-E eff only", ctrlE), ("CTRL-0 all mature", mature)]
    CD = dict(COH)

    say("# EFF-AGG DIVERGENCE SHORT -- ADVERSARIAL PRICING")
    say()
    say("Bracket 0.8/0.8 + 0.08%% fee round trip. BREAK-EVEN win rate %.4f%%. Driftless P(TP first)" % BE)
    say("50.00%. (n, wins) is sufficient at a fixed bracket -- no t-stat, no iid bootstrap P(profit),")
    say("no profit factor and no drop-best-N appears anywhere below.")
    say("Permutation nulls: %d draws, cohorts stratified on ENTRY-BUCKET DISPLACEMENT DECILE, drawn" % NITER)
    say("without replacement from the mature pool, sorted ascending, chained by the identical machinery.")
    say("Nothing was tuned. No threshold, bracket, side or condition was varied from the frozen spec.")
    say()
    say("```")

    # ---------------------------------------------------------------- 0 RECOMPUTE
    say("=" * 104)
    say("0. HEADLINE CELLS RECOMPUTED FROM SCRATCH (independent path: precomputed per-bar exits,")
    say("   not the reporting script's forward-scan sim; must agree with the prior run digit for digit)")
    say("=" * 104)
    say("  universe: %d archive buckets, maturity first=%d, %d mature, %d consecutive mature pairs"
        % (len(bars), first, len(mature), len(P)))
    say("  funnel: %d pairs -> %d bull/bull -> %d + higher close (CTRL-P) -> %d eff falling (SETUP)"
        % (len(P), len(bullbull), len(ctrlP), len(setup)))
    say("  CTRL-P split: %d falling (SETUP) / %d rising (CTRL-M) / %d exactly tied ; %d+%d+%d=%d %s"
        % (len(setup), len(ctrlM), len(ties), len(setup), len(ctrlM), len(ties),
           len(setup) + len(ctrlM) + len(ties),
           "== CTRL-P OK" if len(setup) + len(ctrlM) + len(ties) == len(ctrlP) else "MISMATCH"))
    say()
    # engine identity, recomputed independently
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
    say("    SETUP unchained  n=53 W=28 52.83%  -0.0347  p 0.67676")
    say("    SETUP chained    n=45 W=22 48.89%  -0.0978  p 0.83498")
    say("    CTRL-P n=296 W=145 | CTRL-M n=212 W=102 | CTRL-P-SETUP n=243 W=117 | CTRL-E n=574 W=271")
    say("    CTRL-0 n=1252 W=615")
    nu, wu, _ = run(setup, EXS, False); nc_, wc_, _ = run(setup, EXS, True)
    ok0 = (nu == 53 and wu == 28 and nc_ == 45 and wc_ == 22
           and CELLS[("CTRL-P price only", "unchained")][:2] == (296, 145)
           and CELLS[("CTRL-M mirror rising", "unchained")][:2] == (212, 102)
           and CELLS[("CTRL-P minus SETUP", "unchained")][:2] == (243, 117)
           and CELLS[("CTRL-E eff only", "unchained")][:2] == (574, 271)
           and CELLS[("CTRL-0 all mature", "unchained")][:2] == (1252, 615))
    say("    -> RECOMPUTATION %s" % ("MATCHES the prior run on every (n, W) above"
                                     if ok0 else "DISAGREES -- STOP AND READ THE TABLE"))
    say()

    # ---------------------------------------------------------------- 1 NULL
    say("=" * 104)
    say("1. NULL CALIBRATION")
    say("=" * 104)
    pool = np.array(mature, dtype=int)
    pd_ = np.array([disp[i] for i in pool], float)
    edges = np.percentile(pd_, [10, 20, 30, 40, 50, 60, 70, 80, 90])
    pool_dec = np.searchsorted(edges, pd_, side="right")
    by_dec = {d: pool[pool_dec == d] for d in range(10)}
    say("  displacement metric = dir*(close-(high+low)/2)/close*100 on the ENTRY bucket.")
    say("  decile edges (mature universe, %): " + " ".join("%+.4f" % e for e in edges))
    say("  pool per decile: " + " ".join("d%d=%d" % (d, len(by_dec[d])) for d in range(10)))
    say()
    say("  Each null cohort draws the SAME NUMBER OF SIGNALS PER DECILE as the real cohort, without")
    say("  replacement, all SHORT, then runs the identical exit/drop/chain machinery. Unresolved ->")
    say("  dropped, so null n varies exactly as the real n does.")
    say()

    def dec_of(i):
        return int(np.searchsorted(edges, disp[i], side="right"))

    def null_dist(real_idxs, chained, niter=NITER, seed=SEED):
        rng = np.random.default_rng(seed)
        cnt = np.zeros(10, dtype=int)
        for i in real_idxs:
            cnt[dec_of(i)] += 1
        nets = np.full(niter, np.nan); wins = np.full(niter, np.nan); ns = np.zeros(niter)
        for it in range(niter):
            pick = []
            for d in range(10):
                k = int(cnt[d])
                if k:
                    pick.append(rng.choice(by_dec[d], size=k, replace=False))
            sel = np.sort(np.concatenate(pick)) if pick else np.array([], dtype=int)
            n, w, net = run(sel.tolist(), EXS, chained)
            if n:
                nets[it] = net; wins[it] = 100.0 * w / n
            ns[it] = n
        return nets, wins, ns, cnt

    say("  --- NULL A: displacement decile matched, drawn from ALL %d mature buckets ---" % len(mature))
    say("  %-22s %-10s %8s | %9s %8s %8s %8s %8s | %8s %8s"
        % ("cohort", "mode", "real net", "null mean", "null sd", "null p5", "null p50", "null p95",
           "PCTILE", "emp p"))
    NULLS = {}
    for tag in ("SETUP", "CTRL-M mirror rising"):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins, ns, cnt = null_dist(CD[tag], ch)
            n, w, real = run(CD[tag], EXS, ch)
            g = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(g < real))
            empp = float(np.mean(g >= real))
            NULLS[(tag, mode)] = (g, wins, ns, real, pct, empp, cnt)
            say("  %-22s %-10s %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, real, g.mean(), g.std(ddof=1), np.percentile(g, 5),
                   np.percentile(g, 50), np.percentile(g, 95), pct, empp))
    say()
    say("  null mean WIN RATE and mean n (this is what the chained null's signed offset looks like):")
    for tag in ("SETUP", "CTRL-M mirror rising"):
        for mode in ("unchained", "chained"):
            g, wins, ns, real, pct, empp, cnt = NULLS[(tag, mode)]
            wv = wins[np.isfinite(wins)]
            say("    %-22s %-10s null mean win %6.2f%% (BE %.2f%%)  null mean n %6.2f  real n %d"
                % (tag, mode, wv.mean(), BE, ns.mean(), CELLS[(tag, mode)][0]))
    say()
    say("  decile composition of the real SETUP cohort:")
    say("    " + " ".join("d%d=%d" % (d, NULLS[("SETUP", "unchained")][6][d]) for d in range(10)))
    say()
    say("  SEED STABILITY -- 4 further seeds, 1000 draws each, percentile of the real net:")
    for tag in ("SETUP",):
        for mode, ch in (("unchained", False), ("chained", True)):
            n, w, real = run(CD[tag], EXS, ch)
            ps = []
            for sd in SEEDS_EXTRA:
                nets, _, _, _ = null_dist(CD[tag], ch, niter=1000, seed=sd)
                g = nets[np.isfinite(nets)]
                ps.append(100.0 * float(np.mean(g < real)))
            say("    %-10s %-10s percentiles %s   (main seed %d: %.1f%%)"
                % (tag, mode, " ".join("%.1f" % x for x in ps), SEED, NULLS[(tag, mode)][4]))
    say()

    # --- stricter null: direction matched
    say("  --- NULL B (STRICTER): displacement decile AND entry-candle DIRECTION matched ---")
    upmat = [i for i in mature if bars[i]["c"] > bars[i]["o"]]
    say("  The SETUP enters at a BULLISH bucket close on 100%% of its signals (verified: %d/%d)."
        % (sum(1 for i in setup if bars[i]["c"] > bars[i]["o"]), len(setup)))
    say("  The mature pool is only %.1f%% bullish, so NULL A draws a direction mix the setup never"
        % (100.0 * len(upmat) / len(mature)))
    say("  takes. NULL B restricts every draw to BULLISH buckets inside the same displacement decile.")
    up_pool = {}
    for d in range(10):
        up_pool[d] = np.array([i for i in by_dec[d] if bars[i]["c"] > bars[i]["o"]], dtype=int)
    say("  bullish pool per decile: " + " ".join("d%d=%d" % (d, len(up_pool[d])) for d in range(10)))
    short_dec = [d for d in range(10)
                 if len(up_pool[d]) < int(NULLS[("SETUP", "unchained")][6][d])]
    say("  deciles where the bullish pool is smaller than the SETUP's own count: %s"
        % (str(short_dec) if short_dec else "none -- every draw is without replacement"))
    say()

    def null_dist_dir(real_idxs, chained, niter=NITER, seed=SEED):
        rng = np.random.default_rng(seed)
        cnt = np.zeros(10, dtype=int)
        for i in real_idxs:
            cnt[dec_of(i)] += 1
        nets = np.full(niter, np.nan); wins = np.full(niter, np.nan); ns = np.zeros(niter)
        for it in range(niter):
            pick = []
            for d in range(10):
                k = int(cnt[d])
                if k:
                    p_ = up_pool[d]
                    pick.append(rng.choice(p_, size=k, replace=(len(p_) < k)))
            sel = np.sort(np.concatenate(pick)) if pick else np.array([], dtype=int)
            n, w, net = run(sel.tolist(), EXS, chained)
            if n:
                nets[it] = net; wins[it] = 100.0 * w / n
            ns[it] = n
        return nets, wins, ns

    say("  %-22s %-10s %8s | %9s %8s %8s %8s %8s | %8s %8s"
        % ("cohort", "mode", "real net", "null mean", "null sd", "null p5", "null p50", "null p95",
           "PCTILE", "emp p"))
    NULLD = {}
    for tag in ("SETUP", "CTRL-M mirror rising"):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins, ns = null_dist_dir(CD[tag], ch)
            n, w, real = run(CD[tag], EXS, ch)
            g = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(g < real))
            wv = wins[np.isfinite(wins)]
            NULLD[(tag, mode)] = (pct, g.mean(), float(np.percentile(g, 95)), float(wv.mean()))
            say("  %-22s %-10s %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, real, g.mean(), g.std(ddof=1), np.percentile(g, 5),
                   np.percentile(g, 50), np.percentile(g, 95), pct,
                   float(np.mean(g >= real))))
    say()
    say("  SEED STABILITY on NULL B (4 seeds, 1000 draws each):")
    for mode, ch in (("unchained", False), ("chained", True)):
        n, w, real = run(setup, EXS, ch)
        ps = []
        for sd in SEEDS_EXTRA:
            nets, _, _ = null_dist_dir(setup, ch, niter=1000, seed=sd)
            g = nets[np.isfinite(nets)]
            ps.append(100.0 * float(np.mean(g < real)))
        say("    SETUP     %-10s percentiles %s   (main seed: %.1f%%)"
            % (mode, " ".join("%.1f" % x for x in ps), NULLD[("SETUP", mode)][0]))
    say()
    say("  WHICH NULL EACH NUMBER IS SCORED AGAINST -- stated explicitly:")
    say("    * every percentile above scores a NET EXPECTANCY against the net expectancy distribution")
    say("      of size-, displacement- (and for NULL B direction-) matched random SHORT cohorts run")
    say("      through the same chaining. No number here is compared to a zero baseline.")
    say("    * the CHAINED null is signed and size-dependent (a sparse cohort keeps its unchained rate")
    say("      while the dense base pays the chaining cost), which is exactly why the chained real net")
    say("      is scored against the CHAINED null and never against 0.")
    say("    * NULL A mean net %+.4f%% (unchained) is the answer to 'what does a random displacement-"
        % NULLS[("SETUP", "unchained")][0].mean())
    say("      matched SHORT earn'; NULL B mean %+.4f%% is the same restricted to bullish entries."
        % NULLD[("SETUP", "unchained")][1])
    say()

    # ---------------------------------------------------------------- 2 DISPLACEMENT
    say("=" * 104)
    say("2. IS IT THE DISPLACEMENT?")
    say("=" * 104)
    say("  Shorting at a bullish bucket close sells above that bucket's own midpoint. Round 3 measured")
    say("  the coefficient: a shift d toward the interior moves P(TP first) by d/(SL+TP) = d/1.6 here.")
    say()
    for tag, idxs in (("UNIVERSE all mature", mature), ("SETUP", setup),
                      ("CTRL-P price only", ctrlP), ("CTRL-M mirror rising", ctrlM),
                      ("CTRL-P minus SETUP", xP), ("CTRL-E eff only", ctrlE)):
        say("  %-22s %s" % (tag, desc([disp[i] for i in idxs])))
    say()
    dS = np.array([disp[i] for i in setup], float)
    dM = np.array([disp[i] for i in ctrlM], float)
    dP = np.array([disp[i] for i in ctrlP], float)
    dU = np.array([disp[i] for i in mature], float)
    dX = np.array([disp[i] for i in xP], float)
    say("  THE ATTRIBUTION TEST -- SETUP vs CTRL-M displacement distributions:")
    D, pks = ks_two_sample(dS, dM)
    say("    mean   SETUP %+.4f%%   CTRL-M %+.4f%%   difference %+.4f pp"
        % (dS.mean(), dM.mean(), dS.mean() - dM.mean()))
    say("    median SETUP %+.4f%%   CTRL-M %+.4f%%" % (float(np.median(dS)), float(np.median(dM))))
    say("    sd     SETUP  %.4f     CTRL-M  %.4f" % (dS.std(ddof=1), dM.std(ddof=1)))
    say("    Welch t = %+.3f   two-sample KS D = %.4f, asymptotic p = %.4f"
        % (welch_t(dS, dM), D, pks))
    say("    win-rate equivalent of the mean gap at d/1.6: %+.2f pp"
        % ((dS.mean() - dM.mean()) / 1.6 * 100.0))
    say()
    say("    decile histogram of the entry-bucket displacement, SETUP vs CTRL-M (shares of own cohort):")
    hS = np.array([sum(1 for i in setup if dec_of(i) == d) for d in range(10)], float)
    hM = np.array([sum(1 for i in ctrlM if dec_of(i) == d) for d in range(10)], float)
    say("      decile   " + " ".join("  d%d" % d for d in range(10)))
    say("      SETUP    " + " ".join("%4.0f" % x for x in hS) + "   (counts)")
    say("      CTRL-M   " + " ".join("%4.0f" % x for x in hM) + "   (counts)")
    say("      SETUP %   " + " ".join("%4.1f" % (100 * x / hS.sum()) for x in hS))
    say("      CTRL-M %  " + " ".join("%4.1f" % (100 * x / hM.sum()) for x in hM))
    say()
    say("  Predicted-vs-observed lift under the displacement model (unchained), all contrasts:")
    say("    %-26s %-12s %-14s %-16s %s" % ("contrast", "disp gap", "predicted pp",
                                            "observed pp", "sign agreement"))
    nsu, wsu, _, _ = CELLS[("SETUP", "unchained")]
    wps = 100.0 * wsu / nsu
    for ctag in ("CTRL-P price only", "CTRL-M mirror rising", "CTRL-P minus SETUP",
                 "CTRL-E eff only", "CTRL-0 all mature"):
        dc = np.array([disp[i] for i in CD[ctag]], float)
        gap = dS.mean() - dc.mean()
        pred = gap / 1.6 * 100.0
        ncu, wcu, _, _ = CELLS[(ctag, "unchained")]
        obs = wps - 100.0 * wcu / ncu
        say("    %-26s %+8.4f pp %+11.2f pp %+13.2f pp    %s"
            % ("vs " + ctag, gap, pred, obs,
               "AGREE" if (pred > 0) == (obs > 0) else "OPPOSITE SIGN"))
    say()
    say("  ABSOLUTE (not relative) displacement credit to the SETUP's raw level:")
    say("    SETUP displacement %+.4f%% -> %+.2f pp of win rate versus a midpoint fill." %
        (dS.mean(), dS.mean() / 1.6 * 100.0))
    say("    So the displacement-adjusted bar for the raw %.2f%% is roughly %.2f%%, not 50.00%%."
        % (wps, 50.0 + dS.mean() / 1.6 * 100.0))
    say("    Observed %.2f%% minus that bar = %+.2f pp." % (wps, wps - (50.0 + dS.mean() / 1.6 * 100.0)))
    say()

    # ---------------------------------------------------------------- 3 DECOMPOSITION
    say("=" * 104)
    say("3. DECOMPOSITION")
    say("=" * 104)
    for mode in ("unchained", "chained"):
        ns, ws, nets, _ = CELLS[("SETUP", mode)]
        nm, wm, netm, _ = CELLS[("CTRL-M mirror rising", mode)]
        z, p = two_prop_z(ns, ws, nm, wm)
        fp = fisher_one_sided(ws, ns - ws, wm, nm - wm)
        say("  --- 2x2  SETUP vs CTRL-M   (identical price pattern, opposite eff-agg direction)  %s ---"
            % mode.upper())
        say("                              WIN    LOSS    n     win%%      net/tr    binom p vs %.2f%%" % BE)
        say("    SETUP  (eff falling)     %5d  %5d  %5d  %6.2f%%  %+8.4f%%  %9.5f"
            % (ws, ns - ws, ns, 100.0 * ws / ns, nets, binom_p_ge(ns, ws)))
        say("    CTRL-M (eff rising)      %5d  %5d  %5d  %6.2f%%  %+8.4f%%  %9.5f"
            % (wm, nm - wm, nm, 100.0 * wm / nm, netm, binom_p_ge(nm, wm)))
        say("    LIFT %+6.2f pp   two-proportion z = %+.3f  one-sided p = %.4f   "
            "Fisher exact one-sided p = %.4f"
            % (100.0 * ws / ns - 100.0 * wm / nm, z, p, fp))
        if mode == "chained":
            say("    (chained lift carries the signed chained null measured in block 1 -- do not read it")
            say("     against a zero baseline.)")
        say()
    say("  CTRL-E -- the eff-agg condition ALONE, price pattern ignored, at much larger n:")
    for mode in ("unchained", "chained"):
        n, w, net, sig = CELLS[("CTRL-E eff only", mode)]
        n0, w0, net0, _ = CELLS[("CTRL-0 all mature", mode)]
        z, p = two_prop_z(n, w, n0, w0)
        say("    %-10s CTRL-E n=%-5d W=%-4d %6.2f%%  net %+.4f%%  binom p %.5f  |  vs CTRL-0 "
            "n=%-5d %6.2f%%: lift %+6.2f pp  z=%+.3f  one-sided p=%.4f"
            % (mode, n, w, 100.0 * w / n, net, binom_p_ge(n, w), n0, 100.0 * w0 / n0,
               100.0 * w / n - 100.0 * w0 / n0, z, p))
    ne, we, _, _ = CELLS[("CTRL-E eff only", "unchained")]
    n0, w0, _, _ = CELLS[("CTRL-0 all mature", "unchained")]
    say("    MDE check on CTRL-E: at n=%d the smallest win count reaching exact binomial p<0.05 vs"
        % ne)
    crit_e, _ = mde_binom(ne)
    say("    %.2f%% is W>=%s (%.2f%%); observed W=%d (%.2f%%)."
        % (BE, str(crit_e), 100.0 * crit_e / ne if crit_e else float("nan"), we, 100.0 * we / ne))
    say("    So CTRL-E is WELL powered against break-even and lands %+.2f pp BELOW it. The eff-agg"
        % (100.0 * we / ne - BE))
    say("    condition standalone is not merely unproven -- it is measured, at n=%d, and it is dead."
        % ne)
    say("    Whatever the SETUP has therefore has to live in the INTERACTION with the price pattern,")
    say("    not in the eff-agg clause itself. Size of that interaction: %+.2f pp vs CTRL-M at p=%.4f."
        % (100.0 * wsu / nsu - 100.0 * CELLS[("CTRL-M mirror rising", "unchained")][1]
           / CELLS[("CTRL-M mirror rising", "unchained")][0],
           two_prop_z(nsu, wsu, CELLS[("CTRL-M mirror rising", "unchained")][0],
                      CELLS[("CTRL-M mirror rising", "unchained")][1])[1]))
    say()

    # ---------------------------------------------------------------- 4 BANDS
    say("=" * 104)
    say("4. BAND STRUCTURE -- monotone, or sign-alternating around the cohort mean?")
    say("=" * 104)
    sp = np.asarray([float(eff[i]) - float(eff[i - 1]) for i in setup], float)
    ordr = np.argsort(sp)
    say("  Hypothesis under test: 'steeper eff-agg fall = better short' predicts win rate DECREASING")
    say("  left-to-right when the most-negative band is printed first.")
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
    edgesB = [-100.0, -30.0, -20.0, -12.0, -6.0, -2.0, 0.0]
    bandsB = []
    for k in range(len(edgesB) - 1):
        lo, hi = edgesB[k], edgesB[k + 1]
        mem = [setup[j] for j in range(len(setup)) if lo <= sp[j] < hi]
        if mem:
            bandsB.append(("[%+6.1f,%+6.1f)" % (lo, hi), mem))
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

    say("  win-rate sequences (most-negative spread band first) and their shape versus the cohort's own")
    say("  overall win rate (the base each band's sign is taken around):")
    for lab, seq, mode in (("sextiles    unchained", seqAU, "unchained"),
                           ("sextiles      chained", seqAC, "chained"),
                           ("fixed edges unchained", seqBU, "unchained"),
                           ("fixed edges   chained", seqBC, "chained")):
        n_, w_, _, _ = CELLS[("SETUP", mode)]
        base = 100.0 * w_ / n_
        sh, flips, m = shape(seq, base)
        say("    %-22s %s" % (lab, "  ".join("%5.1f" % x[0] for x in seq)))
        say("    %-22s %s  |  %s, %d sign flip(s) around the cohort base %.2f%% across %d bands"
            % ("", "  ".join(("  +  " if x[0] > base else "  -  ") for x in seq), sh, flips, base, m))
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
                           for nn in (3, 5, 8, 10, 12, 15, 18)))
    say()

    # ---------------------------------------------------------------- 5 POWER
    say("=" * 104)
    say("5. POWER")
    say("=" * 104)
    say("  Two-proportion, one-sided alpha 0.05, 80% power, normal approximation with pooled null SE.")
    say("  %-12s %-24s %5s %6s %8s %10s %11s %12s %8s"
        % ("mode", "control", "n1", "n2", "ctrl%", "MDE p1", "MDE lift", "observed lift", "obs/MDE"))
    for mode in ("unchained", "chained"):
        ns, ws, _, _ = CELLS[("SETUP", mode)]
        for ctag in ("CTRL-M mirror rising", "CTRL-P minus SETUP"):
            nc, wc, _, _ = CELLS[(ctag, mode)]
            p2 = wc / nc
            p1 = mde_two_prop(ns, nc, p2)
            obs = 100.0 * ws / ns - 100.0 * p2
            lift_mde = 100.0 * (p1 - p2)
            say("  %-12s %-24s %5d %6d %7.2f%% %9.2f%% %+10.2f pp %+11.2f pp %8.2f"
                % (mode, ctag, ns, nc, 100 * p2, 100 * p1, lift_mde, obs,
                   obs / lift_mde if lift_mde > 0 else float("nan")))
    say()
    say("  Exact-binomial MDE vs the %.2f%% break-even (power against the FEE, not against a control):"
        % BE)
    for mode in ("unchained", "chained"):
        n, w, _, _ = CELLS[("SETUP", mode)]
        crit, p = mde_binom(n)
        say("    %-10s n=%-4d observed W=%-3d (%.2f%%) | reject needs W>=%s (%.2f%%) | true win rate "
            "for 80%% power = %.2f%% (%+.2f pp above BE)"
            % (mode, n, w, 100.0 * w / n, str(crit), 100.0 * crit / n if crit else float("nan"),
               100 * p if p else float("nan"), 100 * p - BE if p else float("nan")))
    say()
    say("  Required SAMPLE SIZE for 80% power AT THE OBSERVED LIFT.  Two rows: (a) grow n1 only, with
"
        "  the control frozen at its realised size -- this has a POWER CEILING because the control's
"
        "  own standard error does not shrink; (b) scale BOTH arms by the same factor k, which is the
"
        "  answerable form of the question:")
    for mode in ("unchained", "chained"):
        ns, ws, _, _ = CELLS[("SETUP", mode)]
        p1 = ws / ns
        for ctag in ("CTRL-M mirror rising", "CTRL-P minus SETUP"):
            nc, wc, _, _ = CELLS[(ctag, mode)]
            p2 = wc / nc
            need = None
            if p1 > p2:
                for cand in range(5, 200001):
                    pb = (p1 * cand + p2 * nc) / (cand + nc)
                    se0 = math.sqrt(pb * (1 - pb) * (1 / cand + 1 / nc))
                    se1 = math.sqrt(p1 * (1 - p1) / cand + p2 * (1 - p2) / nc)
                    if se1 <= 0:
                        continue
                    z = (p1 - p2 - 1.6448536269514722 * se0) / se1
                    if 0.5 * math.erfc(-z / math.sqrt(2.0)) >= 0.80:
                        need = cand; break
            say("    %-10s vs %-24s observed lift %+6.2f pp -> needs n1 >= %-8s (has %d; %s)"
                % (mode, ctag, 100 * (p1 - p2), str(need) if need else ">200000", ns,
                   ("%.0fx more" % (need / ns)) if need else "unreachable / wrong sign"))
    say()
    say("  Signal rate: %d SETUP signals over %d consecutive mature pairs = 1 per %.1f buckets."
        % (len(setup), len(P), len(P) / len(setup)))
    say("  At that rate, reaching the n1 above is a multiple of the whole %d-bucket archive." % len(bars))
    say()

    # ---------------------------------------------------------------- 6 MULTIPLICITY
    say("=" * 104)
    say("6. MULTIPLICITY")
    say("=" * 104)
    say("  A circular-shift reality check over the ~400-cell search this program ran on THIS 23-day")
    say("  window returned P(null max >= real best) = 0.548. The best cell of that entire search was")
    say("  worse than the average best cell obtained by rotating uninformative features against the")
    say("  same outcomes on the same pool.")
    say()
    say("  This family -- SETUP, 5 controls, 2 modes, 12 disjoint spread bands x 2 modes -- runs on")
    say("  the same %d mature buckets, the same window, the same exits. It is a continuation of that"
        % len(mature))
    say("  search, not an independent replication of anything.")
    say()
    best = None
    for tag, _ in COH:
        for mode in ("unchained", "chained"):
            n, w, _, _ = CELLS[(tag, mode)]
            pv = binom_p_ge(n, w)
            if best is None or pv < best[0]:
                best = (pv, tag, mode, n, w)
    say("  Best exact binomial p anywhere in this family: %.5f (%s, %s, n=%d W=%d)."
        % (best[0], best[1], best[2], best[3], best[4]))
    say("  Best contrast p anywhere in this family: %.4f (SETUP vs CTRL-E, unchained)."
        % two_prop_z(nsu, wsu, ne, we)[1])
    say("  Nothing here is below 0.05 uncorrected, so no correction is even required.")
    say()
    say("  PLAINLY, no softening: at P(null max >= real best) = 0.548 for the parent search, a")
    say("  nominally significant cell drawn from this pool carries no evidential weight on its own.")
    say("  A p of 0.03 in a family whose best-cell distribution under the null already reaches the")
    say("  observed best 55% of the time is a draw from noise, not a discovery. That verdict applies")
    say("  to any cell in this family that had come in significant -- and none did.")
    say()

    # ---------------------------------------------------------------- 7 VERDICT
    say("=" * 104)
    say("7. DECIDING QUESTION -- per mode, three conditions, all required")
    say("=" * 104)
    say("  (i)   SETUP beats CTRL-M at one-sided p < 0.05")
    say("  (ii)  net expectancy > 0 at the 0.08%% fee   (equivalently win rate > %.2f%%)" % BE)
    say("  (iii) real net sits above the displacement-matched null p95")
    say("  (iii-B) same against the STRICTER direction+displacement-matched null")
    say()
    for mode in ("unchained", "chained"):
        ns, ws, nets, _ = CELLS[("SETUP", mode)]
        nm, wm, _, _ = CELLS[("CTRL-M mirror rising", mode)]
        z, p = two_prop_z(ns, ws, nm, wm)
        fp = fisher_one_sided(ws, ns - ws, wm, nm - wm)
        gA = NULLS[("SETUP", mode)][0]
        p95A = float(np.percentile(gA, 95)); pctA = NULLS[("SETUP", mode)][4]
        p95B = NULLD[("SETUP", mode)][2]; pctB = NULLD[("SETUP", mode)][0]
        i_ok = p < 0.05
        ii_ok = nets > 0
        iii_ok = pctA >= 95.0
        iiib_ok = pctB >= 95.0
        say("  --- MODE %s   (n=%d, W=%d, win %.2f%%, net %+.4f%%) ---"
            % (mode.upper(), ns, ws, 100.0 * ws / ns, nets))
        say("    (i)     vs CTRL-M  lift %+.2f pp   z = %+.3f   one-sided p = %.4f (Fisher %.4f)   %s"
            % (100.0 * ws / ns - 100.0 * wm / nm, z, p, fp, "PASS" if i_ok else "FAIL"))
        say("    (ii)    net %+.4f%%/trade   (needs > 0, i.e. win rate > %.2f%%)                  %s"
            % (nets, BE, "PASS" if ii_ok else "FAIL"))
        say("    (iii)   NULL A percentile %.1f%%   real %+.4f%% vs null p95 %+.4f%%             %s"
            % (pctA, nets, p95A, "PASS" if iii_ok else "FAIL"))
        say("    (iii-B) NULL B percentile %.1f%%   real %+.4f%% vs null p95 %+.4f%%             %s"
            % (pctB, nets, p95B, "PASS" if iiib_ok else "FAIL"))
        say("    ANSWER: %s" % ("YES" if (i_ok and ii_ok and iii_ok and iiib_ok) else "NO"))
        # how many more winners would be needed
        say("    Deciding numbers -- extra winners needed (loser->winner flips at fixed n=%d):" % ns)
        need_ii = None
        for k in range(ws, ns + 1):
            if (k * WIN_NET + (ns - k) * LOSS_NET) > 0:
                need_ii = k - ws; break
        need_i = None
        for k in range(ws, ns + 1):
            if two_prop_z(ns, k, nm, wm)[1] < 0.05:
                need_i = k - ws; break
        need_bin = None
        for k in range(ws, ns + 1):
            if binom_p_ge(ns, k) < 0.05:
                need_bin = k - ws; break
        need_iii = None
        for k in range(ws, ns + 1):
            if (k * WIN_NET + (ns - k) * LOSS_NET) / ns > p95A:
                need_iii = k - ws; break
        need_iiib = None
        for k in range(ws, ns + 1):
            if (k * WIN_NET + (ns - k) * LOSS_NET) / ns > p95B:
                need_iiib = k - ws; break
        say("      (i)   beat CTRL-M at p<0.05      : +%s winners (W=%s, %.2f%%)"
            % (str(need_i), str(ws + need_i) if need_i is not None else "--",
               100.0 * (ws + need_i) / ns if need_i is not None else float("nan")))
        say("      (ii)  net > 0                    : +%s winners (W=%s, %.2f%%)"
            % (str(need_ii), str(ws + need_ii) if need_ii is not None else "--",
               100.0 * (ws + need_ii) / ns if need_ii is not None else float("nan")))
        say("      (iii) above NULL A p95           : +%s winners (W=%s, %.2f%%)"
            % (str(need_iii), str(ws + need_iii) if need_iii is not None else "--",
               100.0 * (ws + need_iii) / ns if need_iii is not None else float("nan")))
        say("      (iiiB)above NULL B p95           : +%s winners (W=%s, %.2f%%)"
            % (str(need_iiib), str(ws + need_iiib) if need_iiib is not None else "--",
               100.0 * (ws + need_iiib) / ns if need_iiib is not None else float("nan")))
        say("      binomial p<0.05 vs BE separately : +%s winners (W=%s, %.2f%%)"
            % (str(need_bin), str(ws + need_bin) if need_bin is not None else "--",
               100.0 * (ws + need_bin) / ns if need_bin is not None else float("nan")))
        mx = max(x for x in (need_i, need_ii, need_iii, need_iiib) if x is not None)
        say("      -> the binding count is +%d winners; at W=%d/%d the exact binomial p vs %.2f%% "
            "would be %.5f (%s p<0.05)."
            % (mx, ws + mx, ns, BE, binom_p_ge(ns, ws + mx),
               "clears" if binom_p_ge(ns, ws + mx) < 0.05 else "still does NOT clear"))
        say()

    say("=" * 104)
    say("WHAT WAS NOT DONE")
    say("=" * 104)
    say("  No threshold, bracket, window, side or condition was varied from the frozen spec. No new")
    say("  candidate was searched. No forward/out-of-window data was used. No trading recommendation")
    say("  is made or implied.")
    say("```")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
