"""MIX timeframes on ONE book (one position at a time). Base = 30m @ 0.2% TP. Question: does ALSO taking 1h and 4h
Radar Runner signals (at 0.2 / 0.3 / 0.4% TP) help or hurt -- win%, drawdown, and time to pass a prop challenge?

Model: every signal from every enabled TF is a candidate with a real (entry_time -> exit_time) span. You hold ONE
position; walk chronologically and when FLAT take the next candidate (greedy = 'normal'; or random 'catch' = honest).
A long 4h trade blocks 30m signals for its whole duration -- that throughput cost is the crux, and it's modelled here
because exits use real bucket end_times. R = net/SL-dist (fees+slip inside), sized R%=0.5/trade. Non-overlap is GLOBAL
across TFs. python study/radarrun_mix_tf.py"""
import os, sys, random, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct, maxdd_R
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003
RP = 0.5                                    # % account risked per trade
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 6000; MAXD = 300; NRAND = 4000
SLBUF = {"30m": 0.003, "1h": 0.002, "4h": 0.002}


def rr_signals(A, slbuf):
    """Detect EVERY Radar Runner breakout (shipped logic). Returns per-signal (k,s,entry,sl,dist) + Hi/Lo/C/ET arrays.
    No per-TF non-overlap here -- overlap is resolved GLOBALLY across TFs when the book is walked."""
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ET = np.array([_f(b.get("end_time", b.get("start_time"))) for b in A])
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
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist > 0:
            sigs.append((k, s, entry, sl, dist))
    return sigs, Hi, Lo, C, ET, n


def trades_at_tp(pack, tp, tf):
    """Turn detected signals into candidate trades at a given TP: (entry_ts, exit_ts, R, net, tf). Overlap allowed."""
    sigs, Hi, Lo, C, ET, n = pack; out = []
    for (k, s, entry, sl, dist) in sigs:
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        ex = min(n - 1, k + int(off))
        out.append((float(ET[k]), float(ET[ex]), net / dist, net, tf))
    return out


_TFRANK = {"30m": 0, "1h": 1, "4h": 2}


def book(cands):
    """Walk one book: sort by entry_ts (tie -> prefer faster TF for throughput); take when flat. Returns chrono trades."""
    cs = sorted(cands, key=lambda c: (c[0], _TFRANK.get(c[4], 9)))
    busy = -1e18; taken = []
    for (ets, xts, R, net, tf) in cs:
        if ets >= busy:
            taken.append((ets, xts, R, net, tf)); busy = xts
    return taken


def rand_path(cands, catch):
    cs = sorted(cands, key=lambda c: (c[0], _TFRANK.get(c[4], 9)))
    busy = -1e18; rs = []
    for (ets, xts, R, net, tf) in cs:
        if ets >= busy and random.random() < catch:
            rs.append(R); busy = xts
    if not rs:
        return None
    return 100.0 * np.mean([r > 0 for r in rs]), maxdd_pct(rs, RP / 100.0), len(rs)


def day_blocks(taken):
    by = {}
    for (ets, xts, R, net, tf) in taken:
        by.setdefault(datetime.fromtimestamp(ets, tz=timezone.utc).date(), []).append(R)
    if not by:
        return []
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    return days


def prop_mc(days):
    if not days:
        return 0.0, [0, 0, 0]
    passes = 0; dtp = []
    for _ in range(NMC):
        eq = peak = 0.0; passed = failed = False
        for di in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += RP * r; dlow = min(dlow, eq); peak = max(peak, eq)
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


def summarize(taken):
    if not taken:
        return None
    rs = [t[2] for t in taken]; net = np.array([t[3] for t in taken])
    days = day_blocks(taken); nd = max(1, len(days))
    p, q = prop_mc(days)
    return {"n": len(taken), "win": 100 * (net > 0).mean(), "expR": float(np.mean(rs)),
            "dd": maxdd_pct(rs, RP / 100.0), "perday": len(taken) / nd, "pass": p, "days": q,
            "mix": {tf: sum(1 for t in taken if t[4] == tf) for tf in ("30m", "1h", "4h")}}


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        print("\n############################  %s  ############################" % ds, flush=True)
        packs = {}
        for tf in ("30m", "1h", "4h"):
            try:
                packs[tf] = rr_signals(get_buckets(tf, root), SLBUF[tf])
                print("  loaded %-3s: %d raw signals" % (tf, len(packs[tf][0])), flush=True)
            except Exception as e:
                print("  %-3s UNAVAILABLE: %s" % (tf, e), flush=True)

        # ---- standalone per-TF (raw, overlap-allowed) so you see each TF's own win/EV/DD at each TP ----
        print("\n  --- standalone per TF (all signals, no book) ---", flush=True)
        print("  %-10s %6s %6s %8s %10s" % ("tf @ tp", "n", "win%", "expR", "maxDD%@R.5"), flush=True)
        for tf in ("30m", "1h", "4h"):
            if tf not in packs:
                continue
            for tp in (0.002, 0.003, 0.004):
                c = trades_at_tp(packs[tf], tp, tf)
                if len(c) < 5:
                    print("  %-10s %6d  (too few)" % ("%s @%.1f" % (tf, tp * 100), len(c))); continue
                rs = [x[2] for x in c]; net = np.array([x[3] for x in c])
                print("  %-10s %6d %5.0f%% %+8.3f %10.1f" % (
                    "%s @%.1f" % (tf, tp * 100), len(c), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs, RP / 100.0)), flush=True)

        # ---- portfolios on ONE book: base 30m@0.2, then add 1h / 4h / both at each TP ----
        base = trades_at_tp(packs["30m"], 0.002, "30m") if "30m" in packs else []
        ports = [("BASE 30m@0.2", base)]
        for tp in (0.002, 0.003, 0.004):
            if "1h" in packs:
                ports.append(("+1h@%.1f" % (tp * 100), base + trades_at_tp(packs["1h"], tp, "1h")))
            if "4h" in packs:
                ports.append(("+4h@%.1f" % (tp * 100), base + trades_at_tp(packs["4h"], tp, "4h")))
            if "1h" in packs and "4h" in packs:
                ports.append(("+1h&4h@%.1f" % (tp * 100),
                              base + trades_at_tp(packs["1h"], tp, "1h") + trades_at_tp(packs["4h"], tp, "4h")))

        print("\n  --- ONE BOOK, one-position-at-a-time (NORMAL greedy) + prop MC (target10/DD10/daily5, R0.5) ---", flush=True)
        print("  %-14s %5s %6s %8s %9s %8s %7s %-16s %s" % (
            "portfolio", "n", "win%", "expR", "maxDD%", "trd/day", "pass%", "days 25/50/75", "mix 30m/1h/4h"), flush=True)
        randset = {}
        for name, cands in ports:
            s = summarize(book(cands))
            if s is None:
                print("  %-14s (empty)" % name); continue
            m = s["mix"]
            print("  %-14s %5d %5.0f%% %+8.3f %8.1f %8.1f %6.0f%% %-16s %d/%d/%d" % (
                name, s["n"], s["win"], s["expR"], s["dd"], s["perday"], s["pass"],
                "%d/%d/%d" % (s["days"][0], s["days"][1], s["days"][2]), m["30m"], m["1h"], m["4h"]), flush=True)
            if name in ("BASE 30m@0.2", "+1h&4h@0.2", "+1h&4h@0.3", "+1h&4h@0.4"):
                randset[name] = cands

        # ---- RANDOM selection MC on the key portfolios (honest: you can't catch every signal) ----
        print("\n  --- RANDOM selection MC (catch a random one-at-a-time subset; is the win/DD stable?) ---", flush=True)
        print("  %-14s %6s %5s %-22s %-16s" % ("portfolio", "catch", "~n", "win%  mean[p5..p95]", "maxDD%  med/p95"), flush=True)
        for name in ("BASE 30m@0.2", "+1h&4h@0.2", "+1h&4h@0.3", "+1h&4h@0.4"):
            if name not in randset:
                continue
            for catch in (1.0, 0.6):
                W = []; D = []; NN = []
                for _ in range(NRAND):
                    r = rand_path(randset[name], catch)
                    if r:
                        W.append(r[0]); D.append(r[1]); NN.append(r[2])
                if not W:
                    continue
                W = np.array(W); D = np.array(D)
                print("  %-14s %6.1f %5d  %4.1f [%4.1f..%4.1f]        %5.1f / %5.1f" % (
                    name, catch, int(np.mean(NN)), W.mean(), np.percentile(W, 5), np.percentile(W, 95),
                    np.median(D), np.percentile(D, 95)), flush=True)


if __name__ == "__main__":
    main()
