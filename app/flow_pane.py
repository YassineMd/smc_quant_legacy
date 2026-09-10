# -*- coding: utf-8 -*-
"""Buy / Sell DOLLAR-FLOW store for the 'flow' scanner mode (user 2026-09-08: "the gauge of the Trades ... on a chart
that displays two lines buyers and sellers, x axis is time, y axis is volume in $").

The tablet's gauge is the taker buy $ and sell $ of the last 60 s.  This is the same quantity as a time series: raw
trades are accumulated into fixed-width TIME BINS once (vectorised, O(new trades)) and every read is a rolling sum over
those bins, so nothing re-scans the tape per frame.

  ingest(ts_ms, price, qty, side)  -> adds price*qty into the bin holding each trade (side 1 = taker buy)
  series(t0, t1, win_secs, max_pts) -> (t, buy$, sell$) where each point is the $ traded in the `win_secs` ENDING there,
                                       clipped to [t0, t1] and decimated to at most `max_pts` points

Both are memoized on a revision counter, so a pan / zoom that changes nothing returns the previous arrays untouched
(the terminal's standing perf rule: any per-redraw pass must be bounded by the drawn range and memoized).
"""
import numpy as np


class FlowStore:
    """Fixed-width time bins of taker buy $ / sell $.  Append-anywhere (live batches arrive newest-first in time,
    backfill windows arrive older), pruned to `retain_secs` from the right edge."""

    def __init__(self, bin_secs: float = 1.0, retain_secs: float = 21600.0):
        self.bin = float(bin_secs)
        self.cap = max(64, int(retain_secs / self.bin))
        self._base = None                      # bin index of _buy[0]
        self._buy = np.zeros(0, dtype=np.float64)
        self._sell = np.zeros(0, dtype=np.float64)
        self._px = np.zeros(0, dtype=np.float64)     # LAST trade price in the bin (0 = no trade yet)
        self._pts = np.zeros(0, dtype=np.float64)    # ms of the trade that set _px, so an out-of-order
        self.rev = 0                                 # backfill batch cannot overwrite a newer price
        self._memo = None
        self._bmemo = None
        self._vmemo = None

    # ---------------------------------------------------------------- state
    def __len__(self) -> int:
        return int(len(self._buy))

    def empty(self) -> bool:
        return self._base is None or len(self._buy) == 0

    def span(self):
        """(t_first, t_last) covered, in unix seconds (bin STARTS), or None."""
        if self.empty():
            return None
        return (self._base * self.bin, (self._base + len(self._buy) - 1) * self.bin)

    def totals(self):
        """(buy $, sell $) over everything retained -- for a sanity readout, not per frame."""
        return (float(self._buy.sum()), float(self._sell.sum()))

    # --------------------------------------------------------------- ingest
    def _fit(self, lo_i: int, hi_i: int) -> None:
        """Grow the arrays so bin indices lo_i..hi_i exist (zeros elsewhere), then prune to cap."""
        if self._base is None:
            self._base = int(lo_i)
            n = int(hi_i - lo_i + 1)
            self._buy = np.zeros(n, dtype=np.float64)
            self._sell = np.zeros(n, dtype=np.float64)
            self._px = np.zeros(n, dtype=np.float64)
            self._pts = np.zeros(n, dtype=np.float64)
            return
        if lo_i < self._base:                                  # older data (a backfill window) -> prepend
            pad = int(self._base - lo_i)
            self._buy = np.concatenate([np.zeros(pad), self._buy])
            self._sell = np.concatenate([np.zeros(pad), self._sell])
            self._px = np.concatenate([np.zeros(pad), self._px])
            self._pts = np.concatenate([np.zeros(pad), self._pts])
            self._base = int(lo_i)
        end = self._base + len(self._buy) - 1
        if hi_i > end:                                         # newer data -> append
            pad = int(hi_i - end)
            self._buy = np.concatenate([self._buy, np.zeros(pad)])
            self._sell = np.concatenate([self._sell, np.zeros(pad)])
            self._px = np.concatenate([self._px, np.zeros(pad)])
            self._pts = np.concatenate([self._pts, np.zeros(pad)])
        if len(self._buy) > self.cap:                          # keep the NEWEST cap bins
            drop = len(self._buy) - self.cap
            self._buy = self._buy[drop:]
            self._sell = self._sell[drop:]
            self._px = self._px[drop:]
            self._pts = self._pts[drop:]
            self._base += drop

    def ingest(self, ts_ms, price, qty, side) -> int:
        """Add one decoded trade array. Returns the number of trades that landed (0 = nothing to do)."""
        ts = np.asarray(ts_ms, dtype=np.float64)
        if ts.size == 0:
            return 0
        pr = np.asarray(price, dtype=np.float64)
        qt = np.asarray(qty, dtype=np.float64)
        sd = np.asarray(side)
        usd = pr * qt
        idx = np.floor(ts / 1000.0 / self.bin).astype(np.int64)
        self._fit(int(idx.min()), int(idx.max()))
        loc = idx - self._base
        keep = (loc >= 0) & (loc < len(self._buy))             # a pruned-away old bin can't be revived
        if not keep.any():
            return 0
        loc = loc[keep]; usd = usd[keep]
        isbuy = (np.asarray(sd)[keep].astype(np.int64) > 0)
        np.add.at(self._buy, loc[isbuy], usd[isbuy])
        np.add.at(self._sell, loc[~isbuy], usd[~isbuy])
        # LAST price per bin. Sort by ts so a duplicate index keeps the newest, and only overwrite when this
        # trade is at least as new as whatever set the bin -- a BACKFILL batch arrives after live batches and
        # must not push an older price over a newer one.
        kts = ts[keep]; kpr = pr[keep]
        order = np.argsort(kts, kind="stable")
        li = loc[order]; lt = kts[order]; lp = kpr[order]
        newer = lt >= self._pts[li]
        if newer.any():
            self._px[li[newer]] = lp[newer]
            self._pts[li[newer]] = lt[newer]
        self.rev += 1
        self._memo = None
        self._bmemo = None
        self._vmemo = None
        return int(loc.size)

    def reset(self) -> None:
        self._base = None
        self._buy = np.zeros(0, dtype=np.float64)
        self._sell = np.zeros(0, dtype=np.float64)
        self._px = np.zeros(0, dtype=np.float64)
        self._pts = np.zeros(0, dtype=np.float64)
        self.rev += 1
        self._memo = None
        self._bmemo = None
        self._vmemo = None

    # --------------------------------------------------------------- bursts
    def bar_bursts(self, starts, ends, win_secs: float, cap: float = 50.0, floor_pct: float = 90.0):
        """Per BAR, the strongest ONE-SIDED taker burst inside it (user 2026-09-08's Volume Burst badges).

        The drawn span is cut into NON-OVERLAPPING windows of `win_secs`. For each window: dominant $ / other $.
        A window counts as a burst only if its TOTAL $ is above the `floor_pct` percentile of every window on
        screen -- i.e. the market was genuinely busy there, not merely one-sided in a lull. Each window is credited
        to the bar its END falls in, and a bar takes the largest ratio credited to it.

        Overlapping rolling windows are deliberately NOT used: a candle holds hundreds of them, and the maximum of
        hundreds of correlated samples is extreme by construction (the first version of this badged every bar).

        Returns (ratio, side) float64 / int8 arrays, one per bar: ratio 0.0 where nothing qualified, side 1 = buy,
        0 = sell. Fully vectorised and memoized on (rev, bars, window, floor)."""
        starts = np.asarray(starts, dtype=np.float64)
        ends = np.asarray(ends, dtype=np.float64)
        nb = len(starts)
        if nb == 0 or self.empty():
            return (np.zeros(nb), np.zeros(nb, dtype=np.int8))
        key = ("bursts", self.rev, round(float(starts[0]), 3), round(float(ends[-1]), 3), nb,
               round(float(win_secs), 3), round(float(cap), 3), round(float(floor_pct), 3))
        memo = getattr(self, "_bmemo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        ratio = np.zeros(nb); side = np.zeros(nb, dtype=np.int8)
        out = (ratio, side)
        n = len(self._buy)
        wb = max(1, int(round(float(win_secs) / self.bin)))
        i0 = int(np.clip(np.floor(starts[0] / self.bin) - self._base, 0, n))
        i1 = int(np.clip(np.ceil(ends[-1] / self.bin) - self._base, 0, n))
        nwin = (i1 - i0) // wb
        if nwin < 1:
            self._bmemo = (key, out)
            return out
        m = nwin * wb
        b = self._buy[i0:i0 + m].reshape(nwin, wb).sum(axis=1)
        s_ = self._sell[i0:i0 + m].reshape(nwin, wb).sum(axis=1)
        tot = b + s_
        dom = np.maximum(b, s_); oth = np.minimum(b, s_)
        floor = float(np.percentile(tot, float(floor_pct))) if nwin > 1 else 0.0
        r = np.where(tot >= floor, np.minimum(dom / np.maximum(oth, 1.0), float(cap)), 0.0)
        is_buy = b >= s_
        wend = (self._base + i0 + (np.arange(nwin) + 1) * wb) * self.bin      # each window's END time
        bar_ix = np.searchsorted(starts, wend, side="right") - 1              # the bar that window ended in
        good = (bar_ix >= 0) & (bar_ix < nb) & (r > 0)
        good &= wend <= ends[np.clip(bar_ix, 0, nb - 1)]
        if good.any():
            gi = bar_ix[good]; gr = r[good]; gb = is_buy[good]
            mb = np.zeros(nb); ms = np.zeros(nb)
            np.maximum.at(mb, gi[gb], gr[gb])
            np.maximum.at(ms, gi[~gb], gr[~gb])
            take_buy = mb >= ms
            ratio[:] = np.where(take_buy, mb, ms)
            side[:] = take_buy.astype(np.int8)
            side[ratio <= 0] = 0
        self._bmemo = (key, out)
        return out

    # -------------------------------------------------------- constant-$ bins
    def volume_bins(self, t0: float, t1: float, bin_usd: float, tick: float, max_bins: int = 1200):
        """Cut [t0, t1] into bins of `bin_usd` TAKER DOLLARS and report what each one did to price.

        Returns (t_close, buy$, sell$, imbalance, advance_ticks) where
            imbalance     = (buy - sell) / (buy + sell)  in [-1, 1]  -- how one-sided the bin was
            advance_ticks = ticks the PUSHING side gained: +ve it got paid, -ve price went the other way
        Price comes from the tape (last trade in the closing / preceding 1 s bin), which is what the study
        measured; that carries about half a spread of bid-ask bounce, and the bins are ~37 s at $1M so it is a
        small share of a typical 5-tick move.

        Only the NEWEST `max_bins` are returned, so a 72 h store still draws in bounded time. Memoized on
        (rev, range, bin_usd, max_bins) like every other read here."""
        if self.empty() or bin_usd <= 0:
            z = np.zeros(0)
            return (z, z, z, z, z)
        key = (self.rev, round(float(t0), 3), round(float(t1), 3), round(float(bin_usd), 2), int(max_bins))
        if self._vmemo is not None and self._vmemo[0] == key:
            return self._vmemo[1]
        n = len(self._buy)
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        i1 = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)
        if i1 <= i0:
            z = np.zeros(0)
            out = (z, z, z, z, z)
            self._vmemo = (key, out)
            return out
        b = self._buy[i0:i1 + 1]; s_ = self._sell[i0:i1 + 1]
        tot = b + s_
        c = np.cumsum(tot)
        if c[-1] < bin_usd:
            z = np.zeros(0)
            out = (z, z, z, z, z)
            self._vmemo = (key, out)
            return out
        # a bin CLOSES at the first 1 s bin whose cumulative dollars cross the next multiple of bin_usd
        edges = np.searchsorted(c, np.arange(bin_usd, c[-1], bin_usd), side="left")
        edges = np.unique(np.clip(edges, 0, len(c) - 1))
        if edges.size < 2:
            z = np.zeros(0)
            out = (z, z, z, z, z)
            self._vmemo = (key, out)
            return out
        if edges.size > max_bins:
            edges = edges[-(max_bins + 1):]                     # keep the NEWEST bins
        lo = edges[:-1] + 1
        hi = edges[1:]
        cb = np.concatenate([[0.0], np.cumsum(b)])
        cs = np.concatenate([[0.0], np.cumsum(s_)])
        buy = cb[hi + 1] - cb[lo]
        sell = cs[hi + 1] - cs[lo]
        # price at each boundary: the last trade at or before it, carried forward over tradeless 1 s bins
        px = self._px[i0:i1 + 1].copy()
        have = px > 0
        if not have.any():
            z = np.zeros(0)
            out = (z, z, z, z, z)
            self._vmemo = (key, out)
            return out
        fill = np.maximum.accumulate(np.where(have, np.arange(px.size), 0))
        px = px[fill]
        p_open = px[edges[:-1]]
        p_close = px[edges[1:]]
        realized = (p_close - p_open) / float(tick)
        totb = buy + sell
        nf = np.where(totb > 0, (buy - sell) / np.maximum(totb, 1e-9), 0.0)
        adv = np.sign(nf) * realized
        t_close = (self._base + i0 + hi) * self.bin + self.bin
        out = (t_close, buy, sell, nf, adv)
        self._vmemo = (key, out)
        return out

    # ----------------------------------------------------------------- read
    def series(self, t0: float, t1: float, win_secs: float, max_pts: int = 4000):
        """(t, buy$, sell$) for the bins inside [t0, t1]: each point = the $ traded in the `win_secs` ENDING at it.
        Decimated to <= max_pts points (the rolling values are already smoothed, so plain striding is faithful).
        MEMOIZED on (rev, range, window, max_pts) -- a redraw that changes nothing reuses the arrays."""
        if self.empty():
            return (np.zeros(0), np.zeros(0), np.zeros(0))
        key = (self.rev, round(float(t0), 3), round(float(t1), 3), round(float(win_secs), 3), int(max_pts))
        if self._memo is not None and self._memo[0] == key:
            return self._memo[1]
        n = len(self._buy)
        w = max(1, int(round(float(win_secs) / self.bin)))
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        i1 = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)
        if i1 < i0:
            out = (np.zeros(0), np.zeros(0), np.zeros(0))
            self._memo = (key, out)
            return out
        # the rolling sum ending at bin i needs bins i-w+1..i -> take the cumsum over the slice PLUS its w-1 prefix
        p0 = max(0, i0 - w + 1)
        cb = np.concatenate([[0.0], np.cumsum(self._buy[p0:i1 + 1])])
        cs = np.concatenate([[0.0], np.cumsum(self._sell[p0:i1 + 1])])
        hi = np.arange(i0 - p0 + 1, i1 - p0 + 2)               # exclusive end of each window, in slice space
        lo = np.maximum(0, hi - w)
        buy = cb[hi] - cb[lo]
        sell = cs[hi] - cs[lo]
        t = (self._base + np.arange(i0, i1 + 1)) * self.bin + self.bin   # stamp each point at its bin END
        step = max(1, int(np.ceil(len(t) / float(max(16, max_pts)))))
        if step > 1:
            t = t[::step]; buy = buy[::step]; sell = sell[::step]
        out = (t, buy, sell)
        self._memo = (key, out)
        return out
