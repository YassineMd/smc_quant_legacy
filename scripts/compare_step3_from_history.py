"""Step 3B comparison from FROZEN history (READ-ONLY) — recompute OLD vs NEW from
the raw inputs stored in a COPY of history.db.before-fixes.

The before-fixes buckets store old-math vectors and NO churn field, but they store
the raw inputs needed to recompute both decompositions exactly at bucket level:
curr_vol, buy_vol, sell_vol, and the legacy 4-vectors — from which the signed
bucket ΔOI is recovered exactly as `(opL+opS) - (clL+clS)` (a property of the
legacy split). We then:

  * clamp ΔOI to [-curr_vol, curr_vol]  (Step 2) so the comparison isolates the
    Step-3 churn change rather than re-showing the Step-2 over-unity artifact;
  * recompute NEW (confirmed-only + churn, Step-3 math) and OLD (50/50, legacy)
    from the SAME clamped inputs.

`old_total = new_total + churn/2` is exact; both are bucket-aggregate recomputes
of the same inputs, so the old-vs-new contrast is faithful. Reuses the identical
classifier + renderer from compare_step3_decomp so the figure matches the live one.

Never touches the frozen original — point it at a copy.

Usage:
    python scripts/compare_step3_from_history.py scan            # all TFs: counts + regimes
    python scripts/compare_step3_from_history.py <tf> [s] [e]    # render full or slice [s:e]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import config                          # noqa: E402
from compare_step3_decomp import classify, render  # noqa: E402 - identical classifier+renderer

DB_COPY = r"C:\Users\Yassine Mdouari\AppData\Local\Temp\smc_step3b_hist\before_fixes_copy.db"


def load_old(db_path: str, tf: str) -> list:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute(
            "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id ASC", (tf,)).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


def recompute(buckets: list) -> dict:
    def g(k):
        return np.array([float(b.get(k, 0.0)) for b in buckets], dtype=float)

    cv = g("curr_vol")
    buy, sell = g("buy_vol"), g("sell_vol")
    o_opL, o_opS, o_clL, o_clS = g("opL"), g("opS"), g("clL"), g("clS")
    close = g("close_price")   # persistence stores close_price (not "close")

    # recover signed bucket ΔOI from the legacy split (exact), then Step-2 clamp
    doi_raw = (o_opL + o_opS) - (o_clL + o_clS)
    doi = np.clip(doi_raw, -cv, cv)
    bR = np.divide(buy, cv, out=np.zeros_like(cv), where=cv > 0)
    sR = np.divide(sell, cv, out=np.zeros_like(cv), where=cv > 0)
    oi_mag = np.abs(doi)
    churn = cv - oi_mag

    new_open = np.where(doi > 0, oi_mag, 0.0)     # opL+opS (confirmed)
    new_close = np.where(doi < 0, oi_mag, 0.0)    # clL+clS (confirmed)
    old_open = new_open + churn / 2.0             # legacy 50/50 (exact on totals)
    old_close = new_close + churn / 2.0
    return dict(close=close, churn=churn, cv=cv, doi=doi,
                new_open=new_open, new_close=new_close,
                old_open=old_open, old_close=old_close,
                bR=bR, sR=sR)


def _slice(R: dict, s: int, e: int) -> dict:
    return {k: (v[s:e] if isinstance(v, np.ndarray) else v) for k, v in R.items()}


def _summary(tf: str, R: dict) -> tuple:
    close = R["close"]
    n = len(close)
    if n < 30:
        print(f"  {tf:>3}: {n} buckets  (too few)")
        return tf, n, [], []
    _er, _lab, spans, w, _mr = classify(close)
    trend = [s for s in spans if s[2] == "trend"]
    rng = [s for s in spans if s[2] == "range"]
    net = close[-1] - close[0]
    print(f"  {tf:>3}: {n} buckets  ER_w={w}  net={net:+.2f}  "
          f"trend_spans={len(trend)}  range_spans={len(rng)}  "
          f"BOTH={'YES' if trend and rng else 'no'}")
    if trend:
        print(f"        trend: {trend}")
    if rng:
        print(f"        range: {rng}")
    return tf, n, trend, rng


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if not os.path.exists(DB_COPY):
        print(f"ERROR: db copy not found at {DB_COPY}")
        return 2

    if sys.argv[1] == "scan":
        print(f"[scan] {DB_COPY}")
        for tf in config.TIMEFRAMES:
            _summary(tf, recompute(load_old(DB_COPY, tf)))
        return 0

    tf = sys.argv[1]
    if tf not in config.TIMEFRAMES:
        print(f"unknown tf {tf!r}; tf in {config.TIMEFRAMES}")
        return 2
    R = recompute(load_old(DB_COPY, tf))
    n_full = len(R["close"])
    s = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    e = int(sys.argv[3]) if len(sys.argv) > 3 else n_full
    R = _slice(R, s, e)
    n = len(R["close"])
    if n < 30:
        print(f"window too small ({n})")
        return 1

    er, label, spans, w, _mr = classify(R["close"])
    trend = [sp for sp in spans if sp[2] == "trend"]
    rng = [sp for sp in spans if sp[2] == "range"]
    both = bool(trend) and bool(rng)
    print(f"[render] tf={tf} window=[{s}:{e}] n={n}  trend={trend}  range={rng}  BOTH={both}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_png = os.path.join(_HERE, f"compare_step3_hist_{tf}_{s}_{e}_{stamp}.png")
    out_json = os.path.join(_HERE, f"compare_step3_hist_{tf}_{s}_{e}_{stamp}.json")
    render(f"{tf} [{s}:{e}] (from before-fixes)", R, spans, w, n, out_png)
    dump = {
        "source": "history.db.before-fixes (copy)", "tf": tf, "slice": [s, e], "n": n,
        "both_regimes": both, "trend_spans": trend, "range_spans": rng,
        "buckets": [
            {"i": i, "close": float(R["close"][i]), "churn": float(R["churn"][i]),
             "doi": float(R["doi"][i]),
             "new_open": float(R["new_open"][i]), "new_close": float(R["new_close"][i]),
             "old_open": float(R["old_open"][i]), "old_close": float(R["old_close"][i])}
            for i in range(n)],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(dump, f)
    print(f"[render] PNG : {out_png}")
    print(f"[render] JSON: {out_json}")
    return 0 if both else 3


if __name__ == "__main__":
    raise SystemExit(main())
