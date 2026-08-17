"""Time to pass a prop-firm challenge on the 30m Radar Runner at the 0.30% TP setup. Day-block bootstrap MC: resample
whole CALENDAR days (crypto trades daily, quiet days included), build the equity at risk R%/trade, stop on PASS
(+target%) / FAIL (max total DD or a daily-loss breach). Template: target 10% / max DD 10% / daily loss 5% (2-step
step-1 style). Reports pass-rate + days-to-pass (p25/median/p75 among passers) at R=0.5/0.75/1%, recon + daemon.
python study/radarrun_30m_prop_time.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim
from study.radarrun_30m_bestsetup import detect

random.seed(7); H = 200; FEE = 0.0004; SLIP = 0.0003; TP = 0.003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; N = 8000; MAXD = 250


def trades_030(A):
    sigs, Hi, Lo, C, n = detect(A); tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((_f(A[k].get("start_time")), net / dist)); last = k + int(off)
    return tr


def day_blocks(tr):
    by = {}
    for ts, r in tr:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        by.setdefault(d, []).append(r)
    d0, d1 = min(by), max(by)
    days = []
    d = d0
    while d <= d1:
        days.append(by.get(d, []))          # empty list = a quiet (no-signal) calendar day
        d += timedelta(days=1)
    return days


def mc(days, Rp):
    passes = 0; dtp = []
    for _ in range(N):
        eq = peak = 0.0; passed = failed = False
        for dd in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ds = eq; dlow = eq
            for r in day:
                eq += Rp * r; dlow = min(dlow, eq); peak = max(peak, eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (ds - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(dd)
                break
    return 100.0 * passes / N, dtp


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        tr = trades_030(get_buckets("30m", root)); days = day_blocks(tr)
        spd = len(tr) / max(1, len(days))
        print("\n====  30m 0.30%%-TP  %s   (%d trades, %d cal-days, %.1f/day)  ====" % (ds, len(tr), len(days), spd), flush=True)
        print("  template: +%.0f%% target / %.0f%% maxDD / %.0f%% daily-loss" % (TARGET, MAXDD, DAILY), flush=True)
        print("  %-6s %8s %10s" % ("risk", "pass%", "days-to-pass p25/med/p75"), flush=True)
        for Rp in (0.5, 0.75, 1.0):
            p, dtp = mc(days, Rp)
            if dtp:
                q = np.percentile(dtp, [25, 50, 75])
                print("  R %.2f%%  %7.0f%%   %d / %d / %d" % (Rp, p, q[0], q[1], q[2]), flush=True)
            else:
                print("  R %.2f%%  %7.0f%%   (no passes)" % (Rp, p), flush=True)


if __name__ == "__main__":
    main()
