"""1h — does the swing VA-zone reverse price ONCE YOU CONDITION ON THE 2-DAY TREND?

Trend context (causal): for the touch's day D, look at the last 2 completed days D-1, D-2 — how price traded (daily
close direction) AND the daily volume-profile POC shift. UP = close rising AND POC rising; DOWN = both falling; else
NEUTRAL. In an UPTREND the zone should act as SUPPORT (price pulls back DOWN to it and bounces UP); in a DOWNTREND as
RESISTANCE (pulls back UP, reverses DOWN). We test those TREND-ALIGNED pullbacks and compare to a placebo kept INSIDE
the same trend+approach bucket (so trend drift is controlled, only the zone's contribution is isolated).

reversal = symmetric first-passage from the band mid (+/-D, K bars). EDGE = real - placebo (pp). Recon 1h.

CLI: python study/swing_va_trend_1h.py
"""
import os, sys, random, datetime as dt
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.swing_va_reversal import load_tf, first_touch, resolve, binom_p
from app import swing_lvn_detect as SL

random.seed(707)
MAXSCAN = 250
K = 48


def daily_trend(A):
    """bar_trend[i] in {1,-1,0} from the 2 completed UTC days before bar i's day (close direction AND POC shift)."""
    n = len(A)
    dates = [dt.datetime.utcfromtimestamp(float(b.get("start_time", 0) or 0)).strftime("%Y-%m-%d") for b in A]
    day_list = []; day_idx = {}
    for d in dates:
        if d not in day_idx:
            day_idx[d] = len(day_list); day_list.append(d)
    bar_day = [day_idx[d] for d in dates]
    nd = len(day_list)
    day_lv = [dict() for _ in range(nd)]; day_close = [0.0] * nd
    for i, b in enumerate(A):
        di = bar_day[i]
        for ps, vv in (b.get("levels") or {}).items():
            try:
                p = float(ps)
            except (TypeError, ValueError):
                continue
            cell = day_lv[di].get(p)
            if cell is None:
                cell = [0.0]; day_lv[di][p] = cell
            cell[0] += float(vv.get("b", 0) or 0) + float(vv.get("s", 0) or 0)
        day_close[di] = b["close"]
    day_poc = [0.0] * nd
    for di in range(nd):
        if day_lv[di]:
            day_poc[di] = max(day_lv[di].items(), key=lambda kv: kv[1][0])[0]
    day_trend = [0] * nd
    for D in range(2, nd):
        ret2 = day_close[D - 1] - day_close[D - 2]
        pocs = day_poc[D - 1] - day_poc[D - 2]
        if ret2 > 0 and pocs > 0:
            day_trend[D] = 1
        elif ret2 < 0 and pocs < 0:
            day_trend[D] = -1
    return [day_trend[bar_day[i]] for i in range(n)]


def build_bands(A):
    r = SL._dev_leg(A); H, L, C, thr, piv, dev = r
    out = []
    for k in range(1, len(piv)):
        b0 = int(piv[k - 1][0]); b1 = int(piv[k][0]); cb = int(piv[k][3])
        if b1 <= b0:
            continue
        try:
            va = SL.va_lines(A, b0, b1)
        except Exception:
            va = None
        if not va:
            continue
        lv = [va[x] for x in ("buy_poc", "sell_poc", "lvn") if va.get(x) is not None]
        if len(lv) < 2:
            continue
        lo, hi = min(lv), max(lv)
        if hi <= lo:
            hi = lo * (1.0 + 1e-4)
        out.append((lo, hi, cb))
    return out, thr


def evaluate(A, bands, btr, D, bucket, nplac=6):
    """bucket(trend, from_above) -> True to include. Returns real vs placebo reversal in that bucket."""
    H = [float(b.get("high", 0.0) or 0.0) for b in A]
    L = [float(b.get("low", 0.0) or 0.0) for b in A]
    C = [b["close"] for b in A]

    def test(shift):
        rev = brk = 0
        for (zlo, zhi, cb) in bands:
            zl, zh = zlo * (1.0 + shift), zhi * (1.0 + shift)
            ft = first_touch(H, L, C, cb, zl, zh, MAXSCAN)
            if ft is None:
                continue
            t, fa = ft
            if not bucket(btr[t], fa):
                continue
            v = resolve(H, L, t, zl, zh, fa, D, K)
            if v is None:
                continue
            if v == "rev":
                rev += 1
            else:
                brk += 1
        return rev, brk

    rr, rb = test(0.0); rres = rr + rb
    prev = pres = 0
    for _ in range(nplac):
        s = random.uniform(0.01, 0.03) * random.choice((-1, 1))
        pr, pb = test(s); prev += pr; pres += pr + pb
    prate = (prev / pres) if pres else 0.0
    real = (rr / rres) if rres else 0.0
    p = binom_p(rr, rres, prate) if rres else 1.0
    return dict(n=rres, real=real, plac=prate, edge=(real - prate) * 100.0, p=p)


def row(name, res):
    verdict = "<-- BEATS placebo" if (res["edge"] >= 3 and res["p"] < 0.05) else ""
    print("  %-34s n=%-4d  reversal %5.1f%%  placebo %5.1f%%  edge %+5.1f pp  p=%.3f %s"
          % (name, res["n"], 100 * res["real"], 100 * res["plac"], res["edge"], res["p"], verdict))


def main():
    A = load_tf("1h")
    btr = daily_trend(A)
    bands, thr = build_bands(A)
    up = sum(1 for x in btr if x == 1); dn = sum(1 for x in btr if x == -1); nu = len(btr) - up - dn
    print("1h recon: %d candles | zones=%d | trend bars: up %.0f%% / down %.0f%% / neutral %.0f%% | K=%d"
          % (len(A), len(bands), 100 * up / len(btr), 100 * dn / len(btr), 100 * nu / len(btr), K))
    for D in (0.01, 0.015):
        print("\n=== D = %.1f%% ===" % (D * 100))
        # TREND-ALIGNED pullbacks (the hypothesis): uptrend support-from-above / downtrend resistance-from-below
        row("UPtrend support (pull down, bounce up)", evaluate(A, bands, btr, D, lambda tr, fa: tr == 1 and fa))
        row("DOWNtrend resist (pull up, reverse dn)", evaluate(A, bands, btr, D, lambda tr, fa: tr == -1 and not fa))
        row("  both trend-aligned pullbacks", evaluate(A, bands, btr, D,
                                                       lambda tr, fa: (tr == 1 and fa) or (tr == -1 and not fa)))
        # counter-trend touches (price pushing INTO the zone with the trend -> expect break, not reverse)
        row("counter: UPtrend from below", evaluate(A, bands, btr, D, lambda tr, fa: tr == 1 and not fa))
        row("counter: DOWNtrend from above", evaluate(A, bands, btr, D, lambda tr, fa: tr == -1 and fa))
        # neutral / no-trend context
        row("NEUTRAL trend (any approach)", evaluate(A, bands, btr, D, lambda tr, fa: tr == 0))


if __name__ == "__main__":
    main()
