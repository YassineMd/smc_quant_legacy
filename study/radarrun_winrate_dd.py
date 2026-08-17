"""Reminder card: Radar Runner win rate + realized MAX DRAWDOWN, 30m (native) & 1h, RECON vs DAEMON(forward).
Shipped spec: MINVISIT=1, candle-capped SL (buf 0.2% on 1h / 0.3% on 30m) + fixed 0.5% TP, canonical non-overlap.
Max DD reported two ways: in R (sizing-free, peak-to-trough of cumulative R) and in % at R=0.5%/trade (compounded).
3bps slip, 0.04% fee. python study/radarrun_winrate_dd.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def rr_trades(A, slbuf):
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
    tr = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((float(ST[k]), net, net / dist)); last = k + int(off)
    tr.sort(key=lambda x: x[0])
    return tr


def maxdd_R(rs):
    cum = peak = dd = 0.0
    for r in rs:
        cum += r; peak = max(peak, cum); dd = max(dd, peak - cum)
    return dd


def maxdd_pct(rs, risk=0.005):
    eq = peak = 1.0; dd = 0.0
    for r in rs:
        eq *= (1.0 + risk * r); peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
    return dd * 100.0


def main():
    print("Radar Runner -- win rate + realized max drawdown (shipped spec, non-overlap)\n", flush=True)
    print("  %-16s %5s %6s %8s %9s %8s" % ("TF / dataset", "n", "win%", "expR", "maxDD(R)", "maxDD%@R0.5"), flush=True)
    for tf in ("30m", "1h"):
        slbuf = 0.002 if tf == "1h" else 0.003
        for ds, root in (("recon", {"root": "study/recon_archive"}), ("daemon", {})):
            try:
                A = get_buckets(tf, root)
                tr = rr_trades(A, slbuf)
                rs = [x[2] for x in tr]; net = np.array([x[1] for x in tr])
                if len(rs) < 5:
                    print("  %-16s %5d  (too few)" % ("%s %s" % (tf, ds), len(rs))); continue
                print("  %-16s %5d %5.0f%% %+8.3f %8.2f %8.1f%%" % (
                    "%s %s" % (tf, ds), len(rs), 100 * (net > 0).mean(), np.mean(rs),
                    maxdd_R(rs), maxdd_pct(rs)), flush=True)
            except Exception as e:
                print("  %-16s  skipped: %s" % ("%s %s" % (tf, ds), e), flush=True)


if __name__ == "__main__":
    main()
