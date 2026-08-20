"""RadarRun with a 1m-BUCKET-WALL stop, NOTIONAL sizing (user 2026-08-20). Entry = RR breakout close, TP 0.25%. SL =
0.1%% beyond the CLOSEST DEFENDING 1m-bucket wall (support below a long / resistance above a short) formed within a
lookback before entry -- but ONLY IF that is TIGHTER than the shipped candle-capped stop; else keep the candle stop.
Sizing = NOTIONAL f=1.0 (10%% margin x10 = full $200k account -> per-trade acct move = net price-return). Because the
stop can be tight (~0.2%%) vs the 0.25%% TP, the TP/SL race is resolved on the 1m-BUCKET PATH (not the coarse HTF bar),
which is the honest resolution for a tight stop. HyroTrader MC (target10 / max6 trail / daily 3&4 trail).

CHOICES (state so they can be corrected): 'in favor' = defending-side wall; lookback 24h; 1m-path first-touch, stop-first
same-1m-bar; notional books the loss AT the stop (tight stop => booked ~= floating MAE). python study/radarrun_1mwall_sl.py [smoke]"""
import os, sys, bisect, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
from app import absorption_level_detect as AL

FEE, SLIP, TP = 0.0004, 0.0003, 0.0025
BUF = 0.001                # 0.1% beyond the wall
LOOKBACK = 86400           # 24h window for a "close recent" 1m wall
MAXBARS_1M = 6000          # ~100h cap on the 1m path walk
TARGET, MAXDD = 10.0, 6.0
NPATH, MAXD = 20000, 400
SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def load_1m():
    A = sorted(load_archive("1m", root="study/recon_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    ST = np.array([_f(b.get("start_time")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    walls = AL.detect(A, skip_last=False)
    wl = []
    for w in walls:
        i0 = int(w.get("i0", -1)); side = w.get("side"); P = _f(w.get("price"))
        if 0 <= i0 < len(ST) and side in ("S", "R") and P > 0:
            wl.append((float(ST[i0]), P, side))
    wl.sort()
    wt = [x[0] for x in wl]
    return ST, Hi, Lo, C, wl, wt


def wall_sl(E, P0, s, wl, wt):
    lo = bisect.bisect_left(wt, E - LOOKBACK); hi = bisect.bisect_right(wt, E)
    best = None
    for j in range(lo, hi):
        _ft, price, side = wl[j]
        if s > 0 and side == "S" and price < P0:
            if best is None or price > best:
                best = price
        elif s < 0 and side == "R" and price > P0:
            if best is None or price < best:
                best = price
    if best is None:
        return None
    return best * (1 - BUF) if s > 0 else best * (1 + BUF)


def resolve_1m(E, entry, tp, sl, s, ST1, H1, L1, C1):
    j0 = bisect.bisect_left(ST1, E); n = len(ST1)
    for j in range(j0, min(n, j0 + MAXBARS_1M)):
        hi = H1[j]; lo = L1[j]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, float(ST1[j])
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, float(ST1[j])
    j = min(n - 1, j0 + MAXBARS_1M)
    return "end", s * (C1[j] - entry) / entry, float(ST1[j])


def build_signals():
    sigs = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        for (k, s, entry, csl, dist, ts) in detect(A, SLBUF.get(tf, 0.003))[0]:
            sigs.append((float(ts), s, float(entry), float(csl)))
    sigs.sort(); return sigs


def eval_variant(sigs, ST1, H1, L1, C1, wl, wt, use_wall):
    tr = []; last_t = -1.0; wall_used = 0
    for (ts, s, entry, csl) in sigs:
        if ts <= last_t:
            continue
        sl = csl
        if use_wall:
            wsl = wall_sl(ts, entry, s, wl, wt)
            if wsl is not None:                                  # tighter = higher for long / lower for short, still valid
                if s > 0 and entry > wsl > csl:
                    sl = wsl; wall_used += 1
                elif s < 0 and entry < wsl < csl:
                    sl = wsl; wall_used += 1
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        outc, gross, xt = resolve_1m(ts, entry, entry * (1 + s * TP), sl, s, ST1, H1, L1, C1)
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((ts, net, dist)); last_t = xt
    return tr, wall_used


def day_blocks(tr):
    by = {}
    for ts, net, _d in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(net)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc_notional(days, daily_lim):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for _dn in range(1, MAXD + 1):
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
            passes += 1; dtp.append(_dn)
    med = np.percentile(dtp, 50) if dtp else 0
    return dict(p=100.0 * passes / NPATH, med=med, dd99=np.percentile(mdds, 99), worst=max(mdds))


def report(name, tr, wall_used, n_sig):
    nets = np.array([t[1] for t in tr]) * 100.0; dists = np.array([t[2] for t in tr]) * 100.0
    w = nets[nets > 0]; l = nets[nets <= 0]
    print("  %-16s n=%d  win %.1f%%  avgWin %+.3f%%  avgLoss %+.3f%%  exp %+.4f%%  avgSL %.3f%%  wall-SL used %d (%.0f%%)"
          % (name, len(tr), 100.0 * (nets > 0).mean(), w.mean() if len(w) else 0, l.mean() if len(l) else 0,
             nets.mean(), dists.mean(), wall_used, 100.0 * wall_used / max(1, len(tr))), flush=True)
    days = day_blocks(tr)
    for dl in (3.0, 4.0):
        m = mc_notional(days, dl)
        print("      NOTIONAL f1.0 daily%.0f%%:  pass %.1f%%  med %.0fd  DDp99 %.1f%%  worst-path %.1f%%"
              % (dl, m["p"], m["med"], m["dd99"], m["worst"]), flush=True)


def main():
    smoke = len(sys.argv) > 1 and sys.argv[1] == "smoke"
    print("RadarRun + 1m-wall SL | NOTIONAL 10%%x10 (f1.0) | TP 0.25%% | 1m-path resolved | HyroTrader target10/max6-trail\n", flush=True)
    ST1, H1, L1, C1, wl, wt = load_1m()
    print("  (1m bucket bars=%d, 1m walls=%d, span %s..%s)\n"
          % (len(ST1), len(wl),
             datetime.fromtimestamp(ST1[0], tz=timezone.utc).date(), datetime.fromtimestamp(ST1[-1], tz=timezone.utc).date()), flush=True)
    if smoke:
        A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        sigs = [(float(ts), s, float(e), float(c)) for (k, s, e, c, d, ts) in detect(A, 0.003)[0]]
        sigs.sort()
        sigs = [x for x in sigs if x[0] <= ST1[-1]]
        print("SMOKE 30m-bucket only, %d signals within 1m span" % len(sigs), flush=True)
    else:
        sigs = [x for x in build_signals() if x[0] <= ST1[-1]]
        print("FULL pooled 15c+30c+30bkt, %d signals within 1m span" % len(sigs), flush=True)
    tb, _ = eval_variant(sigs, ST1, H1, L1, C1, wl, wt, use_wall=False)
    tw, wu = eval_variant(sigs, ST1, H1, L1, C1, wl, wt, use_wall=True)
    print("", flush=True)
    report("BASELINE candle-SL", tb, 0, len(sigs))
    report("1m-WALL SL", tw, wu, len(sigs))


if __name__ == "__main__":
    main()
