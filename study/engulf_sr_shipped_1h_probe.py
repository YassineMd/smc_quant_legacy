from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf_sr_detect as E

rng = np.random.default_rng(7)
F = L.load_features("1h"); A = F["A"]; n = F["n"]; yr = F["year"]; hh = F["h"]; ll = F["l"]; cc = F["c"]
FEE = MA.FEE; PAD = E.SL_PAD
marks = E.detect(A, skip_last=True)

rows = []; last = -1
for mk in marks:
    i = mk["i"]
    if i <= last:
        continue
    win, ej = MA.walk(A, i, mk["side"], mk["sl"], mk["tp"], n); last = ej
    e = mk["entry"]; d = abs(e - mk["sl"]) / e; rr = abs(mk["tp"] - e) / abs(e - mk["sl"])
    rows.append(dict(net=(rr * d if win else -d) - FEE, side=mk["side"], src=mk["src"],
                     relaxed=bool(mk["relaxed"]), flow_align=bool(mk["flow_align"]), yr=int(yr[i])))


def ci(rs, label):
    if not rs:
        print("  %-30s n=0" % label); return
    nt = np.array([r["net"] for r in rs])
    m = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(m, [2.5, 97.5])
    print("  %-30s n=%4d  mean/tr %+.3f%%  95%%CI [%+.3f%%, %+.3f%%]  %s"
          % (label, len(rs), nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else "includes 0"))


print("bootstrap 95% CI on mean net/trade (which sub-cohorts individually clear zero):")
ci(rows, "ALL")
ci([r for r in rows if r["side"] > 0], "LONG only")
ci([r for r in rows if r["flow_align"]], "flow-aligned")
ci([r for r in rows if r["flow_align"] and r["side"] > 0], "flow-aligned LONG")
ci([r for r in rows if r["src"] == "VASR"], "VA+SR confluence")
ci([r for r in rows if not r["relaxed"]], "strict c1")
ci([r for r in rows if r["flow_align"] and not r["relaxed"]], "flow-aligned + strict c1")

# shift-null baseline distribution (to explain p=0.002 despite CI-includes-0)
base = [(m["i"], m["side"], abs(m["tp"] - m["entry"]) / abs(m["entry"] - m["sl"])) for m in marks]
tots = []
for _ in range(2000):
    dl = int(rng.integers(1, n)); nets = []; last = -1
    for (i0, side, rr) in base:
        j = (i0 + dl) % n
        if j < 1 or j >= n - 1 or j <= last:
            continue
        e = cc[j]; sl = ll[j] * (1 - PAD) if side > 0 else hh[j] * (1 + PAD)
        if (side > 0 and sl >= e) or (side < 0 and sl <= e):
            continue
        d = abs(e - sl) / e; tp = e + rr * (e * d) * side
        win, ej = MA.walk(A, j, side, sl, tp, n); last = ej
        nets.append((rr * d if win else -d) - FEE)
    tots.append((np.prod(1 + np.array(nets)) - 1) * 100 if nets else -100.0)
tots = np.array(tots)
print("\nshift-null total-net distribution: mean %+.1f%%  median %+.1f%%  (real +8.7%%)  -> random-timed same-side entries %s"
      % (tots.mean(), np.median(tots), "LOSE on average" if tots.mean() < 0 else "profit"))
