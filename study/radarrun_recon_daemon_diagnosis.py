"""WHY the big recon-vs-daemon disparity on the Radar Runner? Decompose it. For 30m (and a 1h cross-check) on RECON
(2025-01..2026-06) and DAEMON (2026-06-20..now):
  A) BUCKET GEOMETRY -- buckets/day, median range%, body%, curr_vol, duration(min), buy/sell split, #levels, wall band%.
     (Are the candles even built the same? A construction gap would show here.)
  B) SIGNAL follow-through -- per signal, MFE = max FAVORABLE excursion (% of entry) reached BEFORE the candle-capped SL.
     win@TP == P(MFE >= TP). So one MFE distribution gives the whole win%-vs-TP curve. If daemon MFE is shorter, breakouts
     just don't RUN as far live -> exactly why wider TPs / higher TFs collapse while 0.2% survives.
  C) MONTHLY TIMELINE across the recon->daemon seam -- n, win@0.2/0.3/0.4, median MFE, median range%. THE decisive test:
     a SMOOTH drift through 2026 = regime; a JUMP exactly at the Jun-19/Jun-20 method boundary = reconstruction artifact.
3bps slip / 0.04% fee (win/MFE are pre-fee geometry here -- we want the raw price behaviour). python study/radarrun_recon_daemon_diagnosis.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; SLBUF = {"30m": 0.003, "1h": 0.002}
TPG = (0.001, 0.002, 0.003, 0.004, 0.005, 0.0075, 0.01)     # TP grid for the reach curve


def ym(ts):
    d = datetime.fromtimestamp(ts, tz=timezone.utc); return "%04d-%02d" % (d.year, d.month)


def analyze(A, slbuf):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A]); ET = np.array([_f(b.get("end_time", b.get("start_time"))) for b in A])
    VOL = np.array([_f(b.get("curr_vol")) for b in A]); BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
    nlev = np.array([len(b.get("levels") or {}) for b in A])
    rng = (Hi - Lo) / np.where(C > 0, C, 1.0); body = np.abs(C - O) / np.where(O > 0, O, 1.0)
    dur = (ET - ST) / 60.0
    days = len(set((ST // 86400).astype(int)))
    geom = {"buckets": n, "per_day": n / max(1, days), "range%": 100 * np.median(rng), "body%": 100 * np.median(body),
            "vol": np.median(VOL), "dur_min": np.median(dur[dur > 0]) if (dur > 0).any() else 0.0,
            "buy_share": float(np.median(BV / np.where(VOL > 0, VOL, 1.0))) if VOL.sum() > 0 else 0.0,
            "levels": float(np.median(nlev)), "days": days}
    # ---- detect signals + walls ----
    ev = {}; bands = []; nwalls = 0; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            nwalls += 1; bands.append(band / P)
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000
    geom["walls_per_1k"] = 1000.0 * nwalls / max(1, n); geom["wall_band%"] = 100 * np.median(bands) if bands else 0.0
    # ---- per-signal MFE before the candle-capped SL (TP-independent follow-through) ----
    sigs = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        mfe = 0.0
        for j in range(k + 1, min(n, k + 1 + H)):
            fav = (Hi[j] - entry) / entry if s > 0 else (entry - Lo[j]) / entry
            if fav > mfe:
                mfe = fav
            if (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl):     # SL touched -> stop measuring the run
                break
        sigs.append((float(ST[k]), s, dist, mfe)); last = k + 1
    return geom, sigs


def reachcurve(mfes):
    m = np.array(mfes); return {tp: 100.0 * (m >= tp).mean() for tp in TPG}


def main():
    for tf in ("30m", "1h"):
        data = {}
        for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
            data[ds] = analyze(get_buckets(tf, root), SLBUF[tf])
        print("\n==================  TF = %s  ==================" % tf, flush=True)
        print("A) BUCKET GEOMETRY", flush=True)
        keys = [("per_day", "buckets/day"), ("range%", "median range%"), ("body%", "median body%"),
                ("vol", "median curr_vol"), ("dur_min", "median dur(min)"), ("buy_share", "median buy-share"),
                ("levels", "median #levels"), ("walls_per_1k", "walls/1k buckets"), ("wall_band%", "median wall band%")]
        print("   %-20s %14s %14s" % ("metric", "RECON", "DAEMON"), flush=True)
        for kk, lbl in keys:
            r = data["RECON"][0][kk]; d = data["DAEMON"][0][kk]
            print("   %-20s %14.3f %14.3f" % (lbl, r, d), flush=True)
        print("B) FOLLOW-THROUGH -- win%% == P(MFE >= TP), and median run", flush=True)
        print("   %-10s %8s %8s %8s %8s %8s %8s %8s | medMFE%%  medDist%%" % (
            "dataset", "TP0.1", "TP0.2", "TP0.3", "TP0.4", "TP0.5", "TP0.75", "TP1.0"), flush=True)
        for ds in ("RECON", "DAEMON"):
            sg = data[ds][1]; mfes = [x[3] for x in sg]; dists = [x[2] for x in sg]
            rc = reachcurve(mfes)
            print("   %-10s %7.0f%% %7.0f%% %7.0f%% %7.0f%% %7.0f%% %7.0f%% %7.0f%% | %6.3f  %6.3f" % (
                "%s n=%d" % (ds, len(sg)), rc[0.001], rc[0.002], rc[0.003], rc[0.004], rc[0.005], rc[0.0075], rc[0.01],
                100 * np.median(mfes), 100 * np.median(dists)), flush=True)
        if tf == "30m":
            print("C) MONTHLY TIMELINE (recon -> daemon seam):  win@0.2 / win@0.3 / win@0.4 / medMFE%% / medRange%%", flush=True)
            rows = defaultdict(list)
            for ds in ("RECON", "DAEMON"):
                for (ts, s, dist, mfe) in data[ds][1]:
                    rows[ym(ts)].append((mfe, ds))
            # per-month median range% from buckets too
            rngmo = defaultdict(list)
            for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
                A = get_buckets("30m", root)
                for b in A:
                    c = _f(b.get("close", b.get("close_price"))); hi = _f(b.get("high")); lo = _f(b.get("low"))
                    if c > 0:
                        rngmo[ym(_f(b.get("start_time")))].append((hi - lo) / c)
            for mo in sorted(rows):
                m = np.array([x[0] for x in rows[mo]]); src = rows[mo][0][1]
                rr = np.median(rngmo[mo]) * 100 if rngmo.get(mo) else 0.0
                print("   %-8s [%-6s] n=%-4d  %3.0f%% / %3.0f%% / %3.0f%%   MFE=%.3f%%  range=%.3f%%" % (
                    mo, src, len(m), 100 * (m >= 0.002).mean(), 100 * (m >= 0.003).mean(), 100 * (m >= 0.004).mean(),
                    100 * np.median(m), rr), flush=True)


if __name__ == "__main__":
    main()
