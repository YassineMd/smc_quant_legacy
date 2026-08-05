"""SESSION LEADER: per weekday day, does an EARLIER session's direction get FOLLOWED by the later sessions?
(e.g. Tokyo up -> London up -> NY up => Tokyo led the day.)  WEEKENDS (UTC Sat/Sun) EXCLUDED.

Day split into 3 consecutive NON-OVERLAPPING UTC blocks so the lead-follow is genuine (not a shared-hours artifact
of the overlapping Session-Filter windows): Tokyo/Asian [00,08)  London/European [08,13)  New York/US [13,21).
Per block: dir = sign(blockClose - blockOpen)  (open = first bucket's open, close = last bucket's close).

Reports (weekday-complete days only):
  [1] marginal up-rates per session (the drift baseline)
  [2] FORWARD ASSOCIATION per ordered pair: raw agreement %, chance % (from marginals), excess, phi + chi-sq p
      -> phi>0 & significant = a REAL lead beyond drift.
  [3] CONDITIONAL follow rates: P(later up | earlier up), P(later dn | earlier dn)
  [4] per-day LEADER attribution: leader = the EARLIEST block whose dir == the day's net dir (Tokyo-open->NY-close);
      also % all-3-agree "trend days".  (NB: this metric mechanically favours Tokyo = first mover.)
  [5] year split.
Run: python study/session_leader.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L

F = L.load_features("1h")
st = F["start"]; O = F["o"]; C = F["c"]; n = F["n"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts]); yr = np.array([d.year for d in dts])

BLOCKS = (("Tokyo", 0, 8), ("London", 8, 13), ("New York", 13, 21))
NAMES = [b[0] for b in BLOCKS]
order = np.argsort(st)


def build_days():
    byday = defaultdict(list)
    for i in order:
        if dow[i] < 5:                                    # weekday only
            byday[dord[i]].append(i)
    days = []
    for d, idxs in byday.items():
        rec = {}; ok = True; tok_open = ny_close = None
        for name, h0, h1 in BLOCKS:
            blk = [i for i in idxs if h0 <= hour[i] < h1]  # idxs already chronological (order-sorted)
            if not blk:
                ok = False; break
            op = O[blk[0]]; cl = C[blk[-1]]
            rec[name] = 1 if cl > op else -1
            if name == "Tokyo":
                tok_open = op
            if name == "New York":
                ny_close = cl
        if not ok:
            continue
        rec["day"] = 1 if ny_close > tok_open else -1     # full-day net: Tokyo open -> NY close
        rec["yr"] = int(yr[idxs[0]])
        days.append(rec)
    return days


def phi_p(a, b):
    """phi coefficient of two +-1 arrays + normal-approx two-sided p (chi-sq df=1)."""
    a = np.asarray(a); b = np.asarray(b); N = len(a)
    n11 = int(np.sum((a > 0) & (b > 0))); n10 = int(np.sum((a > 0) & (b < 0)))
    n01 = int(np.sum((a < 0) & (b > 0))); n00 = int(np.sum((a < 0) & (b < 0)))
    den = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    if den == 0:
        return 0.0, 1.0
    phi = (n11 * n00 - n10 * n01) / den
    z = phi * math.sqrt(N)
    return phi, math.erfc(abs(z) / math.sqrt(2.0))


days = build_days()
D = {nm: np.array([r[nm] for r in days]) for nm in NAMES}
DAY = np.array([r["day"] for r in days])
nd = len(days)

print("=" * 100)
print("SESSION LEADER on 18-mo recon 1h (weekends excluded)  |  %d complete weekday days" % nd)
print("  Non-overlapping UTC blocks: Tokyo [00,08)  London [08,13)  New York [13,21).  dir = close vs open.")
print("=" * 100)

print("\n[1] MARGINAL up-rate (drift baseline):")
for nm in NAMES:
    up = 100.0 * np.mean(D[nm] > 0)
    print("  %-9s  up %5.1f%%   (n=%d)" % (nm, up, nd))
print("  full day (Tokyo open -> NY close)  up %5.1f%%" % (100.0 * np.mean(DAY > 0)))

print("\n[2] FORWARD ASSOCIATION  (does the earlier block's direction associate with the later's, beyond drift?)")
print("  %-18s  agree%%  chance%%  excess   phi    p       verdict" % "pair")
for i, a in enumerate(NAMES):
    for b in NAMES[i + 1:]:
        agree = np.mean(D[a] == D[b])
        pa = np.mean(D[a] > 0); pb = np.mean(D[b] > 0)
        chance = pa * pb + (1 - pa) * (1 - pb)
        phi, p = phi_p(D[a], D[b])
        vd = "REAL lead" if (phi > 0 and p < 0.05) else ("inverse!" if (phi < 0 and p < 0.05) else "~ chance")
        print("  %-18s  %5.1f   %5.1f   %+5.1f   %+.3f  %.4f  %s"
              % ("%s -> %s" % (a, b), agree * 100, chance * 100, (agree - chance) * 100, phi, p, vd))

print("\n[3] CONDITIONAL follow rates:")
for i, a in enumerate(NAMES):
    for b in NAMES[i + 1:]:
        up_up = np.mean(D[b][D[a] > 0] > 0) * 100 if np.any(D[a] > 0) else float("nan")
        dn_dn = np.mean(D[b][D[a] < 0] < 0) * 100 if np.any(D[a] < 0) else float("nan")
        print("  %-9s up  -> %-9s up  %5.1f%%    |    %-9s dn -> %-9s dn %5.1f%%"
              % (a, b, up_up, a, b, dn_dn))

print("\n[4] PER-DAY LEADER = earliest block matching the day's net direction (mechanically favours Tokyo):")
lead = {nm: 0 for nm in NAMES}; allagree = 0
for r in days:
    if r["Tokyo"] == r["London"] == r["New York"]:
        allagree += 1
    for nm in NAMES:                                       # first block in time matching the day dir
        if r[nm] == r["day"]:
            lead[nm] += 1; break
for nm in NAMES:
    print("  %-9s leads %5.1f%%  (n=%d)" % (nm, 100.0 * lead[nm] / nd, lead[nm]))
print("  all-3-agree 'trend' days: %5.1f%%" % (100.0 * allagree / nd))

print("\n[5] BY YEAR — forward association phi (Tokyo->NY, London->NY) + all-agree%%:")
for y in (2025, 2026):
    m = np.array([r["yr"] == y for r in days])
    if m.sum() < 20:
        continue
    tn, ptn = phi_p(D["Tokyo"][m], D["New York"][m])
    ln, pln = phi_p(D["London"][m], D["New York"][m])
    tl, ptl = phi_p(D["Tokyo"][m], D["London"][m])
    aa = 100.0 * np.mean([(r["Tokyo"] == r["London"] == r["New York"]) for r in days if r["yr"] == y])
    print("  %d (n=%d):  T->L phi %+.3f p%.3f   T->N phi %+.3f p%.3f   L->N phi %+.3f p%.3f   all-agree %.0f%%"
          % (y, int(m.sum()), tl, ptl, tn, ptn, ln, pln, aa))
print("=" * 100)
