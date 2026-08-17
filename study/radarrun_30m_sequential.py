"""30m Radar Runner @ 0.30% TP under the ONE-POSITION-AT-A-TIME rule (enter -> let it finish -> then next), vs the
take-EVERY-signal ideal. Shows this is the basis the win/DD/prop numbers already use. ALL = per-signal outcome
(overlap allowed); SEQ = walk in time, skip any signal that fires before the open trade's SL/TP exit. Then the
prop time-to-pass MC (10/10/5) under SEQ. python study/radarrun_30m_sequential.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from study.radarrun_30m_bestsetup import detect       # detect() = candle-SL 0.3% buf, returns (k,s,entry,sl,dist)

random.seed(7); H = 200; FEE = 0.0004; SLIP = 0.0003; TP = 0.003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; N = 8000; MAXD = 250


def evaluate(A, sequential):
    sigs, Hi, Lo, C, n = detect(A)
    tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if sequential and k <= last:                   # in a trade -> skip (one position at a time)
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((_f(A[k].get("start_time")), net, net / dist))
        if sequential:
            last = k + int(off)
    return tr


def day_blocks(tr):
    by = {}
    for ts, _n, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    return days


def mc(days, Rp):
    passes = 0; dtp = []
    for _ in range(N):
        eq = peak = 0.0; passed = failed = False
        for dd in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += Rp * r; dlow = min(dlow, eq); peak = max(peak, eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (dstart - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(dd)
                break
    return 100.0 * passes / N, dtp


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        A = get_buckets("30m", root)
        allt = evaluate(A, sequential=False); seqt = evaluate(A, sequential=True)
        nd = len(day_blocks(seqt))
        print("\n====  30m 0.30%%-TP  %s  (%d cal-days)  ====" % (ds, nd), flush=True)
        for lbl, tr in (("TAKE-ALL (overlap)  ", allt), ("ONE-AT-A-TIME (SEQ) ", seqt)):
            net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
            dd = ("  maxDD=%.1f%%" % maxdd_pct(rs)) if lbl.startswith("ONE") else ""
            print("  %s n=%-4d (%.1f/day)  win=%.0f%%  expR=%+.3f%s" % (
                lbl, len(tr), len(tr) / nd, 100 * (net > 0).mean(), np.mean(rs), dd), flush=True)
        days = day_blocks(seqt)
        print("  prop pass / median-days (SEQ, target10/maxDD10/daily5):", flush=True)
        for Rp in (0.5, 0.75, 1.0):
            p, dtp = mc(days, Rp); q = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
            print("     R%.2f%%  pass=%3.0f%%  days = %d / %d / %d" % (Rp, p, q[0], q[1], q[2]), flush=True)


if __name__ == "__main__":
    main()
