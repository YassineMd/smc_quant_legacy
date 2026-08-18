"""TRADEABILITY backtest of the tiered RADAR-WALL BREAKOUT. Entry = breakout bar close (resisted wall: open inside
[radar_lo,radar_hi], close beyond the defended extreme). SL = opposite radar extreme. Tier N target = broken extreme
+/- N*L, L = radar_hi-radar_lo. Barrier first-passage, SL-first on a same-bar tie (conservative). 0.04% RT fee.
NON-OVERLAP enforced PER SCHEME (a trade holds the account until its own exit bar). Both recon years, 5m/15m/1h.

Exit schemes compared:
  1x / 2x / 3x     : single fixed target at that tier (SL = opposite extreme)
  scale            : 1/3 out at 1x, 1/3 at 2x, 1/3 at 3x ; SL = opposite extreme for the remainder
  scale+BE         : same, but SL -> breakeven once 1x fills
  trail            : hold FULL size, trail SL by tier (BE after 1x, then to (N-1)x), take profit at 5x (hold the runner)
Usage: python study/wall_breakout_backtest.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; MAXT = 5


def sim_scale(side, entry, sl0, targets, weights, ph, pl, pc, be):
    sl = sl0; pos = 1.0; realized = 0.0; ti = 0; nt = len(targets)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            realized += pos * side * (sl - entry) / entry
            return realized, off + 1
        while ti < nt and ((hi >= targets[ti]) if side > 0 else (lo <= targets[ti])):
            realized += weights[ti] * side * (targets[ti] - entry) / entry; pos -= weights[ti]; ti += 1
            if be and ti == 1:
                sl = entry
            if pos <= 1e-9:
                return realized, off + 1
    realized += pos * side * (pc[-1] - entry) / entry if len(pc) else 0.0
    return realized, len(ph)


def sim_trail(side, entry, sl0, tiers, ph, pl, pc):
    sl = sl0; reached = 0; nt = len(tiers)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl - entry) / entry, off + 1
        while reached < nt and ((hi >= tiers[reached]) if side > 0 else (lo <= tiers[reached])):
            reached += 1
            sl = entry if reached == 1 else tiers[reached - 2]
            if reached >= nt:
                return side * (tiers[-1] - entry) / entry, off + 1
    return (side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def study(tf, root="study/recon_archive"):
    A = sorted(load_archive(tf, root=root)[1], key=lambda b: _f(b.get("start_time", 0)))
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
                    ev[(k, side)] = (rlo, rhi, band)
                    break
        if c1 >= n:
            break
        c0 += 5000
    keys = sorted(ev)

    def run(scheme, sel=lambda bp: True):
        res = {2025: [], 2026: []}; last_exit = -1
        for (k, side) in keys:
            if k <= last_exit or k + 1 >= n:
                continue
            rlo, rhi, band = ev[(k, side)]; bp = band / C[k]
            if not sel(bp):
                continue
            s = 1 if side == "S" else -1; entry = C[k]; L = rhi - rlo
            brk = rhi if side == "S" else rlo; sl0 = rlo if side == "S" else rhi
            tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
            j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
            if scheme in ("1x", "2x", "3x"):
                N = int(scheme[0]); ret, off = sim_scale(s, entry, sl0, [tiers[N - 1]], [1.0], ph, pl, pc, False)
            elif scheme == "scale":
                ret, off = sim_scale(s, entry, sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, False)
            elif scheme == "scale+BE":
                ret, off = sim_scale(s, entry, sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
            else:
                ret, off = sim_trail(s, entry, sl0, tiers, ph, pl, pc)
            ret -= FEE
            res[int(yr[k])].append(ret); last_exit = k + off
        return res

    def rep(tag, res):
        for Y in (2025, 2026):
            r = np.array(res[Y])
            if len(r) < 20:
                print("    %-9s %d  n<20" % (tag, Y)); continue
            net = r.sum() * 100; win = 100.0 * (r > 0).mean(); comp = (np.prod(1 + r) - 1) * 100
            print("    %-9s %d  n=%-4d win=%.0f%%  net=%+.0f%%  avg=%+.3f%%  comp=%+.0f%%"
                  % (tag, Y, len(r), win, net, r.mean() * 100, comp), flush=True)

    print("\n============  TF = %s  (events=%d, fee=%.2f%% RT)  ============" % (tf, len(ev), FEE * 100), flush=True)
    for sch in ("1x", "2x", "3x", "scale", "scale+BE", "trail"):
        rep(sch, run(sch))
    # best-looking scheme on the BIGGER-band half (walls whose 1x move clears the fee more comfortably)
    med = np.median([ev[key][2] / C[key[0]] for key in keys])
    print("    -- trail, big-band half (bandpct >= %.4f) --" % med, flush=True)
    rep("trail/big", run("trail", sel=lambda bp: bp >= med))


if __name__ == "__main__":
    # --clock -> run against study/clock_archive (time candles); else the volume recon_archive (default)
    _root = "study/clock_archive" if "--clock" in sys.argv else "study/recon_archive"
    _tfs = [a for a in sys.argv[1:] if not a.startswith("-")]
    for tf in (_tfs or ["15m", "1h", "5m"]):
        try:
            study(tf, root=_root)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
