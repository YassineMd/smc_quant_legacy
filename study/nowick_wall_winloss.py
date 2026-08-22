"""NO-WICK WALL (RR 0.5) — what separates WINNERS from LOSERS? Every hamburger>Candles>Stats-Box parameter, computed
CAUSALLY at the signal bar (entry = that bar's CLOSE, so all its stats are known -> no look-ahead). Directional stats are
SIGNED by trade side (long=bullish no-lower-wick, short=bearish no-upper-wick) so "in-favour flow" is always positive;
magnitude stats (range, volume, absorption) left unsigned.

The REAL bar is expectancy, not win%: RR 0.5 wins ~65% but loses money. A feature RESCUES it only if filtering to its
favourable tercile makes NET expectancy positive in BOTH 2025 (IS) and 2026 (OOS). Per feature we report AUC(feat ranks
winner over loser) per year (credible only if same side of 0.5 in both years) + disjoint-tercile win%/meanR/mean-net% per
year. Pooled over mid/high tfs (clock+bucket 15m/30m/1h) where the ~65% win rate lives; scale-dependent range z-scored
within substrate. python study/nowick_wall_winloss.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.statsbox_edge_5m_clock import _vertical, _ker_diff, _exhaust
from app.footprint_panel import profile_skewness
from app import config, absorption as ABS, reward_eff as RW, region_state as RS, pivot_detect as PD

FEE, SLIP, WICK_TOL, HOLD, TPR = 0.0004, 0.0003, 0.001, 48, 0.5
SUBS = [("study/clock_archive", tf) for tf in ("15m", "30m", "1h")] + \
       [("study/recon_archive", tf) for tf in ("15m", "30m", "1h")]
W = config.ABSORP_VOL_WINDOW

# directional stats -> multiply by side so "confirms the trade" is always positive; the rest are magnitude (unsigned)
DIR = {"st_delta  delta%", "st_daccel da2", "st_oi     oiΔ%", "st_openpos net_open%", "st_closepos net_close%",
       "st_er     er_imb", "st_ease   ease_imb", "st_costtick ct_imb", "st_deltaud vertical", "st_skew   skew",
       "st_movmag mov_signed", "st_mmxskew mmxskew", "st_ohlc   body", "st_ker    ker_diff", "st_cvd    cvd_net%",
       "st_er30   exhaust_diff", "st_reward rew_share", "st_strength str_zdiff", "st_absorpvol av_net",
       "st_effagg ea_net", "st_effaggsp ea_spread"}
MAG = ["st_absorb absorpR", "st_velread vol_mult", "rng_pct(z)", "vol_rel"]
FKEYS = sorted(DIR) + MAG


def _g(b, k, d=0.0):
    return float(b.get(k, d) or 0.0)


def feat_at(A, i):
    """All stats-box params for bar i (causal, single-bar so cheap even on big archives)."""
    b = A[i]; cv = _g(b, "curr_vol", 1.0); cv = cv if cv > 0 else 1.0
    bv = _g(b, "buy_vol"); sv = _g(b, "sell_vol")
    o = _g(b, "open", _g(b, "open_price")); c = _g(b, "close", _g(b, "close_price"))
    h = _g(b, "high"); l = _g(b, "low"); rng = (h - l) if (h - l) > 0 else 1e-9
    opL = _g(b, "opL"); opS = _g(b, "opS"); clL = _g(b, "clL"); clS = _g(b, "clS")
    ber = _g(b, "buyer_er"); ser = _g(b, "seller_er"); ut = _g(b, "up_ticks"); dt = _g(b, "dn_ticks")
    cvdhi = _g(b, "cvd_hi"); cvdlo = _g(b, "cvd_lo"); delta = bv - sv
    d = {}
    d["st_delta  delta%"] = delta / cv
    dh1 = b.get("delta_h1"); d["st_daccel da2"] = ((delta - 2 * float(dh1)) / cv) if dh1 is not None else np.nan
    d["st_oi     oiΔ%"] = ((opL + opS) - (clL + clS)) / cv
    d["st_openpos net_open%"] = (opL - opS) / cv
    d["st_closepos net_close%"] = (clL - clS) / cv
    d["st_er     er_imb"] = (ber - ser) / ((ber + ser) if (ber + ser) > 0 else 1.0)
    d["st_ease   ease_imb"] = (ut - dt) / ((ut + dt) if (ut + dt) > 0 else 1.0)
    bpt = (bv / ut) if ut > 0 else 0.0; spt = (sv / dt) if dt > 0 else 0.0
    d["st_costtick ct_imb"] = (bpt - spt) / ((bpt + spt) if (bpt + spt) > 0 else 1.0)
    d["st_deltaud vertical"] = _vertical(b)
    d["st_skew   skew"] = profile_skewness(b.get("levels")) or 0.0
    ref = l if c > o else (h if c < o else o)
    mm = (((c * 100.0 / (ref if ref > 0 else 1.0)) - 100.0) ** 2 * 100.0) if ref > 0 else 0.0
    d["st_movmag mov_signed"] = mm * np.sign(c - o)
    d["st_mmxskew mmxskew"] = d["st_movmag mov_signed"] * d["st_skew   skew"]
    d["st_ohlc   body"] = (c - o) / rng
    d["st_ker    ker_diff"] = _ker_diff(b)
    d["st_cvd    cvd_net%"] = (cvdhi + cvdlo) / cv
    d["st_velread vol_mult"] = _g(b, "vol_mult", 1.0)
    d["st_er30   exhaust_diff"] = (lambda t: t[0] - t[1])(_exhaust(A, i))
    d["rng_pct(z)"] = rng / (c if c > 0 else 1.0) * 100.0
    med = np.median([_g(A[j], "curr_vol", 1.0) for j in range(max(0, i - 20), i)]) if i > 0 else cv
    d["vol_rel"] = cv / (med if med > 0 else cv)
    # --- slower helper-module stats (single-bar, causal) ---
    absR = rew = stz = av = ea = esp = np.nan
    try:
        a = ABS.absorption(A, i)[0]; absR = a if a is not None else np.nan
    except Exception:
        pass
    if i >= 20:
        try:
            bsh, ok = RW.share(A, i - 19, i)
            if ok:
                rew = bsh - 50.0
        except Exception:
            pass
        try:
            st = RW.strength(A, i, i)
            if st.get("ok"):
                stz = st["buy"]["effort_z"] - st["sell"]["effort_z"]
        except Exception:
            pass
    try:
        ba, be, _ = RS.absorption_vol(A, i, W); av = ba - be
        eab, eas, _ = RS.effective_aggression(A, i, W); ea = eab - eas
    except Exception:
        pass
    if i >= 8:
        try:
            esp = (2.0 * float(PD.eff_causal_share(A[max(0, i - 149):i + 1])[-1]) - 1.0) * 100.0
        except Exception:
            pass
    d["st_absorb absorpR"] = absR; d["st_reward rew_share"] = rew; d["st_strength str_zdiff"] = stz
    d["st_absorpvol av_net"] = av; d["st_effagg ea_net"] = ea; d["st_effaggsp ea_spread"] = esp
    return d


def trades(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    rows = []; i = 1
    while i < n - 1:
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            i += 1; continue
        side = 0
        if C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng:
            side = 1
        elif C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng:
            side = -1
        if side == 0:
            i += 1; continue
        entry = C[i]; sl = entry - side * rng; tp = entry + side * TPR * rng; sld = rng / entry
        net = None; rj = i
        for j in range(i + 1, min(i + 1 + HOLD, n)):
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            rj = j
            if adverse:
                net = side * (sl - entry) / entry - FEE - 2 * SLIP; break
            if favor:
                net = side * (tp - entry) / entry - FEE - SLIP; break
        if net is None:
            net = side * (C[rj] - entry) / entry - FEE - 2 * SLIP
        d = feat_at(A, i)
        for k in list(d):
            if k in DIR and d[k] == d[k]:
                d[k] = d[k] * side
        d["_win"] = 1 if net > 0 else 0; d["_net"] = net * 100.0; d["_R"] = net / sld
        d["_y"] = datetime.fromtimestamp(ST[i], tz=timezone.utc).year
        d["_sub"] = "%s/%s" % ("clk" if "clock" in root else "bkt", tf)
        rows.append(d); i = rj + 1
    return rows


def auc(x, y):
    m = ~np.isnan(x); x = x[m]; y = y[m]
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 < 5 or n0 < 5:
        return float("nan"), n1 + n0
    order = np.argsort(x, kind="mergesort"); ranks = np.empty(len(x)); ranks[order] = np.arange(1, len(x) + 1)
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0), n1 + n0


def main():
    print("NO-WICK WALL RR0.5 — winner/loser separation by stats-box param | signed by side | entry=CLOSE (causal)\n", flush=True)
    rows = []
    for root, tf in SUBS:
        r = trades(root, tf)
        w = np.mean([x["_win"] for x in r]) * 100 if r else 0
        print("  loaded %-8s n=%-5d win%.1f%% netexp%+.3f%%" % ("%s/%s" % ("clk" if "clock" in root else "bkt", tf),
              len(r), w, np.mean([x["_net"] for x in r]) if r else 0), flush=True)
        rows += r
    # z-score the scale-dependent range within each substrate before pooling
    for sub in set(r["_sub"] for r in rows):
        v = np.array([r["rng_pct(z)"] for r in rows if r["_sub"] == sub]); mu, sd = v.mean(), (v.std() or 1.0)
        for r in rows:
            if r["_sub"] == sub:
                r["rng_pct(z)"] = (r["rng_pct(z)"] - mu) / sd

    print("\n==== POOLED baseline (clock+bucket 15m/30m/1h) ====", flush=True)
    for Y in (2025, 2026):
        yr = [r for r in rows if r["_y"] == Y]
        print("  %d:  n=%-5d win=%.1f%%  net-exp=%+.3f%%  meanR=%+.3f"
              % (Y, len(yr), 100 * np.mean([r["_win"] for r in yr]), np.mean([r["_net"] for r in yr]), np.mean([r["_R"] for r in yr])), flush=True)

    print("\n  %-22s %8s %8s   %8s  %s" % ("feature (signed by side)", "AUC25", "AUC26", "avail", "consistent?"), flush=True)
    print("  " + "-" * 78, flush=True)
    keep = []
    for f in FKEYS:
        a25, n25 = auc(np.array([r[f] for r in rows if r["_y"] == 2025], float), np.array([r["_win"] for r in rows if r["_y"] == 2025]))
        a26, n26 = auc(np.array([r[f] for r in rows if r["_y"] == 2026], float), np.array([r["_win"] for r in rows if r["_y"] == 2026]))
        rd = ""
        if a25 == a25 and a26 == a26 and ((a25 > 0.53 and a26 > 0.53) or (a25 < 0.47 and a26 < 0.47)):
            rd = "<-- CONSISTENT"; keep.append(f)
        print("  %-22s %8.3f %8.3f   %8d  %s" % (f.strip(), a25, a26, n25 + n26, rd), flush=True)

    print("\n==== disjoint-tercile win%% / meanR / net-exp%%, PER YEAR (a RESCUER = one tercile net-exp>0 BOTH years) ====", flush=True)
    for f in (keep if keep else FKEYS):
        xs = np.array([r[f] for r in rows], float); mask = ~np.isnan(xs)
        if mask.sum() < 100:
            continue
        q = np.quantile(xs[mask], [1 / 3, 2 / 3])
        print("\n  %s  (terciles q=%.3f / %.3f):" % (f.strip(), q[0], q[1]), flush=True)
        for Y in (2025, 2026):
            yr = [r for r in rows if r["_y"] == Y and r[f] == r[f]]
            parts = []
            for lab, sel in (("LO", lambda v: v < q[0]), ("MID", lambda v: q[0] <= v < q[1]), ("HI", lambda v: v >= q[1])):
                g = [r for r in yr if sel(r[f])]
                if g:
                    parts.append("%s %.0f%%/R%+.2f/exp%+.3f%%(n%d)" % (lab, 100 * np.mean([r["_win"] for r in g]),
                                 np.mean([r["_R"] for r in g]), np.mean([r["_net"] for r in g]), len(g)))
            print("     %d:  %s" % (Y, "   ".join(parts)), flush=True)


if __name__ == "__main__":
    main()
