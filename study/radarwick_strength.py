"""Is the POWERFUL subset of the wick-breakouts (deep close BEYOND the radar + big rejection WICK) where any edge lives?

The base wick-breakout (study/radarwick_backtest.py) is directionally real but WEAK (~breakeven, below RR, decays OOS on
clock 15m/30m). Here we split the SAME events by strength and check OOS. Two knobs from detect_wick: pen = close
penetration beyond the radar in bands; wick = retest-wick fraction of the candle range. Disjoint bands (not cumulative
ladders), plus a combined STRONG (pen>=2 & wick>=0.4) vs REST, split by YEAR (2025 IS / 2026 OOS). Same shipped bracket
(candle-SL, fixed TP), taken() non-overlap, fee 0.04%RT+0.03% slip. Representative TP fixed at 0.3%.

decent-n cells only: bucket 15m/30m/1h + clock 15m/30m. python study/radarwick_strength.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from app import radar_breakout_detect as RB

H = 200; FEE = 0.0004; SLIP = 0.0003; TP = 0.003
SLBUF = {"15m": 0.003, "30m": 0.003, "1h": 0.002}
CELLS = [("bucket", "study/recon_archive", "1h"), ("bucket", "study/recon_archive", "30m"),
         ("bucket", "study/recon_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("clock", "study/clock_archive", "15m")]


def load(tf, root):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year if t else 0 for t in ST])
    return A, C, Hi, Lo, YR, n


def build(A):
    """chunked detect_wick -> sorted [(k, s, rlo, rhi, pen, wick)]."""
    n = len(A); ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        try:
            dets = RB.detect_wick(S, skip_last=False)
        except Exception:
            dets = []
        for e in dets:
            k = int(e["i"]) + c0; s = int(e["side"]); side = "S" if s > 0 else "R"
            if (k, side) not in ev:
                ev[(k, side)] = (s, float(e["radar_lo"]), float(e["radar_hi"]), float(e["pen"]), float(e["wick"]))
        if c1 >= n:
            break
        c0 += step - 1000
    out = [(k,) + v for (k, side), v in ev.items()]
    out.sort(); return out


def ev_eval(events, C, Hi, Lo, YR, n, buf):
    """taken() non-overlap over `events` (already filtered). returns {year:(n,win,net%,expR)}."""
    by = {}; last = -1
    for (k, s, rlo, rhi, pen, wick) in events:
        if k <= last or k + 1 >= n:
            continue
        entry = C[k]
        fsl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - fsl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), fsl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        by.setdefault(int(YR[k]), []).append((net, dist)); last = k + int(off)
    res = {}
    for y, arr in by.items():
        nets = np.array([a[0] for a in arr]); dists = np.array([a[1] for a in arr])
        res[y] = (len(nets), 100.0 * (nets > 0).mean(), nets.mean() * 100.0, (nets / dists).mean())
    return res


def row(label, events, C, Hi, Lo, YR, n, buf):
    r = ev_eval(events, C, Hi, Lo, YR, n, buf)
    def c(y):
        if y not in r:
            return "n=0"
        nn, w, net, e = r[y]; return "n=%-4d win%4.1f%% net%+.3f%% expR%+.2f" % (nn, w, net, e)
    print("    %-22s | IS  %-34s | OOS %s" % (label, c(2025), c(2026)), flush=True)


def main():
    print("WICK-BREAKOUT STRENGTH split (pen = close beyond radar in bands; wick = rejection-wick frac) | TP 0.30%% | OOS\n",
          flush=True)
    for dsname, root, tf in CELLS:
        A, C, Hi, Lo, YR, n = load(tf, root); buf = SLBUF[tf]
        ev = build(A)
        pens = np.array([e[4] for e in ev]); wicks = np.array([e[5] for e in ev])
        print("================ %s %s  (WICK events=%d | pen med=%.2f p75=%.2f | wick med=%.2f p75=%.2f) ==========="
              % (dsname.upper(), tf, len(ev), np.median(pens), np.percentile(pens, 75),
                 np.median(wicks), np.percentile(wicks, 75)), flush=True)
        row("ALL", ev, C, Hi, Lo, YR, n, buf)
        # disjoint PEN bands (fixed, interpretable: shallow / mid / deep close beyond the radar)
        row("pen <1", [e for e in ev if e[4] < 1.0], C, Hi, Lo, YR, n, buf)
        row("pen 1-3", [e for e in ev if 1.0 <= e[4] < 3.0], C, Hi, Lo, YR, n, buf)
        row("pen >=3 (deep)", [e for e in ev if e[4] >= 3.0], C, Hi, Lo, YR, n, buf)
        # disjoint WICK bands (small / mid / big rejection wick)
        row("wick <0.25", [e for e in ev if e[5] < 0.25], C, Hi, Lo, YR, n, buf)
        row("wick 0.25-0.5", [e for e in ev if 0.25 <= e[5] < 0.5], C, Hi, Lo, YR, n, buf)
        row("wick >=0.5 (big)", [e for e in ev if e[5] >= 0.5], C, Hi, Lo, YR, n, buf)
        # combined POWERFUL = deep close beyond + big wick  vs  the rest
        row("STRONG pen>=2 & wk>=.4", [e for e in ev if e[4] >= 2.0 and e[5] >= 0.4], C, Hi, Lo, YR, n, buf)
        row("REST (not strong)", [e for e in ev if not (e[4] >= 2.0 and e[5] >= 0.4)], C, Hi, Lo, YR, n, buf)
        print("", flush=True)


if __name__ == "__main__":
    main()
