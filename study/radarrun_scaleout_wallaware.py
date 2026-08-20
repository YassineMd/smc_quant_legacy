"""WALL-AWARE TP2 for the RR scale-out. 50% at TP1 (0.2% net) + 50% at TP2, stop->BE after TP1, 30c+30bkt notional maker.
BASELINE: TP2 fixed at 0.4% net (gross 0.44%). WALL-AWARE: the runner's TP2 = min(0.44%, just-below the nearest OPPOSING
wall) -- if a ceiling (resistance above a long / support below a short) sits inside 0.44%, bank there instead of holding
for a TP2 the wall likely blocks (study/radarrun_wall_retracement: opp-wall caps the run). Floored at TP1. Compare outcome
mix, win%, avg trade, and NOTIONAL HyroTrader pass/DD. Walls formed causally. IN-SAMPLE. python study/radarrun_scaleout_wallaware.py"""
import os, sys, bisect, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
from app import absorption_level_detect as AL
FEE, H, RM = 0.0004, 200, 3.0
G1, G2, BUF = 0.0024, 0.0044, 0.0005          # gross TP1 0.24% (net .2), TP2 0.44% (net .4); take profit 0.05% before the wall
TN1 = G1 - FEE                                # tranche-A net at TP1
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def all_walls(A):
    n = len(A); Sw = []; Rw = []; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step)
        try:
            ws = AL.detect(A[c0:c1], skip_last=False, radar_mult=RM)
        except Exception:
            ws = []
        for w in ws:
            side = w.get("side"); P = _f(w.get("price")); i0 = int(w.get("i0", -1))
            if side in ("S", "R") and P > 0 and i0 >= 0:
                (Sw if side == "S" else Rw).append((i0 + c0, P))
        if c1 >= n:
            break
        c0 += step - 1000
    Sw.sort(); Rw.sort()
    return Sw, Rw


def nearest(walls, i0s, k, entry, below):
    idx = bisect.bisect_right(i0s, k); best = None
    for j in range(idx):
        p = walls[j][1]
        if below and p < entry:
            d = entry - p
        elif (not below) and p > entry:
            d = p - entry
        else:
            continue
        if best is None or d < best:
            best = d
    return best


def sim_scaleout(s, entry, sl, tp2f, ph, pl, pc):
    """50% @ G1 + 50% @ tp2f (gross), stop->BE after TP1. returns (full_net, outcome, off)."""
    tn2 = tp2f - FEE
    tp1 = entry * (1 + s * G1); tp2 = entry * (1 + s * tp2f); sl_dist = abs(entry - sl) / entry
    hit1 = False; slp = sl; m = len(ph)
    for off in range(m):
        hi = ph[off]; lo = pl[off]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):
                return -sl_dist - FEE, "stop", off + 1
            if (hi >= tp1) if s > 0 else (lo <= tp1):
                hit1 = True; slp = entry
                if (hi >= tp2) if s > 0 else (lo <= tp2):
                    return 0.5 * TN1 + 0.5 * tn2, "both", off + 1
        else:
            if (lo <= slp) if s > 0 else (hi >= slp):
                return 0.5 * TN1 + 0.5 * (-FEE), "tp1_be", off + 1
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * TN1 + 0.5 * tn2, "both", off + 1
    if hit1:
        netB = s * (pc[-1] - entry) / entry - FEE
        return 0.5 * TN1 + 0.5 * netB, "tp1_end", m
    return s * (pc[-1] - entry) / entry - FEE, "stop", m


def day_blocks(tr):
    by = {}
    for ts, net in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(net)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc(days, daily_lim):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for net in day:
                eq += net * 100.0; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if ipeak - eq >= daily_lim:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(dnum)
    return dict(p=100.0 * passes / NPATH, med=(np.percentile(dtp, 50) if dtp else 0),
                dd99=np.percentile(mdds, 99), worst=max(mdds))


def build(wall_aware):
    tr = []; oc = {"stop": 0, "tp1_be": 0, "tp1_end": 0, "both": 0}; capped = 0; tot = 0
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A)
        Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        Sw, Rw = all_walls(A); S_i0 = [w[0] for w in Sw]; R_i0 = [w[0] for w in Rw]
        last = -1
        for (k, s, entry, sl, dist, ts) in detect(A, SLBUF.get(tf, 0.003))[0]:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            tot += 1; tp2f = G2
            if wall_aware:
                opp = nearest(Rw, R_i0, k, entry, False) if s > 0 else nearest(Sw, S_i0, k, entry, True)
                if opp is not None:
                    od = opp / entry
                    cap = min(G2, max(G1, od - BUF))            # runner target: just below the wall, floored at TP1, capped at TP2
                    if cap < G2:
                        capped += 1
                    tp2f = cap
            net, o, off = sim_scaleout(s, entry, sl, tp2f, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            oc[o] += 1; tr.append((float(ST[k]), net)); last = k + int(off)
    tr.sort()
    return tr, oc, capped, tot


def report(name, tr, oc, capped, tot):
    nets = np.array([t[1] for t in tr]) * 100.0; N = len(tr)
    days = day_blocks(tr)
    print("=" * 88, flush=True)
    print("%s   (%d trades, wall-capped TP2 on %d = %.0f%%)" % (name, N, capped, 100.0 * capped / max(1, tot)), flush=True)
    print("  both-TP %.1f%% | TP1-then-BE %.1f%% | full-stop %.1f%% | net>0 %.1f%% | avg trade %+.4f%% | worst %+.3f%%"
          % (100 * oc["both"] / N, 100 * oc["tp1_be"] / N, 100 * oc["stop"] / N, 100 * (nets > 0).mean(), nets.mean(), nets.min()), flush=True)
    for dl in (3.0, 4.0):
        m = mc(days, dl)
        print("    NOTIONAL daily%.0f%%: pass %.1f%% med %.0fd DDp99 %.1f%% worst-path %.1f%%" % (dl, m["p"], m["med"], m["dd99"], m["worst"]), flush=True)
    print("", flush=True)


def main():
    print("WALL-AWARE TP2 scale-out vs fixed-TP2 | 30c+30bkt | notional 10x10 maker | HyroTrader $200k | IN-SAMPLE\n", flush=True)
    trb, ocb, _, tb = build(False)
    trw, ocw, cap, tw = build(True)
    report("BASELINE  fixed TP2 0.4%", trb, ocb, 0, tb)
    report("WALL-AWARE TP2 (min of 0.4% / opp-wall)", trw, ocw, cap, tw)


if __name__ == "__main__":
    main()
