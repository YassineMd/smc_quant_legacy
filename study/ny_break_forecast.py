"""NY range-break SIDE forecast test.
Hypothesis: the DIRECTION of the NY 2-5pm range break can be forecast if the 1h Easy 0.5%, 1h Engulf S/R, or 1h
Reversal strategy PRINTS a signal from 15:30 Morocco (14:30 UTC) onward but BEFORE the break. For each break day we
take the FIRST qualifying signal per strategy and ask: does its side == the break side?
Null = the base rate of the break side (always guess the majority side).  Weekends already excluded by the detector.
Run: python study/ny_break_forecast.py     (SIG_FROM=14:30 default 15:30-Morocco; SIG_FROM=15:30 for a UTC reading)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from scipy import stats as _st
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB
from app import easy1h_detect, engulf_sr_detect, reversal_detect

F = L.load_features("1h"); A = F["A"]; startt = [float(t) for t in F["start"]]
SIG_FROM = os.environ.get("SIG_FROM", "14:30"); SH, SM = (int(x) for x in SIG_FROM.split(":"))
SIG_TO = os.environ.get("SIG_TO", "break")               # "break" (up to the break candle) | "HH:MM" UTC cap (in-range only)


def fired(mod, name):
    try:
        sigs = mod.detect(A, skip_last=False)
    except Exception as e:
        print("  !! %s.detect failed: %s" % (name, e)); return {}
    out = {}
    for e in sigs:
        try:
            out[int(e["i"])] = int(e["side"])
        except Exception:
            continue
    return out


DETS = {"easy1h": fired(easy1h_detect, "easy1h"),
        "engulfsr": fired(engulf_sr_detect, "engulfsr"),
        "reversal": fired(reversal_detect, "reversal")}
for k, v in DETS.items():
    print("  detector %-9s fired %d times total" % (k, len(v)))

ranges = [r for r in RB.detect(A) if r["side"] != 0 and r["break_i"] is not None]

rows = []
for r in ranges:
    bi = int(r["break_i"]); bside = int(r["side"])
    t = datetime.fromtimestamp(startt[bi], tz=timezone.utc)
    sigfrom_ts = datetime(t.year, t.month, t.day, SH, SM, tzinfo=timezone.utc).timestamp()
    brk_ts = startt[bi]; yr = t.year
    if SIG_TO != "break":                                # cap the window at a UTC time (e.g. 16:00 -> in-range only)
        eh, em = (int(x) for x in SIG_TO.split(":"))
        win_end = min(brk_ts, datetime(t.year, t.month, t.day, eh, em, tzinfo=timezone.utc).timestamp())
    else:
        win_end = brk_ts
    rec = {"bside": bside, "yr": yr}
    allsig = []                                          # (idx, side) across all detectors, in the window
    for name, fmap in DETS.items():
        best = None
        for j, side in fmap.items():
            if j < bi and sigfrom_ts <= startt[j] < win_end:
                if best is None or j < best[0]:
                    best = (j, side)
                allsig.append((j, side))
        rec[name] = best[1] if best else None
    rec["any"] = min(allsig, key=lambda z: z[0])[1] if allsig else None    # earliest signal across all three
    rows.append(rec)

nb = len(rows); nshort = sum(1 for r in rows if r["bside"] < 0)
base = max(nshort, nb - nshort) / nb * 100.0                                # always-guess-majority accuracy
print("=" * 96)
print("NY BREAK-SIDE FORECAST | signals from %s UTC (=%02d:%02d Morocco) before the break | 1h recon"
      % (SIG_FROM, (SH + 1) % 24, SM))
print("  break days=%d  (short %d / long %d)  ->  majority-guess base rate %.1f%%" % (nb, nshort, nb - nshort, base))
print("=" * 96)


def rep(name, subset=None):
    rs = rows if subset is None else [r for r in rows if subset(r)]
    hits = [r for r in rs if r[name] is not None]
    k = len(hits)
    if k == 0:
        print("  %-10s n=0" % name); return
    correct = sum(1 for r in hits if r[name] == r["bside"])
    rate = 100.0 * correct / k
    p = _st.binomtest(correct, k, 0.5, alternative="greater").pvalue
    cov = 100.0 * k / len(rs)
    print("  %-10s n=%3d (cov %4.1f%%)  correct %4.1f%%  (p=%.3f vs 50%%)%s"
          % (name, k, cov, rate, p, "  << beats base" if rate > base else ""))


for nm in ("easy1h", "engulfsr", "reversal", "any"):
    rep(nm)
print("-" * 96)
print("  by year (ANY signal):")
rep("any", lambda r: r["yr"] == 2025)
rep("any", lambda r: r["yr"] == 2026)
print("=" * 96)
