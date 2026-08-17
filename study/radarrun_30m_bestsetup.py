"""Best 30m Radar Runner setup for 80%+ win rate at minimum drawdown. TP sweep (candle-SL fixed at 0.3% buffer),
recon + daemon, win% + realized max DD (% @ R0.5). A tighter TP raises win% + lowers DD but costs exp-R -- find the
smallest DD that clears 80% win on the DAEMON (the live constraint). Also a scale-out row (50% at 0.3%->BE, rest 0.5%)
to show the DD it buys. python study/radarrun_30m_bestsetup.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003
TPS = [0.003, 0.0035, 0.004, 0.0045, 0.005]


def detect(A):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
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
    sigs = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist > 0:
            sigs.append((k, s, entry, sl, dist))
    return sigs, Hi, Lo, C, n


def eval_fixed(sigs, Hi, Lo, C, n, tp):
    tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((net, net / dist)); last = k + int(off)
    net = np.array([t[0] for t in tr]); rs = [t[1] for t in tr]
    return len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs)


def eval_scaleout(sigs, Hi, Lo, C, n, tp1=0.003, tp2=0.005, split=0.5):
    tr = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); done = False; g1 = split * tp1; gross = None; off = j1 - j0; outc = "end"
        for o in range(j0, j1):
            hi = Hi[o]; lo = Lo[o]
            if not done:
                if (lo <= sl) if s > 0 else (hi >= sl):
                    gross = s * (sl - entry) / entry; off = o - k; outc = "sl"; break
                if (hi >= entry * (1 + s * tp1)) if s > 0 else (lo <= entry * (1 - s * -tp1)):
                    done = True
                    if (hi >= entry * (1 + s * tp2)) if s > 0 else (lo <= entry * (1 - tp2)):
                        gross = g1 + (1 - split) * tp2; off = o - k; outc = "tp2"; break
            else:
                if (lo <= entry) if s > 0 else (hi >= entry):
                    gross = g1; off = o - k; outc = "be"; break
                if (hi >= entry * (1 + tp2)) if s > 0 else (lo <= entry * (1 - tp2)):
                    gross = g1 + (1 - split) * tp2; off = o - k; outc = "tp2"; break
        if gross is None:
            last_ret = s * (C[min(j1, n) - 1] - entry) / entry
            gross = (g1 + (1 - split) * last_ret) if done else last_ret
        net = gross - FEE - SLIP - (SLIP if outc != "tp2" else 0.0)
        tr.append((net, net / dist)); last = k + int(off)
    net = np.array([t[0] for t in tr]); rs = [t[1] for t in tr]
    return len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        print("\n====  30m  %s  ====" % ds, flush=True)
        sigs, Hi, Lo, C, n = detect(get_buckets("30m", root))
        print("  %-22s %5s %6s %8s %9s" % ("scheme", "n", "win%", "expR", "maxDD%@R0.5"), flush=True)
        for tp in TPS:
            nn, w, er, dd = eval_fixed(sigs, Hi, Lo, C, n, tp)
            print("  fix %.2f%% TP           %5d %5.0f%% %+8.3f %8.1f%%" % (tp * 100, nn, w, er, dd), flush=True)
        nn, w, er, dd = eval_scaleout(sigs, Hi, Lo, C, n)
        print("  scaleout .3->BE/.5     %5d %5.0f%% %+8.3f %8.1f%%" % (nn, w, er, dd), flush=True)


if __name__ == "__main__":
    main()
