"""1h DIRECTIONAL-BIAS screen over the candle stats.

For every 1h candle with a direction (Long = close>open / Short = close<open), test whether each candle stat
predicts the NEXT candle's direction (CONTINUATION = next candle same side). Ranks stats by how well they separate
continuation from reversal (AUC = Mann-Whitney = P(stat_continue > stat_reverse)), WITHIN Long candles and WITHIN
Short candles separately. AUC 0.50 = no bias; >0.55 / <0.45 = the stat leans.

Guards against the 1h-overfit history (recon-signal-search-null / binance-recon-oos-1h-fail):
  * chronological IN-SAMPLE (first half) vs OUT-OF-SAMPLE (second half) split — a stat only "survives" if the SAME
    lean shows OOS.
  * exact-ish Mann-Whitney z p-value per test + a Bonferroni note (many stats x 2 sides).
Data = recon 1h (study/recon_archive), the only 1h set with the flow fields densely present. Recon caveats: vol_mult
(VEL) + OI empty, positioning under-attributed -> those stats are excluded; recon 1h are TIME buckets so tau is constant.

CLI: python study/candle_bias_1h.py
"""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import absorption as ABS
from app import pivot_detect as PD
from app.footprint_panel import profile_skewness
import app.config as config

NAN = float("nan")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load():
    _, rows, _ = load_archive("1h", root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:                                  # app helpers read open/close; recon carries open_price/close_price
        b["open"] = _f(b.get("open_price", 0.0)); b["close"] = _f(b.get("close_price", 0.0))
    return A


def build_stats(A):
    n = len(A)
    O = [b["open"] for b in A]; C = [b["close"] for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    BV = [_f(b.get("buy_vol")) for b in A]; SV = [_f(b.get("sell_vol")) for b in A]
    CV = [_f(b.get("curr_vol")) for b in A]
    UT = [_f(b.get("up_ticks")) for b in A]; DT = [_f(b.get("dn_ticks")) for b in A]
    BER = [_f(b.get("buyer_er")) for b in A]; SER = [_f(b.get("seller_er")) for b in A]

    # per-candle stat columns (float or nan). Signed stats are left RAW; the AUC is run within each side.
    S = {k: [NAN] * n for k in (
        "body_pct", "range_pct", "close_in_range", "upper_wick", "lower_wick", "body_frac", "mov_mag",
        "delta_pct", "da2", "absorb_R", "absorb_h1", "absorb_h2", "vw", "rB", "dP_t", "dP_1", "dP_2",
        "delta_up", "delta_dn", "halfdom_up", "halfdom_dn", "cost_tick_ratio", "vel_ratio", "tape_ratio",
        "ker_buy", "ker_sell", "skew", "mmxskew", "effagg_sp", "ber", "ser", "ber30", "ser30")}

    # trailing-30 E/R means (causal)
    for i in range(n):
        w0 = max(0, i - 30)
        if i > w0:
            S["ber30"][i] = sum(BER[w0:i]) / (i - w0)
            S["ser30"][i] = sum(SER[w0:i]) / (i - w0)

    for i in range(n):
        o, c, h, l = O[i], C[i], H[i], L[i]
        bv, sv, cv = BV[i], SV[i], SV[i] * 0 + CV[i]
        ut, dt = UT[i], DT[i]
        rng = h - l
        if o > 0:
            S["body_pct"][i] = (c - o) / o * 100.0
            S["range_pct"][i] = (rng / o * 100.0) if rng > 0 else 0.0
        if rng > 0:
            S["close_in_range"][i] = (c - l) / rng
            S["upper_wick"][i] = (h - max(o, c)) / rng
            S["lower_wick"][i] = (min(o, c) - l) / rng
            S["body_frac"][i] = abs(c - o) / rng
        ref = l if c > o else (h if c < o else o)
        if ref > 0:
            S["mov_mag"][i] = ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0
        if cv > 0:
            S["delta_pct"][i] = (bv - sv) / cv * 100.0
        dh1 = A[i].get("delta_h1")
        if dh1 is not None and cv > 0:
            dh1 = _f(dh1); dh2 = (bv - sv) - dh1
            S["da2"][i] = (dh2 - dh1) / cv * 100.0
        try:
            aR = ABS.absorption(A, i)[0]
            if aR is not None:
                S["absorb_R"][i] = aR
            a1, a2 = ABS.absorption_halves(A, i)
            if a1 is not None:
                S["absorb_h1"][i] = a1
            if a2 is not None:
                S["absorb_h2"][i] = a2
        except Exception:
            pass
        tot = ut + dt
        if tot > 0:
            S["rB"][i] = 100.0 * ut / tot
        if min(ut, dt) > 0:
            S["vw"][i] = min(999.0, (max(ut, dt) / min(ut, dt) - 1.0) * 100.0)
        try:
            ph = ABS.price_halves(A[i])
            if ph is not None:
                S["dP_1"][i], S["dP_2"][i], S["dP_t"][i] = ph
        except Exception:
            pass
        lv = A[i].get("levels") or {}
        if lv and h > 0 and l > 0 and h >= l and cv > 0:
            mid = (h + l) / 2.0; ub = us = lb = ls = 0.0
            for ps, vv in lv.items():
                try:
                    p = float(ps)
                except (TypeError, ValueError):
                    continue
                b_ = _f(vv.get("b")); s_ = _f(vv.get("s"))
                if p >= mid:
                    ub += b_; us += s_
                else:
                    lb += b_; ls += s_
            S["delta_up"][i] = (ub - us) / cv * 100.0
            S["delta_dn"][i] = (lb - ls) / cv * 100.0
            if ub + us > 0:
                S["halfdom_up"][i] = ub / (ub + us) * 100.0    # buy-share of the upper half
            if lb + ls > 0:
                S["halfdom_dn"][i] = lb / (lb + ls) * 100.0
        bpt = (bv / ut) if ut > 0 else 0.0
        spt = (sv / dt) if dt > 0 else 0.0
        if bpt > 0 and spt > 0:
            S["cost_tick_ratio"][i] = bpt / spt
        if sv > 0:
            S["vel_ratio"][i] = bv / sv                        # duration cancels -> buy/sell volume ratio
        szb = sum(A[i].get("sz_cb") or []); szs = sum(A[i].get("sz_cs") or [])
        if szs > 0:
            S["tape_ratio"][i] = szb / szs
        # KER per side (inline _bucket_ker)
        dp_t = (c - o) / config.TICK_SIZE
        vd = bv - sv
        ob = (bv / ut) if ut > 0 else 0.0; osl = (sv / dt) if dt > 0 else 0.0
        dur = max(1.0, _f(A[i].get("end_time")) - _f(A[i].get("start_time")))
        vb, vs = bv / dur, sv / dur
        Fb = max(0.0, vd) * vb; Fs = max(0.0, -vd) * vs
        Wb = max(0.0, dp_t) * ob; Ws = max(0.0, -dp_t) * osl
        S["ker_buy"][i] = (Wb / Fb) if Fb > 0 else (9999.0 if Wb > 0 else 0.0)
        S["ker_sell"][i] = (Ws / Fs) if Fs > 0 else (9999.0 if Ws > 0 else 0.0)
        sk = profile_skewness(lv)
        if sk is not None:
            S["skew"][i] = sk
            if S["mov_mag"][i] == S["mov_mag"][i]:
                S["mmxskew"][i] = S["mov_mag"][i] * sk
        S["ber"][i] = BER[i]; S["ser"][i] = SER[i]

    # eff-agg spread (causal, trailing 150) — one pass with the module
    try:
        share = PD.eff_causal_share(A)               # list aligned to A
        for i in range(n):
            if share[i] == share[i]:
                S["effagg_sp"][i] = (2.0 * float(share[i]) - 1.0) * 100.0
    except Exception:
        pass
    return S, O, C


def direction(O, C):
    return [(1 if C[i] > O[i] else (-1 if C[i] < O[i] else 0)) for i in range(len(O))]


def auc_p(cont_vals, rev_vals):
    """AUC = P(cont > rev) via Mann-Whitney U + normal-approx two-sided p (tie-corrected mean, uncorrected var)."""
    n1, n0 = len(cont_vals), len(rev_vals)
    if n1 == 0 or n0 == 0:
        return NAN, NAN, n1, n0
    allv = sorted([(v, 1) for v in cont_vals] + [(v, 0) for v in rev_vals])
    # rank with ties = average rank
    ranks = [0.0] * len(allv); i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    R1 = sum(ranks[k] for k in range(len(allv)) if allv[k][1] == 1)
    U1 = R1 - n1 * (n1 + 1) / 2.0
    auc = U1 / (n1 * n0)
    mu = n1 * n0 / 2.0
    sd = math.sqrt(n1 * n0 * (n1 + n0 + 1) / 12.0)
    z = (U1 - mu) / sd if sd > 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))          # two-sided
    return auc, p, n1, n0


def screen(A):
    S, O, C = build_stats(A)
    D = direction(O, C)
    n = len(A)
    half = n // 2
    # outcome per candle i: continuation = D[i+1]==D[i] (both nonzero). skip doji either side.
    def rows(idxs, statname, side):
        cont, rev = [], []
        for i in idxs:
            if i + 1 >= n or D[i] == 0 or D[i + 1] == 0 or D[i] != side:
                continue
            v = S[statname][i]
            if v != v:                              # nan
                continue
            (cont if D[i + 1] == D[i] else rev).append(v)
        return cont, rev

    IS = range(0, half); OOS = range(half, n)
    names = list(S.keys())
    out = []
    for st in names:
        rec = {"stat": st}
        for side, tag in ((1, "L"), (-1, "S")):
            ci, ri = rows(IS, st, side); auc_is, p_is, n1, n0 = auc_p(ci, ri)
            co, ro = rows(OOS, st, side); auc_oos, _p, m1, m0 = auc_p(co, ro)
            rec[tag] = (auc_is, p_is, n1 + n0, auc_oos, m1 + m0)
        out.append(rec)
    return out, S, D, half


def base_rates(D, half, n):
    def br(idxs):
        cL = tL = cS = tS = 0
        for i in idxs:
            if i + 1 >= n or D[i] == 0 or D[i + 1] == 0:
                continue
            if D[i] == 1:
                tL += 1; cL += (D[i + 1] == 1)
            else:
                tS += 1; cS += (D[i + 1] == -1)
        return (cL, tL, cS, tS)
    return br(range(0, half)), br(range(half, n))


def main():
    A = load()
    n = len(A)
    print("recon 1h buckets: %d" % n)
    out, S, D, half = screen(A)
    (isL, isT, isS, isST), (osL, osT, osS, osST) = base_rates(D, half, n)
    print("\nBASE continuation rate (next candle same side):")
    print("  IN-SAMPLE  Long %d/%d = %.1f%%   Short %d/%d = %.1f%%" %
          (isL, isT, 100.0 * isL / max(1, isT), isS, isST, 100.0 * isS / max(1, isST)))
    print("  OUT-SAMPLE Long %d/%d = %.1f%%   Short %d/%d = %.1f%%" %
          (osL, osT, 100.0 * osL / max(1, osT), osS, osST, 100.0 * osS / max(1, osST)))

    def keymax(rec):
        a = abs((rec["L"][0] if rec["L"][0] == rec["L"][0] else 0.5) - 0.5)
        b = abs((rec["S"][0] if rec["S"][0] == rec["S"][0] else 0.5) - 0.5)
        return max(a, b)
    out.sort(key=keymax, reverse=True)

    print("\n%-16s | %-33s | %-33s" % ("", "LONG candles", "SHORT candles"))
    print("%-16s | %s | %s" % ("stat",
          "AUC_is   p_is    n    AUC_oos  n", "AUC_is   p_is    n    AUC_oos  n"))
    print("-" * 90)
    for rec in out:
        def fmt(t):
            auc_is, p_is, nis, auc_oos, noos = t
            return "%6.3f %7.4f %5d %7.3f %5d" % (
                auc_is if auc_is == auc_is else 0.5, p_is if p_is == p_is else 1.0,
                nis, auc_oos if auc_oos == auc_oos else 0.5, noos)
        print("%-16s | %s | %s" % (rec["stat"], fmt(rec["L"]), fmt(rec["S"])))

    # SURVIVORS: |AUC_is-0.5|>=0.03, p_is<0.01, and OOS leans the SAME way with |AUC_oos-0.5|>=0.02
    print("\n=== SURVIVORS (strict: |AUC_is-0.5|>=0.03, p<0.01, same-direction OOS |AUC_oos-0.5|>=0.02) ===")
    any_surv = False
    for rec in out:
        for side, tag in (("L", "Long"), ("S", "Short")):
            auc_is, p_is, nis, auc_oos, noos = rec[side]
            if auc_is != auc_is or auc_oos != auc_oos:
                continue
            di, do = auc_is - 0.5, auc_oos - 0.5
            if abs(di) >= 0.03 and p_is < 0.01 and (di * do > 0) and abs(do) >= 0.02:
                any_surv = True
                print("  %-16s %-5s  AUC_is %.3f (p %.4f)  OOS %.3f" % (rec["stat"], tag, auc_is, p_is, auc_oos))
    if not any_surv:
        print("  (none) — no stat's directional lean is BOTH sizeable (>=0.03) and OOS-replicated.")

    # CONSISTENT LEANS (relaxed): IS p<0.05 AND OOS leans the same direction -> the weak-but-real signals
    print("\n=== CONSISTENT LEANS (in-sample p<0.05 AND OOS same direction) ===")
    leans = []
    for rec in out:
        for side, tag in (("L", "Long"), ("S", "Short")):
            auc_is, p_is, nis, auc_oos, noos = rec[side]
            if auc_is != auc_is or auc_oos != auc_oos:
                continue
            di, do = auc_is - 0.5, auc_oos - 0.5
            if p_is < 0.05 and di * do > 0:
                leans.append((abs(do), rec["stat"], tag, auc_is, p_is, auc_oos,
                              "higher->continue" if di > 0 else "lower->continue"))
    for _, st, tag, auc_is, p_is, auc_oos, lean in sorted(leans, reverse=True):
        print("  %-16s %-5s  AUC_is %.3f (p %.4f)  OOS %.3f   %s" % (st, tag, auc_is, p_is, auc_oos, lean))
    if not leans:
        print("  (none)")

    # DISJOINT-BAND continuation tables for the conviction cluster (quintiles; IS + OOS side by side)
    def bands(statname, side):
        def cont_by_band(idxs):
            pairs = []
            for i in idxs:
                if i + 1 >= n or D[i] == 0 or D[i + 1] == 0 or D[i] != side:
                    continue
                v = S[statname][i]
                if v != v:
                    continue
                pairs.append((v, 1 if D[i + 1] == D[i] else 0))
            pairs.sort()
            if len(pairs) < 25:
                return []
            q = len(pairs) // 5
            res = []
            for b in range(5):
                seg = pairs[b * q:(b + 1) * q] if b < 4 else pairs[4 * q:]
                k = sum(o for _, o in seg); m = len(seg)
                lo, hi = seg[0][0], seg[-1][0]
                res.append((lo, hi, k, m))
            return res
        bi = cont_by_band(range(0, half)); bo = cont_by_band(range(half, n))
        if not bi or not bo:
            return
        sidetag = "Long" if side == 1 else "Short"
        print("\n  %s  [%s candles]  quintile bands  (IS  |  OOS)" % (statname, sidetag))
        for (loi, hii, ki, mi), (_, _, ko, mo) in zip(bi, bo):
            print("    [%8.3f .. %8.3f]  cont %5.1f%% (n%4d)  |  %5.1f%% (n%4d)" %
                  (loi, hii, 100.0 * ki / mi, mi, 100.0 * ko / mo, mo))

    print("\n=== CONTINUATION BY BAND (the conviction cluster) ===")
    for st in ("close_in_range", "vw", "body_frac", "rB", "upper_wick"):
        bands(st, 1); bands(st, -1)


if __name__ == "__main__":
    main()
