package com.smc.domtape;

import java.util.HashMap;

/**
 * Shared trade + book store, the Android port of the state both terminal panels keep:
 * trades at TICK resolution in parallel time-ordered arrays (dom_panel._chunks/_trades_cat)
 * plus the latest 0.4s pulse book. All access synchronized.
 *
 * Dedupe mirrors the terminal exactly: the backfill window keeps only rows OLDER than the
 * first live trade; live batches drop anything at/before the store's end (no aggTrade ids
 * on the wire, so the boundary is cut by timestamp).
 *
 * 2026-09-06 (lag fix): the store carries a {@code version} (bumped on ANY data change: book, live
 * batch, backfill) so the UI redraws only when something arrived, an {@code epoch} (bumped on every
 * STRUCTURAL change: reset, backfill prepend, prune shift) so the incremental {@link DomAgg} knows its
 * store indices went stale, a listener the feed thread pokes after each ingest (event-driven frames),
 * and {@link #aggregate} — the O(delta) window update that replaced the per-frame full scans.
 */
public class TradeStore {

    public static final double TICK = 0.01;
    public static final long WINDOW_MS = (21_600 + 300) * 1000L;   // 6h VP + prune slack

    // parallel trade arrays, time-ordered ascending (ms / int tick / SOL split by aggressor)
    private long[] tsMs = new long[1 << 14];
    private long[] tick = new long[1 << 14];
    private double[] buyQ = new double[1 << 14];
    private double[] sellQ = new double[1 << 14];
    private int n = 0;
    private long liveT0Ms = 0;
    private long customKeepMs = 0;                 // custom-VP start (epoch ms); 0 = presets only

    private double[][] bids = new double[0][];     // [price, qty] best-first
    private double[][] asks = new double[0][];
    private double lastPx = 0.0;
    private int lastSide = -1;                     // 1 taker buy / 0 taker sell / -1 unknown
    private boolean connected;

    private long version = 0;                      // any data change
    private long epoch = 0;                        // structural change (indices moved)
    private volatile Runnable listener;            // poked (on the feed thread) after each ingest

    // CAMPAIGNS (user 2026-09-07): the merge unit tracked INCREMENTALLY so the DOM can mark, per frame, the levels
    // where a player STARTED without re-scanning trades. A campaign = same-side fills each within CAMPAIGN_MS of the
    // previous SAME-SIDE fill (other-side fills in between do not break it, neither does a price reversal: a refill
    // taken again is the fight); it is "merged" once it has >= 2 fills spanning >= 1 tick. Only merged campaigns are
    // kept (parallel arrays, in the order they became merged -- NOT time-sorted: two sides interleave): first/last ms,
    // side, first tick, lo/hi tick, usd, n. Open chains per side in c*[side]; cG[side] = merged index or -1.
    private long[] gT0 = new long[256], gT1 = new long[256], gTick0 = new long[256], gLo = new long[256], gHi = new long[256];
    private double[] gUsd = new double[256];
    private int[] gN = new int[256];
    private byte[] gSide = new byte[256];
    private int gCount = 0;
    private final long[] cT0 = new long[2], cT1 = new long[2], cTk0 = new long[2], cLo = new long[2], cHi = new long[2];
    private final double[] cUsd = new double[2];
    private final int[] cN = new int[2], cG = {-1, -1};

    // tapeRows memo: the same frame asked again (no new data, same filter / scroll / height) is free
    private double[][] tapeMemo;
    private long tapeMemoVer = -1;
    private double tapeMemoMin;
    private int tapeMemoSkip, tapeMemoMax;

    // ── change tracking ─────────────────────────────────────────────────────────────────────
    public void setListener(Runnable r) {
        listener = r;
    }

    private void poke() {
        Runnable r = listener;
        if (r != null) {
            try {
                r.run();
            } catch (Exception ignored) {
                // a UI-side failure never touches the feed
            }
        }
    }

    public synchronized long version() {
        return version;
    }

    public synchronized long epoch() {
        return epoch;
    }

    // ── ingestion (feed thread) ─────────────────────────────────────────────────────────────
    public synchronized void setConnected(boolean c) {
        connected = c;
        version++;
    }

    public synchronized boolean isConnected() {
        return connected;
    }

    public void setBook(double[][] b, double[][] a, double px) {
        if (LATLOG) {
            long now = System.currentTimeMillis();
            android.util.Log.i("LAT", "book gap=" + (lastBookMs == 0 ? 0 : now - lastBookMs));
            lastBookMs = now;
        }
        synchronized (this) {
            bids = b;
            asks = a;
            if (px > 0 && lastPx <= 0) lastPx = px;
            version++;
        }
        poke();
    }

    static final boolean LATLOG = false;           // dev: data-path latency to logcat (LAT)
    private long lastTbMs, lastBookMs;

    public void ingestLive(FeedClient.Trades tr) {
        if (LATLOG && tr.tsMs.length > 0) {
            long now = System.currentTimeMillis();
            android.util.Log.i("LAT", "tb n=" + tr.tsMs.length + " age=" + (now - tr.tsMs[tr.tsMs.length - 1])
                    + " span=" + (tr.tsMs[tr.tsMs.length - 1] - tr.tsMs[0]) + " gap=" + (lastTbMs == 0 ? 0 : now - lastTbMs));
            lastTbMs = now;
        }
        synchronized (this) {
            int i0 = 0;
            if (liveT0Ms == 0) {
                if (n > 0) {
                    long last = tsMs[n - 1];
                    while (i0 < tr.tsMs.length && tr.tsMs[i0] <= last) i0++;
                    if (i0 >= tr.tsMs.length) return;
                }
                liveT0Ms = tr.tsMs[i0];
            } else if (n > 0) {
                long last = tsMs[n - 1];
                while (i0 < tr.tsMs.length && tr.tsMs[i0] <= last) i0++;
            }
            for (int i = i0; i < tr.tsMs.length; i++) append(tr.tsMs[i], tr.px[i], tr.qty[i], tr.side[i]);
            if (tr.tsMs.length > 0) {
                lastPx = tr.px[tr.tsMs.length - 1];
                lastSide = tr.side[tr.tsMs.length - 1] > 0 ? 1 : 0;
            }
            prune();
            version++;
        }
        poke();
    }

    public void ingestWindow(FeedClient.Trades tr) {
        synchronized (this) {
            // dedupe against BOTH the live edge and what's already stored: a deep fetch (custom VP)
            // arrives after the 6h backfill, so anything at/after the store's oldest row is a repeat
            long cut = liveT0Ms == 0 ? Long.MAX_VALUE : liveT0Ms;
            if (n > 0) cut = Math.min(cut, tsMs[0]);
            int keep = 0;
            while (keep < tr.tsMs.length && tr.tsMs[keep] < cut) keep++;
            if (keep == 0) return;
            // prepend: rebuild with the window rows first, then the existing (live) rows
            long[] nts = new long[Math.max(1 << 14, (keep + n) * 2)];
            long[] ntk = new long[nts.length];
            double[] nbq = new double[nts.length];
            double[] nsq = new double[nts.length];
            for (int i = 0; i < keep; i++) {
                nts[i] = tr.tsMs[i];
                ntk[i] = Math.round(tr.px[i] / TICK);
                boolean buy = tr.side[i] > 0;
                nbq[i] = buy ? tr.qty[i] : 0.0;
                nsq[i] = buy ? 0.0 : tr.qty[i];
            }
            System.arraycopy(tsMs, 0, nts, keep, n);
            System.arraycopy(tick, 0, ntk, keep, n);
            System.arraycopy(buyQ, 0, nbq, keep, n);
            System.arraycopy(sellQ, 0, nsq, keep, n);
            tsMs = nts;
            tick = ntk;
            buyQ = nbq;
            sellQ = nsq;
            n += keep;
            if (lastPx <= 0 && n > 0) {
                lastPx = tick[n - 1] * TICK;
                lastSide = buyQ[n - 1] > 0 ? 1 : 0;
            }
            epoch++;                                // rows inserted in FRONT: every index moved
            gRebuild();                             // merged players over the whole (backfilled) tape, once
            prune();
            version++;
        }
        poke();
    }

    private void append(long ts, double px, double qty, byte side) {
        if (n == tsMs.length) {
            int cap = n * 2;
            long[] nts = new long[cap];
            long[] ntk = new long[cap];
            double[] nbq = new double[cap];
            double[] nsq = new double[cap];
            System.arraycopy(tsMs, 0, nts, 0, n);
            System.arraycopy(tick, 0, ntk, 0, n);
            System.arraycopy(buyQ, 0, nbq, 0, n);
            System.arraycopy(sellQ, 0, nsq, 0, n);
            tsMs = nts;
            tick = ntk;
            buyQ = nbq;
            sellQ = nsq;
        }
        tsMs[n] = ts;
        tick[n] = Math.round(px / TICK);
        boolean buy = side > 0;
        buyQ[n] = buy ? qty : 0.0;
        sellQ[n] = buy ? 0.0 : qty;
        n++;
        chain(ts, tick[n - 1], tick[n - 1] * TICK * qty, buy ? 1 : 0);
    }

    /**
     * Feed one fill (time order) to its side's open chain; publishes / updates the merged campaign it belongs to. A
     * fill joins when it is within CAMPAIGN_MS of the previous fill of the SAME side -- whatever the other side did in
     * between and whichever way the price went (a refill taken again is the same player fighting for the level).
     */
    private void chain(long ts, long tk, double usd, int s) {
        if (cN[s] > 0 && ts - cT1[s] <= CAMPAIGN_MS) {
            cT1[s] = ts;
            cLo[s] = Math.min(cLo[s], tk);
            cHi[s] = Math.max(cHi[s], tk);
            cUsd[s] += usd;
            cN[s]++;
            if (cHi[s] > cLo[s]) {                        // ate through the book -> a merged campaign
                if (cG[s] < 0) {
                    cG[s] = gCount;
                    gAdd(cT0[s], cT1[s], cTk0[s], cLo[s], cHi[s], cUsd[s], cN[s], s);
                } else {
                    int g = cG[s];
                    gT1[g] = cT1[s]; gLo[g] = cLo[s]; gHi[g] = cHi[s]; gUsd[g] = cUsd[s]; gN[g] = cN[s];
                }
            }
        } else {
            cT0[s] = cT1[s] = ts;
            cTk0[s] = cLo[s] = cHi[s] = tk;
            cUsd[s] = usd;
            cN[s] = 1;
            cG[s] = -1;
        }
    }

    private void gAdd(long t0, long t1, long tk0, long lo, long hi, double usd, int cnt, int side) {
        if (gCount == gT0.length) gGrow(gCount * 2);
        gT0[gCount] = t0; gT1[gCount] = t1; gTick0[gCount] = tk0; gLo[gCount] = lo; gHi[gCount] = hi;
        gUsd[gCount] = usd; gN[gCount] = cnt; gSide[gCount] = (byte) side;
        gCount++;
    }

    private void gGrow(int cap) {
        gT0 = java.util.Arrays.copyOf(gT0, cap); gT1 = java.util.Arrays.copyOf(gT1, cap);
        gTick0 = java.util.Arrays.copyOf(gTick0, cap); gLo = java.util.Arrays.copyOf(gLo, cap); gHi = java.util.Arrays.copyOf(gHi, cap);
        gUsd = java.util.Arrays.copyOf(gUsd, cap); gN = java.util.Arrays.copyOf(gN, cap); gSide = java.util.Arrays.copyOf(gSide, cap);
    }

    private void gReset() {
        gCount = 0;
        cN[0] = cN[1] = 0;
        cG[0] = cG[1] = -1;
    }

    /** Rebuild the merged-campaign list from the store (backfill prepend): one pass over the trades. */
    private void gRebuild() {
        gReset();
        for (int i = 0; i < n; i++) {
            chain(tsMs[i], tick[i], tick[i] * TICK * (buyQ[i] + sellQ[i]), buyQ[i] > 0 ? 1 : 0);
        }
    }

    /** Drop the merged campaigns that ended before cutT (compaction: the list is not time-sorted). */
    private void gPrune(long cutT) {
        int w = 0;
        int g0 = cG[0], g1 = cG[1];
        cG[0] = cG[1] = -1;
        for (int g = 0; g < gCount; g++) {
            if (gT1[g] < cutT) continue;
            if (g == g0) cG[0] = w;
            if (g == g1) cG[1] = w;
            if (w != g) {
                gT0[w] = gT0[g]; gT1[w] = gT1[g]; gTick0[w] = gTick0[g]; gLo[w] = gLo[g]; gHi[w] = gHi[g];
                gUsd[w] = gUsd[g]; gN[w] = gN[g]; gSide[w] = gSide[g];
            }
            w++;
        }
        gCount = w;
    }

    private void prune() {
        long cut = System.currentTimeMillis() - WINDOW_MS;
        if (customKeepMs > 0) cut = Math.min(cut, customKeepMs - 300_000);
        int lo = lowerBound(cut);
        if (lo > 20000) {                          // amortized: shift only when a big slab is stale
            System.arraycopy(tsMs, lo, tsMs, 0, n - lo);
            System.arraycopy(tick, lo, tick, 0, n - lo);
            System.arraycopy(buyQ, lo, buyQ, 0, n - lo);
            System.arraycopy(sellQ, lo, sellQ, 0, n - lo);
            n -= lo;
            epoch++;                                // indices shifted
            gPrune(n > 0 ? tsMs[0] : Long.MAX_VALUE);
        }
    }

    /** first index with tsMs[i] >= t */
    private int lowerBound(long t) {
        int lo = 0, hi = n;
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (tsMs[mid] < t) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

    public synchronized void reset() {
        n = 0;
        liveT0Ms = 0;
        gReset();
        epoch++;
        version++;
    }

    public synchronized void setCustomKeep(long t0Ms) {
        customKeepMs = t0Ms;                       // 0 clears (presets prune at the 6h horizon again)
    }

    public synchronized long oldestTs() {
        return n > 0 ? tsMs[0] : 0;
    }

    public synchronized long latestTs() {
        return n > 0 ? tsMs[n - 1] : 0;
    }

    // ── INCREMENTAL DOM aggregation (the lag fix) ───────────────────────────────────────────

    /**
     * Bring `agg` up to date with the window [cutoffMs, now] of THIS store: O(trades that entered or left
     * the window since the last call). A stale epoch (reset / backfill / prune), a parameter change
     * (agg.clear()) or the periodic drift guard triggers one full rebuild of the window.
     */
    public synchronized void aggregate(DomAgg agg, long cutoffMs, long nowMs) {
        int newLo = lowerBound(cutoffMs);
        if (agg.needsRebuild(epoch, nowMs)) {
            agg.clear();
            agg.epoch = epoch;
            agg.lastRebuildMs = nowMs;
            agg.lo = agg.hi = newLo;
        }
        if (newLo > agg.lo) {                      // trades that fell out of the trailing window
            int end = Math.min(newLo, agg.hi);
            for (int i = agg.lo; i < end; i++) agg.apply(tick[i], buyQ[i], sellQ[i], -1);
            agg.lo = newLo;
            if (agg.hi < agg.lo) agg.hi = agg.lo;
        } else if (newLo < agg.lo) {               // the window start moved BACK (custom start): add them
            for (int i = newLo; i < agg.lo; i++) agg.apply(tick[i], buyQ[i], sellQ[i], +1);
            agg.lo = newLo;
        }
        for (int i = agg.hi; i < n; i++) agg.apply(tick[i], buyQ[i], sellQ[i], +1);   // newly arrived
        agg.hi = n;
    }

    /** Per-trade (usd, isBuy) samples since cutoffMs (0 = everything) — the size-dist popup feed. */
    public static final class SizeSamples {
        public final double[] usd;
        public final boolean[] buy;

        SizeSamples(double[] usd, boolean[] buy) {
            this.usd = usd;
            this.buy = buy;
        }
    }

    /**
     * The MIN SIZE launch default: the trade size at which trades AT-OR-ABOVE it carry 50% of the
     * tape's total USD volume (volume-weighted split — NOT the median trade, which sits far lower
     * on a fat-tailed tape). Sort ascending, walk from the biggest down until half the volume is in.
     */
    public synchronized double volumeHalfUsd() {
        if (n == 0) return 0.0;
        double[] usd = new double[n];
        double total = 0;
        for (int i = 0; i < n; i++) {
            usd[i] = tick[i] * TICK * (buyQ[i] + sellQ[i]);
            total += usd[i];
        }
        java.util.Arrays.sort(usd);
        double acc = 0;
        for (int i = n - 1; i >= 0; i--) {
            acc += usd[i];
            if (acc >= total / 2.0) return usd[i];
        }
        return usd[0];
    }

    public synchronized SizeSamples sizeSamples(long cutoffMs) {
        int i0 = cutoffMs > 0 ? lowerBound(cutoffMs) : 0;
        int m = n - i0;
        double[] usd = new double[Math.max(0, m)];
        boolean[] buy = new boolean[Math.max(0, m)];
        for (int i = 0; i < m; i++) {
            int j = i0 + i;
            usd[i] = tick[j] * TICK * (buyQ[j] + sellQ[j]);
            buy[i] = buyQ[j] > 0;
        }
        return new SizeSamples(usd, buy);
    }

    // ── book / price reads (UI thread) ──────────────────────────────────────────────────────
    public synchronized double[][] bidsCopy() {
        return bids;                                // feed replaces the array wholesale — safe to share
    }

    public synchronized double[][] asksCopy() {
        return asks;
    }

    public synchronized double lastPrice() {
        return lastPx;
    }

    public synchronized int lastSide() {
        return lastSide;
    }

    /** Book mid with the terminal's crossed/one-sided fallback: tape price is the truth. */
    public synchronized double mid() {
        if (bids.length > 0 && asks.length > 0 && bids[0][0] < asks[0][0])
            return (bids[0][0] + asks[0][0]) / 2.0;
        if (lastPx > 0) return lastPx;
        if (bids.length > 0) return bids[0][0];
        if (asks.length > 0) return asks[0][0];
        return 0.0;
    }

    public synchronized int tradeCount() {
        return n;
    }

    public static final long MERGE_MS = 1;         // ORDER: same-side fills <= 1 ms apart, monotonic = one atomic order
    public static final long CAMPAIGN_MS = 30;     // CAMPAIGN: same-side orders whose gap (next first fill - previous last
                                                   // fill) is <= 30 ms, refills / reversals allowed, other-side fills in
                                                   // between allowed -- user 2026-09-07 ("the 12:01:08 fight"); the
                                                   // aggTrade study: reaction band <= 10 ms, quiet trough to 100 ms,
                                                   // the market's normal cadence from 100 ms (study/campaign_gap_study.py)

    /** One closed unit of the backward two-sided walk: a campaign row, or plain fills. */
    private interface Sink {
        /** @return false to stop the walk */
        boolean campaign(int firstIdx, int lastIdx, long tsFirst, long tsLast, double usd, int side, int cnt, long lo, long hi);

        boolean plain(int k);
    }

    /**
     * Walk the trades newest -> oldest down to index `stop` (inclusive), building each side's chain independently
     * (a fill joins its side's open chain when it is within CAMPAIGN_MS of that chain's oldest fill); a chain closes
     * when the next older same-side fill is further away or the walk ends. Closed chains with >= 2 fills spanning
     * >= 1 tick are emitted as campaigns, the rest fill by fill. Emission lags the fills (a chain closes later than
     * its first fill), so callers sort what they keep by tsFirst; a chain still open when the sink stops has an
     * older first fill than everything emitted, so it is never one of the rows already asked for.
     */
    private void walk(int stop, Sink sink) {
        long[] oT = new long[2], nT = new long[2], lo = new long[2], hi = new long[2];
        int[] oI = new int[2], nI = new int[2], cnt = new int[2];
        double[] usd = new double[2];
        int[][] idx = new int[2][8];                     // the fills of a chain while it has NOT spanned a tick yet
        boolean[] open = new boolean[2];
        for (int k = n - 1; k >= stop - 1; k--) {
            int s = -1;
            if (k >= stop) s = buyQ[k] > 0 ? 1 : 0;
            for (int side = 0; side < 2; side++) {
                boolean joins = k >= stop && side == s && open[side] && oT[side] - tsMs[k] <= CAMPAIGN_MS;
                if (joins) {
                    oT[side] = tsMs[k]; oI[side] = k;
                    lo[side] = Math.min(lo[side], tick[k]); hi[side] = Math.max(hi[side], tick[k]);
                    usd[side] += tick[k] * TICK * (buyQ[k] + sellQ[k]);
                    if (hi[side] == lo[side]) {
                        if (cnt[side] == idx[side].length) idx[side] = java.util.Arrays.copyOf(idx[side], cnt[side] * 2);
                        idx[side][cnt[side]] = k;
                    }
                    cnt[side]++;
                    continue;
                }
                boolean closes = open[side] && (k < stop || side == s);
                if (closes) {
                    open[side] = false;
                    if (cnt[side] >= 2 && hi[side] > lo[side]) {
                        if (!sink.campaign(oI[side], nI[side], oT[side], nT[side], usd[side], side, cnt[side], lo[side], hi[side])) return;
                    } else {
                        for (int m = 0; m < cnt[side]; m++) if (!sink.plain(idx[side][m])) return;
                    }
                }
                if (k >= stop && side == s) {            // start this side's new chain with fill k
                    open[side] = true;
                    oT[side] = nT[side] = tsMs[k]; oI[side] = nI[side] = k;
                    lo[side] = hi[side] = tick[k];
                    usd[side] = tick[k] * TICK * (buyQ[k] + sellQ[k]);
                    idx[side][0] = k; cnt[side] = 1;
                }
            }
        }
    }

    private static double[] campaignRow(long tsFirst, long tsLast, double px0, double usd, int side, int cnt, long lo, long hi) {
        return new double[]{tsFirst, px0, usd, side, cnt, side > 0 ? (hi - lo) : -(hi - lo), (tsLast - tsFirst) / 1000L, tsLast};
    }

    private double[] plainRow(int k) {
        double px = tick[k] * TICK;
        return new double[]{tsMs[k], px, px * (buyQ[k] + sellQ[k]), buyQ[k] > 0 ? 1 : 0, 1, 0, 0, tsMs[k]};
    }

    private static final java.util.Comparator<double[]> NEWEST_FIRST = (a, b) -> {
        int c = Double.compare(b[0], a[0]);              // tsFirst desc
        return c != 0 ? c : Double.compare(b[7], a[7]);  // then tsLast desc
    };

    /**
     * Tape rows, newest-first, MIN SIZE filter + scroll offset applied. A CAMPAIGN (user 2026-09-07) = same-side fills
     * chained within CAMPAIGN_MS of the previous same-side fill (other-side fills in between and price reversals
     * allowed -- the orders inside are shown by the drop-down) that span >= 1 tick: ONE row, usd = the sum, time and
     * price = the FIRST fill, ticks = the net range (signed: + buy / - sell). The filter applies to the campaign
     * total. Same-price rapid fills are NOT merged (nothing was eaten). Row: [tsFirst, priceFirst, usd, side, n,
     * ticks, spanSec, tsLast]. Memoized on (version, filter, scroll, height).
     */
    public synchronized double[][] tapeRows(double minUsd, int skip, int maxRows) {
        if (tapeMemo != null && tapeMemoVer == version && tapeMemoMin == minUsd
                && tapeMemoSkip == skip && tapeMemoMax == maxRows) {
            return tapeMemo;
        }
        final int want = Math.max(0, skip) + Math.max(0, maxRows);
        final java.util.ArrayList<double[]> got = new java.util.ArrayList<>(want + 8);
        walk(0, new Sink() {
            @Override
            public boolean campaign(int fi, int li, long t0, long t1, double u, int side, int cnt, long lo, long hi) {
                if (u >= minUsd) got.add(campaignRow(t0, t1, tick[fi] * TICK, u, side, cnt, lo, hi));
                return got.size() < want;
            }

            @Override
            public boolean plain(int k) {
                if (tick[k] * TICK * (buyQ[k] + sellQ[k]) >= minUsd) got.add(plainRow(k));
                return got.size() < want;
            }
        });
        got.sort(NEWEST_FIRST);
        int from = Math.min(got.size(), Math.max(0, skip));
        int to = Math.min(got.size(), from + Math.max(0, maxRows));
        double[][] rows = got.subList(from, to).toArray(new double[0][]);
        tapeMemo = rows; tapeMemoVer = version; tapeMemoMin = minUsd; tapeMemoSkip = skip; tapeMemoMax = maxRows;
        return rows;
    }

    /**
     * DOM diamonds (user 2026-09-07): for every merged campaign that STARTED inside [cutoffMs, now] with total >= minUsd,
     * add its usd to the bin of its FIRST fill (outBuy / outSell indexed by topBin - bin, the visible rows) -- only
     * where it started, not the ticks it ate through. O(merged campaigns in the store) -- never a trade scan. Returns
     * the number of campaigns marked.
     */
    public synchronized int levelDiamonds(long topBin, int nRows, long tpg, long cutoffMs, double minUsd,
                                          double[] outBuy, double[] outSell) {
        return levelDiamonds(topBin, nRows, tpg, cutoffMs, minUsd, outBuy, outSell, null, null);
    }

    /** As above, plus per row the number of campaigns that started there (outCnt) and the newest one's last ms (outLast). */
    public synchronized int levelDiamonds(long topBin, int nRows, long tpg, long cutoffMs, double minUsd,
                                          double[] outBuy, double[] outSell, int[] outCnt, long[] outLast) {
        java.util.Arrays.fill(outBuy, 0, nRows, 0.0);
        java.util.Arrays.fill(outSell, 0, nRows, 0.0);
        if (outCnt != null) java.util.Arrays.fill(outCnt, 0, nRows, 0);
        if (outLast != null) java.util.Arrays.fill(outLast, 0, nRows, 0L);
        int marked = 0;
        for (int g = 0; g < gCount; g++) {              // not time-sorted (two sides interleave): look at every one
            if (gT0[g] < cutoffMs) continue;             // must have STARTED inside the window
            if (minUsd > 0 && gUsd[g] < minUsd) continue;
            long i = topBin - Math.floorDiv(gTick0[g], tpg);
            if (i < 0 || i >= nRows) continue;
            if (gSide[g] > 0) outBuy[(int) i] += gUsd[g]; else outSell[(int) i] += gUsd[g];
            if (outCnt != null) outCnt[(int) i]++;
            if (outLast != null) outLast[(int) i] = Math.max(outLast[(int) i], gT1[g]);
            marked++;
        }
        return marked;
    }

    /**
     * The tape rows of ONE price bin (popup behind a DOM diamond): campaigns LAUNCHED in the bin (first fill) with a
     * total >= minCampaign (the PLAYER threshold) + plain trades in it >= minUsd (MIN SIZE), inside [cutoffMs, now],
     * newest first -- the tapeRows format.
     */
    public synchronized double[][] levelRows(long bin, long tpg, long cutoffMs, double minUsd, double minCampaign, int maxRows) {
        final java.util.ArrayList<double[]> got = new java.util.ArrayList<>();
        walk(lowerBound(cutoffMs), new Sink() {
            @Override
            public boolean campaign(int fi, int li, long t0, long t1, double u, int side, int cnt, long lo, long hi) {
                if (Math.floorDiv(tick[fi], tpg) == bin && u >= minCampaign && t0 >= cutoffMs) got.add(campaignRow(t0, t1, tick[fi] * TICK, u, side, cnt, lo, hi));
                return got.size() < maxRows;
            }

            @Override
            public boolean plain(int k) {
                if (Math.floorDiv(tick[k], tpg) == bin && tick[k] * TICK * (buyQ[k] + sellQ[k]) >= minUsd) got.add(plainRow(k));
                return got.size() < maxRows;
            }
        });
        got.sort(NEWEST_FIRST);
        return got.subList(0, Math.min(got.size(), maxRows)).toArray(new double[0][]);
    }

    /**
     * Per-level REACTIVITY over the WHOLE window (user 2026-09-07: the diamonds are potential entries / stops /
     * targets, so they must not depend on what is on screen): bin -> [buyUsd, sellUsd, count, lastMs] for every level
     * where a campaign with total >= minUsd (the PLAYER threshold) was LAUNCHED (its first fill) inside [cutoffMs, now]:
     * the launched campaigns' totals per side, how many, the newest one's end; plus the P90 and the max of the
     * per-level totals. Memoized on (version, grouping, cutoff second, threshold): a frame is a map lookup per row.
     */
    public static final class Intensity {
        public final java.util.HashMap<Long, double[]> byBin;
        public final double p90, max;

        Intensity(java.util.HashMap<Long, double[]> byBin, double p90, double max) {
            this.byBin = byBin; this.p90 = p90; this.max = max;
        }
    }

    private Intensity intMemo;
    private long intMemoVer = -1, intMemoTpg = -1, intMemoCut = -1;
    private double intMemoMin = Double.NaN;

    public synchronized Intensity levelIntensity(long tpg, long cutoffMs, double minUsd) {
        long cutS = cutoffMs / 1000L;
        if (intMemo != null && intMemoVer == version && intMemoTpg == tpg && intMemoCut == cutS && intMemoMin == minUsd) return intMemo;
        java.util.HashMap<Long, double[]> m = new java.util.HashMap<>();
        for (int g = 0; g < gCount; g++) {
            if (gT0[g] < cutoffMs) continue;
            if (minUsd > 0 && gUsd[g] < minUsd) continue;
            long bin = Math.floorDiv(gTick0[g], tpg);
            double[] acc = m.get(bin);
            if (acc == null) m.put(bin, acc = new double[4]);
            acc[gSide[g] > 0 ? 0 : 1] += gUsd[g];
            acc[2] += 1;
            acc[3] = Math.max(acc[3], gT1[g]);
        }
        double p90 = Double.POSITIVE_INFINITY, max = 0;
        if (!m.isEmpty()) {
            double[] tot = new double[m.size()];
            int k = 0;
            for (double[] acc : m.values()) tot[k++] = acc[0] + acc[1];
            java.util.Arrays.sort(tot);
            p90 = tot[Math.min(tot.length - 1, (int) (tot.length * 0.9))];
            max = tot[tot.length - 1];
        }
        intMemo = new Intensity(m, p90, max);
        intMemoVer = version; intMemoTpg = tpg; intMemoCut = cutS; intMemoMin = minUsd;
        return intMemo;
    }

    /** P90 of the campaign totals inside [cutoffMs, now] (the PLAYER slider's launch default: a big player = a top-decile campaign); 0 if fewer than 10. */
    public synchronized double campaignP90(long cutoffMs) {
        double[] u = new double[gCount];
        int k = 0;
        for (int g = 0; g < gCount; g++) if (gT0[g] >= cutoffMs) u[k++] = gUsd[g];
        if (k < 10) return 0.0;
        java.util.Arrays.sort(u, 0, k);
        return u[Math.min(k - 1, (int) (k * 0.9))];
    }

    /** Merged campaigns currently tracked (tests / diagnostics). */
    public synchronized int mergedCount() {
        return gCount;
    }

    /**
     * The fills of a campaign row, newest first: [tsMs, price, usd, newOrder] -- newOrder = 1 when that fill STARTED
     * an order inside the campaign (more than MERGE_MS after the previous same-side fill, or a price reversal: the
     * drop-down draws a rule between orders). Located by (tsFirst, priceFirst, side), followed forward through the
     * same side's fills within CAMPAIGN_MS (other-side fills skipped) for `count` fills, confirmed by the total.
     */
    public synchronized double[][] groupTrades(long tsFirst, double priceFirst, int side, int count, double usd) {
        long tk0 = Math.round(priceFirst / TICK);
        for (int k = lowerBound(tsFirst); k < n && tsMs[k] == tsFirst; k++) {
            if ((buyQ[k] > 0 ? 1 : 0) != side || tick[k] != tk0) continue;
            int[] ks = new int[count];
            int got = 0, last = -1;
            for (int m = k; m < n && got < count; m++) {
                if ((buyQ[m] > 0 ? 1 : 0) != side) continue;
                if (last >= 0 && tsMs[m] - tsMs[last] > CAMPAIGN_MS) break;
                ks[got++] = m; last = m;
            }
            if (got != count) continue;
            double sum = 0;
            for (int m = 0; m < count; m++) sum += tick[ks[m]] * TICK * (buyQ[ks[m]] + sellQ[ks[m]]);
            if (Math.abs(sum - usd) > 1e-6 * Math.max(1.0, usd)) continue;
            double[][] out = new double[count][];
            for (int m = 0; m < count; m++) {
                int q = ks[m], prev = m > 0 ? ks[m - 1] : -1;
                boolean newOrder = prev < 0 || tsMs[q] - tsMs[prev] > MERGE_MS
                        || (side > 0 ? tick[q] < tick[prev] : tick[q] > tick[prev]);
                double px = tick[q] * TICK;
                out[count - 1 - m] = new double[]{tsMs[q], px, px * (buyQ[q] + sellQ[q]), newOrder ? 1 : 0};
            }
            return out;
        }
        return new double[0][];
    }

    /** 60s pressure sums (raw, never filtered): [buyUsd, sellUsd]. */
    public synchronized double[] pressure(long lookbackMs) {
        long cutoff = System.currentTimeMillis() - lookbackMs;
        double b = 0, s = 0;
        for (int i = n - 1; i >= 0; i--) {
            if (tsMs[i] < cutoff) break;
            double usd = tick[i] * TICK * (buyQ[i] + sellQ[i]);
            if (buyQ[i] > 0) b += usd;
            else s += usd;
        }
        return new double[]{b, s};
    }

    // ── DOM aggregations — the ORIGINAL full-scan ports (dom_panel's numpy methods). No longer used by
    //    the ladder (DomAgg replaced them); kept as the reference the JVM test compares DomAgg against. ──

    /** {group-bin: [boughtQ, soldQ]} over [cutoffMs, now] for bins in [loBin, hiBin]. */
    public synchronized HashMap<Long, double[]> vpBins(double g, long loBin, long hiBin, long cutoffMs) {
        HashMap<Long, double[]> out = new HashMap<>();
        long tpg = Math.max(1, Math.round(g / TICK));
        for (int i = lowerBound(cutoffMs); i < n; i++) {
            long gb = Math.floorDiv(tick[i], tpg);
            if (gb < loBin || gb > hiBin) continue;
            double[] v = out.get(gb);
            if (v == null) out.put(gb, v = new double[2]);
            v[0] += buyQ[i];
            v[1] += sellQ[i];
        }
        return out;
    }

    /**
     * Per-level player stats in USDT for the SOLD/BOUGHT columns (dom_panel.trade_stats):
     * {bin: [totBuyUsd, totSellUsd, fltBuyUsd, fltSellUsd, cntBuy, cntSell]} — flt/cnt cover only
     * trades whose own size >= minUsd. usd = qty * tick * TICK is exact (a trade's price IS its tick).
     */
    public synchronized HashMap<Long, double[]> tradeStats(double g, long loBin, long hiBin,
                                                           double minUsd, long cutoffMs) {
        HashMap<Long, double[]> out = new HashMap<>();
        long tpg = Math.max(1, Math.round(g / TICK));
        for (int i = lowerBound(cutoffMs); i < n; i++) {
            long gb = Math.floorDiv(tick[i], tpg);
            if (gb < loBin || gb > hiBin) continue;
            double px = tick[i] * TICK;
            double ub = buyQ[i] * px, us = sellQ[i] * px;
            double[] v = out.get(gb);
            if (v == null) out.put(gb, v = new double[6]);
            v[0] += ub;
            v[1] += us;
            if (ub + us >= minUsd || minUsd <= 0) {
                v[2] += ub;
                v[3] += us;
                if (ub > 0) v[4] += 1;
                if (us > 0) v[5] += 1;
            }
        }
        return out;
    }

    /** nearest-rank quantile of the positive values (ascending sort done here). */
    private static double nearestRank(double[] vals, int cnt, double q) {
        return DomAgg.nearestRank(vals, cnt, q);
    }

    /**
     * [boughtThr, soldThr]: per-SIDE nearest-rank P90 of per-level values across the WHOLE window
     * (dom_panel.side_gold_thresholds). minUsd==0 -> SOL volumes; filtered -> USD of trades >= min.
     */
    public synchronized double[] sideGoldThresholds(double g, double minUsd, long cutoffMs) {
        long tpg = Math.max(1, Math.round(g / TICK));
        HashMap<Long, double[]> lv = new HashMap<>();
        for (int i = lowerBound(cutoffMs); i < n; i++) {
            double vb, vs;
            if (minUsd > 0) {
                double px = tick[i] * TICK;
                vb = buyQ[i] * px;
                vs = sellQ[i] * px;
                if (vb + vs < minUsd) continue;
            } else {
                vb = buyQ[i];
                vs = sellQ[i];
            }
            long gb = Math.floorDiv(tick[i], tpg);
            double[] v = lv.get(gb);
            if (v == null) lv.put(gb, v = new double[2]);
            v[0] += vb;
            v[1] += vs;
        }
        double[] bArr = new double[lv.size()];
        double[] sArr = new double[lv.size()];
        int bc = 0, sc = 0;
        for (double[] v : lv.values()) {
            if (v[0] > 0) bArr[bc++] = v[0];
            if (v[1] > 0) sArr[sc++] = v[1];
        }
        return new double[]{nearestRank(bArr, bc, 0.90), nearestRank(sArr, sc, 0.90)};
    }

    /** [goldThr, lvnThr] of per-level TOTAL volumes across the whole window (P90 top / bottom decile). */
    public synchronized double[] vpThresholds(double g, long cutoffMs) {
        long tpg = Math.max(1, Math.round(g / TICK));
        HashMap<Long, double[]> lv = new HashMap<>();
        for (int i = lowerBound(cutoffMs); i < n; i++) {
            long gb = Math.floorDiv(tick[i], tpg);
            double[] v = lv.get(gb);
            if (v == null) lv.put(gb, v = new double[1]);
            v[0] += buyQ[i] + sellQ[i];
        }
        double[] tot = new double[lv.size()];
        int c = 0;
        for (double[] v : lv.values()) if (v[0] > 0) tot[c++] = v[0];
        double gold = nearestRank(tot, c, 0.90);
        double lvn = c < 3 ? Double.NEGATIVE_INFINITY : nearestRank(tot, c, 0.10);
        return new double[]{gold, lvn};
    }
}
