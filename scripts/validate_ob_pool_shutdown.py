"""FIX 2 proof — the OB-pool graceful shutdown is CLEAN (reaps the worker, no leak) and BOUNDED (a hung
shutdown can NEVER block SIGTERM handling / the restart).

Run: python scripts/validate_ob_pool_shutdown.py   (exit 0 iff clean + bounded)
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.feeds import MarketDataCore, _ob_pool_warmup   # noqa: E402


class _HungPool:
    """A pool whose shutdown() never returns — to prove shutdown_ob_pool's timeout bound."""
    def shutdown(self, wait=True):
        time.sleep(30)


def main():
    core = MarketDataCore({}, lambda *a: None, lambda *a: None)
    ok = True

    # (1) BOUNDED — a hung shutdown must return within the timeout (never blocks SIGTERM)
    core._ob_pool = _HungPool(); core._ob_pool_disabled = False
    t0 = time.perf_counter(); core.shutdown_ob_pool(timeout=1.5); dt = time.perf_counter() - t0
    b_ok = dt < 3.0 and core._ob_pool is None
    print("(1) bounded     : hung(30s) shutdown, timeout=1.5 -> returned in %.2fs (never blocks SIGTERM): %s" % (dt, b_ok))
    ok = ok and b_ok

    # (2) no-op when there is no pool
    core._ob_pool = None
    t0 = time.perf_counter(); core.shutdown_ob_pool(); dt2 = time.perf_counter() - t0
    n_ok = dt2 < 0.1
    print("(2) no-op       : pool=None -> %.4fs : %s" % (dt2, n_ok))
    ok = ok and n_ok

    # (3) CLEAN REAP of a REAL spawn pool — the worker is spawned then fully reaped (no process leak)
    core._ob_pool = None; core._ob_pool_disabled = False
    pool = core._ensure_ob_pool()
    pool.submit(_ob_pool_warmup).result(timeout=30)
    spawned = len(multiprocessing.active_children())
    core.shutdown_ob_pool(timeout=8.0)
    left = spawned
    for _ in range(50):
        left = len(multiprocessing.active_children())
        if left == 0:
            break
        time.sleep(0.1)
    r_ok = spawned >= 1 and left == 0 and core._ob_pool is None
    print("(3) clean reap  : spawned=%d  children_left=%d (reaped: %s)  pool=None: %s" % (
        spawned, left, left == 0, r_ok))
    ok = ok and r_ok

    print("\nOB-POOL SHUTDOWN CLEAN + BOUNDED:", ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
