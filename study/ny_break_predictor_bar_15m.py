"""'Conspiracy' test: is there ONE clock-time 15m bar in the 2-5pm (13-16 UTC) window whose DIRECTION (bull/bear)
foreshadows the eventual NY break SIDE (brB long close>rhi / brS short close<rlo, first close beyond the 13-16 body
range in 16-21 UTC)? Scans all 12 slots (13:00,13:15,...,15:45). Per slot: agreement acc = P(bar_dir == break_side),
the two conditionals (bear->short, bull->long), doji rate, LEAD (how long before 16:00 it closes), and IS(2025)/
OOS(2026) acc (fishing across 12 slots WILL overfit one -> OOS is the real test). Base rate controlled. Precedent: the
shipped calc uses the clock-HOUR dirs and found later hours dominate. clock 15m. python study/ny_break_predictor_bar_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from math import comb
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; DOJI = 0.10
SLOTS = [(h, m) for h in (13, 14, 15) for m in (0, 15, 30, 45)]
ROOT, TF = "study/clock_archive", "15m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, MN, DATE, WD, n


def _dir(o, c, h, l):
    rng = h - l
    bf = abs(c - o) / rng if rng > 0 else 0.0
    if bf < DOJI:
        return 0, bf                                              # doji / no clear side
    return (1 if c > o else -1), bf


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    rows = []                                                     # (year, break_side, {slot: (dir, body_frac)})
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        if rhi <= rlo:
            continue
        side = 0
        for i in bi:
            if C[i] > rhi:
                side = 1; break
            if C[i] < rlo:
                side = -1; break
        if side == 0:
            continue
        slotdir = {}
        for i in ri:
            slotdir[(HR[i], MN[i])] = _dir(O[i], C[i], Hi[i], Lo[i])
        yr = datetime.fromtimestamp(ST[bi[0]], tz=timezone.utc).year
        rows.append((yr, side, slotdir))
    return rows


def _binom_p(k, nn):                                              # two-sided binomial vs 0.5
    if nn == 0:
        return 1.0
    tail = sum(comb(nn, j) for j in range(nn + 1) if abs(j - nn / 2) >= abs(k - nn / 2)) / (2.0 ** nn)
    return min(1.0, tail)


def _d(dirs, slot):
    return dirs.get(slot, (0, 0.0))


def acc(rows, slot, yr=None, minbf=0.0):
    r = [(sd, _d(dirs, slot)) for (y, sd, dirs) in rows if (yr is None or y == yr)]
    r = [(s, dd) for (s, (dd, bf)) in r if dd != 0 and bf >= minbf]   # non-doji (+ optional min body) days
    if not r:
        return 0, 0.0, 1.0
    hit = sum(1 for s, dd in r if dd == s)
    return len(r), hit / len(r), _binom_p(hit, len(r))


def cond(rows, slot):
    r = [(sd, _d(dirs, slot)[0]) for (y, sd, dirs) in rows]
    bear = [s for s, dd in r if dd == -1]; bull = [s for s, dd in r if dd == 1]
    b2s = (sum(1 for s in bear if s == -1) / len(bear)) if bear else 0.0
    b2l = (sum(1 for s in bull if s == 1) / len(bull)) if bull else 0.0
    doji = sum(1 for _, dd in r if dd == 0) / max(1, len(r))
    return b2s, len(bear), b2l, len(bull), doji


def strength_split(rows, slot):
    """acc when the bar is DECISIVE (body>=0.5) vs WEAK (0.1..0.5). ALL / OOS."""
    out = []
    for lo, hi, nm in ((0.5, 9.9, "decisive>=0.5"), (DOJI, 0.5, "weak 0.1-0.5")):
        rA = [(sd, dd) for (y, sd, dirs) in rows for (dd, bf) in [_d(dirs, slot)] if dd != 0 and lo <= bf < hi]
        rO = [(sd, dd) for (y, sd, dirs) in rows if y == 2026 for (dd, bf) in [_d(dirs, slot)] if dd != 0 and lo <= bf < hi]
        aA = (sum(1 for s, dd in rA if dd == s) / len(rA)) if rA else 0.0
        aO = (sum(1 for s, dd in rO if dd == s) / len(rO)) if rO else 0.0
        out.append((nm, len(rA), aA, len(rO), aO))
    return out


def combo_agree(rows, s1, s2):
    """when two slots AGREE on direction, acc + coverage + IS/OOS (else no call)."""
    res = {}
    for lab, yr in (("ALL", None), ("IS", 2025), ("OOS", 2026)):
        r = [(sd, _d(dirs, s1)[0], _d(dirs, s2)[0]) for (y, sd, dirs) in rows if (yr is None or y == yr)]
        ag = [(s, d1) for (s, d1, d2) in r if d1 != 0 and d1 == d2]
        cov = len(ag) / max(1, len(r)); a = (sum(1 for s, d in ag if d == s) / len(ag)) if ag else 0.0
        res[lab] = (len(ag), a, cov)
    return res


def main():
    rows = collect()
    ns = sum(1 for _, s, _ in rows if s < 0); nl = len(rows) - ns
    base = max(ns, nl) / len(rows)
    print("NY break SIDE predictor — which 2-5pm 15m bar's direction foreshadows the break? | clock 15m", flush=True)
    print("breaks n=%d  short %.1f%% / long %.1f%%  -> majority base rate %.1f%% (beat THIS, not 50%%)\n"
          % (len(rows), 100.0 * ns / len(rows), 100.0 * nl / len(rows), 100.0 * base), flush=True)
    print("  slot   lead   n   acc     p       IS-acc   OOS-acc | bear->short   bull->long   doji%", flush=True)
    best = None
    for h, m in SLOTS:
        lead = (16 * 60) - (h * 60 + m + 15)                      # minutes from this bar's CLOSE to 16:00
        nA, aA, pA = acc(rows, (h, m)); _, aI, _ = acc(rows, (h, m), 2025); _, aO, _ = acc(rows, (h, m), 2026)
        b2s, nbe, b2l, nbu, dj = cond(rows, (h, m))
        print("  %02d:%02d  %3dm  %3d  %.3f  %.3f   %.3f    %.3f   | %.3f (n%3d)  %.3f (n%3d)  %.0f%%"
              % (h, m, lead, nA, aA, pA, aI, aO, b2s, nbe, b2l, nbu, 100.0 * dj), flush=True)
        if best is None or aO > best[1]:
            best = ((h, m), aO, aA)
    print("\nBEST by OOS acc: %02d:%02d  OOS %.3f (ALL %.3f) vs base %.3f" % (best[0][0], best[0][1], best[1], best[2], base), flush=True)

    print("\n-- DECISIVE vs WEAK body (does a big-body bar predict better?) --", flush=True)
    for h, m in ((14, 30), (15, 0), (15, 45)):
        print("  %02d:%02d:" % (h, m), flush=True)
        for nm, nA, aA, nO, aO in strength_split(rows, (h, m)):
            print("      %-14s ALL n=%-3d acc %.3f | OOS n=%-3d acc %.3f" % (nm, nA, aA, nO, aO), flush=True)

    print("\n-- AGREEMENT combos (both bars same dir -> higher conviction, lower coverage) --", flush=True)
    for s1, s2 in (((15, 0), (15, 45)), ((14, 30), (15, 45)), ((15, 0), (15, 30))):
        r = combo_agree(rows, s1, s2)
        print("  %02d:%02d & %02d:%02d agree:  ALL n=%-3d acc %.3f cov %.0f%% | IS acc %.3f | OOS n=%-3d acc %.3f cov %.0f%%"
              % (s1[0], s1[1], s2[0], s2[1], r["ALL"][0], r["ALL"][1], 100 * r["ALL"][2], r["IS"][1], r["OOS"][0], r["OOS"][1], 100 * r["OOS"][2]), flush=True)


if __name__ == "__main__":
    main()
