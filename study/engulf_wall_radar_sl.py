"""15m ENGULFING WALL — SL at the RADAR edge (worse RR, for testing). TP = the detector's own TP (unchanged). SL =
the DEFENDED wall's radar edge +/- 0.1%: LONG -> below the support radar_lo (price-3*band); SHORT -> above the
resistance radar_hi. All day, day-open 08:00 Morocco (07:00 UTC); gate = TODAY & FLOW reward/eff both aligned with
the trade. WINNER(full)=TP before SL; WINNER(half)=>=50%-to-TP before SL. Both recon years, 0.04% RT, non-overlap net."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bisect
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96; DAY_OFF = 7 * 3600; SL_PAD = 0.001
RM = float(getattr(AL, "RADAR_MULT", 3.0))
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = [_f(b.get("start_time")) for b in A]
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

print("bars=%d RADAR_MULT=%.1f detecting..." % (n, RM), flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]; ns = len(S)
    walls = AL.detect(S, skip_last=False)
    sup_lo = [None] * ns; res_hi = [None] * ns             # per-bar DEFENDED radar edge (min support-lo / max resist-hi)
    for w in walls:
        sd = w.get("side"); pr = _f(w.get("price")); bd = _f(w.get("band"))
        if bd <= 0 or pr <= 0:
            continue
        rlo = pr - RM * bd; rhi = pr + RM * bd
        for r in w.get("radar_runs", ()):
            if len(r) < 2:
                continue
            rk0 = max(0, int(r[0])); rk1 = min(ns - 1, int(r[1]))
            for k in range(rk0, rk1 + 1):
                if sd == "S":
                    sup_lo[k] = rlo if sup_lo[k] is None else min(sup_lo[k], rlo)
                elif sd == "R":
                    res_hi[k] = rhi if res_hi[k] is None else max(res_hi[k], rhi)
    for e in MOM.detect(S, walls, skip_last=False):
        li = int(e["i"]); gi = li + c0
        edge = sup_lo[li] if e["side"] > 0 else res_hi[li]
        if edge is None or gi in sigs:
            continue
        sl = edge * (1 - SL_PAD) if e["side"] > 0 else edge * (1 + SL_PAD)   # SL just BEYOND the radar edge
        sigs[gi] = (int(e["side"]), float(e["entry"]), float(sl), float(e["tp"]), e.get("tier", "normal"))
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
    d0 = (int((ST[i] - DAY_OFF) // 86400)) * 86400 + DAY_OFF
    s, ok = reward_eff.share(A, bisect.bisect_left(ST, float(d0)), i)
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


rows = []
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0 or (side > 0 and sl >= entry) or (side < 0 and sl <= entry):
        continue                                           # degenerate: radar edge on the wrong side of entry
    fs = flow(gi); td = today(gi)
    if fs is None or td is None:
        continue
    rr = abs(tp - entry) / abs(entry - sl)
    fl_ok = (fs > 50) if side > 0 else (fs < 50)
    td_ok = (td > 50) if side > 0 else (td < 50)
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, ho, fo, ret, gi, xi, tier, fl_ok, td_ok, rr))
print("scored=%d  mean RR=%.2f  (radar SL -> worse RR by design)\n" % (len(rows), np.mean([r[10] for r in rows])), flush=True)


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
        return "n=%-4d half=%.1f%% full=%.1f%% net(no)=%+.1f%%/%d  RR=%.2f" % (
            len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn, np.mean([r[10] for r in RR]))
    print("  %-26s BOTH %s" % (tag, cell(R)))
    print("  %-26s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-26s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 15m ENGULFING WALL — SL AT RADAR EDGE, TP unchanged (bad RR, test) — all day, 08:00 Morocco ===")
report("all", lambda r: True)
report("+ TODAY & FLOW align", lambda r: r[8] and r[9])
report("+ TODAY&FLOW + gold", lambda r: r[8] and r[9] and r[7] == "gold")
