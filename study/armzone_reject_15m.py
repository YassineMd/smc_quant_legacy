"""ARMED-ZONE + REJECTION-TRIGGER honest test (user 2026-09-03) — study/HONEST_TEST_PROMPT.md gates.

PRE-SPECIFIED before any run (no mining; every cell below is reported, none dropped):
  SIGNAL (15m clock, both sides):
    - Legs: EMA20/50 cross on closes (warm 60). Band = last COMPLETED bull HIGH / bear LOW,
      thirds; valid only if hi > lo. All state causal at bar close.
    - ARM short: bull leg's first CLOSE in {expensive, beyond-up}; armed until the leg flips.
      Long mirror (cheap / beyond-dn during a bear leg).
    - TRIGGER at a bar close while armed — ONE trade per leg (first trigger):
        T1 (primary): fav in [0.55, 0.80]   — the closepos study's sweet spot, transferred
        T2 (check):   fav > 0.80            — 'extreme rolls over' falsification cell
      fav(short) = (high-close)/(high-low); fav(long) = (close-low)/(high-low).
    - VARIANTS: BASE, +BIGBODY (trigger bar body strictly > each of last 5 bodies).
  EXITS (both reported): E1 structural: SL = leg extreme so far +/-0.3%, TP = band midpoint;
      E2 fixed: TP 0.75%, SL 2.0%. Both also: leg-flip exit at that bar's close, 32-bar time stop.
  EXECUTION: enter at trigger close (taker). Resolution = 1m FIRST TOUCH starting the bar AFTER
      entry; TP+SL in one 1m bar -> AGAINST the trade. Costs 0.10%/round trip (0.04% fees +
      2x0.03% slip). NON-OVERLAP: one account per cell, opens skipped while a trade is on.
  GATES: eras separate (2025 / 2026H1; daemon sliver lacks 18mo 1m -> excluded, noted);
      month density printed; causal spot-check (truncated-history re-derivation of 50 random
      signals); W/BE/L on NET; too-good alarm; verdict line.
  DIAGNOSTICS (labeled, not filters): VP node percentile at entry (trailing 96-bar profile,
      0.05 bins) vs outcome; big-body within 4 bars AFTER entry (LOOK-AHEAD, descriptive only).
Harness: THIS file (study/armzone_reject_15m.py).
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

COST = 0.10                 # % round trip (fees 0.04 + slip 0.03 x2)
TIME_STOP = 32              # 15m bars
SL_BUF = 0.003              # structural SL buffer beyond the leg extreme
FIX_TP, FIX_SL = 0.75, 2.0  # E2 (%)


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    a = 2.0 / (n + 1.0)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = e + a * (vals[i] - e)
        out[i] = e
    return out


def classify(p, lo, hi):
    if hi <= lo:
        return "inverted"
    if p < lo:
        return "beyond-dn"
    if p > hi:
        return "beyond-up"
    t = (p - lo) / (hi - lo)
    return "cheap" if t < 1.0 / 3.0 else ("equilib" if t < 2.0 / 3.0 else "expensive")


def build_events(bars):
    """Causal walk -> candidate trigger events with full context. Also returns leg list."""
    n = len(bars)
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; T = [0.0] * n
    for i, b in enumerate(bars):
        O[i], C[i], H[i], L[i] = _ohlc(b)
        T[i] = float(b.get("start_time", 0.0) or 0.0)
    e20 = ema(C, 20); e50 = ema(C, 50)
    bodies = [abs(C[i] - O[i]) for i in range(n)]
    events = []
    lb_hi = None; lb_lo = None
    cur = None; st = None
    leg_id = 0

    def leg_close(d, a, b2):
        nonlocal lb_hi, lb_lo
        ext = max(H[a:b2 + 1]) if d > 0 else min(L[a:b2 + 1])
        if d > 0:
            lb_hi = ext
        else:
            lb_lo = ext

    armed = False
    ext_so_far = None
    for i in range(60, n):
        if e20[i] is None or e50[i] is None or e20[i] == e50[i]:
            continue
        d = 1 if e20[i] > e50[i] else -1
        if cur is None:
            cur, st = d, i
            armed = False; ext_so_far = None
        elif d != cur:
            leg_close(cur, st, i - 1)
            leg_id += 1
            cur, st = d, i
            armed = False; ext_so_far = None
        if lb_hi is None or lb_lo is None or lb_hi <= lb_lo:
            continue
        ext_so_far = (max(ext_so_far, H[i]) if cur > 0 else min(ext_so_far, L[i])) \
            if ext_so_far is not None else (H[i] if cur > 0 else L[i])
        z = classify(C[i], lb_lo, lb_hi)
        if not armed:
            if (cur > 0 and z in ("expensive", "beyond-up")) or \
               (cur < 0 and z in ("cheap", "beyond-dn")):
                armed = True
        if not armed:
            continue
        rng = H[i] - L[i]
        if rng <= 0:
            continue
        fav = (H[i] - C[i]) / rng if cur > 0 else (C[i] - L[i]) / rng   # rejection depth vs the leg
        bigbody = bodies[i] > 0 and i >= 5 and all(bodies[i] > bodies[i - j] for j in range(1, 6))
        events.append(dict(i=i, t=T[i], leg=leg_id, side=-cur, entry=C[i], fav=fav,
                           bigbody=bigbody, band_lo=lb_lo, band_hi=lb_hi,
                           mid=(lb_lo + lb_hi) / 2.0, ext=ext_so_far))
    return events, (O, C, H, L, T), e20, e50


def sim_cell(events, arrs, e20, e50, m1_by_min, trig, variant, exit_scheme):
    """Non-overlap account sim for one cell. Returns trades list of dicts."""
    O, C, H, L, T = arrs
    n = len(C)
    sel = []
    seen_leg = set()
    for ev in events:
        if trig == "T1" and not (0.55 <= ev["fav"] <= 0.80):
            continue
        if trig == "T2" and not (ev["fav"] > 0.80):
            continue
        if variant == "bigbody" and not ev["bigbody"]:
            continue
        if ev["leg"] in seen_leg:
            continue
        seen_leg.add(ev["leg"])
        sel.append(ev)
    trades = []
    busy_until = -1
    for ev in sel:
        i = ev["i"]; s = ev["side"]; e = ev["entry"]
        if i <= busy_until:
            trades.append(None)                       # skipped: account busy (counted, not traded)
            continue
        if exit_scheme == "E1":
            sl = ev["ext"] * (1 + SL_BUF) if s < 0 else ev["ext"] * (1 - SL_BUF)
            tp = ev["mid"]
        else:
            sl = e * (1 + FIX_SL / 100) if s < 0 else e * (1 - FIX_SL / 100)
            tp = e * (1 - FIX_TP / 100) if s < 0 else e * (1 + FIX_TP / 100)
        if (s < 0 and not (tp < e < sl)) or (s > 0 and not (sl < e < tp)):
            continue                                  # degenerate bracket (entry beyond mid) -> no trade
        res = None
        for k in range(i + 1, min(i + 1 + TIME_STOP, n)):
            if e20[k] is not None and e50[k] is not None:
                flip = (s < 0 and e20[k] > e50[k]) or (s > 0 and e20[k] < e50[k])
            else:
                flip = False
            mins = m1_by_min.get(int(T[k]))
            hit = None
            if mins:
                for mo, mc, mh, ml in mins:           # 1m FIRST TOUCH; both in one 1m bar -> SL
                    sl_hit = (mh >= sl) if s < 0 else (ml <= sl)
                    tp_hit = (ml <= tp) if s < 0 else (mh >= tp)
                    if sl_hit:
                        hit = ("SL", sl); break
                    if tp_hit:
                        hit = ("TP", tp); break
            else:                                     # missing 1m window -> bar-level, AGAINST the trade
                sl_hit = (H[k] >= sl) if s < 0 else (L[k] <= sl)
                tp_hit = (L[k] <= tp) if s < 0 else (H[k] >= tp)
                if sl_hit:
                    hit = ("SL", sl)
                elif tp_hit:
                    hit = ("TP", tp)
            if hit:
                res = (hit[0], hit[1], k); break
            if not flip and (s < 0 and e20[k] < e50[k]) is False and (s > 0 and e20[k] > e50[k]) is False:
                pass
            if ((s < 0 and e20[k] is not None and e20[k] < e50[k]) or
                    (s > 0 and e20[k] is not None and e20[k] > e50[k])):
                pass                                   # leg still in our favor-side; keep holding
            if e20[k] is not None and ((s < 0 and e20[k] > e50[k]) or (s > 0 and e20[k] < e50[k])):
                res = ("FLIP", C[k], k); break
        if res is None:
            k = min(i + TIME_STOP, n - 1)
            res = ("TIME", C[k], k)
        kind, px, k = res
        gross = (e - px) / e * 100.0 if s < 0 else (px - e) / e * 100.0
        net = gross - COST
        risk = abs(e - sl) / e * 100.0
        trades.append(dict(i=i, t=ev["t"], side=s, kind=kind, net=net,
                           r=net / risk if risk > 0 else 0.0, ev=ev, exit_k=k))
        busy_until = k
    return trades


def report(tag, trades):
    real = [t for t in trades if t]
    skipped = sum(1 for t in trades if t is None)
    if not real:
        print("%-26s n=0 (skipped %d)" % (tag, skipped))
        return None
    W = sum(1 for t in real if t["net"] > 0.02)
    Lo = sum(1 for t in real if t["net"] < -0.02)
    BE = len(real) - W - Lo
    avg = sum(t["net"] for t in real) / len(real)
    avr = sum(t["r"] for t in real) / len(real)
    eq = 0.0; peak = 0.0; dd = 0.0
    for t in real:
        eq += t["r"] * 0.4
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    print("%-26s n=%4d (skip %2d)  W/BE/L %3d/%2d/%3d  win %5.1f%%  avg %+0.4f%%  avgR %+0.3f  maxDD %.1f%% @R0.4"
          % (tag, len(real), skipped, W, BE, Lo, W / len(real) * 100.0, avg, avr, dd))
    return real


def month_density(events):
    mm = defaultdict(int)
    for ev in events:
        d = datetime.fromtimestamp(ev["t"], tz=timezone.utc)
        mm["%04d-%02d" % (d.year, d.month)] += 1
    ks = sorted(mm)
    print("  month density: " + " ".join("%s:%d" % (k[2:], mm[k]) for k in ks))


def causal_spotcheck(bars, events):
    """Gate 5: re-derive 50 random events from TRUNCATED history — must reproduce exactly."""
    random.seed(7)
    smp = random.sample(events, min(50, len(events)))
    ok = 0
    for ev in smp:
        cut = ev["i"] + 1
        ev2, _, _, _ = build_events(bars[:cut])
        match = any(abs(e2["t"] - ev["t"]) < 1 and e2["side"] == ev["side"]
                    and abs(e2["entry"] - ev["entry"]) < 1e-9 and abs(e2["fav"] - ev["fav"]) < 1e-9
                    for e2 in ev2)
        ok += bool(match)
    print("  causal spot-check: %d/%d truncated-history re-derivations identical" % (ok, len(smp)))
    return ok == len(smp)


def vp_diag(real, bars15):
    """POST-HOC DIAGNOSTIC (not a filter): entry price's node percentile in the trailing 96-bar
    volume profile (0.05 bins) vs outcome."""
    from bisect import bisect_left
    buckets = {"HVN(>=P70)": [], "mid": [], "LVN(<=P30)": []}
    for t in real:
        i = t["i"]
        prof = defaultdict(float)
        for b in bars15[max(0, i - 96):i]:
            lv = b.get("levels") or {}
            for pr, node in lv.items():
                try:
                    v = float(node.get("b", 0)) + float(node.get("s", 0))
                    prof[round(float(pr) / 0.05) * 0.05] += v
                except Exception:
                    continue
        if not prof:
            continue
        vols = sorted(prof.values())
        my = prof.get(round(t["ev"]["entry"] / 0.05) * 0.05, 0.0)
        if my <= 0:
            continue
        rank = bisect_left(vols, my) / len(vols)
        key = "HVN(>=P70)" if rank >= 0.70 else ("LVN(<=P30)" if rank <= 0.30 else "mid")
        buckets[key].append(t["net"])
    for k, v in buckets.items():
        if v:
            w = sum(1 for x in v if x > 0.02)
            print("    VP %-11s n=%3d  win %5.1f%%  avg %+0.4f%%" % (k, len(v), w / len(v) * 100, sum(v) / len(v)))


def bigbar_after_diag(real, bars15):
    """LOOK-AHEAD DESCRIPTIVE: big-body bar in TRADE direction within 4 bars AFTER entry vs outcome
    ('does a big bar mean the move is really starting') — diagnostic only, unusable as a filter."""
    O = [_ohlc(b)[0] for b in bars15]; C = [_ohlc(b)[1] for b in bars15]
    bodies = [abs(c - o) for o, c in zip(O, C)]
    with_bb, without = [], []
    for t in real:
        i = t["i"]
        found = False
        for k in range(i + 1, min(i + 5, len(bars15))):
            if bodies[k] > 0 and k >= 5 and all(bodies[k] > bodies[k - j] for j in range(1, 6)):
                if (C[k] - O[k] > 0) == (t["side"] > 0):
                    found = True; break
        (with_bb if found else without).append(t["net"])
    for name, v in (("bigbar-after YES", with_bb), ("bigbar-after NO", without)):
        if v:
            w = sum(1 for x in v if x > 0.02)
            print("    %-16s n=%3d  win %5.1f%%  avg %+0.4f%%" % (name, len(v), w / len(v) * 100, sum(v) / len(v)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _b, raws15, g15 = load_archive("15m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    _b, raws1, g1 = load_archive("1m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    assert not g15 and not g1
    m1_by_min = {}
    for b in raws1:
        o, c, h, l = _ohlc(b)
        st = int(float(b.get("start_time", 0)))
        m1_by_min.setdefault(st - st % 900, []).append((o, c, h, l))
    split_t = 1767225600.0
    eras = (("RECON 2025", [b for b in raws15 if float(b.get("start_time", 0)) < split_t]),
            ("RECON 2026H1", [b for b in raws15 if float(b.get("start_time", 0)) >= split_t]))
    for label, bars in eras:
        events, arrs, e20, e50 = build_events(bars)
        print("\n=== %s ===  bars=%d  candidate trigger-bars=%d" % (label, len(bars), len(events)))
        month_density(events)
        causal_spotcheck(bars, events)
        primary = None
        for trig in ("T1", "T2"):
            for variant in ("base", "bigbody"):
                for ex in ("E1", "E2"):
                    tag = "%s/%s/%s" % (trig, variant, ex)
                    tr = sim_cell(events, arrs, e20, e50, m1_by_min, trig, variant, ex)
                    real = report(tag, tr)
                    if trig == "T1" and variant == "base" and ex == "E1" and real:
                        primary = real
        if primary:
            print("  -- diagnostics on the PRIMARY cell (T1/base/E1) --")
            vp_diag(primary, bars)
            bigbar_after_diag(primary, bars)


if __name__ == "__main__":
    main()
