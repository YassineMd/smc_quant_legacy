import os, sys, time; sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

_t0 = time.time()
TF = "15m"
print("precompute start...", flush=True)
S.precompute(TF)
print("precompute done %.1fs" % (time.time() - _t0), flush=True)
BE = (1 + MA.FEE) / (1 + S.RR)
print("BE win = %.4f  FEE=%.4f RR=%.2f" % (BE, MA.FEE, S.RR))

def cell(rows):
    m = len(rows)
    if m == 0:
        return (0, 0, 0.0, float("nan"))
    nt = np.array([r["net"] for r in rows])
    w = int((nt > 0).sum())
    tot = (np.prod(1 + nt) - 1) * 100
    p = S.bp(w, m, BE)
    return (m, w, tot, p)

def split_years(rows):
    y25 = [r for r in rows if r["yr"] == 2025]
    y26 = [r for r in rows if r["yr"] == 2026]
    return y25, y26

sides = ["LONG", "SHORT", "BOTH"]
ab2s = [-0.75, 0.0, 0.75]
engs = ["body", "range"]
scs = ["high", "low"]
bfs = [0.55, 0.70, 0.85]

survivors = []
allrows = []
for ab2 in ab2s:
    for eng in engs:
        for sc in scs:
            for bf in bfs:
                rows = S.analyze_fast(TF, ab2=ab2, engulf=eng, use_bias=True,
                                      short_close=sc, body_frac=bf, bias_arr=None)
                print("  cfg ab2=%+.2f %s %s bf=%.2f done %.1fs" % (ab2, eng, sc, bf, time.time()-_t0), flush=True)
                for side in sides:
                    if side == "LONG":
                        rr = [r for r in rows if r["side"] > 0]
                    elif side == "SHORT":
                        rr = [r for r in rows if r["side"] < 0]
                    else:
                        rr = rows
                    y25, y26 = split_years(rr)
                    m25, w25, t25, p25 = cell(y25)
                    m26, w26, t26, p26 = cell(y26)
                    tag = "%-5s ab2=%+.2f eng=%-5s sc=%-4s bf=%.2f" % (side, ab2, eng, sc, bf)
                    # BOTH years above BE win AND net-positive, n>=15/yr
                    cond25 = (m25 >= 15 and w25 / m25 > BE and t25 > 0)
                    cond26 = (m26 >= 15 and w26 / m26 > BE and t26 > 0)
                    flag = "  <<< ROBUST" if (cond25 and cond26) else ""
                    line = ("%s | 25: n=%3d win=%5.1f%% net=%+6.1f%% p=%.3f || 26: n=%3d win=%5.1f%% net=%+6.1f%% p=%.3f%s"
                            % (tag, m25, 100*w25/m25 if m25 else 0, t25, p25,
                               m26, 100*w26/m26 if m26 else 0, t26, p26, flag))
                    allrows.append(line)
                    if cond25 and cond26:
                        survivors.append((tag, m25, w25, t25, p25, m26, w26, t26, p26))

print("=" * 130)
for l in allrows:
    print(l)
print("=" * 130)
print("SURVIVORS (BE-clear + net>0 in BOTH years, n>=15/yr): %d" % len(survivors))
for s in survivors:
    print("  ", s)
