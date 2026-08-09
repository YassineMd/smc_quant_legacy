# -*- coding: utf-8 -*-
"""FAILED BREAKOUT of a resisted wall. Event chain: wall HELD >=1 visit -> a candle CLOSES out of the radar (breakout
at bk) -> within WFAIL bars a candle CLOSES back INSIDE the radar (confirmation bf) = the breakout FAILED. From bf,
study the REVERSAL (oriented OPPOSITE the breakout, back through the wall): barrier +/-REV*band, horizon Hrev.
Descriptive: base reversal-follow-through, magnitude, does it return through the wall; feature scan of the
confirmation candle (order flow + ALL stats-box) + wall P(resist). Causal (bf has closed; outcome after). Both yr."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import absorption_level_detect as AL

LF, DECAY, STR, WFAIL, HREV, REV = 24, 0.6, 0.12, 12, 12, 2.0
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
SK = list(S.keys())
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
n_brk = n_fail = 0
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
        if seen_resist < 1:                              # breakout only counts if the wall RESISTED first
            continue
        n_brk += 1
        bk = ko                                          # breakout candle
        # find the confirmation of FAILURE: first later bar that CLOSES back inside the radar
        bf = None
        for k in range(bk + 1, min(n, bk + 1 + WFAIL)):
            if (C[k] < r_hi) if side == "R" else (C[k] > r_lo):
                bf = k; break
        if bf is None:                                   # never came back -> breakout held (not a failed breakout)
            break
        n_fail += 1
        if bf + HREV >= n or bf < 6:
            break
        rd = -dd                                         # reversal direction (opposite the breakout)
        c0 = C[bf]; rt = c0 + rd * REV * band; at = c0 - rd * REV * band
        rev = None
        for k in range(bf + 1, bf + 1 + HREV):
            if rd > 0:
                if Hh[k] >= rt:
                    rev = 1; break
                if Ll[k] <= at:
                    rev = 0; break
            else:
                if Ll[k] <= rt:
                    rev = 1; break
                if Hh[k] >= at:
                    rev = 0; break
        if rev is None:
            rev = 1 if (C[bf + HREV] - c0) * rd > 0 else 0
        rev_fwd = (C[bf + HREV] - c0) / c0 * 100.0 * rd
        exc = max((rd * (Hh[k] - c0) if rd > 0 else rd * (Ll[k] - c0)) / band for k in range(bf + 1, bf + 1 + HREV))
        thru = 1.0 if any(((C[k] < P) if side == "R" else (C[k] > P)) for k in range(bf + 1, bf + 1 + HREV)) else 0.0
        rec = {"rev": rev, "yr": YR[bf], "rev_fwd": rev_fwd, "exc": float(exc), "thru": thru, "pr": float(pr),
               "dwell": float(bf - bk), "beyond_brk": (C[bk] - r_hi) / band if side == "R" else (r_lo - C[bk]) / band,
               "volrel": CVv[bf] / (np.median(CVv[max(0, bf - 200):bf]) + 1e-9),
               "cvd6": float(CD[bf] - CD[bf - 6]) * rd / (CVv[bf - 5:bf + 1].sum() + 1e-9) * 100.0,
               "conf_dp": float(DP[bf]) * rd}
        for k in SK:
            rec["sb_" + k] = float(Sarr[k][bf])
        V.append(rec)
        break

N = len(V); base = 100 * sum(v["rev"] for v in V) / N
print("\n=== FAILED breakout -> reversal: %d failed (of %d resisted-wall breakouts, %.0f%%) | base REV-FT %.1f%% ===" % (
    N, n_brk, 100 * n_fail / max(1, n_brk), base), flush=True)
print("   [reversal = +/-%.0f*band from the confirmation close, %d-bar horizon]" % (REV, HREV), flush=True)
print("   mean reversal fwd return @%d: %+.3f%% | mean reversal excursion %.2f*band | returns THROUGH wall: %.1f%%" % (
    HREV, np.mean([v["rev_fwd"] for v in V]), np.mean([v["exc"] for v in V]), 100 * np.mean([v["thru"] for v in V])), flush=True)

extra = ["pr", "dwell", "beyond_brk", "volrel", "cvd6", "conf_dp"]
feats = extra + ["sb_" + k for k in SK]


def au(feat, pop):
    a = [v[feat] for v in pop if v["rev"] and v[feat] == v[feat]]; b = [v[feat] for v in pop if not v["rev"] and v[feat] == v[feat]]
    return auc_p(a, b)[0] if len(a) > 15 and len(b) > 15 else float("nan")


rows = []
for f in feats:
    g = au(f, V); g25 = au(f, [v for v in V if v["yr"] == 2025]); g26 = au(f, [v for v in V if v["yr"] == 2026])
    if g != g or g25 != g25 or g26 != g26:
        continue
    rows.append((abs(g - 0.5), f, g, g25, g26))
rows.sort(reverse=True)
print("\n   feature (AUC reversal-follow-through; >0.5 -> higher -> REVERSAL)  [<== both-yr if |dev|>=.05 same sign]:", flush=True)
for _, f, g, g25, g26 in rows[:20]:
    flag = "  <== both-yr" if (g - 0.5) * (g25 - 0.5) > 0 and (g - 0.5) * (g26 - 0.5) > 0 and abs(g - 0.5) >= 0.05 else ""
    print("   %-16s %.3f (25:%.2f 26:%.2f)%s" % (f, g, g25, g26, flag), flush=True)

print("\n   -- reversal by wall P(resist) tercile (does a strong wall reverse harder?) --", flush=True)
xs = sorted(v["pr"] for v in V); t1, t2 = xs[N // 3], xs[2 * N // 3]
for lab, lo, hi in (("loP", -1, t1), ("midP", t1, t2), ("hiP", t2, 1e9)):
    g = [v for v in V if lo <= v["pr"] < hi]
    g25 = [v for v in g if v["yr"] == 2025]; g26 = [v for v in g if v["yr"] == 2026]
    print("   %-4s pr[%2.0f,%3.0f) n=%4d  REV-FT %.1f%% (25:%.1f 26:%.1f)  fwd %+.3f%%  exc %.2f  thru %.0f%%" % (
        lab, lo if lo > 0 else 0, hi if hi < 1e8 else 100, len(g),
        100 * sum(v["rev"] for v in g) / max(1, len(g)),
        100 * sum(v["rev"] for v in g25) / max(1, len(g25)), 100 * sum(v["rev"] for v in g26) / max(1, len(g26)),
        np.mean([v["rev_fwd"] for v in g]), np.mean([v["exc"] for v in g]), 100 * np.mean([v["thru"] for v in g])), flush=True)
