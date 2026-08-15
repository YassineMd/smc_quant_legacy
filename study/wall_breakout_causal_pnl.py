"""CAUSAL-GEOMETRY P&L for the tiered radar-wall breakout. For each candidate breakout at bar k, RE-DETECT the wall
using ONLY bars [k-CW, k-1] (strictly before the breakout) and derive entry/SL/tier-targets from THAT causal radar.
Run the exit schemes with causal geometry AND (for comparison) with the chunk geometry, side by side. Events with no
causally-present matching wall are dropped. Per-event causal detect is ~0.3s, so we SAMPLE (win% and avg/trade are
sampling-robust; net is net-on-sample). Both recon years, 5m/15m/1h, 0.04% RT fee, SL-first tie, per-scheme non-overlap.

Usage: python study/wall_breakout_causal_pnl.py [SAMPLE] [tf ...]   (default SAMPLE=700, tfs 1h 15m 5m)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; MAXT = 5; CW = 3000


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


def run_schemes(side, entry, rlo, rhi, ph, pl, pc):
    s = 1 if side == "S" else -1; L = rhi - rlo
    brk = rhi if side == "S" else rlo; sl0 = rlo if side == "S" else rhi
    tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
    out = {}
    out["1x"] = sim_scale(s, entry, sl0, [tiers[0]], [1.0], ph, pl, pc, False)
    out["scale+BE"] = sim_scale(s, entry, sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
    out["trail"] = sim_trail(s, entry, sl0, tiers, ph, pl, pc)
    return {k: (v[0] - FEE, v[1]) for k, v in out.items()}


def causal_geom(A, k, side, P0, O_k, C_k):
    lo = max(0, k - CW); win = A[lo:k]
    if len(win) < 60:
        return None
    best = None; bd = 1e18
    for w in AL.detect(win, skip_last=False):
        if w.get("side") != side:
            continue
        band = _f(w.get("band")); P = _f(w.get("price"))
        if band <= 0 or P <= 0:
            continue
        rlo = P - RM * band; rhi = P + RM * band
        if not (rlo <= O_k <= rhi):
            continue
        if not ((C_k > rhi) if side == "S" else (C_k < rlo)):
            continue
        if abs(P - P0) < bd:
            bd = abs(P - P0); best = (rlo, rhi)
    return best


def study(tf, sample):
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
                    ev[(k, side)] = (P, band, rlo, rhi)
                    break
        if c1 >= n:
            break
        c0 += 5000
    keys = sorted(ev)
    if len(keys) > sample:
        step = len(keys) / sample; keys = [keys[int(i * step)] for i in range(sample)]

    SCH = ("1x", "scale+BE", "trail")
    chunk = {Y: {s: [] for s in SCH} for Y in (2025, 2026)}
    caus = {Y: {s: [] for s in SCH} for Y in (2025, 2026)}
    lc = {s: -1 for s in SCH}; lk = {s: -1 for s in SCH}; surv = 0
    for (k, side) in keys:
        if k + 1 >= n:
            continue
        P0, band0, rlo0, rhi0 = ev[(k, side)]; Y = int(yr[k])
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        rc = causal_geom(A, k, side, P0, O[k], C[k])
        ch = run_schemes(side, C[k], rlo0, rhi0, ph, pl, pc)
        cu = run_schemes(side, C[k], rc[0], rc[1], ph, pl, pc) if rc else None
        if rc:
            surv += 1
        for s in SCH:
            if k > lc[s]:                                     # per-scheme non-overlap (chunk exit bar drives it)
                chunk[Y][s].append(ch[s][0])
                if cu:
                    caus[Y][s].append(cu[s][0])
                lc[s] = k + ch[s][1]
    print("\n======  TF = %s  (sampled=%d, causal-survived=%.0f%%)  ======" % (tf, len(keys), 100.0 * surv / max(1, len(keys))), flush=True)
    for s in SCH:
        for Y in (2025, 2026):
            ch = np.array(chunk[Y][s]); cu = np.array(caus[Y][s])
            if len(ch) < 15:
                print("  %-9s %d n<15" % (s, Y)); continue
            print("  %-9s %d  CHUNK win=%.0f%% avg=%+.3f%% net=%+.0f%% (n=%d) | CAUSAL win=%.0f%% avg=%+.3f%% net=%+.0f%% (n=%d)"
                  % (s, Y, 100 * (ch > 0).mean(), ch.mean() * 100, ch.sum() * 100, len(ch),
                     100 * (cu > 0).mean(), cu.mean() * 100, cu.sum() * 100, len(cu)), flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    sample = int(args[0]) if args and args[0].isdigit() else 700
    tfs = [a for a in args if not a.isdigit()] or ["1h", "15m", "5m"]
    for tf in tfs:
        try:
            study(tf, sample)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
