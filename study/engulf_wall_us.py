"""15m ENGULFING WALL — US SESSION ONLY (signal bar UTC hour in [13,21) = the NY/US block).
Baseline vs the reward/eff aligned-gap gate vs a min-gap sweep. WINNER(full)=TP before SL; WINNER(half)=>=50%-to-TP
before SL. reward/eff aligned gap = (FLOW-50) LONG / (50-FLOW) SHORT, FLOW=median[20,30,50,75] buy-share. Both years,
0.04% RT fee, non-overlap for the P&L (net); win% cells use ALL scored signals (overlap ok for a rate)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96; US_LO, US_HI = 13, 21          # US/NY session UTC hours [13,21)
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = np.array([_f(b.get("start_time")) for b in A])
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])
HR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).hour for t in ST])

print("bars=%d detecting..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]
    for e in MOM.detect(S, AL.detect(S, skip_last=False), skip_last=False):
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]), e.get("tier", "normal"))
    if c1 >= n:
        break
    c0 += 5000


def flow(i):
    ws = [s for s, ok in (reward_eff.share(A, i - w + 1, i) for w in (20, 30, 50, 75)) if ok]
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def score(gi, side, entry, sl, tp):
    half = entry + 0.5 * (tp - entry); ho = "T"; fo = "T"; xi = min(n - 1, gi + HORIZON)
    dh = df = False
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hi_sl = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        hi_tp = (H[k] >= tp) if side > 0 else (L[k] <= tp)
        hi_hf = (H[k] >= half) if side > 0 else (L[k] <= half)
        if not dh:
            if hi_sl:
                ho = "L"; dh = True
            elif hi_hf:
                ho = "W"; dh = True
        if not df:
            if hi_sl:
                fo = "L"; xi = k; df = True
            elif hi_tp:
                fo = "W"; xi = k; df = True
        if dh and df:
            break
    exitp = sl if fo == "L" else (tp if fo == "W" else C[xi])
    return ho, fo, side * (exitp - entry) / entry - FEE, xi


rows = []                                                 # (yr, side, gap, ho, fo, ret, gi, xi, tier)
for gi in sorted(sigs):
    if gi + 1 >= n or not (US_LO <= HR[gi] < US_HI):       # US session only
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi)
    if fs is None:
        continue
    gap = (fs - 50.0) if side > 0 else (50.0 - fs)
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, gap, ho, fo, ret, gi, xi, tier))
print("US-session scored signals=%d\n" % len(rows), flush=True)


def report(tag, sel):
    R = [r for r in rows if sel(r)]
    def cell(RR):
        if not RR:
            return "n=0"
        hw = sum(1 for r in RR if r[3] == "W"); hl = sum(1 for r in RR if r[3] == "L")
        fw = sum(1 for r in RR if r[4] == "W"); fl = sum(1 for r in RR if r[4] == "L")
        # non-overlap net
        ov = -1; net = 0.0; nn = 0
        for r in sorted(RR, key=lambda x: x[6]):
            if r[6] <= ov:
                continue
            net += r[5]; ov = r[7]; nn += 1
        return "n=%-4d half=%.1f%% full=%.1f%% net(no)=%+.1f%%/%d" % (
            len(R) if RR is R else len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn)
    print("  %-22s BOTH %s" % (tag, cell(R)), flush=True)
    print("  %-22s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])), flush=True)
    print("  %-22s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])), flush=True)


print("=== 15m ENGULFING WALL — US SESSION [13-21 UTC] (half=>=50%-to-TP, full=TP, net=non-overlap) ===")
report("all US", lambda r: True)
report("+ reward/eff gate", lambda r: r[2] > 0)
report("+ gate gap>=4", lambda r: r[2] >= 4)
report("+ gold tier", lambda r: r[8] == "gold")
report("+ gold + gate", lambda r: r[8] == "gold" and r[2] > 0)
