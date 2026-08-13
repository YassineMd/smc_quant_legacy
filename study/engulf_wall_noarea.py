"""15m ENGULFING WALL — EXCLUSION gate: never SHORT in a buy reward/eff area, never LONG in a sell reward/eff area.
This only BLOCKS clearly counter-flow trades; neutral-flow trades are KEPT (looser than the 'must align' gate).
"area" = FLOW (median[20,30,50,75] buy-share) past a deadband d: buy area = FLOW>50+d, sell area = FLOW<50-d.
  LONG  blocked iff FLOW < 50-d   (in a sell area)
  SHORT blocked iff FLOW > 50+d   (in a buy area)
d=0 reproduces the strict block; d=5/10 cut only CLEAR counter-flow. Fixed detector SL/TP, all day.
WINNER(full)=TP before SL; WINNER(half)=>=50%-to-TP before SL. Both recon years, 0.04% RT, non-overlap net."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

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
    half = entry + 0.5 * (tp - entry); ho = fo = "T"; xi = min(n - 1, gi + HORIZON); dh = df = False
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hs = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        ht = (H[k] >= tp) if side > 0 else (L[k] <= tp)
        hh = (H[k] >= half) if side > 0 else (L[k] <= half)
        if not dh:
            ho = "L" if hs else ("W" if hh else ho); dh = hs or hh
        if not df:
            if hs:
                fo = "L"; xi = k; df = True
            elif ht:
                fo = "W"; xi = k; df = True
        if dh and df:
            break
    exitp = sl if fo == "L" else (tp if fo == "W" else C[xi])
    return ho, fo, side * (exitp - entry) / entry - FEE, xi


rows = []                                                    # (yr, side, ho, fo, ret, gi, xi, tier, flow)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi)
    if fs is None:
        continue
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, ho, fo, ret, gi, xi, tier, fs))
print("scored=%d\n" % len(rows), flush=True)


def keep(side, fs, d):
    return (fs >= 50 - d) if side > 0 else (fs <= 50 + d)   # block only clear counter-flow


def report(tag, sel):
    def cell(RR):
        if not RR:
            return "n=0"
        hw = sum(1 for r in RR if r[2] == "W"); hl = sum(1 for r in RR if r[2] == "L")
        fw = sum(1 for r in RR if r[3] == "W"); fl = sum(1 for r in RR if r[3] == "L")
        ov = -1; net = 0.0; nn = 0
        for r in sorted(RR, key=lambda x: x[5]):
            if r[5] <= ov:
                continue
            net += r[4]; ov = r[6]; nn += 1
        return "n=%-4d half=%.1f%% full=%.1f%% net=%+.1f%%/%d" % (
            len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn)
    R = [r for r in rows if sel(r)]
    print("  %-22s BOTH %s" % (tag, cell(R)))
    print("  %-22s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-22s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 15m ENGULFING WALL — never counter a reward/eff AREA (block clear counter-flow) ===")
report("all (no gate)", lambda r: True)
for d in (0, 5, 10):
    report("exclude counter d=%d" % d, lambda r, d=d: keep(r[1], r[8], d))
print("  --- gold tier only ---")
report("gold all", lambda r: r[7] == "gold")
for d in (0, 5, 10):
    report("gold + exclude d=%d" % d, lambda r, d=d: r[7] == "gold" and keep(r[1], r[8], d))
