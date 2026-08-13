"""15m ENGULFING WALL — do the REWARD/EFF TABLE rows separate winners from losers? For every signal (fixed detector
SL/TP, all day) compute the table reads the terminal shows, ALIGNED to the trade side (aligned>50 = reward/eff favours
the trade): Last 10/20/30/50, Today (since 08:00 Morocco=07:00 UTC), Yesterday, FLOW (median[20,30,50,75]); plus
derived patterns nAgree (# rows aligned), accel (a10-a50), mean6, tyGap (Today-Yest).

[A] per year, winner-mean vs loser-mean + AUC for each read/pattern (winner = >=50%-to-TP before SL; also full-TP).
[B] both-year consistency filter -> any read that separates the SAME direction in 2025 AND 2026.
[C] gate sweep on the best both-year read -> win% + non-overlap net per year. Both recon years, 0.04% RT."""
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


def win(i, w):
    return sh(i - w + 1, i)


def day_bounds(i):
    d0 = (int((ST[i] - DAY_OFF) // 86400)) * 86400 + DAY_OFF
    j0 = bisect.bisect_left(ST, float(d0))
    jp = bisect.bisect_left(ST, float(d0 - 86400))
    return j0, jp


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


FEATS = ["a10", "a20", "a30", "a50", "aToday", "aYest", "aFlow", "accel", "nAgree", "mean6", "tyGap"]
rows = []                                                    # (yr, ho, fo, ret, gi, xi, tier, {feat})
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    al = lambda s: None if s is None else (s if side > 0 else 100.0 - s)   # align to trade side
    a10 = al(win(gi, 10)); a20 = al(win(gi, 20)); a30 = al(win(gi, 30)); a50 = al(win(gi, 50))
    j0, jp = day_bounds(gi)
    aTd = al(sh(j0, gi)); aYs = al(sh(jp, j0 - 1)); aFl = al(flow(gi))
    core = [a10, a20, a30, a50, aTd, aFl]
    if any(v is None for v in core) or aYs is None:
        continue
    f = {"a10": a10, "a20": a20, "a30": a30, "a50": a50, "aToday": aTd, "aYest": aYs, "aFlow": aFl,
         "accel": a10 - a50, "nAgree": float(sum(1 for v in [a10, a20, a30, a50, aTd, aYs, aFl] if v > 50)),
         "mean6": float(np.mean(core)), "tyGap": aTd - aYs}
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), ho, fo, ret, gi, xi, tier, f))
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


def block(defn, label):
    print("=== [A] %s  (winner-mean / loser-mean / AUC) ===" % label, flush=True)
    print("  %-8s |        2025            |        2026" % "feature", flush=True)
    consist = []
    for ft in FEATS:
        line = "  %-8s |" % ft; aucs = []
        for Y in (2025, 2026):
            W = [r[7][ft] for r in rows if r[0] == Y and defn(r) == "W"]
            Lo = [r[7][ft] for r in rows if r[0] == Y and defn(r) == "L"]
            au = auc(W, Lo)
            line += " W=%5.1f L=%5.1f AUC=%.3f |" % (np.mean(W) if W else 0, np.mean(Lo) if Lo else 0, au)
            aucs.append(au)
        both = (aucs[0] - 0.5) * (aucs[1] - 0.5) > 0 and min(abs(aucs[0] - 0.5), abs(aucs[1] - 0.5)) >= 0.03
        print(line + ("  <== both-year" if both else ""), flush=True)
        if both:
            consist.append((ft, aucs))
    print("", flush=True)
    return consist


c_half = block(lambda r: r[1], "HALF winner (>=50%-to-TP before SL)")
c_full = block(lambda r: r[2], "FULL winner (TP before SL)")


def sweep(ft, hi=True):
    print("=== [C] gate sweep on %s (%s), win%%=half + non-overlap net ===" % (ft, "keep>=thr" if hi else "keep<=thr"), flush=True)
    vals = sorted(r[7][ft] for r in rows)
    qs = [vals[int(q * (len(vals) - 1))] for q in (0.0, 0.25, 0.5, 0.6, 0.7, 0.8)]
    for T in sorted(set(round(q, 1) for q in qs)):
        sel = [r for r in rows if (r[7][ft] >= T if hi else r[7][ft] <= T)]

        def yr(Y):
            R = [r for r in sel if Y is None or r[0] == Y]
            if not R:
                return "n=0"
            w = sum(1 for r in R if r[1] == "W"); l = sum(1 for r in R if r[1] == "L")
            ov = -1; net = 0.0; nn = 0
            for r in sorted(R, key=lambda x: x[4]):
                if r[4] <= ov:
                    continue
                net += r[3]; ov = r[5]; nn += 1
            return "n=%-4d half=%.1f%% net=%+.1f%%/%d" % (len(R), 100 * w / max(1, w + l), net * 100, nn)
        print("  %s>=%-5.1f BOTH %-30s 2025 %-30s 2026 %s" % (ft, T, yr(None), yr(2025), yr(2026)), flush=True)
    print("", flush=True)


for ft, aucs in (c_half or [])[:3]:
    sweep(ft, hi=(aucs[0] > 0.5))
if not c_half:
    print("No table read separates winners the same direction in BOTH years (half definition).", flush=True)
