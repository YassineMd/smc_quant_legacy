"""Win rate + max drawdown of Radar Runner signals that fire INSIDE a higher-timeframe wall's radar:
  30m signals inside a 1h wall | 30m inside a 4h wall | 1h inside a 4h wall. RECON + DAEMON(forward).
Shipped spec (MINVISIT=1, candle-SL + 0.5% TP, non-overlap). NOTE: an earlier placebo-controlled study found INSIDE ~
placebo (no confluence EDGE vs a random wide zone) -- these are just the win/DD of that subset. maxDD in R + % @R0.5.
python study/radarrun_htf_confluence_dd.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_R, maxdd_pct
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003


def build_htf_walls(htf, root):
    A = get_buckets(htf, root)
    ST = [_f(b.get("start_time")) for b in A]; n = len(A); walls = []; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            P = _f(w.get("price")); band = _f(w.get("band"))
            if P <= 0 or band <= 0:
                continue
            i0 = int(w.get("i0", 0)) + c0
            i1 = (int(w.get("i1")) + c0) if (w.get("broken") and w.get("i1") is not None) else (n - 1)
            i0 = max(0, min(i0, n - 1)); i1 = max(i0, min(i1, n - 1))
            walls.append((ST[i0], ST[i1], P, RM * band))
        if c1 >= n:
            break
        c0 += 5999
    return walls


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
        tr.append((float(ST[k]), net, net / dist, entry)); last = k + int(off)
    tr.sort(key=lambda x: x[0])
    return tr


def inside(walls, ts, price):
    for (t0, t1, P, rad) in walls:
        if t0 <= ts <= t1 and abs(price - P) <= rad:
            return True
    return False


def rep(label, tr):
    if len(tr) < 5:
        print("  %-26s n=%d (too few)" % (label, len(tr))); return
    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
    print("  %-26s n=%-4d win=%2.0f%% expR=%+.3f  maxDD=%.2fR (%.1f%% @R0.5)" % (
        label, len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_R(rs), maxdd_pct(rs)), flush=True)


def main():
    COMBOS = [("30m", "1h"), ("30m", "4h"), ("1h", "4h")]     # (signal tf, wall htf)
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        print("\n====  %s  ====" % ds, flush=True)
        sig_cache = {}
        for stf, htf in COMBOS:
            try:
                if stf not in sig_cache:
                    sig_cache[stf] = rr_trades(get_buckets(stf, root), 0.002 if stf == "1h" else 0.003)
                walls = build_htf_walls(htf, root)
                ins = [t for t in sig_cache[stf] if inside(walls, t[0], t[3])]
                rep("%s inside %s wall" % (stf, htf), ins)
            except Exception as e:
                print("  %s inside %s wall -- skipped: %s" % (stf, htf, e), flush=True)


if __name__ == "__main__":
    main()
