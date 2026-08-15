"""1h/15m/5m RADAR RUNNER quick-TP stress test: sweep TP in {0.2,0.3,0.4,0.5}% x SLIPPAGE in {0,3,6}bps, structural SL
(opposite radar extreme), base signal (unfiltered). Slippage model: entry slips on EVERY trade (you enter with the
breakout), SL/timeout market exits slip too, TP limit exits do NOT. 0.04% RT fee on top. Canonical taken()-nonoverlap
(per-TP exit offset). Both recon years. CLI: python study/wall_radarrun_tp_slip_sweep.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004
TPS = [0.002, 0.003, 0.004, 0.005]; SLIPS = [0.0, 0.0003, 0.0006]


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

    ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
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
        c0 += 5000

    rows = []                              # (k, year, {tpfrac: (outcome, gross, off)})
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1
        entry = C[k]; sl0 = rlo if up else rhi
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        d = {}
        for tp in TPS:
            d[tp] = sim(s, entry, entry * (1 + s * tp), sl0, ph, pl, pc)
        rows.append((k, int(yr[k]), d))
    print("\n================  TF = %s   (events=%d, structural SL)  ================" % (tf, len(rows)), flush=True)
    if len(rows) < 40:
        print("  too few events"); return

    for slip in SLIPS:
        print("  ----- slippage = %.0f bps -----" % (slip * 1e4), flush=True)
        for tp in TPS:
            line = "    TP=%.1f%%" % (tp * 100)
            for Y in (2025, 2026):
                acc = []; last = -1
                for (k, y, d) in rows:
                    if y != Y or k <= last:
                        continue
                    outcome, gross, off = d[tp]
                    net = gross - FEE - slip - (slip if outcome != "tp" else 0.0)
                    acc.append(net); last = k + int(off)
                a = np.array(acc)
                if len(a) < 8:
                    line += "  | %d n=%-3d(<8)" % (Y, len(a)); continue
                line += "  | %d n=%-4d win=%2.0f%% avg=%+.3f%% net=%+.0f%%" % (
                    Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100)
            print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "15m", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
