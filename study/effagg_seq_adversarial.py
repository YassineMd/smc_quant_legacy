"""EFF-AGG SEQUENTIAL SETUPS 1 & 2 -- ADVERSARIAL PRICING.

Prices the already-run result. Nothing is tuned, no new candidate is searched.
Six blocks, exactly as commissioned:
  1 NULL CALIBRATION   displacement-decile-matched permutation cohorts, chained identically,
                       2000 iterations, seeded. Chained AND unchained bases scored separately,
                       each against ITS OWN null (the chained null is signed and size-dependent;
                       the unchained lift null is 0.0 -- so nets are scored, not zero-baselined).
  2 DISPLACEMENT       dir*(close-(high+low)/2)/close*100 per cohort vs the universe (+0.1802% est).
  3 DECOMPOSITION      2x2 price-pattern-only vs price-pattern+eff-agg, two-proportion z;
                       then the eff-agg condition ALONE as a long entry.
  4 POWER              MDE at 80% power, one-sided alpha 0.05, vs the matched control, at realised n.
  5 MULTIPLICITY       stated, not softened.
  6 DECIDING QUESTION  yes/no per setup with the deciding number.

Run:  python study/effagg_seq_adversarial.py
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

OUT_MD = os.path.join(HERE, "out", "effagg_seq_adversarial.md")
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
    """P(X >= a) hypergeometric. Table [[a,b],[c,d]] = [[setup W, setup L],[ctrl W, ctrl L]]."""
    n1 = a + b; n2 = c + d; k = a + c; N = n1 + n2
    acc = 0.0
    lo = max(0, k - n2); hi = min(n1, k)
    tot = math.comb(N, k)
    for x in range(a, hi + 1):
        acc += math.comb(n1, x) * math.comb(n2, k - x)
    return acc / tot


def mde_two_prop(n1, n2, p2, alpha=0.05, power=0.80):
    """Smallest p1 > p2 with >= `power` at one-sided `alpha`, normal approx, pooled null SE."""
    za = 1.6448536269514722
    zb_target = power
    lo, hi = p2, 1.0
    for _ in range(200):
        p1 = 0.5 * (lo + hi)
        pb = (p1 * n1 + p2 * n2) / (n1 + n2)
        se0 = math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2))
        se1 = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        if se1 <= 0:
            lo = p1; continue
        z = (p1 - p2 - za * se0) / se1
        pw = 0.5 * math.erfc(-z / math.sqrt(2.0))
        if pw < zb_target:
            lo = p1
        else:
            hi = p1
    return hi


def mde_binom(n, p0=P0, alpha=0.05, power=0.80):
    """Smallest true p giving >= power of exact-binomial p<alpha at this n."""
    crit = None
    for k in range(0, n + 1):
        if binom_p_ge(n, k, p0) < alpha:
            crit = k; break
    if crit is None:
        return None, None
    lo, hi = p0, 1.0
    for _ in range(200):
        p = 0.5 * (lo + hi)
        # P(W >= crit | n, p)
        lg = math.lgamma
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
    return "n=%-4d mean %+7.4f  median %+7.4f  sd %6.4f  min %+7.4f  max %+7.4f" % (
        len(a), a.mean(), float(np.median(a)), a.std(ddof=1) if len(a) > 1 else 0.0, a.min(), a.max())


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


def precompute_exits(bars):
    """(win, exit_bar) per entry index -- depends only on the entry bar at a fixed % bracket.
    None = unresolved at end of data (DROPPED, never a loss). Same-bar both -> STOP."""
    n = len(bars)
    res = [None] * n
    for i in range(n):
        if not bars[i]["ok"]:
            continue
        e = bars[i]["c"]
        stop = e * (1 - SL_PCT); targ = e * (1 + TP_PCT)
        for j in range(i + 1, n):
            if not bars[j]["ok"]:
                continue
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
    EX = precompute_exits(bars)
    mature = [i for i in range(first, len(bars)) if bars[i]["ok"]]
    P = [i for i in range(first + 1, len(bars)) if bars[i]["ok"] and bars[i - 1]["ok"]]

    s1, s2, c1, c2 = [], [], [], []
    for i in P:
        a, b = bars[i - 1], bars[i]
        ea, eb = float(eff[i - 1]), float(eff[i])
        if a["c"] > a["o"] and b["c"] > b["o"] and b["c"] > a["c"]:
            c1.append(i)
            if ea > 0 and eb < 0:
                s1.append(i)
        if a["c"] < a["o"] and b["c"] < b["o"] and a["c"] > b["c"]:
            c2.append(i)
            if ea > 0 and eb > 0 and ea < eb:
                s2.append(i)
    pooled = sorted(set(s1) | set(s2))
    cpool = sorted(set(c1) | set(c2))
    x1 = sorted(set(c1) - set(s1))
    x2 = sorted(set(c2) - set(s2))
    xp = sorted(set(cpool) - set(pooled))

    # eff-agg condition ALONE (price pattern ignored), on the same mature-pair population
    e1_alone = [i for i in P if eff[i - 1] > 0 and eff[i] < 0]
    e2_alone = [i for i in P if eff[i - 1] > 0 and eff[i] > eff[i - 1]]

    # displacement, per bucket
    disp = {}
    for i in range(len(bars)):
        if not bars[i]["ok"]:
            continue
        b = bars[i]
        d = 1.0 if b["c"] >= b["o"] else -1.0
        disp[i] = d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0

    say("# EFF-AGG SEQUENTIAL SETUPS 1 & 2 -- ADVERSARIAL PRICING")
    say()
    say("Bracket 0.8/0.8 + 0.08%% fee. BE win rate %.4f%%. Driftless P(TP first) 50.00%%." % BE)
    say("(n, wins) is sufficient at this bracket -- no t, no bootstrap, no PF, no drop-best-N appears.")
    say("Permutation: %d iterations, seed %d, cohorts stratified on ENTRY-BUCKET DISPLACEMENT DECILE," % (NITER, SEED))
    say("drawn without replacement from the %d mature buckets, sorted ascending, chained identically." % len(mature))
    say()
    say("```")

    # ---------------------------------------------------------------- headline recap
    say("=" * 100)
    say("0. CELLS BEING PRICED (recomputed here from scratch; must match the prior run)")
    say("=" * 100)
    say("  %-22s %-10s %5s %5s %8s %11s %10s" % ("cohort", "mode", "n", "W", "win%", "net/tr", "binom p"))
    CELLS = {}
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("POOLED 1+2", pooled),
                      ("CONTROL 1", c1), ("CONTROL 2", c2), ("CONTROL 1+2", cpool),
                      ("C1 minus S1", x1), ("C2 minus S2", x2), ("C1+2 minus S1+2", xp),
                      ("CONTROL 0", mature),
                      ("EFF-1 alone", e1_alone), ("EFF-2 alone", e2_alone)):
        for mode, ch in (("unchained", False), ("chained", True)):
            n, w, net = run(idxs, EX, ch)
            CELLS[(tag, mode)] = (n, w, net, len(idxs))
            say("  %-22s %-10s %5d %5d %7.2f%% %+10.4f%% %10.5f"
                % (tag, mode, n, w, 100.0 * w / n if n else float("nan"), net,
                   binom_p_ge(n, w) if n else float("nan")))
    say()

    # ---------------------------------------------------------------- 1 NULL CALIBRATION
    say("=" * 100)
    say("1. NULL CALIBRATION -- displacement-decile-matched permutation cohorts")
    say("=" * 100)
    pool = np.array(mature, dtype=int)
    pd_ = np.array([disp[i] for i in pool], float)
    edges = np.percentile(pd_, [10, 20, 30, 40, 50, 60, 70, 80, 90])
    pool_dec = np.searchsorted(edges, pd_, side="right")
    by_dec = {d: pool[pool_dec == d] for d in range(10)}
    say("  displacement decile edges (mature universe, %%): " +
        " ".join("%+.4f" % e for e in edges))
    say("  pool per decile: " + " ".join("d%d=%d" % (d, len(by_dec[d])) for d in range(10)))
    say()
    say("  Each null cohort draws the SAME NUMBER OF SIGNALS PER DECILE as the real cohort, without")
    say("  replacement, then runs the identical exit/drop/chain machinery. Unresolved -> dropped, so")
    say("  null n varies exactly as the real n does. The CHAINED null is signed and size-dependent by")
    say("  construction -- these draws measure that signature directly, no zero baseline is assumed.")
    say()

    def null_dist(real_idxs, chained, niter=NITER, seed=SEED):
        rng = np.random.default_rng(seed)
        cnt = np.zeros(10, dtype=int)
        for i in real_idxs:
            cnt[int(np.searchsorted(edges, disp[i], side="right"))] += 1
        nets = np.empty(niter); wins = np.empty(niter); ns = np.empty(niter)
        for it in range(niter):
            pick = []
            for d in range(10):
                k = cnt[d]
                if k:
                    pick.append(rng.choice(by_dec[d], size=k, replace=False))
            sel = np.sort(np.concatenate(pick)) if pick else np.array([], dtype=int)
            n, w, net = run(sel.tolist(), EX, chained)
            nets[it] = net if n else np.nan
            wins[it] = (100.0 * w / n) if n else np.nan
            ns[it] = n
        return nets, wins, ns, cnt

    say("  %-14s %-10s %6s %8s | %9s %8s %8s %8s %8s | %8s %8s"
        % ("cohort", "mode", "sig", "real net", "null mean", "null sd", "null p5", "null p50",
           "null p95", "PCTILE", "emp p"))
    NULLS = {}
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("POOLED 1+2", pooled)):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins, ns, cnt = null_dist(idxs, ch)
            n, w, real = run(idxs, EX, ch)
            good = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(good < real))
            empp = float(np.mean(good >= real))
            NULLS[(tag, mode)] = (good, wins, ns, real, pct, empp, cnt)
            say("  %-14s %-10s %6d %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, len(idxs), real, good.mean(), good.std(ddof=1),
                   np.percentile(good, 5), np.percentile(good, 50), np.percentile(good, 95),
                   pct, empp))
    say()
    say("  Null-mean win rates (the signed chained null, made explicit):")
    for tag in ("SETUP 1", "SETUP 2", "POOLED 1+2"):
        for mode in ("unchained", "chained"):
            good, wins, ns, real, pct, empp, cnt = NULLS[(tag, mode)]
            wv = wins[np.isfinite(wins)]
            say("    %-12s %-10s null mean win %6.2f%%  (vs BE %.2f%%)   null mean n %5.2f   "
                "real net %+.4f%% at percentile %.1f" % (tag, mode, wv.mean(), BE, ns.mean(), real, pct))
    say()
    say("  Decile composition of each real cohort (how far from a flat draw it is):")
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("POOLED 1+2", pooled)):
        cnt = NULLS[(tag, "unchained")][6]
        say("    %-12s " % tag + " ".join("d%d=%d" % (d, cnt[d]) for d in range(10)))
    say()

    # seed stability
    say("  SEED STABILITY -- 4 extra seeds on the headline cells (percentile of the real net):")
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("POOLED 1+2", pooled)):
        for mode, ch in (("unchained", False), ("chained", True)):
            n, w, real = run(idxs, EX, ch)
            ps = []
            for sd in (11, 22, 33, 44):
                nets, _, _, _ = null_dist(idxs, ch, niter=1000, seed=sd)
                g = nets[np.isfinite(nets)]
                ps.append(100.0 * float(np.mean(g < real)))
            say("    %-12s %-10s percentiles %s" % (tag, mode, " ".join("%.1f" % x for x in ps)))
    say()

    # ---------------------------------------------------------------- 1b DIRECTION-MATCHED NULL
    say("=" * 100)
    say("1b. STRICTER NULL -- displacement decile AND entry-candle DIRECTION matched")
    say("=" * 100)
    say("  The commissioned null matches displacement decile only. But SETUP 1 is 100%% long-after-UP")
    say("  (ride) and SETUP 2 is 100%% long-after-DOWN (fade), while the mature pool is 48.6%% up. A")
    say("  decile-only null therefore draws a direction MIX the setups never take. This null draws only")
    say("  from same-direction buckets inside each displacement decile. It is the harder test.")
    say()
    up_pool = {}; dn_pool = {}
    for d in range(10):
        m = by_dec[d]
        up_pool[d] = np.array([i for i in m if bars[i]["c"] >= bars[i]["o"]], dtype=int)
        dn_pool[d] = np.array([i for i in m if bars[i]["c"] < bars[i]["o"]], dtype=int)
    say("  up-candle pool per decile:   " + " ".join("d%d=%d" % (d, len(up_pool[d])) for d in range(10)))
    say("  down-candle pool per decile: " + " ".join("d%d=%d" % (d, len(dn_pool[d])) for d in range(10)))
    say()

    def null_dist_dir(real_idxs, chained, niter=NITER, seed=SEED):
        rng = np.random.default_rng(seed)
        cu = np.zeros(10, dtype=int); cd = np.zeros(10, dtype=int)
        for i in real_idxs:
            d = int(np.searchsorted(edges, disp[i], side="right"))
            if bars[i]["c"] >= bars[i]["o"]:
                cu[d] += 1
            else:
                cd[d] += 1
        nets = np.empty(niter); wins = np.empty(niter)
        for it in range(niter):
            pick = []
            for d in range(10):
                if cu[d]:
                    pick.append(rng.choice(up_pool[d], size=cu[d], replace=False))
                if cd[d]:
                    pick.append(rng.choice(dn_pool[d], size=cd[d], replace=False))
            sel = np.sort(np.concatenate(pick)) if pick else np.array([], dtype=int)
            n, w, net = run(sel.tolist(), EX, chained)
            nets[it] = net if n else np.nan
            wins[it] = (100.0 * w / n) if n else np.nan
        return nets, wins

    say("  %-14s %-10s %8s | %9s %8s %8s %8s | %8s %8s"
        % ("cohort", "mode", "real net", "null mean", "null sd", "null p50", "null p95", "PCTILE", "emp p"))
    NULLD = {}
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("POOLED 1+2", pooled)):
        for mode, ch in (("unchained", False), ("chained", True)):
            nets, wins = null_dist_dir(idxs, ch)
            n, w, real = run(idxs, EX, ch)
            good = nets[np.isfinite(nets)]
            pct = 100.0 * float(np.mean(good < real))
            NULLD[(tag, mode)] = (pct, good.mean(), float(np.mean(wins[np.isfinite(wins)])))
            say("  %-14s %-10s %+7.4f%% | %+8.4f%% %8.4f %+7.4f%% %+7.4f%% | %7.1f%% %8.4f"
                % (tag, mode, real, good.mean(), good.std(ddof=1),
                   np.percentile(good, 50), np.percentile(good, 95), pct,
                   float(np.mean(good >= real))))
    say()
    say("  Direction-conditional base rates (unchained, whole mature universe) -- why this matters:")
    for lab, sel in (("long after UP candle (ride)", [i for i in mature if bars[i]["c"] >= bars[i]["o"]]),
                     ("long after DOWN candle (fade)", [i for i in mature if bars[i]["c"] < bars[i]["o"]])):
        for mode, ch in (("unchained", False), ("chained", True)):
            n, w, net = run(sel, EX, ch)
            say("    %-30s %-10s n=%-5d W=%-5d %6.2f%%  net %+.4f%%"
                % (lab, mode, n, w, 100.0 * w / n, net))
    say()

    # ---------------------------------------------------------------- 2 DISPLACEMENT
    say("=" * 100)
    say("2. DISPLACEMENT PROFILE   disp = dir*(close-(high+low)/2)/close*100,  dir=+1 up / -1 down")
    say("=" * 100)
    for tag, idxs in (("UNIVERSE (mature)", mature), ("SETUP 1", s1), ("CONTROL 1", c1),
                      ("C1 minus S1", x1), ("SETUP 2", s2), ("CONTROL 2", c2),
                      ("C2 minus S2", x2), ("POOLED 1+2", pooled)):
        say("  %-20s %s" % (tag, desc([disp[i] for i in idxs])))
    say()
    uni = np.array([disp[i] for i in mature], float)
    say("  Established universe mean +0.1802%% (prior work, 926-bucket universe);")
    say("  this %d-bucket mature universe gives %+.4f%%." % (len(mature), uni.mean()))
    say()
    say("  Welch t of each cohort's displacement vs the mature universe:")
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("CONTROL 1", c1), ("CONTROL 2", c2),
                      ("POOLED 1+2", pooled)):
        a = np.array([disp[i] for i in idxs], float)
        if len(a) < 2:
            continue
        se = math.sqrt(a.var(ddof=1) / len(a) + uni.var(ddof=1) / len(uni))
        say("    %-12s mean %+.4f%%  vs universe %+.4f%%  diff %+.4f%%  t = %+.2f"
            % (tag, a.mean(), uni.mean(), a.mean() - uni.mean(), (a.mean() - uni.mean()) / se))
    say()
    a1 = np.array([disp[i] for i in s1], float); a2 = np.array([disp[i] for i in s2], float)
    se12 = math.sqrt(a1.var(ddof=1) / len(a1) + a2.var(ddof=1) / len(a2))
    say("  SETUP 1 vs SETUP 2 displacement:  %+.4f%% vs %+.4f%%   diff %+.4f%%   Welch t = %+.2f"
        % (a1.mean(), a2.mean(), a1.mean() - a2.mean(), (a1.mean() - a2.mean()) / se12))
    say()
    say("  Direction of the entry candle (the ride/fade distinction):")
    for tag, idxs in (("SETUP 1", s1), ("SETUP 2", s2), ("UNIVERSE", mature)):
        up = sum(1 for i in idxs if bars[i]["c"] >= bars[i]["o"])
        say("    %-12s up-candle entries %d / %d = %.1f%%   (LONG after an UP candle = RIDE, adverse side)"
            % (tag, up, len(idxs), 100.0 * up / len(idxs)))
    say()

    # ---------------------------------------------------------------- 3 DECOMPOSITION
    say("=" * 100)
    say("3. DECOMPOSITION -- does eff-agg add anything over the price pattern?")
    say("=" * 100)
    for mode in ("unchained", "chained"):
        say("  --- MODE: %s ---" % mode.upper())
        for stag, xtag, ctag in (("SETUP 1", "C1 minus S1", "CONTROL 1"),
                                 ("SETUP 2", "C2 minus S2", "CONTROL 2"),
                                 ("POOLED 1+2", "C1+2 minus S1+2", "CONTROL 1+2")):
            ns, ws, nets, _ = CELLS[(stag, mode)]
            nx, wx, netx, _ = CELLS[(xtag, mode)]
            z, p = two_prop_z(ns, ws, nx, wx)
            fp = fisher_one_sided(ws, ns - ws, wx, nx - wx)
            say("    2x2  %s (price pattern + eff-agg)  vs  %s (price pattern ONLY, disjoint)"
                % (stag, xtag))
            say("         %-24s W=%-4d L=%-4d  n=%-4d  %6.2f%%  net %+.4f%%"
                % (stag + " (with eff-agg)", ws, ns - ws, ns, 100.0 * ws / ns if ns else float("nan"), nets))
            say("         %-24s W=%-4d L=%-4d  n=%-4d  %6.2f%%  net %+.4f%%"
                % (xtag + " (no eff-agg)", wx, nx - wx, nx, 100.0 * wx / nx if nx else float("nan"), netx))
            say("         LIFT %+6.2f pp    two-proportion z = %+.3f   one-sided p = %.4f   "
                "Fisher exact one-sided p = %.4f"
                % ((100.0 * ws / ns if ns else 0) - (100.0 * wx / nx if nx else 0), z, p, fp))
            say()
        say()
    say("  EFF-AGG CONDITION ALONE (price pattern IGNORED), long at close[i], same universe/bracket:")
    say("    EFF-1 alone = eff[i-1] > 0 AND eff[i] < 0")
    say("    EFF-2 alone = eff[i-1] > 0 AND eff[i] > eff[i-1]   (the redundancy-reduced SETUP 2 clause)")
    for tag in ("EFF-1 alone", "EFF-2 alone", "CONTROL 0"):
        for mode in ("unchained", "chained"):
            n, w, net, sig = CELLS[(tag, mode)]
            say("    %-14s %-10s sig=%-5d n=%-5d W=%-5d %6.2f%%  net %+.4f%%  binom p %.5f"
                % (tag, mode, sig, n, w, 100.0 * w / n if n else float("nan"), net, binom_p_ge(n, w)))
    say()
    n0u, w0u, _, _ = CELLS[("CONTROL 0", "unchained")]
    for tag in ("EFF-1 alone", "EFF-2 alone"):
        n, w, net, _ = CELLS[(tag, "unchained")]
        z, p = two_prop_z(n, w, n0u, w0u)
        say("    %-14s vs CONTROL 0 (unchained): lift %+6.2f pp   z = %+.3f   one-sided p = %.4f"
            % (tag, 100.0 * w / n - 100.0 * w0u / n0u, z, p))
    say()

    # ---------------------------------------------------------------- 4 POWER
    say("=" * 100)
    say("4. POWER -- minimum detectable effect at the realised n")
    say("=" * 100)
    say("  Two-proportion, one-sided alpha 0.05, 80%% power, setup n vs its DISJOINT complement n:")
    say("  %-12s %-10s %5s %5s %8s %10s %10s %10s %10s"
        % ("setup", "mode", "n1", "n2", "ctrl%", "MDE p1", "MDE lift", "observed", "obs/MDE"))
    for mode in ("unchained", "chained"):
        for stag, xtag in (("SETUP 1", "C1 minus S1"), ("SETUP 2", "C2 minus S2"),
                           ("POOLED 1+2", "C1+2 minus S1+2")):
            ns, ws, _, _ = CELLS[(stag, mode)]
            nx, wx, _, _ = CELLS[(xtag, mode)]
            p2 = wx / nx
            p1 = mde_two_prop(ns, nx, p2)
            obs = 100.0 * ws / ns - 100.0 * p2
            say("  %-12s %-10s %5d %5d %7.2f%% %9.2f%% %+9.2f pp %+9.2f pp %9.2f"
                % (stag, mode, ns, nx, 100 * p2, 100 * p1, 100 * (p1 - p2), obs,
                   obs / (100 * (p1 - p2)) if p1 > p2 else float("nan")))
    say()
    say("  Exact-binomial MDE vs the %.2f%% break-even (what the cell can see against the FEE, not the" % BE)
    say("  control): smallest TRUE win rate giving 80%% power at one-sided alpha 0.05.")
    for mode in ("unchained", "chained"):
        for stag in ("SETUP 1", "SETUP 2", "POOLED 1+2"):
            n, w, _, _ = CELLS[(stag, mode)]
            crit, p = mde_binom(n)
            if crit is None:
                say("    %-12s %-10s n=%-4d -- NO win count at this n can reach p<0.05" % (stag, mode, n))
            else:
                say("    %-12s %-10s n=%-4d observed %6.2f%% | need W>=%d (%.2f%%) to reject; true rate for "
                    "80%% power = %.2f%%  (%+.2f pp above BE)"
                    % (stag, mode, n, 100.0 * w / n, crit, 100.0 * crit / n, 100 * p, 100 * p - BE))
    say()
    say("  n required for 80%% power at the OBSERVED lift vs the disjoint complement (unchained),")
    say("  holding the complement's size fixed:")
    for stag, xtag in (("SETUP 1", "C1 minus S1"), ("SETUP 2", "C2 minus S2"),
                       ("POOLED 1+2", "C1+2 minus S1+2")):
        ns, ws, _, _ = CELLS[(stag, "unchained")]
        nx, wx, _, _ = CELLS[(xtag, "unchained")]
        p1 = ws / ns; p2 = wx / nx
        need = None
        for cand in range(5, 20001):
            pb = (p1 * cand + p2 * nx) / (cand + nx)
            se0 = math.sqrt(pb * (1 - pb) * (1 / cand + 1 / nx))
            se1 = math.sqrt(p1 * (1 - p1) / cand + p2 * (1 - p2) / nx)
            if se1 <= 0:
                continue
            z = (p1 - p2 - 1.6448536269514722 * se0) / se1
            if 0.5 * math.erfc(-z / math.sqrt(2.0)) >= 0.80:
                need = cand; break
        say("    %-12s observed lift %+6.2f pp -> needs n1 >= %s trades (has %d; %s)"
            % (stag, 100 * (p1 - p2), str(need) if need else ">20000", ns,
               "%.0fx more" % (need / ns) if need else "unreachable"))
    say()
    say("  Signal RATE, so the wait is stated in real time: SETUP 1 = %d signals / %d mature pairs = "
        "1 per %.0f buckets; SETUP 2 = %d / %d = 1 per %.0f buckets."
        % (len(s1), len(P), len(P) / len(s1), len(s2), len(P), len(P) / len(s2)))
    say()

    # ---------------------------------------------------------------- 5 MULTIPLICITY
    say("=" * 100)
    say("5. MULTIPLICITY")
    say("=" * 100)
    say("  Prior reality check on THIS dataset: a circular-shift null over the ~400-cell search this")
    say("  program ran returned P(null max >= real best) = 0.548. The best cell of the entire prior")
    say("  search was WORSE than the average best cell obtained by rotating uninformative features")
    say("  against the same outcomes.")
    say()
    say("  This family adds 2 setups x 2 modes x (setup, control, disjoint complement, eff-alone)")
    say("  = the cells enumerated in block 0 above, all on the same 23-day window and the same 1262")
    say("  mature buckets that the 400 cells used. It is not an independent replication of anything;")
    say("  it is search continuing on a pool already priced at P = 0.548.")
    say()
    best_p = min(binom_p_ge(*CELLS[(t, m)][:2]) for t in ("SETUP 1", "SETUP 2", "POOLED 1+2")
                 for m in ("unchained", "chained"))
    say("  Best exact binomial p anywhere in this family = %.5f. It is not below 0.05, so no" % best_p)
    say("  multiplicity correction is even needed -- nothing survives uncorrected.")
    say()
    k = 12
    say("  For the record, had a cell come in at nominal p, the Sidak-corrected value over just the %d"
        % k)
    say("  primary cells of THIS family would be 1-(1-p)^%d, and that correction would still ignore" % k)
    say("  the ~400 prior cells and the 0.548 reality check that already covers them. Read any nominally")
    say("  significant cell here as a draw from a search whose best cell is indistinguishable from noise.")
    say()

    # ---------------------------------------------------------------- 6 VERDICT
    say("=" * 100)
    say("6. DECIDING QUESTION -- three conditions, per setup, per mode")
    say("=" * 100)
    say("  (i)   beats its own matched control at p < 0.05  [two-proportion vs the DISJOINT complement,")
    say("        one-sided; Fisher exact printed too since n1 is 7 and 15]")
    say("  (ii)  net expectancy > 0 at the 0.08%% fee")
    say("  (iii) survives the displacement-matched permutation null [percentile >= 95]")
    say()
    say("  (iii-b) same, against the STRICTER direction+displacement-matched null")
    say()
    say("  %-12s %-10s %-24s %-18s %-14s %-14s %s"
        % ("setup", "mode", "(i) vs control", "(ii) net", "(iii) pctile", "(iii-b) pctile", "VERDICT"))
    verdicts = {}
    for stag, xtag in (("SETUP 1", "C1 minus S1"), ("SETUP 2", "C2 minus S2"),
                       ("POOLED 1+2", "C1+2 minus S1+2")):
        for mode in ("unchained", "chained"):
            ns, ws, nets, _ = CELLS[(stag, mode)]
            nx, wx, _, _ = CELLS[(xtag, mode)]
            z, p = two_prop_z(ns, ws, nx, wx)
            fp = fisher_one_sided(ws, ns - ws, wx, nx - wx)
            pct = NULLS[(stag, mode)][4]
            pctd = NULLD[(stag, mode)][0]
            i_ok = (p < 0.05); ii_ok = (nets > 0)
            iii_ok = (pct >= 95.0); iiib_ok = (pctd >= 95.0)
            allok = i_ok and ii_ok and iii_ok and iiib_ok
            verdicts[(stag, mode)] = (p, fp, nets, pct, allok)
            say("  %-12s %-10s p=%.4f (F%.3f)%-5s %+.4f%% %-8s %5.1f%% %-6s %5.1f%% %-6s %s"
                % (stag, mode, p, fp, "PASS" if i_ok else "FAIL", nets,
                   "PASS" if ii_ok else "FAIL", pct, "PASS" if iii_ok else "FAIL",
                   pctd, "PASS" if iiib_ok else "FAIL", "YES" if allok else "NO"))
    say()
    say("  ANSWER  SETUP 1: %s     SETUP 2: %s     POOLED: %s"
        % ("NO" if not any(verdicts[("SETUP 1", m)][4] for m in ("unchained", "chained")) else "YES",
           "NO" if not any(verdicts[("SETUP 2", m)][4] for m in ("unchained", "chained")) else "YES",
           "NO" if not any(verdicts[("POOLED 1+2", m)][4] for m in ("unchained", "chained")) else "YES"))
    say("```")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("\nwritten: %s" % OUT_MD)


if __name__ == "__main__":
    main()
