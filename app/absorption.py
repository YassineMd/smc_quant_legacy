"""ABSORPTION RESIDUAL (R) — did this bucket's delta produce the price move it should have?

DISPLAY-ONLY, descriptive. Gates no trade, touches no freeze.

    dV = buy_vol - sell_vol                (effort, in volume units)
    dP = (close - open) / open * 100       (result, in %)

Both are z-scored against the SAME trailing window (strictly prior buckets — causal), then:

    R = Z_dP - rho * Z_dV

where `rho` is the delta/price correlation measured over that same trailing window.

WHY `rho` AND NOT 1.0 — this is the whole point of the metric. The naive form `Z_dV - Z_dP` assumes
delta and price move one-for-one. Measured on this tape rho is ~0.685, so the price move a given delta
should produce is 0.69*Z_dV, not 1.00*Z_dV; subtracting the full Z_dP over-corrects by ~1.5x. The
consequence is not cosmetic: the naive score stays ~0.45-correlated with raw delta, i.e. it half-answers
"was the delta big?" instead of "did the delta work?". R is orthogonal to delta by construction
(measured corr(R, Z_dV) = -0.049), which is what makes it an absorption measure rather than a delta proxy.

R IS SIGNED IN PRICE TERMS, so read it against the delta's sign:
    delta POSITIVE (net buying) and R strongly NEGATIVE -> buying ABSORBED (price lagged the buying)
    delta NEGATIVE (net selling) and R strongly POSITIVE -> selling ABSORBED
    R ~ 0                                                -> effort and result proportional (normal)
`absorption()` folds that into one number oriented so POSITIVE always means "absorbed", whichever side.

CAVEATS, measured on the live tape and deliberately recorded here:
  * FAT TAILS. kurtosis(dV) ~ 9.1 vs 3.0 for a normal, and |Z_dV|>3 fires on 1.9% of buckets vs 0.3%
    expected. A 30-sample sigma is dominated by one outlier, so treat |R| > 3 as "extreme, low
    confidence" rather than "3 sigma".
  * rho DRIFTS with regime; it is recomputed per bucket from the trailing window rather than pinned.
  * NOT INDEPENDENT of what the terminal already shows: buyer_er/seller_er (volume per tick) with
    region_state.exhaustion_mults is the per-side effort/result z-score, and the CVD_RE_* borders are
    the ratio form. R is the pooled, delta-orthogonal version — a continuous readout rather than a flag.
"""
from __future__ import annotations
import math

WINDOW = 30          # trailing buckets — matches EXH_WINDOW / VEL_ABN_WINDOW / CVD_RE_WINDOW
MIN_OBS = 20         # below this the trailing stats are too thin to trust -> None


def _oc(b):
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def _dv_dp(b):
    o, c = _oc(b)
    if o <= 0 or c <= 0:
        return None
    return (float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0),
            (c - o) / o * 100.0)


def residual(buckets: list, i: int, window: int = WINDOW):
    """R for bucket i, or None when the trailing window is too thin / degenerate. CAUSAL: the baseline
    uses buckets[i-window:i] only — never bucket i itself, never anything after it."""
    if i <= 0 or i >= len(buckets):
        return None
    cur = _dv_dp(buckets[i])
    if cur is None:
        return None
    win = []
    for b in buckets[max(0, i - window):i]:
        p = _dv_dp(b)
        if p is not None:
            win.append(p)
    if len(win) < MIN_OBS:
        return None
    vs = [p[0] for p in win]; ps = [p[1] for p in win]
    n = float(len(win))
    mv = sum(vs) / n; mp = sum(ps) / n
    sv = math.sqrt(sum((x - mv) ** 2 for x in vs) / (n - 1))
    sp = math.sqrt(sum((x - mp) ** 2 for x in ps) / (n - 1))
    if sv <= 0 or sp <= 0:
        return None
    cov = sum((vs[k] - mv) * (ps[k] - mp) for k in range(len(win))) / (n - 1)
    rho = max(-1.0, min(1.0, cov / (sv * sp)))
    zv = (cur[0] - mv) / sv
    zp = (cur[1] - mp) / sp
    return zp - rho * zv


def absorption(buckets: list, i: int, window: int = WINDOW):
    """(A, R, side) with A ORIENTED so POSITIVE = absorbed regardless of which side was aggressing.

    side = +1 when this bucket's delta is net BUYING, -1 when net SELLING, 0 when flat.
    A = -R for a buy-side bucket (buying absorbed => price lagged => R negative => A positive)
        +R for a sell-side bucket.
    Returns (None, None, 0) when R is unavailable."""
    R = residual(buckets, i, window)
    if R is None:
        return None, None, 0
    p = _dv_dp(buckets[i])
    if p is None:
        return None, None, 0
    side = 1 if p[0] > 0 else (-1 if p[0] < 0 else 0)
    if side == 0:
        return 0.0, R, 0
    return (-R if side > 0 else R), R, side


def _halves(b):
    """((dV,dP) for h1, (dV,dP) for h2) split at the 50%-VOLUME mark, or None.

    Needs BOTH daemon fields: `delta_h1` (effort at the mark) and `price_h1` (result at the mark).
    price_h1 ships only from the 2026-07-22 13:40 UTC daemon restart onward, so historical buckets
    return None here — bucket-level `residual()` still works on those."""
    dh = b.get("delta_h1"); ph = b.get("price_h1")
    if dh is None or ph is None:
        return None
    o, c = _oc(b)
    ph = float(ph)
    if o <= 0 or c <= 0 or ph <= 0:
        return None
    tot = float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0)
    d1 = float(dh)
    return ((d1, (ph - o) / o * 100.0),            # h1: open -> the 50%-volume price
            (tot - d1, (c - ph) / ph * 100.0))     # h2: that price -> close


def _resid_from(win, cur):
    """R = Zp - rho*Zv for `cur` against the (dV,dP) pairs in `win`. None when degenerate."""
    if len(win) < MIN_OBS:
        return None
    vs = [p[0] for p in win]; ps = [p[1] for p in win]; n = float(len(win))
    mv = sum(vs) / n; mp = sum(ps) / n
    sv = math.sqrt(sum((x - mv) ** 2 for x in vs) / (n - 1))
    sp = math.sqrt(sum((x - mp) ** 2 for x in ps) / (n - 1))
    if sv <= 0 or sp <= 0:
        return None
    cov = sum((vs[k] - mv) * (ps[k] - mp) for k in range(len(win))) / (n - 1)
    rho = max(-1.0, min(1.0, cov / (sv * sp)))
    return (cur[1] - mp) / sp - rho * (cur[0] - mv) / sv


def residual_halves(buckets: list, i: int, window: int = WINDOW):
    """(R_h1, R_h2) for bucket i — each baselined on the SAME half of the prior `window` buckets.

    Per-half baselines, not a pooled one: h1 and h2 have systematically different delta/price
    distributions, so pooling them would bake that asymmetry into the residual.
    Measured over 940 reconstructable buckets: corr(R_h1,R_h2) = -0.050 (near-INDEPENDENT), the halves
    disagree in sign on 51.7% of buckets, and neither proxies the bucket-level R (+0.679 / +0.632).
    Returns (None, None) until enough PRIOR buckets carry price_h1."""
    if i <= 0 or i >= len(buckets):
        return None, None
    cur = _halves(buckets[i])
    if cur is None:
        return None, None
    w1 = []; w2 = []
    for b in buckets[max(0, i - window):i]:
        h = _halves(b)
        if h is not None:
            w1.append(h[0]); w2.append(h[1])
    return _resid_from(w1, cur[0]), _resid_from(w2, cur[1])


def absorption_halves(buckets: list, i: int, window: int = WINDOW):
    """(A_h1, A_h2) oriented so POSITIVE = that half's aggressor was ABSORBED, per the half's own delta sign."""
    R1, R2 = residual_halves(buckets, i, window)
    cur = _halves(buckets[i]) if (0 < i < len(buckets)) else None
    if cur is None:
        return None, None
    out = []
    for R, (dv, _dp) in ((R1, cur[0]), (R2, cur[1])):
        if R is None:
            out.append(None); continue
        out.append(0.0 if dv == 0 else (-R if dv > 0 else R))
    return out[0], out[1]


def label(A):
    """Short verdict for a readout. Thresholds are DESCRIPTIVE, chosen on SD(R) ~ 0.78 measured, not fitted."""
    if A is None:
        return "--"
    if A >= 1.5:
        return "ABSORBED"      # the aggressor got nothing for the effort
    if A >= 0.75:
        return "heavy"
    if A <= -1.5:
        return "EASY"          # price ran far on little effort — a thin book, not a paid-for move
    if A <= -0.75:
        return "light"
    return "proportional"
