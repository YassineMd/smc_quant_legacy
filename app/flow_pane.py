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
                min_hold_secs: float = 20.0, max_n: int = 400, context_secs: float = 600.0,
                tick: float = 0.01):
        """Where the two rolling-window flow lines CROSS, keeping only the crosses that opened a real cycle.

        Returns (t_cross, is_buy, strong, move_ticks, buy_usd, sell_usd, t_end, done).

          is_buy  True where the BUY line took the top.
          strong  the cycle CONFIRMED: before the next cross the spread |buy-sell|/(buy+sell) reached
                  `min_spread_pct` and stayed there for `min_hold_secs` consecutive seconds.
          not strong  the side held for `min_hold_secs` but never got that far apart -- a real cycle, a weak one
                  (user 2026-09-10 asked for these back, drawn in gray rather than dropped).
          move_ticks  how far PRICE travelled over that cycle -- from this cross to the NEXT one, or to the last
                  bin of tape for the one still forming. Its sign is the price's, not the side's: a buy cycle
                  that ends below where it started is negative. NaN where no trade priced either end.
          buy_usd / sell_usd  taker dollars each side traded INSIDE that cycle, over the same span the move is
                  measured across. The caller decides which one to hold the move against.
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
            return (z, zb, zb, z, z, z, z, zb)
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
            out = (z, zb, zb, z, z, z, z, zb)
            self._memo_put(memo, key, out)
            return out
        # series() only needs a w-1 prefix; the MERGE needs enough of the run before the view to know whether
        # the leftmost visible cross is its own head or a repeat of the colour before it.
        p0 = max(0, i0 - max(w - 1, ctx))
        cb = np.concatenate([[0.0], np.cumsum(self._buy[p0:i1 + 1])])
        ca = np.concatenate([[0.0], np.cumsum(self._sell[p0:i1 + 1])])
        m = int(cb.size - 1)
        if m < 3:
            out = (z, zb, zb, z, z, z, z, zb)
            self._memo_put(memo, key, out)
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
            out = (z, zb, zb, z, z, z, z, zb)
            self._memo_put(memo, key, out)
            return out
        dom = up[np.maximum.accumulate(np.where(say, idx, 0))]
        flips = np.flatnonzero(dom[1:] != dom[:-1]) + 1                  # first bin of each new side
        if flips.size == 0:
            out = (z, zb, zb, z, z, z, z, zb)
            self._memo_put(memo, key, out)
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
        # How far price travelled over each cycle: this head -> the NEXT head, or the last bin of tape for the
        # one still forming. The price is carried over tradeless seconds, and a cycle whose ends were never
        # priced at all reports NaN rather than a fake 0.
        px = self._px[p0:i1 + 1].copy()
        pv = px > 0
        if pv.any():
            px = px[np.maximum.accumulate(np.where(pv, np.arange(m), 0))]
            pv = pv[np.maximum.accumulate(np.where(pv, np.arange(m), 0))]
        fin = np.concatenate([fl[1:], [m - 1]]) if fl.size else np.zeros(0, dtype=np.int64)
        if fl.size:
            mv = np.where(pv[fl] & pv[fin], (px[fin] - px[fl]) / float(tick), np.nan)
            # the dollars each side traded INSIDE the cycle, over the same span the move is measured across
            vb_ = cb[fin + 1] - cb[fl]
            vs_ = ca[fin + 1] - ca[fl]
        else:
            mv = np.zeros(0); vb_ = np.zeros(0); vs_ = np.zeros(0)
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
        vb_ = vb_[vis]
        vs_ = vs_[vis]
        ta_ = t_all[vis]
        te_ = t_end_all[vis]
        dn_ = done_all[vis]
        if cs.size == 0:
            out = (z, zb, zb, z, z, z, z, zb)
            self._memo_put(memo, key, out)
            return out
        if cs.size > max_n:
            cs = cs[-int(max_n):]
            sg = sg[-int(max_n):]
            db = db[-int(max_n):]
            mv = mv[-int(max_n):]
            vb_ = vb_[-int(max_n):]
            vs_ = vs_[-int(max_n):]
            ta_ = ta_[-int(max_n):]
            te_ = te_[-int(max_n):]
            dn_ = dn_[-int(max_n):]
        out = (ta_.copy(), db.copy(), sg.copy(), mv.copy(), vb_.copy(), vs_.copy(),
               te_.copy(), dn_.copy())
        self._memo_put(memo, key, out)
        return out

    @staticmethod
    def _memo_put(memo, key, out, cap=4):
        """Keep the last few crosses() answers -- enough for the two ranges the terminal asks for, at both the
        current flow window and one the user just switched away from."""
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
