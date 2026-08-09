# -*- coding: utf-8 -*-
"""1m-resolution radar flow (STREAMED, memory-light): walls on 15m, but the order flow inside the radar box is
measured from the 1m sub-candles. Streams the 1m gz chunks with a sweep line (never loads the whole 780k archive).
Causal: 1m window = [entry 15m bar, resolving 15m bar). Does 1m sharpen the BREAK prediction vs the 15m box volume?"""
import os, sys, glob, gzip, json
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF = 24


def box_flow(bucket, r_lo, r_hi):
    v = bu = sv = 0.0
    for ps, vv in (bucket.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            b_ = _f(vv.get("b")); s_ = _f(vv.get("s")); v += b_ + s_; bu += b_; sv += s_
    return v, bu, sv


print("loading 15m + detecting walls...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
ST = [_f(b.get("start_time")) for b in A]
YR = [datetime.fromtimestamp(s, tz=timezone.utc).year for s in ST]
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


visits = []
for w in walls:
    if w["strength"] < 0.12:
        continue
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for (k0, k1) in w["radar_runs"]:
        ko, oc = outcome_bar(k0, r_lo, r_hi, side)
        if oc is None or ko <= k0 or ko >= n:
            continue
        v15 = 0.0
        for k in range(k0, ko):
            vv, _, _ = box_flow(A[k], r_lo, r_hi); v15 += vv
        visits.append({"t0": ST[k0], "tko": ST[ko], "r_lo": r_lo, "r_hi": r_hi, "side": side, "brk": 1 - oc,
                       "yr": YR[k0], "dur15": ko - k0, "v15": v15,
                       "v1m": 0.0, "buy": 0.0, "sell": 0.0, "mx": 0.0, "n1m": 0, "nd_last": 0.0})
visits.sort(key=lambda v: v["t0"])
print("resolved 15m visits: %d — STREAMING 1m chunks (sweep line)..." % len(visits), flush=True)

files = glob.glob(os.path.join("study/recon_archive/1m", "1m_*.jsonl.gz"))
files.sort(key=lambda fn: int(os.path.basename(fn).split("_")[1]))    # numeric bid order = time order
vp = 0; active = []; done = 0
for fi, fn in enumerate(files):
    with gzip.open(fn, "rt", encoding="utf-8") as gz:
        for line in gz:
            if not line.strip():
                continue
            r = json.loads(line); d = r["data"]
            if isinstance(d, str):
                d = json.loads(d)
            t = _f(d.get("start_time"))
            while vp < len(visits) and visits[vp]["t0"] <= t:      # open new visits whose window has started
                active.append(visits[vp]); vp += 1
            if active:
                active = [v for v in active if v["tko"] > t]        # expire finished visits
            for v in active:
                vv, bb, ss = box_flow(d, v["r_lo"], v["r_hi"])
                if vv <= 0:
                    continue
                v["v1m"] += vv; v["buy"] += bb; v["sell"] += ss; v["n1m"] += 1
                if vv > v["mx"]:
                    v["mx"] = vv
                v["nd_last"] = bb - ss
    done += 1
    if done % 25 == 0:
        print("  ...%d/%d chunks (active %d)" % (done, len(files), len(active)), flush=True)

for v in visits:
    tot = v["v1m"]
    v["v1m_pb"] = tot / v["n1m"] if v["n1m"] else 0.0
    v["spike"] = v["mx"] / tot * 100.0 if tot > 0 else 0.0
    v["nd1m"] = (v["buy"] - v["sell"]) / tot * 100.0 if tot > 0 else 0.0
    v["v15_pb"] = v["v15"] / v["dur15"] if v["dur15"] else 0.0

V = [v for v in visits if v["n1m"] >= 1]
base = sum(v["brk"] for v in V) / len(V)
print("\n=== 15m walls, 1m flow === visits w/ 1m data: %d/%d | base BREAK %.1f%% | avg 1m candles/visit %.1f" % (
    len(V), len(visits), 100 * base, np.mean([v["n1m"] for v in V])), flush=True)


def auc(feat):
    a = [v[feat] for v in V if v["brk"]]; b = [v[feat] for v in V if not v["brk"]]
    g = auc_p(a, b)[0]
    a25 = auc_p([v[feat] for v in V if v["brk"] and v["yr"] == 2025], [v[feat] for v in V if not v["brk"] and v["yr"] == 2025])[0]
    a26 = auc_p([v[feat] for v in V if v["brk"] and v["yr"] == 2026], [v[feat] for v in V if not v["brk"] and v["yr"] == 2026])[0]
    print("   AUC %-9s %.3f (25:%.2f 26:%.2f)" % (feat, g, a25, a26), flush=True)


print("   -- 15m baseline vs 1m features (predict BREAK) --", flush=True)
for f in ("v15_pb", "v1m_pb", "spike", "nd1m", "nd_last"):
    auc(f)
