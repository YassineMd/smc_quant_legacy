"""Phase 2b — terminal-side heatmap helpers (PURE: no Qt, so the cache/stitch/decode are unit-testable).

The daemon's ``depth_window`` endpoint ships a ``cols×ybins`` float32 grid of RAW resting size (base64,
row-major ``[col][bin]``) + per-column BBO. This module:
  * decodes the grid/column blobs to numpy,
  * builds the Bookmap + greyscale lookup tables,
  * owns :class:`HeatmapCache` — a contiguous, time-ordered column store that live-appends one column per
    pulse in O(1) (deque, NO growing-array realloc), prepends lazily-loaded OLDER windows with a strict
    contiguity check (no gap / no overlap / time-ordered), caps memory by TIME span, and re-slices the
    visible window for free on a pan that stays inside the loaded range.

The ~MB grid is decoded + cached HERE (terminal side), never on the 20Hz ``snapshot()`` path.
"""

from __future__ import annotations

import base64
from collections import deque
from typing import Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Decode (base64 little-endian float32, exactly what depth_store packs)
# ---------------------------------------------------------------------------
def decode_grid(grid_b64: str, cols: int, ybins: int) -> np.ndarray:
    """-> float32 array shaped (cols, ybins) = [time_col][price_bin], matching build_window's row-major pack."""
    a = np.frombuffer(base64.b64decode(grid_b64), dtype="<f4")
    if a.size != cols * ybins:                      # defensive: short/garbled frame -> empty
        return np.zeros((cols, ybins), dtype=np.float32)
    return a.reshape(cols, ybins).astype(np.float32, copy=True)


def decode_col(col_b64: str, ybins: int) -> np.ndarray:
    """-> float32 array (ybins,) — one live column."""
    a = np.frombuffer(base64.b64decode(col_b64), dtype="<f4")
    if a.size != ybins:
        return np.zeros(ybins, dtype=np.float32)
    return a.astype(np.float32, copy=True)


# ---------------------------------------------------------------------------
# Lookup tables (256×RGBA uint8). Bookmap: dark(low)->blue->green->yellow->red(high).
# ---------------------------------------------------------------------------
def _interp_lut(stops, alpha_ramp: float = 0.16) -> np.ndarray:
    """256×RGBA LUT interpolated over ``stops``. ALPHA ramps 0->255 across the bottom ``alpha_ramp`` of the
    range then stays opaque — so empty / near-zero liquidity is TRANSPARENT (the dark-gray chart canvas
    shows through, no blue wash), and real liquidity fades in then saturates to its color (Bookmap-like)."""
    xs = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], dtype=float)
    t = np.linspace(0.0, 1.0, 256)
    out = np.zeros((256, 4), dtype=np.uint8)
    for ch in range(3):
        out[:, ch] = np.clip(np.interp(t, xs, cols[:, ch]), 0, 255).astype(np.uint8)
    out[:, 3] = (np.clip(t / alpha_ramp, 0.0, 1.0) * 255).astype(np.uint8) if alpha_ramp > 0 else 255
    return out


def bookmap_lut() -> np.ndarray:
    """Dark→blue→cyan→green→yellow→orange→red (low→high resting size); darker = less."""
    return _interp_lut([
        (0.00, (0, 0, 0)), (0.14, (0, 0, 90)), (0.34, (0, 70, 210)),
        (0.50, (0, 180, 190)), (0.64, (40, 205, 40)), (0.78, (225, 225, 0)),
        (0.90, (255, 140, 0)), (1.00, (255, 30, 30)),
    ])


def grey_lut() -> np.ndarray:
    """Greyscale: dark(low)→white(high)."""
    return _interp_lut([(0.0, (0, 0, 0)), (1.0, (255, 255, 255))])


# ---------------------------------------------------------------------------
# Neon DIVERGING palette (Deepdom-style): bids -> green, asks -> purple, by SIDE
# ---------------------------------------------------------------------------
NEON_GREEN = (0, 255, 110)      # BUY limit orders (bids, below the mid)
NEON_PURPLE = (190, 70, 255)    # SELL limit orders (asks, above the mid)


def neon_diverging_lut(lo_frac: float = 0.0, grey: bool = False) -> np.ndarray:
    """256×RGBA DIVERGING LUT for the SIGNED liquidity grid: negative values (bids) → neon GREEN, positive
    (asks) → neon PURPLE, with a TRANSPARENT center band of half-width ``lo_frac`` (the lower contrast cutoff
    expressed as a fraction of the symmetric half-range) so empty / sub-cutoff bins show the dark canvas.
    Brightness + alpha ramp from the cutoff (faint) to the extreme (bright neon) = size intensity. ``grey``
    collapses both sides to white (a size-only view, for the 'g' toggle).

    Paired with ``ImageItem.setLevels([-M, +M])`` (M = log10(hi+1)): value −M→idx0 (max bid), 0→idx127.5
    (empty), +M→idx255 (max ask)."""
    lo_frac = float(min(max(lo_frac, 0.0), 0.95))
    t = (np.arange(256) - 127.5) / 127.5                 # [-1,1]: sign = side, |t| = size position in-range
    a = np.abs(t)
    inten = np.clip((a - lo_frac) / max(1e-6, 1.0 - lo_frac), 0.0, 1.0)   # 0 at cutoff → 1 at the extreme
    alpha = np.where(a < lo_frac, 0.0, inten ** 0.65 * 255.0)
    out = np.zeros((256, 4), dtype=np.uint8)
    gcol = (235, 235, 235) if grey else NEON_GREEN
    pcol = (235, 235, 235) if grey else NEON_PURPLE
    for ch in range(3):
        col = np.where(t < 0, gcol[ch], pcol[ch]).astype(float)
        out[:, ch] = np.clip(col * inten, 0, 255).astype(np.uint8)   # dark near the cutoff → full neon at extreme
    out[:, 3] = alpha.astype(np.uint8)
    return out


def _sign_by_side(logg: np.ndarray, bbo_bid, bbo_ask, ylo: float, yhi: float, ybins: int) -> np.ndarray:
    """Sign a (cols,ybins) log grid by SIDE so one ImageItem can color bids vs asks: bins BELOW each column's
    mid (resting BIDS) become NEGATIVE, bins at/above (resting ASKS) stay POSITIVE; magnitude = log10(size+1),
    empty bins stay 0. The mid comes from that column's BBO (the spread cells between best-bid and best-ask
    are empty, so the exact boundary inside the spread is immaterial)."""
    bb = np.asarray(bbo_bid, dtype=float); ba = np.asarray(bbo_ask, dtype=float)
    span = (yhi - ylo) or 1.0
    mid = np.where((bb > 0) & (ba > 0), 0.5 * (bb + ba), np.where(bb > 0, bb, ba))
    binmid = np.clip(((mid - ylo) / span * ybins).astype(int), 0, ybins)
    yb = np.arange(ybins)
    sign = np.where(yb[None, :] < binmid[:, None], -1.0, 1.0)        # bids (below mid) negative
    return logg * sign


# ---------------------------------------------------------------------------
# Contrast: percentile cutpoints over the loaded grid's nonzero sizes
# ---------------------------------------------------------------------------
def percentile_levels(grid: np.ndarray, lo_pct: float = 20.0, hi_pct: float = 99.0) -> Tuple[float, float]:
    """(lower, upper) raw-size cutoffs from the loaded grid's NONZERO bins (the contrast defaults)."""
    nz = grid[grid > 0.0]
    if nz.size == 0:
        return 1.0, 10.0
    lo = float(np.percentile(nz, lo_pct)); hi = float(np.percentile(nz, hi_pct))
    if hi <= lo:
        hi = lo * 10.0 + 1.0
    return lo, hi


# ---------------------------------------------------------------------------
# Contiguous, time-ordered column cache (ring-buffer append + lazy prepend)
# ---------------------------------------------------------------------------
class HeatmapCache:
    """Holds the loaded heatmap columns as one contiguous, time-ordered grid at a FIXED price band
    (ylo, yhi, ybins) and a FIXED time resolution (step_ms). Columns are keyed by their END time
    (``ts`` = the column-end, matching build_window). A zoom/resolution change => :meth:`reset`."""

    def __init__(self, max_span_ms: int = 6 * 3600 * 1000):
        self.max_span_ms = max_span_ms
        self.cols: "deque[tuple]" = deque()   # (ts_ms, col float32[ybins], bid, ask), strictly ascending ts
        self.ybins = 0
        self.step_ms: Optional[float] = None
        self.ylo = self.yhi = 0.0

    # Each column tuple is (ts, raw_col, bid, ask, slog_col): raw_col feeds the contrast percentiles, slog_col
    # (= ±log10(size+1), SIGNED by side — bids negative / asks positive, computed ONCE here) feeds the ImageItem
    # so render never re-logs and a single diverging LUT colors bids green / asks purple.

    # -- (re)load a full window (cold-open / zoom / new band) ---------------
    def reset(self, grid: np.ndarray, t0: int, t1: int, bbo_bid, bbo_ask, ylo: float, yhi: float) -> None:
        cols, ybins = grid.shape
        self.cols.clear()
        self.ybins = ybins; self.ylo = ylo; self.yhi = yhi
        self.step_ms = (t1 - t0) / cols if cols else (t1 - t0)
        slog = _sign_by_side(np.log10(grid + 1.0), bbo_bid, bbo_ask, ylo, yhi, ybins)  # log + side-sign once
        for i in range(cols):
            ts = t0 + (i + 1) * self.step_ms          # column END time (build_window convention)
            self.cols.append((ts, grid[i], float(bbo_bid[i]), float(bbo_ask[i]), slog[i]))
        self._trim()

    # -- one live column, BINNED to the display step (Bookmap: the rightmost pixel = the live book) ---------
    def append_live(self, col: np.ndarray, ts: int, bid: float, ask: float) -> bool:
        """A live pulse (~0.4s) folds into the current step-sized pixel-column: OVERWRITE the rightmost while
        within its bin (the live book updating in place), APPEND a new column once wall-time crosses the next
        step boundary. Keeps the time axis uniform and the update O(1) — no growing-array realloc."""
        if self.step_ms is None or not self.cols or col.shape[0] != self.ybins:
            return False
        last_ts = self.cols[-1][0]
        slogc = np.log10(col + 1.0)                    # sign by side (bids below the mid -> negative)
        mid = 0.5 * (bid + ask) if (bid > 0 and ask > 0) else (bid if bid > 0 else ask)
        if mid and mid > 0:
            span = (self.yhi - self.ylo) or 1.0
            binmid = int(min(max((mid - self.ylo) / span * self.ybins, 0), self.ybins))
            slogc[:binmid] *= -1.0
        if ts >= last_ts + self.step_ms:              # crossed into a new pixel-bin -> append (grid-aligned)
            self.cols.append((last_ts + self.step_ms, col, float(bid), float(ask), slogc))
            self._trim()
        else:                                          # same pixel-bin -> overwrite with the latest book
            self.cols[-1] = (last_ts, col, float(bid), float(ask), slogc)
        return True

    # -- prepend an OLDER lazily-loaded window to the LEFT (contiguous) ------
    def prepend_older(self, grid: np.ndarray, t0: int, t1: int, bbo_bid, bbo_ask) -> int:
        """Stitch an older window to the left. ONLY columns strictly older than the current oldest are
        kept (no overlap); the deque stays time-ordered (no gap as long as the request reached the current
        oldest). Same band/resolution required. Returns how many columns were stitched in."""
        if not self.cols:
            return 0
        cols, ybins = grid.shape
        if ybins != self.ybins:
            return 0
        oldest = self.cols[0][0]
        step = (t1 - t0) / cols if cols else (t1 - t0)
        slog = _sign_by_side(np.log10(grid + 1.0), bbo_bid, bbo_ask, self.ylo, self.yhi, self.ybins)
        new = []
        for i in range(cols):
            ts = t0 + (i + 1) * step
            if ts < oldest:                           # strict: drop any overlap with what we already have
                new.append((ts, grid[i], float(bbo_bid[i]), float(bbo_ask[i]), slog[i]))
        self.cols.extendleft(reversed(new))           # extendleft reverses; reverse(new) restores ascending
        return len(new)

    # -- visible slice for the ImageItem (free on a pan inside the range) ----
    def visible(self, t_lo: int, t_hi: int):
        """(SIGNED-LOG grid (n,ybins), ts (n,), bid (n,), ask (n,), t_first, t_last) for columns in [t_lo, t_hi],
        or None. Returns the pre-computed ±log grid (c[4]) so render is a stack — never a re-log."""
        sel = [c for c in self.cols if t_lo <= c[0] <= t_hi]
        if not sel:
            return None
        grid = np.stack([c[4] for c in sel])          # pre-logged columns -> no per-render log10
        ts = np.fromiter((c[0] for c in sel), dtype=np.float64, count=len(sel))
        bid = np.fromiter((c[2] for c in sel), dtype=np.float64, count=len(sel))
        ask = np.fromiter((c[3] for c in sel), dtype=np.float64, count=len(sel))
        return grid, ts, bid, ask, sel[0][0], sel[-1][0]

    def span(self):
        return (self.cols[0][0], self.cols[-1][0]) if self.cols else (None, None)

    def _trim(self) -> None:
        """Cap memory by TIME span: drop columns older than (newest - max_span)."""
        if not self.cols:
            return
        cutoff = self.cols[-1][0] - self.max_span_ms
        while len(self.cols) > 1 and self.cols[0][0] < cutoff:
            self.cols.popleft()
