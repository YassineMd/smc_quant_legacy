"""Step 1 authoritative synthetic gate (rule 0.7): the velocity clock.

History-independent, deterministic. Drives QuantEngine.process_tick with a
hand-computed event-time tick sequence and asserts the EXACT resulting bucket
durations + velocities — proving the new event-time clock and the degenerate-
duration guards behave, without depending on rehydrated (old-math) history.

Run:
    python scripts/test_step1_velocity_clock.py
Exit code 0 = all assertions passed.

The sequence (target_vol = 100; each tick overflows the active bucket, the
remainder seeds the next) is worked out by hand so every closed bucket's
start/end is known exactly:

  tick  event_t   vol   -> closes bucket           dur     note
  T1    1000.0    60        (fills to 60, no close)
  T2    1000.5    60     -> B1 s1000.0 e1000.5      0.5     exact sub-second
  T3    1002.0    90     -> B2 s1000.5 e1002.0      1.5     exact
  T4    1001.0   200     -> B3 s1002.0 e1001.0     -1.0     RECONNECT: E behind
                          -> B4 s1001.0 e1001.0      0.0     zero-duration
  T5    1003.0    95     -> B5 s1001.0 e1003.0      2.0
  T6    1004.0   100     -> B6 s1003.0 e1004.0      1.0
  T7    1005.0   100     -> B7 s1004.0 e1005.0      1.0
  T8    1005.1   100     -> B8 s1005.0 e1005.1      0.1     post-warmup burst

Valid (delta > 0) closes feed rolling_velocity: B1,B2,B5,B6,B7,B8 -> raw vel
= target/dur = [200, 66.67, 50, 100, 100, ~1000]. Degenerate B3 (negative) and
B4 (zero) are skipped entirely. avg_velocity = median([...]) = 100.
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.quant_engine import QuantEngine, _VEL_WARMUP_MIN  # noqa: E402

TARGET = 100.0
TOL = 1e-9

# (event_time_seconds, volume)
SEQUENCE = [
    (1000.0, 60.0),
    (1000.5, 60.0),
    (1002.0, 90.0),
    (1001.0, 200.0),   # reconnect: event time lands behind the previous push
    (1003.0, 95.0),
    (1004.0, 100.0),
    (1005.0, 100.0),
    (1005.1, 100.0),   # fast burst, after the window has warmed up
]

_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    eng = QuantEngine(target_vol=TARGET)

    # Cold-start safety: a live snapshot BEFORE any tick (start_time is None) must
    # not crash and must serialize JSON-safe finite numbers (None-guards).
    pre = eng.active_bucket.live_snapshot(1000.0, eng.avg_velocity)
    check("cold-start live_snapshot does not crash, start_time finite",
          math.isfinite(pre["start_time"]) and math.isfinite(pre["end_time"]),
          f"start={pre['start_time']} end={pre['end_time']}")

    for et, vol in SEQUENCE:
        eng.process_tick(price=150.0, vol=vol, taker_buy=vol * 0.5,
                         delta_oi=0.0, footprints_dict={}, tick_time=et)

    cb = eng.closed_buckets
    print(f"\nclosed buckets: {len(cb)}   rolling_velocity: "
          f"{[round(v, 3) for v in eng.rolling_velocity]}   "
          f"avg_velocity(median): {round(eng.avg_velocity, 4)}\n")

    check("exactly 8 buckets closed", len(cb) == 8, f"got {len(cb)}")
    if len(cb) != 8:
        return _summary()

    durs = [b.end_time - b.start_time for b in cb]
    print("  durations:", [round(d, 6) for d in durs], "\n")

    # --- the headline assertion: exact sub-second durations, NOT floored to 1.0 ---
    check("B1 duration is EXACTLY 0.5 (not floored to 1.0)", approx(durs[0], 0.5),
          f"{durs[0]}")
    check("B2 duration is EXACTLY 1.5 (not floored to 1.0)", approx(durs[1], 1.5),
          f"{durs[1]}")
    check("neither 0.5 nor 1.5 collapsed to 1.0",
          not approx(durs[0], 1.0) and not approx(durs[1], 1.0))
    check("B5/B6/B7 durations exact (2.0/1.0/1.0)",
          approx(durs[4], 2.0) and approx(durs[5], 1.0) and approx(durs[6], 1.0),
          f"{durs[4]},{durs[5]},{durs[6]}")
    check("B8 burst duration ~0.1", approx(durs[7], 0.1, 1e-6), f"{durs[7]}")

    # --- degenerate buckets: reconnect (negative) + zero-duration ---
    check("B3 is the reconnect bucket: end <= start (negative duration)",
          cb[2].end_time <= cb[2].start_time and durs[2] < 0, f"dur={durs[2]}")
    check("B4 is zero-duration: end <= start", cb[3].end_time <= cb[3].start_time
          and approx(durs[3], 0.0), f"dur={durs[3]}")

    # --- degenerate buckets must NOT pollute the velocity baseline (0.6/3) ---
    rv = list(eng.rolling_velocity)
    check("rolling_velocity holds only the 6 VALID closes (degenerate skipped)",
          len(rv) == 6, f"len={len(rv)}")
    check("no velocity sample is negative, zero, NaN or inf",
          all(v > 0 and math.isfinite(v) for v in rv), f"{rv}")
    expected_rv = [TARGET / d for d in (0.5, 1.5, 2.0, 1.0, 1.0, durs[7])]
    check("velocity samples == target/duration in close order",
          len(rv) == 6 and all(approx(a, b, 1e-6) for a, b in zip(rv, expected_rv)),
          f"{[round(v,3) for v in rv]}")

    # --- bursts read HIGH, not low (the legacy floor inverted this) ---
    check("0.5s fill velocity (200) > 1.5s fill velocity (66.7) -- burst reads high",
          rv[0] > rv[1] and approx(rv[0], 200.0) and approx(rv[1], TARGET / 1.5, 1e-6))

    # --- avg_velocity is the outlier-resistant median (0.6/3), finite ---
    check("avg_velocity == median(rolling_velocity) == 100.0",
          math.isfinite(eng.avg_velocity) and approx(eng.avg_velocity, 100.0, 1e-6),
          f"{eng.avg_velocity}")

    # --- warm-up gate (0.6/2): vel_ratio neutral until window fills ---
    vr = [b.vel_ratio for b in cb]
    print("\n  vel_ratios:", [round(v, 4) for v in vr], f"  (_VEL_WARMUP_MIN={_VEL_WARMUP_MIN})\n")
    check("degenerate B3/B4 vel_ratio is neutral 1.0",
          approx(vr[2], 1.0) and approx(vr[3], 1.0))
    check("warm-up buckets B1,B2,B5,B6 vel_ratio neutral 1.0 (window < MIN)",
          all(approx(vr[i], 1.0) for i in (0, 1, 4, 5)), f"{[vr[i] for i in (0,1,4,5)]}")
    # B8 is the first close with a warmed window AND a velocity far from the median.
    exp_b8 = (TARGET / durs[7]) / eng.avg_velocity
    check("post-warmup burst B8 vel_ratio > 1 (reads high) and == vel/median",
          vr[7] > 1.0 and approx(vr[7], exp_b8, 1e-6), f"vr={round(vr[7],4)} exp={round(exp_b8,4)}")
    check("no vel_ratio is negative, NaN or inf",
          all(v >= 0 and math.isfinite(v) for v in vr))

    return _summary()


def _summary() -> int:
    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)} assertion(s)): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
