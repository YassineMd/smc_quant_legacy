# -*- coding: utf-8 -*-
"""Should STRENGTH account for RE-TEST ejections (not just formation, then blind decay)? Measure ejection at formation
AND at every successful defense (hold); define str_max = running max of those (causal). Test: (a) how often a re-test
ejects MORE than formation (the user's scenario the current decay ignores); (b) does str_max predict the next visit's
RESIST at least as well as formation-only ejection `base`? (c) how many walls the 0.6^hits decay dims below the 0.12
draw floor would SURVIVE under running-max. 15m recon, both yr."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, EJ_WIN, EJ_MULT, ATR_WIN = 24, AL.DECAY, AL.EJ_WIN, AL.EJ_ATR_MULT, AL.ATR_WIN
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A]); H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
# vpct (rolling-mean candle-range %) — replicate the detector
vpct = np.zeros(n); s = 0.0
for i in range(n):
    s += (H[i] - L[i]) / C[i] if C[i] > 0 else 0.0
    if i >= ATR_WIN:
        s -= (H[i - ATR_WIN] - L[i - ATR_WIN]) / C[i - ATR_WIN] if C[i - ATR_WIN] > 0 else 0.0
    vpct[i] = s / min(i + 1, ATR_WIN)
walls = AL.detect(A)


def ejection(anchor, P, side):
    fav = 0.0
    for k in range(anchor, min(n, anchor + EJ_WIN + 1)):
        f = (P - L[k]) / P if side == "R" else (H[k] - P) / P
        if f > fav:
            fav = f
    v0 = vpct[anchor]
    return min(1.0, fav / (EJ_MULT * v0)) if v0 > 0 else 0.0


def resolve(k0, rl, rh, side):
    for k in range(k0, min(n, k0 + LF)):
        r = (0 if C[k] > rh else (1 if C[k] < rl else None)) if side == "R" else \
            (0 if C[k] < rl else (1 if C[k] > rh else None))
        if r is not None:
            return k, r
    return None, None


V = []                       # per multi-bar visit: base, str_max_before, resist, yr
reeject_more = tot_holds = 0
survive = 0; dim_below = 0
for w in walls:
    P = w["price"]; band = w["band"]; side = w["side"]; rl = P - 3 * band; rh = P + 3 * band
    base = ejection(w["i0"], P, side)
    str_max = base
    for j, (k0, k1, pr) in enumerate(w["radar_runs"]):
        ko, oc = resolve(k0, rl, rh, side)
        if oc is None:
            continue
        if ko > k0:                                            # multi-bar visit -> a data point
            V.append({"base": base, "smax": str_max, "resist": oc, "yr": YR[k0]})
        if oc == 1:                                            # HELD -> measure this defense's ejection
            ej = ejection(ko, P, side); tot_holds += 1
            if ej > base + 1e-6:
                reeject_more += 1
            # visual impact: current decayed strength vs running-max at this hit index
            cur = base * (DECAY ** (j + 1))                    # after this hit, old model
            if cur < 0.12 and max(str_max, ej) >= 0.12:
                survive += 1
            if cur < 0.12:
                dim_below += 1
            if ej > str_max:
                str_max = ej

N = len(V)
print("\n=== RE-TEST ejection study: %d walls, %d multi-bar visits, %d holds ===" % (len(walls), N, tot_holds), flush=True)
print("   re-test ejected MORE than formation: %d/%d holds (%.0f%%) <- the case the 0.6^hits decay ignores" % (
    reeject_more, tot_holds, 100 * reeject_more / max(1, tot_holds)), flush=True)
print("   visual: of %d holds the old decay dims <0.12, running-max would KEEP %d (%.0f%%) alive" % (
    dim_below, survive, 100 * survive / max(1, dim_below)), flush=True)


def au(feat):
    a = [v[feat] for v in V if v["resist"]]; b = [v[feat] for v in V if not v["resist"]]
    g = auc_p(a, b)[0]
    a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025])[0]
    a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026])[0]
    return g, a25, a26


print("\n   predict next visit RESIST (AUC, >0.5 = higher -> holds):", flush=True)
for feat, lab in (("base", "formation ejection (current)"), ("smax", "running-max ejection (proposed)")):
    g, a25, a26 = au(feat)
    print("   %-32s %.3f (25:%.2f 26:%.2f)" % (lab, g, a25, a26), flush=True)

# does str_max add OVER base? (only the visits where they differ carry the re-test info)
diff = [v for v in V if v["smax"] > v["base"] + 1e-6]
print("\n   visits where str_max > base (a re-ejection happened before): %d (%.0f%%)" % (len(diff), 100 * len(diff) / N), flush=True)
if diff:
    r = 100 * sum(v["resist"] for v in diff) / len(diff)
    rest = [v for v in V if v not in diff]  # note: slow membership but N modest
    print("   RESIST | re-ejected-stronger visits %.1f%%  vs  rest %.1f%%" % (
        r, 100 * sum(v["resist"] for v in rest) / max(1, len(rest))), flush=True)
