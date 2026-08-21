"""Does ALIGNMENT (decisive-bearish 3:45 == short break side) let us TARGET MORE? Tests the user's idea directly and
causally: on ALIGNED short breaks vs NON-aligned, measure (1) the MFE = max favorable excursion (how far the short
actually runs before SL/2-day end) -- if aligned runs further, a bigger TP is justified; (2) a TP-SIZE SWEEP
(0.4/0.6/0.8/1.0/1.5/2.0%% fixed + adaptive) -- does a bigger target pay MORE on aligned days? SHORT only, clock 15m,
entry=break close, SL 0.1%% past wick, 2-day hold, stop-first. THR body>=0.5. IS(2025)/OOS(2026). Causal.
python study/ny_break_align_target.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600; THR = 0.5; S15 = (15, 45)
TP_FRACS = [0.004, 0.006, 0.008, 0.010, 0.015, 0.020]
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


def collect():
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    out = []                                                      # (yr, aligned, entry, sl, rng, seq_indices)
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        whi = max(Hi[i] for i in ri); wlo = min(Lo[i] for i in ri); rng = whi - wlo
        if rng <= 0:
            continue
        d15 = 0
        for i in ri:
            if (HR[i], MN[i]) == S15:
                r_ = Hi[i] - Lo[i]; bf = abs(C[i] - O[i]) / r_ if r_ > 0 else 0.0
                d15 = 0 if bf < THR else (1 if C[i] > O[i] else -1)
        k = None
        for i in bi:
            if C[i] < rlo:                                        # SHORT break only
                k = i; break
            if C[i] > rhi:                                        # a long break -> no short this day
                break
        if k is None:
            continue
        entry = C[k]; sl = whi * (1 + SL_PAD)
        seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD]
        yr = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        out.append((yr, d15 == -1, entry, sl, rng, seq, O, C, Hi, Lo))
    return out


def mfe(entry, sl, seq, Hi, Lo):
    lo_min = entry
    for j in seq:
        if Hi[j] >= sl:                                          # stopped -> favourable excursion ends
            lo_min = min(lo_min, Lo[j]); break
        lo_min = min(lo_min, Lo[j])
    return (entry - lo_min) / entry * 100.0


def sim_tp(entry, sl, tp, seq, C, Hi, Lo):
    for j in seq:
        if Hi[j] >= sl:
            return -1.0 * (sl - entry) / entry - FEE - SLIP - SLIP
        if Lo[j] <= tp:
            return -1.0 * (tp - entry) / entry - FEE - SLIP
    return -1.0 * (C[seq[-1]] - entry) / entry - FEE - SLIP - SLIP if seq else 0.0


def grp(rows, aligned, yr=None):
    return [r for r in rows if r[1] == aligned and (yr is None or r[0] == yr)]


def expwin(rows, tp_frac):
    nets = []
    for (_, _, entry, sl, rng, seq, O, C, Hi, Lo) in rows:
        tp = entry * (1 - tp_frac) if tp_frac else (entry - (TP_LOW if (rng / entry * 100.0) < TP_THR else TP_HIGH) * rng)
        nets.append(sim_tp(entry, sl, tp, seq, C, Hi, Lo))
    a = np.array(nets) * 100.0
    return len(a), 100.0 * (a > 0).mean(), a.mean()


def main():
    rows = collect()
    print("Does ALIGNMENT let us target MORE? SHORT break, aligned(=decisive-bearish 3:45)=%d / non=%d | clock 15m | causal\n"
          % (sum(1 for r in rows if r[1]), sum(1 for r in rows if not r[1])), flush=True)
    print("== MFE (max favourable excursion %, how far the short RUNS before SL/end) ==", flush=True)
    for lab, al in (("ALIGNED  ", True), ("NON-align", False)):
        for pt, yr in (("ALL", None), ("IS ", 2025), ("OOS", 2026)):
            g = grp(rows, al, yr)
            m = np.array([mfe(e, s, sq, Hi, Lo) for (_, _, e, s, rg, sq, O, C, Hi, Lo) in g]) if g else np.array([0.0])
            print("  %s %s  n=%-3d  MFE med %.2f%%  mean %.2f%%  p75 %.2f%%  %%>=0.8%% %.0f%%"
                  % (lab, pt, len(g), np.median(m), m.mean(), np.percentile(m, 75), 100.0 * (m >= 0.8).mean()), flush=True)
    print("\n== TP-SIZE sweep (exp per-unit net %% | win%%) — does a BIGGER target pay on ALIGNED days? ==", flush=True)
    hdr = "  TP        " + "".join("%-22s" % ("%.1f%%" % (f * 100)) for f in TP_FRACS) + "adaptive"
    print(hdr, flush=True)
    for lab, al in (("ALIGNED", True), ("NON-al ", False)):
        for pt, yr in (("ALL", None), ("OOS", 2026)):
            g = grp(rows, al, yr); cells = []
            for f in list(TP_FRACS) + [None]:
                nA, w, e = expwin(g, f)
                cells.append("%+.3f%%/%2.0f%%" % (e, w))
            print("  %s %s " % (lab, pt) + "  ".join("%-20s" % c for c in cells), flush=True)


if __name__ == "__main__":
    main()
