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
        self._cmemo = None
        self._ccmemo = None
        self._xmemo = None

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
        self._cmemo = None
        self._ccmemo = None
        self._xmemo = None
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
        self._cmemo = None
        self._ccmemo = None
        self._xmemo = None

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
                min_hold_secs: float = 20.0, max_n: int = 400):
        """Where the two rolling-window flow lines CROSS, keeping only the crosses that opened a real cycle.

        Returns (t_cross, is_buy, strong).

          is_buy  True where the BUY line took the top.
          strong  the cycle CONFIRMED: before the next cross the spread |buy-sell|/(buy+sell) reached
                  `min_spread_pct` and stayed there for `min_hold_secs` consecutive seconds.
          not strong  the side held for `min_hold_secs` but never got that far apart -- a real cycle, a weak one
                  (user 2026-09-10 asked for these back, drawn in gray rather than dropped).

        A run shorter than `min_hold_secs` is not a cycle at all and never comes back. The time returned is the
        cross itself, not the confirmation, LINEARLY INTERPOLATED between the two bins that straddle it so it
        lands on the actual intersection.

        `win_secs` must be the window the visible lines use, or the crosses will not sit on the crossings the
        user can see -- everything below mirrors series() bin for bin. Vectorised and memoized."""
        z = np.zeros(0)
        zb = np.zeros(0, dtype=bool)
        if self.empty() or win_secs <= 0:
            return (z, zb, zb)
        key = ("cross", self.rev, round(float(t0), 2), round(float(t1), 2), round(float(win_secs), 2),
               round(float(min_spread_pct), 3), round(float(min_hold_secs), 2), int(max_n))
        memo = getattr(self, "_xmemo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        n = len(self._buy)
        w = max(1, int(round(float(win_secs) / self.bin)))
        hold = max(1, int(round(float(min_hold_secs) / self.bin)))
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        iv = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)      # the view's last bin
        # read PAST the view by the hold window: a cross just left of the right edge is confirmed by bins the
        # view does not cover, and it must not wink out just because the user panned.
        i1 = min(n - 1, iv + hold + 1)
        if iv < i0:
            out = (z, zb, zb)
            self._xmemo = (key, out)
            return out
        p0 = max(0, i0 - w + 1)                                          # same prefix series() takes
        cb = np.concatenate([[0.0], np.cumsum(self._buy[p0:i1 + 1])])
        ca = np.concatenate([[0.0], np.cumsum(self._sell[p0:i1 + 1])])
        m = int(cb.size - 1)
        if m < 3:
            out = (z, zb, zb)
            self._xmemo = (key, out)
            return out
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
            out = (z, zb, zb)
            self._xmemo = (key, out)
            return out
        dom = up[np.maximum.accumulate(np.where(say, idx, 0))]
        flips = np.flatnonzero(dom[1:] != dom[:-1]) + 1                  # first bin of each new side
        if flips.size == 0:
            out = (z, zb, zb)
            self._xmemo = (key, out)
            return out
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
        keep = (strong | ((ends - flips) >= hold)) & (flips >= (i0 - p0)) & (flips <= (iv - p0))
        cs = flips[keep]
        sg = strong[keep]
        if cs.size == 0:
            out = (z, zb, zb)
            self._xmemo = (key, out)
            return out
        if cs.size > max_n:
            cs = cs[-int(max_n):]
            sg = sg[-int(max_n):]
        # the EXACT cross: where buy-sell changes sign between bin cs-1 and bin cs, on the same straight segment
        # the curve draws between those two points.
        d0 = (rb - ra)[cs - 1]
        d1 = (rb - ra)[cs]
        den = d1 - d0
        frac = np.where(np.abs(den) > 1e-12, np.clip(-d0 / np.where(den == 0, 1.0, den), 0.0, 1.0), 0.0)
        t_prev = (self._base + p0 + cs - 1) * self.bin + self.bin        # series() stamps each bin at its END
        out = (t_prev + frac * self.bin, dom[cs].copy(), sg.copy())
        self._xmemo = (key, out)
        return out

    # ------------------------------------------------------------- cycles
    def cycle_curve(self, t0: float, t1: float, win_secs: float, tick: float, max_pts: int = 3000):
        """The RUNNING advance of the cycle in progress, sampled continuously.

        Returns (t, val, is_buy, dom_usd) where `val` is the ticks price has moved since the CURRENT cycle
        began, signed
        toward the side winning that cycle so far, and `is_buy` says which side that is. The series resets to 0
        at every boundary, so a cycle BUILDS on screen instead of appearing whole once it has ended -- and the
        forming one is live, not provisional.

        The side is the run's OWN totals UP TO each point (causal: it uses only what has traded so far), which
        is the same definition cycles() applies to the finished run. Vectorised over the 1 s bins; decimated to
        `max_pts`. `dom_usd` is the winning side's RUNNING dollars in the cycle so far, so the caller can draw
        the "what that size normally buys" line building alongside."""
        z = np.zeros(0)
        if self.empty() or win_secs <= 0:
            return (z, z, np.zeros(0, dtype=bool), z)
        key = ("curve", self.rev, round(float(t0), 2), round(float(t1), 2), round(float(win_secs), 2),
               int(max_pts))
        memo = getattr(self, "_ccmemo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        n = len(self._buy)
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        i1 = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)
        w = max(1, int(round(float(win_secs) / self.bin)))
        p0 = max(0, i0 - w)
        b = self._buy[p0:i1 + 1]; a = self._sell[p0:i1 + 1]
        m = b.size
        if m < 3:
            out = (z, z, np.zeros(0, dtype=bool), z)
            self._ccmemo = (key, out)
            return out
        cb = np.concatenate([[0.0], np.cumsum(b)]); ca = np.concatenate([[0.0], np.cumsum(a)])
        idx = np.arange(m)
        lo = np.maximum(0, idx + 1 - w)
        dom = (cb[idx + 1] - cb[lo]) >= (ca[idx + 1] - ca[lo])       # the rolling flag sets the BOUNDARY
        flip = np.empty(m, dtype=bool); flip[0] = True; flip[1:] = dom[1:] != dom[:-1]
        start = np.maximum.accumulate(np.where(flip, idx, 0))        # index where the current run began
        px = self._px[p0:i1 + 1].copy()
        have = px > 0
        if not have.any():
            out = (z, z, np.zeros(0, dtype=bool), z)
            self._ccmemo = (key, out)
            return out
        px = px[np.maximum.accumulate(np.where(have, np.arange(m), 0))]
        move = (px - px[start]) / float(tick)                        # ticks since the cycle began
        run_b = cb[idx + 1] - cb[start]                              # the run's OWN totals so far -> the side
        run_a = ca[idx + 1] - ca[start]
        is_buy = run_b >= run_a
        val = np.where(is_buy, move, -move)                          # signed toward the side winning it
        off = i0 - p0                                                # drop the window's warm-up prefix
        dom_usd = np.where(is_buy, run_b, run_a)
        val = val[off:]; is_buy = is_buy[off:]; dom_usd = dom_usd[off:]
        t = (self._base + i0 + np.arange(val.size)) * self.bin + self.bin
        step = max(1, int(np.ceil(val.size / float(max(16, max_pts)))))
        if step > 1:
            t = t[::step]; val = val[::step]; is_buy = is_buy[::step]; dom_usd = dom_usd[::step]
        out = (t, val, is_buy, dom_usd)
        self._ccmemo = (key, out)
        return out

    def cycles(self, t0: float, t1: float, win_secs: float, tick: float,
               min_secs: float = 3.0, max_cycles: int = 600):
        """Runs where the same side owns the `win_secs` rolling flow.

        Returns (start_t, end_t, is_buy, dom_usd, dur_s, advance_ticks) — advance is signed toward the DOMINANT
        side, so positive means that side got its way. Only the NEWEST `max_cycles` are returned.

        The BOUNDARY is the part that carries information: measured over 72 h, advance ~ dom$ + duration scores
        R2 0.377 against 0.205 for a random cut of the same lengths. Note a cycle's totals only exist once it has
        ENDED, so the last (still-forming) run is returned too and the caller draws it as provisional."""
        z = np.zeros(0)
        if self.empty() or win_secs <= 0:
            return (z, z, np.zeros(0, dtype=bool), z, z, z)
        key = (self.rev, round(float(t0), 2), round(float(t1), 2), round(float(win_secs), 2),
               round(float(min_secs), 2), int(max_cycles))
        if self._cmemo is not None and self._cmemo[0] == key:
            return self._cmemo[1]
        n = len(self._buy)
        i0 = max(0, int(np.floor(t0 / self.bin)) - self._base)
        i1 = min(n - 1, int(np.floor(t1 / self.bin)) - self._base)
        w = max(1, int(round(float(win_secs) / self.bin)))
        p0 = max(0, i0 - w)                      # the window needs its own prefix to be correct at the left edge
        b = self._buy[p0:i1 + 1]; a = self._sell[p0:i1 + 1]
        m = b.size
        if m < 3:
            out = (z, z, np.zeros(0, dtype=bool), z, z, z)
            self._cmemo = (key, out)
            return out
        cb = np.concatenate([[0.0], np.cumsum(b)]); ca = np.concatenate([[0.0], np.cumsum(a)])
        idx = np.arange(m)
        lo = np.maximum(0, idx + 1 - w)
        dom = (cb[idx + 1] - cb[lo]) >= (ca[idx + 1] - ca[lo])
        off = i0 - p0                            # drop the prefix: it exists only to warm the rolling sums
        dom = dom[off:]
        k = dom.size
        if k < 3:
            out = (z, z, np.zeros(0, dtype=bool), z, z, z)
            self._cmemo = (key, out)
            return out
        brk = np.flatnonzero(np.diff(dom)) + 1
        st = np.concatenate([[0], brk]).astype(np.int64)
        en = np.concatenate([brk - 1, [k - 1]]).astype(np.int64)
        dur = (en - st + 1).astype(np.float64) * self.bin
        # A cycle must hold at least `min_secs` of ACTUAL TRADING. Without this, an un-backfilled HOLE (where
        # buy == sell == 0, so `rb >= ra` is trivially True) becomes one enormous "buy cycle" with a huge
        # duration and no volume -- precisely the shape the size/duration reading keys on. Measured: it dropped
        # the in-app calibration from R2 0.455 to 0.187 and broke the top quintile's monotonicity.
        act = np.concatenate([[0], np.cumsum(((b + a) > 0).astype(np.int64))])
        n_act = act[en + off + 1] - act[st + off]
        keep = (dur >= float(min_secs)) & (n_act >= max(1, int(min_secs / self.bin)))
        st, en, dur = st[keep], en[keep], dur[keep]
        if st.size == 0:
            out = (z, z, np.zeros(0, dtype=bool), z, z, z)
            self._cmemo = (key, out)
            return out
        if st.size > max_cycles:
            st, en, dur = st[-max_cycles:], en[-max_cycles:], dur[-max_cycles:]
        gs = st + off; ge = en + off             # back into the prefixed slice
        vb = cb[ge + 1] - cb[gs]; va = ca[ge + 1] - ca[gs]
        is_buy = vb >= va
        dom_usd = np.where(is_buy, vb, va)
        px = self._px[p0:i1 + 1].copy()          # price at the boundaries, carried over tradeless seconds
        have = px > 0
        if not have.any():
            out = (z, z, np.zeros(0, dtype=bool), z, z, z)
            self._cmemo = (key, out)
            return out
        px = px[np.maximum.accumulate(np.where(have, np.arange(px.size), 0))]
        move = (px[ge] - px[gs]) / float(tick)
        adv = np.where(is_buy, move, -move)
        base_t = (self._base + i0) * self.bin
        out = (base_t + st * self.bin, base_t + (en + 1) * self.bin, is_buy, dom_usd, dur, adv)
        self._cmemo = (key, out)
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
