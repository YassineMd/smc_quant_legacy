"""Backtest the 15m ENGULFING WALL strategy (app/momentum_detect, wall-bounce), with a REWARD/EFF directional gate:
take the LONG only when reward/eff is BUY-dominant (buyers being rewarded), the SHORT only when SELL-dominant.

reward/eff read = the FLOW consolidation the terminal shows: MEDIAN of the [20,30,50,75]-bar reward-per-effort BUY
shares at the signal bar (causal, trailing). LONG kept iff FLOW>50, SHORT iff FLOW<50.

Entry = signal-candle close; SL/TP = the detector's own (SL 0.1% beyond prev candle, TP 1:1.2 / 1:2 gold). Barrier
first-passage (SL-first on tie), NON-OVERLAPPING, both recon years, 0.04% RT fee. Wall detection OOMs over full
history -> slide 6000-bar chunks / 1000 overlap, map local->global, dedup by global bar. Reports BASELINE (all
signals) vs the reward/eff-GATED set, per year.
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
HORIZON = 96                                              # ~1 day cap on 15m

print("bars=%d  detecting 15m-Engulfing-Wall signals in windows..." % n, flush=True)
CH, STEP = 6000, 5000
sigs = {}                                                 # global bar -> (side, entry, sl, tp, tier), first-seen wins
c0 = 0
while c0 < n:
    c1 = min(n, c0 + CH); S = A[c0:c1]
    walls = AL.detect(S, skip_last=False)
    for e in MOM.detect(S, walls, skip_last=False):
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]), e.get("tier", "normal"))
    if c1 >= n:
        break
    c0 += STEP
order = sorted(sigs)
print("signals=%d" % len(order), flush=True)


def flow_share(i):
    """FLOW buy-share at bar i = median of the [20,30,50,75]-bar reward/eff buy shares (causal). None if no data."""
    ws = []
    for w in (20, 30, 50, 75):
        s, ok = reward_eff.share(A, i - w + 1, i)
        if ok:
            ws.append(s)
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def run(gate):
    """gate: None (baseline) or a fn(side, flow_share)->bool. NON-OVERLAP barrier."""
    res = []; ou = -1
    for gi in order:
        if gi <= ou or gi + 1 >= n:
            continue
        side, entry, sl, tp, tier = sigs[gi]
        if entry <= 0:
            continue
        if gate is not None:
            fs = flow_share(gi)
            if fs is None or not gate(side, fs):
                continue
        out = None; xi = min(n - 1, gi + HORIZON)
        for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
            hit_sl = (L[k] <= sl) if side > 0 else (H[k] >= sl)
            hit_tp = (H[k] >= tp) if side > 0 else (L[k] <= tp)
            if hit_sl:
                out = "L"; xi = k; break
            if hit_tp:
                out = "W"; xi = k; break
        exitp = (sl if out == "L" else (tp if out == "W" else C[xi]))
        res.append((int(YR[gi]), side, side * (exitp - entry) / entry - FEE, out if out else "T", tier)); ou = xi
    return res


def rep(tag, R):
    if not R:
        print("  %-16s n=0" % tag, flush=True); return
    N = len(R); w = sum(1 for x in R if x[3] == "W"); l = sum(1 for x in R if x[3] == "L")
    net = sum(x[2] for x in R) * 100
    bal = 1.0
    for x in R:
        bal *= (1 + x[2])
    print("  %-16s n=%-4d win=%.1f%% (W%d/L%d/to%d)  net=%+.1f%%  exp=%+.3f%%  comp=%+.1f%%"
          % (tag, N, 100 * w / max(1, w + l), w, l, N - w - l, net, net / N, (bal - 1) * 100), flush=True)


LONG_OK = lambda side, fs: (fs > 50.0) if side > 0 else (fs < 50.0)   # LONG needs buy-dominant reward/eff; SHORT sell-dominant

for name, gate in (("BASELINE (all)", None), ("REWARD/EFF gate", LONG_OK)):
    R = run(gate)
    print("\n=== %s ===" % name, flush=True)
    for tag, yf in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
        rep(tag, [x for x in R if (yf is None or x[0] == yf)])
    rep("long", [x for x in R if x[1] > 0]); rep("short", [x for x in R if x[1] < 0])
    rep("gold-tier", [x for x in R if x[4] == "gold"])
    rep("gold 2025", [x for x in R if x[4] == "gold" and x[0] == 2025])
    rep("gold 2026", [x for x in R if x[4] == "gold" and x[0] == 2026])
