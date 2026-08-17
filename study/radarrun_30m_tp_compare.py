"""30m Radar Runner: 0.30% vs 0.40% TP, one-at-a-time, full prop summary. Per (TP, recon/daemon): win%, realized DD,
and day-block MC to a 10/10/5 challenge -> pass% + days-to-pass + the max DD hit ON THE WAY to passing (the drawdown
you actually experience), at R0.5 / R0.75. python study/radarrun_30m_tp_compare.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from study.radarrun_30m_bestsetup import detect

random.seed(7); H = 200; FEE = 0.0004; SLIP = 0.0003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; N = 8000; MAXD = 250


def eval_seq(sigs, Hi, Lo, C, n, tp):
    tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((_f(get_st(sigs, k)), net, net / dist)); last = k + int(off)
    return tr


def get_st(sigs, k):
    return k          # placeholder (we day-block by trade index groups instead of real dates below)


def day_blocks(tr, A):
    # group by UTC day using the bucket start_time carried in tr as ts
    by = {}
    for ts, _n, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    return days


def mc(days, Rp):
    passes = 0; dtp = []; dds = []
    for _ in range(N):
        eq = peak = pdd = 0.0; passed = failed = False
        for day_i in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += Rp * r; dlow = min(dlow, eq); peak = max(peak, eq); pdd = max(pdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (dstart - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(day_i); dds.append(pdd)
                break
    return 100.0 * passes / N, dtp, dds


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        A = get_buckets("30m", root)
        sigs, Hi, Lo, C, n = detect(A)
        ST = {k: _f(A[k].get("start_time")) for (k, *_ ) in sigs}
        print("\n################  30m  %s  ################" % ds, flush=True)
        for tp in (0.002, 0.003, 0.004):
            tr = []; last = -1
            for (k, s, entry, sl, dist) in sigs:
                if k <= last:
                    continue
                j0 = k + 1; j1 = min(n, k + 1 + H)
                outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
                net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
                tr.append((ST[k], net, net / dist)); last = k + int(off)
            net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
            days = day_blocks(tr, A)
            print("  --- TP %.1f%%:  n=%d  win=%.0f%%  realized-DD=%.1f%% ---" % (
                tp * 100, len(tr), 100 * (net > 0).mean(), maxdd_pct(rs)), flush=True)
            for Rp in (0.5, 0.75):
                p, dtp, dd = mc(days, Rp)
                dq = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
                ddq = np.percentile(dd, [50, 95]) if dd else [0, 0]
                print("     R%.2f%%  pass=%3.0f%%  days=%d/%d/%d  DD-to-target med/p95=%.1f%%/%.1f%%" % (
                    Rp, p, dq[0], dq[1], dq[2], ddq[0], ddq[1]), flush=True)


if __name__ == "__main__":
    main()
