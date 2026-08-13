"""15m ENGULFING WALL — RAW reward/eff EXTREMITY. Does a LOPSIDED book at the signal mark exhaustion (fade sticks =
winner) or continuation (flow keeps pushing = loser)? Two framings, both side-independent-in-meaning:
  ext   = |FLOW-50|                 -> how lopsided, regardless of direction
  against = 50 - aligned_FLOW       -> signed: >0 flow OPPOSES the trade (deep fade), <0 flow SUPPORTS it
Plus window/today extremities. FLOW = median[20,30,50,75] buy-share. Fixed detector SL/TP, all day.

[A] AUC of each extremity read for winner/loser (both winner defs, both years, both-year consistency flag).
[B] win% (half + full) + non-overlap net by EXTREMITY band, per year.
[C] win% by SIGNED against-strength band, per year (exhaustion vs continuation). Both recon years, 0.04% RT."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bisect
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96; DAY_OFF = 7 * 3600
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


def sh(i0, i1):
    s, ok = reward_eff.share(A, i0, i1)
    return s if ok else None


def flow(i):
    ws = [s for s, ok in (reward_eff.share(A, i - w + 1, i) for w in (20, 30, 50, 75)) if ok]
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def today(i):
    d0 = (int((ST[i] - DAY_OFF) // 86400)) * 86400 + DAY_OFF
    return sh(bisect.bisect_left(ST, float(d0)), i)


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


rows = []                                                    # (yr, ho, fo, ret, gi, xi, tier, ext, against, ext30, extTd)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fr = flow(gi); e30 = sh(gi - 29, gi); td = today(gi)
    if fr is None or e30 is None or td is None:
        continue
    af = fr if side > 0 else 100.0 - fr                      # aligned flow
    ext = abs(fr - 50.0); against = 50.0 - af               # >0 flow opposes trade
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), ho, fo, ret, gi, xi, tier, ext, against, abs(e30 - 50.0), abs(td - 50.0)))
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


IDX = {"ext|FLOW-50|": 7, "against(signed)": 8, "ext|L30-50|": 9, "ext|Today-50|": 10}
for lab, defn in (("HALF (>=50%-to-TP)", 1), ("FULL (TP first)", 2)):
    print("=== [A] AUC winner/loser — %s ===" % lab, flush=True)
    for name, ix in IDX.items():
        line = "  %-16s |" % name; a = []
        for Y in (2025, 2026):
            W = [r[ix] for r in rows if r[0] == Y and r[defn] == "W"]
            Lo = [r[ix] for r in rows if r[0] == Y and r[defn] == "L"]
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
        print("  %-16s BOTH %-30s 2025 %-30s 2026 %s" % (lb, yr(None), yr(2025), yr(2026)), flush=True)
    print("", flush=True)


band_report("[B] win% by EXTREMITY |FLOW-50| (exhaustion?=higher band wins)", lambda r: r[7],
            [(0, 4), (4, 8), (8, 12), (12, 999)], ["ext 0-4", "ext 4-8", "ext 8-12", "ext 12+"])
band_report("[C] win% by SIGNED against-strength (>0 = flow opposes fade)", lambda r: r[8],
            [(-999, -8), (-8, -3), (-3, 3), (3, 8), (8, 999)],
            ["with strong", "with", "neutral", "against", "against strong"])
