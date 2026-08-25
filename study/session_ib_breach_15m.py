"""SESSION INITIAL-BALANCE BREACH — 15m bucket, AMT hypothesis (user 2026-08-25: "at the beginning of a session
buy and sell areas get defined; a breach of an area can determine the session bias/direction").

PRE-REGISTERED (frozen; descriptive reliability — tradeability would additionally need the honest gates):
  Sessions: Tokyo 00-08 / London 08-13 / NY 13-21 UTC; instances + coverage guard from session_range_fix_15m.
  IB = the session's first 60 MINUTES (primary) and first 30 MINUTES (secondary); IB hi/lo from bucket H/L of
       buckets ENDING inside the IB window (>= 2 buckets required, else instance skipped).
  BREACH = the FIRST bucket CLOSE beyond an IB edge after the IB window (close, not wick).
  OUTCOMES (causal from the breach bucket's close):
    sustained% = session final close beyond the breached edge (classic range-extension holds)
    cont%      = signed (final close - breach close)/breach close in the breach direction (gross %, fee wall
                 ~0.07%/RT for context)
    dbl%       = the OTHER edge also breaks (close) later in the session
    breach timing = median elapsed fraction of the first breach
  NULL = 20 within-session candle-order shuffles per instance, SAME IB+breach evaluation (kills geometry/drift).
  Split: ALL / UP-breach / DOWN-breach x era (recon 2025 / recon 2026 / daemon).
python study/session_ib_breach_15m.py"""
import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.session_range_fix_15m import SESSIONS, _f, load, instances
from study.session_side_fix_15m import shuffle_rows

N_SHUF = 20
IBS = ((60, "IB60"), (30, "IB30"))


def ib_breach(rows, ib_frac):
    """None or dict(side ±1, t_breach, px_breach, final, sustained, dbl)."""
    ib_hi = -1e18; ib_lo = 1e18; nib = 0
    for (t0, t1, o, h, l, c) in rows:
        if t1 <= ib_frac + 1e-9:
            ib_hi = max(ib_hi, h); ib_lo = min(ib_lo, l); nib += 1
    if nib < 2 or ib_hi <= ib_lo:
        return None
    side = 0; t_b = None; px_b = None
    for (t0, t1, o, h, l, c) in rows:
        if t1 <= ib_frac + 1e-9:
            continue
        if c > ib_hi:
            side = 1; t_b = t1; px_b = c; break
        if c < ib_lo:
            side = -1; t_b = t1; px_b = c; break
    if side == 0:
        return {"side": 0}
    dbl = False
    for (t0, t1, o, h, l, c) in rows:
        if t_b is not None and t1 > t_b:
            if (side > 0 and c < ib_lo) or (side < 0 and c > ib_hi):
                dbl = True; break
    final = rows[-1][5]
    sustained = (final > ib_hi) if side > 0 else (final < ib_lo)
    cont = side * (final - px_b) / px_b * 100.0
    return {"side": side, "t": t_b, "px": px_b, "final": final, "sustained": sustained,
            "dbl": dbl, "cont": cont}


def stats(evs):
    evs = [e for e in evs if e is not None and e["side"] != 0]
    if not evs:
        return None
    sus = 100 * np.mean([e["sustained"] for e in evs])
    cont = np.mean([e["cont"] for e in evs])
    dbl = 100 * np.mean([e["dbl"] for e in evs])
    tmed = np.median([e["t"] for e in evs])
    return len(evs), sus, cont, dbl, tmed


def report(insts):
    rng = random.Random(31)
    by = {}
    for (d, name), rows in insts.items():
        by.setdefault(name, []).append(rows)
    for name, _h0, _h1 in SESSIONS:
        lst = by.get(name, [])
        if len(lst) < 20:
            print("  %-7s n=%d — too few" % (name, len(lst)), flush=True)
            continue
        span_h = _h1 - _h0
        shuf = [[shuffle_rows(r, rng) for _ in range(N_SHUF)] for r in lst]
        for ib_min, ib_name in IBS:
            ib_frac = (ib_min / 60.0) / span_h
            evs = [ib_breach(r, ib_frac) for r in lst]
            n_nob = sum(1 for e in evs if e is not None and e["side"] == 0)
            sh_evs = []
            for group in shuf:
                sh_evs += [ib_breach(s, ib_frac) for s in group]
            for label, keep in (("ALL ", lambda e: True), ("UP  ", lambda e: e["side"] > 0),
                                ("DOWN", lambda e: e["side"] < 0)):
                r_ = stats([e for e in evs if e is not None and e["side"] != 0 and keep(e)])
                s_ = stats([e for e in sh_evs if e is not None and e["side"] != 0 and keep(e)])
                if r_ is None:
                    continue
                n, sus, cont, dbl, tmed = r_
                if s_ is not None:
                    _, ssus, scont, sdbl, _ = s_
                else:
                    ssus = scont = sdbl = float("nan")
                extra = " | no-breach %d" % n_nob if label == "ALL " else ""
                print("  %-7s %s %s n=%3d | sustained %4.1f%% (null %4.1f) | cont %+.3f%% (null %+.3f) | dbl %4.1f%% (null %4.1f) | med breach t=%.2f%s"
                      % (name, ib_name, label, n, sus, ssus, cont, scont, dbl, sdbl, tmed, extra), flush=True)


def main():
    print("SESSION IB BREACH — 15m bucket | IB=first 60/30min | close-breach -> session direction | shuffle null | pre-registered\n", flush=True)
    t0 = time.time()
    A = load("recon")
    for era, lo, hi in (("RECON 2025", "2025-01-01", "2026-01-01"), ("RECON 2026", "2026-01-01", "2026-06-20")):
        loT = datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp()
        hiT = datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp()
        sub = [b for b in A if loT <= _f(b, "start_time") < hiT]
        print("=" * 118, flush=True)
        print(era, flush=True)
        report(instances(sub))
    del A
    print("=" * 118, flush=True)
    print("DAEMON (2026-06-20 ..)", flush=True)
    report(instances(load("daemon")))
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
