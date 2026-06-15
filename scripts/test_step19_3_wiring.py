"""Step 19.3 authoritative gate: aggTrade wired into the REAL MarketDataCore.

Drives the actual wired object (constructed with {} + no-op broadcasts, the
test_step2 pattern) by replaying the FROZEN 19.0 tape through the real handlers
(aggTrade -> _process_aggtrade, oi -> _apply_oi). Four assertion blocks:

  [#2] CLOCK COHERENCE - aggTrade candle_open(T) is byte-identical to the kline key
       for the same candle (boundary-adjacent T included).
  [#1] OI FRAMING      - an aggTrade-FIRST node seeds oi_open from live OI, not 0.
  [LATENCY]            - steady (non-close) per-message ingest latency is flat (p99
       under budget, no first-half/second-half drift = O(1) per trade). The close
       class (recalibrate/OB) is REPORTED, not asserted flat -> that is 19.4.
  [INVARIANTS]         - over every bucket the wired path produced: 5-component
       conservation, no vector overflow, no negative durations, vel_ratio varies
       (Phase 1 survived the rewiring).

The tape is replayed K times with a per-pass T offset so time stays MONOTONIC
(no artificial reconnect-seam negative durations) while history grows enough to
expose any O(n) per-trade drift.

Run:  python scripts/test_step19_3_wiring.py     (exit 0 = pass)
"""

from __future__ import annotations

import json
import os
import sys
from time import perf_counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                                  # noqa: E402
from app.aggtrade import candle_open                    # noqa: E402
from app.feeds import MarketDataCore                     # noqa: E402

_FIX = os.path.join(_ROOT, "scripts", "fixtures", "aggtrade_tape.jsonl")
_K = 5                       # tape replays (monotonic T) for stable percentiles + drift
_STEADY_P99_BUDGET_S = 0.01  # 10 ms; the real per-trade work is ~us, huge margin
_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _noop(*_a):
    return None


def _total_closed(core) -> int:
    return sum(len(e.closed_buckets) for e in core.engines.values())


def main() -> int:
    events = []
    with open(_FIX, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            events.append((r.get("recv", 0.0), r))
    events.sort(key=lambda e: e[0])
    trade_T = [int(r["data"]["T"]) for _, r in events if r["type"] == "aggTrade"]
    span_ms = (max(trade_T) - min(trade_T)) + 1000   # per-replay offset keeps T monotonic

    # ===================================================================
    # [#2] CLOCK COHERENCE — candle_open(T) == kline key, for every tape candle
    # ===================================================================
    mism = checked = 0
    for _, r in events:
        if r["type"] != "kline":
            continue
        k = r["data"]["k"]
        B, secs = int(k["t"]), config.TF_SECONDS[k["i"]]
        kline_key = B // 1000          # == int(pd.to_datetime(k["t"],unit="ms").timestamp()) for boundary B
        checked += 1
        if not (candle_open(B, secs) == kline_key
                and candle_open(B + 1, secs) == kline_key                 # +1 ms -> same candle
                and candle_open(B + secs * 1000 - 1, secs) == kline_key   # last ms -> same candle
                and candle_open(B - 1, secs) == kline_key - secs):        # 1 ms before -> previous
            mism += 1
    print(f"\n[#2] clock coherence: checked {checked} kline frames")
    check("aggTrade candle_open(T) byte-identical to the kline key (boundary-safe)",
          checked > 0 and mism == 0, f"mismatches={mism}")

    # ===================================================================
    # [#1] OI FRAMING — aggTrade-FIRST node seeds oi_open from live OI
    # ===================================================================
    core1 = MarketDataCore({}, _noop, _noop)
    core1.pulse_state["oi"] = 12345.0
    core1._process_aggtrade({"e": "aggTrade", "p": "75.10", "q": "3.0", "m": False, "T": 1781545550000})
    uT = str(candle_open(1781545550000, config.TF_SECONDS["1m"]))
    node = core1.footprints_db["1m"][uT]
    print(f"[#1] aggTrade-first 1m node: oi_open={node['oi_open']} levels={node['levels']}")
    check("aggTrade-first node seeds oi_open/oi_close from live OI (not default 0)",
          node["oi_open"] == 12345.0 and node["oi_close"] == 12345.0, f"oi_open={node['oi_open']}")
    check("aggTrade-first node carries the trade at its TRUE price level",
          node["levels"].get("75.10", {}).get("b") == 3.0, f"levels={node['levels']}")

    # ===================================================================
    # Replay the tape K times (monotonic T) through the REAL wired core
    # ===================================================================
    core = MarketDataCore({}, _noop, _noop)
    steady = []   # (order, dt)
    close_dt = []
    order = 0
    for krep in range(_K):
        off = krep * span_ms
        for _, r in events:
            d = r["data"]
            if r["type"] == "aggTrade":
                dd = dict(d)
                dd["T"] = int(d["T"]) + off
                c0 = _total_closed(core)
                t0 = perf_counter()
                core._process_aggtrade(dd)
                dt = perf_counter() - t0
                if _total_closed(core) > c0:
                    close_dt.append(dt)
                else:
                    steady.append((order, dt))
                order += 1
            elif r["type"] == "oi":
                core._apply_oi(float(d["openInterest"]))

    steady_dt = [dt for _, dt in steady]
    half = len(steady) // 2
    p50_a = _pct([dt for _, dt in steady[:half]], 0.50)
    p50_b = _pct([dt for _, dt in steady[half:]], 0.50)
    s_p50, s_p99, s_max = _pct(steady_dt, 0.50), _pct(steady_dt, 0.99), max(steady_dt)
    print(f"\n[LATENCY] steady msgs={len(steady_dt)}  close msgs={len(close_dt)}")
    print(f"    steady  p50={s_p50*1e6:.1f}us  p99={s_p99*1e6:.1f}us  max={s_max*1e6:.1f}us")
    print(f"    drift   1st-half p50={p50_a*1e6:.1f}us  2nd-half p50={p50_b*1e6:.1f}us")
    if close_dt:
        print(f"    close   p50={_pct(close_dt,0.50)*1e6:.1f}us  p99={_pct(close_dt,0.99)*1e6:.1f}us"
              f"  max={max(close_dt)*1e6:.1f}us   <- 19.4 target (recalibrate/OB), NOT asserted here")
    check("steady-ingest p99 under budget (loop does not stall on the per-trade path)",
          s_p99 < _STEADY_P99_BUDGET_S, f"p99={s_p99*1e6:.1f}us budget={_STEADY_P99_BUDGET_S*1e6:.0f}us")
    check("steady-ingest is O(1): no first->second-half drift (no backlog growth)",
          p50_b <= p50_a * 3.0 + 1e-4, f"1st={p50_a*1e6:.1f}us 2nd={p50_b*1e6:.1f}us")
    check("both message classes present (steady + close-bearing)",
          len(steady_dt) > 0 and len(close_dt) > 0, f"steady={len(steady_dt)} close={len(close_dt)}")

    # ===================================================================
    # [INVARIANTS] — Phase 1 survived the rewiring
    # ===================================================================
    nb = cons = ovf = neg = nonfin = 0
    vel_ratios = []
    for e in core.engines.values():
        for b in e.closed_buckets:
            nb += 1
            s = b.opL + b.opS + b.clL + b.clS + b.churn
            if abs(s - b.curr_vol) > 1e-6 * max(1.0, b.curr_vol):
                cons += 1
            if max(b.opL, b.opS, b.clL, b.clS) > b.curr_vol + 1e-6:
                ovf += 1
            if b.start_time is not None and b.end_time is not None and (b.end_time - b.start_time) < -1e-9:
                neg += 1
            if b.vel_ratio != b.vel_ratio or b.vel_ratio in (float("inf"), float("-inf")):
                nonfin += 1
            vel_ratios.append(b.vel_ratio)
    varied = len(set(round(v, 6) for v in vel_ratios)) > 1
    print(f"\n[INVARIANTS] {nb} closed buckets across {len(core.engines)} engines")
    check("buckets were produced by the wired path", nb > 0, f"n={nb}")
    check("5-component conservation opL+opS+clL+clS+churn == curr_vol (all buckets)", cons == 0, f"violations={cons}")
    check("no single vector exceeds curr_vol (all buckets)", ovf == 0, f"violations={ovf}")
    check("no negative durations (event-time clock coherent)", neg == 0, f"violations={neg}")
    check("vel_ratio finite + varies (the velocity clock is live, not pinned)",
          nonfin == 0 and varied, f"nonfinite={nonfin} varied={varied}")

    print("\n" + "=" * 64)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED  (steady-latency flat + Phase-1 invariants hold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
