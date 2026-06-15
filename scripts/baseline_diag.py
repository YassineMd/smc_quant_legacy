"""READ-ONLY pipeline-integrity baseline diagnostic (Phase 0, Step 0b).

Connects to a RUNNING daemon as an ordinary client by reusing
``app.pipe_client.PipeClientWorker`` (so it speaks the exact same socket +
chunked-CATCHUP protocol the real terminal does — it cannot drift from it),
subscribes to the timeframe given on the command line, waits for the chunked
catch-up to fully load, takes ONE ``snapshot()``, and reports distributional
stats over the closed-bucket history.

It changes NO application code and is NOT imported by the app. It only READS the
daemon's broadcast state — nothing is written back to the daemon, the engines,
or the database.

Usage:
    python scripts/baseline_diag.py <tf>        # tf in {1m,5m,15m,1h,4h}

Output: written to scripts/baseline_<tf>_<UTCdate>.txt AND printed to stdout.

This is the 'before' baseline; later integrity steps re-run it as the regression
harness, so the metric definitions here are frozen and must not be 'improved'.
"""

from __future__ import annotations

import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

# Make the app package importable regardless of the caller's cwd.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                       # noqa: E402
from app.pipe_client import PipeClientWorker  # noqa: E402

# The four open/close-interest vectors, audited individually in metric 4.
_VEC_KEYS = ("opL", "opS", "clL", "clS")
VPIN_TRAIL = 50           # trailing CLOSED buckets for the recomputed VPIN
CATCHUP_TIMEOUT_SECS = 45  # hard ceiling on the catch-up wait


# ---------------------------------------------------------------------------
# Catch-up wait + snapshot
# ---------------------------------------------------------------------------
def load_snapshot(tf: str) -> dict:
    """Start a worker on ``tf``, block until the chunked CATCHUP is fully loaded,
    then return exactly one ``snapshot()``.

    'Fully loaded' = the worker reported ``catchup_loading`` go True then back to
    False with closed buckets present (the CATCHUP_START -> CHUNK* -> END cycle).
    Fallback for a catch-up that completes between polls: connected, not loading,
    buckets present, and the closed-bucket count stable for >=1.5s.
    """
    worker = PipeClientWorker(tf=tf)
    worker.start()
    deadline = time.time() + CATCHUP_TIMEOUT_SECS
    seen_loading = False
    last_nb = -1
    last_change = time.time()
    try:
        while time.time() < deadline:
            snap = worker.snapshot()
            nb = len(snap["closed_buckets"])
            if nb != last_nb:
                last_nb, last_change = nb, time.time()
            if snap["catchup_loading"]:
                seen_loading = True
            if snap["connected"] and not snap["catchup_loading"] and nb > 0:
                if seen_loading or (time.time() - last_change) >= 1.5:
                    return worker.snapshot()   # ONE authoritative snapshot
            time.sleep(0.1)
        # timed out — return whatever we have, the caller reports the bucket count
        return worker.snapshot()
    finally:
        worker.stop()


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def _clean(vals) -> np.ndarray:
    out = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return np.asarray(out, dtype=float)


def summarize(vals) -> dict:
    """count / min / p25 / p50 / p75 / p95 / max for a sequence of floats."""
    a = _clean(vals)
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "min": float(a.min()),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "p95": float(np.percentile(a, 95)),
        "max": float(a.max()),
    }


def _fmt(x: float) -> str:
    if x is None:
        return "       --"
    ax = abs(x)
    if ax != 0 and (ax >= 1e6 or ax < 1e-4):
        return f"{x:13.4e}"
    return f"{x:13.4f}"


def fmt_summary(name: str, s: dict, unit: str = "") -> str:
    if s.get("n", 0) == 0:
        return f"  {name:<28} n=0 (no data)"
    head = f"  {name:<28} n={s['n']}{(' ' + unit) if unit else ''}"
    cols = (f"        min {_fmt(s['min'])}   p25 {_fmt(s['p25'])}   p50 {_fmt(s['p50'])}\n"
            f"        p75 {_fmt(s['p75'])}   p95 {_fmt(s['p95'])}   max {_fmt(s['max'])}")
    return head + "\n" + cols


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(tf: str, snap: dict) -> str:
    cb = snap.get("closed_buckets", []) or []
    target_vol = snap.get("target_vol", 0.0)
    ab = snap.get("active_bucket") or {}
    now = datetime.now(timezone.utc)

    L: list[str] = []
    L.append("=" * 72)
    L.append("PIPELINE-INTEGRITY BASELINE  (Phase 0 / Step 0b)  -- READ-ONLY")
    L.append("=" * 72)
    L.append(f"timeframe          : {tf}")
    L.append(f"captured (UTC)     : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"daemon target_vol  : {target_vol:.4f}")
    L.append(f"closed buckets (n) : {len(cb)}")
    ab_cv = ab.get("curr_vol", 0.0)
    fill = (ab_cv / target_vol * 100.0) if target_vol > 0 else 0.0
    L.append(f"active bucket fill : {ab_cv:.2f} / {target_vol:.2f}  ({fill:.1f}%)")
    L.append("")

    if not cb:
        L.append("NO CLOSED BUCKETS in snapshot — cannot compute metrics.")
        L.append("(Check that the daemon is running and the tf has accumulated history.)")
        return "\n".join(L)

    # --- 1. duration = end_time - start_time ---
    durations = [b.get("end_time", 0.0) - b.get("start_time", 0.0) for b in cb]
    L.append("[1] DURATION = end_time - start_time   (seconds)")
    L.append(fmt_summary("duration", summarize(durations), "buckets"))
    L.append("")

    # --- 2. vol_mult ---
    vol_mults = [b.get("vol_mult", float("nan")) for b in cb]
    L.append("[2] VOL_MULT  (per-bucket velocity ratio vs rolling avg)")
    L.append(fmt_summary("vol_mult", summarize(vol_mults), "buckets"))
    L.append("")

    # --- 3. conservation residual = ((opL+opS+clL+clS) - curr_vol) / curr_vol ---
    residuals, res_skipped = [], 0
    for b in cb:
        cv = b.get("curr_vol", 0.0)
        if cv <= 0:
            res_skipped += 1
            continue
        four = b.get("opL", 0.0) + b.get("opS", 0.0) + b.get("clL", 0.0) + b.get("clS", 0.0)
        residuals.append((four - cv) / cv)
    L.append("[3] CONSERVATION RESIDUAL = ((opL+opS+clL+clS) - curr_vol) / curr_vol")
    L.append("    (4-vector only; the 'churn' field does not exist yet. 0 == exact "
             "conservation;")
    L.append("     >0 == 4 vectors sum to MORE than curr_vol.)")
    L.append(fmt_summary("residual", summarize(residuals), "buckets"))
    if res_skipped:
        L.append(f"        ({res_skipped} bucket(s) skipped for curr_vol <= 0)")
    L.append("")

    # --- 4. any single vector exceeding curr_vol ---
    total_cv = 0
    exceed = 0
    for b in cb:
        cv = b.get("curr_vol", 0.0)
        if cv <= 0:
            continue
        total_cv += 1
        if any(b.get(k, 0.0) > cv for k in _VEC_KEYS):
            exceed += 1
    frac = (exceed / total_cv) if total_cv else 0.0
    L.append("[4] BUCKETS WHERE ANY OF opL/opS/clL/clS INDIVIDUALLY EXCEEDS curr_vol")
    L.append(f"  count               {exceed} / {total_cv}")
    L.append(f"  fraction            {frac:.6f}  ({frac * 100:.4f}%)")
    L.append("")

    # --- 5. engine VPIN vs recomputed VPIN (trailing 50 CLOSED buckets) ---
    engine_vpin = snap.get("vpin", 0.0)
    trail = cb[-VPIN_TRAIL:]
    ti = sum(abs(b.get("buy_vol", 0.0) - b.get("sell_vol", 0.0)) for b in trail)
    tv = sum(b.get("curr_vol", 0.0) for b in trail)
    recomputed_vpin = (ti / tv) if tv > 0 else 0.0
    L.append(f"[5] VPIN  (engine scalar  vs  recompute over trailing {VPIN_TRAIL} CLOSED buckets)")
    L.append(f"  engine vpin (snap)  {engine_vpin:.6f}")
    L.append(f"  recomputed vpin     {recomputed_vpin:.6f}"
             f"   [sum|buy-sell|={ti:.2f} / sum(curr_vol)={tv:.2f}"
             f" over n={len(trail)}]")
    L.append(f"  difference          {engine_vpin - recomputed_vpin:+.6f}"
             f"   (engine - recomputed)")
    L.append("")
    L.append("=" * 72)
    return "\n".join(L)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: python {os.path.basename(sys.argv[0])} <tf>   "
              f"(tf in {config.TIMEFRAMES})")
        return 2
    tf = sys.argv[1].strip()
    if tf not in config.TIMEFRAMES:
        print(f"ERROR: unknown timeframe {tf!r}; must be one of {config.TIMEFRAMES}")
        return 2

    print(f"[baseline_diag] connecting to daemon at "
          f"{config.IPC_HOST}:{config.IPC_PORT}, subscribing to {tf} ...")
    snap = load_snapshot(tf)
    report = build_report(tf, snap)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = os.path.join(out_dir, f"baseline_{tf}_{stamp}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")

    print(report)
    print(f"\n[baseline_diag] report written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
