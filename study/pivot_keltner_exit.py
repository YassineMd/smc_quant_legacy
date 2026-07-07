"""PIVOT variable-TP test: fixed SL -0.3%, but the TP is "ride until price touches the OPPOSITE Keltner
band" instead of a flat +0.5%. As specified: LONG exits on the LOWER band (a trailing floor that rises
under the move), SHORT exits on the UPPER band (a trailing ceiling). 6h cap -> mark-to-market at the last
close. Keltner = EMA(close,20) +/- 2.25*Wilder-ATR(20) on the 1m tape (app.config + app.terminal verbatim).

Same entries as the shipped indicator (detect_pivots + the INDEPENDENT per-side walk), MKT/TOUCH only, so
this is apples-to-apples vs the fixed +0.5/-0.3 exit. Fee convention == pivot_backtest: net = mean(gross)-0.10.

Also computes the OTHER direction (long->upper / short->lower, a "reach the far band" target) as a secondary
line, in case that was the intent. Run: python study/pivot_keltner_exit.py
"""
import os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, config               # noqa: E402
from pivot_backtest import load_local_tape               # noqa: E402

H_S = 6 * 3600.0; FEE = 0.10; SL = 0.003


def keltner(highs, lows, closes, length, mult):
    """EMA(close,length) +/- mult*Wilder-ATR(length) -> (upper, lower). Verbatim app.terminal._keltner_bands."""
    n = len(closes); k = 2.0 / (length + 1)
    mid = np.empty(n); atr = np.empty(n)
    e = float(closes[0]); a = float(highs[0]) - float(lows[0]); mid[0] = e; atr[0] = a
    for i in range(1, n):
        e = closes[i] * k + e * (1.0 - k)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        a = (a * (length - 1) + tr) / length
        mid[i] = e; atr[i] = a
    return mid + mult * atr, mid - mult * atr


def main():
    bids, raws, gaps = load_local_tape()
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    kc_up, kc_lo = keltner(hi, lo, cl, config.KELTNER_LENGTH, config.KELTNER_ATR_MULT)
    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])   # moving POC baseline (5% EMA), s5j verbatim
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95

    # ---- same entries as the indicator: detect + INDEPENDENT per-side walk, MKT/TOUCH only ----
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; entries = []            # (j_e, entry_price, side)
    for f in fires:
        s = f["side"]; det = f["det_i"]; je = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (je + 1) if je is not None else f["wait_end_i"]
        if je is not None:
            entries.append((je, float(cl[je]), s))

    def walk_fixed(je, entry, long):
        sl_lvl = entry * (1 - SL) if long else entry * (1 + SL)
        tp_lvl = entry * 1.005 if long else entry * 0.995
        te = float(et[je])
        for j in range(je + 1, n):
            if st[j] > te + H_S:
                break
            tp = (hi[j] >= tp_lvl) if long else (lo[j] <= tp_lvl)
            sl = (lo[j] <= sl_lvl) if long else (hi[j] >= sl_lvl)
            if sl:
                return "SL", -0.3
            if tp:
                return "TP", 0.5
        return "UNRES", None

    def walk_trail(je, entry, long, band):
        """SL -0.3% AND the opposite band are BOTH on the losing side (below for long); the higher level is
        touched first on a pullback, so exit = max(sl, band). band above sl -> profit ('TP'); else -0.3 SL."""
        sl_lvl = entry * (1 - SL) if long else entry * (1 + SL)
        te = float(et[je]); jl = je
        for j in range(je + 1, n):
            if st[j] > te + H_S:
                break
            jl = j
            if long:
                thr = max(sl_lvl, band[j])
                if lo[j] <= thr:
                    return ("TP" if band[j] >= sl_lvl else "SL"), (thr - entry) / entry * 100.0
            else:
                thr = min(sl_lvl, band[j])
                if hi[j] >= thr:
                    return ("TP" if band[j] <= sl_lvl else "SL"), (entry - thr) / entry * 100.0
        if jl > je:                                          # 6h cap: mark-to-market at the last close
            return "CAP", ((cl[jl] - entry) / entry * 100.0) if long else ((entry - cl[jl]) / entry * 100.0)
        return "UNRES", None

    def walk_target(je, entry, long, band):
        """Opposite structure: band is the far-side TP (above for long), SL below. Two-sided, SL-first."""
        sl_lvl = entry * (1 - SL) if long else entry * (1 + SL)
        te = float(et[je]); jl = je
        for j in range(je + 1, n):
            if st[j] > te + H_S:
                break
            jl = j
            slh = (lo[j] <= sl_lvl) if long else (hi[j] >= sl_lvl)
            tph = (hi[j] >= band[j]) if long else (lo[j] <= band[j])
            if slh:
                return "SL", -0.3
            if tph:
                return "TP", ((band[j] - entry) / entry * 100.0) if long else ((entry - band[j]) / entry * 100.0)
        if jl > je:
            return "CAP", ((cl[jl] - entry) / entry * 100.0) if long else ((entry - cl[jl]) / entry * 100.0)
        return "UNRES", None

    def report(title, fn, band_long, band_short):
        print("\n== %s ==" % title)
        for side in ("long", "short", "ALL"):
            es = [(je, e, s) for (je, e, s) in entries if side in (s, "ALL")]
            grs = []; tags = {"TP": 0, "SL": 0, "CAP": 0, "UNRES": 0}
            for je, e, s in es:
                long = s == "long"
                tag, g = fn(je, e, long, band_long if long else band_short)
                tags[tag] += 1
                if g is not None:
                    grs.append(g)
            if not grs:
                print("  %-5s n=0" % side); continue
            grs = np.array(grs)
            net = grs.mean() - FEE
            win = 100.0 * np.mean(grs > FEE)                 # a trade that clears fees
            print("  %-5s n=%-3d | avg gross %+6.3f%% net %+6.3f%% | win(net>0) %4.1f%% | median %+.3f%% "
                  "| TP/band %d SL %d cap %d | maxW %+.2f maxL %+.2f"
                  % (side, len(grs), grs.mean(), net, win, float(np.median(grs)),
                     tags["TP"], tags["SL"], tags["CAP"], grs.max(), grs.min()))

    # fixed baseline (ledger convention: resolved only)
    print("tape %d bars (%d gaps) | %d taken entries (MKT/TOUCH)" % (n, len(gaps), len(entries)))
    print("\n== FIXED +0.5 / -0.3 (baseline) ==")
    for side in ("long", "short", "ALL"):
        es = [(je, e, s) for (je, e, s) in entries if side in (s, "ALL")]
        res = []; ntp = nsl = nun = 0
        for je, e, s in es:
            tag, g = walk_fixed(je, e, s == "long")
            if tag == "TP": ntp += 1; res.append(0.5)
            elif tag == "SL": nsl += 1; res.append(-0.3)
            else: nun += 1
        if res:
            net = float(np.mean(res)) - FEE
            print("  %-5s n=%-3d | TP%% %4.1f | net %+6.3f%% | %d TP %d SL %d unres"
                  % (side, len(es), 100.0 * ntp / (ntp + nsl) if (ntp + nsl) else float("nan"), net, ntp, nsl, nun))

    report("TRAILING band  (LONG->lower, SHORT->upper)  [as specified]", walk_trail, kc_lo, kc_up)
    report("TARGET band    (LONG->upper, SHORT->lower)  [other direction]", walk_target, kc_up, kc_lo)

    # ---- TWO-STAGE: arm on a band touch, then TP on the next BASELINE (moving-POC) touch -----------------
    def walk_base(je, entry, long, arm_band, arm_above):
        """SL -0.3% hard throughout. ARM when price touches arm_band (arm_above: high>=band else low<=band).
        Once armed, TP = first bar whose range straddles the baseline -> exit at base[j]. 6h cap m2m."""
        sl_lvl = entry * (1 - SL) if long else entry * (1 + SL)
        te = float(et[je]); armed = False; jl = je
        for j in range(je + 1, n):
            if st[j] > te + H_S:
                break
            jl = j
            if (lo[j] <= sl_lvl) if long else (hi[j] >= sl_lvl):
                return "SL", -0.3, armed
            if not armed:
                armed = (hi[j] >= arm_band[j]) if arm_above else (lo[j] <= arm_band[j])
            if armed and lo[j] <= base[j] <= hi[j]:          # baseline touched -> take profit at the baseline
                g = (base[j] - entry) / entry * 100.0 if long else (entry - base[j]) / entry * 100.0
                return "TP", g, True
        if jl > je:
            g = ((cl[jl] - entry) / entry * 100.0) if long else ((entry - cl[jl]) / entry * 100.0)
            return "CAP", g, armed
        return "UNRES", None, armed

    def report_base(title, cfg):        # cfg: side -> (band, arm_above)
        print("\n== %s ==" % title)
        for side in ("long", "short", "ALL"):
            es = [(je, e, s) for (je, e, s) in entries if side in (s, "ALL")]
            grs = []; narm = 0; tags = {"TP": 0, "SL": 0, "CAP": 0, "UNRES": 0}
            for je, e, s in es:
                band, above = cfg[s]
                tag, g, armed = walk_base(je, e, s == "long", band, above)
                tags[tag] += 1; narm += int(armed)
                if g is not None:
                    grs.append(g)
            if not grs:
                print("  %-5s n=0" % side); continue
            grs = np.array(grs)
            print("  %-5s n=%-3d | armed %d (%.0f%%) | avg gross %+6.3f%% net %+6.3f%% | win(net>0) %4.1f%% "
                  "| median %+.3f%% | TP %d SL %d cap %d | maxW %+.2f maxL %+.2f"
                  % (side, len(grs), narm, 100.0 * narm / len(es), grs.mean(), grs.mean() - FEE,
                     100.0 * np.mean(grs > FEE), float(np.median(grs)),
                     tags["TP"], tags["SL"], tags["CAP"], grs.max(), grs.min()))

    report_base("BASELINE-TP after OPPOSITE band  (long arms on lower, short on upper)  [literal]",
                {"long": (kc_lo, False), "short": (kc_up, True)})
    report_base("BASELINE-TP after PROFIT band    (long arms on upper, short on lower)  [momentum->mean]",
                {"long": (kc_up, True), "short": (kc_lo, False)})


if __name__ == "__main__":
    main()
