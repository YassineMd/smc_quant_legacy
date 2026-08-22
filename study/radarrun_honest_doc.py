"""HONEST Radar Runner tables for the docx — everything re-computed from the TERMINAL'S OWN fired record
(data/radarrun_fired.json), split CLOCK vs BUCKET by end-time alignment, each signal re-simulated FORWARD at 1m from its
entry. NOT batch detection (which repaints radar runs and inflates win rates). Coverage = whatever the user has
run/replayed; fires past the 1m archive end (2026-06-30) are unresolvable and dropped. Per (source, config): win%, avg
net%, avg R, trades/day, + prop-MC (R0.4 fixed-risk and Notional 10%x10lev). Configs: as-fired (0.25% TP + candle-SL),
TP sweep, 0.5% cap SL, scale-out (TP1/TP2 -> BE). python study/radarrun_honest_doc.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import config
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_hyro_prop import mc, day_blocks

FEE, SLIP = 0.0004, 0.0003
TF_MIN = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}
MINN = 40                       # min resolved trades to bother with prop-MC


def src_of(t, tf):
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    whole = abs(t - round(t)) < 0.02
    return "CLK" if (whole and dt.second == 0 and dt.minute % TF_MIN.get(tf, 15) == 0) else "BKT"


def load_1m():
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    return T1, H1, L1


def sim_bracket(side, entry, sl, tp, t0, T1, H1, L1, HOLDMIN=6000):
    """Single SL/TP, first-touch at 1m. Returns net (maker TP -fee-slip, taker SL -fee-2slip) or None if no coverage."""
    i0 = int(np.searchsorted(T1, t0 - 1))
    if i0 >= len(T1):
        return None
    for j in range(i0, min(len(T1), i0 + HOLDMIN)):
        sl_hit = (L1[j] <= sl) if side > 0 else (H1[j] >= sl)
        tp_hit = (H1[j] >= tp) if side > 0 else (L1[j] <= tp)
        if sl_hit:                                            # SL priority (conservative) — but tight-TP false-loss is rare here
            return side * (sl - entry) / entry - FEE - 2 * SLIP
        if tp_hit:
            return side * (tp - entry) / entry - FEE - SLIP
    return None                                               # unresolved within hold -> drop (rare)


def sim_scaleout(side, entry, sl, tp1, tp2, t0, T1, H1, L1, HOLDMIN=6000):
    """50% at TP1, then stop->BE; remaining 50% at TP2 or BE. Returns blended net."""
    i0 = int(np.searchsorted(T1, t0 - 1))
    if i0 >= len(T1):
        return None
    hit1 = False
    for j in range(i0, min(len(T1), i0 + HOLDMIN)):
        sl_now = entry if hit1 else sl
        sl_hit = (L1[j] <= sl_now) if side > 0 else (H1[j] >= sl_now)
        t1_hit = (H1[j] >= tp1) if side > 0 else (L1[j] <= tp1)
        t2_hit = (H1[j] >= tp2) if side > 0 else (L1[j] <= tp2)
        if not hit1:
            if sl_hit:                                        # full stop before TP1
                return side * (sl - entry) / entry - FEE - 2 * SLIP
            if t1_hit:
                hit1 = True
                if t2_hit:                                    # same bar reached TP2 too -> both fills
                    return 0.5 * (side * (tp1 - entry) / entry - FEE - SLIP) + 0.5 * (side * (tp2 - entry) / entry - FEE - SLIP)
                continue
        else:
            if t2_hit:
                return 0.5 * (side * (tp1 - entry) / entry - FEE - SLIP) + 0.5 * (side * (tp2 - entry) / entry - FEE - SLIP)
            if sl_hit:                                        # 2nd half stopped at BE (net ~ -fee-slip on that half)
                return 0.5 * (side * (tp1 - entry) / entry - FEE - SLIP) + 0.5 * (0.0 - FEE - SLIP)
    return None


def metrics(tr):
    """tr = list of (ts, net, r). -> dict of win/avg/prop."""
    if len(tr) < 5:
        return dict(n=len(tr))
    nets = np.array([t[1] for t in tr]); rs = np.array([t[2] for t in tr])
    days = day_blocks(tr); spd = len(tr) / max(1, len(days))
    d = dict(n=len(tr), win=100 * (nets > 0).mean(), avg=nets.mean() * 100, avgR=rs.mean(), spd=spd)
    if len(tr) >= MINN:
        r04 = mc(days, 0.4, 4.0, "R"); noti = mc(days, 1.0, 3.0, "N")
        d.update(r04_p=r04["p"], r04_med=r04["d50"], r04_dd=r04["dd99"],
                 noti_p=noti["p"], noti_med=noti["d50"], noti_dd=noti["dd99"], noti_worst_dd=noti["dd90"])
    return d


def fmt(m):
    if m.get("n", 0) < 5:
        return "n=%d (too few)" % m.get("n", 0)
    base = "n=%-4d win %.1f%%  avg %+.3f%%  R %+.2f  %.2f trd/day" % (m["n"], m["win"], m["avg"], m["avgR"], m["spd"])
    if "r04_p" in m:
        base += "  | R0.4: %.0f%%/%.0fd/DD%.1f%%  | Noti: %.0f%%/%.0fd/DD%.1f%%" % (
            m["r04_p"], m["r04_med"], m["r04_dd"], m["noti_p"], m["noti_med"], m["noti_dd"])
    else:
        base += "  | (n<%d: no prop-MC)" % MINN
    return base


def main():
    fired = json.load(open(os.path.join(config.DATA_DIR, "radarrun_fired.json")))
    T1, H1, L1 = load_1m()
    print("HONEST Radar Runner — all metrics from the fired record, 1m-resolved. 1m archive ends %s.\n"
          % datetime.fromtimestamp(T1[-1], tz=timezone.utc).strftime("%Y-%m-%d"), flush=True)
    SRCS = [("15m", "CLK"), ("15m", "BKT"), ("30m", "CLK"), ("30m", "BKT")]

    def signals(tf, src):
        out = []
        for k, v in fired.get(tf, {}).items():
            t = float(k)
            if src_of(t, tf) != src:
                continue
            out.append((t, 1 if float(v.get("side", 0)) > 0 else -1, float(v["entry"]), float(v["sl"]), float(v["tp"])))
        return sorted(out)

    for tf, src in SRCS:
        sg = signals(tf, src)
        name = "%s %s" % (tf, "CLOCK" if src == "CLK" else "BUCKET")
        print("=" * 118, flush=True)
        print("%-11s | fired=%d" % (name, len(sg)), flush=True)
        # AS-FIRED (record sl/tp = 0.25% TP + candle-SL)
        tr = []
        for t, s, e, sl, tp in sg:
            net = sim_bracket(s, e, sl, tp, t, T1, H1, L1)
            if net is not None:
                tr.append((t, net, net / (abs(e - sl) / e)))
        print("  as-fired (0.25%% TP + candle-SL) : %s" % fmt(metrics(tr)), flush=True)
        # TP sweep (candle-SL, gross TP)
        for g in (0.002, 0.003, 0.004):
            tr = []
            for t, s, e, sl, tp in sg:
                net = sim_bracket(s, e, sl, e * (1 + s * g), t, T1, H1, L1)
                if net is not None:
                    tr.append((t, net, net / (abs(e - sl) / e)))
            print("  TP %.1f%% gross + candle-SL      : %s" % (g * 100, fmt(metrics(tr))), flush=True)
        # 0.5% cap SL (as-fired 0.25% TP)
        tr = []
        for t, s, e, sl, tp in sg:
            capsl = e * (1 - s * 0.005)                       # SL distance capped at 0.5%
            sl2 = max(sl, capsl) if s > 0 else min(sl, capsl)  # tighter of candle vs 0.5%
            net = sim_bracket(s, e, sl2, tp, t, T1, H1, L1)
            if net is not None:
                tr.append((t, net, net / (abs(e - sl2) / e)))
        print("  0.5%% cap-SL + 0.25%% TP           : %s" % fmt(metrics(tr)), flush=True)
        # scale-out (TP1/TP2 -> BE), candle-SL
        tr = []
        for t, s, e, sl, tp in sg:
            net = sim_scaleout(s, e, sl, e * (1 + s * config.RR_TP1_FRAC), e * (1 + s * config.RR_TP2_FRAC), t, T1, H1, L1)
            if net is not None:
                tr.append((t, net, net / (abs(e - sl) / e)))
        print("  scale-out TP1/TP2->BE + candle-SL: %s" % fmt(metrics(tr)), flush=True)


if __name__ == "__main__":
    main()
