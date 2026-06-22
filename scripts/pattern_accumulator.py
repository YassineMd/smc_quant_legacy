#!/usr/bin/env python3
"""Passive candidate accumulator — read-only scan of the daemon's history.db.

Banks two kinds of candidate events, with their characteristics AND forward outcomes, so a real
setup can be tested at an HONEST sample size later (n>=200) instead of curve-fitting the handful of
events in the current data (the lesson from the states / rare-pattern / OB-break audits):

  1. OB BREAKS (the priority candidate — the one signal that showed a directional lean): every order
     block that died via a close-through its far edge, with the breaking candle's full
     characteristics + forward continuation/reversal at K=1/3/5 in ATRs, on each scale.
  2. RARE-PATTERN WINDOWS: loose HiddenBullAccum / HiddenBearDist / WhaleWars matches + their factor
     vector + the 12-state the classifier currently assigns (the overlap signal).

PASSIVE + READ-ONLY: opens history.db read-only, never writes it; appends to JSONL logs beside the
DB. IDEMPOTENT: dedups against the existing logs (OB breaks by ob_id, patterns by tf/pattern/L/epoch),
so re-runs on a cron only append genuinely-new events. Reuses the engine's own OB detector
(calc_quant_obs) and the shared pure region_state — no logic is re-derived.

    python scripts/pattern_accumulator.py                 # scan the live history.db (config.HISTORY_DB)
    python scripts/pattern_accumulator.py --db PATH        # scan a specific DB (testing)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import bucket_state, config, region_state  # noqa: E402
from app import vpin_adaptive as VA                  # noqa: E402
from app.persistence import _bucket_from_dict        # noqa: E402
from app.quant_engine import calc_quant_obs          # noqa: E402

SW = bucket_state.SWEEP_WINDOW


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(con, tf: str, limit: int):
    """Most-recent `limit` closed buckets for `tf`, chronological. Returns (qbuckets, snaps, tvs)."""
    rows = con.execute(
        "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id DESC LIMIT ?", (tf, limit)
    ).fetchall()
    qb = [_bucket_from_dict(json.loads(r[0])) for r in reversed(rows)]
    snaps = [b.full_snapshot() for b in qb]
    tvs = [getattr(b, "target_vol", 0.0) for b in qb]
    return qb, snaps, tvs


def _atr(snaps, i: int) -> float:
    rng = [max(snaps[j]["high"] - snaps[j]["low"], config.TICK_SIZE) for j in range(max(0, i - SW), i)]
    return (sum(rng) / len(rng)) if rng else config.TICK_SIZE


def _append(path: str, recs: list) -> None:
    if not recs:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _existing_keys(path: str, key_fn):
    keys = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    keys.add(key_fn(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
    return keys


# ---------------------------------------------------------------------------
# OB breaks — the priority candidate
# ---------------------------------------------------------------------------
def scan_ob_breaks(qb, snaps, tf: str, logged_ids: set) -> list:
    obs = calc_quant_obs(SimpleNamespace(closed_buckets=qb), tf)
    s2i = {round(snaps[i]["start_time"], 3): i for i in range(len(snaps))}
    series = VA.rolling_vpin(snaps)
    max_k = max(config.ACCUM_FWD_KS)
    out = []
    for o in obs:
        if o["active"] or not o.get("end_epoch") or o["ob_id"] in logged_ids:
            continue
        i = s2i.get(round(o["end_epoch"], 3))
        if i is None or i < region_state.EXH_WINDOW or i + max_k >= len(snaps):
            continue  # need priors AND a complete forward window (logged on a later run if too recent)
        b = snaps[i]
        vol = b["curr_vol"] or 1.0
        rng = max(b["high"] - b["low"], config.TICK_SIZE)
        d = 1 if o["type"] == "bearish" else -1   # bearish/supply death = UP break (+1); bullish/demand = DOWN (-1)
        far = o["top"] if d > 0 else o["bottom"]
        atr = _atr(snaps, i)
        warn, tox = VA.vpin_cutpoints(series[max(0, i - config.VPIN_ADAPT_WINDOW):i + 1])
        bm, sm, _om = region_state.exhaustion_mults(snaps, i)
        out.append({
            "ts": _utcnow(), "tf": tf, "ob_id": o["ob_id"], "ob_type": o["type"],
            "break_dir": "up" if d > 0 else "down", "break_epoch": o["end_epoch"],
            "power_score": o.get("power_score"),
            "candle": {
                "body": abs(b["close"] - b["open"]) / rng,           # directional close fraction
                "aggr_in_dir": d * (b["buy_vol"] - b["sell_vol"]) / vol,
                "oi_in_dir": (b["opL"] if d > 0 else b["opS"]) / vol,  # fresh OI in the break direction
                "churn": b.get("churn", 0.0) / vol,
                "vol_mult": b["vol_mult"],
                "penetration_atr": abs(b["close"] - far) / atr,       # how far the close cleared the far edge
                "close_pos": (b["close"] - b["low"]) / rng,           # 1=closed at high, 0=at low (wick/rejection tell)
                "vpin": series[i], "vpin_tier": VA.vpin_tier(series[i], warn, tox),
                "state": bucket_state.classify_bucket(snaps, i, bm, sm)[0],
            },
            # forward continuation in the break direction, in ATRs (>0 continued, <0 reversed)
            "fwd": {f"K{k}": d * (snaps[i + k]["close"] - b["close"]) / atr for k in config.ACCUM_FWD_KS},
        })
    return out


# ---------------------------------------------------------------------------
# Rare-pattern windows — loose recall, banked for later grounding
# ---------------------------------------------------------------------------
def _win_factors(snaps, tvs, a: int, b: int) -> dict:
    seg = snaps[a:b + 1]
    S = lambda k: sum(x.get(k, 0.0) for x in seg)
    buy, sell = S("buy_vol"), S("sell_vol")
    vol = buy + sell or 1.0
    opL, opS, clL, clS = S("opL"), S("opS"), S("clL"), S("clS")
    vec = opL + opS + clL + clS or 1.0
    hi = max(x["high"] for x in seg)
    lo = min(x["low"] for x in seg)
    rng = max(hi - lo, config.TICK_SIZE)
    imb = sum(abs(x["buy_vol"] - x["sell_vol"]) for x in seg)
    n = len(seg)
    tv = tvs[b] or 0.0
    return {
        "cvd": (buy - sell) / vol, "disp": (seg[-1]["close"] - seg[0]["open"]) / rng,
        "opL": opL / vec, "opS": opS / vec, "both": min(opL, opS) / vec,
        "churn": S("churn") / vol, "vpin": (imb / (n * tv)) if tv else 0.0,
    }


def _pattern_match(f: dict) -> "str | None":
    if f["cvd"] <= config.ACCUM_HBA_CVD and f["disp"] >= 0 and f["opL"] >= max(f["opS"], 1e-9):
        return "HiddenBullAccum"
    if f["cvd"] >= config.ACCUM_HBD_CVD and f["disp"] <= 0 and f["opS"] >= max(f["opL"], 1e-9):
        return "HiddenBearDist"
    if f["both"] >= config.ACCUM_WW_BOTH and abs(f["disp"]) <= 0.20 and f["churn"] < 0.65:
        return "WhaleWars"
    return None


def scan_patterns(snaps, tvs, tf: str, logged_keys: set) -> list:
    out = []
    n = len(snaps)
    for L in config.ACCUM_PATTERN_WINDOWS:
        for a in range(SW, n - L):
            b = a + L - 1
            end_ep = round(snaps[b]["end_time"], 3)
            key = (tf, L, end_ep)
            if key in logged_keys:
                continue
            f = _win_factors(snaps, tvs, a, b)
            pat = _pattern_match(f)
            if pat is None:
                continue
            st, _conf, _dbg = region_state.selection_state(snaps, a, b)
            out.append({
                "ts": _utcnow(), "tf": tf, "L": L, "pattern": pat,
                "end_epoch": end_ep, "factors": f, "state_12": st,
            })
            logged_keys.add(key)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Passive OB-break + rare-pattern candidate accumulator.")
    ap.add_argument("--db", default=config.HISTORY_DB, help="history.db path (default: config.HISTORY_DB)")
    ap.add_argument("--limit", type=int, default=config.ACCUM_LIMIT, help="recent buckets/tf to scan")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"history.db not found at {args.db}")
    logdir = os.path.dirname(os.path.abspath(args.db))
    ob_log = os.path.join(logdir, "ob_breaks.jsonl")
    pat_log = os.path.join(logdir, "pattern_candidates.jsonl")

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)   # READ-ONLY
    try:
        ob_ids = _existing_keys(ob_log, lambda r: r["ob_id"])
        pat_keys = _existing_keys(pat_log, lambda r: (r["tf"], r["L"], round(r["end_epoch"], 3)))
        tot_ob = tot_pat = 0
        for tf in config.ACCUM_TFS:
            try:
                qb, snaps, tvs = _load(con, tf, args.limit)
            except sqlite3.OperationalError:
                continue
            if len(snaps) < region_state.EXH_WINDOW + max(config.ACCUM_FWD_KS) + 1:
                continue
            ob_recs = scan_ob_breaks(qb, snaps, tf, ob_ids)
            pat_recs = scan_patterns(snaps, tvs, tf, pat_keys)
            _append(ob_log, ob_recs)
            _append(pat_log, pat_recs)
            for r in ob_recs:
                ob_ids.add(r["ob_id"])
            tot_ob += len(ob_recs)
            tot_pat += len(pat_recs)
            print(f"  {tf}: +{len(ob_recs)} OB breaks, +{len(pat_recs)} pattern candidates "
                  f"({len(snaps)} buckets)")
        print(f"accumulator: +{tot_ob} OB breaks -> {ob_log}; +{tot_pat} patterns -> {pat_log}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
