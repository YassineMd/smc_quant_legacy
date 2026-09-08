# -*- coding: utf-8 -*-
"""Are TRADE-based walls (absorbed BIG-PLAYER prints) the same thing as the current order-flow walls, and do they hold
better?  (user 2026-09-08: "would it be more correct to base the wall on the Trades and big players rather than price
action?")  DESCRIPTIVE, both years, no tradeability verdict.

Data: 15m recon buckets (study/recon_archive) -> the shipped detector's walls + their radar visits (same resolver as
study/wall_aggbase_15m.py); the big-print archive (study/bigprint_archive, prints >= $500K and same-ms sweeps).

ABSORBED big print = a print whose 15m bar CLOSED against the taker: a big BUY with close < print price (the buy was
absorbed -> RESISTANCE at that price), a big SELL with close > price (SUPPORT).  RAN = the bar closed with the taker.
A multi-level SWEEP contributes its END price the same way (where the campaign was stopped).

  1) OVERLAP: share of current walls with an absorbed big print of the matching side within +-0.10% at formation
     (bars i0-2..i0).  Circular-shift null: the prints shifted by k weeks -> what overlap is chance.
  2) HOLD: radar-visit resist rate of walls WITH vs WITHOUT that coincident print (per year, z-test, shift null).
  3) STANDALONE: absorbed-print levels as walls of their own -- same radar geometry (median wall band), same visit /
     resolution rule (body close beyond the far edge = break, beyond the near edge = resist, 24-bar lookahead),
     one live level per side per price (+-0.10%), dies at its first break.  Resist rate vs the walls', per year,
     split by print size and by absorbed vs RAN (does the absorption matter, or just the level?), and for the
     subset NOT within 0.10% of any live wall (the genuinely new levels).
"""
import os, sys, math, random, time
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from app import bigprint_store as BP

LF, DECAY, STR = 24, 0.6, 0.12
TOL = 0.0010                     # +-0.10% price coincidence
T0 = time.time()

print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
ST = np.array([_f(b.get("start_time")) for b in A]); ET = np.array([_f(b.get("end_time")) for b in A])
C = np.array([b["close"] for b in A]); H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])
walls = AL.detect(A)
print("  %d bars %s -> %s, %d walls  (%.0f s)" % (n, datetime.fromtimestamp(ST[0], tz=timezone.utc).date(),
      datetime.fromtimestamp(ET[-1], tz=timezone.utc).date(), len(walls), time.time() - T0), flush=True)


def wall_visits(w, strength_filter=True):
    """[(resist 1/0, year, k0)] for the wall's radar visits (the wall studies' resolver)."""
    P = w["price"]; band = w["band"]; side = w["side"]; rl = P - 3 * band; rh = P + 3 * band
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    out = []
    for j, (k0, k1, pr) in enumerate(w["radar_runs"]):
        if strength_filter and base * (DECAY ** j) < STR:
            continue
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > rh else (1 if C[k] < rl else None)) if side == "R" else \
                (0 if C[k] < rl else (1 if C[k] > rh else None))
            if r is not None:
                if k > k0:
                    out.append((r, int(YR[k0]), k0))
                break
    return out


# ---- big prints -> absorbed / ran levels, mapped to 15m bars --------------------------------------------------------
pr = BP.load_prints_np(float(ST[0]), float(ET[-1]))           # [ts, price, usd, side(1=buy)]
sw = BP.load_sweeps_np(float(ST[0]), float(ET[-1]))           # [ts, p0, p1, usd, side, n]
print("  big prints %d, sweeps %d in range  (archive %s..%s)" % (len(pr), len(sw),
      datetime.fromtimestamp(pr[0, 0], tz=timezone.utc).date() if len(pr) else "-",
      datetime.fromtimestamp(pr[-1, 0], tz=timezone.utc).date() if len(pr) else "-"), flush=True)
# unify: rows [ts, level price, usd, side, is_sweep]
ev = np.concatenate([np.column_stack([pr[:, 0], pr[:, 1], pr[:, 2], pr[:, 3], np.zeros(len(pr))]),
                     np.column_stack([sw[:, 0], sw[:, 2], sw[:, 3], sw[:, 4], np.ones(len(sw))])]) if len(sw) else \
     np.column_stack([pr[:, 0], pr[:, 1], pr[:, 2], pr[:, 3], np.zeros(len(pr))])
ev = ev[np.argsort(ev[:, 0], kind="stable")]
bar = np.searchsorted(ST, ev[:, 0], side="right") - 1                        # the 15m bar holding the event
ok = (bar >= 0) & (bar < n) & (ev[:, 0] <= ET[np.clip(bar, 0, n - 1)])
ev = ev[ok]; bar = bar[ok]
cls = C[bar]
buy = ev[:, 3] > 0
absorbed = (buy & (cls < ev[:, 1])) | (~buy & (cls > ev[:, 1]))              # the bar closed AGAINST the taker
ran = (buy & (cls > ev[:, 1])) | (~buy & (cls < ev[:, 1]))
# a wall SIDE for the level: absorbed BUY -> 'R' (sellers sat there), absorbed SELL -> 'S'
lvl_side = np.where(buy, 0, 1)                                              # 0 = R, 1 = S
print("  events in range %d: absorbed %.1f%%, ran %.1f%%, flat %.1f%%  (%.0f s)" % (
      len(ev), 100 * absorbed.mean(), 100 * ran.mean(), 100 * (~absorbed & ~ran).mean(), time.time() - T0), flush=True)

# per-bar index of events for fast slicing
bar_lo = np.searchsorted(bar, np.arange(n), side="left"); bar_hi = np.searchsorted(bar, np.arange(n), side="right")


def coincident(w, ev_mask, shift_bars=0, any_side=False, lookback=2):
    """Is there an event (ev_mask) of the wall's side within +-TOL of its price in bars i0-lookback..i0 (shifted)?"""
    P = w["price"]; sidecode = 0 if w["side"] == "R" else 1
    b0 = w["i0"] - lookback - shift_bars; b1 = w["i0"] - shift_bars
    if b1 < 0 or b0 >= n:
        return False
    lo = bar_lo[max(0, b0)]; hi = bar_hi[min(n - 1, b1)]
    if hi <= lo:
        return False
    seg = slice(lo, hi)
    m = ev_mask[seg] & (np.abs(ev[seg, 1] - P) <= P * TOL)
    if not any_side:
        m &= (lvl_side[seg] == sidecode)
    return bool(m.any())


def rate(vs):
    return (sum(v[0] for v in vs) / len(vs)) if vs else float("nan")


def ztest(a, b):
    """two-proportion z for resist rates of visit lists a, b"""
    if not a or not b:
        return float("nan")
    p1, p2 = rate(a), rate(b); p = (sum(v[0] for v in a) + sum(v[0] for v in b)) / (len(a) + len(b))
    se = math.sqrt(p * (1 - p) * (1 / len(a) + 1 / len(b))) if 0 < p < 1 else float("nan")
    return (p1 - p2) / se if se and se == se else float("nan")


# ---- 1) + 2) overlap and hold with/without a coincident absorbed print ---------------------------------------------
WV = {id(w): wall_visits(w) for w in walls}
all_v = [v for w in walls for v in WV[id(w)]]
print("\n== 1) OVERLAP: current walls with an ABSORBED big print (matching side) within +-%.2f%% at formation ==" % (100 * TOL))
for usd_min, label in ((0.0, ">= $500K (archive floor)"), (1e6, ">= $1M"), (2e6, ">= $2M")):
    mask = absorbed & (ev[:, 2] >= usd_min)
    has = np.array([coincident(w, mask) for w in walls])
    has_any = np.array([coincident(w, mask, any_side=True) for w in walls])
    # circular-shift null: the print series shifted by whole weeks (the walls stay) -> chance overlap
    nulls = []
    rng = random.Random(7)
    for _ in range(12):
        sh = rng.randint(1, 20) * 7 * 96 * rng.choice((-1, 1))              # k weeks of 15m bars
        nulls.append(np.mean([coincident(w, mask, shift_bars=sh) for w in walls]))
    print("  prints %-24s walls with print %5.1f%%  (any side %5.1f%%)   shift-null %5.1f%% +- %.1f" % (
          label, 100 * has.mean(), 100 * has_any.mean(), 100 * np.mean(nulls), 100 * np.std(nulls)))
    for yr in (2025, 2026):
        wv_yes = [v for w, h in zip(walls, has) if h for v in WV[id(w)] if v[1] == yr]
        wv_no = [v for w, h in zip(walls, has) if not h for v in WV[id(w)] if v[1] == yr]
        print("     %d: resist WITH print %5.1f%% (n=%5d)   WITHOUT %5.1f%% (n=%5d)   gap %+5.1f pp  z=%+.2f" % (
              yr, 100 * rate(wv_yes), len(wv_yes), 100 * rate(wv_no), len(wv_no), 100 * (rate(wv_yes) - rate(wv_no)), ztest(wv_yes, wv_no)))
    if usd_min == 0.0:
        # the same gap under the shift null (is a +x pp gap what chance gives?)
        gaps = []
        for _ in range(12):
            sh = rng.randint(1, 20) * 7 * 96 * rng.choice((-1, 1))
            hs = np.array([coincident(w, mask, shift_bars=sh) for w in walls])
            a = [v for w, h in zip(walls, hs) if h for v in WV[id(w)]]; b = [v for w, h in zip(walls, hs) if not h for v in WV[id(w)]]
            gaps.append(100 * (rate(a) - rate(b)))
        print("     shift-null gap (both yr): mean %+.1f pp, sd %.1f, max %+.1f" % (np.mean(gaps), np.std(gaps), max(gaps)))
    # RAN prints as a control: does a big print that WON also mark a wall?
    maskr = ran & (ev[:, 2] >= usd_min)
    hr = np.array([coincident(w, maskr, any_side=True) for w in walls])
    print("     control: walls with a RAN big print (any side) at formation %5.1f%%" % (100 * hr.mean()))

# ---- 3) standalone absorbed-print levels as walls --------------------------------------------------------------------
band_frac = float(np.median([w["band"] / w["price"] for w in walls]))
print("\n== 3) STANDALONE absorbed-print LEVELS as walls (radar = +-3 x median wall band = +-%.3f%%; visit rule = the walls') ==" % (300 * band_frac))
print("     reference: current walls resist %5.1f%% (n=%d) all visits; 2025 %5.1f%% (n=%d), 2026 %5.1f%% (n=%d)" % (
      100 * rate(all_v), len(all_v), 100 * rate([v for v in all_v if v[1] == 2025]), len([v for v in all_v if v[1] == 2025]),
      100 * rate([v for v in all_v if v[1] == 2026]), len([v for v in all_v if v[1] == 2026])))
# live-wall map per bar for the "not near a wall" subset: walls active at bar k with |P - price| <= TOL
w_i0 = np.array([w["i0"] for w in walls]); w_i1 = np.array([w["i1"] for w in walls]); w_P = np.array([w["price"] for w in walls])
w_side = np.array([0 if w["side"] == "R" else 1 for w in walls])


def near_live_wall(k, P, sidecode):
    m = (w_i0 <= k) & (w_i1 >= k) & (w_side == sidecode) & (np.abs(w_P - P) <= P * TOL)
    return bool(m.any())


def level_visits(k_born, P, sidecode, cap=2000):
    """Radar visits of a level born at bar k_born: after price LEAVES the radar, every re-entry is a visit resolved by
    the walls' rule (first bar > k0 whose close leaves the radar: far side = break (0), near side = resist (1));
    a break kills the level.  Returns ([(resist, year)], broken, capped)."""
    band = P * band_frac; rl = P - 3 * band; rh = P + 3 * band
    out = []; k = k_born + 1; inside = True; end = min(n, k_born + cap)
    while k < end:
        if inside:
            if H[k] < rl or L[k] > rh:
                inside = False
            k += 1; continue
        if H[k] >= rl and L[k] <= rh:                            # re-entry -> a visit starting at k0 = k
            k0 = k; res = None
            for kk in range(k0, min(n, k0 + LF)):
                r = (0 if C[kk] > rh else (1 if C[kk] < rl else None)) if sidecode == 0 else \
                    (0 if C[kk] < rl else (1 if C[kk] > rh else None))
                if r is not None:
                    if kk > k0:
                        res = (r, int(YR[k0]), kk)
                    else:
                        res = ("same", int(YR[k0]), kk)
                    break
            if res is None:                                       # unresolved within LF: still inside -> continue scanning
                k = k0 + LF; continue
            if res[0] == "same":                                  # same-bar resolution (no registered visit, like the walls)
                if (sidecode == 0 and C[res[2]] > rh) or (sidecode == 1 and C[res[2]] < rl):
                    return out, True, False                       # a same-bar body close through = the level breaks
                k = res[2] + 1; inside = True; continue
            out.append((res[0], res[1]))
            if res[0] == 0:
                return out, True, False
            k = res[2] + 1; inside = True; continue
        k += 1
    return out, False, k >= k_born + cap


for usd_min, label in ((1e6, ">= $1M"), (2e6, ">= $2M"), (5e6, ">= $5M")):
    for kind, mask_kind in (("ABSORBED", absorbed), ("RAN (control)", ran)):
        mask = mask_kind & (ev[:, 2] >= usd_min)
        idx = np.nonzero(mask)[0]
        live = {0: [], 1: []}                                     # active levels per side: [price, k_born]
        vis_all = []; vis_new = []; n_lvl = 0; n_dup = 0; n_capped = 0; n_broken = 0
        for e in idx:
            k = int(bar[e]); P = float(ev[e, 1]); sc = int(lvl_side[e])
            if any(abs(P - lp) <= P * TOL and kb <= k for lp, kb, kd in live[sc] if kd > k):
                n_dup += 1; continue                              # a live same-side level already sits there (reinforced)
            vs, broken, capped = level_visits(k, P, sc)
            kdead = (k + 2000) if not broken else (k + 1 + sum(1 for _ in vs) * 0 + 0)   # placeholder, refined below
            # death bar: the bar of the break (approximate by scanning again is costly) -> use the last visit's year only
            live[sc].append((P, k, k + 2000 if not broken else k + 1 + LF * max(1, len(vs))))
            n_lvl += 1; n_capped += int(capped); n_broken += int(broken)
            vis_all.extend(vs)
            if not near_live_wall(k, P, sc):
                vis_new.extend(vs)
        r25 = [v for v in vis_all if v[1] == 2025]; r26 = [v for v in vis_all if v[1] == 2026]
        print("  %-14s %-8s levels %6d (dup %6d, broke %5.1f%%, capped %4.1f%%)  resist ALL %5.1f%% (n=%6d)  2025 %5.1f%% (n=%5d)  2026 %5.1f%% (n=%5d)  NOT-near-a-wall %5.1f%% (n=%6d)  (%.0f s)" % (
              kind, label, n_lvl, n_dup, 100 * n_broken / max(1, n_lvl), 100 * n_capped / max(1, n_lvl),
              100 * rate(vis_all), len(vis_all), 100 * rate(r25), len(r25), 100 * rate(r26), len(r26),
              100 * rate(vis_new), len(vis_new), time.time() - T0), flush=True)
print("\nDONE in %.0f s" % (time.time() - T0))
