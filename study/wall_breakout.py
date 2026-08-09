# -*- coding: utf-8 -*-
"""BREAKOUT of a wall that RESISTED. Event = a candle that CLOSES out of the radar (the wall dies) on a wall that
already HELD >=1 prior radar visit. Study the breakout candle's ORDER FLOW + ALL stats-box params + the wall's
P(resist) at the break, vs FOLLOW-THROUGH vs FAILED-breakout (fakeout). Outcome = oriented band-relative barrier from
the breakout close: does price reach +2*band (continuation) before -2*band (reversal) within H bars. Causal (breakout
candle has closed; outcome strictly after). Both years."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import absorption_level_detect as AL

LF, DECAY, STR, H, BARR = 24, 0.6, 0.12, 12, 2.0     # LF=classify runs; H=forward horizon; BARR=+/- band multiples
print("loading 15m + build_stats + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A]); Hh = np.array([_f(b.get("high")) for b in A]); Ll = np.array([_f(b.get("low")) for b in A])
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
CD = np.concatenate([[0.0], np.cumsum(BV - SV)])[1:]
DP = np.array([(BV[i] - SV[i]) / CVv[i] * 100.0 if CVv[i] > 0 else 0.0 for i in range(n)])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
S, _O, _C = build_stats(A)
SK = [k for k in S.keys()]
Sarr = {k: np.array([(_f(v) if v == v else np.nan) for v in S[k]]) for k in SK}
walls = AL.detect(A)


def resolve(k0, r_lo, r_hi, side):
    for k in range(k0, min(n, k0 + LF)):
        r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
            (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
        if r is not None:
            return k, r
    return None, None


V = []
n_break = n_bar = 0
for w in walls:
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    dd = 1.0 if side == "R" else -1.0
    seen_resist = 0
    for (k0, k1, pr) in w["radar_runs"]:
        ko, oc = resolve(k0, r_lo, r_hi, side)
        if oc is None:
            continue
        if oc == 1:
            seen_resist += 1
            continue
        # oc == 0 : a BREAK. only keep if the wall already RESISTED >=1 time
        n_break += 1
        if seen_resist < 1:
            continue
        bk = ko                                        # THE breakout candle (closed out of the radar)
        if bk + H >= n or bk < 6:
            continue
        # outcome: oriented band-relative barrier from the breakout close
        c0 = C[bk]; cont = c0 + dd * BARR * band; fail = c0 - dd * BARR * band
        ft = None
        for k in range(bk + 1, bk + 1 + H):
            if dd > 0:
                if Hh[k] >= cont:
                    ft = 1; break
                if Ll[k] <= fail:
                    ft = 0; break
            else:
                if Ll[k] <= cont:
                    ft = 1; break
                if Hh[k] >= fail:
                    ft = 0; break
        if ft is None:
            ft = 1 if (C[bk + H] - c0) * dd > 0 else 0     # no barrier hit -> sign of forward move
        fwd = (C[bk + H] - c0) / c0 * 100.0 * dd
        rec = {"ft": ft, "yr": YR[bk], "fwd": fwd, "pr": float(pr),
               "n_resist": float(seen_resist), "hits": float(w["hits"]), "strength": float(w["strength"]),
               "band_rel": band / P * 100.0, "beyond": (c0 - r_hi) / band if side == "R" else (r_lo - c0) / band,
               "volrel": CVv[bk] / (np.median(CVv[max(0, bk - 200):bk]) + 1e-9),
               "cvd6": float(CD[bk] - CD[bk - 6]) * dd / (CVv[bk - 5:bk + 1].sum() + 1e-9) * 100.0,
               "dp_break": float(DP[bk]) * dd}
        for k in SK:                                      # ALL stats-box params of the breakout candle (oriented where signed)
            rec["sb_" + k] = float(Sarr[k][bk])
        V.append(rec)
        break                                             # one breakout event per wall

N = len(V); base = 100 * sum(v["ft"] for v in V) / N
print("\n=== BREAKOUT of a resisted wall: %d events (of %d total breaks) | base FOLLOW-THROUGH %.1f%% ===" % (
    N, n_break, base), flush=True)
print("   [outcome: +%.0f*band continuation before -%.0f*band reversal, %d-bar horizon]" % (BARR, BARR, H), flush=True)
print("   mean fwd oriented return @%d bars: %+.3f%%" % (H, np.mean([v["fwd"] for v in V])), flush=True)

DIR_SB = {"body_pct", "delta_pct", "da2", "dP_t", "dP_1", "dP_2", "delta_up", "delta_dn", "skew", "mmxskew", "effagg_sp"}
extra = ["pr", "n_resist", "hits", "strength", "band_rel", "beyond", "volrel", "cvd6", "dp_break"]
feats = extra + ["sb_" + k for k in SK]


def au(feat, pop):
    a = [v[feat] for v in pop if v["ft"] and v[feat] == v[feat]]; b = [v[feat] for v in pop if not v["ft"] and v[feat] == v[feat]]
    return auc_p(a, b)[0] if len(a) > 15 and len(b) > 15 else float("nan")


rows = []
for f in feats:
    g = au(f, V); g25 = au(f, [v for v in V if v["yr"] == 2025]); g26 = au(f, [v for v in V if v["yr"] == 2026])
    if g != g or g25 != g25 or g26 != g26:
        continue
    rows.append((abs(g - 0.5), f, g, g25, g26))
rows.sort(reverse=True)
print("\n   feature (AUC follow-through; >0.5 -> higher -> FOLLOW-THROUGH)  [both-yr flag if |dev|>=.05 & same sign]:", flush=True)
for _, f, g, g25, g26 in rows[:22]:
    flag = "  <== both-yr" if (g - 0.5) * (g25 - 0.5) > 0 and (g - 0.5) * (g26 - 0.5) > 0 and abs(g - 0.5) >= 0.05 else ""
    print("   %-16s %.3f (25:%.2f 26:%.2f)%s" % (f, g, g25, g26, flag), flush=True)

print("\n   -- P(resist) of the broken wall vs follow-through (tercile splits, both yr) --", flush=True)
xs = sorted(v["pr"] for v in V); t1, t2 = xs[N // 3], xs[2 * N // 3]
for lab, lo, hi in (("loP(resist)", -1, t1), ("midP", t1, t2), ("hiP(resist)", t2, 1e9)):
    g = [v for v in V if lo <= v["pr"] < hi]
    g25 = [v for v in g if v["yr"] == 2025]; g26 = [v for v in g if v["yr"] == 2026]
    print("   %-12s pr[%.0f,%.0f)  n=%4d  FT %.1f%% (25:%.1f 26:%.1f)  fwd %+.3f%%" % (
        lab, lo if lo > 0 else 0, hi if hi < 1e8 else 100, len(g),
        100 * sum(v["ft"] for v in g) / max(1, len(g)),
        100 * sum(v["ft"] for v in g25) / max(1, len(g25)), 100 * sum(v["ft"] for v in g26) / max(1, len(g26)),
        np.mean([v["fwd"] for v in g])), flush=True)
