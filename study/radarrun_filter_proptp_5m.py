"""Winner/loser separation on the ACTUAL prop exit -- fixed 0.2% TP + candle-capped SL -- not the band-tier scheme.

Exit (from study/radarrun_30m_bestsetup + radarrun_1h_setup_prop): entry = breakout close; SL = max(candleLow*(1-buf),
rlo) long / min(candleHigh*(1+buf), rhi) short (candle-capped, never wider than the radar extreme); TP = entry*(1+/-TP).
Fee 0.04% RT; SL/timeout slip 0.03%; TP is a limit (no slip). Then every Stats-Box feature AT the breakout bar is tested
for winner/loser separation -- AUC + top/bottom tercile NET-RETURN -- per year. A real filter beats the bottom tercile's
net return, same sign both years. Usage: python study/radarrun_filter_proptp_5m.py [TP_pct] [SLBUF_pct]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.radarrun_filter_5m_clock import detect_events, features, helper_at, auc
from app import config

FEE = 0.0004; SLIP = 0.0003; H = 200
DIR_SENS = {"st_delta  delta%", "st_oi     oiΔ%", "st_openpos net_open%", "st_deltaud vertical",
            "st_movmag mov_signed", "st_mmxskew mmxskew", "st_cvd    cvd_net%", "st_ohlc   body"}


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "5m"                       # timeframe (default 5m)
    TP = float(sys.argv[2]) / 100.0 if len(sys.argv) > 2 else 0.002       # default 0.2%
    SLBUF = float(sys.argv[3]) / 100.0 if len(sys.argv) > 3 else 0.003
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: float(b.get("start_time", 0) or 0))
    n = len(A)
    O = np.array([float(b.get("open", 0)) for b in A]); C = np.array([float(b.get("close", 0)) for b in A])
    Hi = np.array([float(b.get("high", 0)) for b in A]); Lo = np.array([float(b.get("low", 0)) for b in A])
    yr = np.array([datetime.fromtimestamp(float(b.get("start_time", 0)), tz=timezone.utc).year for b in A])
    ev = detect_events(A, O, C, Hi, Lo)
    Fdir, _ = features(A)

    wins, rets, years = [], [], []
    feat = {name: [] for name in Fdir}
    hnames = ["st_absorb absorpR", "st_reward rew_share", "st_strength str_zdiff",
              "st_absorpvol av_net", "st_effagg ea_net", "st_effaggsp ea_spread"]
    for name in hnames:
        feat[name] = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi, band = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        tp = entry * (1 + s * TP)
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]
        lab = ret = None
        for off in range(len(ph)):
            hi = ph[off]; lo = pl[off]
            if (lo <= sl) if s > 0 else (hi >= sl):
                lab = 0; ret = s * (sl - entry) / entry - FEE - SLIP; break     # SL first (loss)
            if (hi >= tp) if s > 0 else (lo <= tp):
                lab = 1; ret = s * (tp - entry) / entry - FEE; break            # TP first (win)
        if lab is None:
            continue
        wins.append(lab); rets.append(ret); years.append(int(yr[k]))
        for name, x in Fdir.items():
            v = x[k]
            feat[name].append(v * s if name in DIR_SENS else v)
        for name, v in helper_at(A, k).items():
            feat[name].append(v)
    wins = np.array(wins); rets = np.array(rets); years = np.array(years)
    print("%s clock RR, PROP exit: TP=%.2f%% + candle-SL(buf %.2f%%, cap radar). %d trades (2025=%d, 2026=%d)\n"
          % (tf, TP * 100, SLBUF * 100, len(wins), (years == 2025).sum(), (years == 2026).sum()), flush=True)
    for Y in (2025, 2026):
        m = years == Y
        print("  baseline %d: win %.1f%%   avg net-return %+.4f%%  (n=%d)"
              % (Y, 100 * wins[m].mean(), 100 * rets[m].mean(), m.sum()), flush=True)
    print("\n  KEY = avg NET-RETURN per tercile (both years). direction-sensitive features sign-flipped by trade side.\n")
    print("  %-26s | AUC25/26 | ret%% botT/topT 25 | ret%% botT/topT 26 | EXPECTANCY FILTER?" % "feature @ breakout")
    print("  " + "-" * 96)
    rows = []
    for name in feat:
        x = np.array(feat[name], dtype=np.float64)
        a25 = auc(x[years == 2025], wins[years == 2025]); a26 = auc(x[years == 2026], wins[years == 2026])
        if not (np.isfinite(a25) and np.isfinite(a26)):
            continue

        def terc(Y):
            m = (years == Y) & np.isfinite(x)
            xv = x[m]; rv = rets[m]
            if len(xv) < 60:
                return (np.nan, np.nan)
            q = np.quantile(xv, [1 / 3, 2 / 3])
            return (100 * rv[xv <= q[0]].mean(), 100 * rv[xv >= q[1]].mean())
        rb25, rt25 = terc(2025); rb26, rt26 = terc(2026)
        d25 = rt25 - rb25; d26 = rt26 - rb26
        robust = np.isfinite(d25) and np.isfinite(d26) and np.sign(d25) == np.sign(d26) \
            and min(abs(d25), abs(d26)) >= 0.02 and rt25 > 0 and rt26 > 0
        rows.append((name, a25, a26, rb25, rt25, rb26, rt26, robust))
    rows.sort(key=lambda r: -((r[4] - r[3]) + (r[6] - r[5])) / 2.0)
    for name, a25, a26, rb25, rt25, rb26, rt26, robust in rows:
        print("  %-26s | %.2f/%.2f | %+6.3f /%+6.3f  | %+6.3f /%+6.3f  | %s"
              % (name, a25, a26, rb25, rt25, rb26, rt26, "** FILTER" if robust else ""), flush=True)
    good = [r[0] for r in rows if r[7]]
    print("\n  ROBUST expectancy filter (top-tercile net-ret > bottom, same sign both years, >=0.02%%, top>0): %s"
          % (", ".join(good) if good else "NONE"))


if __name__ == "__main__":
    main()
