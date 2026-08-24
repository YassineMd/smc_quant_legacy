"""WALL SURGE (m10_wallsurge) — 1m + 5m CLOCK charts: a STRONG volume-delta candle that KEPT its move,
printing inside a same-side 30m wall/radar area. Green ▲ when strong net BUYING lands on a 30m SUPPORT (buy)
wall, red ▼ when strong net SELLING lands on a 30m RESISTANCE (sell) wall — aggressive flow agreeing with the
wall it trades into, and holding what it bought.

STRONG is the Volume pane's own 'Pct' definition, replicated to the letter (terminal VOL_PCT_* constants):
|delta| ranked against a trailing window of itself + its previous 49 bars; rank = share of the OTHER window
bars strictly below it (NaN-padded early bars shrink the window; <2 valid bars -> neutral 0.5); STRONG = rank
>= 0.80. KEPT is the Eff/Res module's `retention` (effort_result.compute), replicated to the letter:
side·(close-open) / excursion-in-the-delta-direction ((high-open) net buying / (open-low) net selling); the
signal requires retention >= KEPT_MIN (0.80) — no excursion / opposite-close candles are suppressed (user
2026-08-24, on top of the delta filter). Walls are the SAME `absorption_level_detect.detect()` marks the 30m
HTF walls overlay draws (bucket-sourced), radar area = price ± radar_mult·band; a wall counts while DISPLAY-
alive: from its formation bucket (i0) until the end of its close-through bucket (i1) — display-aligned like
the overlay/hover, which means the birth bucket itself is not strictly causal. Signals evaluate CLOSED candles
only. Descriptive/eyeball layer — NO tested edge is claimed.
"""
from __future__ import annotations

import numpy as np

WIN = 50            # trailing rank window — MUST match terminal.VOL_PCT_WIN (pane parity)
STRONG = 0.80       # rank threshold      — MUST match terminal.VOL_PCT_STRONG
KEPT_MIN = 0.80     # Eff/Res retention floor — candle must KEEP >= this share of its delta-direction excursion
RADAR_MULT = 3.0    # wall radar half-width in bands — matches absorption_level_detect's display default


def retention(c: dict, delta: float):
    """Eff/Res `retention` for one candle, formula-identical to app.effort_result.compute (ticks cancel):
    side·(close-open) / ((high-open) if net buying else (open-low)). None when there is no excursion in the
    delta direction (parity-tested against effort_result.compute)."""
    o = float(c.get("open", c.get("open_price", 0.0)) or 0.0)
    cl = float(c.get("close", c.get("close_price", 0.0)) or 0.0)
    h = float(c.get("high", 0.0) or 0.0); l = float(c.get("low", 0.0) or 0.0)
    if o <= 0.0 or cl <= 0.0 or delta == 0.0:
        return None
    side = 1 if delta > 0 else -1
    exc = (h - o) if side > 0 else (o - l)
    if exc <= 0.0:
        return None
    return side * (cl - o) / exc


def strong_rank(vals: np.ndarray, win: int = WIN) -> np.ndarray:
    """Pane-identical trailing percentile rank of each value vs (itself + previous win-1), NaN-padded."""
    v = np.asarray(vals, dtype=float)
    if len(v) == 0:
        return v
    w = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(win - 1, np.nan), v]), win)
    valid = (~np.isnan(w)).sum(1)
    return np.where(valid > 1, (w < v[:, None]).sum(1) / np.maximum(valid - 1, 1), 0.5)


def project_walls(candles: list, signals: list) -> list:
    """SIGNAL WALLS (user 2026-08-24): every Wall Surge fire births a wall spanning the signal candle's FULL
    height (low..high) — a green ▲ births a SUPPORT wall, a red ▼ a RESISTANCE wall — projected forward until a
    later candle BODY-CLOSES beyond the zone (support: close < lo; resistance: close > hi) = MITIGATED. Same
    design + mark shape as the No-Wick Bar Wall (app/nowick_wall_detect): [{i0, i1, side('S'|'R'), lo, hi,
    broken}], i1 = mitigation bar for a broken wall / last evaluated bar for a live one. Fail-safe: []."""
    n = len(candles)
    if n == 0 or not signals:
        return []
    try:
        out = []
        for e in signals:
            i0 = int(e.get("i", -1))
            if not (0 <= i0 < n):
                continue
            c0 = candles[i0]
            lo = float(c0.get("low", 0.0) or 0.0); hi = float(c0.get("high", 0.0) or 0.0)
            if hi <= lo:
                continue
            side = "S" if int(e.get("side", 0)) > 0 else "R"
            w = {"i0": i0, "i1": n - 1, "side": side, "lo": lo, "hi": hi, "broken": False}
            for j in range(i0 + 1, n):                 # the birth candle can never mitigate its own wall
                cl = float(candles[j].get("close", candles[j].get("close_price", 0.0)) or 0.0)
                if cl <= 0.0:
                    continue
                if (cl < lo) if side == "S" else (cl > hi):
                    w["i1"] = j; w["broken"] = True
                    break
            out.append(w)
        return out
    except Exception:
        return []


def detect(candles: list, walls: list, wall_starts: list, wall_tf_secs: float = 1800.0,
           radar_mult: float = RADAR_MULT, win: int = WIN, strong: float = STRONG,
           kept_min: float = KEPT_MIN) -> list:
    """Signals over CLOSED clock candles (1m or 5m). `walls` = absorption_level_detect.detect() marks over the
    30m bucket history whose start_times are `wall_starts` (same inputs as the 30m HTF walls overlay, so a
    badge always sits inside a band the user can see). Returns [{i, side(+1/-1), delta, rank, kept}]."""
    n = len(candles)
    if n < 2 or not walls or not wall_starts:
        return []
    d = np.array([float(c.get("buy_vol", 0.0) or 0.0) - float(c.get("sell_vol", 0.0) or 0.0)
                  for c in candles])
    rank = strong_rank(np.abs(d), win)
    nH = len(wall_starts)
    zones = []                                       # (side, radar_lo, radar_hi, birth_t, death_t)
    for m in walls:
        side = m.get("side"); P = float(m.get("price") or 0.0); band = float(m.get("band") or 0.0)
        if side not in ("R", "S") or P <= 0.0 or band <= 0.0:
            continue
        i0 = int(m.get("i0", 0))
        if not (0 <= i0 < nH):
            continue
        birth = float(wall_starts[i0])
        i1 = m.get("i1")
        if bool(m.get("broken")) and i1 is not None and 0 <= int(i1) < nH:
            death = float(wall_starts[int(i1)]) + wall_tf_secs   # dies with its close-through bucket
        else:
            death = float("inf")
        zones.append((side, P - radar_mult * band, P + radar_mult * band, birth, death))
    if not zones:
        return []
    out = []
    for i in range(n):
        if rank[i] < strong or d[i] == 0.0:
            continue
        c = candles[i]
        t = float(c.get("start_time", 0.0) or 0.0)
        lo = float(c.get("low", 0.0) or 0.0); hi = float(c.get("high", 0.0) or 0.0)
        if hi <= 0.0 or t <= 0.0:
            continue
        kept = retention(c, float(d[i]))             # Eff/Res retention gate ON TOP of the delta gate (user 2026-08-24)
        if kept is None or kept < kept_min:
            continue
        want = "S" if d[i] > 0 else "R"              # strong buying belongs on a buy (support) wall, selling on a sell wall
        for (side, rlo, rhi, birth, death) in zones:
            if side == want and birth <= t < death and lo <= rhi and hi >= rlo:
                out.append({"i": i, "side": 1 if d[i] > 0 else -1,
                            "delta": float(d[i]), "rank": float(rank[i]), "kept": float(kept)})
                break
    return out
