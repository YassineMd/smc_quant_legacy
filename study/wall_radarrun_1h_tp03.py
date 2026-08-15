"""1h RADAR RUNNER with a FIXED 0.3% TP (quick scalp exit instead of the tiered radar-length targets). SL is swept:
structural (opposite radar extreme = the Radar Runner's defined risk) + fixed 0.3%/0.5%/1.0%. First-passage, SL-first on
a tie (conservative), 0.04% round-trip fee, canonical taken()-nonoverlap. Reported for the BASE signal and the
reward/eff same-side OVERLAP subset (ALL + STRONG zones), both recon years. CLI: [tf ...] (default 1h)."""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004
TP_FRAC = 0.003; MAXLIFE = 500; STRONG = 40.0
random.seed(12345)


def sim_fixed(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):           # SL checked first -> conservative tie-break
            return s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return s * (tp - entry) / entry, off + 1
    return (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def reward_zones(A):
    Hh = [_f(b.get("high")) for b in A]; Ll = [_f(b.get("low")) for b in A]
    Cc = [_f(b.get("close", b.get("close_price"))) for b in A]; n = len(A)
    buy = []; sell = []
    for (i, side, strength) in reward_eff.switches(A):
        hi = Hh[i]; lo = Ll[i]
        if hi <= 0 or lo <= 0:
            continue
        mid = 0.5 * (hi + lo); half = max(0.5 * (hi - lo), mid * 0.00025); ylo = mid - half; yhi = mid + half; mit = n
        if side == "buy":
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if Cc[kk] < ylo:
                    mit = kk; break
            buy.append((i, ylo, yhi, mit, strength))
        else:
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if Cc[kk] > yhi:
                    mit = kk; break
            sell.append((i, ylo, yhi, mit, strength))
    return {"S": buy, "R": sell}


def has_overlap(P, band, k, wside, zones, strong_only):
    plo = P - band; phi = P + band
    for (i, zlo, zhi, mit, strength) in zones[wside]:
        if i > k:
            break
        if strong_only and strength < STRONG:
            continue
        if k > min(mit, i + MAXLIFE):
            continue
        if plo <= zhi and phi >= zlo:
            return True
    return False


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
    zones = reward_zones(A)

    ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
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
                    ev[(k, side)] = (P, band, rlo, rhi); break
        if c1 >= n:
            break
        c0 += 5000

    SLV = [("struct", None), ("0.3%", 0.003), ("0.5%", 0.005), ("1.0%", 0.010)]
    rows = []                                                # (k, year, side, P, band, entry, sl0, ovlA, ovlS, {slname:(ret,off)})
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        P, band, rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1
        entry = C[k]; sl0 = rlo if up else rhi
        tp = entry * (1 + s * TP_FRAC)
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        outs = {}
        for name, frac in SLV:
            sl = sl0 if frac is None else entry * (1 - s * frac)
            ret, off = sim_fixed(s, entry, tp, sl, ph, pl, pc)
            outs[name] = (ret - FEE, off)
        rows.append((k, int(yr[k]), side, P, band, entry, sl0,
                     has_overlap(P, band, k, side, zones, False),
                     has_overlap(P, band, k, side, zones, True), outs))
    print("\n================  TF = %s   TP=%.2f%%   (events=%d)  ================" % (tf, TP_FRAC * 100, len(rows)),
          flush=True)
    if len(rows) < 40:
        print("  too few events"); return

    def report(tag, sel):
        print("  === %s ===" % tag, flush=True)
        for name, _frac in SLV:
            line = "    SL=%-7s" % name
            for Y in (2025, 2026):
                acc = []; last = -1
                for r in rows:
                    if r[1] != Y or not sel(r) or r[0] <= last:
                        continue
                    ret, off = r[9][name]; acc.append(ret); last = r[0] + int(off)
                a = np.array(acc)
                if len(a) < 8:
                    line += "  | %d n=%-3d (<8)" % (Y, len(a))
                else:
                    line += "  | %d n=%-3d win=%2.0f%% avg=%+.3f%% net=%+.0f%%" % (
                        Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100)
            print(line, flush=True)

    report("BASE (all radar-runner events)", lambda r: True)
    report("reward/eff OVERLAP  (same-side, ALL zones)", lambda r: r[7])
    report("reward/eff OVERLAP  (same-side, STRONG zones)", lambda r: r[8])


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
