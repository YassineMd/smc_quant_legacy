#!/usr/bin/env python3
"""19.6 fidelity sign-off (read-only, NOT wired into the app): kline-built vs
aggTrade-built footprint for the SAME 1m candle, side by side.

Mirrors compare_step3_*.py: reconstructs BOTH footprints from the active tape and
renders a PNG. Two candles are shown:
  * the most-TRAVELED candle  -> aggTrade should reveal real intra-candle structure
    the kline smear flattened/misplaced (DIVERGENCE);
  * a near-FLAT/quiet candle  -> the two should nearly CONVERGE (proof aggTrade is
    not inventing structure — it only diverges where there is real travel to capture).
The headline per candle is the POC DISPLACEMENT: kline-POC price vs aggTrade-POC
price and the tick distance between them.

  kline-built  = the RETIRED path: each kline frame's deltaVol/deltaBuy smeared onto
                 that frame's close price (the block 19.3 deleted from _process_kline).
  aggTrade-built= the LIVE path: each trade routed to its TRUE price (19.3 logic).

Run:  python scripts/compare_19_6_footprint.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import matplotlib                       # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

from app import config                   # noqa: E402
from app.quant_engine import _effort_ticks  # noqa: E402  (the REAL Step-4 denominator)

_HERE = os.path.dirname(os.path.abspath(__file__))
_TAPE = os.path.join(_HERE, "fixtures", "aggtrade_tape_active.jsonl")
_TICK = config.TICK_SIZE
_TF = 60   # 1m candles
_BUY = "#2ecc71"
_SELL = "#e74c3c"


def load():
    aggs, klines = [], []
    with open(_TAPE, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            d = r["data"]
            if r["type"] == "aggTrade":
                aggs.append(d)
            elif r["type"] == "kline" and d.get("k", {}).get("i") == "1m":
                klines.append(d["k"])
    return aggs, klines


def kline_footprints(klines):
    """Retired path: replay deltaVol/deltaBuy per frame onto the frame's close price."""
    by_candle = defaultdict(list)
    for k in klines:
        by_candle[int(k["t"])].append(k)        # k["t"] = candle-open ms
    fps, ohlc = {}, {}
    for t_ms, frames in by_candle.items():
        lev = defaultdict(lambda: {"b": 0.0, "s": 0.0})
        last_v = last_vb = 0.0
        for k in frames:                          # tape (arrival) order; cumulative v grows
            v, vb, c = float(k["v"]), float(k.get("V", 0.0)), f'{float(k["c"]):.2f}'
            dv, db = v - last_v, vb - last_vb
            ds = dv - db
            if dv > 0:
                lev[c]["b"] += max(0.0, db)
                lev[c]["s"] += max(0.0, ds)
            last_v, last_vb = v, vb
        sec = t_ms // 1000
        fps[sec] = {p: dict(x) for p, x in lev.items()}
        last = frames[-1]
        ohlc[sec] = {"h": float(last["h"]), "l": float(last["l"]), "c": float(last["c"])}
    return fps, ohlc


def agg_footprints(aggs):
    """Live path: each trade onto its TRUE price; m=True => seller aggresses (sell)."""
    fps = defaultdict(lambda: defaultdict(lambda: {"b": 0.0, "s": 0.0}))
    for d in aggs:
        sec = (int(d["T"]) // (_TF * 1000)) * _TF
        p, q = f'{float(d["p"]):.2f}', float(d["q"])
        fps[sec][p]["s" if d["m"] else "b"] += q
    return {sec: {p: dict(x) for p, x in lev.items()} for sec, lev in fps.items()}


def _poc(levels):
    best, bestv = None, -1.0
    for p, v in levels.items():
        tot = v["b"] + v["s"]
        if tot > bestv:
            bestv, best = tot, float(p)
    return best


def metrics(levels):
    buy = sum(v["b"] for v in levels.values())
    sell = sum(v["s"] for v in levels.values())
    ticks = _effort_ticks(levels)
    return {"poc": _poc(levels), "buy": buy, "sell": sell, "n": len(levels),
            "buyer_er": buy / ticks, "seller_er": sell / ticks, "disp_ticks": ticks}


def _draw(ax, levels, title, poc_price):
    prices = sorted(float(p) for p in levels)
    for fp in prices:
        v = levels[f"{fp:.2f}"]
        ax.barh(fp, v["b"], height=_TICK * 0.85, color=_BUY, edgecolor="none")
        ax.barh(fp, v["s"], left=v["b"], height=_TICK * 0.85, color=_SELL, edgecolor="none")
    if poc_price is not None:
        ax.axhline(poc_price, color="black", lw=1.4, ls="--")
        ax.text(0.98, poc_price, f" POC {poc_price:.2f}", va="center", ha="right",
                transform=ax.get_yaxis_transform(), fontsize=8, fontweight="bold")
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("volume (buy | sell)", fontsize=8)
    ax.grid(axis="x", alpha=0.2)


def main() -> int:
    aggs, klines = load()
    kfp, ohlc = kline_footprints(klines)
    afp = agg_footprints(aggs)

    secs = sorted(set(kfp) & set(afp))
    full = secs[1:-1] if len(secs) >= 3 else secs          # drop partial boundary candles
    cand = [s for s in full if sum(v["b"] + v["s"] for v in afp[s].values()) > 500] or full
    rng = lambda s: ohlc[s]["h"] - ohlc[s]["l"]
    traveled = max(cand, key=rng)
    quiet = min(cand, key=rng)

    print(f"tape candles (full): {len(cand)}   "
          + "  ".join(f"{datetime.fromtimestamp(s, timezone.utc):%H:%M}"
                      f"(rng {rng(s)/_TICK:.0f}t, vol {sum(v['b']+v['s'] for v in afp[s].values()):.0f})"
                      for s in cand))

    fig, axes = plt.subplots(2, 2, figsize=(12, 11))
    for row, (sec, label) in enumerate([(traveled, "MOST-TRAVELED"), (quiet, "QUIET")]):
        km, am = metrics(kfp[sec]), metrics(afp[sec])
        disp = abs(km["poc"] - am["poc"]) / _TICK
        hhmm = f"{datetime.fromtimestamp(sec, timezone.utc):%H:%M} UTC"
        rng_t = rng(sec) / _TICK
        print(f"\n[{label}] {hhmm}  range {rng_t:.0f} ticks")
        print(f"   kline-built : POC {km['poc']:.2f}  levels {km['n']:>2}  "
              f"disp {km['disp_ticks']:.1f}t  E/R b={km['buyer_er']:.0f}/s={km['seller_er']:.0f}")
        print(f"   aggTrade    : POC {am['poc']:.2f}  levels {am['n']:>2}  "
              f"disp {am['disp_ticks']:.1f}t  E/R b={am['buyer_er']:.0f}/s={am['seller_er']:.0f}")
        print(f"   >>> POC DISPLACEMENT: {disp:.0f} ticks  (kline {km['poc']:.2f} -> aggTrade {am['poc']:.2f})")

        _draw(axes[row][0], kfp[sec], f"kline-built (smeared onto 1s closes) — {km['n']} levels", km["poc"])
        _draw(axes[row][1], afp[sec], f"aggTrade-built (true price) — {am['n']} levels", am["poc"])
        lo = min(ohlc[sec]["l"], km["poc"], am["poc"]) - 2 * _TICK
        hi = max(ohlc[sec]["h"], km["poc"], am["poc"]) + 2 * _TICK
        for c in (0, 1):
            axes[row][c].set_ylim(lo, hi)
        axes[row][0].set_ylabel(
            f"{label} candle {hhmm}\nrange {rng_t:.0f} ticks\nPOC Δ = {disp:.0f} ticks", fontsize=9)

    fig.suptitle("19.6 fidelity sign-off — kline-smeared vs aggTrade true-price footprint "
                 "(same candle)\nTRAVELED row = divergence · QUIET row = convergence "
                 "(aggTrade not inventing structure)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(_HERE, f"compare_19_6_footprint_{stamp}.png")
    fig.savefig(out, dpi=120)
    print(f"\n[render] PNG -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
