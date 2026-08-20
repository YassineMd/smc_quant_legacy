"""Re-test the wick-breakouts with the BAR-DIRECTION filter (user 2026-08-20): a buy wall (support/up-break) should only
count a BULLISH bar (close>open); a sell wall (resistance/down-break) only a BEARISH bar. The raw signal was diluted by
counter-directional bars (a red bar sitting above a buy wall / green below a sell wall). Compare, per cell x TP, OOS-split:
RR baseline | ALL wick | SAME-DIR wick | SAME-DIR + big-wick(>=0.5). Same shipped bracket (candle-SL), taken(), fees.

all tf except 1m, both datasets. python study/radarwick_samedir.py [tf ...]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from app import radar_breakout_detect as RB

H = 200; FEE = 0.0004; SLIP = 0.0003
TPS = [0.002, 0.003, 0.004, 0.005]
SLBUF = {"5m": 0.003, "15m": 0.003, "30m": 0.003, "1h": 0.002, "4h": 0.002}
DATASETS = [("bucket", "study/recon_archive"), ("clock", "study/clock_archive")]
WICK_BIG = 0.5


def load(tf, root):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year if t else 0 for t in ST])
    return A, C, Hi, Lo, YR, n


def build(A, kind):
    """chunked detection -> sorted list. kind 'rr' -> (k,s,rlo,rhi); 'wick' -> (k,s,rlo,rhi,wick,bull)."""
    n = len(A); ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        try:
            dets = RB.detect(S, skip_last=False) if kind == "rr" else RB.detect_wick(S, skip_last=False)
        except Exception:
            dets = []
        for e in dets:
            k = int(e["i"]) + c0; s = int(e["side"]); side = "S" if s > 0 else "R"
            if (k, side) not in ev:
                if kind == "rr":
                    ev[(k, side)] = (s, float(e["radar_lo"]), float(e["radar_hi"]))
                else:
                    ev[(k, side)] = (s, float(e["radar_lo"]), float(e["radar_hi"]), float(e["wick"]), bool(e["bull"]))
        if c1 >= n:
            break
        c0 += step - 1000
    out = [(k,) + v for (k, side), v in ev.items()]
    out.sort(); return out


def ev_eval(events, C, Hi, Lo, YR, n, buf, tp):
    by = {}; last = -1
    for e in events:
        k = e[0]; s = e[1]; rlo = e[2]; rhi = e[3]
        if k <= last or k + 1 >= n:
            continue
        entry = C[k]; fsl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - fsl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), fsl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        by.setdefault(int(YR[k]), []).append((net, dist)); last = k + int(off)
    res = {}
    for y, arr in by.items():
        nets = np.array([a[0] for a in arr]); dists = np.array([a[1] for a in arr])
        res[y] = (len(nets), 100.0 * (nets > 0).mean(), nets.mean() * 100.0, (nets / dists).mean())
    return res


def samedir(e):        # e = (k,s,rlo,rhi,wick,bull)
    s = e[1]; bull = e[5]
    return (s > 0 and bull) or (s < 0 and not bull)


def c(res, y):
    if y not in res:
        return "n=0"
    nn, w, net, e = res[y]; return "n=%-4d win%4.1f%% net%+.3f%% eR%+.2f" % (nn, w, net, e)


def main():
    tfs = sys.argv[1:] or ["1h", "30m", "15m", "5m", "4h"]
    print("WICK-BREAKOUT + BAR-DIRECTION filter | RR | ALL | SAME-DIR | SAME-DIR+big-wick | OOS-split | eR=expR\n", flush=True)
    for dsname, root in DATASETS:
        for tf in tfs:
            try:
                A, C, Hi, Lo, YR, n = load(tf, root)
            except Exception as ex:
                print("== %s %s : load ERR %s" % (dsname, tf, ex), flush=True); continue
            if not n:
                continue
            buf = SLBUF.get(tf, 0.003)
            rr = build(A, "rr"); wk = build(A, "wick")
            sd = [e for e in wk if samedir(e)]
            sdbig = [e for e in sd if e[4] >= WICK_BIG]
            print("================ %s %s  (RR=%d  ALLwick=%d  SAMEDIR=%d  SD+big=%d) ================"
                  % (dsname.upper(), tf, len(rr), len(wk), len(sd), len(sdbig)), flush=True)
            for tp in TPS:
                rrr = ev_eval(rr, C, Hi, Lo, YR, n, buf, tp)
                a = ev_eval(wk, C, Hi, Lo, YR, n, buf, tp)
                sdr = ev_eval(sd, C, Hi, Lo, YR, n, buf, tp)
                sbr = ev_eval(sdbig, C, Hi, Lo, YR, n, buf, tp)
                print("  TP %.2f%%" % (tp * 100), flush=True)
                for y in (2025, 2026):
                    tag = "IS " if y == 2025 else "OOS"
                    print("    %s | RR %-30s | ALL %-30s" % (tag, c(rrr, y), c(a, y)), flush=True)
                    print("        | SD %-30s | SD+big %s" % (c(sdr, y), c(sbr, y)), flush=True)
            print("", flush=True)


if __name__ == "__main__":
    main()
