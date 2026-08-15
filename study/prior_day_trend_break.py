"""Does a prior-day-level BREAK continue only when it ALIGNS with the multi-day trend? (Follow-up to prior_day_reclaim:
PDH breaks led up in 2025 / died 2026; PDL breaks faded -- smells like regime.) TREND at day D = sign of the 3-day
change of daily closes, ending at the PRIOR day's close (causal, known at D's open). Break = first intraday close beyond
PDH (up) / PDL (down). ALIGNED = break direction == trend; COUNTER = opposite; also NEUTRAL (trend==0 rare).

OUTCOME (symmetric, base=50%): from the break close, does price reach +X% BEFORE −X% in the BREAK direction? If
aligned breaks continue >>50% and counter breaks fade (<50%) in BOTH years, the multi-day trend IS a tradeable daily
bias (trigger = the aligned break). Per TF (15m/1h), both years, X in {0.5%, 1.0%}.
Usage: python study/prior_day_trend_break.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

XS = [0.005, 0.010]
HB = {"5m": 96, "15m": 48, "1h": 12}
TREND_D = 3                                                   # 3-day close momentum


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])
    day = (ST // 86400).astype(np.int64)
    H = HB.get(tf, 48)

    dh = {}; dl = {}; dclose = {}
    for i in range(n):
        d = int(day[i])
        dh[d] = Hi[i] if d not in dh else max(dh[d], Hi[i])
        dl[d] = Lo[i] if d not in dl else min(dl[d], Lo[i])
        dclose[d] = C[i]                                      # last write per day = day's close
    ds = sorted(dh)
    prior = {}; trend = {}
    for k in range(1, len(ds)):
        d = ds[k]; prior[d] = (dh[ds[k - 1]], dl[ds[k - 1]])
        if k > TREND_D:
            ch = dclose[ds[k - 1]] - dclose[ds[k - 1 - TREND_D]]
            trend[d] = 1 if ch > 0 else (-1 if ch < 0 else 0)

    def outcome(k, up, X):
        e = C[k]; tp = e * (1 + X) if up else e * (1 - X); sl = e * (1 - X) if up else e * (1 + X)
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

    breaks = []                                              # (i, up, year, aligned)  aligned in {+1,-1,0}
    cur = None; bu = bd = False
    for i in range(n):
        d = int(day[i])
        if d != cur:
            cur = d; bu = bd = False
        pl = prior.get(d); tr = trend.get(d)
        if pl is None or tr is None or i + 1 >= n:
            continue
        pdh, pdl = pl
        if not bu and C[i] > pdh:
            bu = True; breaks.append((i, True, int(yr[i]), 1 if tr == 1 else (-1 if tr == -1 else 0)))
        if not bd and C[i] < pdl:
            bd = True; breaks.append((i, False, int(yr[i]), 1 if tr == -1 else (-1 if tr == 1 else 0)))

    print("\n========  TF = %s  (bars=%d, horizon=%d, trend=%dd)  ========" % (tf, n, H, TREND_D), flush=True)
    for X in XS:
        print("  ---- barrier ±%.1f%% ----" % (X * 100), flush=True)
        for al, tag in ((1, "ALIGNED  (break w/ trend)"), (-1, "COUNTER  (break vs trend)")):
            row = "    %-26s |" % tag
            for Y in (2025, 2026):
                res = [outcome(i, up, X) for (i, up, y, a) in breaks if y == Y and a == al]
                res = [r for r in res if r >= 0]
                nn = sum(1 for (i, up, y, a) in breaks if y == Y and a == al)
                p = 100.0 * np.mean(res) if res else float("nan")
                row += " %d: n=%-4d P=%.1f%% (res %d) |" % (Y, nn, p, len(res))
            print(row, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
