# -*- coding: utf-8 -*-
"""TAKER-PRESSURE RAMP (user 2026-09-08, from the tablet's 60 s buy/sell $ strip): "at first the buyer volume was
200-600K$, when the move started it doubled, then tripled ... gradually, not in one shot. A sudden burst moves price but
does not necessarily continue; when the pressure starts AND continues, the real move happens."  Can it filter the Radar
Runner (winners vs losers, higher win rate, maybe a bigger RR / TP)?

Data: the 1m clock archive (2025-01 -> 2026-06, buy_vol / sell_vol per minute = the 60 s gauge sampled at minute closes),
$ = coins x close.  The canonical Radar Runner 30m-bucket fires (union-live cache of study/radarrun_30mbkt_live_full.py).

PART A (every minute, descriptive): per side s the dollar flow X_t (buy$ for longs / sell$ for shorts);
  baseline = median X over the PRIOR 60 min; level = X_t / baseline; streak = consecutive rising minutes ending at t with
  that side dominant (share >= 0.5); growth = X_t / X_{t-streak}.
  STATES (disjoint, at minute close, entry direction s):  BURST = level >= 3 & streak <= 1 (one-shot spike);
  RAMP = streak >= 3 & growth >= 2 & level >= 2 (gradual doubling+);  RAMP-CONT = streak >= 5 (it started and kept going).
  Outcomes in direction s: forward close-to-close return at +5/+15/+30 min; first-touch race +0.2% vs -0.2% within
  60 min (same-minute both = against); +0.4% vs -0.2% (RR 2).  Non-overlap events (>= 30 min apart per side), both years,
  z vs the all-minutes baseline, and a CIRCULAR-SHIFT null (flows shifted by k days against price, 20 shifts).
PART B (Radar Runner filter, honest gates): each canonical fire gets the trade-side pressure state of the last closed
  minute before the bucket close (causal): streak, growth, level, 5-min share, 5-min sustained count (minutes at level
  >= 2), opposite-side level, and the A-states.  Winners vs losers by DISJOINT bands on the canonical non-overlap taken()
  sequence; the best band re-run as a FILTERED strategy (re-taken, 1m first-touch, fees, both years, DD, prop MC,
  drop-best-month fragility, circular-shift null of the filter).
PART B2 (RR / TP): two pressure exits on the same canonical entries -- (i) after TP1, trail the runner on the 3-min
  trade-side share (exit when it drops below 0.35, BE stop, cap 240 min, no TP2); (ii) no TP1: ride the pressure from
  entry (exit on flip / SL, cap 240 min).  Net vs the canonical scale-out bracket.
python study/pressure_ramp_study.py"""
import os, sys, json, math, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study import radarrun_30mbkt_live_full as CAN
from study.radarrun_hyro_prop import mc, day_blocks

T0 = time.time()
FEE, SLIP, CAPMIN = CAN.FEE, CAN.SLIP, CAN.CAPMIN
NPZ = os.path.join("study", "out", "clock1m_flow.npz")
if os.path.exists(NPZ):
    z = np.load(NPZ); T1, O1, H1, L1, C1, BV, SV = (z[k] for k in ("T1", "O1", "H1", "L1", "C1", "BV", "SV"))
else:
    print("loading 1m clock archive ...", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); O1 = np.array([_f(b.get("open")) for b in A1])
    H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1]); C1 = np.array([_f(b.get("close")) for b in A1])
    BV = np.array([_f(b.get("buy_vol")) for b in A1]); SV = np.array([_f(b.get("sell_vol")) for b in A1])
    del A1; np.savez(NPZ, T1=T1, O1=O1, H1=H1, L1=L1, C1=C1, BV=BV, SV=SV)
N = len(T1)
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in T1])
BD = BV * C1; SD = SV * C1                                  # taker buy $ / sell $ per minute (the 60 s gauge)
print("1m minutes %d  %s -> %s  (%.0f s)" % (N, datetime.fromtimestamp(T1[0], tz=timezone.utc).date(), datetime.fromtimestamp(T1[-1], tz=timezone.utc).date(), time.time() - T0), flush=True)


def rolling_prior_median(x, w=60):
    """median of x[t-w .. t-1] (NaN for the first w minutes)"""
    out = np.full(len(x), np.nan)
    from numpy.lib.stride_tricks import sliding_window_view
    sw = sliding_window_view(x[:-1], w)                    # window ending at t-1 for t = w .. N-1
    out[w:] = np.median(sw, axis=1)
    return out


def side_features(X, share):
    """streak / growth / level for one side's $ series X (share = X / (buy$+sell$))."""
    base = rolling_prior_median(X, 60)
    level = np.where(base > 0, X / np.where(base > 0, base, 1.0), np.nan)
    streak = np.zeros(N, dtype=np.int32); growth = np.ones(N)
    for t in range(1, N):
        if X[t] > X[t - 1] and share[t] >= 0.5 and T1[t] - T1[t - 1] <= 61:
            streak[t] = streak[t - 1] + 1
        else:
            streak[t] = 0
        s0 = streak[t]
        growth[t] = X[t] / X[t - s0] if s0 > 0 and X[t - s0] > 0 else 1.0
    return level, streak, growth


tot = BD + SD
shB = np.where(tot > 0, BD / np.where(tot > 0, tot, 1.0), 0.5); shS = 1.0 - shB
levB, stkB, groB = side_features(BD, shB)
levS, stkS, groS = side_features(SD, shS)
print("features built (%.0f s)" % (time.time() - T0), flush=True)


def states(lev, stk, gro):
    burst = (lev >= 3) & (stk <= 1)
    ramp = (stk >= 3) & (gro >= 2) & (lev >= 2)
    cont = (stk >= 5)
    return burst, ramp, cont


def race(t, s, up, dn, horizon=60):
    """first-touch race from the close of minute t in direction s: +up before -dn within `horizon` minutes -> 1, else 0
    (same-minute both -> against); None if neither touched."""
    e = C1[t]; tp = e * (1 + s * up); sl = e * (1 - s * dn)
    for j in range(t + 1, min(N, t + 1 + horizon)):
        if s > 0:
            if L1[j] <= sl: return 0
            if H1[j] >= tp: return 1
        else:
            if H1[j] >= sl: return 0
            if L1[j] <= tp: return 1
    return None


def fwd(t, s, k):
    return s * (C1[min(N - 1, t + k)] / C1[t] - 1.0) * 1e4 if t + k < N else np.nan


def eval_events(idx, s, gap=30, light=False, cap=None):
    """non-overlap events (>= gap minutes apart): mean fwd bp at 5/15/30, P(+0.2 before -0.2), P(+0.4 before -0.2), per year"""
    out = {2025: [], 2026: []}; last = -10 ** 9; n_ev = 0
    for t in idx:
        if t - last < gap or t + 61 >= N:
            continue
        last = t; n_ev += 1
        if cap is not None and n_ev > cap:
            break
        if light:
            out[int(YR[t])].append((np.nan, np.nan, np.nan, race(t, s, 0.002, 0.002), None))
        else:
            out[int(YR[t])].append((fwd(t, s, 5), fwd(t, s, 15), fwd(t, s, 30), race(t, s, 0.002, 0.002), race(t, s, 0.004, 0.002)))
    return out


def summ(rows):
    if not rows:
        return "n=0"
    a = np.array([[r[0], r[1], r[2]] for r in rows], dtype=float)
    r1 = [r[3] for r in rows if r[3] is not None]; r2 = [r[4] for r in rows if r[4] is not None]
    return "n=%6d  fwd5 %+6.1f  fwd15 %+6.1f  fwd30 %+6.1f bp   P(+0.2 first) %5.1f%% (n=%d)   P(+0.4 vs -0.2) %5.1f%%" % (
        len(rows), np.nanmean(a[:, 0]), np.nanmean(a[:, 1]), np.nanmean(a[:, 2]), 100 * np.mean(r1) if r1 else float("nan"), len(r1), 100 * np.mean(r2) if r2 else float("nan"))


print("\n" + "=" * 120 + "\nPART A -- every minute: does a taker-pressure RAMP predict continuation in its direction?\n" + "=" * 120)
valid = ~np.isnan(levB) & ~np.isnan(levS)
rng = random.Random(5)
sub = np.nonzero(valid)[0]; sub = sub[rng.sample(range(len(sub)), 60000)] if len(sub) > 60000 else sub   # baseline sample
res = {}
for s, lev, stk, gro, sh, name in ((1, levB, stkB, groB, shB, "BUY"), (-1, levS, stkS, groS, shS, "SELL")):
    burst, ramp, cont = states(lev, stk, gro)
    dom = sh >= 0.5
    for lab, mask in (("ALL minutes, dominant side", valid & dom), ("BURST (level>=3, streak<=1)", valid & burst),
                      ("RAMP (streak>=3, growth>=2, level>=2)", valid & ramp), ("RAMP-CONT (streak>=5)", valid & cont),
                      ("RAMP + level>=4", valid & ramp & (lev >= 4)), ("growing but weak (streak>=3, level<1.5)", valid & (stk >= 3) & (lev < 1.5))):
        idx = np.nonzero(mask)[0]
        if lab.startswith("ALL"):
            idx = np.array(sorted(set(idx) & set(sub)))
        ev = eval_events(idx, s)
        res[(name, lab)] = ev
        print("  %-4s %-44s 2025 %s" % (name, lab, summ(ev[2025])))
        print("  %-4s %-44s 2026 %s" % ("", "", summ(ev[2026])))
# circular-shift null for RAMP (flows shifted by k days against the price path)
print("  -- circular-shift null for RAMP (20 shifts of the flow series by 5..100 days): P(+0.2 first) --")
nulls = []
for i in range(20):
    k = rng.randint(5, 100) * 1440 * rng.choice((-1, 1))
    lev2 = np.roll(levB, k); stk2 = np.roll(stkB, k); gro2 = np.roll(groB, k)
    _, ramp2, _ = states(lev2, stk2, gro2)
    idx = np.nonzero(valid & ramp2)[0]; ev = eval_events(idx, 1, light=True, cap=4000)
    r1 = [r[3] for r in ev[2025] + ev[2026] if r[3] is not None]
    nulls.append(100 * np.mean(r1) if r1 else np.nan)
real = [r[3] for r in res[("BUY", "RAMP (streak>=3, growth>=2, level>=2)")][2025] + res[("BUY", "RAMP (streak>=3, growth>=2, level>=2)")][2026] if r[3] is not None]
print("     BUY RAMP real %.1f%%   null mean %.1f%%  sd %.1f  max %.1f  (%.0f s)" % (100 * np.mean(real), np.nanmean(nulls), np.nanstd(nulls), np.nanmax(nulls), time.time() - T0), flush=True)

# ---------------------------------------------------------------------------------------------------------------------
print("\n" + "=" * 120 + "\nPART B -- Radar Runner 30m bucket: the trade-side pressure state at the fire (last closed minute before the bucket close)\n" + "=" * 120)
fires = [tuple(f) for f in json.load(open(CAN.CACHE))]
T1e = T1 + 60.0


def feat_at(et, s):
    j = int(np.searchsorted(T1e, et, side="right")) - 1          # last minute CLOSED at/before the bucket close
    if j < 65 or T1e[j] < et - 600:
        return None
    lev, stk, gro, sh = (levB, stkB, groB, shB) if s > 0 else (levS, stkS, groS, shS)
    olev = levS if s > 0 else levB
    w5 = slice(j - 4, j + 1)
    share5 = float(np.sum((BD if s > 0 else SD)[w5]) / max(1e-9, np.sum(tot[w5])))
    sustained5 = int(np.sum(lev[w5] >= 2))
    return dict(streak=int(stk[j]), growth=float(gro[j]), level=float(lev[j]), share5=share5, sustained5=sustained5,
                olevel=float(olev[j]), burst=bool((lev[j] >= 3) and (stk[j] <= 1)), ramp=bool((stk[j] >= 3) and (gro[j] >= 2) and (lev[j] >= 2)), cont=bool(stk[j] >= 5))


def run_taken(fs, exit_rule="canonical"):
    taken = []; busy = -1.0
    for (k, t, s, e, sl) in fs:
        if t < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        if exit_rule == "canonical":
            net, outc, tx = CAN.resolve_scaleout(s, e, sl, t, T1, H1, L1)
        else:
            net, outc, tx = pressure_exit(s, e, sl, t, exit_rule)
        f = feat_at(t, s)
        taken.append(dict(t=t, s=s, e=e, sl=sl, net=net, r=net / sld, outc=outc, f=f, y=datetime.fromtimestamp(t, tz=timezone.utc).year,
                          m=datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")))
        busy = tx
    return taken


def pressure_exit(s, e, sl, t0, rule, cap=240):
    """(i) 'trail_after_tp1': 50% off at TP1 (0.24%), stop -> BE, the runner exits at the close of the first minute whose
    3-min trade-side $ share < 0.35 (pressure flipped), or BE / cap.  (ii) 'ride': no TP1 -- the whole position rides until
    the 3-min share < 0.35 (after at least 3 minutes), SL, or cap."""
    g1 = 0.0024; tp1 = e * (1 + s * g1)
    i0 = int(np.searchsorted(T1, t0 - 1)); hit1 = False
    for j in range(i0, min(N, i0 + cap)):
        hi = H1[j]; lo = L1[j]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):
                return s * (sl - e) / e - FEE - 2 * SLIP, "SL", T1[j]
            if rule == "trail_after_tp1" and ((hi >= tp1) if s > 0 else (lo <= tp1)):
                hit1 = True
        else:
            if (lo <= e) if s > 0 else (hi >= e):
                return 0.5 * (g1 - FEE - SLIP) + 0.5 * (0.0 - FEE - 2 * SLIP), "TP1_BE", T1[j]
        if j - i0 >= 2:
            w = slice(j - 2, j + 1)
            share = float(np.sum((BD if s > 0 else SD)[w]) / max(1e-9, np.sum(tot[w])))
            if share < 0.35 and (hit1 or rule == "ride"):
                ret = s * (C1[j] / e - 1.0)
                if hit1:
                    return 0.5 * (g1 - FEE - SLIP) + 0.5 * (ret - FEE - 2 * SLIP), "TP1_TRAIL", T1[j]
                return ret - FEE - 2 * SLIP, "RIDE_FLIP", T1[j]
    j = min(N - 1, i0 + cap - 1); ret = s * (C1[j] / e - 1.0)
    if hit1:
        return 0.5 * (g1 - FEE - SLIP) + 0.5 * (ret - FEE - 2 * SLIP), "TP1_CAP", T1[j]
    return ret - FEE - 2 * SLIP, "CAP", T1[j]


def report(label, taken, full=False):
    if not taken:
        print("  %-46s no trades" % label); return
    nets = np.array([x["net"] for x in taken]); rs = np.array([x["r"] for x in taken])
    nW = int((nets > 0).sum()); nL = int((nets < 0).sum())
    line = "  %-46s n %5d  W/BE/L %5d/%3d/%4d  win %5.1f%%  avg net %+.3f%%  avg R %+.3f" % (label, len(taken), nW, len(taken) - nW - nL, nL, 100 * nW / len(taken), 100 * nets.mean(), rs.mean())
    if full:
        eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
        days = day_blocks([(x["t"], x["net"], x["r"]) for x in taken]); m4 = mc(days, 0.4, 4.0, "R")
        line += "  DD %.1f%%  prop %.1f%%" % (dd, m4["p"])
    print(line, flush=True)
    for Y in (2025, 2026):
        yr = [x for x in taken if x["y"] == Y]
        if yr:
            ny = np.array([x["net"] for x in yr]); nw = int((ny > 0).sum())
            print("      %d: n %5d  win %5.1f%% +- %.1f  avg net %+.3f%%" % (Y, len(yr), 100 * nw / len(yr), 100 * 1.96 * math.sqrt(0.25 / len(yr)), 100 * ny.mean()))


base = run_taken(fires)
report("CANONICAL (unfiltered)", base, full=True)
have = [x for x in base if x["f"] is not None]
print("  fires with a pressure state: %d of %d taken" % (len(have), len(base)))
p_all = np.mean([x["net"] > 0 for x in have])


def band_table(name, key, edges):
    print("  -- %s (disjoint bands) --" % name)
    vals = np.array([x["f"][key] for x in have], dtype=float)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [x for x, v in zip(have, vals) if lo <= v < hi]
        if len(sel) < 30:
            print("     [%5.2f, %5.2f)  n %5d  (too few)" % (lo, hi, len(sel))); continue
        w = np.array([x["net"] > 0 for x in sel]); p = w.mean(); z = (p - p_all) / math.sqrt(p_all * (1 - p_all) / len(sel))
        w25 = [x["net"] > 0 for x in sel if x["y"] == 2025]; w26 = [x["net"] > 0 for x in sel if x["y"] == 2026]
        print("     [%5.2f, %5.2f)  n %5d  win %5.1f%%  z %+5.2f  avg net %+.3f%%   2025 %5.1f%% (n=%d)  2026 %5.1f%% (n=%d)" % (
              lo, hi, len(sel), 100 * p, z, 100 * np.mean([x["net"] for x in sel]), 100 * np.mean(w25) if w25 else float("nan"), len(w25), 100 * np.mean(w26) if w26 else float("nan"), len(w26)))


band_table("trade-side STREAK (rising minutes into the fire)", "streak", [0, 1, 3, 5, 99])
band_table("trade-side GROWTH over the streak (fold)", "growth", [0, 1.0001, 1.5, 2, 3, 99])
band_table("trade-side LEVEL vs prior-60-min median (fold)", "level", [0, 1, 2, 4, 99])
band_table("trade-side 5-min $ SHARE", "share5", [0, 0.4, 0.5, 0.6, 0.7, 1.01])
band_table("5-min SUSTAINED count (minutes at level >= 2)", "sustained5", [0, 1, 3, 6])
band_table("OPPOSITE-side LEVEL (pressure against the trade)", "olevel", [0, 1, 2, 4, 99])
print("  -- A-states at the fire --")
for lab, key in (("BURST", "burst"), ("RAMP", "ramp"), ("RAMP-CONT", "cont")):
    for flag in (True, False):
        sel = [x for x in have if x["f"][key] == flag]
        if sel:
            w = np.array([x["net"] > 0 for x in sel]); p = w.mean(); z = (p - p_all) / math.sqrt(p_all * (1 - p_all) / len(sel))
            w25 = [x["net"] > 0 for x in sel if x["y"] == 2025]; w26 = [x["net"] > 0 for x in sel if x["y"] == 2026]
            print("     %-10s %-5s n %5d  win %5.1f%%  z %+5.2f  avg net %+.3f%%   2025 %5.1f%% (n=%d)  2026 %5.1f%% (n=%d)" % (
                  lab, flag, len(sel), 100 * p, z, 100 * np.mean([x["net"] for x in sel]), 100 * np.mean(w25) if w25 else float("nan"), len(w25), 100 * np.mean(w26) if w26 else float("nan"), len(w26)))

# the strongest causal candidates re-run as FILTERED strategies (re-taken), with fragility + shift null
print("\n  -- FILTERED strategies (fires filtered, then re-taken; canonical bracket) --")
cands = {"share5 >= 0.6": lambda f: f["share5"] >= 0.6, "share5 < 0.4 (inverse)": lambda f: f["share5"] < 0.4,
         "level >= 2": lambda f: f["level"] >= 2, "streak >= 3": lambda f: f["streak"] >= 3,
         "RAMP": lambda f: f["ramp"], "sustained5 >= 3": lambda f: f["sustained5"] >= 3,
         "opposite level < 1 (no counter-pressure)": lambda f: f["olevel"] < 1}
for lab, fn in cands.items():
    fs = [f for f in fires if (feat_at(f[1], f[2]) or {}) and fn(feat_at(f[1], f[2]))]
    tk = run_taken(fs)
    report("FILTER %s" % lab, tk, full=True)
    if tk:
        months = {}
        for x in tk:
            months.setdefault(x["m"], []).append(x["net"])
        best = max(months, key=lambda mm: sum(months[mm]))
        rest = [x for x in tk if x["m"] != best]
        print("      fragility: without the best month (%s) win %.1f%%  avg net %+.3f%%" % (best, 100 * np.mean([x["net"] > 0 for x in rest]), 100 * np.mean([x["net"] for x in rest])))
# circular-shift null for the filter with the largest |z| (share5 >= 0.6 and level >= 2 both tested)
print("  -- circular-shift null (flows shifted 5..100 days; filter recomputed; 12 shifts): filtered win rate --")
for lab in ("share5 >= 0.6", "level >= 2"):
    outs = []
    for i in range(12):
        k = rng.randint(5, 100) * 1440 * rng.choice((-1, 1))
        BDs, SDs = np.roll(BD, k), np.roll(SD, k); tots = BDs + SDs
        levBs = np.roll(levB, k); levSs = np.roll(levS, k)
        fs = []
        for (kk, t, s, e, sl) in fires:
            j = int(np.searchsorted(T1e, t, side="right")) - 1
            if j < 65: continue
            w5 = slice(j - 4, j + 1)
            if lab.startswith("share5"):
                ok = float(np.sum((BDs if s > 0 else SDs)[w5]) / max(1e-9, np.sum(tots[w5]))) >= 0.6
            else:
                ok = float((levBs if s > 0 else levSs)[j]) >= 2
            if ok: fs.append((kk, t, s, e, sl))
        tk = run_taken(fs)
        outs.append(100 * np.mean([x["net"] > 0 for x in tk]) if tk else np.nan)
    print("     %-14s null win mean %.1f%%  sd %.1f  max %.1f  (n per shift ~%d)" % (lab, np.nanmean(outs), np.nanstd(outs), np.nanmax(outs), len(tk)))

print("\n" + "=" * 120 + "\nPART B2 -- pressure EXITS on the canonical entries (same taken sequence basis: re-taken per exit rule)\n" + "=" * 120)
report("canonical scale-out (TP1 0.24 / TP2 0.44 / BE)", base, full=True)
for rule, lab in (("trail_after_tp1", "TP1 then trail the runner on 3-min share < 0.35"), ("ride", "RIDE the pressure from entry (exit on 3-min share < 0.35 / SL / 240 min)")):
    tk = run_taken(fires, exit_rule=rule)
    report(lab, tk, full=True)
    mix = {}
    for x in tk:
        mix[x["outc"]] = mix.get(x["outc"], 0) + 1
    print("      outcomes: %s" % mix)
print("\nharness: study/pressure_ramp_study.py (fires: study/radarrun_30mbkt_live_full.py cache; 1m: study/clock_archive)  DONE in %.0f s" % (time.time() - T0))
