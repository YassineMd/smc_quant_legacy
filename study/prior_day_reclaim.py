"""PRIOR-DAY HIGH/LOW structure-reaction study. Prior day's HIGH (PDH) / LOW (PDL) as key levels (UTC day; PDH/PDL from
the FULLY-CLOSED prior day, so causal). Four intraday setups, first occurrence per level per day:
  PDH break    : first close > PDH        -> LONG  (breakout continuation)
  PDL break    : first close < PDL        -> SHORT
  PDL reclaim  : day traded < PDL, then a close back > PDL  -> LONG   (failed breakdown)
  PDH reclaim  : day traded > PDH, then a close back < PDH  -> SHORT  (failed breakout)

OUTCOME (SYMMETRIC, so base = a true 50%): from the signal close, does price reach +X% BEFORE −X% in the setup's
direction? P>50% = the setup direction leans right; P<50% = FADE it. Reported per setup, per TF (15m/1h), both recon
years, at X=0.5%. n + unresolved. Usage: python study/prior_day_reclaim.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

X = 0.005                                                     # +/- 0.5% symmetric barrier
HB = {"1m": 480, "5m": 96, "15m": 48, "1h": 12, "4h": 3}     # ~half-day forward horizon per TF


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])
    day = (ST // 86400).astype(np.int64)
    H = HB.get(tf, 48)

    # per-day high/low -> prior-day levels
    dh = {}; dl = {}
    for i in range(n):
        d = int(day[i])
        dh[d] = Hi[i] if d not in dh else max(dh[d], Hi[i])
        dl[d] = Lo[i] if d not in dl else min(dl[d], Lo[i])
    ds = sorted(dh); prior = {ds[k]: (dh[ds[k - 1]], dl[ds[k - 1]]) for k in range(1, len(ds))}

    def outcome(k, up):
        entry = C[k]; tp = entry * (1 + X) if up else entry * (1 - X); sl = entry * (1 - X) if up else entry * (1 + X)
        for j in range(k + 1, min(n, k + 1 + H)):
            if up:
                if Lo[j] <= sl:
                    return 0
                if Hi[j] >= tp:
                    return 1
            else:
                if Hi[j] >= sl:
                    return 0
                if Lo[j] <= tp:
                    return 1
        return -1

    sig = {"PDH break (L)": [], "PDL break (S)": [], "PDL reclaim (L)": [], "PDH reclaim (S)": []}
    cur = None; above = below = False; brk_up = brk_dn = False   # per-day state
    for i in range(n):
        d = int(day[i])
        if d != cur:
            cur = d; above = below = brk_up = brk_dn = False
        pl = prior.get(d)
        if pl is None or i + 1 >= n:
            continue
        pdh, pdl = pl
        # PDH break (first close above)
        if not brk_up and C[i] > pdh:
            brk_up = True; sig["PDH break (L)"].append((i, True, int(yr[i])))
        # PDL break
        if not brk_dn and C[i] < pdl:
            brk_dn = True; sig["PDL break (S)"].append((i, False, int(yr[i])))
        # reclaim needs a prior wick beyond the level THIS day, then a close back across
        if Lo[i] < pdl:
            below = True
        if Hi[i] > pdh:
            above = True
        if below and C[i] > pdl and (i == 0 or C[i - 1] <= pdl):
            sig["PDL reclaim (L)"].append((i, True, int(yr[i])))
        if above and C[i] < pdh and (i == 0 or C[i - 1] >= pdh):
            sig["PDH reclaim (S)"].append((i, False, int(yr[i])))

    print("\n========  TF = %s  (bars=%d, barrier=±%.1f%%, horizon=%d bars)  ========" % (tf, n, X * 100, H), flush=True)
    print("  setup             |            2025            |            2026", flush=True)
    for name, lst in sig.items():
        row = "  %-17s |" % name
        for Y in (2025, 2026):
            res = [outcome(i, up) for (i, up, y) in lst if y == Y]
            res = [r for r in res if r >= 0]
            nn = len([1 for (i, up, y) in lst if y == Y])
            p = 100.0 * np.mean(res) if res else float("nan")
            row += " n=%-4d P(works)=%.1f%% (res %d) |" % (nn, p, len(res))
        print(row, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
