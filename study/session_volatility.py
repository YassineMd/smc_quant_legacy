"""Which trading SESSION is most volatile? (Tokyo / London / New York, UTC windows), WEEKENDS EXCLUDED.

Recon "1h" buckets are constant-VOLUME (median ~36 min), so per-bucket range is confounded by bucket duration
(busy sessions close buckets faster). Two duration-ROBUST measures instead:
  1. SESSION-DAY RANGE%  = (session high - session low) / session open * 100, per (UTC-date, session). This is the
     total price excursion over the wall-clock session window, independent of how many volume-buckets subdivide it.
     PRIMARY volatility proxy.
  2. CLOCK-HOUR bars (buckets grouped by UTC (date, hour) -> OHLC): mean hour-range% + mean |hourly return|%, one
     observation per clock-hour regardless of bucket count -> the hour-of-day shape.

Sessions (UTC, same as the Session Filter overlay): Tokyo [00,08)  London [08,16)  New York [13,21).
London & NY overlap 13-16 (those hours count for both). Weekends = UTC Sat/Sun, excluded by the session-day's date.

Run: python study/session_volatility.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L

rng = np.random.default_rng(20260804)
F = L.load_features("1h")
st = F["start"]; H = F["h"]; Lo = F["l"]; O = F["o"]; C = F["c"]; n = F["n"]
# UTC date-ordinal / hour / weekday straight from start_time (don't trust cached tz)
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])   # 0=Mon..6=Sun
dord = np.array([d.toordinal() for d in dts]); yr = np.array([d.year for d in dts])
wkday = dow < 5                                                                       # exclude Sat(5)/Sun(6)

SESSIONS = (("Tokyo", 0, 8), ("London", 8, 16), ("New York", 13, 21))


def session_day_ranges(mask):
    """Per (date, session) total range% = (maxH - minL)/firstO*100, over buckets in the mask. Returns {sess: [..]}."""
    out = {s: [] for s, _, _ in SESSIONS}
    order = np.argsort(st)                                    # chronological (first-open needs time order)
    for name, h0, h1 in SESSIONS:
        sel = mask & (hour >= h0) & (hour < h1)
        days = {}
        for i in order:
            if not sel[i]:
                continue
            days.setdefault(dord[i], []).append(i)
        for _d, idxs in days.items():
            hh = max(H[k] for k in idxs); ll = min(Lo[k] for k in idxs); oo = O[idxs[0]]
            if oo > 0 and hh > ll:
                out[name].append((hh - ll) / oo * 100.0)
    return out


def boot_ci(a, B=10000):
    a = np.asarray(a, float)
    if len(a) == 0:
        return (float("nan"),) * 3
    m = np.array([rng.choice(a, size=len(a), replace=True).mean() for _ in range(B)])
    return a.mean(), np.percentile(m, 2.5), np.percentile(m, 97.5)


def mannwhitney_p(a, b):
    """Two-sided Mann-Whitney U -> normal-approx p (large n). Tie-uncorrected (fine for continuous range%)."""
    a = np.asarray(a); b = np.asarray(b); na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan")
    allv = np.concatenate([a, b]); rank = allv.argsort().argsort() + 1.0
    Ua = rank[:na].sum() - na * (na + 1) / 2.0
    mu = na * nb / 2.0; sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    if sd == 0:
        return 1.0
    z = (Ua - mu) / sd
    return math.erfc(abs(z) / math.sqrt(2.0))


print("=" * 100)
print("SESSION VOLATILITY on 18-mo recon 1h (weekends UTC Sat/Sun EXCLUDED)  |  %d buckets, %d weekday"
      % (n, int(wkday.sum())))
print("  Tokyo [00,08)  London [08,16)  New York [13,21) UTC (London/NY overlap 13-16).  Metric = duration-ROBUST.")
print("=" * 100)

# ---- PRIMARY: session-day total range% ----
sd = session_day_ranges(wkday)
print("\n[1] SESSION-DAY RANGE%%  = (sessionHigh - sessionLow)/sessionOpen*100  (one value per weekday session-day)")
rows = []
for name, _, _ in SESSIONS:
    a = np.array(sd[name]); m, lo, hi = boot_ci(a)
    rows.append((name, a, m))
    print("  %-9s  n=%4d   mean %5.2f%%  [95%% CI %5.2f, %5.2f]   median %5.2f%%   std %5.2f"
          % (name, len(a), m, lo, hi, np.median(a), a.std()))
rows.sort(key=lambda r: -r[2])
print("  RANK: " + "  >  ".join("%s %.2f%%" % (r[0], r[2]) for r in rows))
top, second = rows[0], rows[1]
p = mannwhitney_p(top[1], second[1])
dm = top[2] - second[2]
print("  TOP = %s.  vs %s: +%.2fpp (%.0f%% higher)   Mann-Whitney p=%.4f  -> %s"
      % (top[0], second[0], dm, dm / second[2] * 100, p, "SIGNIFICANT" if p < 0.05 else "not significant"))

# ---- year split (robustness) ----
print("\n[2] BY YEAR (mean session-day range%):")
for y in (2025, 2026):
    sdy = session_day_ranges(wkday & (yr == y))
    line = "  %d:  " % y + "   ".join("%-9s %5.2f%% (n=%d)" % (s, (np.mean(sdy[s]) if sdy[s] else float('nan')), len(sdy[s])) for s, _, _ in SESSIONS)
    print(line)

# ---- CLOCK-HOUR bars: hour-of-day shape + per-session cross-check ----
def hour_bars(mask):
    bars = {}                                                # (date, hour) -> [o, h, l, c, first_st]
    order = np.argsort(st)
    for i in order:
        if not mask[i]:
            continue
        k = (dord[i], hour[i]); b = bars.get(k)
        if b is None:
            bars[k] = [O[i], H[i], Lo[i], C[i]]
        else:
            b[1] = max(b[1], H[i]); b[2] = min(b[2], Lo[i]); b[3] = C[i]
    return bars

bars = hour_bars(wkday)
hr_rng = {h: [] for h in range(24)}; hr_ret = {h: [] for h in range(24)}
for (d, h), (o, hh, ll, c) in bars.items():
    if o > 0 and c > 0:
        hr_rng[h].append((hh - ll) / c * 100.0); hr_ret[h].append(abs(math.log(c / o)) * 100.0)
print("\n[3] HOUR-OF-DAY mean range%% (clock-hour bars, weekday) — the shape:")
mx = max((np.mean(hr_rng[h]) for h in range(24) if hr_rng[h]), default=1.0)
for h in range(24):
    if not hr_rng[h]:
        continue
    v = np.mean(hr_rng[h]); bar = "#" * int(round(v / mx * 40))
    tag = "".join(s[0] for s, a, b in SESSIONS if a <= h < b)   # which session(s) own this hour
    print("  %02d:00  %5.3f%%  %-3s |%s" % (h, v, tag, bar))

print("\n[4] CLOCK-HOUR cross-check (duration-robust): mean hour-range%% + mean |hourly log-ret|%% per session")
for name, h0, h1 in SESSIONS:
    rr = [v for h in range(h0, h1) for v in hr_rng[h]]; rt = [v for h in range(h0, h1) for v in hr_ret[h]]
    print("  %-9s  hour-range %5.3f%%   |ret| %5.3f%%   (n_hours=%d)" % (name, np.mean(rr), np.mean(rt), len(rr)))
print("=" * 100)
