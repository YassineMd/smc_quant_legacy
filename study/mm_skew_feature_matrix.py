"""MMXSKEW — comprehensive CAUSAL feature matrix + uniform W/L-separation ranking.

For EVERY v1.1 MMXSKEW signal (long = bull&skew>0&spread>=+35&close>POC-base&delta<15 ; short = mirror), emit one
row with ~40 CAUSAL features (order-flow, candle shape, trailing context, prior-day value area, volatility, time,
sequence, VPIN) + labels win@1.0 / win@1.5 / net / MAE / MFE. All features use ONLY data up to & including the
signal bar (no forward peek). Direction-sensitive features are multiplied by side (`_dir` = "agrees with the trade").

Then rank every numeric feature by OOS-ROBUST separation: point-biserial r vs win, and — the bar nothing has cleared —
whether the median-split hi-vs-lo win gap keeps the SAME SIGN in BOTH time-halves AND at BOTH RRs. Dumps the matrix to
CSV (path from argv[1], default scratchpad) for downstream mining.

Run:  python study/mm_skew_feature_matrix.py  [out.csv]
"""
from __future__ import annotations
import os, sys, math, csv, statistics, datetime as dt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app import pivot_detect as PD, region_state as R, config
from app.footprint_panel import profile_skewness
import study.mm_skew_rr_sweep as RR

LW = config.LIVE_PANEL_WINDOW; THR = 35.0; W = 15   # trailing-context window (buckets)


def va_poc(prof, pct=0.70):
    items = sorted((p, w) for p, w in (prof or {}).items() if w > 0)
    if len(items) < 3:
        return None
    total = sum(w for _, w in items); tgt = pct * total
    pi = max(range(len(items)), key=lambda k: items[k][1]); lo = hi = pi; va = items[pi][1]
    while va < tgt and (lo > 0 or hi < len(items) - 1):
        up = items[hi + 1][1] if hi < len(items) - 1 else -1
        dn = items[lo - 1][1] if lo > 0 else -1
        if up >= dn:
            hi += 1; va += items[hi][1]
        else:
            lo -= 1; va += items[lo][1]
    poc = items[pi][0]; plo, phi = items[0][0], items[-1][0]; mid = (plo + phi) / 2
    up = sum(w for p, w in items if p > poc); dn = sum(w for p, w in items if p < poc)
    pc = 1 - 2 * abs(poc - mid) / (phi - plo) if phi > plo else 0
    sym = min(up, dn) / max(up, dn) if max(up, dn) > 0 else 0
    return dict(val=items[lo][0], vah=items[hi][0], D=pc * sym)


def build():
    _, raws, _ = load_archive("1h")
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r); d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["open"] = o; d["close"] = c
        d["tv"] = r.get("target_vol", 0.0) or 0.0; d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o; d["poc"] = float(r.get("poc_price", 0.0) or 0.0)
        cv = float(r.get("curr_vol", 0.0)) or 1.0
        d["delta"] = (float(r.get("buy_vol", 0.0)) - float(r.get("sell_vol", 0.0))) / cv * 100.0
        A.append(d)
    n = len(A)
    # panels 1/2/3/4 (causal first-print, exactly like the frozen spread)
    ab, ar, sval = R.absorption_series(A, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(A, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    a_sh = PD._causal_share(ab, ar, LW); e_sh = PD._causal_share(eb, er_, LW)
    rb = [s.get("buyer_er", 0.0) for s in A]; rs = [s.get("seller_er", 0.0) for s in A]
    r_sh = PD._causal_share(rb, rs, LW)
    ex = R.trailing_exhaustion(A, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    x4 = np.zeros(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        x4[k] = hold
    p1 = (2 * np.asarray(a_sh, float) - 1) * 100; p2 = (2 * np.asarray(e_sh, float) - 1) * 100
    p3 = (2 * np.asarray(r_sh, float) - 1) * 100
    # POC baseline (5% EMA), daily VWAP, ema9/20/50
    poc = [a["poc"] for a in A]; base = [0.0] * n
    base[0] = poc[0] if poc[0] > 0 else next((p for p in poc if p > 0), 0.0)
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95 if poc[k] > 0 else base[k - 1]
    cum_pv = cum_v = 0.0; cur = None
    for k, b in enumerate(A):
        day = dt.datetime.utcfromtimestamp(float(b.get("start_time", 0.0))).date()
        if day != cur:
            cum_pv = cum_v = 0.0; cur = day
        tp = (b["h"] + b["l"] + b["c"]) / 3.0; v = float(b.get("curr_vol", 0.0)) or 0.0
        cum_pv += tp * v; cum_v += v
        b["vwap"] = cum_pv / cum_v if cum_v > 0 else b["c"]
    for N, key in ((9, "ema9"), (20, "ema20"), (50, "ema50")):
        a_ = 2.0 / (N + 1); prev = None
        for b in A:
            prev = b["c"] if prev is None else b["c"] * a_ + prev * (1 - a_)
            b[key] = prev
    # per-UTC-day full-profile VA (for prior-day reference) + causal rolling VPIN (50-bucket)
    days = {}
    for i, b in enumerate(A):
        days.setdefault(dt.datetime.utcfromtimestamp(float(b["start_time"])).date(), []).append(b)
    dayfull = {}
    for day, bs in days.items():
        prof = {}
        for b in bs:
            for pr, v in (b.get("levels") or {}).items():
                prof[float(pr)] = prof.get(float(pr), 0.0) + float(v.get("b", 0)) + float(v.get("s", 0))
        dayfull[day] = va_poc(prof)
    daylist = sorted(days)
    vpin = np.zeros(n)
    for i in range(n):
        w0 = A[max(0, i - 49):i + 1]
        ti = sum(abs(x.get("buy_vol", 0.0) - x.get("sell_vol", 0.0)) for x in w0)
        tv = sum(x.get("curr_vol", 0.0) for x in w0)
        vpin[i] = ti / tv if tv > 0 else 0.0
    for i in range(n):
        A[i]["p1"] = float(p1[i]); A[i]["spread"] = float(p2[i]); A[i]["p3"] = float(p3[i])
        A[i]["p4"] = float(x4[i]); A[i]["base"] = base[i]; A[i]["vpin"] = float(vpin[i])
    first = next((i for i in range(n) if A[i]["tv"] >= 100000.0
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, n))]) >= 100000.0), n)
    return A, first, daylist, dayfull


def sig(b):
    if b["sk"] is None:
        return 0
    if b["up"] and b["sk"] > 0 and b["spread"] >= THR and b["c"] > b["base"] and b["delta"] < 15:
        return 1
    if b["dn"] and b["sk"] < 0 and b["spread"] <= -THR and b["c"] < b["base"]:
        return -1
    return 0


def mae_mfe(A, i, s, exit_j):
    e = A[i]["c"]; adv = fav = 0.0
    for j in range(i + 1, min(exit_j + 1, len(A))):
        fav = max(fav, s * (A[j]["h"] - e) / e * 100.0, s * (A[j]["l"] - e) / e * 100.0)
        adv = min(adv, s * (A[j]["h"] - e) / e * 100.0, s * (A[j]["l"] - e) / e * 100.0)
    return adv, fav


def features(A, i, s, daylist, dayfull):
    b = A[i]; o, c, h, l = b["o"], b["c"], b["h"], b["l"]; e = c
    win = A[max(0, i - W):i]
    f = {}
    # location / momentum (directionalised)
    for key, ref in (("base", b["base"]), ("vwap", b["vwap"]), ("ema9", b["ema9"]),
                     ("ema20", b["ema20"]), ("ema50", b["ema50"])):
        f["dist_%s_dir" % key] = s * (c - ref) / ref * 100.0 if ref else 0.0
    # prior-day value area
    day = dt.datetime.utcfromtimestamp(float(b["start_time"])).date(); di = daylist.index(day)
    pva = dayfull[daylist[di - 1]] if di > 0 else None
    if pva:
        f["dist_prevVAH_dir"] = s * (c - pva["vah"]) / pva["vah"] * 100.0
        f["dist_prevVAL_dir"] = s * (c - pva["val"]) / pva["val"] * 100.0
        f["chase_outside_prevVA"] = 1.0 if ((s > 0 and c > pva["vah"]) or (s < 0 and c < pva["val"])) else 0.0
        f["prevday_Dscore"] = pva["D"]
    else:
        f["dist_prevVAH_dir"] = f["dist_prevVAL_dir"] = f["chase_outside_prevVA"] = f["prevday_Dscore"] = float("nan")
    # candle shape
    rng = (h - l) or 1e-9
    f["body_dir"] = s * (c - o) / o * 100.0
    f["range_pct"] = rng / o * 100.0
    f["body_frac"] = abs(c - o) / rng
    f["close_pos_dir"] = ((c - l) / rng) if s > 0 else (1 - (c - l) / rng)   # toward the trade extreme
    f["upper_wick"] = (h - max(o, c)) / o * 100.0
    f["lower_wick"] = (min(o, c) - l) / o * 100.0
    if c > o:
        ref = l
    elif c < o:
        ref = h
    else:
        ref = o
    f["mov_mag"] = ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else float("nan")
    # order flow (directional + magnitudes)
    f["skew_dir"] = s * (b["sk"] or 0.0); f["skew_abs"] = abs(b["sk"] or 0.0)
    f["spread2_dir"] = s * b["spread"]; f["spread2_abs"] = abs(b["spread"])
    f["p1_absorp_dir"] = s * b["p1"]; f["p3_er_dir"] = s * b["p3"]; f["p4_exh_dir"] = s * b["p4"]
    f["delta_dir"] = s * b["delta"]; f["delta_abs"] = abs(b["delta"])
    f["er_diff_dir"] = s * (b.get("buyer_er", 0.0) - b.get("seller_er", 0.0))
    f["churn"] = float(b.get("churn", 0.0) or 0.0)
    f["vel_ratio"] = float(b.get("vel_ratio", 0.0) or 0.0)
    _liq = b.get("liquidations", 0.0)
    f["liq"] = float(len(_liq)) if isinstance(_liq, (list, tuple)) else float(_liq or 0.0)
    oi = (b.get("opL", 0.0) + b.get("opS", 0.0)) - (b.get("clL", 0.0) + b.get("clS", 0.0))
    f["oi_dir"] = s * oi
    f["vol_vs_target"] = float(b.get("curr_vol", 0.0)) / (b["tv"] or 1.0)
    f["vpin"] = b["vpin"]
    # trailing context (causal, window W)
    if win:
        rets = [(w["c"] - w["o"]) / w["o"] * 100.0 for w in win if w["o"]]
        f["trail_ret_dir"] = s * sum(rets)
        f["trail_vol"] = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        f["trail_range"] = statistics.mean([(w["h"] - w["l"]) / w["o"] * 100.0 for w in win if w["o"]])
        cvs = [float(w.get("curr_vol", 0.0)) for w in win]; mcv = statistics.mean(cvs) if cvs else 1.0
        f["vol_regime"] = float(b.get("curr_vol", 0.0)) / (mcv or 1.0)
        hi = max(w["h"] for w in win); lo = min(w["l"] for w in win); span = (hi - lo) or 1e-9
        f["pos_recent_range_dir"] = ((c - lo) / span) if s > 0 else (1 - (c - lo) / span)
        f["spread_traj_dir"] = s * (b["spread"] - A[i - W]["spread"])
    else:
        for k in ("trail_ret_dir", "trail_vol", "trail_range", "vol_regime", "pos_recent_range_dir", "spread_traj_dir"):
            f[k] = float("nan")
    # time
    tt = dt.datetime.utcfromtimestamp(float(b["start_time"]))
    f["utc_hour"] = tt.hour; f["dow"] = tt.weekday()
    f["session"] = 0 if tt.hour < 7 else (1 if tt.hour < 13 else 2)   # Asia/EU/US (UTC bins)
    f["is_long"] = 1.0 if s > 0 else 0.0
    return f


def main():
    out_csv = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "."), "mmx_features.csv")
    A, first, daylist, dayfull = build()
    rows = []
    run = 0; prev_side = 0; last_op = None
    sig_idx = [i for i in range(first, len(A) - 1) if sig(A[i]) != 0]
    for i in sig_idx:
        s = sig(A[i])
        run = run + 1 if s == prev_side else 1
        f = features(A, i, s, daylist, dayfull)
        f["run_pos"] = run
        f["bars_since_opp"] = (i - last_op) if last_op is not None else 999
        r10 = RR.simulate_rr(A, i, s, 1.0, "sl"); r15 = RR.simulate_rr(A, i, s, 1.5, "sl")
        if r10 is None or r15 is None:
            prev_side = s
            continue
        adv, fav = mae_mfe(A, i, s, r10[2])
        f["win10"] = 1 if r10[0] == "TP" else 0; f["win15"] = 1 if r15[0] == "TP" else 0
        f["net10"] = r10[1] * 100.0; f["net15"] = r15[1] * 100.0; f["mae"] = adv; f["mfe"] = fav
        f["t"] = float(A[i].get("start_time", 0.0)); f["side"] = s
        rows.append(f)
        prev_side = s; last_op = i  # (opp tracked loosely; refined below not needed for ranking)

    cols = [k for k in rows[0].keys()]
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols); wr.writeheader(); wr.writerows(rows)
    print("wrote %d signals x %d cols -> %s" % (len(rows), len(cols), out_csv))
    print("  base win10 %.1f%%  win15 %.1f%%  (long %d / short %d)\n"
          % (100 * np.mean([r["win10"] for r in rows]), 100 * np.mean([r["win15"] for r in rows]),
             sum(1 for r in rows if r["side"] > 0), sum(1 for r in rows if r["side"] < 0)))

    # ---- uniform ranking: point-biserial + split-half + both-RR sign consistency ----
    feat_cols = [c for c in cols if c not in ("win10", "win15", "net10", "net15", "mae", "mfe", "t", "side")]

    def pbis(xs, ys):
        xs = [x for x, y in zip(xs, ys) if not (isinstance(x, float) and math.isnan(x))]
        ys = [y for x, y in zip([r for r in xs], ys)]  # placeholder (recomputed below)
        return None

    def rank_for(label):
        res = []
        ys_all = [r[label] for r in rows]
        for c in feat_cols:
            pairs = [(r[c], r[label], r["t"]) for r in rows if not (isinstance(r[c], float) and math.isnan(r[c]))]
            if len(pairs) < 20:
                continue
            xs = np.array([p[0] for p in pairs], float); ys = np.array([p[1] for p in pairs], float)
            if xs.std() == 0 or ys.std() == 0:
                continue
            r_pb = float(np.corrcoef(xs, ys)[0, 1])
            t = r_pb * math.sqrt((len(xs) - 2) / max(1e-9, 1 - r_pb ** 2))
            p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
            # split-half by time: hi-vs-lo median-split win-gap sign in each half
            order = sorted(pairs, key=lambda z: z[2]); mid = len(order) // 2
            signs = []
            for seg in (order[:mid], order[mid:]):
                xv = np.array([z[0] for z in seg]); yv = np.array([z[1] for z in seg]); m = np.median(xv)
                hi = yv[xv > m]; lo = yv[xv <= m]
                if len(hi) and len(lo):
                    signs.append(np.sign(hi.mean() - lo.mean()))
                else:
                    signs.append(0)
            consistent = (signs[0] != 0 and signs[0] == signs[1])
            res.append((c, r_pb, p, consistent, len(xs)))
        return res

    r10 = {c: (rp, p, cons) for c, rp, p, cons, nn in rank_for("win10")}
    r15 = {c: (rp, p, cons) for c, rp, p, cons, nn in rank_for("win15")}
    both = []
    for c in feat_cols:
        if c in r10 and c in r15:
            rp10, p10, cons10 = r10[c]; rp15, p15, cons15 = r15[c]
            same_dir = (np.sign(rp10) == np.sign(rp15)) and rp10 != 0
            score = abs(rp10) + abs(rp15) + (0.5 if (cons10 and cons15 and same_dir) else 0)
            both.append((c, rp10, p10, cons10, rp15, p15, cons15, same_dir, score))
    both.sort(key=lambda z: -z[8])
    print("=" * 104)
    print("FEATURE RANKING — point-biserial vs win, both RRs.  ROBUST = split-half sign-consistent at BOTH RR + same dir")
    print("=" * 104)
    print("  %-22s | %8s %6s %4s | %8s %6s %4s | %s" %
          ("feature", "r@1.0", "p", "cons", "r@1.5", "p", "cons", "ROBUST?"))
    for c, rp10, p10, cons10, rp15, p15, cons15, sd, sc in both:
        robust = "** ROBUST **" if (cons10 and cons15 and sd) else ""
        print("  %-22s | %+8.3f %6.3f %4s | %+8.3f %6.3f %4s | %s" %
              (c, rp10, p10, "Y" if cons10 else "-", rp15, p15, "Y" if cons15 else "-", robust))


if __name__ == "__main__":
    main()
