"""RadarRun falsification battery H1-H3 — RE-ANALYSES of the EXISTING signal set (no new filters, no param search, no
signal-def change). Baseline = live config: TP 0.25%, candle-capped stop, RR-only, 15c+30c+30bkt pooled. Every number is
IN-SAMPLE (fitted on the same archive the config was selected on) => UPPER BOUND, not validated. python study/radarrun_h123.py

H1  tighter fixed stop cap (0.4/0.5/0.6/0.7%/uncapped): does cushion rise without materially dropping expectancy?
H2  time stop at N bars (2/3/5/8/none): does expectancy/cushion improve?
H3  edge by source x side (6 cells) + positive-cell restriction (flagged as cherry-pick upper bound)."""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF, FEE, SLIP
H = 200; TP = 0.0025
SRCS = [("study/clock_archive", "15m", "15c"), ("study/clock_archive", "30m", "30c"), ("study/recon_archive", "30m", "30bkt")]


def sim_ts(s, entry, tp, sl, ph, pl, pc, time_n):
    m = len(ph); lim = m if time_n is None else min(m, time_n)
    for off in range(lim):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):        # STOP first (shipped pessimistic tie)
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    if lim <= 0:
        return "end", 0.0, 1
    idx = lim - 1
    outc = "time" if (time_n is not None and lim == time_n and lim < m) else "end"
    return outc, s * (pc[idx] - entry) / entry, lim


def resim(det, source, stop_cap=None, time_n=None):
    sigs, Hi, Lo, C, n = det; out = []; last = -1
    for (k, s, entry, sl_c, dist_c, ts) in sigs:
        if k <= last:
            continue
        eff = min(dist_c, stop_cap) if stop_cap is not None else dist_c
        sl = entry * (1 - s * eff); tp_px = entry * (1 + s * TP)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim_ts(s, entry, tp_px, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1], time_n)
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((float(ts), net, eff, net > 0, outc, source, "long" if s > 0 else "short")); last = k + int(off)
    return out


def stats(rows):
    nets = np.array([r[1] for r in rows]) * 100.0
    if not len(nets):
        return None
    w = nets[nets > 0]; l = nets[nets <= 0]
    aw = w.mean() if len(w) else 0.0; al = l.mean() if len(l) else 0.0
    be = (-al) / (aw - al) * 100.0 if (aw - al) != 0 else float("nan")   # break-even win% from avg win / |avg loss|
    win = 100.0 * (nets > 0).mean()
    return dict(n=len(nets), win=win, aw=aw, al=al, be=be, exp=nets.mean(), cush=win - be)


def wilson(k, nn, z=1.96):
    if nn == 0:
        return (0.0, 0.0)
    p = k / nn; d = 1 + z * z / nn
    c = (p + z * z / (2 * nn)) / d; h = z * math.sqrt(p * (1 - p) / nn + z * z / (4 * nn * nn)) / d
    return (100.0 * (c - h), 100.0 * (c + h))


def pooled(dets, **kw):
    r = []
    for det, src in dets:
        r.extend(resim(det, src, **kw))
    r.sort(key=lambda t: t[0]); return r


def main():
    dets = [(detect(sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                           key=lambda b: _f(b.get("start_time", 0))), SLBUF.get(tf, 0.003)), src) for root, tf, src in SRCS]
    print("RadarRun H1-H3 | RR-only 15c+30c+30bkt | TP 0.25%% | R0.4 | ALL IN-SAMPLE => UPPER BOUNDS\n", flush=True)
    base = stats(pooled(dets))
    print("BASELINE (candle stop, uncapped): n=%d win %.1f%% avgWin %+.3f%% avgLoss %+.3f%% BE %.1f%% exp %+.4f%% cushion %.1fpp\n"
          % (base["n"], base["win"], base["aw"], base["al"], base["be"], base["exp"], base["cush"]), flush=True)

    print("### H1 — tighter FIXED stop cap. Falsified if cushion doesn't rise (or exp drops materially) at every level ###", flush=True)
    print("  cap      n     win%   avgWin   avgLoss   BE%%    exp/tr%%   cushion", flush=True)
    for cap in (0.004, 0.005, 0.006, 0.007, None):
        s = stats(pooled(dets, stop_cap=cap))
        print("  %-7s %-5d %5.1f%%  %+.3f%%  %+.3f%%  %5.1f%%  %+.4f%%  %+.1fpp"
              % (("%.1f%%" % (cap * 100)) if cap else "uncap", s["n"], s["win"], s["aw"], s["al"], s["be"], s["exp"], s["cush"]), flush=True)

    print("\n### H2 — time stop at N bars. Falsified if exp/cushion doesn't improve at any N ###", flush=True)
    print("  N       win%   avgWin   avgLoss   BE%%    exp/tr%%   cushion   %%exit-on-time  avgTimeStopResult", flush=True)
    for N in (2, 3, 5, 8, None):
        rows = pooled(dets, time_n=N)
        s = stats(rows)
        tstop = [r[1] * 100.0 for r in rows if r[4] == "time"]
        pct_t = 100.0 * len(tstop) / max(1, len(rows)); avg_t = (sum(tstop) / len(tstop)) if tstop else 0.0
        print("  %-6s  %5.1f%%  %+.3f%%  %+.3f%%  %5.1f%%  %+.4f%%  %+.1fpp   %5.1f%%        %+.3f%%"
              % (str(N), s["win"], s["aw"], s["al"], s["be"], s["exp"], s["cush"], pct_t, avg_t), flush=True)

    print("\n### H3 — edge by source x side (6 cells) + positive-cell restriction (IN-SAMPLE cherry-pick = UPPER BOUND) ###", flush=True)
    allrows = pooled(dets)
    print("  cell           n     win%   [95%% CI]        avgWin   avgLoss   exp/tr%%", flush=True)
    cell_rows = {}
    for src in ("15c", "30c", "30bkt"):
        for side in ("long", "short"):
            rr = [r for r in allrows if r[5] == src and r[6] == side]
            cell_rows[(src, side)] = rr
            if not rr:
                print("  %-14s n=0" % ("%s/%s" % (src, side)), flush=True); continue
            s = stats(rr); nn = s["n"]; kwin = int(round(s["win"] / 100.0 * nn)); lo, hi = wilson(kwin, nn)
            print("  %-14s %-5d %5.1f%%  [%4.1f, %4.1f]   %+.3f%%  %+.3f%%  %+.4f%%"
                  % ("%s/%s" % (src, side), nn, s["win"], lo, hi, s["aw"], s["al"], s["exp"]), flush=True)
    pos = [rc for c, rc in cell_rows.items() if stats(rc) and stats(rc)["exp"] > 0]
    if pos:
        prows = sorted([r for rc in pos for r in rc], key=lambda t: t[0])
        s = stats(prows); dropped = base["n"] - s["n"]
        print("\n  POSITIVE-CELLS-ONLY (cherry-picked, UPPER BOUND): n=%d (dropped %d = %.0f%%) exp %+.4f%% (base %+.4f%%) cushion %.1fpp (base %.1fpp)"
              % (s["n"], dropped, 100.0 * dropped / base["n"], s["exp"], base["exp"], s["cush"], base["cush"]), flush=True)


if __name__ == "__main__":
    main()
