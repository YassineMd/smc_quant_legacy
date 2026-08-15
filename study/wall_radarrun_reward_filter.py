"""Does a wall sitting INSIDE a SAME-SIDE (breakout-aligned) REWARD/EFF AREA improve the RADAR RUNNER continuation?
(Filter test on the proven edge, NOT a reversal test.) Radar Runner = resisted wall -> breakout bar (open in radar,
close beyond the defended extreme) -> tiered run. Support wall breaks UP (long) / resistance breaks DOWN (short); the
buy reward/eff zone is the breakout-aligned side for an S wall, the sell zone for an R wall.

FILTER = at the breakout bar, the wall band [P-band,P+band] OVERLAPS an ACTIVE (unmitigated) same-side reward/eff zone.
Compare tiered P&L (scale+BE, trail; 0.04% RT fee; taken()-nonoverlap) across BASELINE (all events) / REAL-overlap /
PLACEBO-overlap (zones shifted +-1..3%), BOTH years. A real filter must beat BOTH baseline and placebo avg/trade in both
years. CLI: python study/wall_radarrun_reward_filter.py [tf ...]"""
import os, sys, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; MAXT = 5
NPLAC = 4; MAXLIFE = 500; STRONG = 40.0
random.seed(12345)


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


def reward_zones(A):
    """{'S': buy-zone list, 'R': sell-zone list}, each (i, zlo, zhi, mit, strength), sorted by i. S wall <-> buy zone."""
    H_ = [_f(b.get("high")) for b in A]; L_ = [_f(b.get("low")) for b in A]
    C_ = [_f(b.get("close", b.get("close_price"))) for b in A]; n = len(A)
    buy = []; sell = []
    for (i, side, strength) in reward_eff.switches(A):
        hi = H_[i]; lo = L_[i]
        if hi <= 0 or lo <= 0:
            continue
        mid = 0.5 * (hi + lo); half = max(0.5 * (hi - lo), mid * 0.00025)
        ylo = mid - half; yhi = mid + half; mit = n
        if side == "buy":
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if C_[kk] < ylo:
                    mit = kk; break
            buy.append((i, ylo, yhi, mit, strength))
        else:
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if C_[kk] > yhi:
                    mit = kk; break
            sell.append((i, ylo, yhi, mit, strength))
    return {"S": buy, "R": sell}


def overlap(P, band, k, wside, zones, shift, strong_only):
    plo = P - band; phi = P + band
    for (i, zlo, zhi, mit, strength) in zones[wside]:
        if i > k:
            break                                            # zones sorted by i -> none later can be active at k
        if strong_only and strength < STRONG:
            continue
        if k > min(mit, i + MAXLIFE):
            continue
        if plo <= zhi * (1.0 + shift) and phi >= zlo * (1.0 + shift):
            return True
    return False


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
    zones = reward_zones(A)

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
                    ev[(k, side)] = (P, band, rlo, rhi); break
        if c1 >= n:
            break
        c0 += 5000

    rows = []      # (k, year, side, P, band, retBE, offBE, retTR, offTR, bias)
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        P, band, rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; L = rhi - rlo
        brk = rhi if up else rlo; sl0 = rlo if up else rhi
        tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
        TP = tiers[0]; SL = sl0; bias = -1
        for j in range(k + 1, min(n, k + 1 + H)):
            if (Lo[j] <= SL) if up else (Hi[j] >= SL):
                bias = 0; break
            if (Hi[j] >= TP) if up else (Lo[j] <= TP):
                bias = 1; break
        if bias < 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        rBE, oBE = sim_scale(s, C[k], sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
        rTR, oTR = sim_trail(s, C[k], sl0, tiers, ph, pl, pc)
        rows.append((k, int(yr[k]), side, P, band, rBE - FEE, oBE, rTR - FEE, oTR, bias))
    print("\n================  TF = %s   (radar-runner events=%d)  ================" % (tf, len(rows)), flush=True)
    if len(rows) < 80:
        print("  too few events"); return

    def stats(sel, reti, offi):
        res = {2025: [], 2026: []}; last = -1
        for r in rows:
            k = r[0]
            if k <= last or not sel(r):
                continue
            res[r[1]].append(r[reti]); last = k + int(r[offi])
        out = {}
        for Y in (2025, 2026):
            a = np.array(res[Y])
            out[Y] = (len(a), 100 * (a > 0).mean() if len(a) else 0.0, a.mean() * 100 if len(a) else 0.0,
                      a.sum() * 100 if len(a) else 0.0)
        return out

    # precompute overlap flags
    for strong_only in (False, True):
        rmark = {r[0]: overlap(r[3], r[4], r[0], r[2], zones, 0.0, strong_only) for r in rows}
        shifts = [random.uniform(0.01, 0.03) * random.choice((-1, 1)) for _ in range(NPLAC)]
        pmark = [{r[0]: overlap(r[3], r[4], r[0], r[2], zones, sh, strong_only) for r in rows} for sh in shifts]
        novl = sum(1 for r in rows if rmark[r[0]])
        print("  ----- %s   (real-overlap events=%d / %d) -----"
              % ("STRONG zones" if strong_only else "ALL zones", novl, len(rows)), flush=True)
        for schname, reti, offi in (("scale+BE", 5, 6), ("trail", 7, 8)):
            base = stats(lambda r: True, reti, offi)
            real = stats(lambda r: rmark[r[0]], reti, offi)
            print("    --- %s ---" % schname, flush=True)
            for Y in (2025, 2026):
                # placebo pooled across draws
                pv = []
                for pm in pmark:
                    st = stats(lambda r, pm=pm: pm[r[0]], reti, offi)[Y]
                    if st[0] >= 8:
                        pv.append(st[2])
                pavg = float(np.mean(pv)) if pv else float("nan")
                bn, bw, ba, bnet = base[Y]; rn, rw, ra, rnet = real[Y]
                if rn < 12:
                    print("      %d  REAL-ovl n=%d (<12, skip)  [baseline avg=%+.3f%% n=%d]" % (Y, rn, ba, bn)); continue
                flag = "  <== helps" if (ra - ba >= 0.02 and ra - pavg >= 0.02) else ""
                print("      %d  base avg=%+.3f%%(n%d win%.0f)  REAL-ovl avg=%+.3f%%(n%d win%.0f net%+.0f)  "
                      "placebo avg=%+.3f%%   dBase=%+.3f dPlac=%+.3f%s"
                      % (Y, ba, bn, bw, ra, rn, rw, rnet, pavg, ra - ba, ra - pavg, flag), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
