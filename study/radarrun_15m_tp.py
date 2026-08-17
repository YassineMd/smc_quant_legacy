"""15m Radar Runner @ 0.2% TP (with 0.3/0.4% for context). Same battery as the 30m work: candle-capped SL (0.3% buf),
one-position-at-a-time (non-overlap) basis. Per dataset: standalone win%/expR/realized-DD at each TP; day-block prop MC
(target10/DD10/daily5) pass% + days-to-pass at R0.5/0.75; and the HONEST random-selection MC at 0.2% (catch 1.0..0.4 ->
is the win% a selection artifact?) + bootstrap 95% CI. RECON (both yrs) + DAEMON (live). 3bps slip / 0.04% fee.
python study/radarrun_15m_tp.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 8000; MAXD = 400; NRAND = 6000


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
            sigs.append((k, s, entry, sl, dist))
    return sigs, Hi, Lo, C, ST, n


def book(pack, tp):
    """Non-overlap (one-at-a-time) trades at TP: (ts, net, R)."""
    sigs, Hi, Lo, C, ST, n = pack; tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((float(ST[k]), net, net / dist)); last = k + int(off)
    return tr


def all_R(pack, tp):
    """Every signal (overlap ok) as (k, exit_bar, R) for the random-selection MC."""
    sigs, Hi, Lo, C, ST, n = pack; out = []
    for (k, s, entry, sl, dist) in sigs:
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((k, k + int(off), net / dist))
    return out


def day_blocks(tr):
    by = {}
    for ts, _n, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    if not by:
        return []
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    return days


def prop_mc(days, Rp):
    if not days:
        return 0.0, [0, 0, 0]
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


def rand_path(sigs, catch):
    busy = -1; rs = []
    for (k, ex, R) in sigs:
        if k > busy and random.random() < catch:
            rs.append(R); busy = ex
    if not rs:
        return None
    return 100.0 * np.mean([r > 0 for r in rs]), maxdd_pct(rs, 0.005), len(rs)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        pack = detect(get_buckets("15m", root))
        nd = max(1, len(day_blocks(book(pack, 0.002))))
        print("\n################  15m  %s  (%d raw signals, ~%.1f/day)  ################" % (
            ds, len(pack[0]), len(pack[0]) / nd), flush=True)
        print("  --- non-overlap book: win / DD / prop pass (target10/DD10/daily5) ---", flush=True)
        print("  %-7s %5s %6s %8s %9s   %-22s %-22s" % (
            "TP", "n", "win%", "expR", "realDD%", "R0.5  pass / days", "R0.75 pass / days"), flush=True)
        for tp in (0.002, 0.003, 0.004):
            tr = book(pack, tp); rs = [t[2] for t in tr]; net = np.array([t[1] for t in tr])
            days = day_blocks(tr)
            row = []
            for Rp in (0.5, 0.75):
                p, q = prop_mc(days, Rp); row.append("%3.0f%% / %d/%d/%d" % (p, q[0], q[1], q[2]))
            print("  %-7s %5d %5.0f%% %+8.3f %8.1f   %-22s %-22s" % (
                "%.1f%%" % (tp * 100), len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs), row[0], row[1]), flush=True)

        print("  --- HONEST random-selection MC @ 0.2%% (one-at-a-time subset; R=0.5%%) ---", flush=True)
        sigs = all_R(pack, 0.002)
        print("  %-8s %6s %-22s %-16s" % ("catch", "~n", "win%  mean[p5..p95]", "maxDD%  med/p95"), flush=True)
        for catch in (1.0, 0.8, 0.6, 0.4):
            W = []; D = []; NN = []
            for _ in range(NRAND):
                r = rand_path(sigs, catch)
                if r:
                    W.append(r[0]); D.append(r[1]); NN.append(r[2])
            W = np.array(W); D = np.array(D)
            print("  %-8.1f %6d  %4.1f [%4.1f..%4.1f]        %5.1f / %5.1f" % (
                catch, int(np.mean(NN)), W.mean(), np.percentile(W, 5), np.percentile(W, 95),
                np.median(D), np.percentile(D, 95)), flush=True)
        seq = []; busy = -1
        for (k, ex, R) in sigs:
            if k > busy:
                seq.append(1 if R > 0 else 0); busy = ex
        seq = np.array(seq)
        boot = [np.mean(seq[np.random.randint(0, len(seq), len(seq))]) for _ in range(5000)]
        print("  diligent win = %.0f%%  (bootstrap 95%% CI %.0f-%.0f%%, n=%d one-at-a-time)" % (
            100 * seq.mean(), 100 * np.percentile(boot, 2.5), 100 * np.percentile(boot, 97.5), len(seq)), flush=True)


if __name__ == "__main__":
    main()
