"""Shared lib for the PER-TIMEFRAME reward/eff study.

LEAN loading: keep ONLY scalar OHLCV+start_time per bar (drop the heavy nested sz_cb/sz_cs/levels) and cache to
study/_re_<tf>.npz, so every timeframe — including 1m (1.26M bars) — loads instantly with a few hundred MB, never
the multi-GB full-dict OOM. 1m reuses the existing study/_1m_arrays.npz.

reward/eff share over a window = 100 * rpe_buy / (rpe_buy + rpe_sell), rpe_side = (sum of that side's rewarded price
moves) / (that side's taker volume). Buy>50 => buyers rewarded. Everything here is CAUSAL (trailing) and vectorised.
"""
import os
import sys
import glob
import gzip
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RECON = os.path.join(HERE, "recon_archive")
Y2026 = 1767225600.0        # 2026-01-01 UTC (recon spans 2025-01 .. 2026-06, so year = 2025 if st < this else 2026)

# bars per calendar period, per tf (session ~= 8h overlap block, day = 24h, week = 168h, month ~= 30d)
BARS_PER = {
    "1m":  {"session": 480, "day": 1440, "week": 10080, "month": 43200},
    "5m":  {"session": 96,  "day": 288,  "week": 2016,  "month": 8640},
    "15m": {"session": 32,  "day": 96,   "week": 672,   "month": 2880},
    "1h":  {"session": 8,   "day": 24,   "week": 168,   "month": 720},
    "4h":  {"session": 2,   "day": 6,    "week": 42,    "month": 180},
}


def _lean_stream(tf):
    by_bid = {}
    for fn in sorted(glob.glob(os.path.join(RECON, tf, "%s_*.jsonl.gz" % tf))):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                by_bid[int(r["bid"])] = (
                    float(d.get("open", d.get("open_price", 0.0)) or 0.0),
                    float(d.get("high", 0.0) or 0.0),
                    float(d.get("low", 0.0) or 0.0),
                    float(d.get("close", d.get("close_price", 0.0)) or 0.0),
                    float(d.get("buy_vol", 0.0) or 0.0),
                    float(d.get("sell_vol", 0.0) or 0.0),
                    float(d.get("start_time", 0.0) or 0.0),
                )
    bids = sorted(by_bid)
    M = np.array([by_bid[b] for b in bids], dtype=float)
    return M


def load_arrays(tf, root=RECON):
    """Return dict of numpy arrays o,h,l,c,bv,sv,st,yr for `tf`. npz-cached (built once, lean)."""
    if tf == "1m":
        cache = os.path.join(HERE, "_1m_arrays.npz")
        if os.path.exists(cache):
            z = np.load(cache)
            st = z["st1"]
            return {"o": z["o1"], "h": z["h1"], "l": z["l1"], "c": z["c1"], "bv": z["bv1"], "sv": z["sv1"],
                    "st": st, "yr": np.where(st < Y2026, 2025, 2026)}
    cache = os.path.join(HERE, "_re_%s.npz" % tf)
    if os.path.exists(cache):
        z = np.load(cache)
        M = z["M"]
    else:
        M = _lean_stream(tf)
        np.savez_compressed(cache, M=M)
    st = M[:, 6]
    return {"o": M[:, 0], "h": M[:, 1], "l": M[:, 2], "c": M[:, 3], "bv": M[:, 4], "sv": M[:, 5],
            "st": st, "yr": np.where(st < Y2026, 2025, 2026)}


def _cum(d):
    o, c, bv, sv = d["o"], d["c"], d["bv"], d["sv"]
    dp = np.where(o > 0, (c - o) / o, 0.0)
    up = np.where(dp > 0, dp, 0.0); dn = np.where(dp < 0, -dp, 0.0)
    return (np.concatenate([[0.0], np.cumsum(up)]), np.concatenate([[0.0], np.cumsum(dn)]),
            np.concatenate([[0.0], np.cumsum(bv)]), np.concatenate([[0.0], np.cumsum(sv)]))


def roll_share(d, W, cum=None):
    """Trailing-W buy share (0..100) of reward-per-effort at each bar (causal). NaN during warm-up / no volume."""
    cU, cD, cB, cS = cum if cum is not None else _cum(d)
    n = len(d["c"])
    s = np.full(n, np.nan)
    idx = np.arange(W - 1, n)
    j = idx - W + 1
    rb = cU[idx + 1] - cU[j]; rs = cD[idx + 1] - cD[j]
    eb = cB[idx + 1] - cB[j]; es = cS[idx + 1] - cS[j]
    rpeb = np.where(eb > 0, rb / eb, 0.0); rpes = np.where(es > 0, rs / es, 0.0)
    t = rpeb + rpes
    val = np.where(t > 0, 100.0 * rpeb / t, np.nan)
    s[idx] = val
    return s


def anchor_share(d, period, cum=None):
    """Buy share since the START of the current calendar `period` ('day'|'week'|'month', UTC), per bar (causal,
    expanding within the period). Uses true UTC boundaries via period id from start_time."""
    cU, cD, cB, cS = cum if cum is not None else _cum(d)
    st = d["st"]; n = len(st)
    if period == "day":
        pid = np.floor(st / 86400.0)
    elif period == "week":
        pid = np.floor((st + 3 * 86400.0) / 604800.0)          # +3d aligns the epoch-Thursday to Monday weeks
    elif period == "month":
        # calendar month id = year*12+month, via a cheap civil-from-days conversion (vectorised)
        days = np.floor(st / 86400.0).astype(np.int64) + 719468
        era = np.where(days >= 0, days, days - 146096) // 146097
        doe = days - era * 146097
        yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
        y = yoe + era * 400
        doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
        mp = (5 * doy + 2) // 153
        m = np.where(mp < 10, mp + 3, mp - 9)
        y = np.where(m <= 2, y + 1, y)
        pid = (y * 12 + m).astype(float)
    else:
        raise ValueError(period)
    change = np.concatenate([[True], pid[1:] != pid[:-1]])
    start_of = np.maximum.accumulate(np.where(change, np.arange(n), 0))
    j = start_of
    idx = np.arange(n)
    rb = cU[idx + 1] - cU[j]; rs = cD[idx + 1] - cD[j]
    eb = cB[idx + 1] - cB[j]; es = cS[idx + 1] - cS[j]
    rpeb = np.where(eb > 0, rb / eb, 0.0); rpes = np.where(es > 0, rs / es, 0.0)
    t = rpeb + rpes
    return np.where(t > 0, 100.0 * rpeb / t, np.nan)


def reliability(side, valid):
    """side: -1/0/+1 per bar; valid: bool mask. Returns (flip_rate, mean_run_len, decisiveness_placeholder)."""
    idx = np.where(valid)[0]
    if len(idx) < 3:
        return float("nan"), float("nan")
    s = side[idx]
    flips = int(np.sum(s[1:] != s[:-1])); flip = flips / (len(idx) - 1)
    runs = []; cur = 1
    for k in range(1, len(s)):
        if s[k] == s[k - 1]:
            cur += 1
        else:
            runs.append(cur); cur = 1
    runs.append(cur)
    return flip, float(np.mean(runs))


def fwd_and_barrier(d, direction, horizons, f, K, sample=1):
    """direction: +1/-1/0 per bar (0 = skip). Returns per-year dict:
    { yr: (n, {h: edge_bps}, win_pct) }. edge = direction * fwd_return (bps); barrier = symmetric 1:1 (+/-f, K bars)."""
    C, H, L, yr = d["c"], d["h"], d["l"], d["yr"]
    n = len(C)
    out = {}
    for Y in (2025, 2026):
        m = (yr == Y) & (direction != 0)
        edge = {}
        for k in horizons:
            ii = np.where(m[:n - k])[0]
            if sample > 1:
                ii = ii[::sample]
            if len(ii) == 0:
                edge[k] = float("nan"); continue
            edge[k] = float((direction[ii] * (C[ii + k] / C[ii] - 1.0) * 1e4).mean())
        ii = np.where(m)[0]; ii = ii[ii + K < n]
        if sample > 1:
            ii = ii[::sample]
        w = l = 0
        for i in ii:
            dd = direction[i]; e = C[i]
            tp = e * (1 + dd * f); sl = e * (1 - dd * f)
            hi = H[i + 1:i + 1 + K]; lo = L[i + 1:i + 1 + K]
            if dd > 0:
                tp_hit = np.where(hi >= tp)[0]; sl_hit = np.where(lo <= sl)[0]
            else:
                tp_hit = np.where(lo <= tp)[0]; sl_hit = np.where(hi >= sl)[0]
            th = tp_hit[0] if len(tp_hit) else 10 ** 9
            sh = sl_hit[0] if len(sl_hit) else 10 ** 9
            if sh < th:
                l += 1
            elif th < sh:
                w += 1
        out[Y] = (int(m.sum()), edge, 100.0 * w / max(1, w + l))
    return out


def barrier_counts(d, direction, f, K):
    """NON-OVERLAPPING symmetric 1:1 barrier (canonical taken() basis: skip until the prior trade resolves). Returns
    {yr: (wins, losses, timeouts)} for a clean binomial test of win-rate vs 50%."""
    C, H, L, yr = d["c"], d["h"], d["l"], d["yr"]
    n = len(C)
    out = {2025: [0, 0, 0], 2026: [0, 0, 0]}
    nxt = 0
    for i in range(n - 1):
        if i < nxt or direction[i] == 0:
            continue
        dd = direction[i]; e = C[i]
        tp = e * (1 + dd * f); sl = e * (1 - dd * f)
        hi = H[i + 1:i + 1 + K]; lo = L[i + 1:i + 1 + K]
        if dd > 0:
            th = np.where(hi >= tp)[0]; sh = np.where(lo <= sl)[0]
        else:
            th = np.where(lo <= tp)[0]; sh = np.where(hi >= sl)[0]
        t = th[0] if len(th) else 10 ** 9
        s = sh[0] if len(sh) else 10 ** 9
        Y = int(yr[i]); res = min(t, s)
        if res == 10 ** 9:
            out[Y][2] += 1; nxt = i + K + 1
        else:
            out[Y][0 if t < s else 1] += 1; nxt = i + res + 2
    return out


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else "4h"
    d = load_arrays(tf)
    print("%s: %d bars  2025=%d 2026=%d  bars/day=%d /week=%d"
          % (tf, len(d["c"]), int((d["yr"] == 2025).sum()), int((d["yr"] == 2026).sum()),
             BARS_PER[tf]["day"], BARS_PER[tf]["week"]))
