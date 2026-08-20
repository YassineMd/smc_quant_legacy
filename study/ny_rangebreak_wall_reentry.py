"""NY short-break re-entry at a SELL WALL (order-flow RESISTANCE) instead of the midpoint POC. Hypothesis: the midpoint
POC add filled adversely (retrace precedes failed breaks -> SL); adding only where the retrace hits a genuine overhead
sell wall ('R' resistance) should fill SELECTIVELY on trades that actually reverse down = less adverse. Walls from
app.absorption_level_detect.detect() over the full archive; the add level = the nearest CAUSAL SAME-DAY 'R' wall whose
price sits in (entry1, SL) and formed at/before the break bar (i0<=k). Two fill triggers: at the wall PRICE, and at the
wall's lower radar EDGE (price-3*band, resistance-zone start). Sizing = REAL 2-unit (trade_R=r1+r2, base risk const),
HyroTrader $200k R0.4 prop-MC. Also a CONDITIONAL cut (wall-days only) to isolate the wall's marginal effect from the
no-wall dilution. SHORT side, 30m clock+bucket. IN-SAMPLE. python study/ny_rangebreak_wall_reentry.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
import random
from app import absorption_level_detect as AL
from study.ny_rangebreak_poc_prop import (sim, mc, day_blocks, R_HRS, B_HRS, SL_PAD, RP,
                                          TARGET, MAXDD, NPATH, MAXD)
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def _mc_scaled(days, scale):
    """same day-block MC as ny_rangebreak_poc_prop.mc but each trade R scaled by `scale` (uniform leverage)."""
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dn in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for R in day:
                eq += RP * scale * R; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD or ipeak - eq >= 4.0:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(dn)
    return dict(p=100.0 * passes / NPATH, med=(np.percentile(dtp, 50) if dtp else 0),
                dd99=np.percentile(mdds, 99), worst=max(mdds))
RADAR_MULT = 3.0; BAND_MIN = float(getattr(AL, "BAND_MIN", 0.10)); ATR_WIN = int(getattr(AL, "ATR_WIN", 50))


def _vpct(Hi, Lo, C, n):
    """rolling-mean candle-range% = the CAUSAL volatility unit (same as AL.detect's vpct; uses only past bars)."""
    vp = np.zeros(n); s = 0.0
    for i in range(n):
        s += (Hi[i] - Lo[i]) / C[i] if C[i] > 0 else 0.0
        if i >= ATR_WIN:
            s -= (Hi[i - ATR_WIN] - Lo[i - ATR_WIN]) / C[i - ATR_WIN] if C[i - ATR_WIN] > 0 else 0.0
        vp[i] = s / min(i + 1, ATR_WIN)
    return vp


def load(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    vp = _vpct(Hi, Lo, C, n)
    W = AL.detect(A, skip_last=False)
    rwalls_by_day = {}                                            # same-day 'R'(sell) walls: (i0, price, v0-at-formation)
    for w in W:                                                   # NOTE: w['band'] is LOOK-AHEAD (uses post-formation ejection)
        if w.get("side") != "R":                                 #   -> we do NOT use it; we rebuild a CAUSAL band from vpct[i0]
            continue
        i0 = int(w.get("i0", -1)); p = _f(w.get("price"))
        if i0 < 0 or p <= 0:
            continue
        rwalls_by_day.setdefault(DATE[i0], []).append((i0, p, float(vp[i0])))
    return O, C, Hi, Lo, ST, HR, DATE, WD, rwalls_by_day, n


def wall_level(rwalls, k, entry1, sl, edge):
    """nearest causal same-day overhead sell wall in (entry1, sl). edge=True -> fill at the CAUSAL lower radar edge
    p*(1 - RADAR_MULT*BAND_MIN*v0) (formation-volatility floor band, no future ejection); else fill at the wall price."""
    best = None
    for i0, p, v0 in rwalls:
        if i0 > k:
            continue
        trig = p * (1.0 - RADAR_MULT * BAND_MIN * v0) if edge else p
        if entry1 < trig < sl and (best is None or trig < best):
            best = trig
    return best


def trades(D, mode):
    O, C, Hi, Lo, ST, HR, DATE, WD, rw, n = D
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
            if C[i] < rlo:
                k = i; break
        if k is None:
            continue
        ndays += 1; entry1 = C[k]; rngpct = 100.0 * rng / entry1; sl = whi * (1 + SL_PAD)
        mid = (rhi + rlo) / 2.0
        if mode == "base":
            poc = mid; reentry = False
        elif mode == "mid":
            poc = mid; reentry = True
        else:                                                     # wall / walledge
            lvl = wall_level(rw.get(d, []), k, entry1, sl, edge=(mode == "walledge"))
            if lvl is None:
                poc = mid; reentry = False                        # no overhead sell wall -> single entry
            else:
                poc = lvl; reentry = True; nwall += 1
        R, outc, filled = sim(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry)
        if filled:
            nfill += 1
        out.append((ST[k], R, filled, entry1, mid, sl, rng, rngpct, k))
    return out, dict(ndays=ndays, nwall=nwall, nfill=nfill), (O, C, Hi, Lo, ST, n)


def report(name, tr, meta):
    Rs = np.array([t[1] for t in tr]); days, _ = day_blocks([(t[0], t[1]) for t in tr]); m = mc(days)
    wr = 100.0 * meta["nwall"] / max(1, meta["ndays"]); fr = 100.0 * meta["nfill"] / max(1, meta["ndays"])
    print("  %-24s n=%-4d win%4.1f%% avgR%+.3f wall-day%4.0f%% add-fill%4.0f%% | R0.4 pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
          % (name, len(tr), 100.0 * (Rs > 0).mean(), Rs.mean(), wr, fr, m["p"], m["med"], m["dd99"], m["worst"]), flush=True)


def main():
    print("NY short-break SELL-WALL re-entry vs midpoint | REAL 2-unit sizing, base-risk const | R0.4 HyroTrader $200k | IN-SAMPLE", flush=True)
    print("wall add = nearest causal same-day 'R'(resistance) wall in (entry1,SL). avgR = ACCOUNT expectancy (SUM of units).\n", flush=True)
    MODES = (("base", "BASELINE (1 unit)"), ("mid", "re-entry MIDPOINT"),
             ("wall", "re-entry SELL WALL (price)"), ("walledge", "re-entry SELL WALL (causal edge)"))
    for root, tf in SRCS:
        lab = ("clock" if "clock" in root else "bucket") + " " + tf
        print("---- %s ----" % lab, flush=True)
        D = load(root, tf)
        cache = {mode: trades(D, mode) for mode, _ in MODES}
        for mode, nm in MODES:
            tr, meta, _ = cache[mode]
            report(nm, tr, meta)
        # CONDITIONAL: restrict to days a sell wall existed, baseline vs wall-reentry (isolate the wall's marginal effect)
        btr = cache["base"][0]; wtr = cache["wall"][0]; etr = cache["walledge"][0]; wdays = _wall_day_keys(D)
        print("  -- CONDITIONAL (sell-wall days only) --", flush=True)
        _cond("BASELINE on wall-days", [t for t in btr if t[8] in wdays])
        _cond("WALL re-entry (price)", [t for t in wtr if t[8] in wdays])
        _cond("WALL re-entry (causal edge)", [t for t in etr if t[8] in wdays])
        print("  -- PAIRED marginal (wall_reentry_R - baseline_R, same days) --", flush=True)
        bmap = {t[8]: t[1] for t in btr}
        _paired("add at wall PRICE", bmap, {t[8]: (t[0], t[1]) for t in wtr}, wdays)
        _paired("add at causal EDGE", bmap, {t[8]: (t[0], t[1]) for t in etr}, wdays)
        # LEVERAGE control: lever the single-entry baseline to the re-entry's account drift; if pass/DD match -> the
        # re-entry's pass-rate gain is PURE LEVERAGE, not a real risk-adjusted edge.
        print("  -- LEVERAGE control (single-entry baseline levered to match re-entry drift) --", flush=True)
        bavg = np.mean([t[1] for t in btr]); eavg = np.mean([t[1] for t in etr])
        L = eavg / bavg if bavg > 0 else 1.0
        bdays, _ = day_blocks([(t[0], t[1]) for t in btr]); mb = _mc_scaled(bdays, L)
        edays, _ = day_blocks([(t[0], t[1]) for t in etr]); me = mc(edays)
        print("    baseline x%.2f (drift-matched)  pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
              % (L, mb["p"], mb["med"], mb["dd99"], mb["worst"]), flush=True)
        print("    causal-EDGE re-entry           pass %5.1f%% med %3.0fd DDp99 %4.1f%% worst %4.1f%%"
              % (me["p"], me["med"], me["dd99"], me["worst"]), flush=True)


def _wall_day_keys(D):
    O, C, Hi, Lo, ST, HR, DATE, WD, rw, n = D
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
        rlo = min(min(O[i], C[i]) for i in ri); whi = max(Hi[i] for i in ri); wlo = min(Lo[i] for i in ri)
        if whi - wlo <= 0:
            continue
        k = None
        for i in bi:
            if C[i] < rlo:
                k = i; break
        if k is None:
            continue
        entry1 = C[k]; sl = whi * (1 + SL_PAD)
        if wall_level(rw.get(d, []), k, entry1, sl, edge=False) is not None:
            keys.add(k)
    return keys


def _yr(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).year


def _cell(tr):
    if not tr:
        return "n=0           "
    Rs = np.array([t[1] for t in tr])
    return "n=%-3d win%4.1f%% avgR%+.3f" % (len(tr), 100.0 * (Rs > 0).mean(), Rs.mean())


def _cond(name, tr):
    print("    %-28s ALL %s | IS %s | OOS %s"
          % (name, _cell(tr), _cell([t for t in tr if _yr(t[0]) == 2025]), _cell([t for t in tr if _yr(t[0]) == 2026])), flush=True)


def _pcell(diffs):
    if len(diffs) < 3:
        return "n=%-3d              " % len(diffs)
    d = np.array(diffs); m = d.mean(); se = d.std(ddof=1) / np.sqrt(len(d))
    t = m / se if se > 0 else 0.0
    return "n=%-3d mean%+.3fR t%+.2f" % (len(d), m, t)


def _paired(name, bmap, remap, wdays):
    """paired diff on wall-days that ALSO filled/attempted: re_R - base_R (same entry/SL, only the add differs)."""
    keys = [k for k in wdays if k in bmap and k in remap]
    all_d = [remap[k][1] - bmap[k] for k in keys]
    is_d = [remap[k][1] - bmap[k] for k in keys if _yr(remap[k][0]) == 2025]
    oos_d = [remap[k][1] - bmap[k] for k in keys if _yr(remap[k][0]) == 2026]
    print("    %-28s ALL %s | IS %s | OOS %s" % (name, _pcell(all_d), _pcell(is_d), _pcell(oos_d)), flush=True)


if __name__ == "__main__":
    main()
