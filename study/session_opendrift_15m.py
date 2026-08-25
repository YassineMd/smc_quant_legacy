"""SESSION OPEN-DRIFT -> BIAS — 15m bucket (user 2026-08-25: "does the distance from current price to the
session open after 1-2h help determine the bias of the day?").

PRE-REGISTERED (frozen; descriptive; the shuffle null is ESSENTIAL here — drift-at-checkpoint is PART of the
day's total drift, so it 'predicts' the close mechanically even in shuffled data; only real-minus-null counts):
  Sessions Tokyo 00-08 / London 08-13 / NY 13-21 UTC; checkpoints 1h and 2h after the session open.
  SIGNAL d = (close@checkpoint - session open)/session open; direction = sign(d); CLEAR = |d| >= 0.5% (fixed).
  OUTCOMES (causal, from the CHECKPOINT price, not the open):
    hit%      = session final close on the same side of the session open as d
    contS%    = sign(d) * (session close - close@cp)/close@cp * 100   (fee wall ~0.07%/RT for context)
    contD%    = sign(d) * (UTC DAY close - close@cp)/close@cp * 100   ("bias of the day")
  NULL = 20 within-session candle-order shuffles (same total drift, randomized path), identical evaluation.
  Splits: ALL / CLEAR (|d|>=0.5%) / UP / DOWN x era (recon 2025 / 2026 / daemon).
python study/session_opendrift_15m.py"""
import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.session_range_fix_15m import SESSIONS, _f, load, instances
from study.session_side_fix_15m import shuffle_rows

N_SHUF = 20
CLEAR = 0.005


def day_closes(A):
    dc = {}
    for b in A:
        st = _f(b, "start_time")
        dc[datetime.fromtimestamp(st, timezone.utc).date()] = _f(b, "close", "close_price")
    return dc


def drift_eval(rows, cp_frac, day_close):
    """None or dict(d, sign, cp_px, contS, contD, hit)."""
    op = rows[0][2]
    cp = None
    for (t0, t1, o, h, l, c) in rows:
        if t1 <= cp_frac + 1e-9:
            cp = c
    if cp is None or op <= 0 or cp <= 0:
        return None
    d = (cp - op) / op
    if d == 0.0:
        return None
    s = 1 if d > 0 else -1
    fin = rows[-1][5]
    out = {"d": d, "s": s, "hit": (fin > op) if s > 0 else (fin < op),
           "contS": s * (fin - cp) / cp * 100.0}
    out["contD"] = s * (day_close - cp) / cp * 100.0 if day_close else None
    return out


def stats(evs, key):
    v = [e[key] for e in evs if e.get(key) is not None]
    if not v:
        return float("nan")
    return (100.0 * np.mean(v)) if key == "hit" else float(np.mean(v))


def report(insts, dcloses):
    rng = random.Random(41)
    by = {}
    for (d, name), rows in insts.items():
        by.setdefault(name, []).append((d, rows))
    for name, _h0, _h1 in SESSIONS:
        lst = by.get(name, [])
        if len(lst) < 20:
            print("  %-7s n=%d — too few" % (name, len(lst)), flush=True)
            continue
        span_h = _h1 - _h0
        shuf = [[shuffle_rows(r, rng) for _ in range(N_SHUF)] for _, r in lst]
        for cp_h in (1.0, 2.0):
            cp_frac = cp_h / span_h
            evs = [drift_eval(r, cp_frac, dcloses.get(d)) for d, r in lst]
            sh = []
            for (d, _), group in zip(lst, shuf):
                for srows in group:
                    sh.append(drift_eval(srows, cp_frac, dcloses.get(d)))
            for label, keep in (("ALL  ", lambda e: True), ("CLEAR", lambda e: abs(e["d"]) >= CLEAR),
                                ("UP   ", lambda e: e["s"] > 0), ("DOWN ", lambda e: e["s"] < 0)):
                r_ = [e for e in evs if e is not None and keep(e)]
                s_ = [e for e in sh if e is not None and keep(e)]
                if len(r_) < 15:
                    continue
                print("  %-7s +%dh %s n=%3d | hit %4.1f%% (null %4.1f) | contS %+.3f%% (null %+.3f) | contD %+.3f%% (null %+.3f)"
                      % (name, int(cp_h), label, len(r_),
                         stats(r_, "hit"), stats(s_, "hit"),
                         stats(r_, "contS"), stats(s_, "contS"),
                         stats(r_, "contD"), stats(s_, "contD")), flush=True)


def main():
    print("SESSION OPEN-DRIFT -> BIAS — 15m bucket | drift@1h/2h from session open | shuffle null | pre-registered\n", flush=True)
    t0 = time.time()
    A = load("recon")
    dc_all = day_closes(A)
    for era, lo, hi in (("RECON 2025", "2025-01-01", "2026-01-01"), ("RECON 2026", "2026-01-01", "2026-06-20")):
        loT = datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp()
        hiT = datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp()
        sub = [b for b in A if loT <= _f(b, "start_time") < hiT]
        print("=" * 118, flush=True)
        print(era, flush=True)
        report(instances(sub), dc_all)
    del A
    print("=" * 118, flush=True)
    print("DAEMON (2026-06-20 ..)", flush=True)
    Ad = load("daemon")
    report(instances(Ad), day_closes(Ad))
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
