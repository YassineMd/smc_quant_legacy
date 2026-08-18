"""Radar Runner @ 0.2% TP + candle-capped SL on the CLOCK archive, ALL timeframes -> win% / maxDD / prop-firm pass / n.

Identical method to study/radarrun_1h_setup_prop (taken()-nonoverlap, day-block MC target10/maxDD10/daily5, R sizing
0.5/0.75/1%), only the data source is study/clock_archive and it loops every tf. SL buffer per tf follows the shipped
values (1h/4h 0.2%, else 0.3%). Fee 0.04% RT + 0.03% slip on SL/timeout. Both recon years pooled (2025 + 2026-H1).
Usage: python study/radarrun_proptp_alltf_clock.py [tf ...]
"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; TP = 0.002
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NPATH = 8000; MAXD = 400
SLBUF = {"1m": 0.003, "5m": 0.003, "15m": 0.003, "30m": 0.003, "1h": 0.002, "4h": 0.002}


def detect(A, buf):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
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
        sl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
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
    if not by:
        return []
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    return days


def mc(days, Rp):
    if not days:
        return 0.0, []
    passes = 0; dtp = []
    for _ in range(NPATH):
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
    return 100.0 * passes / NPATH, dtp


def main():
    tfs = sys.argv[1:] or ["1m", "5m", "15m", "30m", "1h", "4h"]
    print("Radar Runner @ %.1f%% TP + candle-SL, CLOCK candles (2025 + 2026-H1). fee %.2f%%RT + %.2f%% slip.\n"
          % (TP * 100, FEE * 100, SLIP * 100), flush=True)
    print("  %-4s | %5s | win%% | maxDD(R) | trades/day |   prop-firm PASS%% (target10/maxDD10/daily5)"
          % ("tf", "n"), flush=True)
    print("  %-4s | %5s |      |          |            |   R0.5 / R0.75 / R1.0" % ("", ""), flush=True)
    print("  " + "-" * 92, flush=True)
    for tf in tfs:
        A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
                   key=lambda b: _f(b.get("start_time", 0)))
        tr = eval_tp(*detect(A, SLBUF.get(tf, 0.003)), TP)
        if not tr:
            print("  %-4s | %5d | -- no trades --" % (tf, 0), flush=True); continue
        net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
        win = 100.0 * (net > 0).mean(); dd = maxdd_pct(rs)
        days = day_blocks(tr); spd = sum(len(d) for d in days) / max(1, len(days))
        ps = [mc(days, Rp)[0] for Rp in (0.5, 0.75, 1.0)]
        verdict = "PASS" if ps[0] >= 80 else ("marginal" if ps[0] >= 40 else "FAIL")
        print("  %-4s | %5d | %4.1f | %7.2f | %9.2f | %3.0f%% / %4.0f%% / %4.0f%%   [%s @R0.5]"
              % (tf, len(tr), win, dd, spd, ps[0], ps[1], ps[2], verdict), flush=True)


if __name__ == "__main__":
    main()
