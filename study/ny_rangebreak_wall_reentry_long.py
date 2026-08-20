"""SYMMETRIC LONG test: NY LONG break (close>rangehigh) + re-entry at a BUY WALL (order-flow 'S'=support) on the retrace
DOWN. Mirror of study/ny_rangebreak_wall_reentry.py (which did the SHORT side). ⚠ the LONG break base is ~dead (coin
flip; see memory) so a re-entry cannot manufacture an edge on it — but we prove it empirically, not by assertion. Same
battery: BASELINE(1 long) vs midpoint vs buy-wall(price / causal edge); conditional on wall-days; PAIRED marginal
(isolates the add); LEVERAGE control (lever single-entry to matched drift). REAL 2-unit sizing (trade_R=r1+r2, base risk
const), R0.4 HyroTrader $200k. 30m clock+bucket. IN-SAMPLE. python study/ny_rangebreak_wall_reentry_long.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from study.ny_rangebreak_poc_prop import (mc, day_blocks, R_HRS, B_HRS, SL_PAD, FEE, SLIP, TP_THR, MAXHOLD)
from study.ny_rangebreak_wall_reentry import _vpct, RADAR_MULT, BAND_MIN, _mc_scaled, _cell, _yr, _pcell
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def load_long(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    vp = _vpct(Hi, Lo, C, n)
    W = AL.detect(A, skip_last=False)
    swalls_by_day = {}                                            # same-day 'S'(support=BUY) walls: (i0, price, v0)
    for w in W:
        if w.get("side") != "S":
            continue
        i0 = int(w.get("i0", -1)); p = _f(w.get("price"))
        if i0 < 0 or p <= 0:
            continue
        swalls_by_day.setdefault(DATE[i0], []).append((i0, p, float(vp[i0])))
    return O, C, Hi, Lo, ST, HR, DATE, WD, swalls_by_day, n


def wall_level_long(swalls, k, entry1, sl, edge):
    """nearest causal same-day BUY wall (support) in (sl, entry1) below entry1; edge -> fill at the causal UPPER radar
    edge p*(1+RADAR_MULT*BAND_MIN*v0); else the wall price. Pick the HIGHEST such level (first support hit on the way down)."""
    best = None
    for i0, p, v0 in swalls:
        if i0 > k:
            continue
        trig = p * (1.0 + RADAR_MULT * BAND_MIN * v0) if edge else p
        if sl < trig < entry1 and (best is None or trig > best):
            best = trig
    return best


def sim_long(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry):
    """LONG. trade_R = r1 + r2, r_i = unit net return / sl1_dist (unit2 same notional as unit1). Stop-first pessimistic."""
    tpmult = 2.0 if rngpct < TP_THR else 0.5
    sl1 = (entry1 - sl) / entry1                                   # long risk fraction (stop below)
    filled = False; et = ST[k]
    def avg():
        return (entry1 + poc) / 2.0 if filled else entry1
    def tp():
        return avg() + tpmult * rng
    def R_at(exitp, is_tp):
        f = FEE + SLIP + (0.0 if is_tp else SLIP)
        r1 = ((exitp - entry1) / entry1 - f) / sl1
        if filled:
            r2 = ((exitp - poc) / poc - f) / sl1
            return r1 + r2
        return r1
    for j in range(k + 1, n):
        if ST[j] > et + MAXHOLD:
            return R_at(C[j - 1], False), "end", filled
        hi = Hi[j]; lo = Lo[j]
        if reentry and not filled and lo <= poc:                  # retrace DOWN to support -> add the 2nd long
            filled = True
        if lo <= sl:                                              # stopped (all units), below the range wick
            return R_at(sl, False), "sl", filled
        if hi >= tp():                                            # TP (all units), above the blended avg
            return R_at(tp(), True), "tp", filled
    return R_at(C[-1], False), "end", filled


def trades(D, mode):
    O, C, Hi, Lo, ST, HR, DATE, WD, sw, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    out = []; nwall = 0; nfill = 0; ndays = 0
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
        k = None
        for i in bi:
            if C[i] > rhi:                                         # LONG break (close above the range body high)
                k = i; break
        if k is None:
            continue
        ndays += 1; entry1 = C[k]; rngpct = 100.0 * rng / entry1; sl = wlo * (1 - SL_PAD)
        mid = (rhi + rlo) / 2.0
        if mode == "base":
            poc = mid; reentry = False
        elif mode == "mid":
            poc = mid; reentry = True
        else:
            lvl = wall_level_long(sw.get(d, []), k, entry1, sl, edge=(mode == "walledge"))
            if lvl is None:
                poc = mid; reentry = False
            else:
                poc = lvl; reentry = True; nwall += 1
        R, outc, filled = sim_long(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry)
        if filled:
            nfill += 1
        out.append((ST[k], R, filled, entry1, mid, sl, rng, rngpct, k))
    return out, dict(ndays=ndays, nwall=nwall, nfill=nfill)


def _wall_day_keys(D):
    O, C, Hi, Lo, ST, HR, DATE, WD, sw, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    keys = set()
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rhi = max(max(O[i], C[i]) for i in ri); wlo = min(Lo[i] for i in ri); whi = max(Hi[i] for i in ri)
        if whi - wlo <= 0:
            continue
        k = None
        for i in bi:
            if C[i] > rhi:
                k = i; break
        if k is None:
            continue
        entry1 = C[k]; sl = wlo * (1 - SL_PAD)
        if wall_level_long(sw.get(d, []), k, entry1, sl, edge=False) is not None:
            keys.add(k)
    return keys


def report(name, tr, meta):
    Rs = np.array([t[1] for t in tr]); days, _ = day_blocks([(t[0], t[1]) for t in tr]); m = mc(days)
    wr = 100.0 * meta["nwall"] / max(1, meta["ndays"]); fr = 100.0 * meta["nfill"] / max(1, meta["ndays"])
    print("  %-24s n=%-4d win%4.1f%% avgR%+.3f wall-day%4.0f%% add-fill%4.0f%% | R0.4 pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
          % (name, len(tr), 100.0 * (Rs > 0).mean(), Rs.mean(), wr, fr, m["p"], m["med"], m["dd99"], m["worst"]), flush=True)


def _cond(name, tr):
    print("    %-28s ALL %s | IS %s | OOS %s"
          % (name, _cell(tr), _cell([t for t in tr if _yr(t[0]) == 2025]), _cell([t for t in tr if _yr(t[0]) == 2026])), flush=True)


def _paired(name, bmap, remap, wdays):
    keys = [k for k in wdays if k in bmap and k in remap]
    a = [remap[k][1] - bmap[k] for k in keys]
    i = [remap[k][1] - bmap[k] for k in keys if _yr(remap[k][0]) == 2025]
    o = [remap[k][1] - bmap[k] for k in keys if _yr(remap[k][0]) == 2026]
    print("    %-28s ALL %s | IS %s | OOS %s" % (name, _pcell(a), _pcell(i), _pcell(o)), flush=True)


def main():
    print("NY LONG break + BUY-WALL re-entry (symmetric mirror) | REAL 2-unit sizing | R0.4 HyroTrader $200k | IN-SAMPLE", flush=True)
    print("buy wall add = nearest causal same-day 'S'(support) wall in (SL, entry1). ⚠ LONG break base is ~dead.\n", flush=True)
    MODES = (("base", "BASELINE (1 long)"), ("mid", "re-entry MIDPOINT"),
             ("wall", "re-entry BUY WALL (price)"), ("walledge", "re-entry BUY WALL (causal edge)"))
    for root, tf in SRCS:
        lab = ("clock" if "clock" in root else "bucket") + " " + tf
        print("---- %s ----" % lab, flush=True)
        D = load_long(root, tf)
        cache = {mode: trades(D, mode) for mode, _ in MODES}
        for mode, nm in MODES:
            tr, meta = cache[mode]
            report(nm, tr, meta)
        btr = cache["base"][0]; wtr = cache["wall"][0]; etr = cache["walledge"][0]; wdays = _wall_day_keys(D)
        print("  -- CONDITIONAL (buy-wall days only) --", flush=True)
        _cond("BASELINE on wall-days", [t for t in btr if t[8] in wdays])
        _cond("WALL re-entry (price)", [t for t in wtr if t[8] in wdays])
        _cond("WALL re-entry (causal edge)", [t for t in etr if t[8] in wdays])
        print("  -- PAIRED marginal (wall_reentry_R - baseline_R, same days) --", flush=True)
        bmap = {t[8]: t[1] for t in btr}
        _paired("add at wall PRICE", bmap, {t[8]: (t[0], t[1]) for t in wtr}, wdays)
        _paired("add at causal EDGE", bmap, {t[8]: (t[0], t[1]) for t in etr}, wdays)
        print("  -- LEVERAGE control (single-entry baseline levered to match re-entry drift) --", flush=True)
        bavg = np.mean([t[1] for t in btr]); eavg = np.mean([t[1] for t in etr])
        if bavg <= 0:
            print("    baseline avgR<=0 (%+.3f) -> long break base has no positive drift to lever; re-entry cannot pass. " % bavg, flush=True)
        L = eavg / bavg if bavg > 0 else 1.0
        bdays, _ = day_blocks([(t[0], t[1]) for t in btr]); mb = _mc_scaled(bdays, L)
        edays, _ = day_blocks([(t[0], t[1]) for t in etr]); me = mc(edays)
        print("    baseline x%.2f (drift-matched)  pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
              % (L, mb["p"], mb["med"], mb["dd99"], mb["worst"]), flush=True)
        print("    causal-EDGE re-entry           pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
              % (me["p"], me["med"], me["dd99"], me["worst"]), flush=True)


if __name__ == "__main__":
    main()
