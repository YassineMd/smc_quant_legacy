"""S3 — TRADER-SETUP COMPOSITE STUDY. Yassine's two live strategies as composite yes/no features,
screened against the EXISTING 0.5/0.3/6h labels. EXTERNAL pre-registered hypotheses — characterization
on the full 4 days, reported per slice (discovery / spent-holdout); forward data is the judge.

STEP-0 CHANNEL (identified in source): the price-chart bands ARE the code's Keltner overlay
(_keltner_bands / kc_upper / kc_lower): EMA(close, KELTNER_LENGTH=20) ± KELTNER_ATR_MULT=2.25 × Wilder-ATR(20)
over the rendered bucket history; the EMA midline is HIDDEN on screen (operator pref) — the visible center
line is the POC 5% EMA baseline. NOTE: the terminal renders 2.25×ATR (config), NOT the registry's frozen
K.* 2.0 — all channel legs here use the TERMINAL's 2.25 per the ruling ("THESE parameters").

FROZEN CONVENTIONS / FLAGGED JUDGMENT CALLS (no tuning):
 * Selection-scoped legs (A2 P0-crosses, A4/A5 phases) use a trailing W=64-bucket "selection" per entry
   with the terminal's own pre-rolls and LOCKED index (last-7); P0 shares/lean are computed on the global
   series (they are selection-independent by construction; the terminal's <7-bucket edge clamp differs only
   in the first 7 buckets of a render — evaluated here in the long-selection limit). [FLAG]
 * A1 "traded through earlier today" = an earlier same-UTC-day bucket with low<level<high; "retesting from
   above within 0.05%" = current low <= level*(1+5e-4) AND close >= level (short mirrored). VA = standard
   70% expansion from POC on the merged prior-day ladder. [FLAG]
 * A6 "window's top decile" = p90 of the large-buy (large-sell for shorts) per-bucket volume over ALL
   sz-present rows (fixed-273c bins; sz-present universe only). [FLAG]
 * A7 uses the candle BODY vs the band (max(o,c) <= lower for long) — flagged judgment call per the ruling.
 * B2 correction = highs strictly decreasing over ALL buckets from the overshoot+1 through i-1, run >= 2;
   B3 = least-squares line through those highs extrapolated to i, fire if close_i above it. [FLAG]
 * KC warm-up: series seeded at snapshot start; converged well before the first studied entries. [FLAG]
Breakeven reference: Yassine REAL fees assumed VIP0 taker in+out 0.10% RT -> p* = (0.3+0.1)/0.8 = 50.0%. [FLAG]
"""
import os, sys, csv, json, sqlite3, time, math
from collections import defaultdict
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict            # noqa: E402
from app import region_state as R, config                # noqa: E402
import analysis_A                                        # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
W_SEL = 64; LOCK = config.LIVE_PANEL_WINDOW // 2; LW = config.LIVE_PANEL_WINDOW
TOL = 5e-4; NULL = 37.5; BREAKEVEN = 50.0
LARGE_BINS = [k for k in range(config.SIZE_HIST_NBINS)
              if (0.0 if k == 0 else config.SIZE_HIST_EDGES[k - 1]) >= 273.0]


def kc_bands(hi, lo, cl):
    n = len(cl); L = config.KELTNER_LENGTH; M = config.KELTNER_ATR_MULT; k = 2.0 / (L + 1)
    up = np.empty(n); dn = np.empty(n)
    e = cl[0]; a = hi[0] - lo[0]
    up[0] = e + M * a; dn[0] = e - M * a
    for i in range(1, n):
        e = cl[i] * k + e * (1 - k)
        tr = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
        a = (a * (L - 1) + tr) / L
        up[i] = e + M * a; dn[i] = e - M * a
    return up, dn


def day_profiles(bks):
    """{utc_day: (poc, vah, val)} from merged per-day ladders (70% VA, standard POC expansion)."""
    daily = defaultdict(lambda: defaultdict(float))
    for b in bks:
        day = int(b.end_time // 86400)
        for p, v in (b.levels or {}).items():
            daily[day][float(p)] += float(v.get("b", 0.0)) + float(v.get("s", 0.0))
    out = {}
    for day, lad in daily.items():
        if not lad:
            continue
        prices = sorted(lad); vols = [lad[p] for p in prices]
        tot = sum(vols); poc_i = int(np.argmax(vols))
        covered = vols[poc_i]; lo_i = hi_i = poc_i
        while covered < 0.70 * tot and (lo_i > 0 or hi_i < len(prices) - 1):
            vlo = vols[lo_i - 1] if lo_i > 0 else -1.0
            vhi = vols[hi_i + 1] if hi_i < len(prices) - 1 else -1.0
            if vhi >= vlo:
                hi_i += 1; covered += vols[hi_i]
            else:
                lo_i -= 1; covered += vols[lo_i]
        out[day] = (prices[poc_i], prices[hi_i], prices[lo_i])     # (POC, VAH, VAL)
    return out


def p9_global(snaps):
    """Global P9 bull/bear lines + P0 smoothed sum, exactly the terminal's arithmetic (fixed windows)."""
    n = len(snaps)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    a_sh = np.array(R.rolling_share(ab, ar, LW), float)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.array(R.rolling_share(eb, er_, LW), float)
    rb = [s.get("buyer_er", 0.0) for s in snaps]; rs_ = [s.get("seller_er", 0.0) for s in snaps]
    r_sh = np.array(R.rolling_share(rb, rs_, LW), float)
    lean = (1 - 2 * a_sh) * 100 + (2 * e_sh - 1) * 100 + (2 * r_sh - 1) * 100
    ex = R.trailing_exhaustion(snaps, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    s4 = np.empty(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        s4[k] = hold
    bull = (lean + s4) / 4.0; bear = (lean - s4) / 4.0
    idx0 = np.maximum(np.arange(n) - LOCK, 0)
    sum0 = (bull + bull[idx0]) / 2.0 + (bear + bear[idx0]) / 2.0        # P0 smoothed SUM line
    return a_sh, e_sh, r_sh, sum0


def confirmed_crosses(vals):
    """All confirmed crosses of vals at +50/0/-50 (terminal rule: sign change + new side holds >= 2 buckets).
    Returns list of (k, level, is_up)."""
    out = []
    n = len(vals)
    for L in (50.0, 0.0, -50.0):
        for k in range(1, n - 1):
            a = vals[k - 1] - L; b = vals[k] - L
            if a == 0.0 or b == 0.0 or (a < 0) == (b < 0):
                continue
            newpos = b > 0
            if all((vals[j] - L > 0) == newpos for j in range(k, min(n, k + 2))):
                out.append((k, L, newpos))
    return out


def phase_traj(a_sh, e_sh, r_sh, i):
    """Terminal phase trajectory for the trailing W_SEL selection ending at i: returns (up_locked, dn_locked)
    3-vectors [BEFORE, START/DURING, END] at the LOCKED row (EMA seeded at the selection+preroll start)."""
    lo = max(0, i - W_SEL + 1); pre = min(lo, LW); a0 = lo - pre
    lam = config.PHASE_EMA_LAMBDA
    res = {}
    for key, sgn in (("up", 1.0), ("down", -1.0)):
        op = None
        traj = []
        for k in range(a0, i + 1):
            vals = (sgn * (a_sh[k] - 0.5) * 200.0, sgn * (r_sh[k] - 0.5) * 200.0, sgn * (e_sh[k] - 0.5) * 200.0)
            ll = []
            for _n, *g in config.PHASE_STATS[key]:
                s = 0.0
                for x, (m, sd) in zip(vals, g):
                    sd = max(8.0, sd); s += -0.5 * ((x - m) / sd) ** 2 - math.log(sd)
                ll.append(s)
            mx = max(ll); exp_ = [math.exp(l - mx) for l in ll]; t = sum(exp_) or 1.0
            p = [e / t for e in exp_]
            conf = [p[0] * 100, (p[1] + p[2]) * 100, p[3] * 100]
            op = conf[:] if op is None else [lam * op[j] + (1 - lam) * conf[j] for j in range(3)]
            traj.append(op[:])
        traj = traj[pre:]                                   # drop pre-roll -> [lo, i]
        ti = len(traj) - 1 - LOCK
        res[key] = traj[ti if ti >= 0 else -1]
    return res["up"], res["down"]


def fit_line_break(idx_hi, ys, xi, yi, above):
    """LS line through (idx, ys); True if yi is above (long) / below (short) the line at xi."""
    x = np.array(idx_hi, float); y = np.array(ys, float)
    if len(x) < 2:
        return False
    A = np.vstack([x, np.ones_like(x)]).T
    m, c = np.linalg.lstsq(A, y, rcond=None)[0]
    line = m * xi + c
    return yi > line if above else yi < line


def main():
    t0 = time.time()
    db = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
    tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone(); con.close()
    bks = [_bucket_from_dict(d) for d in raw]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks); base_id = int(tc[0]) - n
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    op = np.array([b.open_price for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks])
    kc_up, kc_dn = kc_bands(hi, lo_, cl)
    profiles = day_profiles(bks)
    big = analysis_A.large_net_map(db)
    lgv_b = np.array([sum((getattr(b, "sz_vb", None) or [0] * 21)[k] for k in LARGE_BINS) for b in bks])
    lgv_s = np.array([sum((getattr(b, "sz_vs", None) or [0] * 21)[k] for k in LARGE_BINS) for b in bks])
    sz_present = np.array([(getattr(b, "sz_vb", None) is not None and (sum(b.sz_vb) + sum(b.sz_vs)) > 0)
                           for b in bks])
    p90_b = float(np.percentile(lgv_b[sz_present], 90)) if sz_present.any() else np.inf
    p90_s = float(np.percentile(lgv_s[sz_present], 90)) if sz_present.any() else np.inf
    a_sh, e_sh, r_sh, sum0 = p9_global(snaps)
    crosses = confirmed_crosses(list(sum0))
    print("[%.0fs] prep done (crosses on P0 sum: %d)" % (time.time() - t0, len(crosses)), flush=True)

    def leg_A1(i, long):
        day = int(et[i] // 86400)
        prev = profiles.get(day - 1)
        if prev is None:
            return None                                     # no prior UTC day in snapshot
        day_start_idx = np.searchsorted(et // 86400, day, side="left")
        for level in prev:
            through = np.any((lo_[day_start_idx:i] < level) & (hi[day_start_idx:i] > level)) if i > day_start_idx else False
            if not through:
                continue
            if long and lo_[i] <= level * (1 + TOL) and cl[i] >= level:
                return True
            if not long and hi[i] >= level * (1 - TOL) and cl[i] <= level:
                return True
        return False

    def leg_A2(i, long):
        end = i - LOCK
        if end < 2:
            return False
        vis = [c for c in crosses if (i - W_SEL + 1) <= c[0] < end and c[1] != 0.0]
        last_per = {}
        for k, L, isup in vis:
            last_per[L] = isup
        if len(last_per) < 2:
            return False
        return all(v == long for v in last_per.values())    # both most-recent ±50 crosses bull (long) / bear

    def leg_A6(i, long):
        if not sz_present[max(0, i - 4):i + 1].all():
            return None
        for j in range(max(0, i - 4), i + 1):
            if long and lgv_b[j] >= p90_b and big.get(base_id + j + 1) == "large-buy":
                return True
            if not long and lgv_s[j] >= p90_s and big.get(base_id + j + 1) == "large-sell":
                return True
        return False

    def leg_A7(i, long):
        for j in range(max(0, i - 9), i + 1):
            body_hi = max(op[j], cl[j]); body_lo = min(op[j], cl[j])
            if long and body_hi <= kc_dn[j]:
                return True
            if not long and body_lo >= kc_up[j]:
                return True
        return False

    def setup_B(i, long):
        for o in range(i - 1, max(0, i - 12) - 1, -1):      # most recent overshoot within last 12
            osh = (cl[o] >= kc_up[o]) if long else (cl[o] <= kc_dn[o])
            if not osh:
                continue
            corr = list(range(o + 1, i))
            if len(corr) < 2:
                return False, (True, False, False)
            seq = hi[corr] if long else lo_[corr]
            mono = all((seq[k] < seq[k - 1]) if long else (seq[k] > seq[k - 1]) for k in range(1, len(seq)))
            if not mono:
                return False, (True, False, False)
            b3 = fit_line_break(corr, seq, i, cl[i], above=long)
            return (b3, (True, True, b3))
        return False, (False, False, False)

    # labels
    df = pd.read_parquet(os.path.join(OUT, "dataset.parquet"))
    lab = {}
    for d in ("long", "short"):
        s = df[df["L.01"] == d].set_index("bucket_id")
        lab[d] = (s["L.02"].to_dict(), s["L.08"].to_dict(), s["L.06"].astype(float).to_dict())
    ts_all = df["L.06"].astype(float); tmin, tmax = ts_all.min(), ts_all.max()
    tcut = tmin + 0.70 * (tmax - tmin); emb = tcut + 6 * 3600

    rows = []; fires = []
    ph_cache = {}
    for i in range(16, n):
        bid = base_id + i + 1
        if bid not in lab["long"][0]:
            continue
        need_phase = None
        for d, long in (("long", True), ("short", False)):
            # A3: eff-agg share at the LOCKED index (the badge's value), bull side for longs / bear for shorts
            sh = e_sh[max(0, i - LOCK)]
            A3 = (sh * 100 >= 65.0) if long else ((1 - sh) * 100 >= 65.0)
            legs = {}
            legs["A1"] = leg_A1(i, long)
            legs["A2"] = leg_A2(i, long)
            legs["A3"] = A3
            if need_phase is None:
                if i not in ph_cache:
                    ph_cache[i] = phase_traj(a_sh, e_sh, r_sh, i)
                need_phase = ph_cache[i]
            upv, dnv = need_phase
            colv = upv if long else dnv
            legs["A4"] = (int(np.argmax(colv)) == 1)
            sd_up, sd_dn = upv[1], dnv[1]
            legs["A5"] = ((sd_up - sd_dn) >= 15.0) if long else ((sd_dn - sd_up) >= 15.0)
            legs["A6"] = leg_A6(i, long)
            legs["A7"] = leg_A7(i, long)
            fireA = all(v is True for v in legs.values())
            fireB, blegs = setup_B(i, long)
            out_ = lab[d][0][bid]; whip = lab[d][1][bid]; ts = lab[d][2][bid]
            rows.append(dict(i=i, bid=bid, d=d, ts=ts, out=out_, whip=whip,
                             **{k: legs[k] for k in legs}, fireA=fireA, fireB=fireB))
            if fireA or fireB:
                bitmap = "".join("1" if legs["A%d" % k] is True else "0" for k in range(1, 8))
                fires.append(dict(ts=round(ts, 3), bucket_id=bid, side=d,
                                  setup=("A" if fireA else "") + ("B" if fireB else ""),
                                  legsA=bitmap, legsB="".join("1" if x else "0" for x in blegs),
                                  outcome=out_, joint=whip))
        if i % 2000 == 0:
            print("[%.0fs] i=%d" % (time.time() - t0, i), flush=True)
    ev = pd.DataFrame(rows)
    ev["slice"] = np.where(ev["ts"] <= tcut, "disc", np.where(ev["ts"] >= emb, "hold", "emb"))
    pd.DataFrame(fires).to_csv(os.path.join(OUT, "setups_S3_fires.csv"), index=False)
    ev.to_pickle(os.path.join(OUT, "_setups_S3_eval.pkl"))
    print("[%.0fs] rows=%d fires=%d -> setups_S3_fires.csv" % (time.time() - t0, len(ev), len(fires)), flush=True)
    return ev


if __name__ == "__main__":
    main()
