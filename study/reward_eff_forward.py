"""Is the CURRENTLY-rewarded reward/eff side a tradeable BUY/SELL decision? i.e. does being on the side that is being
rewarded NOW make the NEXT move go that way (so a TP is hit more often than an SL)?

CAUSAL forward test on 5m recon, both years:
  * at bar i, reward/eff BUY share over a trailing window W (ends at i, no look-ahead).
  * direction d = +1 if buy-dominant / -1 if sell-dominant.
  * measure the FORWARD directional edge  d * (close[i+k]/close[i]-1)  at several horizons (bps), and a symmetric
    1:1 barrier win-rate (TP/SL = +/-f over the next K bars). Edge>0 both years = continuation (be WITH the rewarded
    side); Edge<0 = reversion (FADE it); ~0 = no forward edge (descriptive only).
Baselines: raw price momentum sign(close[i]-close[i-W]); tiers basic/strong/very-strong (>62 / >70 dominance).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
dp = np.where(O > 0, (C - O) / O, 0.0)
UP = np.where(dp > 0, dp, 0.0); DN = np.where(dp < 0, -dp, 0.0)
cUP = np.concatenate([[0.0], np.cumsum(UP)]); cDN = np.concatenate([[0.0], np.cumsum(DN)])
cBV = np.concatenate([[0.0], np.cumsum(BV)]); cSV = np.concatenate([[0.0], np.cumsum(SV)])


def share_series(W):
    """Trailing-W buy share of reward-per-effort at each bar i (causal), NaN during warm-up."""
    s = np.full(n, np.nan)
    for i in range(W - 1, n):
        j = i - W + 1
        rb = (cUP[i + 1] - cUP[j]); rs = (cDN[i + 1] - cDN[j])
        eb = (cBV[i + 1] - cBV[j]); es = (cSV[i + 1] - cSV[j])
        rpeb = rb / eb if eb > 0 else 0.0; rpes = rs / es if es > 0 else 0.0
        t = rpeb + rpes
        if t > 0:
            s[i] = 100.0 * rpeb / t
    return s


HOR = [1, 3, 6, 12, 24]
print("bars=%d  2025=%d 2026=%d\n" % (n, int((YR == 2025).sum()), int((YR == 2026).sum())), flush=True)


def fwd_edge(sig_dir, mask, tag):
    """sig_dir: +1/-1/0 per bar. Report d*fwd_return (bps) at each horizon, per year, on `mask` bars."""
    for yr in (2025, 2026):
        ym = mask & (YR == yr) & (sig_dir != 0)
        row = []
        for k in HOR:
            idx = np.where(ym[:n - k])[0]
            if len(idx) == 0:
                row.append("  h%-2d n=0" % k); continue
            ret = sig_dir[idx] * (C[idx + k] / C[idx] - 1.0) * 1e4   # bps
            row.append("h%-2d %+5.1f" % (k, ret.mean()))
        print("  %-22s %d  n=%-6d  %s" % (tag, yr, int(ym.sum()), "  ".join(row)), flush=True)


def barrier_win(sig_dir, mask, f, K, tag):
    """Symmetric 1:1 barrier: TP/SL = +/-f in the signal direction, first-passage over next K bars. Win% per year."""
    for yr in (2025, 2026):
        ym = mask & (YR == yr) & (sig_dir != 0)
        idx = np.where(ym)[0]; idx = idx[idx + K < n]
        w = l = 0
        for i in idx:
            d = sig_dir[i]; e = C[i]
            tp = e * (1 + d * f); sl = e * (1 - d * f)
            for k in range(i + 1, i + 1 + K):
                hi_tp = (H[k] >= tp) if d > 0 else (L[k] <= tp)
                hi_sl = (L[k] <= sl) if d > 0 else (H[k] >= sl)
                if hi_sl:
                    l += 1; break
                if hi_tp:
                    w += 1; break
        tot = w + l
        print("  %-22s %d  n=%-6d  win=%.1f%%  (f=%.1f%% K=%d, 1:1 -> 50%% base)"
              % (tag, yr, tot, 100 * w / max(1, tot), f * 100, K), flush=True)


print("=== FORWARD DIRECTIONAL EDGE  d * fwd_return (bps), by window ===", flush=True)
shares = {W: share_series(W) for W in (10, 20, 30, 50)}
for W in (10, 20, 30, 50):
    s = shares[W]
    d = np.where(np.isnan(s), 0, np.where(s > 50, 1, np.where(s < 50, -1, 0))).astype(float)
    fwd_edge(d, ~np.isnan(s), "rewarded-side W=%d" % W)
print(flush=True)

print("=== BASELINE: raw price momentum  sign(close[i]-close[i-W]) ===", flush=True)
for W in (10, 20, 30, 50):
    d = np.zeros(n)
    d[W:] = np.sign(C[W:] - C[:-W])
    fwd_edge(d, np.arange(n) >= W, "raw-momentum W=%d" % W)
print(flush=True)

print("=== TIERS (W=20): does STRONGER dominance predict better? ===", flush=True)
s = shares[20]; dom = np.where(np.isnan(s), np.nan, np.maximum(s, 100 - s))
d20 = np.where(np.isnan(s), 0, np.where(s > 50, 1, -1)).astype(float)
fwd_edge(d20, (~np.isnan(s)) & (dom < 62), "basic  (<62) W=20")
fwd_edge(d20, (~np.isnan(s)) & (dom >= 62) & (dom < 70), "strong (62-70) W=20")
fwd_edge(d20, (~np.isnan(s)) & (dom >= 70), "vstrong(>=70) W=20")
print(flush=True)

print("=== BARRIER WIN-RATE (be WITH the rewarded side, symmetric 1:1) ===", flush=True)
for W in (10, 20, 30):
    s = shares[W]
    d = np.where(np.isnan(s), 0, np.where(s > 50, 1, np.where(s < 50, -1, 0))).astype(float)
    barrier_win(d, ~np.isnan(s), 0.003, 24, "with-reward W=%d" % W)
