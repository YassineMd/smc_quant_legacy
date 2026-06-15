"""Step 19.2 authoritative gate: the global OI pending-balance attributor.

Covers the load-bearing semantics (HANDOFF SS8 + operator refinements): one global
balance, per-trade Step-2 clamp, scale-free K*Vw cap-and-hold (Vw floored at a
fraction of the MEDIAN engine target_vol), SEPARATE dropped_oi (cap) vs
resync_discarded_oi (reconnect), and bounded attribution lag.

Two halves (rule 0.7) + the dead-volume floor proof:
  [a] NORMAL   - deterministic replay of the frozen 19.0 tape: the exact accounting
       identity holds (nothing lost), the benign tape never trips the cap, every
       share is clamped to the trade volume, and a flat-OI run attributes zero.
  [b] STRESSOR - a SYNTHETIC sustained-OI-expansion (the quiet tape has none):
       pending stays bounded by the cap, the excess is dropped-and-accounted, the
       lag drains within <= K poll-intervals once OI stops; a DEAD-VOLUME patch can
       NOT collapse the cap below K*FLOOR_FRAC*ref; reconnect discards (separately).

Run:  python scripts/test_step19_2_oi_attributor.py     (exit 0 = pass)
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                                   # noqa: E402
from app.aggtrade import OiAttributor, median_target_vol  # noqa: E402

_FIX = os.path.join(_ROOT, "scripts", "fixtures", "aggtrade_tape.jsonl")
_TV = config.DEFAULT_TARGET_VOL                       # synthetic global target_vol ref
_FLOOR_CAP = config.OI_CAP_K * config.OI_VW_FLOOR_FRAC * _TV   # K * FLOOR_FRAC * ref
_K = config.OI_CAP_K
_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _interval(attr: OiAttributor, vol: float, n_trades: int, doi_after: float,
              ref: float = _TV) -> None:
    """`n_trades` trades summing to `vol`, then a poll injecting `doi_after`."""
    per = vol / n_trades
    for _ in range(n_trades):
        attr.on_trade(per)
    attr.on_poll(attr.last_oi + doi_after, ref)


def main() -> int:
    print(f"\nconstants: K={_K}  VW_EWMA_N={config.OI_VW_EWMA_N}  "
          f"FLOOR_FRAC={config.OI_VW_FLOOR_FRAC}  ref={_TV:.0f}  floor_cap(K*frac*ref)={_FLOOR_CAP:.0f}")

    # the median floor reference (symmetric, no single-engine coupling)
    check("median_target_vol: median across 5 engine target_vols",
          median_target_vol([5000, 3000, 7000, 4000, 6000]) == 5000)
    check("median_target_vol: empty/cold -> DEFAULT_TARGET_VOL",
          median_target_vol([]) == config.DEFAULT_TARGET_VOL)

    # ===================================================================
    # [a] NORMAL — deterministic replay of the frozen tape
    # ===================================================================
    attr = OiAttributor()
    events = []
    with open(_FIX, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            events.append((r.get("recv", 0.0), r))
    events.sort(key=lambda e: e[0])      # process in arrival (recv) order, as the daemon would

    sum_shares = 0.0
    n_tr = n_poll = clamp_viol = 0
    oi_first = oi_last = None
    for _, r in events:
        t, d = r.get("type"), r.get("data", {})
        if t == "aggTrade":
            q = float(d["q"])
            share = attr.on_trade(q)
            sum_shares += share
            if abs(share) > q + 1e-9:
                clamp_viol += 1
            n_tr += 1
        elif t == "oi":
            oi = float(d["openInterest"])
            if oi_first is None:
                oi_first = oi
            oi_last = oi
            attr.on_poll(oi, _TV)
            n_poll += 1

    injected = (oi_last - oi_first) if oi_first is not None else 0.0
    identity = sum_shares + attr.pending_oi + attr.dropped_oi + attr.resync_discarded_oi
    print(f"\n[a] NORMAL (tape): {n_tr} trades / {n_poll} polls   sum_shares={sum_shares:.4f} "
          f"pending={attr.pending_oi:.4f} dropped={attr.dropped_oi:.4f}")
    print(f"    injected (oi_last-oi_first)={injected:.4f}   identity residual={identity - injected:.3e}")
    check("tape shape pinned: 904 trades / 54 polls", n_tr == 904 and n_poll == 54, f"{n_tr}/{n_poll}")
    check("ACCOUNTING IDENTITY: shares+pending+dropped+resync == injected dOI (NOTHING LOST)",
          approx(identity, injected, 1e-5), f"residual={identity - injected:.3e}")
    check("benign tape never trips the cap (dropped_oi == 0)", attr.dropped_oi == 0.0,
          f"dropped={attr.dropped_oi}")
    check("no reconnect on the tape (resync_discarded_oi == 0)", attr.resync_discarded_oi == 0.0)
    check("per-trade clamp held: every |share| <= q (Step-2, per-trade)", clamp_viol == 0,
          f"violations={clamp_viol}")

    flat = OiAttributor()
    flat.on_poll(1_000_000.0, _TV)        # seed baseline
    flat.on_poll(1_000_000.0, _TV)        # dOI = 0 -> pending stays 0
    flat_shares = [flat.on_trade(5.0) for _ in range(20)]
    check("neutral-when-flat: dOI==0 -> every share is exactly 0 (pure churn)",
          all(s == 0.0 for s in flat_shares) and flat.pending_oi == 0.0,
          f"max|share|={max(abs(s) for s in flat_shares)}")

    # ===================================================================
    # [b] STRESSOR — synthetic
    # ===================================================================
    IV = 1000.0          # per-interval volume; > floor (FLOOR_FRAC*ref) so Vw drives the cap

    # ---- (b1) Vw-driven cap: bounded + dropped-accounted + lag <= K ----
    s1 = OiAttributor()
    s1.on_poll(0.0, _TV)                  # seed
    for _ in range(_K + 3):               # warm Vw toward IV at flat OI
        _interval(s1, IV, 10, 0.0)
    cap1 = s1.current_cap(_TV)
    print(f"\n[b1] warmed: vw={s1.vw:.1f}  cap={cap1:.1f}  (floor_cap={_FLOOR_CAP:.0f})")
    check("(b1) Vw warmed to ~per-interval volume", approx(s1.vw, IV, IV * 0.2), f"vw={s1.vw:.1f}")
    check("(b1) Vw drives the cap here (Vw-cap > floor-cap)", cap1 > _FLOOR_CAP, f"cap={cap1:.1f}")

    bounded = True
    for _ in range(20):                   # sustained expansion: +5000/interval (>> cap)
        for _ in range(10):
            s1.on_trade(IV / 10)
        s1.on_poll(s1.last_oi + 5000.0, _TV)
        if abs(s1.pending_oi) > s1.current_cap(_TV) + 1e-6:
            bounded = False
    print(f"[b1] after sustained expansion: pending={s1.pending_oi:.1f} cap={s1.current_cap(_TV):.1f} "
          f"dropped={s1.dropped_oi:.1f}")
    check("(b1) BOUNDED: |pending| <= cap after every poll through sustained expansion", bounded,
          f"pending={s1.pending_oi:.1f} cap={s1.current_cap(_TV):.1f}")
    check("(b1) DROPPED accounted: dropped_oi > 0 in a past-cap burst", s1.dropped_oi > 0,
          f"dropped={s1.dropped_oi:.1f}")

    drain = []                            # stop expansion -> drain at flat OI
    for _ in range(_K):
        for _ in range(10):
            s1.on_trade(IV / 10)
        s1.on_poll(s1.last_oi + 0.0, _TV)
        drain.append(s1.pending_oi)
    print(f"[b1] drain (flat OI) pending per interval: {[round(x, 1) for x in drain]}")
    check("(b1) LAG <= K: pending drains to ~0 within K flat-OI intervals", approx(drain[-1], 0.0, 1.0),
          f"after K={_K} intervals pending={drain[-1]:.3f}")
    check("(b1) lag is REAL (not instant): pending still > 0 after K-1 intervals",
          drain[_K - 2] > 1.0 if _K >= 2 else True, f"after K-1 pending={drain[_K - 2]:.1f}")

    # ---- (b2) DEAD-VOLUME FLOOR: cap can't collapse to 0 ----
    s2 = OiAttributor()
    s2.on_poll(0.0, _TV)
    for _ in range(_K + 3):               # warm Vw toward IV
        _interval(s2, IV, 10, 0.0)
    floor_held = True
    for _ in range(40):                   # long dead patch: ZERO volume between polls -> Vw -> 0
        s2.on_poll(s2.last_oi + 0.0, _TV)
        if s2.current_cap(_TV) < _FLOOR_CAP - 1e-9:
            floor_held = False
    print(f"\n[b2] dead patch: vw={s2.vw:.3f}  cap={s2.current_cap(_TV):.1f}  floor_cap={_FLOOR_CAP:.0f}")
    check("(b2) Vw decayed toward 0 in the dead patch", s2.vw < IV * 0.1, f"vw={s2.vw:.3f}")
    check("(b2) DEAD-VOLUME FLOOR: cap stayed >= K*FLOOR_FRAC*ref through the quiet patch",
          floor_held and approx(s2.current_cap(_TV), _FLOOR_CAP, 1.0),
          f"cap={s2.current_cap(_TV):.1f} floor_cap={_FLOOR_CAP:.1f}")
    dropped_before = s2.dropped_oi
    legit = _FLOOR_CAP * 0.5              # a legit balance UNDER the floor-cap
    s2.on_poll(s2.last_oi + legit, _TV)
    check("(b2) legit balance under the floor-cap SURVIVES (not silently nuked when vol dries up)",
          approx(s2.pending_oi, legit, 1e-6) and s2.dropped_oi == dropped_before,
          f"pending={s2.pending_oi:.1f} legit={legit:.1f}")

    # ---- reconnect mini-case (separate counter) ----
    rc = OiAttributor()
    rc.on_poll(100.0, _TV)
    rc.on_poll(100.0 + 300.0, _TV)        # +300 dOI, no trades -> pending=300 (under floor-cap)
    pend_before = rc.pending_oi
    print(f"\n[reconnect] pending before={pend_before:.1f}")
    check("reconnect setup: pending built up", pend_before > 0, f"pending={pend_before:.1f}")
    rc.on_reconnect(99999.0)
    check("reconnect: pending_oi reset to 0", rc.pending_oi == 0.0)
    check("reconnect: last_oi resynced to current (gap not attributed)", rc.last_oi == 99999.0)
    check("reconnect: discard surfaced in resync_discarded_oi, SEPARATE from dropped_oi",
          approx(rc.resync_discarded_oi, pend_before, 1e-9) and rc.dropped_oi == 0.0,
          f"resync={rc.resync_discarded_oi:.1f} dropped={rc.dropped_oi:.1f}")
    post = [rc.on_trade(10.0) for _ in range(5)]
    check("reconnect: gap OI NOT dumped on resumed trades (shares 0 until OI moves)",
          all(s == 0.0 for s in post))

    print("\n" + "=" * 64)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED  (both gate halves green)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
