"""Does an ORDER-FLOW filter improve the Radar Runner (resisted-wall radar breakout) P&L, or just thin the trades?
Same event / entry (breakout close) / SL (opposite radar extreme) / tiered targets as study/wall_breakout_backtest.py.
Best exit schemes: scale+BE (1/3 out at 1x/2x/3x, stop->BE after 1x) and trail (hold, trail stop by tier). 0.04% RT
fee, SL-first tie, per-scheme + per-filter NON-OVERLAP, both recon years.

FILTERS (from study/wall_breakout_orderflow.py's both-year signals, all ALIGNED to the breakout direction):
  bo_str = breakout-bar STRENGTH effort z (the winning-side effort)   -- the strongest genuine order-flow predictor
  t_reff50 = reward/eff over the last 50 bars (>50 favours the break)  -- weak-modest table read
Reports, per scheme: baseline (all) vs each filter -> win% / avg-per-trade / net, both years. The test: does AVG/TRADE
rise in BOTH years (real lift) or is it flat (just fewer trades)?  Usage: python study/wall_breakout_filtered.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; MAXT = 5


def sim_scale(side, entry, sl0, targets, weights, ph, pl, pc, be):
    sl = sl0; pos = 1.0; realized = 0.0; ti = 0; nt = len(targets)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return realized + pos * side * (sl - entry) / entry, off + 1
        while ti < nt and ((hi >= targets[ti]) if side > 0 else (lo <= targets[ti])):
            realized += weights[ti] * side * (targets[ti] - entry) / entry; pos -= weights[ti]; ti += 1
            if be and ti == 1:
                sl = entry
            if pos <= 1e-9:
                return realized, off + 1
    return realized + (pos * side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def sim_trail(side, entry, sl0, tiers, ph, pl, pc):
    sl = sl0; reached = 0; nt = len(tiers)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl - entry) / entry, off + 1
        while reached < nt and ((hi >= tiers[reached]) if side > 0 else (lo <= tiers[reached])):
            reached += 1; sl = entry if reached == 1 else tiers[reached - 2]
            if reached >= nt:
                return side * (tiers[-1] - entry) / entry, off + 1
    return (side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


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

    rows = []                                                 # (k, year, bo_str, t_reff50, retBE, offBE, retTR, offTR)
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; L = rhi - rlo
        brk = rhi if up else rlo; sl0 = rlo if up else rhi
        tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
        base = reward_eff.strength_baseline(A, k)
        if not base or base.get("vol") is None:
            continue
        st = reward_eff.strength(A, k, k, base=base)
        bo_str = st["buy" if up else "sell"]["effort_z"] if st["ok"] else 0.0
        sh, ok = reward_eff.share(A, k - 49, k); t_reff50 = (sh if up else 100.0 - sh) if ok else 50.0
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        rBE, oBE = sim_scale(s, C[k], sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
        rTR, oTR = sim_trail(s, C[k], sl0, tiers, ph, pl, pc)
        rows.append((k, int(yr[k]), bo_str, t_reff50, rBE - FEE, oBE, rTR - FEE, oTR))
    R = rows

    def run(reti, offi, keep):
        res = {2025: [], 2026: []}; last = -1
        for r in R:
            k = r[0]
            if k <= last or not keep(r):
                continue
            res[r[1]].append(r[reti]); last = k + r[offi]
        return res

    def line(tag, res):
        for Y in (2025, 2026):
            a = np.array(res[Y])
            if len(a) < 15:
                print("      %-22s %d n<15" % (tag, Y)); continue
            print("      %-22s %d  n=%-4d win=%.0f%%  avg=%+.3f%%  net=%+.0f%%"
                  % (tag, Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100), flush=True)

    FILT = [("baseline (all)", lambda r: True),
            ("bo_str > 0", lambda r: r[2] > 0),
            ("bo_str >= 0.5", lambda r: r[2] >= 0.5),
            ("t_reff50 > 50", lambda r: r[3] > 50),
            ("bo_str>=.5 & reff50>50", lambda r: r[2] >= 0.5 and r[3] > 50)]
    print("\n========  TF = %s   (events=%d)  ========" % (tf, len(R)), flush=True)
    for schname, reti, offi in (("scale+BE", 4, 5), ("trail", 6, 7)):
        print("  --- %s ---" % schname, flush=True)
        for fname, keep in FILT:
            line(fname, run(reti, offi, keep))


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "15m", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
