"""Backtest the RADAR WICK-BREAKOUT (the powerful breaks the Radar Runner SKIPS: body already BEYOND the radar, only the
WICK retests it — app.radar_breakout_detect.detect_wick) with the EXACT shipped Radar Runner bracket, side-by-side vs the
RR baseline, so we see if the skipped breaks are better / worse / same as the ones RR takes.

Method == the validated RR harness: chunked detection (6000/1000 overlap) calling RB.detect (baseline) and RB.detect_wick
(candidate) per chunk with global (k,side) dedup; entry = breakout close; SL = candle-capped at the radar extreme
(max(low*(1-buf), radar_lo) long / mirror short), per-tf buf (1h/4h 0.2%, else 0.3%); fixed TP {0.2/0.3/0.4/0.5}%; barrier
first-touch (SL-first tie), non-overlap taken(), fee 0.04%RT + 0.03% slip (extra slip on non-TP exit). OOS split by bar YEAR
(2025 in-sample / 2026-H1). Reports n, win%, avgNet%, expR (net/SL-dist), and mean SL-dist. REVERSE (fade) control too.

datasets: bucket = study/recon_archive, clock = study/clock_archive. tfs 5m/15m/30m/1h/4h (no 1m).
python study/radarwick_backtest.py [tf ...]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from app import radar_breakout_detect as RB

H = 200; FEE = 0.0004; SLIP = 0.0003
TPS = [0.002, 0.003, 0.004, 0.005]
SLBUF = {"5m": 0.003, "15m": 0.003, "30m": 0.003, "1h": 0.002, "4h": 0.002}
DATASETS = [("bucket", "study/recon_archive"), ("clock", "study/clock_archive")]


def load(tf, root):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year if t else 0 for t in ST])
    return A, O, C, Hi, Lo, YR, n


def build(A, kind):
    """chunked detection -> sorted list of (k, s, radar_lo, radar_hi). kind in ('rr','wick')."""
    n = len(A); ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        try:
            dets = RB.detect(S, skip_last=False) if kind == "rr" else RB.detect_wick(S, skip_last=False)
        except Exception:
            dets = []
        for e in dets:
            k = int(e["i"]) + c0; s = int(e["side"]); side = "S" if s > 0 else "R"
            if (k, side) not in ev:
                ev[(k, side)] = (s, float(e["radar_lo"]), float(e["radar_hi"]))
        if c1 >= n:
            break
        c0 += step - 1000
    out = [(k, v[0], v[1], v[2]) for (k, side), v in ev.items()]
    out.sort(); return out


def evaluate(events, O, C, Hi, Lo, YR, n, tp, buf, reverse=False):
    by = {}; last = -1
    for (k, s, rlo, rhi) in events:
        if k <= last or k + 1 >= n:
            continue
        entry = C[k]
        # forward (continuation) candle-capped SL defines the risk box (dist); the reverse control fades in the SAME box.
        fsl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - fsl) / entry
        if dist <= 0:
            continue
        ss = -s if reverse else s
        sl = entry * (1 - ss * dist)                      # loss side for direction ss (mirror stop on the correct side)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(ss, entry, entry * (1 + ss * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        by.setdefault(int(YR[k]), []).append((net, dist)); last = k + int(off)
    res = {}
    for y, arr in by.items():
        nets = np.array([a[0] for a in arr]); dists = np.array([a[1] for a in arr])
        res[y] = (len(nets), 100.0 * (nets > 0).mean(), nets.mean() * 100.0,
                  (nets / dists).mean(), dists.mean() * 100.0)
    return res


def cell(res, y):
    if y not in res:
        return "n=0"
    n, win, net, expr, sld = res[y]
    return "n=%-4d win%4.1f%% net%+.3f%% expR%+.2f (SL%.2f%%)" % (n, win, net, expr, sld)


def main():
    tfs = sys.argv[1:] or ["5m", "15m", "30m", "1h", "4h"]
    print("RADAR WICK-BREAKOUT vs RR baseline | shipped bracket (candle-SL) | fee 0.04%%RT+0.03%%slip | taken() | OOS\n",
          flush=True)
    for dsname, root in DATASETS:
        for tf in tfs:
            try:
                A, O, C, Hi, Lo, YR, n = load(tf, root)
            except Exception as e:
                print("== %s %s : load ERR %s" % (dsname, tf, e), flush=True); continue
            if not n:
                print("== %s %s : empty" % (dsname, tf), flush=True); continue
            buf = SLBUF.get(tf, 0.003)
            rr = build(A, "rr"); wk = build(A, "wick")
            print("================ %s  %s  (RR events=%d | WICK events=%d) ================" % (dsname.upper(), tf, len(rr), len(wk)), flush=True)
            for tp in TPS:
                rrr = evaluate(rr, O, C, Hi, Lo, YR, n, tp, buf)
                wkr = evaluate(wk, O, C, Hi, Lo, YR, n, tp, buf)
                wrev = evaluate(wk, O, C, Hi, Lo, YR, n, tp, buf, reverse=True)
                print("  TP %.2f%%" % (tp * 100), flush=True)
                for y in (2025, 2026):
                    tag = "IS " if y == 2025 else "OOS"
                    print("    %d %s | RR   %s" % (y, tag, cell(rrr, y)), flush=True)
                    print("    %d %s | WICK %s | rev %s" % (y, tag, cell(wkr, y), cell(wrev, y)), flush=True)
            print("", flush=True)


if __name__ == "__main__":
    main()
