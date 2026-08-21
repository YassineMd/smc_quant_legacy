"""S6-SESSION — session-boundary liquidity study ("London hypothesis"), footprint-only, honest naming. Tests whether the
London-open window sweeps the Asian range + reverses (Judas swing), vs circular-shift + volatility nulls. NO narrative
attribution. clock 1m, UTC. Sessions: ASIA 00-07, LONDON 07-16, NY 13-21, OVERLAP 13-16.
PRE-REGISTERED PRIMARY CELL (declared before results): eps=0.05*AR, N=30m reclaim, sweep window 07:00-10:00.
Stages: H3/H4 descriptive (framing) ; H1 sweep-probability vs nulls ; H2 Judas reversal vs unconditional. python study/s6_session_study.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from math import comb
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
ROOT, TF = "study/clock_archive", "1m"
EPS_FRAC, N_RECLAIM, SWEEP_END = 0.05, 30, 600      # PRIMARY: eps 0.05*AR, 30-min reclaim, window ends min 600 (10:00)
RANGE_MIN, SWEEP_MIN = 420, 420                      # Asia = min 0..419 (00-07); sweep window starts min 420 (07:00)


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); V = np.zeros(n)
    AM = np.zeros(n, dtype=np.int64)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); V[i] = _f(b.get("curr_vol", 0) or 0)
        AM[i] = int(round(_f(b.get("start_time", 0)) / 60.0))         # absolute minute
    return O, C, Hi, Lo, V, AM, n


def sweep_prob(Lo, Hi, absmin, k, eps_frac):
    """P(sweep of the 7h-range extreme in the next 3h window), for clock offset k hours. Circular-shift null when k!=0
    ... k=0 = the real ASIA(00-07)->LONDON(07-10) config."""
    shifted = absmin - k * 60
    dayid = shifted // 1440; mind = shifted % 1440
    bnd = [0] + list(np.flatnonzero(np.diff(dayid)) + 1) + [len(dayid)]
    days = 0; hits = 0
    for a, b in zip(bnd[:-1], bnd[1:]):
        md = mind[a:b]
        rm = md < RANGE_MIN; sm = (md >= SWEEP_MIN) & (md < SWEEP_END)
        if not rm.any() or not sm.any():
            continue
        lo_r = Lo[a:b][rm]; hi_r = Hi[a:b][rm]
        AL = lo_r.min(); AH = hi_r.max(); AR = AH - AL
        if AR <= 0:
            continue
        eps = eps_frac * AR
        days += 1
        if (Lo[a:b][sm] < AL - eps).any() or (Hi[a:b][sm] > AH + eps).any():
            hits += 1
    return hits / days if days else 0.0, days


def build_days(O, C, Hi, Lo, V, absmin):
    """real calendar-day records (k=0): Asia AH/AL/AM, first low/high sweep in 07-10, reclaim, London close, session rets."""
    dayid = absmin // 1440; mind = absmin % 1440
    bnd = [0] + list(np.flatnonzero(np.diff(dayid)) + 1) + [len(dayid)]
    out = []
    for a, b in zip(bnd[:-1], bnd[1:]):
        md = mind[a:b]
        m2i = {int(md[j]): a + j for j in range(len(md))}          # minute-of-day -> global index
        rm = md < RANGE_MIN
        if not rm.any():
            continue
        AL = Lo[a:b][rm].min(); AH = Hi[a:b][rm].max(); AR = AH - AL; AMd = (AH + AL) / 2.0
        if AR <= 0:
            continue
        eps = EPS_FRAC * AR
        lo_sw = hi_sw = None
        for m in range(SWEEP_MIN, SWEEP_END):
            gi = m2i.get(m)
            if gi is None:
                continue
            if lo_sw is None and Lo[gi] < AL - eps:
                lo_sw = (m, gi)
            if hi_sw is None and Hi[gi] > AH + eps:
                hi_sw = (m, gi)
        def reclaim(sw, side):                                    # side=+1 low-sweep(reclaim up), -1 high-sweep
            if sw is None:
                return None
            m0 = sw[0]
            for m in range(m0, min(m0 + N_RECLAIM + 1, 960)):
                gi = m2i.get(m)
                if gi is None:
                    continue
                if (C[gi] > AL) if side > 0 else (C[gi] < AH):
                    return m
            return None
        lc = m2i.get(959)                                         # London close = 15:59 bar
        lon_close = C[lc] if lc is not None else None
        def px(m):
            gi = m2i.get(m); return C[gi] if gi is not None else None
        o0700 = O[m2i[420]] if 420 in m2i else None
        c1259 = px(779); o1600 = O[m2i[960]] if 960 in m2i else None; c2059 = px(1259)
        # session realized range (sum |1m ret|) + volume by hour bucket
        ret = np.zeros(b - a); ret[1:] = np.abs(C[a + 1:b] / np.where(C[a:b - 1] == 0, np.nan, C[a:b - 1]) - 1.0)
        hh = (md // 60)
        def bucket(mask):
            return float(np.nansum(ret[mask])), float(V[a:b][mask].sum())
        buckets = {"asia": bucket(hh < 7), "lon_exov": bucket((hh >= 7) & (hh < 13)),
                   "overlap": bucket((hh >= 13) & (hh < 16)), "ny_exov": bucket((hh >= 16) & (hh < 21)),
                   "rest": bucket(hh >= 21)}
        out.append(dict(AL=AL, AH=AH, AM=AMd, AR=AR, lo_sw=lo_sw, hi_sw=hi_sw,
                        lo_reclaim=reclaim(lo_sw, +1), hi_reclaim=reclaim(hi_sw, -1),
                        lon_close=lon_close, retL=(c1259 / o0700 - 1 if o0700 and c1259 else None),
                        retN=(c2059 / o1600 - 1 if o1600 and c2059 else None), buckets=buckets,
                        yr=datetime.fromtimestamp(absmin[a] * 60, tz=timezone.utc).year))
    return out


def binom_p(k, nn, p0):
    if nn == 0:
        return 1.0
    from math import comb as _cb
    # two-sided vs p0
    lo = sum(_cb(nn, j) * p0 ** j * (1 - p0) ** (nn - j) for j in range(nn + 1) if _cb(nn, j) * p0 ** j * (1 - p0) ** (nn - j) <= _cb(nn, k) * p0 ** k * (1 - p0) ** (nn - k) + 1e-18)
    return min(1.0, lo)


def main():
    O, C, Hi, Lo, V, absmin, n = load()
    t0 = datetime.fromtimestamp(absmin[0] * 60, tz=timezone.utc); t1 = datetime.fromtimestamp(absmin[-1] * 60, tz=timezone.utc)
    D = build_days(O, C, Hi, Lo, V, absmin)
    print("=== S6-SESSION (London hypothesis) === 1m clock, %d bars, %s -> %s, %d days\n" % (n, t0.date(), t1.date(), len(D)), flush=True)
    print("PRE-REGISTERED PRIMARY: eps=0.05*AR, N=30m reclaim, sweep window 07:00-10:00. UTC sessions.\n", flush=True)

    # ---- H3 session share (descriptive) ----
    print("--- H3 session share of daily realized range | volume (mean %) — overlap broken out ---", flush=True)
    keys = ["asia", "lon_exov", "overlap", "ny_exov", "rest"]
    rr = {kk: [] for kk in keys}; vv = {kk: [] for kk in keys}
    for d in D:
        tr = sum(d["buckets"][kk][0] for kk in keys); tv = sum(d["buckets"][kk][1] for kk in keys)
        if tr <= 0 or tv <= 0:
            continue
        for kk in keys:
            rr[kk].append(d["buckets"][kk][0] / tr); vv[kk].append(d["buckets"][kk][1] / tv)
    for kk in keys:
        print("    %-9s range %4.1f%%  volume %4.1f%%" % (kk, 100 * np.mean(rr[kk]), 100 * np.mean(vv[kk])), flush=True)

    # ---- H4 London ex-overlap -> NY ex-overlap sign agreement ----
    pr = [(d["retL"], d["retN"]) for d in D if d["retL"] is not None and d["retN"] is not None]
    agree = np.mean([1.0 if (a > 0) == (b > 0) else 0.0 for a, b in pr])
    print("\n--- H4 London(07-13) -> NY(16-21) sign agreement = %.1f%% (null 50%%, n=%d) ---" % (100 * agree, len(pr)), flush=True)

    # ---- H1 sweep probability vs nulls (PRIMARY) ----
    print("\n=== H1 — London-open sweep of the Asian range (primary cell) ===", flush=True)
    real, nd = sweep_prob(Lo, Hi, absmin, 0, EPS_FRAC)
    nulls = [sweep_prob(Lo, Hi, absmin, k, EPS_FRAC)[0] for k in range(1, 24)]
    nmean = np.mean(nulls); n95 = np.percentile(nulls, 95); pctile = 100.0 * np.mean([real > x for x in nulls])
    print("    REAL P(sweep either side, 07-10) = %.1f%%  (n=%d days)" % (100 * real, nd), flush=True)
    print("    circular-shift NULL (k=1..23): mean %.1f%%  95th %.1f%%  max %.1f%%  -> real beats %.0f%% of offsets"
          % (100 * nmean, 100 * n95, 100 * max(nulls), pctile), flush=True)
    print("    H1 verdict: %s (real %s 95th-pct null)" % ("PASS" if real > n95 else "FAIL", ">" if real > n95 else "<="), flush=True)
    lo_only = np.mean([d["lo_sw"] is not None for d in D]); hi_only = np.mean([d["hi_sw"] is not None for d in D])
    print("    per side: P(low-sweep)=%.1f%%  P(high-sweep)=%.1f%%  P(both)=%.1f%%"
          % (100 * lo_only, 100 * hi_only, 100 * np.mean([d["lo_sw"] is not None and d["hi_sw"] is not None for d in D])), flush=True)

    # ---- H1 sensitivity grid ----
    print("\n--- H1 sensitivity: real P(sweep) vs null-mean, by eps x window-end ---", flush=True)
    for ef in (0.03, 0.05, 0.10):
        row = []
        for we in (540, 600, 660):                                # 09:00 / 10:00 / 11:00
            global SWEEP_END
            SWEEP_END = we
            r, _ = sweep_prob(Lo, Hi, absmin, 0, ef)
            nm = np.mean([sweep_prob(Lo, Hi, absmin, k, ef)[0] for k in range(1, 24)])
            row.append("%4.1f%% vs %4.1f%%" % (100 * r, 100 * nm))
        print("    eps=%.2f  " % ef + "  |  ".join("win%02d:00 %s" % (we // 60, row[j]) for j, we in enumerate((9, 10, 11))), flush=True)
    SWEEP_END = 600

    # ---- H2 Judas reversal ----
    print("\n=== H2 — Judas swing: sweep -> reclaim -> reverse through the midpoint ===", flush=True)
    lon_gt_am = [d["lon_close"] > d["AM"] for d in D if d["lon_close"] is not None]
    uncond_up = np.mean(lon_gt_am)
    print("    unconditional P(London close > Asian mid) = %.1f%% (n=%d)" % (100 * uncond_up, len(lon_gt_am)), flush=True)
    # low-sweep -> reclaim -> expect London close > AM (reversal UP)
    lrec = [d for d in D if d["lo_sw"] is not None and d["lo_reclaim"] is not None and d["lon_close"] is not None]
    lgo = [d for d in D if d["lo_sw"] is not None and d["lo_reclaim"] is None and d["lon_close"] is not None]
    p_lrec = np.mean([d["lon_close"] > d["AM"] for d in lrec]) if lrec else 0
    p_lgo = np.mean([d["lon_close"] > d["AM"] for d in lgo]) if lgo else 0
    print("    LOW-sweep + reclaim (n=%d): P(London>AM)=%.1f%%  vs uncond %.1f%%  -> lift %+.1fpp"
          % (len(lrec), 100 * p_lrec, 100 * uncond_up, 100 * (p_lrec - uncond_up)), flush=True)
    print("    LOW-sweep + NO reclaim / sweep-and-go (n=%d): P(London>AM)=%.1f%% (continuation = LOW)" % (len(lgo), 100 * p_lgo), flush=True)
    # high-sweep mirror -> expect London close < AM (reversal DOWN)
    hrec = [d for d in D if d["hi_sw"] is not None and d["hi_reclaim"] is not None and d["lon_close"] is not None]
    hgo = [d for d in D if d["hi_sw"] is not None and d["hi_reclaim"] is None and d["lon_close"] is not None]
    p_hrec = np.mean([d["lon_close"] < d["AM"] for d in hrec]) if hrec else 0
    p_hgo = np.mean([d["lon_close"] < d["AM"] for d in hgo]) if hgo else 0
    print("    HIGH-sweep + reclaim (n=%d): P(London<AM)=%.1f%%  vs uncond %.1f%%  -> lift %+.1fpp"
          % (len(hrec), 100 * p_hrec, 100 * (1 - uncond_up), 100 * (p_hrec - (1 - uncond_up))), flush=True)
    print("    HIGH-sweep + NO reclaim (n=%d): P(London<AM)=%.1f%% (continuation = HIGH)" % (len(hgo), 100 * p_hgo), flush=True)


if __name__ == "__main__":
    main()
