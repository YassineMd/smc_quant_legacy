"""SESSION-VA MIGRATION strategy + LONDON-BREAKOUT entry filter, on 1h recon (weekdays only).
Same as session_va_strategy.py PLUS: the NY entry candle must CLOSE BEYOND the London session range in the bias
direction  ->  LONG: close > London HIGH ;  SHORT: close < London LOW.

  bias: LONG if London VAH>Tokyo VAH AND London VAL>Tokyo VAL ; SHORT if both < ; else no trade.
  entry: FIRST NY (13-21 UTC) candle with dir==bias, absorption A<=-0.75, AND close beyond the London extreme.
  entry=close, TP 1%, SL 0.2% beyond entry-candle extreme [B] (also 0.2%-from-entry [A]); 1/day; non-overlap; fee 0.08%.
Sessions: Tokyo [00,08) London [08,13) NY [13,21) UTC.
Run: python study/session_va_strategy_brk.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import bar_quantiles as BQ

rng = np.random.default_rng(20260804)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]
st = F["start"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts]); yr = np.array([d.year for d in dts])
order = np.argsort(st)
FEE = MA.FEE; TP_PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01; SL_PAD = 0.002; R_EASY = -0.75


def sess_va(idxs, h0, h1):
    lvl = {}
    for i in idxs:
        if not (h0 <= hour[i] < h1):
            continue
        for ps, vv in (A[i].get("levels") or {}).items():
            try:
                p = float(ps)
            except (TypeError, ValueError):
                continue
            r = lvl.setdefault(p, {"b": 0.0, "s": 0.0})
            r["b"] += float(vv.get("b", 0.0) or 0.0); r["s"] += float(vv.get("s", 0.0) or 0.0)
    if len(lvl) < 3:
        return None
    val, vah = BQ.value_area(lvl, 0.70)
    return None if (val != val or vah != vah) else (val, vah)


byday = defaultdict(list)
for i in order:
    if dow[i] < 5:
        byday[dord[i]].append(i)

signals = []; n_bias = 0; n_no_entry = 0; n_broke = 0
for d in sorted(byday):
    idxs = byday[d]
    T = sess_va(idxs, 0, 8); Lo = sess_va(idxs, 8, 13)
    lon = [i for i in idxs if 8 <= hour[i] < 13]
    if T is None or Lo is None or not lon:
        continue
    (valT, vahT) = T; (valL, vahL) = Lo
    if vahL > vahT and valL > valT:
        bias = 1
    elif vahL < vahT and valL < valT:
        bias = -1
    else:
        continue
    n_bias += 1
    lon_hi = max(Hh[i] for i in lon); lon_lo = min(Ll[i] for i in lon)
    entry_i = None
    for i in idxs:
        if not (13 <= hour[i] < 21):
            continue
        dir_ok = (C[i] > O[i]) if bias > 0 else (C[i] < O[i])
        if not (dir_ok and absA[i] <= R_EASY):
            continue
        brk = (C[i] > lon_hi) if bias > 0 else (C[i] < lon_lo)   # NEW: close beyond the London range
        if not brk:
            continue
        entry_i = i; break
    if entry_i is None:
        n_no_entry += 1; continue
    signals.append((entry_i, bias, int(yr[entry_i])))
signals.sort()


def run(sl_mode):
    rows = []; last = -1; skipped = 0
    for (i, bias, y) in signals:
        if i <= last:
            skipped += 1; continue
        e = C[i]
        sl = (Ll[i] * (1 - SL_PAD) if bias > 0 else Hh[i] * (1 + SL_PAD)) if sl_mode == "candle" \
            else (e * (1 - SL_PAD) if bias > 0 else e * (1 + SL_PAD))
        tp = e * (1 + TP_PCT) if bias > 0 else e * (1 - TP_PCT)
        if (bias > 0 and sl >= e) or (bias < 0 and sl <= e):
            continue
        win, ej = MA.walk(A, i, bias, sl, tp, n); last = ej
        dist = abs(e - sl) / e; tpret = abs(tp - e) / e
        rows.append(dict(net=(tpret if win else -dist) - FEE, bias=bias, yr=y, win=bool(win), dist=dist, rr=tpret / dist))
    return rows, skipped


def boot_ci(a, B=10000):
    a = np.asarray(a, float)
    if len(a) == 0:
        return (float("nan"),) * 3
    m = np.array([rng.choice(a, size=len(a), replace=True).mean() for _ in range(B)])
    return a.mean() * 100, np.percentile(m, 2.5) * 100, np.percentile(m, 97.5) * 100


def rep(label, rows):
    k = len(rows)
    if k == 0:
        print("  %-14s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * sum(r["win"] for r in rows) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf")
    dm = np.mean([r["dist"] for r in rows]) * 100; rrm = np.mean([r["rr"] for r in rows]); bal = MA.account(list(nt))
    print("  %-14s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  RR %.2f  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, dm, rrm, bal, (bal - MA.B0) / MA.B0 * 100))


nb = len(signals)
print("=" * 112)
print("SESSION-VA + LONDON-BREAKOUT (close beyond London hi/lo) | NY absorb A<=-0.75 | TP %.2f%% | 1h recon, weekdays"
      % (TP_PCT * 100))
print("  bias days %d;  no qualifying breakout entry %d;  SIGNALS=%d  (of %d weekday days)"
      % (n_bias, n_no_entry, nb, len(byday)))
print("=" * 112)
for mode, name in (("candle", "SL = 0.2% beyond entry-candle extreme [B]"),
                   ("entry", "SL = 0.2% from entry price (5:1) [A]")):
    rows, skipped = run(mode)
    print("\n--- %s%s ---" % (name, ("   [%d overlap-skipped]" % skipped) if skipped else ""))
    rep("ALL", rows); rep("LONG", [r for r in rows if r["bias"] > 0]); rep("SHORT", [r for r in rows if r["bias"] < 0])
    rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
    nt = np.array([r["net"] for r in rows])
    if len(nt):
        m, lo, hi = boot_ci(nt)
        print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
              % (m, lo, hi, "clears 0" if lo > 0 else "INCLUDES 0"))
print("=" * 112)
