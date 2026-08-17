"""1h Radar Runner: highest win rate at minimum DD (TP sweep) + prop-firm time-to-pass MC. 1h candle-SL = 0.2% buffer.
Same method as the 30m study. Sweep TP 0.3-0.5% (win%/DD recon+daemon), then day-block MC (target 10 / maxDD 10 /
daily 5) at the best TP + the shipped 0.5%, across R=0.5/0.75/1%. python study/radarrun_1h_setup_prop.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.002
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; N = 8000; MAXD = 400


def detect(A):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000
    sigs = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist > 0:
            sigs.append((k, s, entry, sl, dist, float(ST[k])))
    return sigs, Hi, Lo, C, n


def eval_tp(sigs, Hi, Lo, C, n, tp):
    tr = []; last = -1
    for (k, s, entry, sl, dist, ts) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((ts, net, net / dist)); last = k + int(off)
    return tr


def day_blocks(tr):
    by = {}
    for ts, _net, r in tr:
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
    data = {}
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        data[ds] = detect(get_buckets("1h", root))
    print("====  1h TP sweep  (win%% / maxDD%% @R0.5)  ====", flush=True)
    print("  %-10s %18s %18s" % ("TP", "RECON", "DAEMON"), flush=True)
    for tp in (0.003, 0.0035, 0.004, 0.0045, 0.005):
        row = []
        for ds in ("RECON", "DAEMON"):
            tr = eval_tp(*data[ds], tp)
            net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
            row.append("n%-4d %2.0f%% / %.1f%%" % (len(tr), 100 * (net > 0).mean(), maxdd_pct(rs)))
        print("  fix %.2f%%  %18s %18s" % (tp * 100, row[0], row[1]), flush=True)
    for tp in (0.003, 0.005):
        print("\n====  1h  prop time-to-pass  @ %.1f%% TP  (target10/maxDD10/daily5)  ====" % (tp * 100), flush=True)
        for ds in ("RECON", "DAEMON"):
            days = day_blocks(eval_tp(*data[ds], tp))
            spd = sum(len(d) for d in days) / max(1, len(days))
            print("  %s (%.1f trades/day):" % (ds, spd), flush=True)
            for Rp in (0.5, 0.75, 1.0):
                p, dtp = mc(days, Rp)
                q = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
                print("     R%.2f%%  pass=%3.0f%%  days p25/med/p75 = %d / %d / %d" % (Rp, p, q[0], q[1], q[2]), flush=True)


if __name__ == "__main__":
    main()
