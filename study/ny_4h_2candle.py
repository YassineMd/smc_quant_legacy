"""NY-session 4h two-candle breakout — MEASURED-MOVE TP. Clock-aligned 4h candles from 15m:
  Candle 1 = 13:00-17:00 UTC, Candle 2 = 17:00-21:00 UTC. Range = Candle 1 BODY (max/min of open & close). Candle 2
CLOSE above upper -> LONG, below lower -> SHORT. Enter at Candle 2 close (~21:00). TP = 1x the Candle-1 body range
(measured move) projected from entry. Two stops: CANDLE (0.3% beyond Candle 1 low/high) and RANGE (symmetric 1x-range
the other way = a clean 1:1, which isolates the raw directional edge). 4-day hold cap. Weekdays; causal. RECON + DAEMON;
non-overlap; win%/expR/DD + prop MC. python study/ny_4h_2candle.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import maxdd_pct

random.seed(7); np.random.seed(7)
FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003; HOLD = 4 * 86400
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 8000; MAXD = 500
_o = lambda b: _f(b.get("open", b.get("open_price"))); _c = lambda b: _f(b.get("close", b.get("close_price")))
_h = lambda b: _f(b.get("high")); _l = lambda b: _f(b.get("low"))


def setups(A15):
    """Per weekday: (entry_time, side, entry, hi1, lo1, rng1) where Candle 2 breaks Candle 1's body range.
    rng1 = Candle 1 body height (upper-lower), the measured-move unit."""
    days = {}
    for b in A15:
        st = _f(b.get("start_time"))
        if st <= 0:
            continue
        t = datetime.fromtimestamp(st, tz=timezone.utc)
        if t.weekday() >= 5:
            continue
        slot = "c1" if 13 <= t.hour < 17 else ("c2" if 17 <= t.hour < 21 else None)
        if slot:
            days.setdefault(t.date(), {}).setdefault(slot, []).append(b)
    out = []
    for d in sorted(days):
        c1 = days[d].get("c1"); c2 = days[d].get("c2")
        if not c1 or not c2:
            continue
        o1 = _o(c1[0]); c1c = _c(c1[-1]); hi1 = max(_h(x) for x in c1); lo1 = min(_l(x) for x in c1)
        c2c = _c(c2[-1]); et = _f(c2[-1].get("end_time", c2[-1].get("start_time")))
        upper = max(o1, c1c); lower = min(o1, c1c); rng1 = upper - lower
        if rng1 <= 0:
            continue
        if c2c > upper:
            side = 1
        elif c2c < lower:
            side = -1
        else:
            continue
        out.append((et, side, c2c, hi1, lo1, rng1))
    return out, len(days)


def resolve(ST, Hi, Lo, C, et, side, entry, sl, tp):
    i0 = int(np.searchsorted(ST, et, side="right")); cap = et + HOLD; last = i0 - 1
    for i in range(i0, len(ST)):
        if ST[i] > cap:
            break
        last = i
        if (Lo[i] <= sl) if side > 0 else (Hi[i] >= sl):
            return "sl", side * (sl - entry) / entry
        if (Hi[i] >= tp) if side > 0 else (Lo[i] <= tp):
            return "tp", side * (tp - entry) / entry
    px = C[last] if last >= i0 else entry
    return "end", side * (px - entry) / entry


def trades(A15, sl_mode):
    ST = np.array([_f(b.get("start_time")) for b in A15]); C = np.array([_c(b) for b in A15])
    Hi = np.array([_h(b) for b in A15]); Lo = np.array([_l(b) for b in A15])
    stp, ndays = setups(A15); tr = []; last_t = -1.0; mix = {"tp": 0, "end": 0, "sl": 0}
    for (et, side, entry, hi1, lo1, rng1) in stp:
        if entry <= 0 or et <= last_t:
            continue
        tp = entry + side * rng1                                   # MEASURED MOVE: 1x the Candle-1 body range
        if sl_mode == "candle":
            sl = lo1 * (1 - SLBUF) if side > 0 else hi1 * (1 + SLBUF)
        else:                                                      # RANGE: symmetric 1x range the other way -> clean 1:1
            sl = entry - side * rng1
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        outc, gross = resolve(ST, Hi, Lo, C, et, side, entry, sl, tp)
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((et, net, net / dist)); mix[outc] += 1; last_t = et
    return tr, ndays, mix


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
    for sl_mode, lbl in (("candle", "measured-move TP  +  SL 0.3% beyond Candle 1"),
                         ("range", "measured-move TP  +  1:1 SL (symmetric 1x range)")):
        print("\n################  NY 4h 2-CANDLE  —  %s  ################" % lbl, flush=True)
        print("  %-6s %6s %5s %6s %8s %8s %-15s %-12s" % (
            "data", "wkday", "n", "win%", "expR", "realDD%", "prop pass/days", "tp/end/sl%"), flush=True)
        for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
            try:
                tr, nd, mix = trades(get_buckets("15m", root), sl_mode)
                if len(tr) < 8:
                    print("  %-6s %6d %5d (too few)" % (ds, nd, len(tr))); continue
                net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]; N = len(tr)
                p, q = prop_mc(tr)
                print("  %-6s %6d %5d %5.0f%% %+8.3f %8.1f  %3.0f%% / %d/%d/%d   %.0f/%.0f/%.0f" % (
                    ds, nd, N, 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs), p, q[0], q[1], q[2],
                    100 * mix["tp"] / N, 100 * mix["end"] / N, 100 * mix["sl"] / N), flush=True)
            except Exception as e:
                print("  %-6s  skipped: %s" % (ds, e), flush=True)


if __name__ == "__main__":
    main()
