"""Volume version of the 3:45pm break-side predictor. Does the 2-5pm 15m bar's ORDER-FLOW DELTA (buy_vol - sell_vol)
foreshadow the NY break side? delta_dir = net-buy(->long) / net-sell(->short); |delta%%| = |bv-sv|/curr_vol (conviction,
analogous to body-strength); curr_vol = participation. Scans all 12 slots for delta_dir accuracy, then for the late bars
splits by |delta%%| tercile + volume tercile, and tests body_dir vs delta_dir agreement (+ decisive-body AND strong-delta).
Compares vs the body finding (15:45 decisive body = 78/84%%). Causally clean (bar closes 16:00; break is a later close
beyond the range). clock 15m, IS(2025)/OOS(2026). python study/ny_break_predictor_vol_15m.py"""
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
    BV = np.zeros(n); SV = np.zeros(n); CV = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        BV[i] = _f(b.get("buy_vol", 0) or 0); SV[i] = _f(b.get("sell_vol", 0) or 0)
        CV[i] = _f(b.get("curr_vol", 0) or 0) or (BV[i] + SV[i])
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, BV, SV, CV, HR, MN, DATE, WD, n


def collect():
    O, C, Hi, Lo, ST, BV, SV, CV, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    rows = []                                                     # (yr, side, {slot: (bdir, bf, ddir, dpct, vol)})
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
        sd = {}
        for i in ri:
            rng = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / rng if rng > 0 else 0.0
            bdir = 0 if bf < DOJI else (1 if C[i] > O[i] else -1)
            cv = CV[i]; dlt = BV[i] - SV[i]
            dpct = (dlt / cv * 100.0) if cv > 0 else 0.0
            ddir = 0 if abs(dpct) < 1.0 else (1 if dlt > 0 else -1)
            sd[(HR[i], MN[i])] = (bdir, bf, ddir, dpct, cv)
        yr = datetime.fromtimestamp(ST[bi[0]], tz=timezone.utc).year
        rows.append((yr, side, sd))
    return rows


def _binom_p(k, nn):
    if nn == 0:
        return 1.0
    return min(1.0, sum(comb(nn, j) for j in range(nn + 1) if abs(j - nn / 2) >= abs(k - nn / 2)) / (2.0 ** nn))


def dir_acc(rows, slot, which, yr=None):
    idx = 2 if which == "delta" else 0                            # ddir vs bdir
    r = [(sd, dirs[slot][idx]) for (y, sd, dirs) in rows if (yr is None or y == yr) and slot in dirs]
    r = [(s, dd) for (s, dd) in r if dd != 0]
    if not r:
        return 0, 0.0, 1.0
    hit = sum(1 for s, dd in r if dd == s)
    return len(r), hit / len(r), _binom_p(hit, len(r))


def tercile(rows, slot, key):
    """split non-neutral days by |dpct| (key='d') or vol (key='v') into low/mid/high, delta_dir accuracy each (ALL/OOS)."""
    recs = []
    for (y, sd, dirs) in rows:
        if slot not in dirs:
            continue
        bdir, bf, ddir, dpct, vol = dirs[slot]
        if ddir == 0:
            continue
        mag = abs(dpct) if key == "d" else vol
        recs.append((y, sd, ddir, mag))
    if not recs:
        return []
    qs = np.percentile([m for _, _, _, m in recs], [33.3, 66.7])
    out = []
    for lo, hi, nm in ((-1e18, qs[0], "low "), (qs[0], qs[1], "mid "), (qs[1], 1e18, "high")):
        A = [(sd, dd) for (y, sd, dd, m) in recs if lo <= m < hi]
        Oo = [(sd, dd) for (y, sd, dd, m) in recs if lo <= m < hi and y == 2026]
        aA = (sum(1 for s, dd in A if dd == s) / len(A)) if A else 0.0
        aO = (sum(1 for s, dd in Oo if dd == s) / len(Oo)) if Oo else 0.0
        out.append((nm, len(A), aA, len(Oo), aO))
    return out


def agree(rows, slot, both_strong=False):
    """body_dir == delta_dir agreement -> accuracy + coverage (ALL/IS/OOS). both_strong: also body>=0.5 & |dpct|>=median."""
    med = np.median([abs(dirs[slot][3]) for (_, _, dirs) in rows if slot in dirs and dirs[slot][2] != 0]) if rows else 0
    res = {}
    for lab, yr in (("ALL", None), ("IS", 2025), ("OOS", 2026)):
        r = [(sd, dirs[slot]) for (y, sd, dirs) in rows if (yr is None or y == yr) and slot in dirs]
        ag = []
        for s, (bdir, bf, ddir, dpct, vol) in r:
            if bdir == 0 or ddir == 0 or bdir != ddir:
                continue
            if both_strong and (bf < 0.5 or abs(dpct) < med):
                continue
            ag.append((s, bdir))
        cov = len(ag) / max(1, len(r)); a = (sum(1 for s, d in ag if d == s) / len(ag)) if ag else 0.0
        res[lab] = (len(ag), a, cov)
    return res


def main():
    rows = collect()
    ns = sum(1 for _, s, _ in rows if s < 0); base = max(ns, len(rows) - ns) / len(rows)
    print("NY break SIDE predictor — ORDER-FLOW DELTA (buy_vol - sell_vol) of the 2-5pm 15m bars | clock 15m", flush=True)
    print("breaks n=%d  base rate %.1f%%. delta_dir = net-buy->long / net-sell->short. (body 15:45 decisive = 78/84%%)\n"
          % (len(rows), 100.0 * base), flush=True)
    print("  slot   DELTA: n   acc    p      IS     OOS  |  BODY: acc   OOS", flush=True)
    for h, m in SLOTS:
        nD, aD, pD = dir_acc(rows, (h, m), "delta"); _, aDi, _ = dir_acc(rows, (h, m), "delta", 2025); _, aDo, _ = dir_acc(rows, (h, m), "delta", 2026)
        _, aB, _ = dir_acc(rows, (h, m), "body"); _, aBo, _ = dir_acc(rows, (h, m), "body", 2026)
        print("  %02d:%02d   %3d  %.3f  %.3f  %.3f  %.3f  |  %.3f  %.3f" % (h, m, nD, aD, pD, aDi, aDo, aB, aBo), flush=True)

    for h, m in ((14, 30), (15, 0), (15, 45)):
        print("\n-- %02d:%02d  |delta%%| tercile (does STRONG imbalance predict better?) --" % (h, m), flush=True)
        for nm, nA, aA, nO, aO in tercile(rows, (h, m), "d"):
            print("      %s|delta%%|  ALL n=%-3d acc %.3f | OOS n=%-3d acc %.3f" % (nm, nA, aA, nO, aO), flush=True)
        print("   %02d:%02d  volume tercile (does high PARTICIPATION predict better?)" % (h, m), flush=True)
        for nm, nA, aA, nO, aO in tercile(rows, (h, m), "v"):
            print("      %svol      ALL n=%-3d acc %.3f | OOS n=%-3d acc %.3f" % (nm, nA, aA, nO, aO), flush=True)

    print("\n-- BODY & DELTA agreement (both point same way) --", flush=True)
    for h, m in ((14, 30), (15, 0), (15, 45)):
        a = agree(rows, (h, m)); s = agree(rows, (h, m), both_strong=True)
        print("  %02d:%02d agree      ALL n=%-3d acc %.3f cov %.0f%% | IS %.3f | OOS n=%-3d acc %.3f cov %.0f%%"
              % (h, m, a["ALL"][0], a["ALL"][1], 100 * a["ALL"][2], a["IS"][1], a["OOS"][0], a["OOS"][1], 100 * a["OOS"][2]), flush=True)
        print("        +both STRONG ALL n=%-3d acc %.3f cov %.0f%% | IS %.3f | OOS n=%-3d acc %.3f cov %.0f%%"
              % (s["ALL"][0], s["ALL"][1], 100 * s["ALL"][2], s["IS"][1], s["OOS"][0], s["OOS"][1], 100 * s["OOS"][2]), flush=True)


if __name__ == "__main__":
    main()
