# -*- coding: utf-8 -*-
"""Disentangle VOLUME from DURATION in the radar-box "volume -> BREAK" signal (AUC 0.31 in wall_radar_flow).

Is it INTENSITY (volume per bar — measurable early, potentially usable) or just DURATION (more bars inside ->
more accumulated volume -> and lingering itself breaks; NOT usable since future duration is unknown)?

Per resolved radar visit (causal: flow strictly BEFORE the resolving bar):
  duration = bars inside before resolution ;  box_tot = footprint vol at radar levels ;  box_per_bar = box_tot/duration
Predict BREAK. AUC of each + break-rate stratified by duration, and per_bar median-split WITHIN each duration.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF = 24


def run(tf="15m"):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    n = len(A)
    C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    CV = [_f(b.get("curr_vol")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    print("loaded %s: %d — detecting walls..." % (tf, n))
    walls = AL.detect(A)

    def outcome_bar(k0, r_lo, r_hi, side):
        for k in range(k0, min(n, k0 + LF)):
            if side == "R":
                if C[k] > r_hi: return k, 0
                if C[k] < r_lo: return k, 1
            else:
                if C[k] < r_lo: return k, 0
                if C[k] > r_hi: return k, 1
        return None, None

    V = []
    for w in walls:
        if w["strength"] < 0.12:
            continue
        P = w["price"]; band = w["band"]; side = w["side"]
        r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
        for (k0, k1) in w["radar_runs"]:
            ko, oc = outcome_bar(k0, r_lo, r_hi, side)
            if oc is None or ko <= k0:
                continue
            dur = ko - k0
            box = 0.0; cand = 0.0
            for k in range(k0, ko):
                cand += CV[k]
                for ps, vv in (A[k].get("levels") or {}).items():
                    try:
                        p = float(ps)
                    except (TypeError, ValueError):
                        continue
                    if r_lo <= p <= r_hi:
                        box += _f(vv.get("b")) + _f(vv.get("s"))
            if box <= 0:
                continue
            V.append({"brk": 1 - oc, "dur": dur, "box": box, "box_pb": box / dur,
                      "cand_pb": cand / dur, "yr": YR[k0]})

    brk = sum(v["brk"] for v in V) / len(V)
    print("\n=== %s === resolved visits %d | base BREAK %.1f%%" % (tf, len(V), 100 * brk))

    def auc(feat):
        a = [v[feat] for v in V if v["brk"]]; b = [v[feat] for v in V if not v["brk"]]
        g = auc_p(a, b)[0]
        a25 = auc_p([v[feat] for v in V if v["brk"] and v["yr"] == 2025], [v[feat] for v in V if not v["brk"] and v["yr"] == 2025])[0]
        a26 = auc_p([v[feat] for v in V if v["brk"] and v["yr"] == 2026], [v[feat] for v in V if not v["brk"] and v["yr"] == 2026])[0]
        print("   AUC %-10s %.3f (25:%.2f 26:%.2f)   [>0.5 => higher %s -> BREAK]" % (feat, g, a25, a26, feat))
    for f in ("dur", "box", "box_pb", "cand_pb"):
        auc(f)

    print("\n   -- BREAK rate by DURATION (bars inside before resolution) --")
    for d in (1, 2, 3, 4, 5):
        sel = [v for v in V if v["dur"] == d]
        if sel:
            print("   dur=%d : n=%4d  BREAK %4.1f%%" % (d, len(sel), 100 * sum(v["brk"] for v in sel) / len(sel)))
    sel = [v for v in V if v["dur"] >= 6]
    if sel:
        print("   dur>=6: n=%4d  BREAK %4.1f%%" % (len(sel), 100 * sum(v["brk"] for v in sel) / len(sel)))

    print("\n   -- within each DURATION, does box_per_bar still separate? (median split) --")
    for d in (1, 2, 3, 4):
        sel = [v for v in V if v["dur"] == d]
        if len(sel) < 40:
            continue
        med = float(np.median([v["box_pb"] for v in sel]))
        lo = [v for v in sel if v["box_pb"] < med]; hi = [v for v in sel if v["box_pb"] >= med]
        print("   dur=%d : per_bar LOW  BREAK %4.1f%% (n=%d) | HIGH BREAK %4.1f%% (n=%d)  delta=%+.1f" % (
            d, 100 * sum(v["brk"] for v in lo) / max(1, len(lo)), len(lo),
            100 * sum(v["brk"] for v in hi) / max(1, len(hi)), len(hi),
            100 * (sum(v["brk"] for v in hi) / max(1, len(hi)) - sum(v["brk"] for v in lo) / max(1, len(lo)))))


run("15m")
