"""BURST-GAP STUDY (2026-09-07, user: "if I place a big order it wouldn't take 1 s to eat through the book, rather ms").
Question: when a big player eats through the book, do the follow-up same-side fills land within milliseconds or spread
over a second (the 1 s burst rule of the Big Player diamonds / the tablet's merged tape)? Raw SOLUSDT USD-M aggTrades
from Binance REST (public, newest N pages; cached next to this file as aggtrades_solusdt.npz). DESCRIPTIVE only.

RESULT (400 pages = 400k trades, 35.1 h, 09-05 23:19 -> 09-07 10:27 UTC). The numbers point at 10 ms; the USER set
BIGPLAYER_BURST_MS / tablet MERGE_MS = 2 (his call):
    SOLUSDT aggTrades: n=400000  span 35.1 h  (09-05 23:19 -> 09-07 10:27 UTC)
    
    1) level-eating same-side pairs: n=41170 (10.3% of trades)
       gap                      pairs   share USD share
       same ms (one order)      10071   24.5%     48.7%
       1 ms                      3524    8.6%     14.4%
       2-10 ms                   8716   21.2%     19.0%
       11-50 ms                  6573   16.0%      8.6%
       51-100 ms                 3876    9.4%      4.4%
       101-300 ms                5022   12.2%      3.1%
       301-1000 ms               2902    7.0%      1.6%
       > 1 s                      486    1.2%      0.2%
       ... pairs whose 2nd fill is >= $50K: n=5496 -> same ms 57.0%, <=1 ms 72.9%, <=10 ms 88.3%, <=100 ms 97.7%, <=1 s 99.9%
    
    2) diamonds (chains >= 2 fills spanning >= 1 tick) by merge window:
       window      >=$50K  >=$100K  >=$500K  USD>=$100K   med fills | max intra-chain gap of the >= $100K ones: same-ms / <=1 / <=10 / <=100 ms
       0 ms          3569     2175      178      539.2M           3 | 100.0%  100.0%  100.0%   100.0%
       1 ms          4095     2487      284      679.4M           3 |  46.9%  100.0%  100.0%   100.0%
       10 ms         6528     3649      389      958.6M           3 |  22.8%   43.2%  100.0%   100.0%
       50 ms         7757     4132      454     1082.5M           3 |  17.7%   33.8%   72.2%   100.0%
       100 ms        8559     4466      466     1151.3M           3 |  15.7%   29.7%   62.7%   100.0%
       300 ms        9456     4833      473     1218.3M           4 |  10.2%   19.5%   40.8%    63.8%
       1000 ms      10103     5111      478     1270.2M           4 |   7.5%   14.5%   30.6%    47.4%
    
    3) 1 s chains >= $100K: n=5111; span (first->last fill) median 145 ms, p75 580 ms, p90 1191 ms; 7.5% are a single ms

Reading: a single order is atomic (one ms) = 49% of the level-eating $; the 1-10 ms tail (33% of $) is ~10x denser per
ms than anything beyond 50 ms (background of independent participants), so it is the same burst phenomenon; a 1 ms
window sits at the clock's own resolution and adds little over "same ms" (2175 -> 2487 diamonds >= $100K vs 3649 at
10 ms); the 1 s rule's >= $100K diamonds contained a > 100 ms gap 53% of the time = separate orders glued together.
Run: python study/burst_gap_study.py [pages]
"""
import os, sys, time, json
import numpy as np, requests

OUT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(OUT, "aggtrades_solusdt.npz")   # gitignored cache of the fetched pages
URL = "https://fapi.binance.com/fapi/v1/aggTrades"
PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 400
TICK = 0.01

if os.path.exists(CACHE):
    z = np.load(CACHE); ts, px, qty, buy = z["ts"], z["px"], z["qty"], z["buy"]
else:
    s = requests.Session()
    last = s.get(URL, params={"symbol": "SOLUSDT", "limit": 1}, timeout=10).json()[-1]["a"]
    rows = []
    for k in range(PAGES):
        fid = last - 1000 * (k + 1) + 1
        for attempt in range(4):
            try:
                r = s.get(URL, params={"symbol": "SOLUSDT", "fromId": fid, "limit": 1000}, timeout=10)
                if r.status_code == 200:
                    rows.extend(r.json()); break
                time.sleep(2.0)
            except Exception:
                time.sleep(2.0)
        time.sleep(0.45)
        if k % 50 == 49:
            print("  fetched %d pages" % (k + 1), flush=True)
    rows.sort(key=lambda d: d["a"])
    ts = np.array([d["T"] for d in rows], dtype=np.int64)
    px = np.array([float(d["p"]) for d in rows]); qty = np.array([float(d["q"]) for d in rows])
    buy = np.array([0 if d["m"] else 1 for d in rows], dtype=np.int8)      # m = buyer is maker -> aggressor SOLD
    np.savez(CACHE, ts=ts, px=px, qty=qty, buy=buy)

usd = px * qty
n = len(ts)
print("SOLUSDT aggTrades: n=%d  span %.1f h  (%s -> %s UTC)" % (n, (ts[-1] - ts[0]) / 3.6e6,
      time.strftime("%m-%d %H:%M", time.gmtime(ts[0] / 1000)), time.strftime("%m-%d %H:%M", time.gmtime(ts[-1] / 1000))))

# 1) LEVEL-EATING same-side pairs: consecutive trades, same aggressor side, the 2nd strictly beyond the 1st in the
#    aggressor's direction (= it ate a further level). Gap between them.
gap = np.diff(ts)
same = buy[1:] == buy[:-1]
beyond = np.where(buy[1:] == 1, px[1:] > px[:-1] + TICK / 2, px[1:] < px[:-1] - TICK / 2)
sel = same & beyond
g = gap[sel]; u2 = usd[1:][sel]
edges = [(0, 0, "same ms (one order)"), (1, 1, "1 ms"), (2, 10, "2-10 ms"), (11, 50, "11-50 ms"), (51, 100, "51-100 ms"),
         (101, 300, "101-300 ms"), (301, 1000, "301-1000 ms"), (1001, 10 ** 12, "> 1 s")]
print("\n1) level-eating same-side pairs: n=%d (%.1f%% of trades)" % (len(g), 100.0 * len(g) / n))
print("   %-22s %7s %7s %9s" % ("gap", "pairs", "share", "USD share"))
for lo, hi, lbl in edges:
    m = (g >= lo) & (g <= hi)
    print("   %-22s %7d %6.1f%% %8.1f%%" % (lbl, m.sum(), 100.0 * m.mean(), 100.0 * u2[m].sum() / u2.sum()))
big = u2 >= 50_000
print("   ... pairs whose 2nd fill is >= $50K: n=%d -> same ms %.1f%%, <=1 ms %.1f%%, <=10 ms %.1f%%, <=100 ms %.1f%%, <=1 s %.1f%%" % (
    big.sum(), 100.0 * (g[big] == 0).mean(), 100.0 * (g[big] <= 1).mean(), 100.0 * (g[big] <= 10).mean(), 100.0 * (g[big] <= 100).mean(), 100.0 * (g[big] <= 1000).mean()))

# 2) CHAINS under a merge window W: consecutive same-side fills each within W ms of the previous; a chain with >= 2
#    fills spanning >= 1 tick = a diamond. Count / USD / max intra-chain gap by W, for the >= $50K / $100K / $500K ones.
def chains(W):
    out = []; i = 0
    while i < n:
        j = i; lo = hi = px[i]; u = usd[i]; mg = 0
        while j + 1 < n and buy[j + 1] == buy[i] and ts[j + 1] - ts[j] <= W:
            j += 1; lo = min(lo, px[j]); hi = max(hi, px[j]); u += usd[j]; mg = max(mg, ts[j] - ts[j - 1])
        if j > i and hi - lo > TICK / 2:
            out.append((u, mg, ts[j] - ts[i], j - i + 1, hi - lo))
        i = j + 1
    return np.array(out) if out else np.zeros((0, 5))

print("\n2) diamonds (chains >= 2 fills spanning >= 1 tick) by merge window:")
print("   %-9s %8s %8s %8s %11s %11s | max intra-chain gap of the >= $100K ones: same-ms / <=1 / <=10 / <=100 ms" % (
    "window", ">=$50K", ">=$100K", ">=$500K", "USD>=$100K", "med fills"))
for W in (0, 1, 10, 50, 100, 300, 1000):
    c = chains(W)
    k50 = (c[:, 0] >= 50_000).sum(); k100 = c[:, 0] >= 100_000; k500 = (c[:, 0] >= 500_000).sum()
    mg = c[k100, 1] if k100.any() else np.zeros(0)
    f = lambda m: 100.0 * m.mean() if len(mg) else 0
    print("   %-9s %8d %8d %8d %10.1fM %11.0f | %5.1f%% %6.1f%% %6.1f%% %7.1f%%" % (
        "%d ms" % W, k50, k100.sum(), k500, c[k100, 0].sum() / 1e6, np.median(c[k100, 3]) if k100.any() else 0,
        f(mg == 0), f(mg <= 1), f(mg <= 10), f(mg <= 100)))

# 3) inside the 1 s chains >= $100K: how much of the USD arrives at the FIRST ms (the atomic order) vs later fills,
#    and the gap from the first ms to the rest
c1 = chains(1000); big1 = c1[c1[:, 0] >= 100_000]
print("\n3) 1 s chains >= $100K: n=%d; span (first->last fill) median %.0f ms, p75 %.0f ms, p90 %.0f ms; %.1f%% are a single ms" % (
    len(big1), np.median(big1[:, 2]) if len(big1) else 0, np.percentile(big1[:, 2], 75) if len(big1) else 0,
    np.percentile(big1[:, 2], 90) if len(big1) else 0, 100.0 * (big1[:, 2] == 0).mean() if len(big1) else 0))
print("DONE")
