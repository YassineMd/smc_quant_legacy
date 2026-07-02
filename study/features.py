"""T1 feature families for the barrier study. Every value is either a production quantity (reused from the
repo via full_snapshot + region_state/bucket_state/vpin_adaptive/quant_engine — bit-identical, no
re-implementation) or a GENERIC statistic (z / percentile / day-rank / streak / slope) of such a quantity.

Design honesty (surfaced in the report): feature_registry.json is a descriptive feature CATALOG, not an
executable formula spec. So a field is filled ONLY when it maps unambiguously to (a) a production scalar or
(b) a single named generic transform of one. Compound / bespoke / sub-quantity-specific derivation texts,
structure-dependent (order-block) fields, and the depth.db/trade-tape `6h` fields are left NULL with a
categorized reason — never guessed. Windows are NULL-masked until they fill; nothing is zero-filled.
"""
from __future__ import annotations
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from app import region_state as R, bucket_state as BS, vpin_adaptive as V, quant_engine as QE, config  # noqa

W30 = 30          # trailing-30 (repo's shared baseline window)
W240 = 240        # trailing-240 (adaptive-VPIN / POC window)
KC_N = 20         # KC EMA/ATR window (frozen in registry: EMA-20 / 2.0xATR-20)
KC_K = 2.0
POC_N = 240       # rolling-POC window (frozen)
CTX_N = 15        # C.* pre-entry context = last 15 buckets (+ entry = 16)

NULL = None


# ── generic statistics (masked until the window fills) ──────────────────────
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def zscore_trailing(series, i, w=W30):
    if i < w:
        return NULL
    win = series[i - w:i]
    if any(v is None for v in win) or series[i] is None:
        return NULL
    m = _mean(win); sd = _std(win)
    if sd is None or sd == 0:
        return 0.0
    return (series[i] - m) / sd


def pctile_trailing(series, i, w=W240):
    if i < w or series[i] is None:
        return NULL
    win = [v for v in series[i - w:i] if v is not None]
    if not win:
        return NULL
    return sum(1 for v in win if v <= series[i]) / len(win)


def streak_vs_baseline(series, i, w=W30):
    """Signed run-length: consecutive buckets (ending at i) on the same side of the trailing-30 mean."""
    if i < w or series[i] is None:
        return NULL
    run = 0; sign = None
    j = i
    while j >= w:
        win = series[j - w:j]
        if any(v is None for v in win) or series[j] is None:
            break
        m = _mean(win)
        s = 1 if series[j] >= m else -1
        if sign is None:
            sign = s
        if s != sign:
            break
        run += 1
        j -= 1
    return sign * run if sign is not None else 0


def slope_lastN(series, i, n=5):
    if i < n - 1:
        return NULL
    ys = series[i - n + 1:i + 1]
    if any(v is None for v in ys):
        return NULL
    xs = list(range(n))
    mx = sum(xs) / n; my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / den if den else 0.0


def signed_log(x):
    return math.copysign(math.log1p(abs(x)), x) if x is not None else NULL


def day_rank(series, times, i):
    """Rank of series[i] within its own UTC calendar day so far (0..1)."""
    if series[i] is None:
        return NULL
    day = int(times[i] // 86400)
    same = [series[j] for j in range(0, i + 1)
            if int(times[j] // 86400) == day and series[j] is not None]
    if len(same) < 2:
        return NULL
    return sum(1 for v in same if v <= series[i]) / len(same)


# ── repo-measure series (computed once, bit-identical with production) ───────
def repo_series(snaps, bks):
    n = len(snaps)
    bmult = [NULL] * n; smult = [NULL] * n; oimult = [NULL] * n
    state = [NULL] * n; conf = [NULL] * n
    for i in range(n):
        bm, sm, oi = R.exhaustion_mults(snaps, i)
        bmult[i], smult[i], oimult[i] = bm, sm, oi
        st, cf = BS.classify_bucket(snaps, i, bm, sm)
        state[i], conf[i] = st, cf
    bull, bear, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    effb = [NULL] * n; effr = [NULL] * n
    for i in range(n):
        eb, er, _s = R.effective_aggression(snaps, i, config.ABSORP_VOL_WINDOW)
        effb[i], effr[i] = eb, er
    vpin = V.rolling_vpin(snaps)
    effort = [QE._effort_ticks(snaps[i].get("levels") or {}) for i in range(n)]
    return dict(bmult=bmult, smult=smult, oimult=oimult, state=state, conf=conf,
                bull=bull, bear=bear, sval=sval, effb=effb, effr=effr, vpin=vpin, effort=effort)


# ── base-field canonical PRODUCTION scalar (E01..E72) ───────────────────────
def base_series(snaps, bks, rs):
    """Return {base_code: (series|None, reason_if_none)} — the field's canonical production scalar."""
    n = len(snaps)
    g = lambda k: [float(snaps[i].get(k, 0.0)) for i in range(n)]
    close = g("close"); openp = g("open"); high = g("high"); low = g("low")
    cv = g("curr_vol"); bv = g("buy_vol"); sv = g("sell_vol")
    opL = g("opL"); opS = g("opS"); clL = g("clL"); clS = g("clS"); churn = g("churn")
    ber = g("buyer_er"); ser = g("seller_er"); poc = g("poc_price"); vm = g("vol_mult")
    st = g("start_time"); et = g("end_time"); liqs = g("liq_short"); liql = g("liq_long")
    rng = [high[i] - low[i] for i in range(n)]
    body = [close[i] - openp[i] for i in range(n)]
    dur = [et[i] - st[i] for i in range(n)]
    delta = [bv[i] - sv[i] for i in range(n)]
    doi = [(opL[i] + opS[i]) - (clL[i] + clS[i]) for i in range(n)]

    def frac(num, den):
        return [(num[i] / den[i]) if den[i] else NULL for i in range(n)]

    def poc_pos(i):
        return ((poc[i] - low[i]) / rng[i]) if rng[i] else NULL

    def nlevels(i):
        return float(len(snaps[i].get("levels") or {}))

    def poc_share(i):
        lv = snaps[i].get("levels") or {}
        if not lv or cv[i] <= 0:
            return NULL
        pv = max((v.get("b", 0.0) + v.get("s", 0.0)) for v in lv.values())
        return pv / cv[i]

    def sz_total_count(i):
        c = sum(bks[i].sz_cb) + sum(bks[i].sz_cs) if hasattr(bks[i], "sz_cb") else 0.0
        return c if c > 0 else NULL          # age-mask: sz_* only exist since 2026-06-30
    def sz_total_vol(i):
        c = sum(bks[i].sz_vb) + sum(bks[i].sz_vs) if hasattr(bks[i], "sz_vb") else 0.0
        return c if c > 0 else NULL

    def vel(i):
        return cv[i] / dur[i] if dur[i] > 0 else NULL
    def avg_vel(i):
        return (vel(i) / vm[i]) if (vm[i] and vel(i) is not None) else NULL   # engine baseline, backed out

    def atr_comp(i):                          # E56 range / trailing-30 mean range
        if i < W30:
            return NULL
        m = _mean(rng[i - W30:i])
        return (rng[i] / m) if m else NULL
    def sweep_depth(i):                       # E57 sweep beyond prior-10 extremes, over range
        if i < 10 or rng[i] <= 0:
            return NULL
        ph = max(high[i - 10:i]); pl = min(low[i - 10:i])
        up = max(0.0, high[i] - ph); dn = max(0.0, pl - low[i])
        return max(up, dn) / rng[i]
    def vpin_pct(i):
        return pctile_trailing(rs["vpin"], i, W240)

    B = {}
    B["E01"] = (st, None)
    B["E02"] = (et, None)
    B["E03"] = (dur, None)
    B["E04"] = (close, None)                  # canonical OHLC scalar = close
    B["E05"] = (rng, None)
    B["E06"] = (body, None)
    B["E07"] = ([abs(body[i]) / rng[i] if rng[i] else NULL for i in range(n)], None)
    B["E08"] = ([rng[i] - abs(body[i]) if rng[i] else NULL for i in range(n)], None)  # total wick
    B["E09"] = (poc, None)
    B["E10"] = ([poc_pos(i) for i in range(n)], None)
    B["E11"] = (cv, None)
    B["E12"] = (bv, None)
    B["E13"] = (sv, None)
    B["E14"] = (delta, None)
    B["E15"] = ([abs(delta[i]) / cv[i] if cv[i] else NULL for i in range(n)], None)
    B["E16"] = (opL, None); B["E17"] = (opS, None); B["E18"] = (clL, None); B["E19"] = (clS, None)
    B["E20"] = (churn, None)
    B["E21"] = (doi, None)
    B["E22"] = (frac([opL[i] + opS[i] for i in range(n)], cv), None)   # opens fraction (canonical)
    B["E23"] = (ber, None); B["E24"] = (ser, None)
    B["E25"] = (rs["effort"], None)
    B["E26"] = (None, "descriptive display anomaly (side E/R vs trailing-30 border) — deferred")
    B["E27"] = (None, "descriptive neon-border flag — deferred")
    B["E28"] = (vm, None)
    B["E29"] = ([vel(i) for i in range(n)], None)
    B["E30"] = (None, "abnormal-velocity flag family — deferred (threshold semantics)")
    B["E31"] = ([avg_vel(i) for i in range(n)], None)
    B["E32"] = ([bks[i].target_vol for i in range(n)], None)
    B["E33"] = ([max(rs["bmult"][i], rs["smult"][i]) for i in range(n)], None)   # dominant exhaustion mult
    B["E34"] = (None, "gated true-exhaustion geomean — deferred (exact gate not exposed as one fn)")
    B["E35"] = (None, "selection-scoped exhaustion lines — belongs to scope panels (T2/T3)")
    B["E36"] = (rs["sval"], None)
    B["E37"] = ([rs["bull"][i] + rs["bear"][i] for i in range(n)], None)         # total absorption V*s
    B["E38"] = ([rs["effb"][i] + rs["effr"][i] for i in range(n)], None)         # total eff-agg V*(1-s)
    B["E39"] = ([1.0 if (rs["sval"][i] is not None and rs["sval"][i] >= 0.60) else 0.0 for i in range(n)], None)
    B["E40"] = (None, "adaptive display zones — deferred (display-only)")
    B["E41"] = (None, "intrabucket iceberg-absorption marks — NOT COMPUTABLE (live tape / depth.db, 6h)")
    B["E42"] = ([nlevels(i) for i in range(n)], None)                            # ladder depth
    B["E43"] = ([poc_share(i) for i in range(n)], None)
    B["E44"] = (None, "per-level imbalance flag — deferred (per-level, needs trailing BER/SER baseline)")
    B["E45"] = ([nlevels(i) for i in range(n)], None)
    B["E46"] = (None, "further-derivable ladder metrics — deferred (unspecified)")
    B["E47"] = (liqs, None); B["E48"] = (liql, None)
    B["E49"] = ([(liqs[i] + liql[i]) / cv[i] if cv[i] else NULL for i in range(n)], None)
    B["E50"] = ([sz_total_count(i) for i in range(n)], None)
    B["E51"] = ([sz_total_vol(i) for i in range(n)], None)
    B["E52"] = (None, "LARGE/SMALL/WHALE slices — deferred (needs size_thr percentile anchors, E53)")
    B["E53"] = (None, "size_thr rolling-60min anchors — NOT in closed_buckets (engine_state; moving anchor)")
    B["E54"] = (rs["vpin"], None)
    B["E55"] = ([vpin_pct(i) for i in range(n)], None)                           # vpin percentile in trailing-240
    B["E56"] = ([atr_comp(i) for i in range(n)], None)
    B["E57"] = ([sweep_depth(i) for i in range(n)], None)
    B["E58"] = (None, "fail/reclaim depth — deferred (bespoke trap geometry)")
    B["E59"] = (None, "trailing-30 baseline bundle — deferred (multi-quantity, ambiguous canonical)")
    B["E60"] = (rs["state"], None)                                              # categorical state
    B["E61"] = (None, "per-state factor breakdown — deferred (dict output, not one scalar)")
    B["E62"] = (rs["state"], None)                                              # categorical (12-state)
    for c in ("E63", "E64", "E65"):
        B[c] = (None, "order-block / cross-tf structure — deferred (needs order_blocks reconstruction)")
    for c in ("E66", "E67", "E68", "E69", "E70", "E71", "E72"):
        B[c] = (None, "six-hour store (depth.db / trade tape) — NOT COMPUTABLE (excluded by design)")
    return B


# ── transform engine: derivation text -> generic transform of the base scalar ──
def classify_transform(text):
    """Map a derivation text to ONE unambiguous generic-transform kind, or None (=> deferred). No compute."""
    t = text.lower()
    if "day-rank" in t or "day rank" in t:
        return "dayrank"
    if "z-score" in t or "z score" in t or (" z " in (" " + t + " ")) or ("anomaly" in t and "trailing" in t):
        return "z"
    if "percentile" in t or "pctile" in t:
        return "pctile"
    if "streak" in t:
        return "streak"
    if "slope" in t:
        return "slope"
    if "accel" in t:
        return "accel"
    if t.startswith("sign") or " sign " in t:
        return "sign"
    if ("log value" in t or t.startswith("log") or " log " in t) and "log-odds" not in t and "odds" not in t:
        return "log"
    if "raw" in t or "absolute value" in t:
        return "raw"
    return None


def precompute_field_transforms(x_list, times, kinds):
    """Precompute, ONCE per base field, each needed transform as a full n-array (masked with None until the
    window fills). Vectorized with numpy/pandas; per-cell lookup is then O(1)."""
    import numpy as np
    import pandas as pd
    n = len(x_list)
    x = np.array([np.nan if (v is None or isinstance(v, str)) else float(v) for v in x_list], float)
    s = pd.Series(x)
    out = {}
    def emit(a):
        return [None if (a[i] is None or (isinstance(a[i], float) and np.isnan(a[i]))) else a[i] for i in range(n)]
    if "raw" in kinds:
        out["raw"] = list(x_list)
    if "log" in kinds:
        out["log"] = [None if (v is None or isinstance(v, str)) else signed_log(float(v)) for v in x_list]
    if "sign" in kinds:
        out["sign"] = [None if np.isnan(x[i]) else (1 if x[i] > 0 else (-1 if x[i] < 0 else 0)) for i in range(n)]
    if "z" in kinds:
        m = s.shift(1).rolling(W30).mean(); sd = s.shift(1).rolling(W30).std(ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (x - m.values) / sd.values
        z = np.where(sd.values == 0, 0.0, z)
        out["z"] = emit(z)
    if "streak" in kinds:
        m = s.shift(1).rolling(W30).mean().values
        st = [None] * n; run = 0; sign = 0
        for i in range(n):
            if np.isnan(m[i]) or np.isnan(x[i]):
                run = 0; sign = 0; st[i] = None; continue
            sgn = 1 if x[i] >= m[i] else -1
            run = run + 1 if sgn == sign else 1
            sign = sgn; st[i] = sgn * run
        out["streak"] = st
    if "pctile" in kinds:
        pc = [None] * n
        for i in range(W240, n):
            w = x[i - W240:i]
            w = w[~np.isnan(w)]
            if len(w) and not np.isnan(x[i]):
                pc[i] = float((w <= x[i]).sum()) / len(w)
        out["pctile"] = pc
    if "slope" in kinds:
        ker = np.array([-2.0, -1.0, 0.0, 1.0, 2.0]) / 10.0   # slope over last 5 (centered t, var=10)
        sl = [None] * n
        for i in range(4, n):
            w = x[i - 4:i + 1]
            sl[i] = float((ker * w).sum()) if not np.isnan(w).any() else None
        out["slope"] = sl
    if "accel" in kinds:
        ac = [None] * n
        for i in range(2, n):
            if not (np.isnan(x[i]) or np.isnan(x[i - 1]) or np.isnan(x[i - 2])):
                ac[i] = x[i] - 2 * x[i - 1] + x[i - 2]
        out["accel"] = ac
    if "dayrank" in kinds:
        import bisect
        dr = [None] * n; cur = None; day = None; seen = []
        for i in range(n):
            d = int(times[i] // 86400)
            if d != day:
                day = d; seen = []
            if np.isnan(x[i]):
                dr[i] = None; continue
            bisect.insort(seen, x[i])
            if len(seen) >= 2:
                dr[i] = bisect.bisect_right(seen, x[i]) / len(seen)
        out["dayrank"] = dr
    return out


# ── K.* Keltner channel (EMA-20 / 2.0xATR-20) + rolling-POC-240 (frozen params) ──
def build_kc(snaps):
    n = len(snaps)
    close = [float(snaps[i].get("close", 0.0)) for i in range(n)]
    high = [float(snaps[i].get("high", 0.0)) for i in range(n)]
    low = [float(snaps[i].get("low", 0.0)) for i in range(n)]
    # EMA-20 of close (masked until KC_N)
    mid = [NULL] * n
    alpha = 2.0 / (KC_N + 1)
    ema = None
    for i in range(n):
        ema = close[i] if ema is None else (alpha * close[i] + (1 - alpha) * ema)
        if i >= KC_N - 1:
            mid[i] = ema
    # ATR-20 = SMA of true range over KC_N
    tr = [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
                               for i in range(1, n)]
    atr = [NULL] * n
    for i in range(n):
        if i >= KC_N:
            atr[i] = sum(tr[i - KC_N + 1:i + 1]) / KC_N
    # rolling-POC-240 (sliding merged ladder)
    from collections import defaultdict
    win = defaultdict(float)
    rpoc = [NULL] * n
    lvl_tot = []
    for i in range(n):
        d = defaultdict(float)
        for p, v in (snaps[i].get("levels") or {}).items():
            d[float(p)] += v.get("b", 0.0) + v.get("s", 0.0)
        lvl_tot.append(d)
    for i in range(n):
        for p, v in lvl_tot[i].items():
            win[p] += v
        if i >= POC_N:
            for p, v in lvl_tot[i - POC_N].items():
                win[p] -= v
                if win[p] <= 1e-9:
                    del win[p]
        if i >= POC_N - 1 and win:
            rpoc[i] = max(win, key=win.get)
    out = []
    prev_rpoc = None
    for i in range(n):
        row = {c: NULL for c in ["K.01", "K.02", "K.03", "K.04", "K.05", "K.06",
                                 "K.07", "K.08", "K.09", "K.10", "K.11", "K.12"]}
        if mid[i] is not None and atr[i] is not None:
            up = mid[i] + KC_K * atr[i]; dn = mid[i] - KC_K * atr[i]
            band = up - dn
            row["K.01"] = mid[i]; row["K.02"] = up; row["K.03"] = dn
            row["K.04"] = band / mid[i] if mid[i] else NULL
            row["K.05"] = (close[i] - dn) / band if band else NULL
            row["K.06"] = ("above" if close[i] > up else "below" if close[i] < dn else "inside")
            row["K.07"] = min(abs(close[i] - up), abs(close[i] - dn)) / (atr[i] or 1) if atr[i] else NULL
            if i >= KC_N and mid[i - 1] is not None:
                row["K.08"] = mid[i] - mid[i - 1]
        if rpoc[i] is not None:
            row["K.10"] = rpoc[i]
            row["K.11"] = close[i] - rpoc[i]
            if prev_rpoc is not None:
                row["K.12"] = rpoc[i] - prev_rpoc
            prev_rpoc = rpoc[i]
        out.append(row)
    # K.09 squeeze = bandwidth percentile-rank in trailing-240 (low = squeeze)
    bw = [out[i]["K.04"] for i in range(n)]
    for i in range(n):
        out[i]["K.09"] = pctile_trailing(bw, i, W240)
    return out


# ── C.* PRE-ENTRY context (indices [i-n_ctx, i-1]) ──────────────────────────
def build_context(snaps, rs, i, n_ctx=CTX_N):
    """C.* context over the ``n_ctx`` buckets strictly before entry. Default n_ctx=15 = the registry's
    literal definition (byte-identical to T1); the weight sweep passes k-1 per frame (labeled C@k there —
    only k=16 equals the registry). n_ctx<1 (the k=1 bookend) -> all None."""
    C = {("C.%02d" % k): NULL for k in range(1, 13)}
    if n_ctx < 1 or i < n_ctx:
        return C
    a, b = i - n_ctx, i                                  # window [i-n_ctx .. i-1]
    close = [float(snaps[j].get("close", 0.0)) for j in range(a, b)]
    high = [float(snaps[j].get("high", 0.0)) for j in range(a, b)]
    low = [float(snaps[j].get("low", 0.0)) for j in range(a, b)]
    rng = [high[k] - low[k] for k in range(n_ctx)]
    delta = [float(snaps[j].get("buy_vol", 0.0)) - float(snaps[j].get("sell_vol", 0.0)) for j in range(a, b)]
    doi = [(float(snaps[j].get("opL", 0.0)) + float(snaps[j].get("opS", 0.0)))
           - (float(snaps[j].get("clL", 0.0)) + float(snaps[j].get("clS", 0.0))) for j in range(a, b)]
    rets = [close[k] - close[k - 1] for k in range(1, n_ctx)]
    net = close[-1] - close[0]
    C["C.01"] = 1 if net > 0 else (-1 if net < 0 else 0)
    C["C.02"] = net / close[0] if close[0] else NULL
    C["C.03"] = slope_lastN(close, n_ctx - 1, n_ctx)      # slope over the window
    # R^2 of close vs time
    mx = (n_ctx - 1) / 2.0; my = sum(close) / n_ctx
    sxx = sum((k - mx) ** 2 for k in range(n_ctx)); syy = sum((c - my) ** 2 for c in close)
    sxy = sum((k - mx) * (close[k] - my) for k in range(n_ctx))
    r2 = (sxy * sxy) / (sxx * syy) if sxx and syy else NULL
    C["C.04"] = "trend" if (r2 is not None and r2 >= 0.5) else "chop"
    # drawup / drawdown over window
    peak = close[0]; trough = close[0]; maxup = 0.0; maxdn = 0.0
    for c in close:
        peak = max(peak, c); trough = min(trough, c)
        maxup = max(maxup, c - trough); maxdn = min(maxdn, c - peak)
    C["C.05"] = maxup / close[0] if close[0] else NULL      # drawup fraction (drawdown in C.05b via maxdn sign)
    C["C.06"] = _std(rets)                                  # realized vol (stdev of 1-bucket changes)
    C["C.07"] = sum(1 for k in range(1, n_ctx) if high[k] > high[k - 1]) \
        - sum(1 for k in range(1, n_ctx) if low[k] < low[k - 1])   # HH minus LL count
    states = [rs["state"][j] for j in range(a, b) if rs["state"][j] is not None]
    if states:
        C["C.08"] = max(set(states), key=states.count)     # dominant 12-state
    C["C.09"] = sum(delta)
    C["C.10"] = sum(doi)
    m_rng = sum(rng) / n_ctx
    C["C.11"] = sum(1 for r in rng if r < m_rng) / n_ctx   # compression share
    sweeps = 0
    for k in range(1, n_ctx):
        if high[k] > max(high[:k] or [high[k]]) or low[k] < min(low[:k] or [low[k]]):
            sweeps += 1
    C["C.12"] = sweeps
    return C


# ── O.* directional excursion tail (per episode, from OHLC) ─────────────────
O_TAIL = ["O.%02d" % k for k in range(2, 26)]


def compute_paths(bk, ids, ep):
    O = {c: NULL for c in O_TAIL}
    i = ep["i"]; long = ep["direction"] == "long"
    entry = ep["entry"]; tp = ep["tp"]; sl = ep["sl"]; ec = ep["entry_close"]
    O["O.02"] = ep["outcome"]
    if ep["outcome"] not in ("TP", "SL"):
        return O                                           # UNRESOLVED -> excursion NULL
    n = len(bk)
    touch = ep["touch_idx"]
    hz = ep["horizon_end"]
    last = touch
    for j in range(i + 1, n):
        if bk[j].start_time > hz:
            break
        last = j
    tp_dist = abs(tp - entry); sl_dist = abs(sl - entry)

    def fav_price(j):      # most-favorable price in bucket j (long: high, short: low)
        return bk[j].high if long else bk[j].low
    def adv_price(j):      # most-adverse price in bucket j (long: low, short: high)
        return bk[j].low if long else bk[j].high
    def fav_disp(px):      # favorable displacement magnitude vs entry
        return (px - entry) if long else (entry - px)
    def adv_disp(px):
        return (entry - px) if long else (px - entry)

    # pre-touch path (i+1 .. touch)
    max_adv_before = 0.0; max_fav_before = 0.0; underwater_t = 0.0; inprofit_t = 0.0
    prev_side = 0; recross = 0
    for j in range(i + 1, touch + 1):
        max_adv_before = max(max_adv_before, adv_disp(adv_price(j)))
        max_fav_before = max(max_fav_before, fav_disp(fav_price(j)))
        c = bk[j].close_price
        dt = bk[j].end_time - bk[j - 1].end_time if j > i + 1 else bk[j].end_time - ec
        side = 1 if (c > entry if long else c < entry) else (-1 if (c < entry if long else c > entry) else 0)
        if side < 0:
            underwater_t += dt
        elif side > 0:
            inprofit_t += dt
        if side != 0 and prev_side != 0 and side != prev_side:
            recross += 1
        if side != 0:
            prev_side = side
    O["O.03"] = bk[touch].end_time - ec
    O["O.04"] = touch - i
    O["O.05"] = ids[touch]
    O["O.25"] = recross

    if ep["outcome"] == "TP":
        # post-TP favorable continuation (touch .. last)
        best = tp; best_j = touch
        for j in range(touch, last + 1):
            fp = fav_price(j)
            if fav_disp(fp) > fav_disp(best):
                best = fp; best_j = j
        beyond = (fav_disp(best) - tp_dist) / entry * 100.0     # % beyond TP
        O["O.06"] = max(0.0, beyond)
        O["O.07"] = bk[best_j].end_time - ec
        O["O.08"] = best_j - i
        hrs = (bk[best_j].end_time - bk[touch].end_time) / 3600.0
        O["O.09"] = (beyond / hrs) if hrs > 0 else NULL
        span = fav_disp(best) - tp_dist
        final = fav_disp(bk[last].close_price) - tp_dist
        O["O.10"] = (final / span) if span > 0 else NULL       # retention (give-back)
        O["O.11"] = 1 if best_j == last else 0                 # max at window edge
        O["O.16"] = ids[best_j]
        O["O.17"] = max_adv_before / entry * 100.0             # against-entry depth before hit %
        O["O.19"] = 1 if max_adv_before > 0 else 0
        O["O.20"] = underwater_t
        O["O.21"] = (max_adv_before / sl_dist) if sl_dist else NULL   # near-death ratio
    else:  # SL
        worst = sl; worst_j = touch
        for j in range(touch, last + 1):
            ap = adv_price(j)
            if adv_disp(ap) > adv_disp(worst):
                worst = ap; worst_j = j
        beyond = (adv_disp(worst) - sl_dist) / entry * 100.0
        O["O.12"] = max(0.0, beyond)
        O["O.13"] = bk[worst_j].end_time - ec
        hrs = (bk[worst_j].end_time - bk[touch].end_time) / 3600.0
        O["O.14"] = (beyond / hrs) if hrs > 0 else NULL
        # recovery: price returned to entry (in favor) after SL
        rec = 0
        for j in range(touch, last + 1):
            if fav_disp(fav_price(j)) >= 0 and (bk[j].high >= entry if long else bk[j].low <= entry):
                rec = 1; break
        O["O.15"] = rec
        O["O.16"] = ids[worst_j]
        O["O.18"] = max_fav_before / entry * 100.0             # in-favor before stop %
        O["O.22"] = 1 if max_fav_before > 0 else 0
        O["O.23"] = inprofit_t
        O["O.24"] = (max_fav_before / tp_dist) if tp_dist else NULL   # near-win ratio
    return O


# ── G.* canonical primitives (the unambiguous ones; composites deferred) ────
def build_g(base, rs, times, i):
    """Only the G items that ARE a named primitive/transform get a value; the composites are deferred."""
    g = {}
    def bv(code):
        s = base.get(code, (None, None))[0]
        return s[i] if s is not None else NULL
    g["G01.1"] = bv("E03")                                   # duration
    g["G02.1"] = bv("E07")                                   # body fraction (geometry headline)
    g["G03.1"] = bv("E14")                                   # delta
    g["G04.1"] = (bv("E06") / bv("E14")) if (bv("E14") not in (None, 0)) else NULL   # price moved per net contract
    g["G06.1"] = bv("E21")                                   # net OI change
    g["G08.1"] = bv("E29")                                   # vel
    g["G10.1"] = bv("E10")                                   # poc_pos
    return g

