# -*- coding: utf-8 -*-
"""RADAR RUNNER 30m BUCKET on TRADE-BASED walls (user 2026-09-08: "radar runner on order-flow walls based on trades on the
30m bucket -- which one gives the highest win rate on the 0.2% TP").  Same canonical harness as
study/radarrun_30mbkt_live_full.py (bracket, 1m first-touch resolution, fees, non-overlap taken(), prop MC), the ONLY
change is where a wall comes from:

  CANONICAL  = the shipped detector's walls (the cached union-live fires of the canonical harness)
  TRADE      = ABSORBED BIG-PLAYER levels: a print (>= usd floor) or a same-ms sweep's END price whose 30m bar CLOSED
               against the taker (big buy, close below it -> 'R'; big sell, close above it -> 'S').  Known at bar close.
  RAN        = the control: the same prints when the bar closed WITH the taker.
  MERGE      = canonical + trade fires (union, deduped per bar).

Trade levels get the detector's OWN geometry and bookkeeping (absorption_level_detect.detect mirrored): volatility unit
vpct (ATR_WIN rolling range%), ejection over EJ_WIN bars -> band = P*v0*(BAND_MIN + base*BAND_RANGE), radar = +-3 band,
BREAK = body close beyond the radar, radar VISITS = re-entry after leaving, near-duplicate suppression (same side within
2*EPS of a live level).  The tracker is causal by construction (a level and its visits at bar k depend only on bars <= k),
so the full-history breakout detect equals the bar-by-bar union-live replay; gate 1 is VERIFIED below on sampled bar
closes (incremental detect at k must emit exactly the batch fires whose breakout bar is k).  The W=2000 trailing window
is applied as in the harness (a fire from a level older than W bars is dropped).

0.2% TP = the bracket's TP1 (0.24% gross = ~0.20% net): 'TP1 first' = TP1 touched before the SL at 1m (same bar -> SL).
python study/radarrun_30mbkt_tradewalls.py"""
import os, sys, json, math, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study import radarrun_30mbkt_live_full as CAN
from study.radarrun_hyro_prop import mc, day_blocks
from app import config, radar_breakout_detect as RB, absorption_level_detect as AL, bigprint_store as BP

W, SLBUF, FEE, SLIP, CAPMIN = CAN.W, CAN.SLBUF, CAN.FEE, CAN.SLIP, CAN.CAPMIN
T0 = time.time()
print("loading 30m buckets ...", flush=True)
A = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = np.array([_f(b.get("open_price", b.get("open"))) for b in A]); C = np.array([_f(b.get("close_price", b.get("close"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = np.array([_f(b.get("start_time")) for b in A]); ET = np.array([_f(b.get("end_time")) for b in A])
print("  %d bars %s -> %s (%.0f s)" % (n, datetime.fromtimestamp(ST[0], tz=timezone.utc).date(), datetime.fromtimestamp(ET[-1], tz=timezone.utc).date(), time.time() - T0), flush=True)
# volatility unit exactly as the detector: rolling mean of range% over ATR_WIN bars
vpct = np.zeros(n); _s = 0.0
for i in range(n):
    _s += (H[i] - L[i]) / C[i] if C[i] > 0 else 0.0
    if i >= AL.ATR_WIN:
        _s -= (H[i - AL.ATR_WIN] - L[i - AL.ATR_WIN]) / C[i - AL.ATR_WIN] if C[i - AL.ATR_WIN] > 0 else 0.0
    vpct[i] = _s / min(i + 1, AL.ATR_WIN)

print("loading big prints ...", flush=True)
pr = BP.load_prints_np(float(ST[0]), float(ET[-1])); sw = BP.load_sweeps_np(float(ST[0]), float(ET[-1]))
ev = np.concatenate([np.column_stack([pr[:, 0], pr[:, 1], pr[:, 2], pr[:, 3]]), np.column_stack([sw[:, 0], sw[:, 2], sw[:, 3], sw[:, 4]])])
ev = ev[np.argsort(ev[:, 0], kind="stable")]
bar = np.searchsorted(ST, ev[:, 0], side="right") - 1
ok = (bar >= 0) & (bar < n) & (ev[:, 0] <= ET[np.clip(bar, 0, n - 1)])
ev = ev[ok]; bar = bar[ok]
buy = ev[:, 3] > 0; cls = C[bar]
absorbed = (buy & (cls < ev[:, 1])) | (~buy & (cls > ev[:, 1]))
ran = (buy & (cls > ev[:, 1])) | (~buy & (cls < ev[:, 1]))
print("  %d prints + %d sweep ends in range: absorbed %.1f%%, ran %.1f%% (%.0f s)" % (len(pr), len(sw), 100 * absorbed.mean(), 100 * ran.mean(), time.time() - T0), flush=True)


def build_levels(mask, radar_mult=3.0, evs=None):
    """Trade levels as detector-shaped walls: [{price, side, band, i0, i1, broken, radar_runs:[(a,b,50.0)], ...}]."""
    E = ev if evs is None else evs
    idx = np.nonzero(mask)[0]
    by_bar = {}
    for e in idx:
        by_bar.setdefault(int(bar[e]), []).append((float(E[e, 1]), "R" if E[e, 3] > 0 else "S", float(E[e, 2])))
    active = []; done = []
    for i in range(n):
        still = []
        for w in active:
            P = w["P"]
            if i - w["i0"] <= AL.EJ_WIN:
                fav = (P - L[i]) / P if w["side"] == "R" else (H[i] - P) / P
                if fav > w["ej"]:
                    w["ej"] = fav
            base = min(1.0, w["ej"] / (AL.EJ_ATR_MULT * w["v0"])) if w["v0"] > 0 else 0.0
            band = P * w["v0"] * (AL.BAND_MIN + base * AL.BAND_RANGE); w["band"] = band
            r_lo = P - radar_mult * band; r_hi = P + radar_mult * band
            if (w["side"] == "R" and C[i] > r_hi) or (w["side"] == "S" and C[i] < r_lo):
                w["i1"] = i; w["broken"] = True; done.append(w); continue
            inside = (L[i] <= r_hi and H[i] >= r_lo)
            if inside:
                if not w["inzone"] and w["ever_left"]:
                    w["runs"].append([i, i])
                if w["ever_left"] and w["runs"]:
                    w["runs"][-1][1] = i
                w["inzone"] = True
            else:
                w["inzone"] = False; w["ever_left"] = True
            still.append(w)
        active = still
        for (price, side, usd) in by_bar.get(i, ()):                 # new levels born at this bar close
            if any(a["side"] == side and abs(a["P"] - price) <= price * AL.EPS * 2 for a in active):
                continue                                              # a live same-side level sits there already
            active.append({"P": price, "side": side, "i0": i, "ej": 0.0, "v0": vpct[i], "inzone": True, "ever_left": False,
                           "runs": [], "broken": False, "i1": None, "band": price * vpct[i] * AL.BAND_MIN, "usd": usd})
    out = []
    for w in done + active:
        out.append({"price": w["P"], "side": w["side"], "src": "trade", "i0": w["i0"], "i1": w["i1"] if w["broken"] else n - 1,
                    "broken": bool(w["broken"]), "strength": 1.0, "hits": len(w["runs"]), "band": w["band"],
                    "radar_runs": [(a, b, 50.0) for a, b in w["runs"]], "usd": w["usd"]})
    out.sort(key=lambda w: w["i0"])
    return out


def random_levels(mask, seed=3):
    """Null: one level per masked event at a RANDOM price inside its bar's range and a random side (same timing, same
    count) -> does the print's exact price / side carry anything, or is it the breakout-bar geometry alone?"""
    rng = np.random.RandomState(seed)
    idx = np.nonzero(mask)[0]
    ev2 = ev.copy()
    ev2[idx, 1] = L[bar[idx]] + rng.rand(len(idx)) * (H[bar[idx]] - L[bar[idx]])
    ev2[idx, 3] = rng.randint(0, 2, len(idx))
    return ev2


def fires_union(walls):
    """UNION-LIVE fires from frozen runs (what a bar-by-bar replay of radar_breakout_detect emits, see the module doc):
    during a visit (a, b) every bar k in [a+MINVISIT, b] that opens inside the radar and closes beyond the run side
    fires (the live visit ends at k); after the visit, the first qualifying bar in [b, b+2] fires (RB breaks after the
    first).  One signal per bar (first wall wins), the W trailing-window rule of the harness applied."""
    recs = []
    for w in walls:
        P = float(w["price"]); side = w["side"]; band = float(w["band"]); rlo = P - 3 * band; rhi = P + 3 * band
        s_ = 1 if side == "S" else -1
        for r in w["radar_runs"]:
            a = int(r[0]); b = int(r[1]); ks = []
            for k in range(max(1, a + RB.MINVISIT), min(b, n - 1) + 1):
                if rlo <= O[k] <= rhi and ((C[k] > rhi) if side == "S" else (C[k] < rlo)):
                    ks.append(k)
            for k in range(max(1, b), min(b + 2, n - 1) + 1):
                if (k - a) >= RB.MINVISIT and rlo <= O[k] <= rhi and ((C[k] > rhi) if side == "S" else (C[k] < rlo)):
                    if k not in ks:
                        ks.append(k)
                    break
            for k in ks:
                if k - int(w["i0"]) > W:
                    continue
                sl = max(L[k] * (1.0 - SLBUF), rlo) if s_ > 0 else min(H[k] * (1.0 + SLBUF), rhi)
                recs.append((k, float(ET[k]), s_, float(C[k]), float(sl)))
    recs.sort(key=lambda x: x[0])
    byet = {}
    for x in recs:
        byet.setdefault(x[1], x)
    return sorted(byet.values())


def fires_from(walls):
    """One-shot batch breakout detect over the full history (fires only at visit ENDS -> under-fires vs the terminal)."""
    sig = RB.detect(A, walls=walls, skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC)
    w_i0 = {}
    for w in walls:
        w_i0.setdefault((w["price"], w["side"]), []).append(w["i0"])
    byet = {}
    for g in sig:
        k = int(g["i"]); cands = [i for i in w_i0.get((g["price"], g["wall_side"]), ()) if i <= k]
        i0 = max(cands) if cands else None                            # the level alive at the fire
        if i0 is None or k - i0 > W:
            continue
        et = float(ET[k])
        if et not in byet:                                            # first signal on a bar wins (terminal persist key)
            byet[et] = (k, et, int(g["side"]), float(g["entry"]), float(g["sl_trade"]))
    return sorted(byet.values())


def verify_gate1(walls, fires, n_samples=250, seed=11):
    """Incremental detect at sampled bar closes k (trailing W bars, levels/visits truncated at k) must emit exactly the
    union fires whose breakout bar is k (fired-or-not per bar; the kept signal's side/entry/SL must be among the live ones)."""
    rng = random.Random(seed); ks = sorted(rng.sample(range(W + 10, n - 1), n_samples))
    batch_at = {}
    for (k, et, s, e, sl) in fires:
        batch_at.setdefault(k, set()).add((s, round(e, 4), round(sl, 4)))
    ok = 0; mism = []
    for k in ks:
        lo = k - W; sub = A[lo:k + 1]
        wk = []
        for w in walls:
            if w["i0"] < lo or w["i0"] > k:
                continue
            runs = [(a - lo, min(b, k) - lo, 50.0) for a, b in ((r[0], r[1]) for r in w["radar_runs"]) if a <= k]
            wk.append({"price": w["price"], "side": w["side"], "band": w["band"], "radar_runs": runs})
        got = set()
        for g in RB.detect(sub, walls=wk, skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            if lo + int(g["i"]) == k:
                got.add((int(g["side"]), round(float(g["entry"]), 4), round(float(g["sl_trade"]), 4)))
        exp = batch_at.get(k, set())
        # the batch keeps ONE signal per bar (first side); compare on the bar level: fired-or-not + entry/sl of the kept one
        exp_any = bool(exp); got_any = bool(got)
        if exp_any == got_any and (not exp or exp & got):
            ok += 1
        else:
            mism.append((k, exp, got))
    return ok, len(ks), mism[:3]


def tp1_first(s, e, sl, t0, T1, H1, L1):
    tp1 = e * (1 + s * 0.0024)
    i0 = int(np.searchsorted(T1, t0 - 1))
    for j in range(i0, min(len(T1), i0 + CAPMIN)):
        hi = H1[j]; lo = L1[j]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return 0
        if (hi >= tp1) if s > 0 else (lo <= tp1):
            return 1
    return None


def evaluate(label, fires, T1, H1, L1):
    taken = []; busy_until = -1.0
    for (k, t, s, e, sl) in fires:
        if t < busy_until:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        net, outc, tx = CAN.resolve_scaleout(s, e, sl, t, T1, H1, L1)
        taken.append(dict(t=t, s=s, e=e, sl=sl, net=net, r=net / sld, outc=outc, tp1=tp1_first(s, e, sl, t, T1, H1, L1),
                          y=datetime.fromtimestamp(t, tz=timezone.utc).year, m=datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")))
        busy_until = tx
    if not taken:
        print("  %-28s no trades" % label); return
    nets = np.array([x["net"] for x in taken]); rs = np.array([x["r"] for x in taken])
    nW = int((nets > 0).sum()); nL = int((nets < 0).sum()); nBE = len(taken) - nW - nL
    tp = [x["tp1"] for x in taken if x["tp1"] is not None]
    eq = np.cumsum(0.4 * rs); hist_dd = float((np.maximum.accumulate(eq) - eq).max())
    days = day_blocks([(x["t"], x["net"], x["r"]) for x in taken]); m4 = mc(days, 0.4, 4.0, "R")
    months = {}
    for x in taken:
        months[x["m"]] = months.get(x["m"], 0) + 1
    mc_ = sorted(months.values())
    print("  %-28s fires %5d  n %4d  W/BE/L %4d/%3d/%4d  win %5.1f%%  TP1-first %5.1f%% (n=%d)  avg net %+.3f%%  avg R %+.3f  DD %.1f%%  prop %4.1f%%  months %d (fires/month min/med/max %d/%d/%d)" % (
          label, len(fires), len(taken), nW, nBE, nL, 100 * nW / len(taken), 100 * np.mean(tp) if tp else float("nan"), len(tp),
          100 * nets.mean(), rs.mean(), hist_dd, m4["p"], len(months), mc_[0], mc_[len(mc_) // 2], mc_[-1]), flush=True)
    for Y in (2025, 2026):
        yr = [x for x in taken if x["y"] == Y]
        if yr:
            ny = np.array([x["net"] for x in yr]); tpy = [x["tp1"] for x in yr if x["tp1"] is not None]
            nw = int((ny > 0).sum()); nl = int((ny < 0).sum())
            se = math.sqrt(0.25 / len(tpy)) if tpy else float("nan")
            print("      %d: n %4d  W/BE/L %4d/%3d/%4d  win %5.1f%%  TP1-first %5.1f%% +- %.1f  avg net %+.3f%%" % (
                  Y, len(yr), nw, len(yr) - nw - nl, nl, 100 * nw / len(yr), 100 * np.mean(tpy) if tpy else float("nan"), 100 * 1.96 * se, 100 * ny.mean()), flush=True)
    slds = np.array([abs(x["e"] - x["sl"]) / x["e"] for x in taken]); kidx = {f[1]: f[0] for f in fires}
    bodies = np.array([abs(C[kidx[x["t"]]] - O[kidx[x["t"]]]) / O[kidx[x["t"]]] for x in taken if x["t"] in kidx])
    q = np.quantile(slds, [1 / 3, 2 / 3]); tps = np.array([x["tp1"] if x["tp1"] is not None else np.nan for x in taken])
    ter = [100 * np.nanmean(tps[(slds <= q[0])]), 100 * np.nanmean(tps[(slds > q[0]) & (slds <= q[1])]), 100 * np.nanmean(tps[(slds > q[1])])]
    print("      profile: SL distance median %.2f%% (p10 %.2f, p90 %.2f)   fire-bar body median %.2f%%   TP1-first by SL tercile (near/mid/far): %.1f / %.1f / %.1f%%" % (
          100 * np.median(slds), 100 * np.quantile(slds, 0.1), 100 * np.quantile(slds, 0.9), 100 * np.median(bodies) if len(bodies) else float("nan"), ter[0], ter[1], ter[2]), flush=True)
    if 100 * nW / len(taken) > 90 or m4["p"] > 95:
        print("      !! TOO-GOOD ALARM (gate 7): win > 90%% or prop > 95%% -- treat as a BUG until proven", flush=True)


print("loading 1m clock archive for resolution ...", flush=True)
_npz = os.path.join("study", "out", "clock1m_thl.npz")
if os.path.exists(_npz):
    _z = np.load(_npz); T1, H1, L1 = _z["T1"], _z["H1"], _z["L1"]
else:
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1; np.savez(_npz, T1=T1, H1=H1, L1=L1)
print("  %d 1m bars (%.0f s)" % (len(T1), time.time() - T0), flush=True)

canon = [tuple(f) for f in json.load(open(CAN.CACHE))]
print("\n" + "=" * 118)
print("RADAR RUNNER 30m BUCKET -- wall SOURCE comparison  |  canonical harness bracket + 1m resolution  |  0.2%% TP = TP1 (0.24%% gross)")
print("=" * 118)
evaluate("CANONICAL walls", canon, T1, H1, L1)
sets = {}
for usd_min, lab in ((5e5, "$500K"), (1e6, "$1M"), (2e6, "$2M"), (5e6, "$5M")):
    walls = build_levels(absorbed & (ev[:, 2] >= usd_min))
    f = fires_union(walls); sets[lab] = f
    nv = sum(len(w["radar_runs"]) for w in walls)
    print("-- TRADE walls, absorbed >= %s: %d levels, %d radar visits, %d fires (one-shot batch would give %d)  (%.0f s)" % (lab, len(walls), nv, len(f), len(fires_from(walls)), time.time() - T0), flush=True)
    if usd_min == 1e6:
        ok, tot, mism = verify_gate1(walls, f)
        print("   gate 1 (bar-by-bar replay == union fires on %d sampled bar closes): %d/%d match%s" % (tot, ok, tot, ("  MISMATCH e.g. %s" % mism) if mism else ""), flush=True)
    evaluate("TRADE absorbed >= %s" % lab, f, T1, H1, L1)
walls_r = build_levels(ran & (ev[:, 2] >= 1e6)); f_r = fires_union(walls_r)
print("-- RAN control >= $1M: %d levels, %d fires" % (len(walls_r), len(f_r)), flush=True)
evaluate("RAN control >= $1M", f_r, T1, H1, L1)
# CANONICAL walls detected ONCE over the full history (one-shot batch): NOT what the terminal shows (gate 1), but it
# isolates the wall CONTENT from the union-live repaint noise (transient walls that vanish at the next bar still fire)
walls_c = AL.detect(A, skip_last=False)
for w in walls_c:
    w["radar_runs"] = [(int(r[0]), int(r[1]), float(r[2]) if len(r) > 2 else 50.0) for r in w["radar_runs"]]
f_c = fires_union(walls_c)
print("-- CANONICAL walls detected ONCE (no repaint), replay fire semantics: %d walls, %d fires (union-live cache: %d)" % (len(walls_c), len(f_c), len(canon)), flush=True)
evaluate("CANONICAL no-repaint walls", f_c, T1, H1, L1)
cb2 = {(f[0], f[2]) for f in f_c}
print("   %d of the %d union-live canonical fires come from walls that survive a one-shot detect; the other %d from walls the layer later erased" % (
      sum(1 for f in canon if (f[0], f[2]) in cb2), len(canon), sum(1 for f in canon if (f[0], f[2]) not in cb2)), flush=True)
only_live = [f for f in canon if (f[0], f[2]) not in cb2]
evaluate("CANONICAL transient-wall fires", only_live, T1, H1, L1)
for seed in (3, 4):
    m1 = absorbed & (ev[:, 2] >= 1e6)
    walls_x = build_levels(m1, evs=random_levels(m1, seed)); f_x = fires_union(walls_x)
    print("-- RANDOM-price null (same bars/count as absorbed >= $1M, seed %d): %d levels, %d fires" % (seed, len(walls_x), len(f_x)), flush=True)
    evaluate("RANDOM levels null seed %d" % seed, f_x, T1, H1, L1)
# MERGE: canonical + trade (>= $1M) fires, one per bar (canonical first)
byet = {f[1]: f for f in sets["$1M"]}
for f in canon:
    byet[f[1]] = f
merged = sorted(byet.values())
print("-- MERGE canonical + trade >= $1M: %d fires (%d canonical, %d trade-only)" % (len(merged), len(canon), len(merged) - len(canon)), flush=True)
evaluate("MERGE canonical + trade $1M", merged, T1, H1, L1)
# how much do the two wall sets even coincide? trade fires whose bar/side also fired canonically
cb = {(f[0], f[2]) for f in canon}
same = sum(1 for f in sets["$1M"] if (f[0], f[2]) in cb)
print("\ncoincidence: %d of %d trade($1M) fires are on a bar+side the canonical walls also fired (%.1f%%)" % (same, len(sets["$1M"]), 100 * same / max(1, len(sets["$1M"]))))
print("harness: study/radarrun_30mbkt_tradewalls.py (canonical fires: study/radarrun_30mbkt_live_full.py cache)  DONE in %.0f s" % (time.time() - T0))
