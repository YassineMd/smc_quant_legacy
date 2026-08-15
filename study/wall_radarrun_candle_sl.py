"""Radar Runner tail-capping SL test. Instead of the wide structural SL (opposite radar extreme -> ~1.9% avg losers),
try a TIGHT candle-anchored stop, capped at structural. Three SL rules (LONG shown; SHORT mirrors):
  struct       : radar extreme rlo                                  (current best, wide)
  cand+0.3 cap : max(entry_candle_low*(1-0.003), rlo)               (0.3% below the breakout candle's low, but never wider)
  entry0.3 cap : max(entry*(1-0.003), rlo)                          (0.3% below the entry price, but never wider)

Reports win%, avg %/trade net, net%, AND avg LOSER size (the tail) per rule, at TP in {0.3,0.5}% x slip {0,6}bps,
both recon years, 1h + 15m. 0.04% RT fee; entry+SL/timeout slip, TP limit no slip; taken()-nonoverlap. CLI: [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004
TPS = [0.003, 0.005]; SLIPS = [0.0, 0.0006]


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def sl_rules(up, entry, rlo, rhi, lok, hik):
    if up:
        return {"struct": rlo,
                "cand+0.3cap": max(lok * (1 - 0.003), rlo),
                "entry0.3cap": max(entry * (1 - 0.003), rlo)}
    return {"struct": rhi,
            "cand+0.3cap": min(hik * (1 + 0.003), rhi),
            "entry0.3cap": min(entry * (1 + 0.003), rhi)}


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

    RULES = ["struct", "cand+0.3cap", "entry0.3cap"]
    rows = []                                   # (k, year, {rule:{tp:(outcome,gross,off)}}, {rule: sl_dist})
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sls = sl_rules(up, entry, rlo, rhi, Lo[k], Hi[k])
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        per = {}; dist = {}
        for rule in RULES:
            sl = sls[rule]; dist[rule] = abs(entry - sl) / entry
            per[rule] = {tp: sim(s, entry, entry * (1 + s * tp), sl, ph, pl, pc) for tp in TPS}
        rows.append((k, int(yr[k]), per, dist))
    print("\n================  TF = %s   (events=%d)  ================" % (tf, len(rows)), flush=True)
    if len(rows) < 40:
        print("  too few events"); return

    for rule in RULES:
        avgdist = 100 * np.mean([r[3][rule] for r in rows])
        print("  ===== SL rule: %-12s  (avg stop distance = %.2f%%) =====" % (rule, avgdist), flush=True)
        for tp in TPS:
            for slip in SLIPS:
                line = "    TP=%.1f%% slip=%.0fbps" % (tp * 100, slip * 1e4)
                for Y in (2025, 2026):
                    acc = []; last = -1
                    for (k, y, per, _d) in rows:
                        if y != Y or k <= last:
                            continue
                        outcome, gross, off = per[rule][tp]
                        net = gross - FEE - slip - (slip if outcome != "tp" else 0.0)
                        acc.append(net); last = k + int(off)
                    a = np.array(acc)
                    if len(a) < 8:
                        line += "  | %d n=%-3d(<8)" % (Y, len(a)); continue
                    losers = a[a < 0]
                    lavg = losers.mean() * 100 if len(losers) else 0.0
                    line += "  | %d n=%-4d win=%2.0f%% avg=%+.3f%% net=%+.0f%% Lavg=%.2f%%" % (
                        Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100, lavg)
                print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "15m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
