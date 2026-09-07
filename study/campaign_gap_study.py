"""CAMPAIGN-WINDOW STUDY (2026-09-07, user: "look at the drop-down, somebody was protecting 104.91/92 with a market-order
algo, a fight in milliseconds; how do we take that into account given the 1 ms + directional rule?"). The 12:01:08 raw
tape (Binance aggTrades 11:01:08.633-.698 UTC): 16 consecutive BUYS, $1.37M, 104.91 -> 105.02, zero sells inside; the
buyer took every re-posted ask 4-15 ms after it appeared -- the shipped ORDER rule (1 ms + monotonic) rightly split it
into several orders; the CAMPAIGN tier links them.

ORDER = consecutive same-side fills <= 1 ms apart, monotonic (one atomic order). CAMPAIGN = consecutive same-side orders
whose gap (next first fill - previous last fill) is <= W, refills / reversals allowed, other-side fills in between
allowed. Question: where does the same-side inter-order gap density fall to the background -> W. Uses the cached raw
aggTrades of burst_gap_study.py (aggtrades_solusdt.npz next to this file). DESCRIPTIVE only.

RESULT (400k trades, 35.1 h) -> BIGPLAYER_CAMPAIGN_MS = tablet CAMPAIGN_MS = 30 (the user's hypothesis was 100 ms):
    SOLUSDT aggTrades n=400000, 35.1 h
    orders: 386389 (1.04 fills each on average); 8221 ate >= 1 tick
    mean same-side inter-order gap 655 ms -> background density ~0.00153 per ms per order (memoryless)
    
    2) same-side inter-order gap: share of pairs, density per ms (x background), refill share, crossed share, $ of the next order (>= $50K count)
       gap ms         pairs     share    x-bkgd   refill  crossed next>=50K
       0-1             6429     1.66%      5.4x      98%      88%       541
       2-3             5897     1.53%      5.0x      31%      27%       740
       4-5             3464     0.90%      2.9x      33%      31%       384
       6-10            4755     1.23%      1.6x      39%      40%       499
       11-20           5573     1.44%      0.9x      46%      48%       443
       21-30           3935     1.02%      0.7x      51%      56%       245
       31-50           5946     1.54%      0.5x      57%      63%       354
       51-100         13073     3.38%      0.4x      62%      70%       709
       101-200        66786    17.28%      1.1x      93%      27%      1933
       201-500       107536    27.83%      0.6x      94%      43%      3092
       501-1000       86023    22.26%      0.3x      95%      63%      2228
       >1000          76970    19.92%      0.0x      95%      84%      1881
       next order >= $50K only: pairs 13049 -> share <=10 ms 16.6%, <=20 20.0%, <=30 21.9%, <=50 24.6%, <=100 30.0%, <=300 54.8%
    
    3) campaigns by window: multi-order campaigns >= $100K / >= $500K / >= $1M, $ share of >= $100K campaigns that are multi-order, orders per campaign (p90), refills inside
       W ms        >=100K    >=500K     >=1M   multi $%   orders p90   refill%  span p90 ms
       1              629       143       31      19.6%           12       97%            4
       5             2427       358       90      59.0%            6       51%           10
       10            2843       438      112      67.7%            8       50%           21
       20            3103       497      141      73.1%            9       51%           47
       30            3293       523      161      75.9%           10       51%           67
       50            3517       551      181      78.8%           12       52%          114
       100           4000       593      209      84.3%           13       56%          233
       200           4796       640      236      91.1%           15       75%          658
       500           5538       766      296      97.1%           24       91%         3334
       1000          5656       875      360      99.3%           48       97%        14374

Reading: the reaction band is <= 10 ms (5x background at 2-3 ms, 2.9x at 4-5, 1.6x at 6-10), 11-100 ms is a quiet
trough (0.4-0.9x, the other side trading in between 48-70% of the time), and from 100 ms on the density is back to
the market's normal cadence (1.1x at 101-200 ms: independent orders and algos slicing at 100 ms steps). 30 ms covers
the 12:01:08 fight (inter-order gaps up to 15 ms) with margin and stays inside the trough; 100 ms sits on the edge
where unrelated orders start being glued. One constant per side to change it.
Run: python study/campaign_gap_study.py   (needs study/aggtrades_solusdt.npz from burst_gap_study.py)
"""
import os, sys, numpy as np
SCR = os.path.dirname(os.path.abspath(__file__))
z = np.load(os.path.join(SCR, "aggtrades_solusdt.npz")); ts, px, qty, buy = z["ts"], z["px"], z["qty"], z["buy"]
usd = px * qty; n = len(ts); TICK = 0.01
print("SOLUSDT aggTrades n=%d, %.1f h" % (n, (ts[-1] - ts[0]) / 3.6e6))

# 1) ORDERS (shipped rule: consecutive same-side, <= 1 ms, monotonic) -> arrays per order
o_t0, o_t1, o_side, o_lo, o_hi, o_usd, o_p0, o_n = [], [], [], [], [], [], [], []
i = 0
while i < n:
    j = i; lo = hi = px[i]; u = usd[i]
    while j + 1 < n and buy[j + 1] == buy[i] and ts[j + 1] - ts[j] <= 1 and (px[j + 1] >= px[j] - 1e-9 if buy[i] else px[j + 1] <= px[j] + 1e-9):
        j += 1; lo = min(lo, px[j]); hi = max(hi, px[j]); u += usd[j]
    o_t0.append(ts[i]); o_t1.append(ts[j]); o_side.append(buy[i]); o_lo.append(lo); o_hi.append(hi); o_usd.append(u); o_p0.append(px[i]); o_n.append(j - i + 1)
    i = j + 1
o_t0, o_t1, o_side, o_lo, o_hi, o_usd, o_p0, o_n = map(np.array, (o_t0, o_t1, o_side, o_lo, o_hi, o_usd, o_p0, o_n))
m = len(o_t0)
print("orders: %d (%.2f fills each on average); %d ate >= 1 tick" % (m, n / m, int(((o_hi - o_lo) > TICK / 2).sum())))

# 2) gap to the NEXT SAME-SIDE order (skipping other-side orders); refill flag; other-side-in-between flag
gap = np.full(m, -1, dtype=np.int64); refill = np.zeros(m, bool); crossed = np.zeros(m, bool); nxt = np.full(m, -1)
last = {0: None, 1: None}
for k in range(m):
    s = int(o_side[k]); p = last[s]
    if p is not None:
        gap[p] = o_t0[k] - o_t1[p]; nxt[p] = k
        refill[p] = (o_p0[k] <= o_hi[p] + 1e-9) if s else (o_p0[k] >= o_lo[p] - 1e-9)   # next STARTS inside/below the previous top (mirror)
        crossed[p] = k - p > 1                                                             # an other-side order sat in between
    last[s] = k
ok = gap >= 0
g = gap[ok]; rf = refill[ok]; cr = crossed[ok]; u_next = o_usd[nxt[ok]]
rate = ok.sum() / ((ts[-1] - ts[0]))                                                        # same-side orders per ms overall
print("mean same-side inter-order gap %.0f ms -> background density ~%.5f per ms per order (memoryless)" % (np.mean(g), 1.0 / np.mean(g)))
bands = [(0, 1), (2, 3), (4, 5), (6, 10), (11, 20), (21, 30), (31, 50), (51, 100), (101, 200), (201, 500), (501, 1000), (1001, 10 ** 9)]
print("\n2) same-side inter-order gap: share of pairs, density per ms (x background), refill share, crossed share, $ of the next order (>= $50K count)")
print("   %-12s %7s %9s %9s %8s %8s %9s" % ("gap ms", "pairs", "share", "x-bkgd", "refill", "crossed", "next>=50K"))
bk = 1.0 / np.mean(g)
for lo, hi in bands:
    sel = (g >= lo) & (g <= hi)
    width = hi - lo + 1 if hi < 10 ** 8 else max(1, g.max() - lo + 1)
    dens = sel.mean() / width
    print("   %-12s %7d %8.2f%% %8.1fx %7.0f%% %7.0f%% %9d" % ("%d-%d" % (lo, hi) if hi < 10 ** 8 else ">%d" % (lo - 1), sel.sum(), 100 * sel.mean(), dens / bk,
          100 * rf[sel].mean() if sel.any() else 0, 100 * cr[sel].mean() if sel.any() else 0, int((u_next[sel] >= 50_000).sum())))
big = u_next >= 50_000
print("   next order >= $50K only: pairs %d -> share <=10 ms %.1f%%, <=20 %.1f%%, <=30 %.1f%%, <=50 %.1f%%, <=100 %.1f%%, <=300 %.1f%%" % (
    big.sum(), *[100 * (g[big] <= w).mean() for w in (10, 20, 30, 50, 100, 300)]))

# 3) CAMPAIGNS by window W (per side, other-side fills allowed in between): count / $ / orders per campaign / refills inside
def campaigns(W):
    out = []; cur = {0: None, 1: None}                                  # [t0, t1, lo, hi, usd, n_orders, n_refill, p0]
    for k in range(m):
        s = int(o_side[k]); c = cur[s]
        if c is not None and o_t0[k] - c[1] <= W:
            rf_ = (o_p0[k] <= c[3] + 1e-9) if s else (o_p0[k] >= c[2] - 1e-9)
            c[1] = o_t1[k]; c[2] = min(c[2], o_lo[k]); c[3] = max(c[3], o_hi[k]); c[4] += o_usd[k]; c[5] += 1; c[6] += int(rf_)
        else:
            if c is not None: out.append(c)
            cur[s] = [o_t0[k], o_t1[k], o_lo[k], o_hi[k], o_usd[k], 1, 0, o_p0[k]]
    for s in (0, 1):
        if cur[s] is not None: out.append(cur[s])
    return np.array(out, dtype=float)
print("\n3) campaigns by window: multi-order campaigns >= $100K / >= $500K / >= $1M, $ share of >= $100K campaigns that are multi-order, orders per campaign (p90), refills inside")
print("   %-8s %9s %9s %8s %10s %12s %9s %12s" % ("W ms", ">=100K", ">=500K", ">=1M", "multi $%", "orders p90", "refill%", "span p90 ms"))
for W in (1, 5, 10, 20, 30, 50, 100, 200, 500, 1000):
    c = campaigns(W); multi = c[:, 5] >= 2; k100 = c[:, 4] >= 100_000
    mm = multi & k100
    print("   %-8d %9d %9d %8d %9.1f%% %12.0f %8.0f%% %12.0f" % (W, mm.sum(), (multi & (c[:, 4] >= 500_000)).sum(), (multi & (c[:, 4] >= 1e6)).sum(),
          100 * c[mm, 4].sum() / max(1.0, c[k100, 4].sum()), np.percentile(c[mm, 5], 90) if mm.any() else 0,
          100 * (c[mm, 6] > 0).mean() if mm.any() else 0, np.percentile(c[mm, 1] - c[mm, 0], 90) if mm.any() else 0))
print("DONE")
