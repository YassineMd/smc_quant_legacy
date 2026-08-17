"""30m Radar Runner: is VWAP a useful directional BIAS? Rule = take LONG only when entry is ABOVE VWAP, SHORT only when
BELOW. Test whether VWAP-ALIGNED breakouts win more than VWAP-AGAINST ones (the split IS the both-sides control: if the
rule helps, aligned>against; the inverted rule is against-only). Two VWAP anchors:
  DAILY  = UTC-midnight-anchored session VWAP (the classic bias line), cumulative typ-price*vol / vol, reset each day.
  ROLL48 = rolling 48-bucket VWAP (recent context).
VWAP taken THROUGH the breakout bar (what you'd see on the chart at entry), typ price = (H+L+C)/3, weight = curr_vol.
Canonical NON-OVERLAP book. Per TP (0.2/0.3/0.4%): aligned vs against win%/expR + label-permutation p, THEN the practical
baseline-vs-aligned-only view (n, win%, expR, maxDD%, prop pass%/days). RECON + DAEMON. Descriptive. 3bps slip/0.04% fee.
python study/radarrun_30m_vwap.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003; WROLL = 48
RP = 0.5; TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 6000; MAXD = 300


def detect(A):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    V = np.array([_f(b.get("curr_vol")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    typ = (Hi + Lo + C) / 3.0; pv = typ * V
    day = (ST // 86400).astype(np.int64); vwap_d = np.zeros(n)                 # UTC-midnight-anchored daily VWAP (causal)
    acc_pv = acc_v = 0.0; cur = None
    for i in range(n):
        if day[i] != cur:
            cur = day[i]; acc_pv = acc_v = 0.0
        acc_pv += pv[i]; acc_v += V[i]
        vwap_d[i] = acc_pv / acc_v if acc_v > 0 else C[i]
    cpv = np.cumsum(pv); cv = np.cumsum(V); vwap_r = np.zeros(n)               # rolling WROLL-bucket VWAP
    for i in range(n):
        lo = max(0, i - WROLL + 1); dv = cv[i] - (cv[lo - 1] if lo > 0 else 0.0)
        vwap_r[i] = (cpv[i] - (cpv[lo - 1] if lo > 0 else 0.0)) / dv if dv > 0 else C[i]
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
            al_d = (entry > vwap_d[k]) if s > 0 else (entry < vwap_d[k])       # LONG above / SHORT below (daily)
            al_r = (entry > vwap_r[k]) if s > 0 else (entry < vwap_r[k])       # ...rolling
            sigs.append((k, s, entry, sl, dist, bool(al_d), bool(al_r)))
    return sigs, Hi, Lo, C, ST, n


def book_at_tp(pack, tp):
    """Non-overlap trades: (win, R, aligned_daily, aligned_roll, ts)."""
    sigs, Hi, Lo, C, ST, n = pack; out = []; last = -1
    for (k, s, entry, sl, dist, al_d, al_r) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((1 if net > 0 else 0, net / dist, al_d, al_r, float(ST[k])))
        last = k + int(off)
    return out


def perm_gap(we, ne, wn, nn, iters=10000):
    if ne == 0 or nn == 0:
        return 0.0, 1.0
    obs = we / ne - wn / nn
    pool = np.array([1] * we + [0] * (ne - we) + [1] * wn + [0] * (nn - wn)); cnt = 0
    for _ in range(iters):
        np.random.shuffle(pool)
        if abs(pool[:ne].mean() - pool[ne:].mean()) >= abs(obs) - 1e-12:
            cnt += 1
    return 100.0 * obs, cnt / iters


def split(trades, idx):
    al = [t for t in trades if t[idx]]; ag = [t for t in trades if not t[idx]]
    def st(g):
        w = sum(t[0] for t in g); return len(g), w, (100.0 * w / len(g) if g else 0.0), (float(np.mean([t[1] for t in g])) if g else 0.0)
    return st(al), st(ag)


def prop_mc(trades):
    by = {}
    for t in trades:
        by.setdefault(datetime.fromtimestamp(t[4], tz=timezone.utc).date(), []).append(t[1])
    if not by:
        return 0.0, [0, 0, 0]
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    passes = 0; dtp = []
    for _ in range(NMC):
        eq = peak = 0.0; passed = failed = False
        for di in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += RP * r; dlow = min(dlow, eq); peak = max(peak, eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (dstart - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(di)
                break
    q = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
    return 100.0 * passes / NMC, q


def practical(label, trades):
    if not trades:
        print("     %-18s (empty)" % label); return
    rs = [t[1] for t in trades]; net = np.array([1 if t[0] else -1 for t in trades])
    p, q = prop_mc(trades)
    print("     %-18s n=%-4d win=%2.0f%% expR=%+.3f maxDD=%.1f%% pass=%3.0f%% days=%d/%d/%d" % (
        label, len(trades), 100 * (np.array([t[0] for t in trades]) > 0).mean(), float(np.mean(rs)),
        maxdd_pct(rs, RP / 100.0), p, q[0], q[1], q[2]), flush=True)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        pack = detect(get_buckets("30m", root))
        b0 = book_at_tp(pack, 0.002)
        print("\n############  30m %s  (non-overlap book, n=%d)  ############" % (ds, len(b0)), flush=True)
        print("  VWAP-aligned share:  DAILY %.0f%%   ROLL48 %.0f%%" % (
            100 * np.mean([t[2] for t in b0]), 100 * np.mean([t[3] for t in b0])), flush=True)
        for defn, idx in (("DAILY ", 2), ("ROLL48", 3)):
            print("  -- VWAP %s bias --   %-26s | %-26s   gap / perm-p" % (
                defn, "ALIGNED n win%  expR", "AGAINST n win%  expR"), flush=True)
            for tp in (0.002, 0.003, 0.004):
                tr = book_at_tp(pack, tp)
                (na, wa, wpa, ra), (ng, wg, wpg, rg) = split(tr, idx)
                gap, p = perm_gap(wa, na, wg, ng)
                star = "  <-- p<.05" if (p < 0.05 and na >= 10 and ng >= 10) else ""
                print("     @%.1f%%   %4d %4.0f%% %+7.3f       | %4d %4.0f%% %+7.3f       %+5.1fpp p=%.3f%s" % (
                    tp * 100, na, wpa, ra, ng, wpg, rg, gap, p, star), flush=True)
        print("  -- practical: baseline vs DAILY-VWAP-aligned-only (prop target10/DD10/daily5, R0.5) --", flush=True)
        for tp in (0.002, 0.003):
            tr = book_at_tp(pack, tp)
            print("   TP %.1f%%:" % (tp * 100), flush=True)
            practical("all (baseline)", tr)
            practical("aligned-only", [t for t in tr if t[2]])
            practical("against-only", [t for t in tr if not t[2]])


if __name__ == "__main__":
    main()
