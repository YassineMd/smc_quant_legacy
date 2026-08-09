# -*- coding: utf-8 -*-
"""Five candidate predictors for P(resist), all causal, all controlled within volume terciles:
  1 HTF confluence  — is an ALIVE 1h/4h wall at the same price?
  2 trend context   — is the approach WITH the 15m trend (pushes break) or counter (pullback -> hold)?
  3 approach depth  — how deep into the radar the entry candle reached (toward the wall)
  4 session         — Tokyo/London/NY/off
  5 vpin proxy      — rolling-50 |buy-sell|/curr toxicity at entry
Base = resist (wall holds). Reports resist/AUC both years + within-volume control for the live ones."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR = 24, 0.6, 0.12


def load_tf(tf):
    _, r, _ = load_archive(tf, root="study/recon_archive")
    B = sorted(r, key=lambda b: _f(b.get("start_time", 0)))
    for b in B:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    return B


print("loading 15m/1h/4h + detecting ...", flush=True)
A = load_tf("15m"); n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
BV = [_f(b.get("buy_vol")) for b in A]; SV = [_f(b.get("sell_vol")) for b in A]; CVv = np.array([_f(b.get("curr_vol")) for b in A])
ST = [_f(b.get("start_time")) for b in A]
DTh = [datetime.fromtimestamp(s, tz=timezone.utc) for s in ST]; YR = [d.year for d in DTh]
walls = AL.detect(A)
# rolling-50 VPIN proxy (causal): sum|buy-sell| / sum curr over last 50 bars
ad = np.array([abs(BV[i] - SV[i]) for i in range(n)])
vpin = np.array([ad[max(0, i - 50):i].sum() / max(1.0, CVv[max(0, i - 50):i].sum()) for i in range(n)])


def htf_spans(tf, secs):
    B = load_tf(tf); s = [_f(b.get("start_time")) for b in B]
    w = AL.detect(B)
    out = [(x["price"], s[x["i0"]], s[min(x["i1"], len(s) - 1)] + secs) for x in w if x["strength"] >= 0.12]
    out.sort()
    return out, [o[0] for o in out]


H1, H1p = htf_spans("1h", 3600); H4, H4p = htf_spans("4h", 14400)
print("15m walls %d | 1h walls %d | 4h walls %d" % (len(walls), len(H1), len(H4)), flush=True)


def confl(spans, prices, P, t):
    lo = bisect.bisect_left(prices, P * 0.998); hi = bisect.bisect_right(prices, P * 1.002)
    for idx in range(lo, hi):
        wp, t0, t1 = spans[idx]
        if t0 <= t <= t1:
            return 1
    return 0


def sess(h):
    return "Tokyo" if 0 <= h < 8 else ("London" if 8 <= h < 13 else ("NY" if 13 <= h < 21 else "off"))


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR or k0 < 20:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:
            continue
        from math import fsum
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for k in range(k0, ko)
                  for _p, vv in (A[k].get("levels") or {}).items() if r_lo <= (_f(_p)) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        vr = (bx / (ko - k0)) / rmv if rmv > 0 else 0.0
        bd = 1.0 if side == "R" else -1.0
        pen = ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / (r_hi - r_lo)
        V.append({"resist": oc, "yr": YR[k0], "vr": vr,
                  "conf": confl(H1, H1p, P, ST[k0]) + confl(H4, H4p, P, ST[k0]),
                  "trend": (C[k0] / C[k0 - 20] - 1.0) * bd, "pen": pen,
                  "sess": sess(DTh[k0].hour), "vpin": float(vpin[k0])})

base_r = sum(v["resist"] for v in V) / len(V)
vmed = np.median([v["vr"] for v in V])
print("\n=== %d causal visits | base RESIST %.1f%% ===" % (len(V), 100 * base_r), flush=True)


def auc(feat):
    a = auc_p([v[feat] for v in V if v["resist"]], [v[feat] for v in V if not v["resist"]])[0]
    a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025])[0]
    a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026])[0]
    within = []
    for lab, lo, hi in (("LOv", -1, vmed), ("HIv", vmed, 1e9)):
        seg = [v for v in V if lo <= v["vr"] < hi]
        m = np.median([v[feat] for v in seg])
        loo = [v for v in seg if v[feat] < m]; hii = [v for v in seg if v[feat] >= m]
        within.append("%s %+.1f" % (lab, 100 * (sum(v["resist"] for v in hii) / max(1, len(hii)) - sum(v["resist"] for v in loo) / max(1, len(loo)))))
    print("   AUC %-6s %.3f (25:%.2f 26:%.2f)   within-vol hi-lo delta: %s" % (feat, a, a25, a26, "  ".join(within)), flush=True)


print("   [AUC>0.5 -> higher feature -> RESIST]")
for f in ("conf", "trend", "pen", "vpin"):
    auc(f)
print("\n   -- HTF confluence: resist by # of aligned 1h/4h walls --", flush=True)
for c in (0, 1, 2):
    sel = [v for v in V if v["conf"] == c]
    if sel:
        s25 = [v for v in sel if v["yr"] == 2025]; s26 = [v for v in sel if v["yr"] == 2026]
        print("   conf=%d  n=%5d  RESIST %.1f%%  (25:%.1f/26:%.1f)" % (
            c, len(sel), 100 * sum(v["resist"] for v in sel) / len(sel),
            100 * sum(v["resist"] for v in s25) / max(1, len(s25)), 100 * sum(v["resist"] for v in s26) / max(1, len(s26))))
print("   -- session --", flush=True)
for s in ("Tokyo", "London", "NY", "off"):
    sel = [v for v in V if v["sess"] == s]
    if sel:
        print("   %-7s n=%5d  RESIST %.1f%%" % (s, len(sel), 100 * sum(v["resist"] for v in sel) / len(sel)))
