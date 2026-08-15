"""RADAR RUNNER — a RESISTED Order-Flow WALL's radar BREAKOUT. LIVE overlay signal (the one forward-validated edge of
the 2026-08-15 study line; see study/wall_breakout_*.py).

SETUP: a wall's radar visit RESISTS, then a bar BREAKS OUT of the radar in the DEFENSE direction — its open is INSIDE
the radar [radar_lo, radar_hi] (radar = wall price +/- 3*band) and its close is BEYOND the defended extreme (UP through
radar_hi for a support S / buy wall; DOWN through radar_lo for a resistance R / sell wall), after a visit of >= 3 bars.
Price then tends to RUN in TIERS of radar-lengths (L = radar_hi - radar_lo): recon base ~75% reach 1x, ~51% 2x, ~40% 3x,
and the tier-to-tier continuation RISES (~67% 1->2 up to ~80%+ 4->5). Net-positive BOTH recon years across every exit
scheme on 1h/15m/5m after fees; survives causal base-rate + causal-geometry P&L. Best on 1h/15m (1m/5m ~ sub-fee).

EXIT PLAN emitted per signal: SL = the OPPOSITE radar extreme; tiered targets TP1/TP2/TP3 = broken_extreme +/- N*L.
Backtest-favoured management: scale 1/3 out at each tier + stop->breakeven after TP1, OR hold and trail the stop by tier.

detect(buckets, walls=None, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp1, tp2, tp3, targets, band, price,
  radar_lo, radar_hi, pen, wall_side('S'|'R'), p_resist}].  `walls` = app.absorption_level_detect.detect() marks
  (detected internally if None). i is in the passed-list index space. Causal (each event uses only bars up to its own k).
  NOT yet live-proven — recon-validated only (recon-vs-live wall fidelity + breakout-bar slippage are the open gates)."""
from __future__ import annotations

from . import absorption_level_detect as _al
from .engulf_sr_detect import _ohlc

RADAR_MULT = float(getattr(_al, "RADAR_MULT", 3.0))
MINVISIT = 3           # a real wall test: the radar visit must be at least this many bars before the breakout
NTIERS = 3             # tiered targets 1x / 2x / 3x radar-lengths


def detect(buckets, walls=None, skip_last=True):
    n = len(buckets)
    if n < 4:
        return []
    O = [0.0] * n; C = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], _h, _l = _ohlc(b)
    if walls is None:
        try:
            walls = _al.detect(buckets, skip_last=False)
        except Exception:
            walls = []
    hi_n = (n - 1) if skip_last else n                       # never fire on a still-forming last bar
    seen = set(); out = []
    for w in (walls or []):
        side = w.get("side"); P = float(w.get("price") or 0.0); band = float(w.get("band") or 0.0)
        if band <= 0 or P <= 0 or side not in ("S", "R"):
            continue
        rlo = P - RADAR_MULT * band; rhi = P + RADAR_MULT * band
        for r in w.get("radar_runs", ()):                    # each radar visit window
            if len(r) < 2:
                continue
            a = int(r[0]); b = int(r[1]); pr = float(r[2]) if len(r) > 2 else 50.0
            for k in range(b, min(b + 2, hi_n - 1) + 1):     # breakout bar at / just after the visit end
                if k < 1 or k >= hi_n:
                    continue
                if not (rlo <= O[k] <= rhi):                  # open INSIDE the radar
                    continue
                broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)   # close BEYOND the defended extreme
                if not broke or (k - a) < MINVISIT or (k, side) in seen:
                    continue
                seen.add((k, side))
                s = 1 if side == "S" else -1; L = rhi - rlo
                brk = rhi if side == "S" else rlo                       # the extreme it broke through
                sl = rlo if side == "S" else rhi                        # SL = opposite extreme
                tgt = [brk + s * (N * L) for N in range(1, NTIERS + 1)]  # tiered targets
                pen = (C[k] - rhi) / band if side == "S" else (rlo - C[k]) / band
                out.append(dict(i=k, side=s, entry=C[k], sl=sl, tp1=tgt[0], tp2=tgt[1], tp3=tgt[2],
                                targets=tgt, band=band, price=P, radar_lo=rlo, radar_hi=rhi,
                                pen=pen, wall_side=side, p_resist=pr))
                break
    out.sort(key=lambda e: e["i"])
    return out
