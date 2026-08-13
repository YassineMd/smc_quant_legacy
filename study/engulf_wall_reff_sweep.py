"""15m ENGULFING WALL — does the reward/eff BUYER-vs-SELLER gap separate winners from losers, and what MINIMUM gap
helps? WINNER = price went >= 50% of the way to the TP before touching the SL (softer than full TP; SL-first on a
same-bar tie = conservative).

reward/eff read = FLOW = median of the [20,30,50,75]-bar reward-per-effort buy shares at the signal bar (causal).
ALIGNED GAP = (FLOW-50) for a LONG / (50-FLOW) for a SHORT = how far reward/eff favours the TRADE side, in points.

[A] winners vs losers: mean aligned gap + the % of winners/losers that are reward/eff-aligned.
[B] sweep a MIN aligned-gap threshold: winner% (half-to-TP) + full-TP win% + n, per year. Both recon years, 0.04% RT.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
HORIZON = 96

print("bars=%d  detecting signals..." % n, flush=True)
CH, STEP = 6000, 5000
sigs = {}
c0 = 0
while c0 < n:
    c1 = min(n, c0 + CH); S = A[c0:c1]
    for e in MOM.detect(S, AL.detect(S, skip_last=False), skip_last=False):
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]), e.get("tier", "normal"))
    if c1 >= n:
        break
    c0 += STEP
print("signals=%d" % len(sigs), flush=True)


def flow(i):
    ws = [s for s, ok in (reward_eff.share(A, i - w + 1, i) for w in (20, 30, 50, 75)) if ok]
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def outcome_half(gi, side, entry, sl, tp):
    """WINNER = reach 50%-to-TP before SL (SL-first on same-bar tie). Returns 'W'/'L'/'T'."""
    half = entry + 0.5 * (tp - entry)
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hi_sl = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        hi_hf = (H[k] >= half) if side > 0 else (L[k] <= half)
        if hi_sl:
            return "L"
        if hi_hf:
            return "W"
    return "T"


def outcome_full(gi, side, entry, sl, tp):
    xi = min(n - 1, gi + HORIZON); o = None
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hi_sl = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        hi_tp = (H[k] >= tp) if side > 0 else (L[k] <= tp)
        if hi_sl:
            o = "L"; xi = k; break
        if hi_tp:
            o = "W"; xi = k; break
    exitp = sl if o == "L" else (tp if o == "W" else C[xi])
    return (o or "T"), side * (exitp - entry) / entry - FEE


rows = []                                                 # (yr, side, gap, half_out, full_out, ret, tier)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi)
    if fs is None:
        continue
    gap = (fs - 50.0) if side > 0 else (50.0 - fs)         # aligned reward/eff gap in points
    ho = outcome_half(gi, side, entry, sl, tp)
    fo, ret = outcome_full(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, gap, ho, fo, ret, tier))

print("scored=%d\n" % len(rows), flush=True)
gaps = np.array([r[2] for r in rows]); half = np.array([r[3] for r in rows]); yr = np.array([r[0] for r in rows])

print("=== [A] WINNERS vs LOSERS — aligned reward/eff gap (winner = >=50%-to-TP before SL) ===", flush=True)
for Y in (2025, 2026):
    W = gaps[(yr == Y) & (half == "W")]; Lo = gaps[(yr == Y) & (half == "L")]
    print("  %d  winners: mean gap=%+.1f  (aligned>0: %.0f%%, n=%d) | losers: mean gap=%+.1f  (aligned>0: %.0f%%, n=%d)"
          % (Y, W.mean() if len(W) else 0, 100 * np.mean(W > 0) if len(W) else 0, len(W),
             Lo.mean() if len(Lo) else 0, 100 * np.mean(Lo > 0) if len(Lo) else 0, len(Lo)), flush=True)

print("\n=== [B] MIN aligned-gap threshold sweep (winner = >=50%-to-TP before SL) ===", flush=True)
print("  thr  |     BOTH          |     2025          |     2026          | fullTP-win both | net% both", flush=True)
for T in (-99, 0, 2, 4, 6, 8, 10, 12):
    def wr(mask):
        m = [r for r in rows if r[2] >= T and mask(r)]
        w = sum(1 for r in m if r[3] == "W"); l = sum(1 for r in m if r[3] == "L")
        return len(m), (100 * w / max(1, w + l))
    nB, wB = wr(lambda r: True); n25, w25 = wr(lambda r: r[0] == 2025); n26, w26 = wr(lambda r: r[0] == 2026)
    sub = [r for r in rows if r[2] >= T]
    fw = sum(1 for r in sub if r[4] == "W"); fl = sum(1 for r in sub if r[4] == "L")
    net = sum(r[5] for r in sub) * 100
    print("  %+4d | n=%-4d win=%.1f%% | n=%-4d win=%.1f%% | n=%-4d win=%.1f%% |   %.1f%%  n=%-4d | %+.1f%%"
          % (T, nB, wB, n25, w25, n26, w26, (100 * fw / max(1, fw + fl)), fw + fl, net), flush=True)
