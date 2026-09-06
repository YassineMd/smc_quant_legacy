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

    /**
     * Tape iteration, newest-first with the MIN SIZE filter and scroll offset applied —
     * the exact row-selection loop of _TapeCanvas.paintEvent. Each row: [tsMs, price, usd, side].
     * Memoized on (version, filter, scroll, height): a repaint without new data is free.
     */
    public synchronized double[][] tapeRows(double minUsd, int skip, int maxRows) {
        if (tapeMemo != null && tapeMemoVer == version && tapeMemoMin == minUsd
                && tapeMemoSkip == skip && tapeMemoMax == maxRows) {
            return tapeMemo;
        }
        int skip0 = skip;
        double[][] out = new double[Math.max(0, maxRows)][];
        int got = 0;
        for (int i = n - 1; i >= 0 && got < maxRows; i--) {
            double px = tick[i] * TICK;
            double qty = buyQ[i] + sellQ[i];
            double usd = px * qty;
            if (usd < minUsd) continue;
            if (skip > 0) {
                skip--;
                continue;
            }
            out[got++] = new double[]{tsMs[i], px, usd, buyQ[i] > 0 ? 1 : 0};
        }
        if (got < out.length) {
            double[][] trimmed = new double[got][];
            System.arraycopy(out, 0, trimmed, 0, got);
            out = trimmed;
        }
        tapeMemo = out;
        tapeMemoVer = version;
        tapeMemoMin = minUsd;
        tapeMemoSkip = skip0;
        tapeMemoMax = maxRows;
        return out;
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
