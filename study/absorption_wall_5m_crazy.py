"""5m ABSORPTION WALL + two filters:
 (1) SESSION: signal bar between 08:00 and 24:00 Morocco time (UTC+1) = 07:00-23:00 UTC (drops the 00:00-08:00 quiet).
 (2) CRAZY/BIG ABSORPTION in the SAME wall/radar: the bounced wall must have absorbed >=1 crazy-or-big order on its
     DEFENDING side (app/crazy_wall_detect events: sell absorbed at a support / buy absorbed at a resistance), causal
     (event bar <= signal bar), inside THIS wall's radar (price +/- 3*band) and during its life (wall i0 <= event i).

Native detector SL/TP (SL 0.1% widest{prev,entry}; engulf TP 1:1.5 / gold 1:2; absorb2 1:1.5). Barrier first-passage
(SL-first tie), non-overlap net, both recon years, 0.04% RT, HORIZON=288. FLOW gate = median[20,30,50,75] reward/eff."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import (absorption_level_detect as AL, engulf5m_detect as ENG, absorb2_detect as AB2,
                 absorption as ABSM, crazy_wall_detect as CW, reward_eff)

FEE = 0.0004; HORIZON = 288; RM = 3.0
A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = [_f(b.get("start_time")) for b in A]
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])
HR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).hour for t in ST])

print("bars=%d detecting walls+crazy+signals..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]; ns = len(S)
    walls = AL.detect(S, skip_last=False)
    cev = [(int(ev["i"]), _f(ev["price"]), ev.get("wall_side")) for ev in CW.detect(S, walls, skip_last=False)]
    wlist = [(w.get("side"), _f(w.get("price")), _f(w.get("band")), int(w.get("i0", 0)),
              [(int(r[0]), int(r[1])) for r in w.get("radar_runs", ()) if len(r) >= 2]) for w in walls]
    absorp = []
    for _k in range(ns):
        try:
            absorp.append(ABSM.absorption(S, _k)[0])
        except Exception:
            absorp.append(None)

    def defended_wall(li, side, entry):
        want = "S" if side > 0 else "R"; best = None; bd = 1e18
        for (sd, P, band, i0, runs) in wlist:
            if sd != want or band <= 0:
                continue
            for (rk0, rk1) in runs:
                if rk0 <= li <= rk1:
                    if abs(P - entry) < bd:
                        bd = abs(P - entry); best = (P, band, i0)
                    break
        return best

    def has_crazy(li, side, entry):
        fw = defended_wall(li, side, entry)
        if fw is None:
            return False
        P, band, i0 = fw; lo = P - RM * band; hi = P + RM * band; want = "S" if side > 0 else "R"
        return any(ws == want and i0 <= ci <= li and lo <= cp <= hi for (ci, cp, ws) in cev)

    for src, det in (("WALL", ENG.detect(S, walls=walls, skip_last=False, absorp=absorp)),
                     ("ABSORB2", AB2.detect(S, walls=walls, skip_last=False, absorp=absorp))):
        for e in det:
            gi = int(e["i"]) + c0
            if gi in sigs:
                continue
            side = int(e["side"]); entry = float(e["entry"])
            sigs[gi] = (side, entry, float(e["sl"]), float(e["tp"]), src, bool(e.get("gold")),
                        has_crazy(int(e["i"]), side, entry))
    if c1 >= n:
        break
    c0 += 5000
print("signals=%d  with-crazy=%d\n" % (len(sigs), sum(1 for v in sigs.values() if v[6])), flush=True)


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


rows = []                                                    # (yr, side, ho, fo, ret, gi, xi, src, gold, crazy, sess, fok)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, src, gold, crazy = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi)
    if fs is None:
        continue
    fok = (fs > 50) if side > 0 else (fs < 50)
    sess = 7 <= HR[gi] < 23                                  # 08:00-24:00 Morocco = 07:00-23:00 UTC
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, ho, fo, ret, gi, xi, src, gold, crazy, sess, fok))
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
        return "n=%-5d half=%.1f%% full=%.1f%% net=%+.1f%%/%d" % (
            len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn)
    print("  %-26s BOTH %s" % (tag, cell(R)))
    print("  %-26s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-26s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 5m ABSORPTION WALL — session (08:00-24:00 Morocco) + crazy/big absorption filter ===")
report("all (no filter)", lambda r: True)
report("session only", lambda r: r[10])
report("session + crazy/big", lambda r: r[10] and r[9])
report("session + crazy + FLOW", lambda r: r[10] and r[9] and r[11])
report("session + crazy, gold", lambda r: r[10] and r[9] and r[8])
report("session + crazy, engulf", lambda r: r[10] and r[9] and r[7] == "WALL")
report("crazy/big (all day)", lambda r: r[9])
