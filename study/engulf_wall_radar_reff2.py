"""15m ENGULFING WALL — is the wall P(resist) winner/loser separation REAL or a look-ahead leak?

The shipped P(resist) averages box VOLUME over the WHOLE visit [rk0, rk1] (absorption_level_detect line 258), and rk1
can be AFTER the signal bar -> leak (volume is the model's dominant factor). Here we recompute P(resist) CAUSALLY:
volume only over [rk0, gi] (the visit SO FAR), entry-candle geometry (rk0) and ejection (wall strength) unchanged —
using the detector's own helpers _box_vol_lv / _p_resist / _recal. Compare LEAKY vs CAUSAL AUC(win/lose), both years.
Fixed detector SL/TP, all day. WINNER(full)=TP first; WINNER(half)=>=50%-to-TP before SL. Both recon years, 0.04% RT."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM
from app.absorption_level_detect import _box_vol_lv, _p_resist, _recal

FEE = 0.0004; HORIZON = 96
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
TV = np.array([_f(b.get("buy_vol")) + _f(b.get("sell_vol")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

print("bars=%d detecting..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]
    walls = AL.detect(S, skip_last=False)
    wl = [(w.get("side"), _f(w.get("price")), _f(w.get("band")), _f(w.get("strength")),
           [(int(r[0]), int(r[1]), (_f(r[2]) if len(r) > 2 else 50.0)) for r in w.get("radar_runs", ()) if len(r) >= 2])
          for w in walls]
    for e in MOM.detect(S, walls, skip_last=False):
        li = int(e["i"]); gi = li + c0
        if gi in sigs:
            continue
        side = int(e["side"]); entry = float(e["entry"]); want = "S" if side > 0 else "R"
        best = None; bd = 1e18
        for (sd, pr, band, strg, runs) in wl:
            if sd != want:
                continue
            for (rk0, rk1, pres) in runs:
                if rk0 <= li <= rk1:
                    if abs(pr - entry) < bd:
                        bd = abs(pr - entry); best = (rk0 + c0, pres, pr, band, strg)
                    break
        if best is None:
            continue
        sigs[gi] = (side, entry, float(e["sl"]), float(e["tp"]), e.get("tier", "normal"), *best)
    if c1 >= n:
        break
    c0 += 5000


def causal_presist(side, rk0, gi, price, band, strength):
    if band <= 0 or gi < rk0:
        return None
    r_lo = price - 3.0 * band; r_hi = price + 3.0 * band; span = r_hi - r_lo
    if span <= 0:
        return None
    bars = gi - rk0 + 1
    bx = sum(_box_vol_lv(A[k], r_lo, r_hi) for k in range(rk0, gi + 1))
    pre = [v for v in TV[max(0, rk0 - 200):rk0] if v > 0]
    rm = float(np.median(pre)) if pre else 0.0
    vr = (bx / bars) / rm if (rm > 0 and bars > 0) else 0.0
    isR = side < 0                                            # short defends a resistance (R); long a support (S)
    pen = ((H[rk0] - r_lo) if isR else (r_hi - L[rk0])) / span; pen = min(1.0, max(0.0, pen))
    clpos = ((C[rk0] - r_lo) if isR else (r_hi - C[rk0])) / span; clpos = min(1.0, max(0.0, clpos))
    body = (C[rk0] - O[rk0]) * (1.0 if isR else -1.0) / span
    return _recal(_p_resist(vr, pen, clpos, body, strength))


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


rows = []                                                     # (yr, ho, fo, ret, gi, xi, tier, pres_leaky, pres_causal)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier, rk0, pres, price, band, strength = sigs[gi]
    if entry <= 0:
        continue
    pc = causal_presist(side, rk0, gi, price, band, strength)
    if pc is None:
        continue
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), ho, fo, ret, gi, xi, tier, float(pres), float(pc)))
print("scored=%d\n" % len(rows), flush=True)


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


for lab, dfn in (("HALF (>=50%-to-TP)", 1), ("FULL (TP first)", 2)):
    print("=== AUC winner/loser — %s ===" % lab, flush=True)
    for name, ix in (("P(resist) LEAKY (whole run)", 7), ("P(resist) CAUSAL (visit-so-far)", 8)):
        line = "  %-30s |" % name
        for Y in (2025, 2026):
            W = [r[ix] for r in rows if r[0] == Y and r[dfn] == "W"]
            Lo = [r[ix] for r in rows if r[0] == Y and r[dfn] == "L"]
            line += " W=%5.1f L=%5.1f AUC=%.3f |" % (np.mean(W) if W else 0, np.mean(Lo) if Lo else 0, auc(W, Lo))
        print(line, flush=True)
    print("", flush=True)


def band_report(title, ix, bands, labels):
    print("=== %s ===" % title, flush=True)
    for (lo, hi), lb in zip(bands, labels):
        def yr(Y):
            R = [r for r in rows if lo <= r[ix] < hi and (Y is None or r[0] == Y)]
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
        print("  %-12s BOTH %-28s 2025 %-28s 2026 %s" % (lb, yr(None), yr(2025), yr(2026)), flush=True)
    print("", flush=True)


band_report("win% by CAUSAL P(resist)", 8, [(-1, 50), (50, 60), (60, 70), (70, 200)],
            ["Pres <50", "Pres 50-60", "Pres 60-70", "Pres 70+"])
