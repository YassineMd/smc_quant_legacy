"""NY Range-break on 30m (+ 15m/1h context). Uses the SHIPPED detector (app.ny_rangebreak_detect): range = 2-5pm UTC
body hi/lo, break = first close beyond it 5-9pm UTC, entry at the break close, SL 0.1% beyond the opposite wick.
Two exit schemes: NATIVE (volatility-adaptive TP = 2x/0.5x range) and 1:1 (TP = SL distance -> isolates the raw
directional edge). Non-overlap book; RECON + DAEMON; win%/expR/realized-DD + day-block prop MC. Causal (range done by
5pm, break after). DESCRIPTIVE. 3bps slip / 0.04% fee. python study/ny_rangebreak_30m.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import ny_rangebreak_detect as NY

random.seed(7); np.random.seed(7)
H = 120; FEE = 0.0004; SLIP = 0.0003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 8000; MAXD = 500


def trades(buckets, mode):
    n = len(buckets)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in buckets])
    Hi = np.array([_f(b.get("high")) for b in buckets]); Lo = np.array([_f(b.get("low")) for b in buckets])
    ST = np.array([_f(b.get("start_time")) for b in buckets])
    days = NY.detect(buckets)
    rows = sorted([r for r in days if r.get("break_i") is not None and r["side"] != 0], key=lambda r: r["break_i"])
    tr = []; last = -1; nbreaks = len(rows); ndays = len(days)
    for r in rows:
        bi = int(r["break_i"]); side = int(r["side"]); entry = float(r["entry"]); sl = float(r["sl"])
        dist = abs(entry - sl) / entry
        if dist <= 0 or bi <= last or bi + 1 >= n:
            continue
        tp = float(r["tp"]) if mode == "native" else entry * (1 + side * dist)   # 1:1 -> TP distance == SL distance
        j0 = bi + 1; j1 = min(n, bi + 1 + H)
        outc, gross, off = sim(side, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((float(ST[bi]), net, net / dist)); last = bi + int(off)
    return tr, ndays, nbreaks


def prop_mc(tr):
    by = {}
    for ts, _n, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    if not by:
        return 0.0, [0, 0, 0]
    d0, d1 = min(by), max(by); ds = []; d = d0
    while d <= d1:
        ds.append(by.get(d, [])); d += timedelta(days=1)
    passes = 0; dtp = []
    for _ in range(NMC):
        eq = peak = 0.0; passed = failed = False
        for di in range(1, MAXD + 1):
            day = ds[random.randrange(len(ds))]; dstart = eq; dlow = eq
            for r in day:
                eq += 0.5 * r; dlow = min(dlow, eq); peak = max(peak, eq)
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
    for mode in ("native", "1:1"):
        lbl = "NATIVE (vol-adaptive TP, wick SL)" if mode == "native" else "1:1 (TP = SL dist)"
        print("\n################  NY RANGE-BREAK  —  %s  ################" % lbl, flush=True)
        print("  %-4s %-6s %5s %6s %6s %8s %8s %-16s" % ("tf", "data", "days", "n", "win%", "expR", "realDD%", "prop pass / days"), flush=True)
        for tf in ("15m", "30m", "1h"):
            for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
                try:
                    tr, nd, nb = trades(get_buckets(tf, root), mode)
                    if len(tr) < 8:
                        print("  %-4s %-6s %5d %5d (too few)" % (tf, ds, nd, len(tr))); continue
                    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
                    p, q = prop_mc(tr)
                    print("  %-4s %-6s %5d %5d %5.0f%% %+8.3f %8.1f  %3.0f%% / %d/%d/%d" % (
                        tf, ds, nd, len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs),
                        p, q[0], q[1], q[2]), flush=True)
                except Exception as e:
                    print("  %-4s %-6s  skipped: %s" % (tf, ds, e), flush=True)


if __name__ == "__main__":
    main()
