"""Does ANY Stats-Box feature separate WINNING Radar Runner trades from LOSING ones? (5m clock candles)

Detect every 5m clock RR breakout (identical logic to study/wall_breakout_backtest), label it win/loss by the 1x
barrier (the 72%-win headline scheme: reach broken-extreme +/- 1L before the opposite-extreme SL; SL-first on a same-
bar tie), compute EVERY Stats-Box feature AT the breakout bar, and test each for winner/loser separation -- AUC + top-
vs-bottom-tercile win-rate -- reported PER YEAR. A real FILTER = same-side AUC and a consistent tercile win lift in
BOTH years. If nothing survives, the edge is take-them-all (alpha is in the setup, not cherry-pickable).
Usage: python study/radarrun_filter_5m_clock.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.statsbox_edge_5m_clock import features, _vertical, _ker_diff, _exhaust
from app import absorption_level_detect as AL, config
from app import absorption as ABS, reward_eff as RW, region_state as RS, pivot_detect as PD

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; H = 200; W = config.ABSORP_VOL_WINDOW; FEE = 0.0004


def detect_events(A, O, C, Hi, Lo):
    """(k, side, rlo, rhi, band) for every RR breakout -- copied from wall_breakout_backtest.study."""
    n = len(A); ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = float(w.get("price") or 0); band = float(w.get("band") or 0)
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi, band); break
        if c1 >= n:
            break
        c0 += 5000
    return ev


def label_1x(side, entry, sl, tgt, ph, pl):
    """-> (win 0/1, net_return) under the 1x barrier. Return uses the ACTUAL entry->TP / entry->SL distances (so a
    win-rate lift that's really just a shorter TP / longer SL shows up as a WORSE return, not an edge)."""
    s = 1 if side == "S" else -1
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return 0, s * (sl - entry) / entry - FEE    # SL first (loss) -- SL wins the same-bar tie (conservative)
        if (hi >= tgt) if s > 0 else (lo <= tgt):
            return 1, s * (tgt - entry) / entry - FEE    # 1x target first (win)
    return None, None                                    # unresolved within H -> drop


def helper_at(A, k):
    out = {}
    try:
        a = ABS.absorption(A, k)[0]; out["st_absorb absorpR"] = a if a is not None else np.nan
    except Exception:
        out["st_absorb absorpR"] = np.nan
    try:
        bsh, ok = RW.share(A, k - 19, k); out["st_reward rew_share"] = (bsh - 50.0) if ok else np.nan
    except Exception:
        out["st_reward rew_share"] = np.nan
    try:
        st = RW.strength(A, k, k)
        out["st_strength str_zdiff"] = (st["buy"]["effort_z"] - st["sell"]["effort_z"]) if st.get("ok") else np.nan
    except Exception:
        out["st_strength str_zdiff"] = np.nan
    try:
        ba, be, _ = RS.absorption_vol(A, k, W); out["st_absorpvol av_net"] = ba - be
        eb, es, _ = RS.effective_aggression(A, k, W); out["st_effagg ea_net"] = eb - es
    except Exception:
        out["st_absorpvol av_net"] = np.nan; out["st_effagg ea_net"] = np.nan
    try:
        out["st_effaggsp ea_spread"] = (2.0 * float(PD.eff_causal_share(A[max(0, k - 149):k + 1])[-1]) - 1.0) * 100.0
    except Exception:
        out["st_effaggsp ea_spread"] = np.nan
    return out


def auc(x, y):
    """AUC of feature x separating y (1=win). Rank-based, NaN-safe."""
    m = np.isfinite(x)
    x = x[m]; y = y[m]
    npos = y.sum(); nneg = len(y) - npos
    if npos < 10 or nneg < 10:
        return np.nan
    from scipy.stats import rankdata
    r = rankdata(x)
    return (r[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def main():
    A = sorted(load_archive("5m", root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: float(b.get("start_time", 0) or 0))
    n = len(A)
    O = np.array([float(b.get("open", 0)) for b in A]); C = np.array([float(b.get("close", 0)) for b in A])
    Hi = np.array([float(b.get("high", 0)) for b in A]); Lo = np.array([float(b.get("low", 0)) for b in A])
    yr = np.array([datetime.fromtimestamp(float(b.get("start_time", 0)), tz=timezone.utc).year for b in A])
    ev = detect_events(A, O, C, Hi, Lo)
    Fdir, _ = features(A)                                 # direct features for all candles

    ks, sides, wins, rets, years = [], [], [], [], []
    feat = {name: [] for name in Fdir}
    hnames = ["st_absorb absorpR", "st_reward rew_share", "st_strength str_zdiff",
              "st_absorpvol av_net", "st_effagg ea_net", "st_effaggsp ea_spread"]
    for name in hnames:
        feat[name] = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi, band = ev[(k, side)]; s = 1 if side == "S" else -1
        entry = C[k]; L = rhi - rlo
        brk = rhi if side == "S" else rlo; sl = rlo if side == "S" else rhi; tgt = brk + s * L
        j0 = k + 1; j1 = min(n, k + 1 + H)
        lab, ret = label_1x(side, entry, sl, tgt, Hi[j0:j1], Lo[j0:j1])
        if lab is None:
            continue
        ks.append(k); sides.append(s); wins.append(lab); rets.append(ret); years.append(int(yr[k]))
        for name, x in Fdir.items():
            v = x[k]
            feat[name].append(v * s if name in ("st_delta  delta%", "st_oi     oiΔ%", "st_openpos net_open%",
                                                 "st_deltaud vertical", "st_movmag mov_signed", "st_mmxskew mmxskew",
                                                 "st_cvd    cvd_net%", "st_ohlc   body") else v)
        for name, v in helper_at(A, k).items():
            feat[name].append(v)
    wins = np.array(wins); rets = np.array(rets); years = np.array(years)
    print("5m clock RR trades (1x barrier): %d resolved  (2025=%d, 2026=%d)" % (
        len(wins), (years == 2025).sum(), (years == 2026).sum()), flush=True)
    for Y in (2025, 2026):
        m = years == Y
        print("  baseline %d: win %.1f%%   avg net-return %+.4f%%  (n=%d)"
              % (Y, 100 * wins[m].mean(), 100 * rets[m].mean(), m.sum()), flush=True)
    print("\n  KEY = avg NET-RETURN per tercile (win-rate is secondary; a big breakout wins more but from a worse R).")
    print("  direction-sensitive features are sign-flipped by trade side (+ = 'more toward the trade').\n")
    print("  %-26s | AUC25/26 | ret%% botT/topT 25 | ret%% botT/topT 26 | win%% top 25/26 | EXPECTANCY FILTER?"
          % "feature @ breakout bar")
    print("  " + "-" * 112)
    rows = []
    for name in feat:
        x = np.array(feat[name], dtype=np.float64)
        a25 = auc(x[years == 2025], wins[years == 2025]); a26 = auc(x[years == 2026], wins[years == 2026])
        if not (np.isfinite(a25) and np.isfinite(a26)):
            continue

        def terc(Y):
            m = (years == Y) & np.isfinite(x)
            xv = x[m]; rv = rets[m]; wv = wins[m]
            if len(xv) < 60:
                return (np.nan,) * 3
            q = np.quantile(xv, [1 / 3, 2 / 3])
            bot = rv[xv <= q[0]]; top = rv[xv >= q[1]]; topw = wv[xv >= q[1]]
            return (100 * bot.mean() if len(bot) else np.nan, 100 * top.mean() if len(top) else np.nan,
                    100 * topw.mean() if len(topw) else np.nan)
        rb25, rt25, tw25 = terc(2025); rb26, rt26, tw26 = terc(2026)
        # EXPECTANCY filter: top tercile's avg net-return beats the bottom tercile, SAME sign both years, and material
        d25 = rt25 - rb25; d26 = rt26 - rb26
        robust = np.isfinite(d25) and np.isfinite(d26) and (np.sign(d25) == np.sign(d26)) \
            and min(abs(d25), abs(d26)) >= 0.02 and rt25 > 0 and rt26 > 0
        rows.append((name, a25, a26, rb25, rt25, rb26, rt26, tw25, tw26, robust))
    rows.sort(key=lambda r: -((r[4] - r[3]) + (r[6] - r[5])) / 2.0)   # sort by top-minus-bottom RETURN spread
    for name, a25, a26, rb25, rt25, rb26, rt26, tw25, tw26, robust in rows:
        print("  %-26s | %.2f/%.2f | %+6.3f /%+6.3f  | %+6.3f /%+6.3f  | %4.1f /%4.1f  | %s"
              % (name, a25, a26, rb25, rt25, rb26, rt26, tw25, tw26, "** FILTER" if robust else ""), flush=True)
    good = [r[0] for r in rows if r[9]]
    print("\n  ROBUST EXPECTANCY filter (top-tercile net-return > bottom, same sign both years, >=0.02%%, top>0): %s"
          % (", ".join(good) if good else "NONE -- no feature improves NET RETURN; win-rate lifts are just R trade-offs"))


if __name__ == "__main__":
    main()
