"""Step 19.4 authoritative gate: recalibrate / OB rescan moved off the close hot path.

Drives the REAL wired MarketDataCore over the frozen 19.0 tape (T-offset, monotonic
multi-pass = clustered closes). The 19.3 gate measured the per-close work at ~1.8ms;
profiling pinned it to recalibrate (the 20-step optimizer over the 2h window, ~541us)
+ the O(buckets^2) OB rescan (~80us, growing) — both of which used to run on EVERY
close. 19.4 moves them to a periodic cadence.

NOTE on the gate's close cost: all 5 engines share target_vol here, so they fill in
lockstep and close on the SAME trade (5 closes/message). In production recalibrate
gives each tf a different target_vol, desyncing them. So we report the PER-ENGINE
close cost; the message-level figure is 5x that by the synchronization artifact.

Blocks:
  [LATENCY]   - per-engine close cost is now serialization only (~tens of us) and FLAT
       as history grows (no O(history) work left on the close path).
  [AVOIDED]   - quantifies what 19.4 moved off: recalibrate + OB rescan, measured now,
       dwarf the steady per-trade cost, and the OB rescan GROWS with history (would
       balloon on a long-running daemon if left per-close).
  [ADAPT]     - closes alone do NOT change target_vol; the periodic _recompute_engine
       DOES (recalibration still works), and it is synchronous = the consistency basis.
  [INVARIANTS]- Phase-1 still holds, incl. buckets closed AFTER a mid-stream recompute.

Run:  python scripts/test_step19_4_recompute.py     (exit 0 = pass)
"""

from __future__ import annotations

import inspect
import json
import os
import statistics
import sys
from time import perf_counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                                         # noqa: E402
from app.feeds import MarketDataCore                            # noqa: E402
from app.quant_engine import calc_quant_obs                     # noqa: E402

_FIX = os.path.join(_ROOT, "scripts", "fixtures", "aggtrade_tape.jsonl")
_K = 5
_RECOMPUTE_AT = 2
_DEFAULT = config.DEFAULT_TARGET_VOL
_PER_ENGINE_CLOSE_BUDGET_S = 0.0005    # 500 us/engine sanity bound: serialize-only, no recalibrate
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
    span_ms = (max(trade_T) - min(trade_T)) + 1000

    print(f"\nconstants: RECOMPUTE_SECS={config.RECOMPUTE_SECS}  per-engine-close budget={_PER_ENGINE_CLOSE_BUDGET_S*1e6:.0f}us")
    check("_recompute_engine is SYNCHRONOUS (no await) -> atomic w.r.t. closes (consistency basis)",
          not inspect.iscoroutinefunction(MarketDataCore._recompute_engine))

    core = MarketDataCore({}, _noop, _noop)
    steady, close = [], []          # close: (order, per_engine_dt)
    order = 0
    tv_pre = tv_post = None
    for krep in range(_K):
        if krep == _RECOMPUTE_AT:
            tv_pre = [core.engines[tf].target_vol for tf in config.TIMEFRAMES]
            for tf in config.TIMEFRAMES:
                core._recompute_engine(tf)
            tv_post = [core.engines[tf].target_vol for tf in config.TIMEFRAMES]
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
                n_closed = _total_closed(core) - c0
                if n_closed > 0:
                    close.append((order, dt / n_closed))   # normalize: per-engine close cost
                else:
                    steady.append(dt)
                order += 1
            elif r["type"] == "oi":
                core._apply_oi(float(d["openInterest"]))

    close_dt = [dt for _, dt in close]
    half = len(close) // 2
    c_first = _pct([dt for _, dt in close[:half]], 0.50)
    c_second = _pct([dt for _, dt in close[half:]], 0.50)
    s_p50 = _pct(steady, 0.50)
    c_p50, c_p99, c_max = _pct(close_dt, 0.50), _pct(close_dt, 0.99), (max(close_dt) if close_dt else 0.0)
    print(f"\n[LATENCY] steady msgs={len(steady)} (p50={s_p50*1e6:.1f}us)  close msgs={len(close_dt)}")
    print(f"    per-engine close: p50={c_p50*1e6:.1f}us  p99={c_p99*1e6:.1f}us  max={c_max*1e6:.1f}us  (serialize-only now)")
    print(f"    flat as history grows: 1st-half p50={c_first*1e6:.1f}us  2nd-half p50={c_second*1e6:.1f}us")
    check("per-engine close is FLAT as history grows (no O(history) work on the close path)",
          c_second <= c_first * 1.6 + 3e-5, f"1st={c_first*1e6:.1f}us 2nd={c_second*1e6:.1f}us")
    check("per-engine close p99 bounded (serialize-only, no recalibrate/OB)",
          c_p99 < _PER_ENGINE_CLOSE_BUDGET_S, f"p99={c_p99*1e6:.1f}us budget={_PER_ENGINE_CLOSE_BUDGET_S*1e6:.0f}us")

    # ---- AVOIDED: what 19.4 moved to recompute_loop (was per close in 19.3) ----
    tf_big = max(config.TIMEFRAMES, key=lambda t: len(core.engines[t].closed_buckets))
    eb = core.engines[tf_big]
    nbk = len(eb.closed_buckets)
    t = perf_counter()
    for _ in range(20):
        eb.recalibrate(eb.last_tick_time, core.footprints_db.get(tf_big, {}))
    t_recal = (perf_counter() - t) / 20 * 1e6
    saved = eb.closed_buckets
    eb.closed_buckets = saved[: len(saved) // 2]
    t = perf_counter()
    for _ in range(20):
        calc_quant_obs(eb, tf_big)
    t_obs_half = (perf_counter() - t) / 20 * 1e6
    eb.closed_buckets = saved
    t = perf_counter()
    for _ in range(20):
        calc_quant_obs(eb, tf_big)
    t_obs_full = (perf_counter() - t) / 20 * 1e6
    avoided = t_recal + t_obs_full
    print(f"\n[AVOIDED] per close in 19.3 (now on the {config.RECOMPUTE_SECS}s recompute_loop):")
    print(f"    recalibrate={t_recal:.0f}us  +  OB-rescan={t_obs_full:.0f}us  =  {avoided:.0f}us/engine   (steady/trade={s_p50*1e6:.1f}us)")
    print(f"    OB rescan GROWS O(buckets^2): half-history={t_obs_half:.0f}us  ->  full({nbk} buckets)={t_obs_full:.0f}us")
    print(f"    (small at this fixture's scale; on a long-running daemon toward the {config.CLOSED_BUCKETS_CAP} cap"
          f" this is the unbounded cost that 19.4 keeps off the close path.)")
    check("the moved OB rescan GROWS with history (would balloon if left per-close)",
          t_obs_full > t_obs_half * 1.2, f"half={t_obs_half:.0f}us full={t_obs_full:.0f}us")

    # ---- ADAPT ----
    print(f"\n[ADAPT] target_vol pre-recompute={[round(v) for v in tv_pre]}  post={[round(v) for v in tv_post]}")
    check("closes alone do NOT change target_vol (recalibrate left the hot path)",
          all(v == _DEFAULT for v in tv_pre), f"pre={tv_pre}")
    check("periodic _recompute_engine ADAPTS target_vol (recalibration still works)",
          any(po != pr for pr, po in zip(tv_pre, tv_post)) and all(v > 0 for v in tv_post),
          f"post={[round(v) for v in tv_post]}")

    # ---- INVARIANTS (incl. buckets closed AFTER the mid-stream recompute) ----
    nb = cons = ovf = neg = nonfin = 0
    vel = []
    for e in core.engines.values():
        for b in e.closed_buckets:
            nb += 1
            if abs((b.opL + b.opS + b.clL + b.clS + b.churn) - b.curr_vol) > 1e-6 * max(1.0, b.curr_vol):
                cons += 1
            if max(b.opL, b.opS, b.clL, b.clS) > b.curr_vol + 1e-6:
                ovf += 1
            if b.start_time is not None and b.end_time is not None and (b.end_time - b.start_time) < -1e-9:
                neg += 1
            if b.vel_ratio != b.vel_ratio or b.vel_ratio in (float("inf"), float("-inf")):
                nonfin += 1
            vel.append(b.vel_ratio)
    print(f"\n[INVARIANTS] {nb} closed buckets across {len(core.engines)} engines")
    check("buckets produced (incl. after the mid-stream recompute)", nb > 0, f"n={nb}")
    check("5-component conservation holds (all buckets)", cons == 0, f"violations={cons}")
    check("no vector exceeds curr_vol (all buckets)", ovf == 0, f"violations={ovf}")
    check("no negative durations", neg == 0, f"violations={neg}")
    check("vel_ratio finite + varies", nonfin == 0 and len(set(round(v, 6) for v in vel)) > 1,
          f"nonfinite={nonfin}")

    # ===================================================================
    # [DISCRIMINATOR] both ObPacket shapes through the REAL pipe_client handler
    # (the load-bearing terminal-side decode: CLOSE piggyback vs OB refresh).
    # ===================================================================
    from app import protocol                            # noqa: E402
    from app.pipe_client import PipeClientWorker          # noqa: E402

    cap = []
    dcore = MarketDataCore({}, lambda tf, line: cap.append((tf, line)), _noop)
    base_T = trade_T[0]
    for i in range(60):                                  # big trades -> force a real close fast
        if cap:
            break
        dcore._process_aggtrade({"e": "aggTrade", "p": "75.10", "q": "2000",
                                 "m": (i % 2 == 0), "T": base_T + i})
    close_lines = [ln for tf, ln in cap if tf == "1m"]
    check("producer: feeds CLOSE path emits a frame", len(close_lines) > 0, f"frames={len(cap)}")
    close_pkt = protocol.parse_line(close_lines[0])
    check("producer: CLOSE frame carries new_buckets + EMPTY order_blocks",
          bool(close_pkt.new_buckets) and close_pkt.order_blocks == [],
          f"nb={len(close_pkt.new_buckets)} obs={len(close_pkt.order_blocks)}")
    refresh_shape = protocol.parse_line(
        protocol.ObPacket(tf="1m", order_blocks=dcore._recompute_engine("1m"),
                          new_buckets=[], vpin=0.0).to_line())
    check("producer: recompute REFRESH frame carries NO new_buckets",
          refresh_shape.new_buckets == [], f"nb={len(refresh_shape.new_buckets)}")

    w = PipeClientWorker(tf="1m")
    w.order_blocks = [{"ob_id": "seed1"}, {"ob_id": "seed2"}]
    w.closed_buckets = [{"start_time": 0.0}]
    ob0, cb0 = w._ob_ver, w._cb_ver
    w._apply(close_pkt)                                  # REAL feeds close frame
    check("consumer: CLOSE frame grows history AND leaves the OB matrix untouched (no blink)",
          len(w.closed_buckets) == 2 and w._cb_ver > cb0
          and [o.get("ob_id") for o in w.order_blocks] == ["seed1", "seed2"] and w._ob_ver == ob0,
          f"obs={[o.get('ob_id') for o in w.order_blocks]} cb={len(w.closed_buckets)}")
    cb1 = w._cb_ver
    w._apply(protocol.parse_line(
        protocol.ObPacket(tf="1m", order_blocks=[{"ob_id": "fresh"}], new_buckets=[], vpin=0.0).to_line()))
    check("consumer: REFRESH frame REPLACES the OB matrix, does NOT grow history",
          [o.get("ob_id") for o in w.order_blocks] == ["fresh"]
          and len(w.closed_buckets) == 2 and w._cb_ver == cb1,
          f"obs={[o.get('ob_id') for o in w.order_blocks]}")
    w._apply(protocol.parse_line(
        protocol.ObPacket(tf="1m", order_blocks=[], new_buckets=[], vpin=0.0).to_line()))
    check("consumer: EMPTY refresh CLEARS the matrix (authoritative, not stuck stale)",
          w.order_blocks == [])
    obx = w._ob_ver
    w._apply(protocol.parse_line(
        protocol.ObPacket(tf="5m", order_blocks=[{"ob_id": "x"}], new_buckets=[], vpin=0.0).to_line()))
    check("consumer: wrong-tf ObPacket ignored", w._ob_ver == obx and w.order_blocks == [])
    print("\n[DISCRIMINATOR] both ObPacket shapes verified through the real pipe_client handler")

    print("\n" + "=" * 64)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED  (close-class flat + heavy work moved off + invariants hold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
