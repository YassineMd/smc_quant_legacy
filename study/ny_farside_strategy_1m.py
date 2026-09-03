"""NY FAR-SIDE HOLD -> STRATEGY honest test (user 2026-09-03: "find a proper strategy").

Converts the project's strongest untested null-beating effect (study/session_side_fix_15m: at
15:00-15:25Z the NY-session extreme FARTHER from price holds to the close 61-73%, +6..+17pp over
the shuffle null in ALL THREE eras) into pre-specified trades. HONEST_TEST_PROMPT gates.

PRE-SPECIFIED (all cells reported; checkpoints/tolerance taken from the PRIOR study, not tuned):
  Session: NY 13:00-21:00 UTC on 1m clock candles. Checkpoints f=0.25 (15:00Z) and f=0.30
  (15:24Z). At cp (1m closes <= cp only): Hs/Ls = session extremes so far, P = close, mid,
  R = Hs-Ls, tol = 0.10*R (the study's own hold tolerance). far = LOW if P >= mid else HIGH.
  CELL FADE : limit AT the far extreme after cp (LONG at Ls if far=LOW / SHORT at Hs if far=HIGH).
              SL = tol beyond the extreme; TP = checkpoint mid. No fill by 21:00Z -> no trade.
              Fill bar may hit SL (against) but never TP (no same-bar TP credit).
  CELL DRIFT: market at cp close, direction AWAY from the far extreme (LONG if far=LOW).
              SL = tol beyond the far extreme; TP = the NEAR extreme. ⚠ family warning noted:
              this is the 'small TP + session-extreme SL' shape memory calls structurally losing —
              included because it is the other faithful expression, reported either way.
  CELL HOLD : third/final expression (added after FADE/DRIFT printed, reported regardless):
              DRIFT entry + disaster stop only (tol beyond the far extreme), NO TP — exit at the
              21:00Z close. Tests whether letting winners run fixes DRIFT's truncation geometry.
  Both: unresolved -> exit at the 21:00Z close. One trade/session/cell. Costs 0.10% RT
  (all-taker, conservative for the limit leg). 1m bars native; SL+TP in one 1m bar -> AGAINST.
  Eras: recon 2025 / recon 2026H1 (daemon 1m history not retained -> noted, not silently skipped).
  Causal: cp state uses only bars closing <= cp. Verdict line per gate 9.
Harness: THIS file (study/ny_farside_strategy_1m.py).
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

COST = 0.10
S0, S1 = 13 * 3600, 21 * 3600          # NY session seconds-of-day (UTC)
CPS = (0.25, 0.30)
TOL = 0.10


def sessions(raws1):
    by_day = defaultdict(list)
    for b in raws1:
        st = int(float(b.get("start_time", 0)))
        sod = st % 86400
        if S0 <= sod < S1:
            o, c, h, l = _ohlc(b)
            by_day[st - sod].append((st, o, c, h, l))
    return {d: sorted(v) for d, v in by_day.items() if len(v) >= 400}


def run_cell(sess, f, cell):
    cp_off = S0 + f * (S1 - S0)
    trades = []
    for day, rows in sorted(sess.items()):
        pre = [r for r in rows if (r[0] % 86400) + 60 <= cp_off]
        post = [r for r in rows if (r[0] % 86400) + 60 > cp_off]
        if len(pre) < 30 or len(post) < 30:
            continue
        Hs = max(r[3] for r in pre); Ls = min(r[4] for r in pre); P = pre[-1][2]
        R = Hs - Ls
        if R <= 0:
            continue
        mid = (Hs + Ls) / 2.0
        far_low = P >= mid
        tol = TOL * R
        if cell == "FADE":
            if far_low:
                side, entry, sl, tp = 1, Ls, Ls - tol, mid
            else:
                side, entry, sl, tp = -1, Hs, Hs + tol, mid
            filled = False
            net = None
            for (st, o, c, h, l) in post:
                if not filled:
                    touched = (l <= entry) if side > 0 else (h >= entry)
                    if touched:
                        filled = True
                        # fill bar: SL may hit (against); TP never credited on the fill bar
                        if (side > 0 and l <= sl) or (side < 0 and h >= sl):
                            net = (-(entry - sl) / entry if side > 0 else -(sl - entry) / entry) * 100 - COST
                            break
                    continue
                sl_hit = (l <= sl) if side > 0 else (h >= sl)
                tp_hit = (h >= tp) if side > 0 else (l <= tp)
                if sl_hit:                                   # ambiguity -> against
                    net = (-(entry - sl) / entry if side > 0 else -(sl - entry) / entry) * 100 - COST
                    break
                if tp_hit:
                    net = ((tp - entry) / entry if side > 0 else (entry - tp) / entry) * 100 - COST
                    break
            if not filled:
                continue
            if net is None:
                px = post[-1][2]
                net = ((px - entry) / entry if side > 0 else (entry - px) / entry) * 100 - COST
        elif cell == "HOLD":
            side = 1 if far_low else -1
            entry = P
            sl = (Ls - tol) if far_low else (Hs + tol)
            if (side > 0 and sl >= entry) or (side < 0 and sl <= entry):
                continue
            net = None
            for (st, o, c, h, l) in post:
                if (side > 0 and l <= sl) or (side < 0 and h >= sl):
                    net = (-(abs(entry - sl)) / entry) * 100 - COST
                    break
            if net is None:
                px = post[-1][2]
                net = ((px - entry) / entry if side > 0 else (entry - px) / entry) * 100 - COST
        else:                                               # DRIFT
            side = 1 if far_low else -1
            entry = P
            sl = (Ls - tol) if far_low else (Hs + tol)
            tp = Hs if far_low else Ls
            if (side > 0 and not (sl < entry < tp)) or (side < 0 and not (tp < entry < sl)):
                continue
            net = None
            for (st, o, c, h, l) in post:
                sl_hit = (l <= sl) if side > 0 else (h >= sl)
                tp_hit = (h >= tp) if side > 0 else (l <= tp)
                if sl_hit:
                    net = (-(abs(entry - sl)) / entry) * 100 - COST
                    break
                if tp_hit:
                    net = ((abs(tp - entry)) / entry) * 100 - COST
                    break
            if net is None:
                px = post[-1][2]
                net = ((px - entry) / entry if side > 0 else (entry - px) / entry) * 100 - COST
        risk = abs(entry - sl) / entry * 100
        trades.append(dict(day=day, side=side, net=net, r=net / risk if risk > 0 else 0.0))
    return trades


def report(tag, trades):
    if not trades:
        print("%-22s n=0" % tag)
        return
    W = sum(1 for t in trades if t["net"] > 0.02)
    Lo = sum(1 for t in trades if t["net"] < -0.02)
    BE = len(trades) - W - Lo
    avg = sum(t["net"] for t in trades) / len(trades)
    avr = sum(t["r"] for t in trades) / len(trades)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["r"] * 0.4
        peak = max(peak, eq); dd = max(dd, peak - eq)
    mm = defaultdict(int)
    for t in trades:
        d = datetime.fromtimestamp(t["day"], tz=timezone.utc)
        mm["%02d" % d.month] += 1
    print("%-22s n=%4d  W/BE/L %3d/%2d/%3d  win %5.1f%%  avg %+0.4f%%  avgR %+0.3f  maxDD %5.1f%% @R0.4  months:%d"
          % (tag, len(trades), W, BE, Lo, W / len(trades) * 100, avg, avr, dd, len(mm)))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _b, raws1, g1 = load_archive("1m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    assert not g1
    split_t = 1767225600.0
    for label, sel in (("RECON 2025", lambda d: d < split_t),
                       ("RECON 2026H1", lambda d: d >= split_t)):
        sess = {d: v for d, v in sessions(raws1).items() if sel(d)}
        print("\n=== %s ===  NY sessions=%d" % (label, len(sess)))
        for f in CPS:
            for cell in ("FADE", "DRIFT", "HOLD"):
                report("cp%.2f/%s" % (f, cell), run_cell(sess, f, cell))
    print("\n(daemon era: 1m history not retained beyond ~3d -> no honest 1m sim possible; "
          "forward validation must be LIVE)")


if __name__ == "__main__":
    main()
