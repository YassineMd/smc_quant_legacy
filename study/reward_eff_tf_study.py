"""PER-TIMEFRAME reward/eff study driver:  python study/reward_eff_tf_study.py <tf>

For ONE timeframe answers, both recon years:
  A. WINDOW reliability vs window size N (bars): flip rate (lower=stabler), mean run length (persistence), agreement
     with the ensemble consensus, decisiveness. -> which rolling "Last N" rows are worth showing on this TF.
  B. CALENDAR-ANCHOR reliability (day / week / month) + how many bars each period spans on this TF -> which anchor
     ("Today", "This week", ...) is meaningful here (the user's 4h-needs-weekly hypothesis).
  C. FORWARD edge: is being WITH the rewarded side tradeable on this TF? directional edge (bps) at several bar
     horizons + a symmetric 1:1 barrier win-rate, PER YEAR. Sign-consistent both years & above fee = real.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study import reward_eff_lib as L

np.seterr(divide="ignore", invalid="ignore")
TF = sys.argv[1] if len(sys.argv) > 1 else "4h"
d = L.load_arrays(TF)
cum = L._cum(d)
n = len(d["c"])
bp = L.BARS_PER[TF]
HOR = [1, 3, 6, 12, 24]
SAMPLE = max(1, n // 120000)                       # keep the barrier loop bounded on 1m

# median |bar return| -> a TF-scaled barrier (TP/SL ~ 3 typical bars), so win-rates compare across TFs
o, c = d["o"], d["c"]
barret = np.abs(np.where(o > 0, (c - o) / o, 0.0))
F = float(max(np.median(barret[barret > 0]) * 3.0, 0.0015))
KBAR = 24

print("=" * 96)
print("TF %s : %d bars  (2025=%d / 2026=%d)   bars/period: session=%d day=%d week=%d month=%d"
      % (TF, n, int((d["yr"] == 2025).sum()), int((d["yr"] == 2026).sum()),
         bp["session"], bp["day"], bp["week"], bp["month"]))
print("barrier f=%.2f%% (=3x median bar range), K=%d bars, sample=1/%d" % (F * 100, KBAR, SAMPLE))
print("=" * 96)

# ---- window shares + ensemble ----
NS = [5, 10, 15, 20, 30, 50, 75, 100, 150, 200]
NS = [W for W in NS if W < n // 3]
shares = {W: L.roll_share(d, W, cum) for W in NS}
ENS_SET = [W for W in (20, 30, 50, 75) if W in shares]
SM = np.vstack([np.sign(shares[W] - 50) for W in ENS_SET])
ens = np.sign(np.nansum(SM, axis=0))


def fwd_line(direction, valid, tag):
    r = L.fwd_and_barrier({"c": d["c"], "h": d["h"], "l": d["l"], "yr": d["yr"]},
                          np.where(valid, direction, 0.0), HOR, F, KBAR, sample=SAMPLE)
    for Y in (2025, 2026):
        nn, edge, win = r[Y]
        es = "  ".join("h%-2d %+5.1f" % (k, edge[k]) for k in HOR)
        print("      %-16s %d  n=%-7d  %s   win=%.1f%%" % (tag, Y, nn, es, win))
    return r


print("\n[A] WINDOW RELIABILITY  (flip lower=stabler | persist=run bars | agree=w/ensemble | decisive=|share-50|)")
for W in NS:
    s = shares[W]; v = ~np.isnan(s); side = np.where(v, np.sign(s - 50), 0)
    flip, persist = L.reliability(side, v)
    agree = float(np.mean(side[v] == ens[v])) if v.any() else float("nan")
    dec = float(np.nanmean(np.abs(s - 50)))
    realtime = W * {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}[TF]
    print("  N=%-3d (~%5dmin) flip=%.3f persist=%5.1f agree=%.3f decisive=%4.1f"
          % (W, realtime, flip, persist, agree, dec))

print("\n[A-fwd] FORWARD EDGE by window  (edge=dir*fwd_ret bps; win=1:1 barrier; want same SIGN both yrs & >fee ~%.0fbps)"
      % (F * 2 * 1e4 * 0 + 4))
for W in NS:
    s = shares[W]
    fwd_line(np.sign(s - 50), ~np.isnan(s), "with-reward N=%d" % W)

print("\n[B] CALENDAR ANCHORS  (share since period start; reliability + forward)")
for period in ("day", "week", "month"):
    s = L.anchor_share(d, period, cum)
    v = ~np.isnan(s); side = np.where(v, np.sign(s - 50), 0)
    flip, persist = L.reliability(side, v)
    dec = float(np.nanmean(np.abs(s - 50)))
    print("  %-6s (~%5d bars/period) flip=%.3f persist=%5.1f decisive=%4.1f"
          % (period, bp[period], flip, persist, dec))
    fwd_line(np.sign(s - 50), v, "with-%s" % period)

print("\n[C] CONSENSUS (median of ensemble windows %s) reliability + forward" % ENS_SET)
med = np.nanmedian(np.vstack([shares[W] for W in ENS_SET]), axis=0)
v = ~np.isnan(med); side = np.where(v, np.sign(med - 50), 0)
flip, persist = L.reliability(side, v)
agree = float(np.mean(side[v] == ens[v]))
print("  median flip=%.3f persist=%5.1f agree=%.3f" % (flip, persist, agree))
fwd_line(np.sign(med - 50), v, "with-consensus")
print("\n[done TF %s]" % TF)
