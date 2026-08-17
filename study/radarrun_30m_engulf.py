"""30m Radar Runner: is an ENGULFING breakout bar a higher-probability winner than a non-engulfing one? Classify each
breakout candle (bar k, in the breakout direction) two ways and compare win% of the two cohorts:
  BODY-engulf  = classic: prev bar OPPOSITE colour, curr body fully engulfs prev body (o<=prev_close & c>=prev_open ...).
  RANGE-engulf = outside bar: curr high>=prev high AND curr low<=prev low (engulfs the whole prior range).
Canonical NON-OVERLAP book (one-at-a-time) so n + significance aren't inflated. Tested at 0.2/0.3/0.4% TP (0.2% is the
base but win% is ceilinged there, so wider TPs give an effect room to appear). Label-PERMUTATION null on the win-rate
gap (10k shuffles) -> is the gap more than chance? RECON (both yrs) AND DAEMON (live, the honest check). Descriptive
reliability only. 3bps slip, 0.04% fee. python study/radarrun_30m_engulf.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim
from app import absorption_level_detect as AL

np.random.seed(7); RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003


def detect(A):
    """Every 30m Radar Runner breakout: (k, s, entry, sl, dist) + O/C/Hi/Lo arrays (need O + prev bar for engulf)."""
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
        if k + 1 >= n or k < 1:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist > 0:
            sigs.append((k, s, entry, sl, dist))
    return sigs, O, C, Hi, Lo, n


def body_engulf(O, C, k, s):
    po, pc, o, c = O[k - 1], C[k - 1], O[k], C[k]
    if s > 0:                                    # long -> bullish engulfing (prev bearish, curr bullish body-engulfs)
        return (c > o) and (pc < po) and (o <= pc) and (c >= po)
    return (c < o) and (pc > po) and (o >= pc) and (c <= po)   # short -> bearish engulfing


def range_engulf(Hi, Lo, k):
    return (Hi[k] >= Hi[k - 1]) and (Lo[k] <= Lo[k - 1])       # outside bar (engulfs prior high+low), direction-agnostic


def book_at_tp(pack, tp):
    """Non-overlap one-at-a-time trades at TP; tag each with body/range engulf flags. Returns list of (win, R, body, rng)."""
    sigs, O, C, Hi, Lo, n = pack; out = []; last = -1
    for (k, s, entry, sl, dist) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((1 if net > 0 else 0, net / dist, body_engulf(O, C, k, s), range_engulf(Hi, Lo, k)))
        last = k + int(off)
    return out


def perm_gap(wins_e, n_e, wins_n, n_n, iters=10000):
    if n_e == 0 or n_n == 0:
        return 0.0, 1.0
    obs = wins_e / n_e - wins_n / n_n
    pool = np.array([1] * wins_e + [0] * (n_e - wins_e) + [1] * wins_n + [0] * (n_n - wins_n))
    cnt = 0
    for _ in range(iters):
        np.random.shuffle(pool)
        if abs(pool[:n_e].mean() - pool[n_e:].mean()) >= abs(obs) - 1e-12:
            cnt += 1
    return 100.0 * obs, cnt / iters


def cohort(trades, flag_idx):
    e = [t for t in trades if t[flag_idx]]; nn = [t for t in trades if not t[flag_idx]]
    def stat(g):
        if not g:
            return 0, 0, 0.0, 0.0
        w = sum(t[0] for t in g); return len(g), w, 100.0 * w / len(g), float(np.mean([t[1] for t in g]))
    return stat(e), stat(nn)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        pack = detect(get_buckets("30m", root))
        base = book_at_tp(pack, 0.002)
        bshare = 100.0 * np.mean([t[2] for t in base]); rshare = 100.0 * np.mean([t[3] for t in base])
        print("\n############  30m %s  (non-overlap book, n=%d)  ############" % (ds, len(base)), flush=True)
        print("  breakout-bar base rate:  BODY-engulf %.0f%%   RANGE-engulf %.0f%%" % (bshare, rshare), flush=True)
        for defn, idx in (("BODY ", 2), ("RANGE", 3)):
            print("  -- %s-engulf --   %-30s | %-30s   gap / perm-p" % (
                defn, "ENGULF  n win%  expR", "NON     n win%  expR"), flush=True)
            for tp in (0.002, 0.003, 0.004):
                trades = book_at_tp(pack, tp)
                (ne, we, wpe, re), (nn, wn, wpn, rn) = cohort(trades, idx)
                gap, p = perm_gap(we, ne, wn, nn)
                star = "  <-- p<.05" if (p < 0.05 and ne >= 10 and nn >= 10) else ""
                print("     @%.1f%%   %5d %4.0f%% %+7.3f      | %5d %4.0f%% %+7.3f      %+5.1fpp  p=%.3f%s" % (
                    tp * 100, ne, wpe, re, nn, wpn, rn, gap, p, star), flush=True)


if __name__ == "__main__":
    main()
