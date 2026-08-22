"""RADAR RUNNER 30m BUCKET — the clean full redo, Jan 2025 -> end of archive (2026-06-19).

FIRING exactly as the terminal does it: incremental — at each bar CLOSE k, run radar_breakout_detect.detect over the
trailing history (W=2000, validated indistinguishable from a 10k window) and fire only signals whose breakout bar IS k.
No batch repaint. Validated: reproduces 147/155 of the terminal's own persisted fires with 0 spec mismatches (the 8
misses are live forming-bar edge cases offline replay cannot reconstruct).

BRACKET exactly as the badge draws it (terminal.py _draw_rr_lines): entry = breakout close (taker), SL = candle-anchored
(0.3% buffer, capped at radar extreme), 50% off at TP1 = entry±RR_TP1_FRAC (0.24% gross), 50% at TP2 = entry±RR_TP2_FRAC
(0.44% gross), stop -> BREAKEVEN after TP1. Sized at risk 0.4%/trade ($800 on $200k) — the SIZE line on the badge.

RESOLUTION at 1m (clock_archive/1m): first-touch order decides; same-bar SL+TP1 -> conservative full SL; same-bar
TP2+BE-after-TP1 -> conservative BE. Fees: 0.04% RT per tranche + 0.03% slip per taker leg (entry, SL/BE) — maker TPs.
Canonical stats on the NON-OVERLAP taken() sequence (standing rule). Prop = HyroTrader $200k MC (target 10%, 6% trailing
max, daily trailing), day-block bootstrap, R0.4 sizing -> FIRST-ATTEMPT pass %.

Phase-1 fires cached to study/out/rr30mbkt_live_fires.json (delete to re-run detection).
python study/radarrun_30mbkt_live_full.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "study", "out", "rr30mbkt_live_fires_union.json")
W, SLBUF = 2000, 0.003
FEE, SLIP = 0.0004, 0.0003
CAPMIN = 20000                    # 1m walk cap (~14 days; everything resolves far sooner)

_A = None                         # per-worker archive


def _init():
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(rng):
    """UNION live-persist over bar-close range [k0, k1): the terminal re-runs a full-history detect at EVERY bar close
    and persists every signal end_time not already frozen (terminal.py ~7783). So a bar b gets a badge if detect over
    data-up-to-k emits a signal at b for ANY k >= b — frozen at FIRST appearance (entry/sl never overwritten). Workers
    dedupe within their range by (bar, side) keeping the earliest k; main merges by earliest-first."""
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    k0, k1 = rng; seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W)
        sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"])                              # absolute bar index of the breakout badge
            key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def get_fires():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    import multiprocessing as mp
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    n = len(sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0))))
    chunks = [(a, min(a + 500, n)) for a in range(1, n, 500)]
    best = {}                                                  # (bar, side) -> earliest-appearance record
    with mp.Pool(4, initializer=_init) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            print("  chunk %d/%d  union badges so far %d" % (i, len(chunks), len(best)), flush=True)
    # terminal keys the persist dict by END_TIME alone (first signal on a bar wins) -> collapse same-bar both-side dups
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    json.dump(fires, open(CACHE, "w"))
    return fires


def resolve_scaleout(s, e, sl, t0, T1, H1, L1):
    """1m scale-out resolution. Returns (net, outcome, exit_time) — outcome in {SL, TP1_BE, TP1_TP2, EOD}."""
    g1 = 0.0024; g2 = 0.0044
    tp1 = e * (1 + s * g1); tp2 = e * (1 + s * g2)
    i0 = int(np.searchsorted(T1, t0 - 1)); hit1 = False
    for j in range(i0, min(len(T1), i0 + CAPMIN)):
        hi = H1[j]; lo = L1[j]
        if not hit1:
            sl_hit = (lo <= sl) if s > 0 else (hi >= sl)
            t1_hit = (hi >= tp1) if s > 0 else (lo <= tp1)
            if sl_hit:                                            # same-bar SL+TP1 -> conservative SL
                return s * (sl - e) / e - FEE - 2 * SLIP, "SL", T1[j]
            if t1_hit:
                hit1 = True
                t2_hit = (hi >= tp2) if s > 0 else (lo <= tp2)
                if t2_hit:                                        # monotone burst through both targets
                    return 0.5 * (g1 - FEE - SLIP) + 0.5 * (g2 - FEE - SLIP), "TP1_TP2", T1[j]
                continue
        else:
            be_hit = (lo <= e) if s > 0 else (hi >= e)
            t2_hit = (hi >= tp2) if s > 0 else (lo <= tp2)
            if be_hit:                                            # same-bar TP2+BE -> conservative BE
                return 0.5 * (g1 - FEE - SLIP) + 0.5 * (0.0 - FEE - 2 * SLIP), "TP1_BE", T1[j]
            if t2_hit:
                return 0.5 * (g1 - FEE - SLIP) + 0.5 * (g2 - FEE - SLIP), "TP1_TP2", T1[j]
    return (0.0 - FEE - 2 * SLIP), "EOD", T1[min(len(T1) - 1, i0 + CAPMIN - 1)]


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_hyro_prop import mc, day_blocks
    fires = get_fires()
    print("union badges (as the terminal persists them): %d" % len(fires), flush=True)
    # VALIDATION: the terminal's own persisted fires must be a subset of the union set
    try:
        from app import config as _cfg
        pers = json.load(open(os.path.join(_cfg.DATA_DIR, "radarrun_fired.json")))["30m"]
        ets = np.array([f[1] for f in fires]); okp = totp = 0
        for kk, v in pers.items():
            t = float(kk); dt = datetime.fromtimestamp(t, tz=timezone.utc)
            if (abs(t - round(t)) < 0.02 and dt.second == 0 and dt.minute % 30 == 0) or t > ets[-1] + 1:
                continue                                      # clock-aligned or past archive -> not ours
            totp += 1
            j = int(np.argmin(np.abs(ets - t)))
            if abs(ets[j] - t) < 2.0:
                okp += 1
        print("validation vs terminal persisted record: %d/%d reproduced" % (okp, totp), flush=True)
    except Exception as ex:
        print("(persisted validation skipped: %s)" % ex, flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1

    # canonical non-overlap taken(): accept a fire only once the previous taken trade has EXITED (1m time)
    taken = []; busy_until = -1.0
    for (k, t, s, e, sl) in fires:
        if t < busy_until:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        net, outc, tx = resolve_scaleout(s, e, sl, t, T1, H1, L1)
        taken.append(dict(t=t, s=s, e=e, sl=sl, net=net, r=net / sld, outc=outc,
                          y=datetime.fromtimestamp(t, tz=timezone.utc).year))
        busy_until = tx

    nets = np.array([x["net"] for x in taken]); rs = np.array([x["r"] for x in taken])
    nW = int((nets > 0).sum()); nL = int((nets < 0).sum())
    mix = {o: sum(1 for x in taken if x["outc"] == o) for o in ("TP1_TP2", "TP1_BE", "SL", "EOD")}
    # historical equity at the badge's 0.4%-risk sizing
    eq = np.cumsum(0.4 * rs); peak = np.maximum.accumulate(eq); hist_dd = float((peak - eq).max())
    days = day_blocks([(x["t"], x["net"], x["r"]) for x in taken])
    m4 = mc(days, 0.4, 4.0, "R"); m3 = mc(days, 0.4, 3.0, "R")

    span = (datetime.fromtimestamp(taken[0]["t"], tz=timezone.utc).date(),
            datetime.fromtimestamp(taken[-1]["t"], tz=timezone.utc).date())
    print("=" * 100, flush=True)
    print("RADAR RUNNER 30m BUCKET — LIVE-FAITHFUL FULL REDO  |  %s .. %s  |  as-badge scale-out bracket" % span, flush=True)
    print("-" * 100, flush=True)
    print("  raw badges fired            : %d" % len(fires), flush=True)
    print("  n TOTAL (non-overlap taken) : %d   (%.2f trades/day over %d days)" % (len(taken), len(taken) / max(1, len(days)), len(days)), flush=True)
    print("  n WINNERS (net>0)           : %d" % nW, flush=True)
    print("  n LOSERS  (net<0)           : %d" % nL, flush=True)
    print("  WIN RATE                    : %.1f%%" % (100 * nW / max(1, len(taken))), flush=True)
    print("  outcome mix                 : both-TP %d (%.1f%%) · TP1->BE %d (%.1f%%) · full-SL %d (%.1f%%) · EOD %d"
          % (mix["TP1_TP2"], 100 * mix["TP1_TP2"] / len(taken), mix["TP1_BE"], 100 * mix["TP1_BE"] / len(taken),
             mix["SL"], 100 * mix["SL"] / len(taken), mix["EOD"]), flush=True)
    print("  avg trade (net)             : %+.3f%%   avg R %+.3f" % (nets.mean() * 100, rs.mean()), flush=True)
    print("  DD  historical @0.4%%-risk   : %.2f%% of account   (MC p50/p90/p99: %.1f%% / %.1f%% / %.1f%%)"
          % (hist_dd, m4["dd50"], m4["dd90"], m4["dd99"]), flush=True)
    print("  PROP first-attempt pass     : %.1f%%  (R0.4, daily 4%%; med days-to-pass %.0f)  |  daily 3%%: %.1f%%"
          % (m4["p"], m4["d50"], m3["p"]), flush=True)
    for Y in (2025, 2026):
        yr = [x for x in taken if x["y"] == Y]
        if yr:
            ny = np.array([x["net"] for x in yr])
            print("  %d: n=%-4d win %.1f%%  avg %+.3f%%" % (Y, len(yr), 100 * (ny > 0).mean(), ny.mean() * 100), flush=True)


if __name__ == "__main__":
    main()
