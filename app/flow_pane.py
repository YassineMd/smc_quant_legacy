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
import os

import numpy as np


class FlowStore:
    """Fixed-width time bins of taker buy $ / sell $.  Append-anywhere (live batches arrive newest-first in time,
    backfill windows arrive older), pruned to `retain_secs` from the right edge."""

    def __init__(self, bin_secs: float = 1.0, retain_secs: float = 21600.0):
        self.bin = float(bin_secs)
        self.cap = max(64, int(retain_secs / self.bin))
        self._base = None                      # bin index of _buy[0]
        self._bufs = None; self._start = 0     # the over-allocated buffers the six arrays are views into (_fit)
        self._buy = np.zeros(0, dtype=np.float64)
        self._sell = np.zeros(0, dtype=np.float64)
        self._px = np.zeros(0, dtype=np.float64)     # LAST trade price in the bin (0 = no trade yet)
        # TRUE high / low per bin, from EVERY trade (user 2026-09-12). The wicks used to be reduced from the
        # per-second CLOSE, which is blind to anything that recovers inside a second: re-measured against the
        # DOM's tick tape over 6.04 h, that understated the cycle high on 30.2% of cycles, by up to 77 TICKS,
        # and 22 of 46 Big Player sweeps reached outside the candle they sat on. 0 = no trade in the bin yet.
        self._pxh = np.zeros(0, dtype=np.float64)
        self._pxl = np.zeros(0, dtype=np.float64)
        self._pts = np.zeros(0, dtype=np.float64)    # ms of the trade that set _px, so an out-of-order
        self.rev = 0                                 # backfill batch cannot overwrite a newer price
        self.rev_hist = 0                            # bumps only when a batch wrote bins OLDER than the live
        #                                              edge it found (a backfill chunk, a gap fill, a late
        #                                              batch): "the tape behind you changed". A lagging but
        #                                              contiguous live stream never bumps it. Bumped at the
        #                                              END of ingest, so a reader that saw span() move while
        #                                              the bins were still being written is told to re-read.
        self._memo = None
        self._bmemo = None
        self._xmemo = None
        self._pxmemo = None

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
    _GROW_BINS = 8192            # reserve appended per reallocation (~2.3 h of seconds)
    _HIST_MARGIN_BINS = 5        # a batch this far behind the previous edge is OUT OF ORDER (rev_hist)
    _KEYS = ("buy", "sell", "px", "pxh", "pxl", "pts")

    def _views(self, start: int, n: int) -> None:
        """Point the six public arrays at bins [start, start + n) of the buffers."""
        self._start = int(start)
        for k in self._KEYS:
            setattr(self, "_" + k, self._bufs[k][start:start + n])

    def _fit(self, lo_i: int, hi_i: int) -> None:
        """Grow the arrays so bin indices lo_i..hi_i exist (zeros elsewhere), then prune to cap.

        ⚠ the arrays are VIEWS into over-allocated buffers (2026-09-14): the old six np.concatenate calls
        copied the whole 72 h store on EVERY new second of tape (12 MB per second). An append into the
        reserve is a re-slice; a prune advances the view's start; a copy happens on a prepend (a backfill
        chunk) or once the reserve is used up (~2.3 h)."""
        if self._base is None:
            self._base = int(lo_i)
            n = int(hi_i - lo_i + 1)
            self._bufs = {k: np.zeros(n + self._GROW_BINS, dtype=np.float64) for k in self._KEYS}
            self._views(0, n)
            return
        n = int(len(self._buy))
        if lo_i < self._base:                                  # older data (a backfill window) -> prepend
            pad = int(self._base - lo_i)
            new = {}
            for k in self._KEYS:
                bb = np.zeros(pad + n + self._GROW_BINS, dtype=np.float64)
                bb[pad:pad + n] = getattr(self, "_" + k)
                new[k] = bb
            self._bufs = new
            self._views(0, pad + n)
            self._base = int(lo_i)
            n = pad + n
        end = self._base + n - 1
        if hi_i > end:                                         # newer data -> append
            pad = int(hi_i - end)
            if self._start + n + pad <= int(self._bufs["buy"].shape[0]):
                self._views(self._start, n + pad)              # inside the reserve: no copy at all
            else:
                for k in self._KEYS:
                    bb = np.zeros(n + pad + self._GROW_BINS, dtype=np.float64)
                    bb[:n] = getattr(self, "_" + k)
                    self._bufs[k] = bb
                self._views(0, n + pad)
            n = n + pad
        if n > self.cap:                                       # keep the NEWEST cap bins
            drop = n - self.cap
            self._views(self._start + drop, n - drop)
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
        _prev_last = None if self._base is None else int(self._base) + int(len(self._buy)) - 1
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
        # TRUE HIGH / LOW per bin, over every trade in the batch. Order-independent, so a backfill window
        # arriving after live batches can only widen a bin, never replace it -- which is what the _pts guard
        # above has to do for the CLOSE. An empty bin holds 0, so the low needs the first trade to seed it
        # rather than min(0, price); _pxh can take maximum.at directly.
        np.maximum.at(self._pxh, loc, kpr)
        _fresh = self._pxl[loc] <= 0.0
        if _fresh.any():
            self._pxl[loc[_fresh]] = kpr[_fresh]
        np.minimum.at(self._pxl, loc, kpr)
        # out of order = this batch reached further back than a contiguous stream would: the previous edge
        # minus a few seconds of legitimate overlap between consecutive live batches
        if _prev_last is not None and int(loc.min()) + int(self._base) < _prev_last - self._HIST_MARGIN_BINS:
            self.rev_hist += 1
        self.rev += 1
        self._memo = None
        self._bmemo = None
        self._xmemo = None
        self._pxmemo = None
        return int(loc.size)

    def ingest_window(self, t0_ms, t1_ms, ts_ms, price, qty, side) -> int:
        """A backfill WINDOW is the daemon's authoritative tape for [t0, t1]: the bins it covers are REPLACED,
        never added to.

        ⚠⚠ ingest() ADDS, and every window used to be added on top of whatever the bins already held: the live
        batches of the same seconds (a boot chunk runs up to "now"), a chunk delivered twice after the pump's 90 s
        re-ask, the 60 s the right-gap plan reaches back over the bins loaded from disk. Measured 2026-09-22
        against a fresh engine on the same daemon: the terminal's bins were EXACT 2x / 4x / 1.5x / 0.5x multiples
        of the engine's over chunk-sized spans -- hours at a time -- and every cycle rating, I x I bar and
        interpretation line built on them. Bins past the window's last trade are left alone: a window's end is
        often "now", and the live stream owns those seconds."""
        ts = np.asarray(ts_ms, dtype=np.float64)
        if ts.size == 0:
            return 0
        lo_i = int(np.floor(float(t0_ms) / 1000.0 / self.bin))
        hi_i = min(int(np.floor(float(t1_ms) / 1000.0 / self.bin)), int(np.floor(float(ts.max()) / 1000.0 / self.bin)))
        if hi_i >= lo_i:
            self._fit(lo_i, hi_i)
            a = max(0, lo_i - int(self._base)); b = min(int(len(self._buy)), hi_i - int(self._base) + 1)
            if b > a:
                for k in self._KEYS:
                    getattr(self, "_" + k)[a:b] = 0.0
        return self.ingest(ts, price, qty, side)

    # ------------------------------------------------------------------ persistence
    _SAVE_KEYS = ("buy", "sell", "px", "pxh", "pxl", "pts")
    _SAVE_VER = 2                # 2 = bins built with ingest_window (a file without it holds double-counted spans)

    def save(self, path: str) -> bool:
        """Write the bins to `path` (npz, atomic via a temp file). ~15 MB for 72 h; the caller runs it on a
        thread off a SNAPSHOT (see snapshot_arrays), never on the frame loop."""
        snap = self.snapshot_arrays()
        return self.save_snapshot(snap, path)

    def snapshot_arrays(self):
        """(base, rev, {name: array copy}) -- a consistent copy taken on the GUI thread (~5 ms at 72 h)."""
        if self.empty():
            return None
        return (int(self._base), int(self.rev), {k: np.array(getattr(self, "_" + k), copy=True) for k in self._SAVE_KEYS})

    @staticmethod
    def save_snapshot(snap, path: str) -> bool:
        if snap is None:
            return False
        base, rev, arrs = snap
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                np.savez(fh, base=np.int64(base), rev=np.int64(rev), bin=np.float64(1.0), ver=np.int64(FlowStore._SAVE_VER), **arrs)
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def load(self, path: str, max_age_secs: float = 0.0) -> bool:
        """Replace the bins with a saved set. Rejects a file whose bin width differs or whose newest bin is older
        than `max_age_secs` (0 = any age); the cap prune applies as usual. Bumps rev so every memo re-keys."""
        try:
            with np.load(path) as z:
                if abs(float(z["bin"]) - self.bin) > 1e-9:
                    return False
                if "ver" not in z.files or int(z["ver"]) < self._SAVE_VER:
                    return False                             # pre-fix bins: double-counted spans -> rebuild from the daemon
                base = int(z["base"])
                arrs = {k: np.array(z[k], dtype=np.float64) for k in self._SAVE_KEYS}
        except Exception:
            return False
        n = int(arrs["buy"].shape[0])
        if n == 0 or any(a.shape[0] != n for a in arrs.values()):
            return False
        if max_age_secs > 0:
            import time as _time
            if (base + n) * self.bin < _time.time() - float(max_age_secs):
                return False
        self._base = base
        self._bufs = {k: arrs[k] for k in self._KEYS}
        self._views(0, n)
        if n > self.cap:                                         # keep the NEWEST cap bins
            drop = n - self.cap
            self._views(drop, n - drop)
            self._base += drop
        self.rev += 1
        self._memo = None; self._bmemo = None; self._xmemo = None; self._pxmemo = None
        self.rev_hist += 1
        return True

    def reset(self) -> None:
        self.rev_hist += 1                           # everything a reader rated is behind a tape that is gone
        self._base = None
        self._bufs = None; self._start = 0
        self._buy = np.zeros(0, dtype=np.float64)
        self._sell = np.zeros(0, dtype=np.float64)
        self._px = np.zeros(0, dtype=np.float64)
        self._pxh = np.zeros(0, dtype=np.float64)
        self._pxl = np.zeros(0, dtype=np.float64)
        self._pts = np.zeros(0, dtype=np.float64)
        self.rev += 1
        self._memo = None
        self._bmemo = None
        self._xmemo = None
        self._pxmemo = None

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

    # ------------------------------------------------------- confirmed crosses
    def crosses(self, t0: float, t1: float, win_secs: float, min_spread_pct: float = 10.0,
                min_hold_secs: float = 20.0, max_n: int = 400, context_secs: float = 600.0,
                tick: float = 0.01):
        """Where the two rolling-window flow lines CROSS -- the 8 arrays every pane reads. See _crosses_full."""
        return self._crosses_full(t0, t1, win_secs, min_spread_pct, min_hold_secs, max_n,
                                  context_secs, tick)[:8]

    def crosses_hl(self, t0: float, t1: float, win_secs: float, min_spread_pct: float = 10.0,
                   min_hold_secs: float = 20.0, max_n: int = 400, context_secs: float = 600.0,
                   tick: float = 0.01):
        """(highest price in each cycle, lowest price in each cycle).

        What absorption is actually about: buyers pushing price to the HIGH and then handing it back to the
        close. Called with the SAME arguments as crosses(), so it lands on that call's memo entry.

        Reduced from the store's per-bin TRUE high / low, which are accumulated over every trade at ingest.

        ⚠⚠ These used to come from the per-bin LAST price, on the strength of a measurement I got wrong:
        "exact on 91% of cycles, never more than ONE tick low". Re-measured against the DOM's tick tape over
        6.04 h / 212 cycles, that approach understated the high on 30.2% of cycles, by >=5 ticks on 4.2% and
        by as much as 77 TICKS -- and 22 of 46 Big Player sweeps reached outside the candle they sat on, which
        is how the user found it. On the real extremes the same comparison is exact on 99.5% of cycles, with
        the high never below the tape (the residual 0.5% is one cycle at the window edge).

        ⚠ A cycle owns WHOLE SECONDS: its start second through the second before the next cycle's start second,
        plus its OPEN (the close of the second before it). Any reference that slices a tick tape at the exact
        cycle timestamps instead will disagree on some cycles for that reason alone -- the crossing time sits
        inside the start second and cuts it in half."""
        return self._crosses_full(t0, t1, win_secs, min_spread_pct, min_hold_secs, max_n,
                                  context_secs, tick)[10:]

    def crosses_px(self, t0: float, t1: float, win_secs: float, min_spread_pct: float = 10.0,
                   min_hold_secs: float = 20.0, max_n: int = 400, context_secs: float = 600.0,
                   tick: float = 0.01):
        """(price at each cycle's START, price at its END) -- the two numbers move_ticks is the difference of.
        START = the close of the second before the cycle's start second, i.e. the previous cycle's END.

        Called with the SAME arguments as crosses(), so it lands on that call's memo entry and costs a dict
        lookup rather than a second pass. NaN where that end of the cycle was never priced, exactly where
        move_ticks is NaN."""
        return self._crosses_full(t0, t1, win_secs, min_spread_pct, min_hold_secs, max_n,
                                  context_secs, tick)[8:10]

    def _cross_scan(self, i0: int, iv: int, i1: int, w: int, hold: int, ctx: int, min_spread_pct: float,
                    tick: float):
        """The scan behind _crosses_full over bins [i0 - max(w-1, ctx), i1], clipped to the view [i0, iv]:
        (cs, sg, db, mv, px0, px1, pxh, pxl, vb, vs, ta, te, dn) or None when there is no cycle."""
        z = np.zeros(0)
        zb = np.zeros(0, dtype=bool)
        p0 = max(0, i0 - max(w - 1, ctx))
        cb = np.concatenate([[0.0], np.cumsum(self._buy[p0:i1 + 1])])
        ca = np.concatenate([[0.0], np.cumsum(self._sell[p0:i1 + 1])])
        m = int(cb.size - 1)
        if m < 3:
            return None
        idx = np.arange(m)
        lo = np.maximum(0, idx + 1 - w)
        rb = cb[idx + 1] - cb[lo]
        ra = ca[idx + 1] - ca[lo]
        # Dominance is CARRIED FORWARD through bins that cannot express one (a hole with no trades at all, or an
        # exact tie). Without this a gap in a partially backfilled store reads as buy>=sell and invents a cross.
        up = rb > ra
        dn = ra > rb
        say = up | dn
        if not say.any():
            return None
        dom = up[np.maximum.accumulate(np.where(say, idx, 0))]
        flips = np.flatnonzero(dom[1:] != dom[:-1]) + 1                  # first bin of each new side
        if flips.size == 0:
            return None
        tot = rb + ra
        spread = np.where(tot > 0, 100.0 * np.abs(rb - ra) / np.maximum(tot, 1e-9), 0.0)
        okc = spread >= float(min_spread_pct)
        # Consecutive seconds of `okc` ending at each bin. The streak is FORCED to restart at every cross: a
        # single huge print can carry the spread across a crossing without ever dipping under the threshold, and
        # then the previous cycle's hold would confirm this one.
        brk = ~okc
        brk[flips] = True
        streak = np.where(okc, idx + 1 - np.maximum.accumulate(np.where(brk, idx + 1, 0)), 0)
        qual = streak >= hold
        seg = np.zeros(m, dtype=np.int64)
        seg[flips] = 1
        seg = np.cumsum(seg)                                             # 0 = before the first cross
        cnt = np.bincount(seg[qual], minlength=flips.size + 1) if qual.any() else np.zeros(flips.size + 1,
                                                                                           dtype=np.int64)
        # STRONG = the spread gate was met inside the run. WEAK = the side held for min_hold_secs but the two
        # lines never got min_spread_pct apart. Anything shorter than the hold is not a cycle and is dropped.
        ends = np.concatenate([flips[1:], [m]])
        strong = cnt[1:] > 0
        cyc = strong | ((ends - flips) >= hold)
        fl = flips[cyc]
        sg = strong[cyc]
        db = dom[fl]
        # MERGE runs of the same colour into their FIRST line. Dropping a sub-hold run leaves the crosses on
        # either side of it on the SAME side, and that cycle never ended -- so it must not be marked as starting
        # twice. Weak crosses are all one colour, so a chop of them collapses to one marker too. This runs over
        # the WHOLE read, view and lookback alike, so the answer does not depend on where the view starts.
        if fl.size:
            colr = np.where(sg, np.where(db, 1, 2), 0)               # 0 = gray, 1 = green (buy), 2 = red (sell)
            head = np.empty(colr.size, dtype=bool)
            head[0] = True
            head[1:] = colr[1:] != colr[:-1]
            fl = fl[head]; sg = sg[head]; db = db[head]
        # ONE SECOND BELONGS TO ONE CYCLE (user 2026-09-23, "go with B"). A cycle owns its START second -- the
        # one its lines crossed in -- through the second BEFORE the next cycle's start second (the last bin of
        # tape for the one still forming). Its candle OPENS at the close of the second before its start second
        # (= the previous cycle's close) and CLOSES at its own last second, so the candle and the dollars cover
        # exactly the same trades. ⚠ It used to open at the start second's CLOSE while counting that second's
        # dollars, and the boundary second was counted in BOTH cycles: the print that makes the lines cross
        # sits in that second, so each cycle was credited with its trigger's dollars but not its price move
        # (21:22:39 on 2026-09-23: a $582k sell sweep, 114.18 -> 114.05 in one second, counted in a green
        # candle that opened at 114.05 -- "sellers dominate, price rose").
        # The price is carried over tradeless seconds, and a cycle whose ends were never priced at all reports
        # NaN rather than a fake 0.
        px = self._px[p0:i1 + 1].copy()
        pv = px > 0
        if pv.any():
            px = px[np.maximum.accumulate(np.where(pv, np.arange(m), 0))]
            pv = pv[np.maximum.accumulate(np.where(pv, np.arange(m), 0))]
        fin = np.concatenate([fl[1:] - 1, [m - 1]]) if fl.size else np.zeros(0, dtype=np.int64)
        if fl.size:
            op = fl - 1                                    # the second before the start (flips are >= 1)
            _ok = pv[op] & pv[fin]
            mv = np.where(_ok, (px[fin] - px[op]) / float(tick), np.nan)
            # the two prices the move is the difference of, kept rather than discarded: the feed prints the
            # cycle as "100.01 -> 97.30". NaN exactly where move_ticks is NaN, so they can never disagree.
            # One cycle's close IS the next one's open (px[fin[k]] == px[op[k + 1]]).
            px0_ = np.where(_ok, px[op], np.nan)
            px1_ = np.where(_ok, px[fin], np.nan)
            # the cycle's HIGH and LOW, from the per-bin TRUE extremes -- NOT from the closes. The spans are
            # contiguous and disjoint (fin[k] == fl[k+1] - 1), so reduceat's HALF-OPEN segments are exactly the
            # cycles, O(n). The OPEN is folded in: it is a real trade (the second before), and without it a
            # cycle that gapped away from the previous close would draw a body outside its own wick.
            # ⚠ an untraded bin holds 0 in both arrays, which would drag every low to 0: mask those to the
            # bin's own close (itself 0 there, so the fill in _filled/price_series still governs what is
            # DRAWN) by taking the max against _pxl's positive entries only.
            # ⚠ p0, NOT i0: `px` above is sliced from p0 (it needs the rolling window's prefix) and fl / fin
            # index into THAT array. Slicing the extremes from i0 instead shifts every wick by (i0 - p0) bins.
            _hh = self._pxh[p0:i1 + 1]
            _ll = self._pxl[p0:i1 + 1]
            _ll = np.where(_ll > 0, _ll, np.inf)               # untraded bins must not pull the low to zero
            _hi = np.maximum.reduceat(_hh, fl)
            _lo = np.minimum.reduceat(_ll, fl)
            pxh_ = np.where(_ok, np.maximum(_hi, px[op]), np.nan)
            pxl_ = np.minimum(_lo, np.where(pv[op], px[op], np.inf))
            pxl_ = np.where(_ok & np.isfinite(pxl_), pxl_, np.nan)
            # the dollars each side traded INSIDE the cycle -- exactly the seconds the candle covers, each second
            # in one cycle only
            vb_ = cb[fin + 1] - cb[fl]
            vs_ = ca[fin + 1] - ca[fl]
        else:
            mv = np.zeros(0); vb_ = np.zeros(0); vs_ = np.zeros(0)
            px0_ = np.zeros(0); px1_ = np.zeros(0)
            pxh_ = np.zeros(0); pxl_ = np.zeros(0)
        # The exact crossing time of EVERY head, before the view clip: a bar's right edge is the NEXT head's
        # crossing, and that one can sit outside the view -- taking it from the clipped set would make the
        # rightmost bar stop at the screen edge whenever the user pans.
        if fl.size:
            _d0 = (rb - ra)[np.maximum(fl - 1, 0)]
            _d1 = (rb - ra)[fl]
            _dd = _d1 - _d0
            _fr = np.where(np.abs(_dd) > 1e-12, np.clip(-_d0 / np.where(_dd == 0, 1.0, _dd), 0.0, 1.0), 0.0)
            _fr = np.where(fl > 0, _fr, 0.0)
            t_all = (self._base + p0 + fl - 1) * self.bin + self.bin + _fr * self.bin
            # ... and the end: the next head's crossing, or where the tape runs out for the one still open
            t_end_all = np.concatenate([t_all[1:], [(self._base + p0 + m - 1) * self.bin + self.bin]])
            done_all = np.ones(fl.size, dtype=bool)
            done_all[-1] = False                                     # nothing crossed back yet -> still forming
        else:
            t_all = z; t_end_all = z; done_all = zb
        vis = (fl >= (i0 - p0)) & (fl <= (iv - p0))                  # ... and only now clip to the view
        cs = fl[vis]
        sg = sg[vis]
        db = db[vis]
        mv = mv[vis]
        px0_ = px0_[vis]
        px1_ = px1_[vis]
        pxh_ = pxh_[vis]
        pxl_ = pxl_[vis]
        vb_ = vb_[vis]
        vs_ = vs_[vis]
        ta_ = t_all[vis]
        te_ = t_end_all[vis]
        dn_ = done_all[vis]
        return (cs, sg, db, mv, px0_, px1_, pxh_, pxl_, vb_, vs_, ta_, te_, dn_)

    def _crosses_full(self, t0: float, t1: float, win_secs: float, min_spread_pct: float = 10.0,
                      min_hold_secs: float = 20.0, max_n: int = 400, context_secs: float = 600.0,
                      tick: float = 0.01):
        """Where the two rolling-window flow lines CROSS, keeping only the crosses that opened a real cycle.

        Returns (t_cross, is_buy, strong, move_ticks, buy_usd, sell_usd, t_end, done, px_start, px_end,
        px_high, px_low).

          is_buy  True where the BUY line took the top.
          strong  the cycle CONFIRMED: before the next cross the spread |buy-sell|/(buy+sell) reached
                  `min_spread_pct` and stayed there for `min_hold_secs` consecutive seconds.
          not strong  the side held for `min_hold_secs` but never got that far apart -- a real cycle, a weak one
                  (user 2026-09-10 asked for these back, drawn in gray rather than dropped).
          move_ticks  how far PRICE travelled over that cycle -- from the close of the second before its cross
                  to the close of the second before the NEXT cross, or to the last bin of tape for the one still
                  forming. Its sign is the price's, not the side's: a buy cycle that ends below where it started
                  is negative. NaN where no trade priced either end.
          buy_usd / sell_usd  taker dollars each side traded INSIDE that cycle, over exactly the seconds the
                  move covers -- its cross's second through the second before the next cross, each second in
                  ONE cycle. The caller decides which one to hold the move against.
          t_end   where the cycle ENDED -- the NEXT cross's own interpolated crossing, so one cycle's right edge
                  is the next one's left edge and both sit exactly on the vertical line drawn there.
          done    False only for the cycle still open at the end of the read, i.e. the one still forming when
                  following the live edge. Anything drawn per-cycle should wait for this.

        A run shorter than `min_hold_secs` is not a cycle at all and never comes back -- and because it is
        dropped, the crosses on either side of it are the SAME colour. Two or more consecutive same-colour
        crosses are MERGED into the first (user 2026-09-10): that cycle never really ended, so it may not be
        marked as starting twice. Consecutive weak (gray) crosses collapse the same way, into one marker at the
        head of the indecisive stretch. `context_secs` is how much tape is read on EITHER side of the view:
        backwards so the leftmost visible cross knows whether it continues a run that starts off-screen,
        forwards so the last visible cycle can find its real END to measure the move over (and so a cross near
        the right edge can still be confirmed).

        The time returned is the cross itself, not the confirmation, LINEARLY INTERPOLATED between the two bins
        that straddle it so it lands on the actual intersection.

        `win_secs` must be the window the visible lines use, or the crosses will not sit on the crossings the
        user can see -- everything below mirrors series() bin for bin. Vectorised and memoized."""
        z = np.zeros(0)
        zb = np.zeros(0, dtype=bool)
        if self.empty() or win_secs <= 0 or tick <= 0:
            return (z, zb, zb, z, z, z, z, zb, z, z, z, z)
        key = ("cross", self.rev, round(float(t0), 2), round(float(t1), 2), round(float(win_secs), 2),
               round(float(min_spread_pct), 3), round(float(min_hold_secs), 2), int(max_n),
               round(float(context_secs), 2), round(float(tick), 6))
        # a SMALL LRU, not one slot: the vertical lines read the view while the Volume pane reads an hour
        # further back for its baseline, and with a single slot those two keys would evict each other every
        # frame, so every call would be a cold one.
        memo = getattr(self, "_xmemo", None)
        if not isinstance(memo, dict):
            memo = {}
            self._xmemo = memo
        hit = memo.get(key)
        if hit is not None:
            return hit
        n = len(self._buy)
        w = max(1, int(round(float(win_secs) / self.bin)))
        hold = max(1, int(round(float(min_hold_secs) / self.bin)))
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        iv = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)      # the view's last bin
        ctx = max(hold + 1, int(round(max(0.0, float(context_secs)) / self.bin)))
        # read PAST the view: a cross just left of the right edge is confirmed by bins the view does not cover
        # (and must not wink out because the user panned), and the last visible cycle needs its real END to
        # measure the move over.
        i1 = min(n - 1, iv + ctx)
        if iv < i0:
            out = (z, zb, zb, z, z, z, z, zb, z, z, z, z)
            self._memo_put(memo, key, out)
            return out
        # series() only needs a w-1 prefix; the MERGE needs enough of the run before the view to know whether
        # the leftmost visible cross is its own head or a repeat of the colour before it.
        # ⚠ BOUNDED SCAN (2026-09-14). A capped read returns the NEWEST max_n cycles of its view, yet the scan
        # ran over the whole view + lookback + context: 177k bins on a two-day view, ~30 ms cold, and three
        # distinct reader keys re-scanned on every live batch. Cross detection is local beyond the rolling
        # window + context, so a window holding >= max_n + 16 cycles gives the newest max_n EXACTLY (only its
        # first cycles can differ, and a gate holds every array equal to the full scan): try the last 8 h,
        # then 24 h, then the whole range. An uncapped read (max_n >= 1e6) always scans everything.
        res = None
        if 0 < int(max_n) < 10 ** 6:
            # the first window is a HINT learnt from the last capped read of this size (the tape's cycle
            # density changes with the session): what sufficed last time, shrunk when it held twice what was
            # needed; then 2.5x that; then the whole range
            _need = int(max_n) + 16
            _hints = getattr(self, "_scan_hint", None)
            if not isinstance(_hints, dict):
                _hints = {}
                self._scan_hint = _hints
            _h0 = float(_hints.get(int(max_n), 10.0 * 3600.0))
            for _secs in (_h0, _h0 * 2.5):
                _cut = int(iv - _secs / self.bin)
                if _cut <= i0 + 2 * ctx + w:
                    break                                   # the narrow window is nearly the whole read
                _r = self._cross_scan(_cut, iv, i1, w, hold, ctx, min_spread_pct, tick)
                _k = 0 if _r is None else int(_r[0].size)
                if _k >= _need:
                    res = _r
                    _hints[int(max_n)] = _secs * 0.6 if _k >= 2 * _need else _secs
                    break
                if _k >= 8:                                 # too few: size the next try from the density seen
                    _h0 = max(_secs * 2.5, _secs * (_need / float(_k)) * 1.3)
        if res is None:
            res = self._cross_scan(i0, iv, i1, w, hold, ctx, min_spread_pct, tick)
        if res is None:
            out = (z, zb, zb, z, z, z, z, zb, z, z, z, z)
            self._memo_put(memo, key, out)
            return out
        cs, sg, db, mv, px0_, px1_, pxh_, pxl_, vb_, vs_, ta_, te_, dn_ = res
        if cs.size == 0:
            out = (z, zb, zb, z, z, z, z, zb, z, z, z, z)
            self._memo_put(memo, key, out)
            return out
        if cs.size > max_n:
            cs = cs[-int(max_n):]
            sg = sg[-int(max_n):]
            db = db[-int(max_n):]
            mv = mv[-int(max_n):]
            px0_ = px0_[-int(max_n):]
            px1_ = px1_[-int(max_n):]
            pxh_ = pxh_[-int(max_n):]
            pxl_ = pxl_[-int(max_n):]
            vb_ = vb_[-int(max_n):]
            vs_ = vs_[-int(max_n):]
            ta_ = ta_[-int(max_n):]
            te_ = te_[-int(max_n):]
            dn_ = dn_[-int(max_n):]
        out = (ta_.copy(), db.copy(), sg.copy(), mv.copy(), vb_.copy(), vs_.copy(),
               te_.copy(), dn_.copy(), px0_.copy(), px1_.copy(), pxh_.copy(), pxl_.copy())
        self._memo_put(memo, key, out)
        return out

    @staticmethod
    def _memo_put(memo, key, out, cap=8):
        """Keep the last few crosses() answers -- the ranges the terminal asks for (the four view-following
        panes share one, the feed and the cross lines have their own) at both the current flow window and one
        the user just switched away from, PLUS the PRICE pane's fill reads (one new key per tick while a wide
        view fills; 4 slots let those evict the shared entry and force a cold read every tick)."""
        memo[key] = out
        while len(memo) > cap:
            memo.pop(next(iter(memo)))
        return out

    @staticmethod
    def volume_ratio(is_dom_buy, buy_usd, sell_usd, done, dur_secs, n_base=5, min_n=3,
                     per_second=False, min_usd=20_000.0):
        """Each finished cycle's dominant-side volume over the MEDIAN of that side's previous `n_base` cycles.

        NaN where the history is too short or the cycle traded nothing worth rating. Median, not mean, because
        at n_base=5 a single outsized cycle would drag a mean around completely.

        Pure arithmetic on what crosses() already returned, so the caller can hand it the PRE-CLIP arrays and
        get a baseline that does not change when the view moves -- the previous five cycles of one side reach
        much further back than the drawn range.

        ⚠ Measured on 20 h of live tape: 52% of this ratio's variance is shared with how long the cycle ran.
        `per_second` divides by duration to remove that, which asks a different question (how INTENSE was the
        flow, not how much of it there was)."""
        n = int(np.size(done))
        out = np.full(n, np.nan)
        if n == 0:
            return out
        vol = np.where(np.asarray(is_dom_buy, dtype=bool),
                       np.asarray(buy_usd, dtype=np.float64), np.asarray(sell_usd, dtype=np.float64))
        if per_second:
            vol = vol / np.maximum(np.asarray(dur_secs, dtype=np.float64), 1e-9)
        dn = np.asarray(done, dtype=bool)
        db = np.asarray(is_dom_buy, dtype=bool)
        ok = dn & np.isfinite(vol) & (vol > 0)
        floor = float(min_usd) / (np.maximum(np.asarray(dur_secs, dtype=np.float64), 1e-9) if per_second else 1.0)
        hist = {True: [], False: []}
        nb = max(1, int(n_base))
        mn = max(1, int(min_n))
        for k in range(n):
            if not ok[k]:
                continue
            h = hist[bool(db[k])]
            if len(h) >= mn and vol[k] >= (floor[k] if per_second else floor):
                base = float(np.median(h[-nb:]))
                if base > 0:
                    out[k] = float(vol[k]) / base
            h.append(float(vol[k]))
        return out

    # ----------------------------------------------------------------- read
    def _filled(self, i0: int, i1: int):
        """(t, price) for bin indices i0..i1 with unpriced bins FORWARD-FILLED and leading blanks dropped.

        Shared by price_series and candles so the two can never disagree about what the price was. A price
        persists until the next print, so a quiet stretch is flat; the fill is seeded from the last print
        BEFORE the window (bounded to an hour of look-back, so this never walks the whole 72 h array), and
        bins before the first print anywhere are dropped rather than drawn at zero."""
        px = self._px[i0:i1 + 1]
        if px.size == 0:
            return (np.zeros(0), np.zeros(0))
        idx = np.where(px > 0, np.arange(px.size), -1)
        np.maximum.accumulate(idx, out=idx)
        seed = 0.0
        if idx[0] < 0:
            _lo = max(0, i0 - 3600)
            _prev = np.flatnonzero(self._px[_lo:i0])
            if _prev.size:
                seed = float(self._px[_lo + _prev[-1]])
        out = np.where(idx >= 0, px[np.maximum(idx, 0)], seed)
        t = (self._base + np.arange(i0, i1 + 1)) * self.bin + self.bin
        keep = out > 0
        if not keep.all():
            t = t[keep]; out = out[keep]
        return (t, out)

    def price_series(self, t0: float, t1: float, max_pts: int = 2400):
        """(t, price) for the bins inside [t0, t1] -- the LAST trade price in each one.

        Unpriced bins are forward-filled by _filled, which candles() shares.

        Decimated MIN/MAX per bucket, NOT by striding. `series()` can afford to stride because its rolling sums
        are already smoothed, but striding a price line deletes precisely the highs and lows a reader is looking
        for. Two points per bucket, emitted in their true time order, keep the envelope exact.

        MEMOIZED in its OWN slot -- sharing series()' slot would make the two evict each other every frame."""
        if self.empty():
            return (np.zeros(0), np.zeros(0))
        key = (self.rev, round(float(t0), 3), round(float(t1), 3), int(max_pts))
        if self._pxmemo is not None and self._pxmemo[0] == key:
            return self._pxmemo[1]
        n = len(self._px)
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        i1 = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)
        _empty = (np.zeros(0), np.zeros(0))
        if i1 < i0:
            self._pxmemo = (key, _empty)
            return _empty
        t, out_px = self._filled(i0, i1)     # forward-filled, leading blanks dropped -- see _filled
        if t.size == 0:
            self._pxmemo = (key, _empty)
            return _empty
        step = int(np.ceil(t.size / float(max(8.0, max_pts / 2.0))))
        if step > 1 and t.size > 4:
            nb = t.size // step
            if nb >= 1:
                B = out_px[:nb * step].reshape(nb, step)
                T = t[:nb * step].reshape(nb, step)
                r = np.arange(nb)
                amin = B.argmin(1); amax = B.argmax(1)
                a = np.minimum(amin, amax); b = np.maximum(amin, amax)   # true time order inside the bucket
                xs = np.empty(nb * 2); ys = np.empty(nb * 2)
                xs[0::2] = T[r, a]; xs[1::2] = T[r, b]
                ys[0::2] = B[r, a]; ys[1::2] = B[r, b]
                if nb * step < t.size:                                   # the ragged tail, undecimated
                    xs = np.concatenate([xs, t[nb * step:]])
                    ys = np.concatenate([ys, out_px[nb * step:]])
                t, out_px = xs, ys
        out = (t, out_px)
        self._pxmemo = (key, out)
        return out

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
