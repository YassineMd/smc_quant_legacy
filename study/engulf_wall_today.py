"""15m ENGULFING WALL — ALL DAY, trading day anchored at 08:00 Morocco time (UTC+1 -> 07:00 UTC). Gate: the trade side
must align with BOTH (a) TODAY reward/eff (share since the 07:00-UTC day open) AND (b) the rolling FLOW reward/eff
(median[20,30,50,75] buy share). LONG needs Today>50 AND FLOW>50; SHORT needs Today<50 AND FLOW<50.

WINNER(full)=TP before SL; WINNER(half)=>=50%-to-TP before SL. Both recon years, 0.04% RT, non-overlap net.
(Morocco = permanent UTC+1; Ramadan UTC+0 not modelled -> ~1h approx on those weeks.)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bisect
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96; DAY_OFF = 7 * 3600            # 08:00 Morocco (UTC+1) = 07:00 UTC day open
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = [_f(b.get("start_time")) for b in A]
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

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


def today(i):
    d0 = (int((ST[i] - DAY_OFF) // 86400)) * 86400 + DAY_OFF      # last 07:00-UTC boundary at/ before the signal
    j = bisect.bisect_left(ST, float(d0))
    s, ok = reward_eff.share(A, j, i)
    return s if ok else None


def score(gi, side, entry, sl, tp):
    half = entry + 0.5 * (tp - entry); ho = fo = "T"; xi = min(n - 1, gi + HORIZON); dh = df = False
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hi_sl = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        hi_tp = (H[k] >= tp) if side > 0 else (L[k] <= tp)
        hi_hf = (H[k] >= half) if side > 0 else (L[k] <= half)
        if not dh:
            ho = "L" if hi_sl else ("W" if hi_hf else ho); dh = hi_sl or hi_hf
        if not df:
            if hi_sl:
                fo = "L"; xi = k; df = True
            elif hi_tp:
                fo = "W"; xi = k; df = True
        if dh and df:
            break
    exitp = sl if fo == "L" else (tp if fo == "W" else C[xi])
    return ho, fo, side * (exitp - entry) / entry - FEE, xi


rows = []                                                 # (yr, side, ho, fo, ret, gi, xi, tier, fl_ok, td_ok)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi); td = today(gi)
    if fs is None or td is None:
        continue
    fl_ok = (fs > 50) if side > 0 else (fs < 50)          # FLOW aligns with trade
    td_ok = (td > 50) if side > 0 else (td < 50)          # TODAY aligns with trade
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, ho, fo, ret, gi, xi, tier, fl_ok, td_ok))
print("scored=%d\n" % len(rows), flush=True)


def report(tag, sel):
    R = [r for r in rows if sel(r)]

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
        return "n=%-4d half=%.1f%% full=%.1f%% net(no)=%+.1f%%/%d" % (
            len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn)
    print("  %-26s BOTH %s" % (tag, cell(R)))
    print("  %-26s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-26s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 15m ENGULFING WALL — ALL DAY (08:00 Morocco day-open) ===")
report("all", lambda r: True)
report("+ FLOW aligns", lambda r: r[8])
report("+ TODAY aligns", lambda r: r[9])
report("+ TODAY & FLOW align", lambda r: r[8] and r[9])
report("+ TODAY&FLOW + gold", lambda r: r[8] and r[9] and r[7] == "gold")
