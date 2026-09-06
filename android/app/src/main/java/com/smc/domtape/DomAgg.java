package com.smc.domtape;

import java.util.Arrays;

/**
 * INCREMENTAL per-level aggregation over the DOM's sliding VP window (2026-09-06, the lag fix).
 *
 * Before: every frame the ladder re-scanned the WHOLE window (up to 6h of trades, hundreds of thousands
 * of rows) FOUR times on the UI thread — vpBins + tradeStats + sideGoldThresholds + vpThresholds, each
 * boxing a HashMap<Long,double[]> per trade — ~60 ms per frame on the tablet (37% janky, 24% CPU).
 *
 * Now the store applies only the DELTA to this object under its own lock ({@link TradeStore#aggregate}):
 * trades that entered the store since the last frame are ADDED to their group bin, trades that fell out
 * of the trailing window are SUBTRACTED, and a structural change of the store (reconnect reset, backfill
 * prepend, prune shift) or of the parameters (group, MIN SIZE) triggers ONE full rebuild. Per-bin sums
 * live in flat primitive arrays indexed by (bin - binBase) — no boxing, no hashing. A full rebuild is also
 * forced every REBUILD_MS so add/subtract floating-point drift can never accumulate.
 *
 * Per bin: buyQ / sellQ (SOL), buyUsd / sellUsd (all trades), fltBuyUsd / fltSellUsd + cntBuy / cntSell
 * (only trades whose own size >= minUsd) — exactly the numbers vpBins / tradeStats produced. The
 * thresholds (per-side P90 of per-level values, whole-window P90 gold / P10 LVN of level totals) are
 * derived from the bin arrays (a sort over the LEVELS, not the trades) and cached until the window changes.
 */
final class DomAgg {

    static final long REBUILD_MS = 300_000;        // periodic full rebuild: bounds add/subtract drift

    double g = 0.0;                                // group size (price units)
    long tpg = 1;                                  // ticks per group bin
    double minUsd = 0.0;                           // MIN SIZE filter (0 = off)

    long binBase = 0;                              // bin index of arrays[0]
    int nb = 0;                                    // number of bins allocated
    double[] buyQ = new double[0], sellQ = new double[0];
    double[] buyUsd = new double[0], sellUsd = new double[0];
    double[] fltBuy = new double[0], fltSell = new double[0];
    double[] cntBuy = new double[0], cntSell = new double[0];

    // window bookkeeping (store indices, valid for `epoch`)
    long epoch = -1;
    int lo = 0, hi = 0;
    long lastRebuildMs = 0;
    long changes = 0;                              // bumps whenever a trade was added / evicted

    // thresholds cache
    private long thrChanges = -1;
    private double thrB = Double.POSITIVE_INFINITY, thrS = Double.POSITIVE_INFINITY;
    private double gold = Double.POSITIVE_INFINITY, lvn = Double.NEGATIVE_INFINITY;

    /** (Re)configure; a change of group or filter invalidates everything (the next aggregate() rebuilds). */
    void configure(double g, double minUsd) {
        if (g != this.g || minUsd != this.minUsd) {
            this.g = g;
            this.tpg = Math.max(1, Math.round(g / TradeStore.TICK));
            this.minUsd = minUsd;
            clear();
        }
    }

    void clear() {
        nb = 0;
        binBase = 0;
        buyQ = sellQ = buyUsd = sellUsd = fltBuy = fltSell = cntBuy = cntSell = new double[0];
        lo = hi = 0;
        epoch = -1;
        changes++;
    }

    boolean needsRebuild(long storeEpoch, long nowMs) {
        return epoch != storeEpoch || nowMs - lastRebuildMs > REBUILD_MS;
    }

    /** Index into the bin arrays for `bin`, growing / shifting the arrays so it fits (rare). */
    private int slot(long bin) {
        if (nb == 0) {
            binBase = bin - 256;
            alloc(512);
        } else if (bin < binBase) {
            long newBase = bin - 256;
            int shift = (int) (binBase - newBase);
            grow(nb + shift, shift);
            binBase = newBase;
        } else if (bin >= binBase + nb) {
            int need = (int) (bin - binBase) + 257;
            grow(Math.max(need, nb * 2), 0);
        }
        return (int) (bin - binBase);
    }

    private void alloc(int n) {
        nb = n;
        buyQ = new double[n]; sellQ = new double[n];
        buyUsd = new double[n]; sellUsd = new double[n];
        fltBuy = new double[n]; fltSell = new double[n];
        cntBuy = new double[n]; cntSell = new double[n];
    }

    private void grow(int n, int shift) {
        buyQ = re(buyQ, n, shift); sellQ = re(sellQ, n, shift);
        buyUsd = re(buyUsd, n, shift); sellUsd = re(sellUsd, n, shift);
        fltBuy = re(fltBuy, n, shift); fltSell = re(fltSell, n, shift);
        cntBuy = re(cntBuy, n, shift); cntSell = re(cntSell, n, shift);
        nb = n;
    }

    private static double[] re(double[] a, int n, int shift) {
        double[] b = new double[n];
        System.arraycopy(a, 0, b, shift, a.length);
        return b;
    }

    /** Fold ONE trade in (sign +1) or out (sign -1). tick = integer price tick, bq / sq = SOL by aggressor. */
    void apply(long tick, double bq, double sq, int sign) {
        int i = slot(Math.floorDiv(tick, tpg));
        double px = tick * TradeStore.TICK;
        double ub = bq * px, us = sq * px;
        buyQ[i] += sign * bq;
        sellQ[i] += sign * sq;
        buyUsd[i] += sign * ub;
        sellUsd[i] += sign * us;
        if (minUsd <= 0 || ub + us >= minUsd) {
            fltBuy[i] += sign * ub;
            fltSell[i] += sign * us;
            if (ub > 0) cntBuy[i] += sign;
            if (us > 0) cntSell[i] += sign;
        }
        changes++;
    }

    /** Array index of `bin`, or -1 when the bin has never been touched. */
    int index(long bin) {
        long i = bin - binBase;
        return (nb > 0 && i >= 0 && i < nb) ? (int) i : -1;
    }

    static double nz(double v) {                   // drift guard: a sum can never be meaningfully negative
        return v > 1e-9 ? v : 0.0;
    }

    private void refreshThresholds() {
        if (thrChanges == changes) return;
        thrChanges = changes;
        double[] b = new double[nb], s = new double[nb], t = new double[nb];
        int bc = 0, sc = 0, tc = 0;
        boolean fon = minUsd > 0;
        for (int i = 0; i < nb; i++) {
            double vb = nz(fon ? fltBuy[i] : buyQ[i]);
            double vs = nz(fon ? fltSell[i] : sellQ[i]);
            if (vb > 0) b[bc++] = vb;
            if (vs > 0) s[sc++] = vs;
            double tot = nz(buyQ[i]) + nz(sellQ[i]);
            if (tot > 0) t[tc++] = tot;
        }
        thrB = nearestRank(b, bc, 0.90);
        thrS = nearestRank(s, sc, 0.90);
        gold = nearestRank(t, tc, 0.90);
        lvn = tc < 3 ? Double.NEGATIVE_INFINITY : nearestRank(t, tc, 0.10);
    }

    /** nearest-rank quantile of the first cnt values (sorted here). */
    static double nearestRank(double[] vals, int cnt, double q) {
        if (cnt == 0) return Double.POSITIVE_INFINITY;
        double[] a = Arrays.copyOf(vals, cnt);
        Arrays.sort(a);
        int k = Math.min(cnt - 1, Math.max(0, (int) Math.ceil(q * cnt) - 1));
        return a[k];
    }

    /** [boughtThr, soldThr] — per-SIDE P90 of per-level values over the whole window (dom_panel.side_gold_thresholds). */
    double[] sideGoldThresholds() {
        refreshThresholds();
        return new double[]{thrB, thrS};
    }

    /** [goldThr, lvnThr] — P90 / bottom-decile of per-level TOTAL volumes over the whole window. */
    double[] vpThresholds() {
        refreshThresholds();
        return new double[]{gold, lvn};
    }
}
