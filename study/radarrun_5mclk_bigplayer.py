"""RADAR RUNNER 5m CLOCK x BIG PLAYER PRINT — screen (user 2026-09-04).

RULE (pre-registered): a 5m-CLOCK Radar Runner badge is taken only if, INSIDE its signal candle,
a single tape print >= $500K fired on the SAME side (taker BUY for a long / taker SELL for a short)
AND the candle itself agrees (long: close > open; short: close < open). Entry = badge close, SL =
badge sl_trade (0.3% buffer), exits 0.2%/0.4% fix + RR 1/1.5/2, 1m-clock first-touch ties-against,
canonical fees, non-overlap taken().
SIGNALS = what the terminal shows on the 5m clock: union-persist replay (per-close trailing W=2000,
first-appearance freeze) THEN the terminal's 5m-TIME absorpR gate (absorption R >= config.RR_ABSORPR_MIN,
uncomputable -> KEPT). Big prints from study/bigprint_archive (Binance aggTrades, >= $50K retained).
SAMPLE: 15 week-distinct days (8 in 2025, 7 in 2026H1 — the clock archive ends 2026-06-30), 24h, seed
20260920. SCREEN ONLY (small n): a hit must replicate on a fresh draw before anything else.
PREDICTION ON RECORD: 'strong flow behind the break' filters are 0-for-4 (HC ring, absorbed-only,
delta+kept, DELTA>=P80) — expect no stable edge.
python study/radarrun_5mclk_bigplayer.py"""
import os, sys, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, EXITS, W1, SLBUF
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ
from app import bigprint_store

SEED = int(os.environ.get("RR_SEED", "20260920"))
N_DAYS = {2025: 8, 2026: 7}
BP_USD = 500_000.0


def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    from app import config, radar_breakout_detect as RB
    from app.absorption import absorption
    t0 = time.time()
    print("RR 5m CLOCK x BIG PLAYER (>= $%.0fK same-side print inside the signal candle + candle agrees) "
          "| 15 week-distinct days | seed %d\n" % (BP_USD / 1e3, SEED), flush=True)

    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]
    A5 = sorted(load_archive("5m", root="study/clock_archive", drop_degenerate=False)[1],
                key=lambda b: _f(b.get("start_time", 0)))
    ST5 = np.array([_f(b.get("start_time")) for b in A5])
    print("5m clock bars: %d (%s -> %s)  load %.0fs" % (len(A5), day_of(ST5[0]), day_of(ST5[-1]),
                                                        time.time() - t0), flush=True)

    # --- sample: week-distinct days per year, seeded
    days = sorted({day_of(t) for t in ST5})
    rng = random.Random(SEED)
    sample = []
    for yr, nd in N_DAYS.items():
        by_week = {}
        for d in days:
            if d[:4] == str(yr):
                by_week.setdefault(datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2], []).append(d)
        for w in rng.sample(sorted(by_week), min(nd, len(by_week))):
            sample.append(rng.choice(by_week[w]))
    sample = sorted(sample)
    print("sampled days: %s\n" % ", ".join(sample), flush=True)

    # --- union-persist replay on the sampled days + the terminal's 5m absorpR gate
    fires = {}                                     # (bar, side) -> (bar, et, side, entry, sl)
    n_gate_out = 0
    for di, d in enumerate(sample):
        d0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        k0 = int(np.searchsorted(ST5, d0)); k1 = int(np.searchsorted(ST5, d0 + 86400))
        for k in range(max(1, k0), k1):
            lo = max(0, k - W1)
            sub = A5[lo:k + 1]
            for g in RB.detect(sub, skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
                b = lo + int(g["i"])
                key = (b, g["side"])
                if key in fires or b < k0 or b > k:
                    continue
                try:
                    aR = absorption(sub, int(g["i"]))[0]
                except Exception:
                    aR = None
                if aR is not None and aR < config.RR_ABSORPR_MIN:
                    n_gate_out += 1
                    fires[key] = None              # seen + gated (never re-admitted)
                    continue
                fires[key] = (b, _f(A5[b].get("end_time")), int(g["side"]), float(g["entry"]),
                              float(g["sl_trade"]))
        print("  day %d/%d %s: badges so far %d (absorpR-gated %d)" % (
            di + 1, len(sample), d, sum(1 for v in fires.values() if v), n_gate_out), flush=True)
    badges = sorted(v for v in fires.values() if v)
    print("\n5m badges on sampled days: %d shown / %d gated out by absorpR\n" % (len(badges), n_gate_out),
          flush=True)

    # --- big-print + candle conditions per badge
    trades = []
    cnt = {"bp_same": 0, "bp_opp": 0, "bp_none": 0, "candle_ok": 0}
    for (b, et, s, e, sl) in badges:
        bar = A5[b]
        st = _f(bar.get("start_time")); o = _f(bar.get("open")); c = _f(bar.get("close", bar.get("close_price")))
        prints = bigprint_store.load_prints(st, et, BP_USD)
        same = any(p[3] == (1 if s > 0 else 0) for p in prints)
        opp = any(p[3] == (0 if s > 0 else 1) for p in prints)
        candle_ok = (c > o) if s > 0 else (c < o)
        cnt["bp_same" if same else ("bp_opp" if opp else "bp_none")] += 1
        cnt["candle_ok"] += int(candle_ok)
        trades.append(dict(t=et, s=s, e=e, sl=sl, same=same, opp=opp, cok=candle_ok,
                           bpmax=max((p[2] for p in prints), default=0.0)))
    print("conditions: %s  (of %d badges)\n" % (cnt, len(trades)), flush=True)

    print("=" * 132, flush=True)
    for tag, sel in (("ALL", lambda x: True),
                     ("HYP", lambda x: x["same"] and x["cok"]),          # same-side big print + candle agrees
                     ("BP-SAME", lambda x: x["same"]),                    # same-side big print (candle any)
                     ("BP-OPP", lambda x: x["opp"] and not x["same"]),    # only an opposite-side big print
                     ("NO-BP", lambda x: not x["same"] and not x["opp"]),
                     ("CANDLE-OK", lambda x: x["cok"])):
        sub = [x for x in trades if sel(x)]
        for ename, kind, val in EXITS:
            report_cell("5m %s" % tag, ename, sub, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
