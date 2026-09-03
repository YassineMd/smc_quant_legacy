package com.smc.domtape;

import java.util.HashMap;

/**
 * Shared trade + book store, the Android port of the state both terminal panels keep:
 * trades at TICK resolution in parallel time-ordered arrays (dom_panel._chunks/_trades_cat)
 * plus the latest 0.4s pulse book. All access synchronized; the UI polls at ~2.5Hz.
 *
 * Dedupe mirrors the terminal exactly: the backfill window keeps only rows OLDER than the
 * first live trade; live batches drop anything at/before the store's end (no aggTrade ids
 * on the wire, so the boundary is cut by timestamp).
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

    // ── ingestion (feed thread) ─────────────────────────────────────────────────────────────
    public synchronized void setConnected(boolean c) {
        connected = c;
    }

    public synchronized boolean isConnected() {
        return connected;
    }

    public synchronized void setBook(double[][] b, double[][] a, double px) {
        bids = b;
        asks = a;
        if (px > 0 && lastPx <= 0) lastPx = px;
    }

    public synchronized void ingestLive(FeedClient.Trades tr) {
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
    }

    public synchronized void ingestWindow(FeedClient.Trades tr) {
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
        prune();
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

    /** Per-trade (usd, isBuy) samples since cutoffMs (0 = everything) — the size-dist popup feed. */
    public static final class SizeSamples {
        public final double[] usd;
        public final boolean[] buy;

        SizeSamples(double[] usd, boolean[] buy) {
            this.usd = usd;
            this.buy = buy;
        }
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
     */
    public synchronized double[][] tapeRows(double minUsd, int skip, int maxRows) {
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
            return trimmed;
        }
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

    // ── DOM aggregations (ports of dom_panel's numpy methods) ───────────────────────────────

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
        if (cnt == 0) return Double.POSITIVE_INFINITY;
        double[] a = new double[cnt];
        System.arraycopy(vals, 0, a, 0, cnt);
        java.util.Arrays.sort(a);
        int k = Math.min(cnt - 1, Math.max(0, (int) Math.ceil(q * cnt) - 1));
        return a[k];
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
