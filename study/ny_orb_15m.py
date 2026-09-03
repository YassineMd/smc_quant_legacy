"""NY OPENING-RANGE 15m breakout — HONEST test (user 2026-09-03).

USER RULE: mark the 14h30-14h45 bar's high/low; a later 15m bar CLOSING beyond it sets the bias
(long above / short below), entered at that close. SL at the NY-session extreme (opposite side,
session-so-far at entry), TP = 1:1 RR. No qualifying close before 16h -> skip the day.

PRE-REGISTERED (both time READINGS reported — the user's clock is UTC+1, so "14h30" is ambiguous):
  CELL LOCAL: anchor bar 13:30-13:45 UTC (the NYSE bell bar), cutoff 15:00 UTC.
  CELL UTC:   anchor bar 14:30-14:45 UTC, cutoff 16:00 UTC.
  Common: first qualifying 15m close only, one trade/day. SL = NY-session (13:00Z ->entry)
  extreme on the opposite side, from 1m data, causal. TP = 1:1. Unresolved by the 21:00Z session
  close -> exit at that close (noted; the user rule leaves it open). Costs 0.10% RT; resolution
  on 1m bars, TP+SL in one 1m bar -> AGAINST. Eras separate; W/BE/L on NET; verdict per gate 9.
Harness: THIS file (study/ny_orb_15m.py)."""
import os, sys
from collections import defaultdict
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

COST = 0.10
S0, SEND = 13 * 3600, 21 * 3600


def by_day_1m(raws1):
    d = defaultdict(list)
    for b in raws1:
        st = int(float(b.get("start_time", 0)))
        sod = st % 86400
        if S0 <= sod < SEND:
            o, c, h, l = _ohlc(b)
            d[st - sod].append((st, o, c, h, l))
    return {k: sorted(v) for k, v in d.items() if len(v) >= 400}


def by_day_15m(raws15):
    d = defaultdict(list)
    for b in raws15:
        st = int(float(b.get("start_time", 0)))
        sod = st % 86400
        if S0 <= sod < SEND:
            o, c, h, l = _ohlc(b)
            d[st - sod].append((sod, o, c, h, l))
    return {k: sorted(v) for k, v in d.items()}


def run_cell(d15, d1, anchor_sod, cutoff_sod, tp_mode="rr1"):
    trades = []
    for day, bars in sorted(d15.items()):
        rows1 = d1.get(day)
        if not rows1:
            continue
        anchor = next((b for b in bars if b[0] == anchor_sod), None)
        if anchor is None:
            continue
        _s, _o, _c, ah, al = anchor
        trig = None
        for (sod, o, c, h, l) in bars:
            if sod <= anchor_sod:
                continue
            if sod + 900 > cutoff_sod:                      # bar must CLOSE before the cutoff
                break
            if c > ah:
                trig = (sod + 900, 1, c); break
            if c < al:
                trig = (sod + 900, -1, c); break
        if trig is None:
            continue
        t_entry, side, entry = trig
        pre = [r for r in rows1 if (r[0] % 86400) + 60 <= t_entry]
        if not pre:
            continue
        s_hi = max(r[3] for r in pre); s_lo = min(r[4] for r in pre)
        sl = s_lo if side > 0 else s_hi
        if (side > 0 and sl >= entry) or (side < 0 and sl <= entry):
            continue
        risk = abs(entry - sl)
        if tp_mode == "rr1":
            tp = entry + risk if side > 0 else entry - risk
        else:                                               # fixed % TP (user follow-up: 0.3%)
            tp = entry * (1 + tp_mode / 100.0) if side > 0 else entry * (1 - tp_mode / 100.0)
        net = None
        for (st, o, c, h, l) in rows1:
            if (st % 86400) < (t_entry % 86400):
                continue
            sl_hit = (l <= sl) if side > 0 else (h >= sl)
            tp_hit = (h >= tp) if side > 0 else (l <= tp)
            if sl_hit:                                      # ambiguity -> against
                net = -risk / entry * 100 - COST; break
            if tp_hit:
                net = abs(tp - entry) / entry * 100 - COST; break
        if net is None:
            px = rows1[-1][2]
            net = ((px - entry) / entry if side > 0 else (entry - px) / entry) * 100 - COST
        rk = risk / entry * 100
        trades.append(dict(day=day, side=side, net=net, r=net / rk if rk > 0 else 0.0))
    return trades


def report(tag, trades):
    if not trades:
        print("%-14s n=0" % tag); return
    W = sum(1 for t in trades if t["net"] > 0.02)
    Lo = sum(1 for t in trades if t["net"] < -0.02)
    BE = len(trades) - W - Lo
    avg = sum(t["net"] for t in trades) / len(trades)
    avr = sum(t["r"] for t in trades) / len(trades)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["r"] * 0.4
        peak = max(peak, eq); dd = max(dd, peak - eq)
    nl = sum(1 for t in trades if t["side"] > 0)
    mm = len({datetime.fromtimestamp(t["day"], tz=timezone.utc).month for t in trades})
    print("%-14s n=%4d (L%3d/S%3d)  W/BE/L %3d/%2d/%3d  win %5.1f%%  avg %+0.4f%%  avgR %+0.3f  maxDD %5.1f%% @R0.4  months:%d"
          % (tag, len(trades), nl, len(trades) - nl, W, BE, Lo, W / len(trades) * 100, avg, avr, dd, mm))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _b, raws1, _g = load_archive("1m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    _b, raws15, _g = load_archive("15m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    split_t = 1767225600.0
    d1_all = by_day_1m(raws1)
    d15_all = by_day_15m(raws15)
    cells = (("LOCAL(13:30Z)", 13 * 3600 + 1800, 15 * 3600),
             ("UTC(14:30Z)", 14 * 3600 + 1800, 16 * 3600))
    for label, sel in (("RECON 2025", lambda d: d < split_t), ("RECON 2026H1", lambda d: d >= split_t)):
        d15 = {k: v for k, v in d15_all.items() if sel(k)}
        d1 = {k: v for k, v in d1_all.items() if sel(k)}
        print("\n=== %s ===  sessions=%d" % (label, len(d1)))
        for tag, a_sod, cut in cells:
            report(tag + " RR1:1", run_cell(d15, d1, a_sod, cut))
            report(tag + " TP0.3", run_cell(d15, d1, a_sod, cut, tp_mode=0.3))


if __name__ == "__main__":
    main()
