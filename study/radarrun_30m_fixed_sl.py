"""30m Radar Runner @ 0.2% TP: FIXED stop vs the shipped CANDLE-CAPPED stop. User idea: fixed SL 0.5%, TP 0.2%.
Geometry note: under fixed-fractional risk (R% of account per trade), a WIDER stop stops out less (higher win%) but
each win earns LESS in account terms (smaller position), and reward:risk = TP/SL = 0.4:1 -> break-even win ~82% incl fees.
So this only helps if the wider stop lifts win% enough. Compare CANDLE (0.3% buf, radar-capped) vs FIX 0.4/0.5/0.6%,
all at 0.2% TP, on the non-overlap book: win%, expR (net/dist = account impact per R), realized DD, prop pass%/days
(R0.5/0.75). RECON + DAEMON. 3bps slip / 0.04% fee. python study/radarrun_30m_fixed_sl.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003; TP = 0.002
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 8000; MAXD = 400


def detect(A):
    """30m signals: (k, s, entry, sl_candle, rlo, rhi) + arrays. sl_candle = shipped candle-capped stop."""
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
        slc = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        sigs.append((k, s, float(entry), float(slc), float(rlo), float(rhi)))
    return sigs, Hi, Lo, C, ST, n


def book(pack, scheme):
    """scheme = 'candle' or a float fixed-SL fraction. Non-overlap trades: (ts, R)."""
    sigs, Hi, Lo, C, ST, n = pack; tr = []; last = -1
    for (k, s, entry, slc, rlo, rhi) in sigs:
        sl = slc if scheme == "candle" else (entry * (1 - s * scheme))
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((float(ST[k]), net / dist)); last = k + int(off)
    return tr


def prop_mc(tr, Rp):
    by = {}
    for ts, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    if not by:
        return 0.0, [0, 0, 0]
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    passes = 0; dtp = []
    for _ in range(NMC):
        eq = peak = 0.0; passed = failed = False
        for di in range(1, MAXD + 1):
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
                    passes += 1; dtp.append(di)
                break
    q = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
    return 100.0 * passes / NMC, q


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        pack = detect(get_buckets("30m", root))
        print("\n################  30m @0.2%% TP  %s  ################" % ds, flush=True)
        print("  %-14s %5s %6s %8s %9s  %-20s %-20s" % (
            "SL scheme", "n", "win%", "expR", "realDD%", "R0.5 pass/days", "R0.75 pass/days"), flush=True)
        for scheme, label in (("candle", "candle (shipped)"), (0.004, "fixed 0.4%"), (0.005, "fixed 0.5%"), (0.006, "fixed 0.6%")):
            tr = book(pack, scheme)
            rs = [t[1] for t in tr]; win = 100.0 * np.mean([r > 0 for r in rs])
            row = []
            for Rp in (0.5, 0.75):
                p, q = prop_mc(tr, Rp); row.append("%3.0f%% / %d/%d/%d" % (p, q[0], q[1], q[2]))
            print("  %-14s %5d %5.0f%% %+8.3f %8.1f  %-20s %-20s" % (
                label, len(tr), win, float(np.mean(rs)), maxdd_pct(rs), row[0], row[1]), flush=True)


if __name__ == "__main__":
    main()
