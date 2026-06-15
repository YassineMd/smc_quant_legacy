"""Step 3B verification render (READ-ONLY) — OLD 50/50 vs NEW confirmed-only.

Reconstructs the legacy 50/50 churn-split decomposition analytically from the
new-math buckets a running daemon produces. Both decompositions are deterministic
functions of the same bucket inputs; with the Step-2 clamp `|delta_oi| <= vol`,
`churn` is identical under both, so:

    old_total_open  = (opL+opS) + churn/2      # EXACT (split-independent)
    old_total_close = (clL+clS) + churn/2      # EXACT

The TOTAL opening/closing intent — the quantity the climb-through-chop verdict is
judged on — is therefore reconstructed EXACTLY (the buy/sell split of churn, the
only approximate part, is not needed for the totals). The per-vector split is
reconstructed with the bucket-aggregate ratio for completeness.

Renders an offscreen PNG (price + regime shading / cumulative OLD-dashed vs
NEW-solid / per-bucket rate pulse) and dumps buckets+reconstruction+regimes to
JSON so the figure is reproducible. Changes NO app code; only reads the daemon.

Usage:
    python scripts/compare_step3_decomp.py <tf>
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                       # noqa: E402
from app.pipe_client import PipeClientWorker  # noqa: E402

CATCHUP_TIMEOUT = 60

# regime classifier (efficiency ratio = |net move| / path length over a window)
TREND_THR = 0.55
RANGE_THR = 0.30


# ---------------------------------------------------------------------------
def load_snapshot(tf: str) -> dict:
    """Start a worker, wait for the chunked CATCHUP to fully load, take one snap."""
    w = PipeClientWorker(tf=tf)
    w.start()
    deadline = time.time() + CATCHUP_TIMEOUT
    seen_loading = False
    last_nb, last_change = -1, time.time()
    try:
        while time.time() < deadline:
            s = w.snapshot()
            nb = len(s["closed_buckets"])
            if nb != last_nb:
                last_nb, last_change = nb, time.time()
            if s["catchup_loading"]:
                seen_loading = True
            if s["connected"] and not s["catchup_loading"] and nb > 0:
                if seen_loading or (time.time() - last_change) >= 1.5:
                    return w.snapshot()
            time.sleep(0.1)
        return w.snapshot()
    finally:
        w.stop()


def reconstruct(cb: list) -> dict:
    def arr(k):
        return np.array([float(b.get(k, 0.0)) for b in cb], dtype=float)

    opL, opS, clL, clS, churn = arr("opL"), arr("opS"), arr("clL"), arr("clS"), arr("churn")
    cv = arr("curr_vol")
    close = arr("close")
    bratio = np.divide(arr("buy_vol"), cv, out=np.zeros_like(cv), where=cv > 0)
    sratio = np.divide(arr("sell_vol"), cv, out=np.zeros_like(cv), where=cv > 0)

    new_open, new_close = opL + opS, clL + clS
    old_open = new_open + churn / 2.0          # EXACT
    old_close = new_close + churn / 2.0        # EXACT
    # per-vector reconstruction (bucket-ratio approx of the churn split)
    old_opL = opL + (churn / 2.0) * bratio
    old_opS = opS + (churn / 2.0) * sratio
    old_clL = clL + (churn / 2.0) * sratio
    old_clS = clS + (churn / 2.0) * bratio
    return dict(close=close, churn=churn, cv=cv,
                new_open=new_open, new_close=new_close,
                old_open=old_open, old_close=old_close,
                opL=opL, opS=opS, clL=clL, clS=clS,
                old_opL=old_opL, old_opS=old_opS, old_clL=old_clL, old_clS=old_clS)


def efficiency_ratio(close: np.ndarray, w: int) -> np.ndarray:
    n = len(close)
    er = np.zeros(n)
    for i in range(n):
        lo = max(0, i - w)
        if i <= lo:
            continue
        net = abs(close[i] - close[lo])
        path = float(np.sum(np.abs(np.diff(close[lo:i + 1]))))
        er[i] = (net / path) if path > 0 else 0.0
    return er


def classify(close: np.ndarray):
    n = len(close)
    w = max(12, n // 10)
    min_run = max(6, n // 25)
    er = efficiency_ratio(close, w)
    # A real directional leg is super-diffusive: net displacement over the window
    # beats a random walk's expected |disp| ~ sigma*sqrt(w). Chop is sub-diffusive.
    # This is scale-free (adapts to the window's own volatility); ER is a secondary
    # efficiency gate. Fixes the prior bug where efficient *small* moves on flat
    # price were mislabeled "trend".
    step_sigma = float(np.std(np.diff(close))) if n > 1 else 0.0
    rw = max(1e-9, step_sigma * np.sqrt(w))
    disp = np.array([abs(close[i] - close[max(0, i - w)]) for i in range(n)])
    label = np.full(n, "mixed", dtype=object)
    label[(disp >= 2.0 * rw) & (er >= 0.50)] = "trend"
    label[(disp <= 0.7 * rw) & (er <= 0.40)] = "range"
    spans = []
    i = 0
    while i < n:
        k = label[i]
        j = i
        while j < n and label[j] == k:
            j += 1
        if k in ("trend", "range") and (j - i) >= min_run:
            spans.append((i, j - 1, k))
        i = j
    return er, label, spans, w, min_run


def _shade(ax, spans):
    for s, e, kind in spans:
        ax.axvspan(s, e + 1, color="#2ecc71" if kind == "trend" else "#9aa0aa",
                   alpha=0.13, lw=0)


def render(tf: str, R: dict, spans: list, w: int, n: int, out_png: str) -> None:
    x = np.arange(n)
    cn_open, cn_close = np.cumsum(R["new_open"]), np.cumsum(R["new_close"])
    fig, (ax0, ax1, axN, ax2) = plt.subplots(
        4, 1, sharex=True, figsize=(15, 13),
        gridspec_kw={"height_ratios": [1.0, 1.4, 1.1, 1.1]})

    ax0.plot(x, R["close"], color="#222222", lw=1.1)
    _shade(ax0, spans)
    ax0.set_ylabel("Price (bucket close)")
    ax0.set_title(f"Step 3 churn decomposition — OLD 50/50 vs NEW confirmed-only  "
                  f"[{tf}, {n} buckets, ER window={w}]   "
                  f"green=trend  gray=range", fontsize=11)

    # shared-scale cumulative: shows the chop inflation (OLD >> NEW)
    ax1.plot(x, np.cumsum(R["old_open"]), "--", color="#1e8449", lw=1.5,
             label="OLD opens (50/50)")
    ax1.plot(x, cn_open, "-", color="#2ecc71", lw=2.1, label="NEW opens (confirmed)")
    ax1.plot(x, np.cumsum(R["old_close"]), "--", color="#922b21", lw=1.5,
             label="OLD closes (50/50)")
    ax1.plot(x, cn_close, "-", color="#e74c3c", lw=2.1, label="NEW closes (confirmed)")
    _shade(ax1, spans)
    ax1.set_ylabel("Cumulative intent\n(shared scale)")
    ax1.legend(loc="upper left", fontsize=8, ncol=2)

    # NEW-only cumulative at its OWN scale: shows where NEW actually builds
    axN.plot(x, cn_open, "-", color="#2ecc71", lw=2.0, label="NEW opens")
    axN.plot(x, cn_close, "-", color="#e74c3c", lw=2.0, label="NEW closes")
    _shade(axN, spans)
    ax_ylim = max(1.0, float(max(cn_open[-1] if len(cn_open) else 0,
                                 cn_close[-1] if len(cn_close) else 0)))
    axN.set_ylim(0, ax_ylim * 1.08)
    axN.set_ylabel("NEW cumulative\n(own scale)")
    axN.legend(loc="upper left", fontsize=8, ncol=2)

    ax2.bar(x, R["new_open"], width=1.0, color="#2ecc71", label="opens (up)")
    ax2.bar(x, -R["new_close"], width=1.0, color="#e74c3c", label="closes (down)")
    ax2.plot(x, R["churn"], color="#7f8c8d", lw=0.9, label="churn (neutral)")
    _shade(ax2, spans)
    ax2.axhline(0, color="#000000", lw=0.6)
    ax2.set_ylabel("Per-bucket rate (pulse)")
    ax2.set_xlabel("bucket index")
    ax2.legend(loc="upper left", fontsize=8, ncol=3)

    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in config.TIMEFRAMES:
        print(f"usage: python {os.path.basename(sys.argv[0])} <tf>  (tf in {config.TIMEFRAMES})")
        return 2
    tf = sys.argv[1]
    print(f"[compare] connecting to daemon, subscribing to {tf} ...")
    snap = load_snapshot(tf)
    cb = snap.get("closed_buckets", []) or []
    n = len(cb)
    print(f"[compare] closed buckets: {n}")
    if n < 30:
        print("[compare] too few buckets for a meaningful comparison (need >=30). "
              "Let the daemon accumulate more.")
        return 1

    R = reconstruct(cb)
    er, label, spans, w, min_run = classify(R["close"])
    trend_spans = [s for s in spans if s[2] == "trend"]
    range_spans = [s for s in spans if s[2] == "range"]
    both = bool(trend_spans) and bool(range_spans)

    print(f"[compare] ER window={w}  min_run={min_run}  "
          f"ER min/med/max={er.min():.2f}/{np.median(er):.2f}/{er.max():.2f}")
    print(f"[compare] trend spans: {trend_spans}")
    print(f"[compare] range spans: {range_spans}")
    print(f"[compare] BOTH REGIMES PRESENT: {both}")
    net_move = R["close"][-1] - R["close"][0]
    print(f"[compare] net price move over window: {net_move:+.2f}  "
          f"(first={R['close'][0]:.2f} last={R['close'][-1]:.2f})")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_png = os.path.join(out_dir, f"compare_step3_{tf}_{stamp}.png")
    out_json = os.path.join(out_dir, f"compare_step3_{tf}_{stamp}.json")
    render(tf, R, spans, w, n, out_png)
    dump = {
        "tf": tf, "captured_utc": stamp, "n": n, "er_window": w,
        "both_regimes": both, "trend_spans": trend_spans, "range_spans": range_spans,
        "buckets": [
            {"i": i, "close": float(R["close"][i]), "er": float(er[i]),
             "label": str(label[i]), "churn": float(R["churn"][i]),
             "new_open": float(R["new_open"][i]), "new_close": float(R["new_close"][i]),
             "old_open": float(R["old_open"][i]), "old_close": float(R["old_close"][i])}
            for i in range(n)],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(dump, f)
    print(f"[compare] PNG : {out_png}")
    print(f"[compare] JSON: {out_json}")
    return 0 if both else 3   # exit 3 = rendered but single-regime (don't sign off)


if __name__ == "__main__":
    raise SystemExit(main())
