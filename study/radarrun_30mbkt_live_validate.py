"""VALIDATE the incremental (live-faithful) Radar Runner detection on 30m BUCKET before the full redo.
The terminal fires at each bar CLOSE with the history it has then (no repaint). We reproduce that with
detect(A[k-W:k+1], skip_last=False) and accept only signals whose i == last bar. Three checks:
  1. REPRODUCTION: every persisted terminal fire (data/radarrun_fired.json, 30m bucket-paced, within recon
     coverage) must be re-fired by the windowed sim at the same bar with the same side/entry/sl.
  2. WINDOW STABILITY: W=1000 vs W=2000 vs prefix-capped-10k agreement on a random bar sample.
  3. COST: seconds per bar -> sizing the full 37k-bar run.
python study/radarrun_30mbkt_live_validate.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import config, radar_breakout_detect as RB
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

TF_MIN = 30


def live_fire(A, k, W):
    """Signals the terminal would fire at bar k's close, using only history up to k (window W)."""
    lo = max(0, k - W)
    sub = A[lo:k + 1]
    sig = RB.detect(sub, skip_last=False, sl_buf=0.003, tp_frac=config.RR_TP_FRAC)
    li = len(sub) - 1
    return [g for g in sig if g["i"] == li]


def main():
    A = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    ET = np.array([_f(b.get("end_time")) for b in A])
    print("recon 30m: %d buckets  %s .. %s" % (n,
          datetime.fromtimestamp(_f(A[0].get("start_time")), tz=timezone.utc).date(),
          datetime.fromtimestamp(ET[-1], tz=timezone.utc).date()), flush=True)

    fired = json.load(open(os.path.join(config.DATA_DIR, "radarrun_fired.json")))["30m"]
    pers = []
    for kk, v in fired.items():
        t = float(kk)
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        bkt = not (abs(t - round(t)) < 0.02 and dt.second == 0 and dt.minute % TF_MIN == 0)
        if bkt and t <= ET[-1] + 1:
            j = int(np.argmin(np.abs(ET - t)))
            if abs(ET[j] - t) < 2.0:
                pers.append((j, t, 1 if float(v.get("side", 0)) > 0 else -1, float(v["entry"]), float(v["sl"])))
    pers.sort()
    print("persisted bucket-paced 30m fires matched to recon bars: %d" % len(pers), flush=True)

    # --- 1. reproduction of the terminal's own fires ---
    for W in (1000, 2000):
        ok = miss = badspec = 0; t0 = time.time()
        for (j, t, s, e, sl) in pers:
            hits = live_fire(A, j, W)
            m = [g for g in hits if g["side"] == s]
            if not m:
                miss += 1
                continue
            g = m[0]
            if abs(g["entry"] - e) < 0.02 and abs(g["sl_trade"] - sl) < 0.05:
                ok += 1
            else:
                badspec += 1
        print("W=%-5d reproduction: ok=%d  spec-mismatch=%d  MISSED=%d  (%.2fs/fire)"
              % (W, ok, badspec, miss, (time.time() - t0) / max(1, len(pers))), flush=True)

    # --- 2. window stability on random bars ---
    rng = np.random.default_rng(7)
    sample = sorted(rng.choice(np.arange(2000, n - 1), size=120, replace=False))
    res = {}
    for W in (1000, 2000, 10000):
        t0 = time.time()
        res[W] = ["|".join("%d:%.2f" % (g["side"], g["entry"]) for g in live_fire(A, int(k), W)) for k in sample]
        print("W=%-5d random-120-bar pass: %.3fs/bar  fires=%d"
              % (W, (time.time() - t0) / len(sample), sum(1 for r in res[W] if r)), flush=True)
    agree12 = sum(1 for a, b in zip(res[1000], res[2000]) if a == b)
    agree210 = sum(1 for a, b in zip(res[2000], res[10000]) if a == b)
    print("agreement W1000 vs W2000: %d/120   W2000 vs W10000: %d/120" % (agree12, agree210), flush=True)


if __name__ == "__main__":
    main()
