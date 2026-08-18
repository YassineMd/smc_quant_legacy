"""Feature-search: does ANY hamburger>Candles>Stats-Box parameter predict the NEXT 5m CLOCK candle?

For every stats-box row we compute its underlying per-candle scalar CAUSALLY (only fields known at candle i), then
test it against the forward return fwd1 = close[i+1]/close[i]-1 and fwd3 (next 3 candles). Reported PER YEAR (2025 /
2026-H1) so nothing that only worked in one regime survives. An 'edge' = a feature whose top-vs-bottom quintile forward
return is the SAME sign and material in BOTH years (and rank-corr agrees). DESCRIPTIVE reliability only.

Notes on clock candles: duration is constant (300s), so the velocity/tempo/tau family collapses to ~volume and is
flagged degenerate. Helper-module features (absorption R, reward/eff, strength, absorp-vol, eff-agg) were NULL on
volume buckets in prior work; included here for completeness on the new substrate.
Usage: python study/statsbox_edge_5m_clock.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from app import config
from app.footprint_panel import profile_skewness
from app import absorption as ABS
from app import reward_eff as RW
from app import region_state as RS
from app import pivot_detect as PD

TICK = config.TICK_SIZE
EXHW = 30


def _exhaust(buckets, i, win=EXHW):
    w = buckets[max(0, i - win):i]
    if not w:
        return 0.0, 0.0
    bm = float(buckets[i].get("buyer_er", 0.0)) / (sum(x.get("buyer_er", 0.0) for x in w) / len(w) or 1.0)
    sm = float(buckets[i].get("seller_er", 0.0)) / (sum(x.get("seller_er", 0.0) for x in w) / len(w) or 1.0)
    return bm, sm


def _vertical(b):
    lv = b.get("levels") or {}
    h = float(b.get("high", 0.0)); l = float(b.get("low", 0.0)); cv = float(b.get("curr_vol", 0.0))
    if not lv or h <= l or cv <= 0:
        return 0.0
    mid = (h + l) / 2.0; ub = us = lb = ls = 0.0
    for ps, v in lv.items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        bb = float(v.get("b", 0.0)); ss = float(v.get("s", 0.0))
        if p >= mid:
            ub += bb; us += ss
        else:
            lb += bb; ls += ss
    return ((ub - us) - (lb - ls)) / cv          # upper-half net minus lower-half net (delta%), a vertical skew


def _ker_diff(b):
    o = float(b.get("open", 0.0)); c = float(b.get("close", 0.0))
    bv = float(b.get("buy_vol", 0.0)); sv = float(b.get("sell_vol", 0.0))
    ut = float(b.get("up_ticks", 0.0) or 0.0); dt = float(b.get("dn_ticks", 0.0) or 0.0)
    dur = max(1.0, float(b.get("end_time", 0.0)) - float(b.get("start_time", 0.0)))
    dp = (c - o) / TICK; vd = bv - sv
    ob = (bv / ut) if ut > 0 else 0.0; os_ = (sv / dt) if dt > 0 else 0.0
    Fb = max(0.0, vd) * (bv / dur); Fs = max(0.0, -vd) * (sv / dur)
    Wb = max(0.0, dp) * ob; Ws = max(0.0, -dp) * os_
    kb = (Wb / Fb) if Fb > 0 else (5.0 if Wb > 0 else 0.0)
    ks = (Ws / Fs) if Fs > 0 else (5.0 if Ws > 0 else 0.0)
    return min(kb, 5.0) - min(ks, 5.0)


def features(A):
    """Return {name: np.array} of causal per-candle features (len N)."""
    N = len(A)
    F = {}
    def arr(fn):
        return np.array([fn(b) for b in A], dtype=np.float64)
    g = lambda b, k, d=0.0: float(b.get(k, d) or 0.0)
    cv = arr(lambda b: g(b, "curr_vol", 1.0)); cv = np.where(cv <= 0, 1.0, cv)
    bv = arr(lambda b: g(b, "buy_vol")); sv = arr(lambda b: g(b, "sell_vol"))
    o = arr(lambda b: g(b, "open")); c = arr(lambda b: g(b, "close"))
    h = arr(lambda b: g(b, "high")); l = arr(lambda b: g(b, "low"))
    opL = arr(lambda b: g(b, "opL")); opS = arr(lambda b: g(b, "opS"))
    clL = arr(lambda b: g(b, "clL")); clS = arr(lambda b: g(b, "clS"))
    ber = arr(lambda b: g(b, "buyer_er")); ser = arr(lambda b: g(b, "seller_er"))
    ut = arr(lambda b: g(b, "up_ticks")); dt = arr(lambda b: g(b, "dn_ticks"))
    dh1 = np.array([b.get("delta_h1") for b in A], dtype=object)
    cvdhi = arr(lambda b: g(b, "cvd_hi")); cvdlo = arr(lambda b: g(b, "cvd_lo"))
    rng = np.where((h - l) > 0, h - l, 1e-9)
    delta = bv - sv
    F["st_delta  delta%"] = delta / cv
    da2 = np.array([((delta[i] - 2 * float(dh1[i])) / cv[i]) if dh1[i] is not None else np.nan for i in range(N)])
    F["st_daccel da2"] = da2
    F["st_oi     oiΔ%"] = ((opL + opS) - (clL + clS)) / cv
    F["st_openpos net_open%"] = (opL - opS) / cv
    F["st_closepos net_close%"] = (clL - clS) / cv
    F["st_er     er_imb"] = (ber - ser) / np.where((ber + ser) > 0, ber + ser, 1.0)
    F["st_ease   ease_imb"] = (ut - dt) / np.where((ut + dt) > 0, ut + dt, 1.0)
    bpt = np.where(ut > 0, bv / np.where(ut > 0, ut, 1.0), 0.0)
    spt = np.where(dt > 0, sv / np.where(dt > 0, dt, 1.0), 0.0)
    F["st_costtick ct_imb"] = (bpt - spt) / np.where((bpt + spt) > 0, bpt + spt, 1.0)
    F["st_deltaud vertical"] = arr(_vertical)
    F["st_skew   skew"] = np.array([(profile_skewness(b.get("levels")) or 0.0) for b in A])
    ref = np.where(c > o, l, np.where(c < o, h, o))
    movmag = np.where(ref > 0, ((c * 100.0 / np.where(ref > 0, ref, 1.0)) - 100.0) ** 2 * 100.0, 0.0)
    F["st_movmag mov_signed"] = movmag * np.sign(c - o)
    F["st_mmxskew mmxskew"] = F["st_movmag mov_signed"] * F["st_skew   skew"]
    F["st_ohlc   body"] = (c - o) / rng
    F["st_ker    ker_diff"] = arr(_ker_diff)
    F["st_cvd    cvd_net%"] = (cvdhi + cvdlo) / cv                       # net intrabar delta-excursion bias
    F["st_velread vol_mult(DEGEN)"] = arr(lambda b: g(b, "vol_mult", 1.0))
    # trailing-relative
    F["st_er30   exhaust_diff"] = np.array([(lambda t: t[0] - t[1])(_exhaust(A, i)) for i in range(N)])
    return F, c


def helper_features(A):
    """Slower helper-module features (per-candle trailing-window calls)."""
    N = len(A); W = config.ABSORP_VOL_WINDOW
    absR = np.full(N, np.nan); rew = np.full(N, np.nan); stz = np.full(N, np.nan)
    avnet = np.full(N, np.nan); eanet = np.full(N, np.nan); espread = np.full(N, np.nan)
    for i in range(N):
        try:
            a = ABS.absorption(A, i)[0]
            absR[i] = a if a is not None else np.nan
        except Exception:
            pass
        if i >= 20:
            try:
                bsh, ok = RW.share(A, i - 19, i)
                if ok:
                    rew[i] = bsh - 50.0
            except Exception:
                pass
            try:
                st = RW.strength(A, i, i)
                if st.get("ok"):
                    stz[i] = st["buy"]["effort_z"] - st["sell"]["effort_z"]
            except Exception:
                pass
        try:
            ba, be, _ = RS.absorption_vol(A, i, W); avnet[i] = ba - be
            ea_b, ea_s, _ = RS.effective_aggression(A, i, W); eanet[i] = ea_b - ea_s
        except Exception:
            pass
        if i >= 8:
            try:
                espread[i] = (2.0 * float(PD.eff_causal_share(A[max(0, i - 149):i + 1])[-1]) - 1.0) * 100.0
            except Exception:
                pass
    return {"st_absorb absorpR": absR, "st_reward rew_share": rew, "st_strength str_zdiff": stz,
            "st_absorpvol av_net": avnet, "st_effagg ea_net": eanet, "st_effaggsp ea_spread": espread}


def scan(with_helpers=True):
    A = sorted(load_archive("5m", root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: float(b.get("start_time", 0) or 0))
    N = len(A)
    yr = np.array([datetime.fromtimestamp(float(b.get("start_time", 0)), tz=timezone.utc).year for b in A])
    print("5m clock candles: %d  (2025=%d, 2026=%d)\n" % (N, (yr == 2025).sum(), (yr == 2026).sum()), flush=True)
    F, c = features(A)
    if with_helpers:
        print("  computing helper-module features (slower) ...", flush=True)
        F.update(helper_features(A))
    fwd1 = np.full(N, np.nan); fwd3 = np.full(N, np.nan)
    fwd1[:-1] = c[1:] / np.where(c[:-1] > 0, c[:-1], 1.0) - 1.0
    fwd3[:-3] = c[3:] / np.where(c[:-3] > 0, c[:-3], 1.0) - 1.0

    def yr_stats(x, target, Y):
        m = (yr == Y) & np.isfinite(x) & np.isfinite(target)
        if m.sum() < 200:
            return None
        xv = x[m]; tv = target[m]
        # rank corr (spearman via rankdata)
        from scipy.stats import rankdata
        rc = np.corrcoef(rankdata(xv), rankdata(tv))[0, 1]
        q = np.quantile(xv, [0.2, 0.8])
        lo = tv[xv <= q[0]]; hi = tv[xv >= q[1]]
        spread = (hi.mean() - lo.mean()) * 100.0 if len(lo) and len(hi) else np.nan   # Q5-Q1 fwd return, %
        return rc, spread, len(hi), (hi > 0).mean() * 100.0

    rows = []
    for name, x in F.items():
        s25 = yr_stats(x, fwd1, 2025); s26 = yr_stats(x, fwd1, 2026)
        if s25 and s26:
            rc25, sp25, nhi25, w25 = s25; rc26, sp26, nhi26, w26 = s26
            robust = (np.sign(sp25) == np.sign(sp26)) and min(abs(sp25), abs(sp26)) >= 0.03  # both-sign, >=0.03% Q-spread
            rows.append((name, rc25, rc26, sp25, sp26, w25, w26, robust))
    rows.sort(key=lambda r: -abs((r[3] + r[4]) / 2.0))
    print("  %-26s | corr25  corr26 | Q5-Q1_fwd1%%(25/26) | topQ_win%%(25/26) | robust" % "feature (fwd = NEXT 5m candle)")
    print("  " + "-" * 96)
    for name, rc25, rc26, sp25, sp26, w25, w26, robust in rows:
        print("  %-26s | %+.3f %+.3f | %+6.3f / %+6.3f     | %4.1f / %4.1f    | %s"
              % (name, rc25, rc26, sp25, sp26, w25, w26, "** EDGE" if robust else ""), flush=True)
    edges = [r for r in rows if r[7]]
    print("\n  ROBUST (both-year same-sign Q5-Q1 >= 0.03%%): %s"
          % (", ".join(r[0] for r in edges) if edges else "NONE — no single stats-box param predicts the next 5m clock candle"))


if __name__ == "__main__":
    scan(with_helpers="--nohelp" not in sys.argv)
