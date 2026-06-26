"""Step A proof #2 — the OB-pool fallback handles SUSTAINED failure stably.

A broken process pool must degrade to ON-LOOP compute (today's behavior) and STAY there — never crash the
daemon, never thrash-recreate a doomed pool forever, never leak worker processes. Asserts:
  (1) sustained failure: EVERY cycle returns the correct on-loop line; after _OB_POOL_MAX_FAILS the pool is
      PERMANENTLY disabled (latched on-loop); the number of pools created is bounded (NO thrash); each broken
      pool was shut down (no leak).
  (2) real spawn pool: a worker actually spawns, and shutdown() fully REAPS it (no lingering child process).

Run:  python scripts/validate_ob_pool_fallback.py
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, feeds                              # noqa: E402
from app.feeds import MarketDataCore, _recompute_ob_line, _ob_pool_warmup  # noqa: E402
from app.persistence import _bucket_from_dict             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CB_LOCAL = os.path.join(ROOT, "data", "cb_1m_6k.jsonl")


def _buckets():
    if os.path.exists(CB_LOCAL):
        with open(CB_LOCAL) as f:
            return [_bucket_from_dict(json.loads(ln)) for ln in f if ln.strip()][:400]
    return []


class _AlwaysBrokenPool:
    """A pool whose every submit raises (a permanently-broken worker). Records its shutdown() calls."""
    def __init__(self): self.shutdowns = 0
    def submit(self, *a, **k): raise BrokenProcessPool("simulated sustained failure")
    def shutdown(self, *a, **k): self.shutdowns += 1


async def part1_sustained_failure() -> bool:
    buckets = _buckets()
    truth = _recompute_ob_line(buckets, "1m", 0.0)         # the correct on-loop answer
    loop = asyncio.get_event_loop()

    core = MarketDataCore({}, lambda *a: None, lambda *a: None)
    created = []
    # Make the REAL _ensure_ob_pool build a broken pool each time (exercises _note_ob_pool_fail's real logic).
    orig_ppe = feeds.ProcessPoolExecutor
    feeds.ProcessPoolExecutor = lambda *a, **k: created.append(_AlwaysBrokenPool()) or created[-1]
    try:
        N = feeds._OB_POOL_MAX_FAILS
        lines = [await core._recompute_ob_line_async(loop, buckets, "1m", 0.0) for _ in range(N + 5)]
    finally:
        feeds.ProcessPoolExecutor = orig_ppe

    correct = all(ln == truth for ln in lines)
    latched = core._ob_pool_disabled is True
    no_thrash = len(created) <= N                           # one per failure up to the cap, NONE after disable
    all_shut = all(p.shutdowns >= 1 for p in created)       # every broken pool torn down -> no leak
    print(f"(1) sustained-failure: cycles={len(lines)} all_correct={correct} latched_onloop={latched} "
          f"pools_created={len(created)} (<= {N} => no thrash: {no_thrash}) every_pool_shutdown={all_shut}")
    return correct and latched and no_thrash and all_shut


def part2_real_pool_reaped() -> bool:
    before = len(multiprocessing.active_children())
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    try:
        pool.submit(_ob_pool_warmup).result(timeout=30)    # forces the worker to spawn
        during = len(multiprocessing.active_children())
    finally:
        pool.shutdown(wait=True)
    after = before
    for _ in range(30):                                     # let the OS reap
        after = len(multiprocessing.active_children())
        if after <= before:
            break
        time.sleep(0.1)
    spawned = during > before
    reaped = after <= before
    print(f"(2) real-pool: children before={before} during={during} after_shutdown={after} "
          f"spawned={spawned} reaped(no leak)={reaped}")
    return spawned and reaped


def main():
    ok1 = asyncio.run(part1_sustained_failure())
    ok2 = part2_real_pool_reaped()
    print("\nFALLBACK STABLE (degrade + no-thrash + no-leak):", ok1 and ok2)
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
