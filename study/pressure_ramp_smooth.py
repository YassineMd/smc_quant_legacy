# -*- coding: utf-8 -*-
"""TAKER-VOLUME GROWTH, SMOOTHED (follow-up to study/pressure_ramp_study.py, user 2026-09-08: "did you measure only the
pressure percentage or also the volume growth?").

The first study measured BOTH, but its two growth features required STRICTLY rising minutes: `streak` reset to 0 on any
single down minute and `growth` was only measured across that unbroken run.  The user's description -- "200-600K, then it
doubled, then tripled, gradually" -- can easily span 10-30 minutes WITH dips in between, which the strict run misses
(only 10 fires ever reached streak >= 5).  `level` and `sustained5` were non-monotone but single-minute / 5-minute.

This study adds the SMOOTHED, dip-tolerant volume-growth features and re-runs the same three tests:
  g10   = $ in the last 10 min / $ in the 10 min before that      (the "it doubled" ratio, dips allowed)
  g30   = same over 30 min                                        (the slow build)
  lvl10 = mean $ per min over the last 10 / prior-60-min median   (sustained elevation, not a single spike)
  slp15 = OLS slope of $ per min over 15 min, / its mean          (per-minute growth rate)
  age20 = minutes of the last 20 with level >= 1.5                (how long the pressure has been elevated)
  totg10 = TOTAL (buy+sell) $ last 10 / prior 10                  (is it just overall activity?)
  ostg10 = the OPPOSITE side's g10                                (both sides growing = churn, not pressure)
All are causal (last closed minute only) and per trade-side.

TESTS -- identical gates to the first study:
 A) every minute: P(+0.2% before -0.2% within 60 min) in the pressure direction, by disjoint growth bands, both years,
    vs an all-minutes baseline and a CIRCULAR-SHIFT null (flows rolled against price).
 B) Radar Runner 30m bucket (canonical union-live fires, 1m first-touch, fees, non-overlap taken()): disjoint bands at
    the fire, then the best cells re-run as FILTERED strategies with drop-best-month fragility and a shift null.
 C) the interaction the user described: growth that STARTED and CONTINUED (g10 >= 2 AND age20 >= 10) vs a fresh burst
    (g10 >= 2 AND age20 <= 3), and pure-pressure growth (g10 >= 2 AND opposite g10 < 1.2).
python study/pressure_ramp_smooth.py"""
import os, sys, json, math, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from study import radarrun_30mbkt_live_full as CAN
from study.radarrun_hyro_prop import mc, day_blocks

T0 = time.time()
FEE, SLIP = CAN.FEE, CAN.SLIP
z = np.load(os.path.join("study", "out", "clock1m_flow.npz"))
T1, O1, H1, L1, C1, BV, SV = (z[k] for k in ("T1", "O1", "H1", "L1", "C1", "BV", "SV"))
N = len(T1)
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in T1])
BD = BV * C1; SD = SV * C1; TOT = BD + SD
print("1m minutes %d  %s -> %s" % (N, datetime.fromtimestamp(T1[0], tz=timezone.utc).date(), datetime.fromtimestamp(T1[-1], tz=timezone.utc).date()), flush=True)


def rsum(x, w):
    """sum of x[t-w+1 .. t] (NaN before it exists)"""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[w - 1:] = c[w:] - c[:-w]
    return out


def prior_median(x, w=60):
    out = np.full(len(x), np.nan)
    out[w:] = np.median(sliding_window_view(x[:-1], w), axis=1)
    return out


def growth_feats(X):
    """the SMOOTHED growth family for one side's $ series (dips allowed, no monotonicity required)."""
    s10 = rsum(X, 10); s30 = rsum(X, 30)
    g10 = np.full(N, np.nan); g10[10:] = np.where(s10[:-10] > 0, s10[10:] / np.where(s10[:-10] > 0, s10[:-10], 1.0), np.nan)
    g30 = np.full(N, np.nan); g30[30:] = np.where(s30[:-30] > 0, s30[30:] / np.where(s30[:-30] > 0, s30[:-30], 1.0), np.nan)
    med = prior_median(X, 60)
    lvl1 = np.where(med > 0, X / np.where(med > 0, med, 1.0), np.nan)
    lvl10 = np.where(med > 0, (s10 / 10.0) / np.where(med > 0, med, 1.0), np.nan)
    w = np.arange(15, dtype=float) - 7.0                      # OLS slope over 15 min / mean  -> per-minute growth rate
    sw = sliding_window_view(X, 15)
    num = sw @ w; den = float(np.sum(w * w)); mu = sw.mean(axis=1)
    slp15 = np.full(N, np.nan); slp15[14:] = np.where(mu > 0, (num / den) / np.where(mu > 0, mu, 1.0), np.nan)
    hot = (lvl1 >= 1.5).astype(np.float64); hot[np.isnan(lvl1)] = 0.0
    age20 = np.full(N, np.nan); age20[19:] = rsum(hot, 20)[19:]
    return dict(g10=g10, g30=g30, lvl10=lvl10, slp15=slp15, age20=age20)


FB = growth_feats(BD); FS = growth_feats(SD)
TG = growth_feats(TOT)["g10"]
print("smoothed growth features built (%.0f s)" % (time.time() - T0), flush=True)


def race(t, s, up=0.002, dn=0.002, horizon=60):
    e = C1[t]; tp = e * (1 + s * up); sl = e * (1 - s * dn)
    for j in range(t + 1, min(N, t + 1 + horizon)):
        if s > 0:
            if L1[j] <= sl: return 0
            if H1[j] >= tp: return 1
        else:
            if H1[j] >= sl: return 0
            if L1[j] <= tp: return 1
    return None


def events(mask, s, gap=30, cap=None):
    out = {2025: [], 2026: []}; last = -10 ** 9; k = 0
    for t in np.nonzero(mask)[0]:
        if t - last < gap or t + 61 >= N:
            continue
        last = t; k += 1
        if cap and k > cap:
            break
        r = race(int(t), s)
        if r is not None:
            out[int(YR[t])].append(r)
    return out


def line(lab, ev):
    a, b = ev[2025], ev[2026]
    f = lambda v: ("%5.1f%% (n=%5d)" % (100 * np.mean(v), len(v))) if len(v) >= 30 else ("  --   (n=%5d)" % len(v))
    return "  %-46s 2025 %s   2026 %s" % (lab, f(a), f(b))


print("\n" + "=" * 118 + "\nA -- every minute: continuation after SMOOTHED volume growth (dips allowed)\n" + "=" * 118)
base_mask = ~np.isnan(FB["g10"]) & ~np.isnan(FS["g10"]) & ~np.isnan(FB["age20"])
rngp = np.random.RandomState(5)
samp = np.zeros(N, bool); ix = np.nonzero(base_mask)[0]; samp[ix[rngp.choice(len(ix), min(60000, len(ix)), replace=False)]] = True
for s, F, Fo, nm in ((1, FB, FS, "BUY"), (-1, FS, FB, "SELL")):
    dom = (BD > SD) if s > 0 else (SD > BD)
    print(line("%s  ALL minutes, side dominant (baseline)" % nm, events(base_mask & dom & samp, s)), flush=True)
    for lab, m in (("g10 >= 1.5 (last 10 min 1.5x the prior 10)", F["g10"] >= 1.5),
                   ("g10 >= 2   (it DOUBLED over 10 min)", F["g10"] >= 2.0),
                   ("g10 >= 3   (it TRIPLED)", F["g10"] >= 3.0),
                   ("g30 >= 2   (doubled over 30 min)", F["g30"] >= 2.0),
                   ("lvl10 >= 2 (10-min mean 2x the prior hour)", F["lvl10"] >= 2.0),
                   ("slp15 > 0.05 (rising 5%/min for 15 min)", F["slp15"] > 0.05),
                   ("STARTED+CONTINUED: g10>=2 & age20>=10", (F["g10"] >= 2.0) & (F["age20"] >= 10)),
                   ("FRESH burst:       g10>=2 & age20<=3", (F["g10"] >= 2.0) & (F["age20"] <= 3)),
                   ("PURE: g10>=2 & opposite g10 < 1.2", (F["g10"] >= 2.0) & (Fo["g10"] < 1.2))):
        print(line("%s  %s" % (nm, lab), events(base_mask & dom & m, s)), flush=True)
print("  -- circular-shift null: BUY g10 >= 2 (flows rolled 5..100 days against price, 12 shifts) --", flush=True)
rng = random.Random(7); nulls = []
for _ in range(12):
    k = rng.randint(5, 100) * 1440 * rng.choice((-1, 1))
    g = np.roll(FB["g10"], k); d = np.roll(BD > SD, k)
    ev = events(~np.isnan(g) & d & (g >= 2.0), 1, cap=4000)
    v = ev[2025] + ev[2026]
    nulls.append(100 * np.mean(v) if v else np.nan)
real = events(base_mask & (BD > SD) & (FB["g10"] >= 2.0), 1, cap=4000)
rv = real[2025] + real[2026]
print("     real %.1f%% (n=%d)   null mean %.1f%%  sd %.1f  max %.1f   (%.0f s)" % (
      100 * np.mean(rv), len(rv), np.nanmean(nulls), np.nanstd(nulls), np.nanmax(nulls), time.time() - T0), flush=True)

# ---------------------------------------------------------------------------------------------------------------------
print("\n" + "=" * 118 + "\nB -- Radar Runner 30m bucket: smoothed volume growth at the fire\n" + "=" * 118)
fires = [tuple(f) for f in json.load(open(CAN.CACHE))]
T1e = T1 + 60.0


def feat_at(et, s):
    j = int(np.searchsorted(T1e, et, side="right")) - 1
    if j < 95 or T1e[j] < et - 600:
        return None
    F, Fo = (FB, FS) if s > 0 else (FS, FB)
    d = {k: float(F[k][j]) for k in ("g10", "g30", "lvl10", "slp15", "age20")}
    if any(np.isnan(v) for v in d.values()):
        return None
    d["og10"] = float(Fo["g10"][j]); d["totg10"] = float(TG[j])
    return d


def run_taken(fs):
    taken = []; busy = -1.0
    for (k, t, s, e, sl) in fs:
        if t < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        net, outc, tx = CAN.resolve_scaleout(s, e, sl, t, T1, H1, L1)
        taken.append(dict(t=t, s=s, net=net, r=net / sld, f=feat_at(t, s), y=datetime.fromtimestamp(t, tz=timezone.utc).year,
                          m=datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")))
        busy = tx
    return taken


def report(lab, tk, full=False):
    if not tk:
        print("  %-44s no trades" % lab); return
    nets = np.array([x["net"] for x in tk]); rs = np.array([x["r"] for x in tk])
    nW = int((nets > 0).sum())
    ln = "  %-44s n %5d  win %5.1f%%  avg net %+.3f%%  avg R %+.3f" % (lab, len(tk), 100 * nW / len(tk), 100 * nets.mean(), rs.mean())
    if full:
        eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
        m4 = mc(day_blocks([(x["t"], x["net"], x["r"]) for x in tk]), 0.4, 4.0, "R")
        ln += "  DD %.1f%%  prop %.1f%%" % (dd, m4["p"])
    print(ln, flush=True)
    for Y in (2025, 2026):
        yr = [x for x in tk if x["y"] == Y]
        if yr:
            ny = np.array([x["net"] for x in yr])
            print("      %d: n %5d  win %5.1f%% +- %.1f  avg net %+.3f%%" % (Y, len(yr), 100 * (ny > 0).mean(), 100 * 1.96 * math.sqrt(0.25 / len(yr)), 100 * ny.mean()))


base = run_taken(fires)
have = [x for x in base if x["f"] is not None]
p_all = float(np.mean([x["net"] > 0 for x in have]))
report("CANONICAL (unfiltered)", base, full=True)
print("  fires with a smoothed-growth state: %d of %d taken   (baseline win %.1f%%)" % (len(have), len(base), 100 * p_all))
for nm, key, edges in (("g10  last-10 / prior-10 $ (fold)", "g10", [0, 0.8, 1.2, 2, 3, 99]),
                       ("g30  last-30 / prior-30 $ (fold)", "g30", [0, 0.8, 1.2, 2, 99]),
                       ("lvl10 10-min mean vs prior-hour median", "lvl10", [0, 1, 2, 4, 99]),
                       ("slp15 per-minute growth rate", "slp15", [-99, -0.02, 0.02, 0.05, 99]),
                       ("age20 minutes elevated of the last 20", "age20", [0, 3, 8, 14, 21]),
                       ("totg10 TOTAL volume growth", "totg10", [0, 0.8, 1.2, 2, 99]),
                       ("og10 OPPOSITE-side growth", "og10", [0, 0.8, 1.2, 2, 99])):
    print("  -- %s (disjoint) --" % nm)
    v = np.array([x["f"][key] for x in have])
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [x for x, q in zip(have, v) if lo <= q < hi]
        if len(sel) < 30:
            print("     [%6.2f,%6.2f)  n %5d  (too few)" % (lo, hi, len(sel))); continue
        p = float(np.mean([x["net"] > 0 for x in sel])); zz = (p - p_all) / math.sqrt(p_all * (1 - p_all) / len(sel))
        w25 = [x["net"] > 0 for x in sel if x["y"] == 2025]; w26 = [x["net"] > 0 for x in sel if x["y"] == 2026]
        print("     [%6.2f,%6.2f)  n %5d  win %5.1f%%  z %+5.2f  avg net %+.3f%%   2025 %5.1f%%(n=%4d)  2026 %5.1f%%(n=%4d)" % (
              lo, hi, len(sel), 100 * p, zz, 100 * np.mean([x["net"] for x in sel]),
              100 * np.mean(w25) if len(w25) >= 20 else float("nan"), len(w25), 100 * np.mean(w26) if len(w26) >= 20 else float("nan"), len(w26)))

print("\n  -- C: the user's shape (started AND continued) vs a fresh burst, at the fire --")
CELLS = {"STARTED+CONTINUED g10>=2 & age20>=10": lambda f: f["g10"] >= 2 and f["age20"] >= 10,
         "FRESH burst      g10>=2 & age20<=3": lambda f: f["g10"] >= 2 and f["age20"] <= 3,
         "PURE growth      g10>=2 & og10<1.2": lambda f: f["g10"] >= 2 and f["og10"] < 1.2,
         "SLOW build       g30>=2": lambda f: f["g30"] >= 2,
         "rising 15 min    slp15>0.05": lambda f: f["slp15"] > 0.05,
         "SHRINKING        g10<0.8": lambda f: f["g10"] < 0.8}
for lab, fn in CELLS.items():
    sel = [x for x in have if fn(x["f"])]
    if len(sel) < 30:
        print("     %-38s n %5d (too few)" % (lab, len(sel))); continue
    p = float(np.mean([x["net"] > 0 for x in sel])); zz = (p - p_all) / math.sqrt(p_all * (1 - p_all) / len(sel))
    w25 = [x["net"] > 0 for x in sel if x["y"] == 2025]; w26 = [x["net"] > 0 for x in sel if x["y"] == 2026]
    print("     %-38s n %5d  win %5.1f%%  z %+5.2f  avg net %+.3f%%   2025 %5.1f%%(n=%4d)  2026 %5.1f%%(n=%4d)" % (
          lab, len(sel), 100 * p, zz, 100 * np.mean([x["net"] for x in sel]),
          100 * np.mean(w25) if len(w25) >= 20 else float("nan"), len(w25), 100 * np.mean(w26) if len(w26) >= 20 else float("nan"), len(w26)))

print("\n  -- FILTERED strategies (re-taken, canonical bracket) + fragility --")
FILT = {"g10 >= 2": lambda f: f["g10"] >= 2, "g10 >= 1.2": lambda f: f["g10"] >= 1.2,
        "STARTED+CONTINUED": lambda f: f["g10"] >= 2 and f["age20"] >= 10,
        "PURE growth (og10 < 1.2)": lambda f: f["g10"] >= 2 and f["og10"] < 1.2,
        "g30 >= 2": lambda f: f["g30"] >= 2, "lvl10 >= 2": lambda f: f["lvl10"] >= 2}
for lab, fn in FILT.items():
    fs = []
    for f in fires:
        d = feat_at(f[1], f[2])
        if d is not None and fn(d):
            fs.append(f)
    tk = run_taken(fs)
    report("FILTER %s" % lab, tk, full=True)
    if tk:
        mo = {}
        for x in tk:
            mo.setdefault(x["m"], []).append(x["net"])
        best = max(mo, key=lambda k: sum(mo[k])); rest = [x for x in tk if x["m"] != best]
        if rest:
            print("      fragility: drop the best month (%s) -> win %.1f%%  avg net %+.3f%%" % (best, 100 * np.mean([x["net"] > 0 for x in rest]), 100 * np.mean([x["net"] for x in rest])))
print("  -- circular-shift null of the filter 'g10 >= 2' (12 shifts) --", flush=True)
outs = []
for _ in range(12):
    k = rng.randint(5, 100) * 1440 * rng.choice((-1, 1))
    gB = np.roll(FB["g10"], k); gS = np.roll(FS["g10"], k)
    fs = []
    for (kk, t, s, e, sl) in fires:
        j = int(np.searchsorted(T1e, t, side="right")) - 1
        if j < 95: continue
        g = (gB if s > 0 else gS)[j]
        if not np.isnan(g) and g >= 2:
            fs.append((kk, t, s, e, sl))
    tk = run_taken(fs)
    outs.append(100 * np.mean([x["net"] > 0 for x in tk]) if tk else np.nan)
print("     null win mean %.1f%%  sd %.1f  max %.1f  (n per shift ~%d)" % (np.nanmean(outs), np.nanstd(outs), np.nanmax(outs), len(tk)), flush=True)
print("\nharness: study/pressure_ramp_smooth.py  DONE in %.0f s" % (time.time() - T0))
