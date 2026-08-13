"""15m ENGULFING WALL — does the WALL-RADAR stats-box DEFENDER reward/eff separate winners from losers?

Defender reward/eff (exactly as app/terminal._reff_rotation): reward-per-effort share of the DEFENDING side over the
radar visit — buyers at a support (S), sellers at a resistance (R). The Engulfing Wall bounces WITH the defender
(long at support, short at resistance), so defender-high = the side we trade is winning the reward battle at the wall.
CAUSAL: computed over [visit-start rk0 .. signal bar gi] only (the box's rk1 can be after entry; we never use it).
  dW    = defender reward/eff over the whole visit-so-far [rk0, gi]
  d1    = defender reward/eff over the RECENT half [mid, gi]
  slope = d1 - d0 (is the defender GAINING as the visit develops?)
  pres  = wall entry P(resist) for that run  (mild look-ahead in wall calc — flagged)
Fixed detector SL/TP, all day. WINNER(full)=TP first; WINNER(half)=>=50%-to-TP before SL. Both recon years, 0.04% RT."""
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

print("bars=%d detecting (walls+signals per chunk)..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]
    walls = AL.detect(S, skip_last=False)
    wl = [(w.get("side"), _f(w.get("price")), _f(w.get("band")),
           [(int(r[0]), int(r[1]), (_f(r[2]) if len(r) > 2 else 50.0)) for r in w.get("radar_runs", ()) if len(r) >= 2])
          for w in walls]
    for e in MOM.detect(S, walls, skip_last=False):
        li = int(e["i"]); gi = li + c0
        if gi in sigs:
            continue
        side = int(e["side"]); entry = float(e["entry"]); want = "S" if side > 0 else "R"
        best = None; bd = 1e18
        for (sd, pr, band, runs) in wl:                       # defended wall = nearest same-side wall whose visit holds gi
            if sd != want:
                continue
            for (rk0, rk1, pres) in runs:
                if rk0 <= li <= rk1:
                    if abs(pr - entry) < bd:
                        bd = abs(pr - entry); best = (rk0 + c0, pres)
                    break
        if best is None:
            continue
        sigs[gi] = (side, entry, float(e["sl"]), float(e["tp"]), e.get("tier", "normal"), best[0], best[1])
    if c1 >= n:
        break
    c0 += 5000


def dshare(side, a, b):
    s, ok = reward_eff.share(A, a, b)
    if not ok:
        return None
    return s if side > 0 else 100.0 - s                       # defender: long defends at support (buy), short at resist (sell)


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


rows = []                                                     # (yr,ho,fo,ret,gi,xi,tier, dW,d1,slope,vlen,pres)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier, rk0, pres = sigs[gi]
    if entry <= 0 or gi - rk0 < 2:                            # need a couple of bars of visit to read a share
        continue
    mid = rk0 + (gi - rk0) // 2
    dW = dshare(side, rk0, gi); d0 = dshare(side, rk0, mid); d1 = dshare(side, mid, gi)
    if dW is None or d0 is None or d1 is None:
        continue
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), ho, fo, ret, gi, xi, tier, dW, d1, d1 - d0, gi - rk0, pres))
print("scored=%d  (visits with >=2 bars)\n" % len(rows), flush=True)


def auc(pos, neg):
    pos = np.asarray(pos, float); neg = np.asarray(neg, float)
    n1, n0 = len(pos), len(neg)
    if n1 == 0 or n0 == 0:
        return float("nan")
    allv = np.concatenate([pos, neg]); order = np.argsort(allv, kind="mergesort"); sv = allv[order]
    ranks = np.empty(len(allv), float); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (ranks[:n1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)


IDX = {"defender r/eff dW": 7, "defender recent d1": 8, "defender slope": 9, "visit len": 10, "P(resist)": 11}
for lab, dfn in (("HALF (>=50%-to-TP)", 1), ("FULL (TP first)", 2)):
    print("=== [A] AUC winner/loser — %s ===" % lab, flush=True)
    for name, ix in IDX.items():
        line = "  %-18s |" % name; a = []
        for Y in (2025, 2026):
            W = [r[ix] for r in rows if r[0] == Y and r[dfn] == "W"]
            Lo = [r[ix] for r in rows if r[0] == Y and r[dfn] == "L"]
            au = auc(W, Lo); a.append(au)
            line += " W=%5.1f L=%5.1f AUC=%.3f |" % (np.mean(W) if W else 0, np.mean(Lo) if Lo else 0, au)
        both = (a[0] - 0.5) * (a[1] - 0.5) > 0 and min(abs(a[0] - 0.5), abs(a[1] - 0.5)) >= 0.03
        print(line + ("  <== both-year" if both else ""), flush=True)
    print("", flush=True)


def band_report(title, keyfn, bands, labels):
    print("=== %s ===" % title, flush=True)
    for (lo, hi), lb in zip(bands, labels):
        def yr(Y):
            R = [r for r in rows if lo <= keyfn(r) < hi and (Y is None or r[0] == Y)]
            if not R:
                return "n=0"
            hw = sum(1 for r in R if r[1] == "W"); hl = sum(1 for r in R if r[1] == "L")
            fw = sum(1 for r in R if r[2] == "W"); fl = sum(1 for r in R if r[2] == "L")
            ov = -1; net = 0.0
            for r in sorted(R, key=lambda x: x[4]):
                if r[4] <= ov:
                    continue
                net += r[3]; ov = r[5]
            return "n=%-4d half=%.0f%% full=%.0f%% net=%+.0f%%" % (len(R), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100)
        print("  %-14s BOTH %-28s 2025 %-28s 2026 %s" % (lb, yr(None), yr(2025), yr(2026)), flush=True)
    print("", flush=True)


band_report("[B] win% by DEFENDER reward/eff so far (dW)", lambda r: r[7],
            [(-1, 40), (40, 50), (50, 60), (60, 999)], ["dW <40", "dW 40-50", "dW 50-60", "dW 60+"])
band_report("[C] win% by DEFENDER slope (rising = defender gaining)", lambda r: r[9],
            [(-999, -5), (-5, 5), (5, 999)], ["slope <-5", "slope -5..5", "slope 5+"])
