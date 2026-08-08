# -*- coding: utf-8 -*-
"""RADAR ORDER-FLOW study: when price enters a wall's radar area, does the PURE order flow INSIDE that box
(its height [P-3band, P+3band] x its width [the candles while price is inside]) predict RESIST vs BREAK?

Flow oriented to the DEFENDING side: sellers defend a resistance, buyers defend a support.
  net_defend%% = (defend - aggressor) / total * 100   (inside the box only — no more, no less)
Outcome (first-passage from entry): BREAK = close beyond the FAR radar edge; RESIST = close back out the NEAR edge.
Hypothesis: strong defending flow inside the box -> RESIST; aggressor dominates -> BREAK. AUC + precision, both years.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL
try:
    from scipy.stats import binomtest
    def pv(k, n, p): return binomtest(k, n, p, alternative="two-sided").pvalue if n else 1.0
except Exception:
    def pv(k, n, p): return 1.0

LF = 24                       # first-passage window for the outcome (bars from entry)


def run(tf="15m"):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    n = len(A)
    C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    print("loaded %s: %d buckets — running the wall detector..." % (tf, n))
    walls = AL.detect(A)
    print("walls: %d — measuring radar-box flow per visit" % len(walls))

    def outcome_bar(k0, r_lo, r_hi, side):
        """First bar (from entry) whose CLOSE exits the radar: returns (bar, resist?) or (None, None)."""
        for k in range(k0, min(n, k0 + LF)):
            if side == "R":
                if C[k] > r_hi: return k, 0     # BREAK (close above the radar)
                if C[k] < r_lo: return k, 1     # RESIST (close back below)
            else:
                if C[k] < r_lo: return k, 0
                if C[k] > r_hi: return k, 1
        return None, None

    V = []                                       # per-visit: {net_defend, defend_share, tot, resist, yr, side}
    for w in walls:
        if w["strength"] < 0.12:
            continue
        P = w["price"]; band = w["band"]; side = w["side"]
        r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
        for (k0, k1) in w["radar_runs"]:
            ko, oc = outcome_bar(k0, r_lo, r_hi, side)
            if oc is None or ko <= k0:            # unresolved, or resolves on the entry bar (no pre-flow) -> skip
                continue
            defend = aggr = tot = 0.0
            for k in range(k0, ko):               # flow STRICTLY BEFORE the resolving bar (causal)
                for ps, vv in (A[k].get("levels") or {}).items():
                    try:
                        p = float(ps)
                    except (TypeError, ValueError):
                        continue
                    if p < r_lo or p > r_hi:      # ONLY levels inside the radar height
                        continue
                    b_ = _f(vv.get("b")); s_ = _f(vv.get("s"))
                    if side == "R":
                        defend += s_; aggr += b_
                    else:
                        defend += b_; aggr += s_
                    tot += b_ + s_
            if tot <= 0:
                continue
            V.append({"net_defend": (defend - aggr) / tot * 100.0, "defend_share": defend / tot * 100.0,
                      "tot": tot, "resist": oc, "yr": YR[k0], "side": side})

    base = sum(v["resist"] for v in V) / len(V)
    print("\n=== %s === resolved radar visits: %d | base RESIST %.1f%% (BREAK %.1f%%)" % (
        tf, len(V), 100 * base, 100 * (1 - base)))
    for feat in ("net_defend", "defend_share", "tot"):
        rv = [v[feat] for v in V if v["resist"]]; bv = [v[feat] for v in V if not v["resist"]]
        a = auc_p(rv, bv)[0]
        a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025])[0]
        a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026])[0]
        print("   AUC %-13s %.3f (25:%.2f 26:%.2f)   [>0.5 => higher %s -> RESIST]" % (feat, a, a25, a26, feat))

    print("\n   -- RESIST rate by net_defend band (disjoint) --")
    bands = [(-1e9, -30), (-30, -10), (-10, 10), (10, 30), (30, 1e9)]
    for lo, hi in bands:
        sel = [v for v in V if lo <= v["net_defend"] < hi]
        if len(sel) < 20:
            print("   net_defend [%5.0f,%5.0f): n=%d (few)" % (lo, hi, len(sel))); continue
        r = sum(v["resist"] for v in sel)
        r25 = [v for v in sel if v["yr"] == 2025]; r26 = [v for v in sel if v["yr"] == 2026]
        p25 = (sum(v["resist"] for v in r25) / len(r25) * 100) if r25 else 0
        p26 = (sum(v["resist"] for v in r26) / len(r26) * 100) if r26 else 0
        print("   net_defend [%5.0f,%5.0f): n=%4d  RESIST %4.1f%%  (25:%.0f/26:%.0f)  p=%.4f" % (
            lo, hi, len(sel), 100 * r / len(sel), p25, p26, pv(r, len(sel), base)))


run("15m")
