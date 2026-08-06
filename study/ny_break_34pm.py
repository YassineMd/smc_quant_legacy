"""NY break-side forecast from the 3-4pm Morocco candle (14:00-15:00 UTC = the MIDDLE hour of the 2-5pm range).
Hypothesis: that hour's direction predicts the break side.  close > open -> expect brB (long) ; close < open -> brS.
The hour-H candle: open = first bucket's open in hour H, close = last bucket's close (clock-hour candle). Causal — it
closes at 4pm, before the range completes (5pm) and well before the break (>5pm). Null = majority-break base rate.
Run: python study/ny_break_34pm.py     (HOUR=14 default; sweep prints 13/14/15 too)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
from scipy import stats as _st
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB

F = L.load_features("1h"); A = F["A"]; O = F["o"]; C = F["c"]; st = [float(t) for t in F["start"]]; n = F["n"]
INVERT = os.environ.get("INVERT") == "1"                      # 1 -> predict the OPPOSITE of the candle direction (fade)
ranges = [r for r in RB.detect(A) if r["side"] != 0 and r["break_i"] is not None]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    days[t.date()].append((t.hour, i))
for d in days:
    days[d].sort(key=lambda z: z[1])

nb = len(ranges); nshort = sum(1 for r in ranges if r["side"] < 0)
base = max(nshort, nb - nshort) / nb * 100.0


def test(HOUR, label):
    rows = []
    for r in ranges:
        bi = int(r["break_i"]); bside = int(r["side"])
        d = datetime.fromtimestamp(st[bi], tz=timezone.utc).date()
        hh = [i for (h, i) in days[d] if h == HOUR]
        if not hh:
            continue
        o = float(O[hh[0]]); c = float(C[hh[-1]])
        if o <= 0 or c <= 0 or c == o:
            continue
        pred = 1 if c > o else -1
        if INVERT:                                            # fade: expect the break OPPOSITE the candle
            pred = -pred
        rows.append((pred, bside, datetime.fromtimestamp(st[bi], tz=timezone.utc).year))
    k = len(rows)
    if k == 0:
        print("  %-22s n=0" % label); return
    correct = sum(1 for p, b, _ in rows if p == b)
    rate = 100.0 * correct / k
    p = _st.binomtest(correct, k, 0.5, alternative="greater").pvalue
    y25 = [(p_, b) for p_, b, y in rows if y == 2025]; y26 = [(p_, b) for p_, b, y in rows if y == 2026]
    r25 = 100.0 * sum(1 for p_, b in y25 if p_ == b) / len(y25) if y25 else 0
    r26 = 100.0 * sum(1 for p_, b in y26 if p_ == b) / len(y26) if y26 else 0
    print("  %-22s n=%3d (cov %4.1f%%)  correct %4.1f%% (p=%.3f)  | 2025 %4.1f%% (n=%d)  2026 %4.1f%% (n=%d)%s"
          % (label, k, 100.0 * k / nb, rate, p, r25, len(y25), r26, len(y26), "  << beats base" if rate > base else ""))


print("=" * 108)
print("NY BREAK-SIDE from the clock-hour candle direction (close vs open) | 1h recon | breaks=%d (short %d/long %d) base %.1f%%"
      % (nb, nshort, nb - nshort, base))
print("=" * 108)
test(14, "3-4pm Morocco (14 UTC)")
print("-- hour sweep (which hour of the 2-5pm range predicts best) --")
test(13, "2-3pm (13 UTC)")
test(14, "3-4pm (14 UTC)")
test(15, "4-5pm (15 UTC)")
print("=" * 108)
