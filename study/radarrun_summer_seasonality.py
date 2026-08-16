"""HYPOTHESIS (user): the Radar Runner underperformed on the daemon/forward data because that window is mostly SUMMER,
and SOL volume + velocity run low in summer. Test it two ways, DESCRIPTIVELY:

  PART A - is summer actually low volume/velocity?  Per calendar month over the FULL timeline (recon 2025-01..2026-06
           + forward cold-archive 2026-06-20..now), 1h buckets: daily $volume (Σ curr_vol), bars/day, daily range %,
           per-bar |return| %. Summer = Jun/Jul/Aug. Compare summer vs the rest, and the forward window vs recon.
  PART B - does the EDGE track it?  Radar Runner shipped 1h spec (MINVISIT=1, candle-SL 0.2% + 0.5% TP) over the same
           timeline, signals bucketed by month -> win / avg%net / exp-R for summer vs non-summer (recon 2025 has a full
           summer, so this is a clean in-sample seasonal read, not confounded by the 2026 forward regime).

3bps slip, 0.04% fee. python study/radarrun_summer_seasonality.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003
SUMMER = {6, 7, 8}


def load_1h():
    rec = sorted(load_archive("1h", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    try:
        fwd = sorted(load_archive("1h")[1], key=lambda b: _f(b.get("start_time", 0)))   # default root = cold-archive (forward)
    except Exception:
        fwd = []
    cut = _f(rec[-1].get("start_time", 0)) if rec else 0.0
    fwd = [b for b in fwd if _f(b.get("start_time", 0)) > cut]                            # strictly after recon
    return rec, fwd


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def part_a(rec, fwd):
    print("\n############  PART A - volume & velocity by month (1h buckets)  ############", flush=True)
    allb = rec + fwd
    day = {}                                                   # UTC date -> [vol, nbars, hi, lo, open, close, Σabsret, Σrng]
    for b in allb:
        ts = _f(b.get("start_time", 0.0))
        if ts <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        key = dt.date()
        o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
        hi = _f(b.get("high")); lo = _f(b.get("low")); v = _f(b.get("curr_vol"))
        if o <= 0:
            continue
        d = day.get(key)
        if d is None:
            d = day[key] = [0.0, 0, lo, hi, o, c, 0.0, 0.0]
        d[0] += v; d[1] += 1; d[2] = min(d[2], lo); d[3] = max(d[3], hi); d[5] = c
        d[6] += abs(c - o) / o; d[7] += (hi - lo) / o
    # per month aggregate
    mon = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0])       # (yyyy,mm) -> [ndays, Σvol, Σrange%, Σbars, Σabsret%, Σbars_for_ret]
    for dte, d in day.items():
        vol, nb, lo, hi, o, c, sar, srg = d
        if nb < 2 or o <= 0:
            continue
        mk = (dte.year, dte.month)
        m = mon[mk]
        m[0] += 1; m[1] += vol; m[2] += (hi - lo) / o * 100.0; m[3] += nb
        m[4] += (sar / nb) * 100.0; m[5] += 1
    print("  %-8s %6s %12s %8s %9s %9s" % ("month", "ndays", "vol/day", "bars/dy", "rng%/dy", "|ret|%/bar"), flush=True)
    sums = {True: [], False: []}
    for mk in sorted(mon):
        m = mon[mk]; nd = m[0]
        if nd < 5:
            continue
        vpd = m[1] / nd; rpd = m[2] / nd; bpd = m[3] / nd; arb = m[4] / m[5]
        tag = "  <SUMMER" if mk[1] in SUMMER else ""
        print("  %04d-%02d %6d %12.0f %8.1f %9.2f %9.3f%s" % (mk[0], mk[1], nd, vpd, bpd, rpd, arb, tag), flush=True)
        sums[mk[1] in SUMMER].append((vpd, bpd, rpd, arb))
    for lbl, key in (("SUMMER (Jun-Aug)", True), ("REST (Sep-May)", False)):
        a = np.array(sums[key])
        if len(a):
            print("  -> %-18s vol/day=%10.0f  bars/day=%5.1f  rng%%/day=%.2f  |ret|%%/bar=%.3f  (n=%d months)" % (
                lbl, a[:, 0].mean(), a[:, 1].mean(), a[:, 2].mean(), a[:, 3].mean(), len(a)), flush=True)
    if sums[True] and sums[False]:
        s = np.array(sums[True]).mean(0); r = np.array(sums[False]).mean(0)
        print("  -> SUMMER vs REST ratio:  vol=%.2f x  bars=%.2f x(!june-boundary artifact)  range=%.2f x  velocity=%.2f x" % (
            s[0] / r[0], s[1] / r[1], s[2] / r[2], s[3] / r[3]), flush=True)
    return {mk: (mon[mk][4] / mon[mk][5]) for mk in mon if mon[mk][0] >= 5 and mon[mk][5] > 0}   # (y,m)->|ret|%/bar velocity


def part_c(rows_rec, mon_vel):
    """Does the edge track VELOCITY (not the calendar)? Split RECON signals by their month's |ret|/bar into the low
    vs high half (median of recon months). If low-velocity months keep the edge, velocity is NOT the driver."""
    print("\n############  PART C - RECON edge by VELOCITY regime (the real variable)  ############", flush=True)
    vels = sorted(v for (y, m), v in mon_vel.items() if y <= 2026 and (y, m) <= (2026, 6))
    if not vels:
        print("  (no recon months)"); return
    med = vels[len(vels) // 2]
    lowv = [x for x in rows_rec if mon_vel.get((x[0], x[1]), med) < med]
    hiv = [x for x in rows_rec if mon_vel.get((x[0], x[1]), med) >= med]
    print("  median recon monthly velocity = %.3f %%/bar" % med, flush=True)
    for lbl, g in (("LOW-velocity months ", lowv), ("HIGH-velocity months", hiv)):
        if len(g) < 10:
            print("    %-20s n=%d (<10)" % (lbl, len(g))); continue
        net = np.array([x[2] for x in g]); rr = np.array([x[3] for x in g])
        print("    %-20s n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (
            lbl, len(g), 100 * (net > 0).mean(), net.mean() * 100, rr.mean()), flush=True)


def detect_rows(A):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
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
        c0 += step - 1000
    rows = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.002), rlo) if s > 0 else min(Hi[k] * (1 + 0.002), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        dt = datetime.fromtimestamp(ST[k], tz=timezone.utc)
        rows.append((dt.year, dt.month, net, net / dist)); last = k + int(off)
    return rows


def part_b(rec, fwd):
    print("\n############  PART B - Radar Runner 1h edge by season  ############", flush=True)
    rows_rec = detect_rows(rec)
    rows_fwd = detect_rows(fwd) if fwd else []

    def rep(rows, title):
        summer = [x for x in rows if x[1] in SUMMER]; rest = [x for x in rows if x[1] not in SUMMER]
        print("  %s" % title, flush=True)
        for lbl, g in (("SUMMER (Jun-Aug)", summer), ("REST   (Sep-May)", rest)):
            if len(g) < 10:
                print("    %-18s n=%d (<10)" % (lbl, len(g))); continue
            net = np.array([x[2] for x in g]); rr = np.array([x[3] for x in g])
            print("    %-18s n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (
                lbl, len(g), 100 * (net > 0).mean(), net.mean() * 100, rr.mean()), flush=True)
    rep(rows_rec, "RECON 2025-01..2026-06 (has a full summer 2025 -> clean seasonal read):")
    for Y in (2025, 2026):
        g = [x for x in rows_rec if x[0] == Y]
        if len(g) >= 10:
            net = np.array([x[2] for x in g])
            print("      recon %d only: n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (
                Y, len(g), 100 * (net > 0).mean(), net.mean() * 100, np.array([x[3] for x in g]).mean()), flush=True)
    if rows_fwd:
        rep(rows_fwd, "FORWARD cold-archive 2026-06-20..now (mostly summer 2026):")
    return rows_rec


def main():
    rec, fwd = load_1h()
    r0 = datetime.fromtimestamp(_f(rec[0].get("start_time")), tz=timezone.utc).date() if rec else "?"
    r1 = datetime.fromtimestamp(_f(rec[-1].get("start_time")), tz=timezone.utc).date() if rec else "?"
    if fwd:
        f0 = datetime.fromtimestamp(_f(fwd[0].get("start_time")), tz=timezone.utc).date()
        f1 = datetime.fromtimestamp(_f(fwd[-1].get("start_time")), tz=timezone.utc).date()
        print("recon 1h: %d bars %s..%s   |   forward 1h: %d bars %s..%s" % (len(rec), r0, r1, len(fwd), f0, f1), flush=True)
    else:
        print("recon 1h: %d bars %s..%s   |   forward: NONE loaded" % (len(rec), r0, r1), flush=True)
    mon_vel = part_a(rec, fwd)
    rows_rec = part_b(rec, fwd)
    part_c(rows_rec, mon_vel)


if __name__ == "__main__":
    main()
