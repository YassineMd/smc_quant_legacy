package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RecordingCanvas;
import android.graphics.RectF;
import android.graphics.RenderNode;
import android.graphics.Typeface;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;

import java.util.ArrayList;
import java.util.HashMap;

/**
 * The painted DOM ladder — Android port of dom_panel._DomCanvas:
 * [ BID | SOLD | PRICE | BOUGHT | ASK | VP(right, gray, grows left) ] under a whole-book
 * imbalance strip. Same rules as the terminal: integer-tick binning, P90 wall emphasis on the
 * visible book, whole-window P90 gold VP + bottom-decile purple LVN, per-side P90 bold on
 * SOLD/BOUGHT, single last-trade price highlight, follow armed by centering and disarmed by
 * any manual pan. Touch: drag = pan, double-tap = re-center (+ re-arm follow).
 *
 * RENDERING (2026-09-06, the lag fix): each ladder row is recorded into its own {@link RenderNode} with a
 * GPU compositing layer and re-recorded ONLY when the row's content signature changed (bar widths in whole
 * px, texts, emphasis flags). An unchanged row costs nothing on the UI thread and one textured quad on the
 * render thread; before, every frame re-recorded ~330 canvas ops (~18 ms UI + ~16 ms render on the tablet).
 * The live-price chip is NOT part of the rows — it is the {@link PriceChip} overlay the host places above
 * this view, so the price can slide between levels without touching any row.
 */
public class DomView extends View {

    interface Host {
        double group();

        long vpCutoffMs();

        String vpLabel();

        double minUsd();

        TradeStore store();

        DomAgg agg();

        PriceChip chip();                          // the live-price overlay (may be null)
    }

    private static final double TICK = TradeStore.TICK;
    private static final int FOLLOW_MARGIN = 10;   // auto-follow: levels kept free above and below the price
    private static final boolean PROFILE = false;  // per-frame paint timing to logcat (DOMPERF) — dev only

    private final Host host;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float rowH, hdrH, stripH, pad;
    private final float dp2_5, dp3, dp3_5, dp5, dp6, dp9, dp10, dp13, dp14, dp19, dp40, dp44, dp54, dp84, dp108, dp110;
    private final RectF rf = new RectF();          // one reusable rect
    private final GlyphCache glN, glB, glH;        // shaped-text caches per paint (text / bold / header)
    private final HashMap<Long, String> priceCache = new HashMap<>();   // bin -> "%,.2f" (levels rarely change)
    private double priceCacheG = -1;
    private final GestureDetector gestures;

    private Double anchorPx = null;                // ladder anchor PRICE; null = center on next draw
    private boolean follow = true;                 // armed by centering, disarmed by any manual pan
    private float dragLastY = -1;
    private Float hoverY = null;                   // stylus/mouse hover row highlight; null = off-canvas
    private final ArrayList<double[]> areas = new ArrayList<>();   // [loPrice, hiPrice) level bands
    private float[] areaDrag = null;               // in-progress band [yPress, yNow] (long-press + drag)
    // geometry of the LAST drawn frame (interaction helpers share the paint math)
    private float gY0;
    private int gNRows;
    private long gTopBin;
    private long lastTopBin = Long.MIN_VALUE;      // the chip animates only when the ladder itself did not move
    private long lastChipBin = Long.MIN_VALUE;

    // row cache: one RenderNode per visible row + the signature of what it currently shows
    private RenderNode[] rows = new RenderNode[0];
    private long[] rowSig = new long[0];
    private int rowNodeW = -1;
    private int rowNodeH = -1;
    private int recorded, drawn;                   // PROFILE counters

    public DomView(Context ctx, Host host) {
        super(ctx);
        this.host = host;
        setBackgroundColor(Ui.BG);
        rowH = Ui.dp(ctx, 17);
        hdrH = Ui.dp(ctx, 24);
        stripH = Ui.dp(ctx, 34);
        pad = Ui.dp(ctx, 12);
        dp2_5 = Ui.dp(ctx, 2.5f); dp3 = Ui.dp(ctx, 3); dp3_5 = Ui.dp(ctx, 3.5f); dp5 = Ui.dp(ctx, 5);
        dp6 = Ui.dp(ctx, 6); dp9 = Ui.dp(ctx, 9); dp10 = Ui.dp(ctx, 10); dp13 = Ui.dp(ctx, 13);
        dp14 = Ui.dp(ctx, 14); dp19 = Ui.dp(ctx, 19); dp40 = Ui.dp(ctx, 40); dp44 = Ui.dp(ctx, 44);
        dp54 = Ui.dp(ctx, 54); dp84 = Ui.dp(ctx, 84); dp108 = Ui.dp(ctx, 108); dp110 = Ui.dp(ctx, 110);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(Ui.dp(ctx, 11));
        textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textB.setTextSize(Ui.dp(ctx, 11));
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
        glN = new GlyphCache(text);
        glB = new GlyphCache(textB);
        glH = new GlyphCache(textH);
        gestures = new GestureDetector(ctx, new GestureDetector.SimpleOnGestureListener() {
            @Override
            public boolean onDoubleTap(MotionEvent e) {
                if (!deleteAreaAt(e.getY())) recenter();   // double-tap ON a band deletes it; else re-center
                return true;
            }

            @Override
            public void onLongPress(MotionEvent e) {       // hold-then-drag = mark a band of levels
                if (anchorPx == null) return;
                areaDrag = new float[]{e.getY(), e.getY()};
                dragLastY = -1;                            // the long-press claims this gesture from panning
                performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS);
                invalidate();
            }
        });
    }

    float rowHeight() {
        return rowH;
    }

    // ── level bands (the terminal's Ctrl+drag areas, touch edition) ─────────────────────────
    /** Snap two canvas ys to whole rows -> [loPrice, hiPrice) covering every level dragged over. */
    private double[] bandFromYs(float ya, float yb) {
        if (anchorPx == null || gNRows <= 0) return null;
        double g = host.group();
        long b1 = gTopBin - (long) Math.floor((ya - gY0) / rowH);
        long b2 = gTopBin - (long) Math.floor((yb - gY0) / rowH);
        long lo = Math.min(b1, b2), hi = Math.max(b1, b2);
        return new double[]{lo * g, (hi + 1) * g};         // PRICE-anchored: pans/regroups keep it on its levels
    }

    /** Delete the most recent band covering the tapped level. True if one was removed. */
    private boolean deleteAreaAt(float y) {
        if (anchorPx == null || gNRows <= 0) return false;
        double g = host.group();
        double px = (gTopBin - (y - gY0) / rowH + 0.5) * g;
        for (int k = areas.size() - 1; k >= 0; k--) {
            if (areas.get(k)[0] <= px && px < areas.get(k)[1]) {
                areas.remove(k);
                invalidate();
                return true;
            }
        }
        return false;
    }

    private void commitArea() {
        float[] ad = areaDrag;
        areaDrag = null;
        if (ad == null || Math.abs(ad[1] - ad[0]) < dp3) {
            invalidate();                                  // a still long-press is NOT a band
            return;
        }
        double[] band = bandFromYs(ad[0], ad[1]);
        if (band != null) areas.add(band);
        invalidate();
    }

    public void recenter() {
        anchorPx = null;                           // next draw pins the anchor to the current mid
        follow = true;
        invalidate();
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        gestures.onTouchEvent(ev);
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragLastY = ev.getY();
                return true;
            case MotionEvent.ACTION_MOVE:
                if (areaDrag != null) {            // band-in-progress: extend, never pan
                    areaDrag[1] = ev.getY();
                    invalidate();
                } else if (dragLastY >= 0 && anchorPx != null) {
                    float dy = ev.getY() - dragLastY;
                    dragLastY = ev.getY();
                    if (dy != 0) {                 // dragging DOWN pulls content down -> higher prices
                        anchorPx = anchorPx + (dy / rowH) * host.group();
                        follow = false;            // going off-center deactivates auto-follow
                        invalidate();
                    }
                }
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (areaDrag != null) commitArea();
                dragLastY = -1;
                return true;
        }
        return super.onTouchEvent(ev);
    }

    @Override
    public boolean onHoverEvent(MotionEvent ev) {  // stylus/mouse hover -> terminal's row highlight
        if (ev.getActionMasked() == MotionEvent.ACTION_HOVER_EXIT) hoverY = null;
        else hoverY = ev.getY();
        invalidate();
        return true;
    }

    private float centerY(float top, Paint p) {
        return top + rowH / 2f - (p.descent() + p.ascent()) / 2f;
    }

    private RectF rset(float l, float t, float r, float b) {
        rf.set(l, t, r, b);
        return rf;
    }

    private String priceStr(long bin, double g) {
        if (priceCacheG != g) {
            priceCache.clear();
            priceCacheG = g;
        }
        String s = priceCache.get(bin);
        if (s == null) {
            if (priceCache.size() > 4000) priceCache.clear();
            s = Fmt.price(bin * g);
            priceCache.put(bin, s);
        }
        return s;
    }

    private GlyphCache gl(Paint p) {
        return p == textB ? glB : (p == textH ? glH : glN);
    }

    private void txt(Canvas c, String s, float x, float y, Paint p) {
        gl(p).draw(c, s, x, y, p);
    }

    private static long mix(long h, long v) {
        return (h ^ (v + 0x9E3779B97F4A7C15L + (h << 6) + (h >>> 2))) * 0xBF58476D1CE4E5B9L;
    }

    private static long mix(long h, String s) {
        return mix(h, s == null ? 0x51L : s.hashCode());
    }

    /** One row's inputs for this frame — hashed into its signature, then drawn if the signature changed. */
    private static final class Row {
        long bin;
        int bidPx, askPx, vpPx;                    // bar widths in whole px (quantized so tiny maxSz moves don't dirty)
        boolean bidHot, askHot, soldHot, boughtHot, gold, lvn;
        String bidTxt, askTxt, soldTxt, boughtTxt, vpTxt, priceTxt;
    }

    private final Row row = new Row();

    private void ensureRowNodes(int n, int w, int hPx) {
        if (rows.length == n && rowNodeW == w && rowNodeH == hPx) return;
        for (RenderNode r : rows) if (r != null) r.discardDisplayList();
        rows = new RenderNode[n];
        rowSig = new long[n];
        for (int i = 0; i < n; i++) {
            RenderNode r = new RenderNode("dom-row-" + i);
            r.setPosition(0, 0, w, hPx);
            r.setUseCompositingLayer(true, null);  // GPU layer: an unchanged row is one textured quad per frame
            rows[i] = r;
            rowSig[i] = Long.MIN_VALUE;
        }
        rowNodeW = w;
        rowNodeH = hPx;
    }

    @Override
    protected void onDraw(Canvas c) {
        if (!PROFILE) {
            onDrawBody(c);
            return;
        }
        long t0 = System.nanoTime();
        recorded = 0;
        drawn = 0;
        onDrawBody(c);
        profFrame(System.nanoTime() - t0);
    }

    private void onDrawBody(Canvas c) {
        int w = getWidth(), h = getHeight();
        TradeStore st = host.store();
        double[][] bids = st.bidsCopy();
        double[][] asks = st.asksCopy();
        PriceChip chip = host.chip();

        if (bids.length == 0 && asks.length == 0) {
            text.setColor(Ui.WAIT_TXT);
            text.setTextAlign(Paint.Align.CENTER);
            txt(c, st.isConnected() ? "waiting for order book…" : "connecting to bridge…", w / 2f, h / 2f, text);
            if (chip != null) chip.hide();
            return;
        }

        float y0 = 0;
        // ── whole-book imbalance strip (all levels, SOL) ───────────────────────────────────
        double tb = 0, ta = 0;
        for (double[] lv : bids) tb += lv[1];
        for (double[] lv : asks) ta += lv[1];
        double tot = tb + ta;
        if (tot > 0) {
            float barY = y0 + dp19, barH = dp5;
            float bw = (float) ((w - 2 * pad) * (tb / tot));
            fill.setColor(Ui.BUY);
            c.drawRoundRect(rset(pad, barY, pad + Math.max(2, bw), barY + barH), 2.5f, 2.5f, fill);
            fill.setColor(Ui.SELL);
            c.drawRoundRect(rset(pad + bw + 2, barY, w - pad, barY + barH), 2.5f, 2.5f, fill);
            textH.setColor(Ui.BUY);
            textH.setTextAlign(Paint.Align.LEFT);
            txt(c, "BIDS " + Fmt.k(tb) + "  " + Fmt.pct(tb / tot * 100), pad, y0 + dp13, textH);
            textH.setColor(Ui.SELL);
            textH.setTextAlign(Paint.Align.RIGHT);
            txt(c, Fmt.pct(ta / tot * 100) + "  " + Fmt.k(ta) + " ASKS", w - pad, y0 + dp13, textH);
        }
        y0 += stripH;

        // ── column geometry: [BID | SOLD | PRICE | BOUGHT | ASK | VP(right)] ───────────────
        double g = host.group();
        float tradedW = dp108;
        float priceW = dp84;
        float vpW = Math.max(dp110, w * 0.20f);
        float cVp0 = w - pad - vpW;
        float span = Math.max(dp54, (cVp0 - 2 * tradedW - priceW - 2 * pad - dp40) / 2f);
        float cBid1 = pad + span;
        float cSold0 = cBid1 + dp10;
        float cPrice0 = cSold0 + tradedW + 4;
        float cBought0 = cPrice0 + priceW + 4;
        float cAsk0 = cBought0 + tradedW + dp10;
        float askSpan = Math.max(dp54, cVp0 - dp14 - cAsk0);
        float bidSpan = span;

        // ── header ─────────────────────────────────────────────────────────────────────────
        boolean fon = host.minUsd() > 0;
        float hy = y0 + hdrH / 2f - (textH.descent() + textH.ascent()) / 2f;
        textH.setColor(Ui.HDR_TXT);
        textH.setTextAlign(Paint.Align.RIGHT);
        txt(c, fon ? "BIDS $" : "BIDS", pad + span, hy, textH);
        textH.setTextAlign(Paint.Align.CENTER);
        txt(c, fon ? "SOLD $" : "SOLD", cSold0 + tradedW / 2f, hy, textH);
        txt(c, "PRICE", cPrice0 + priceW / 2f, hy, textH);
        txt(c, fon ? "BOUGHT $" : "BOUGHT", cBought0 + tradedW / 2f, hy, textH);
        textH.setTextAlign(Paint.Align.LEFT);
        txt(c, fon ? "ASKS $" : "ASKS", cAsk0, hy, textH);
        textH.setTextAlign(Paint.Align.RIGHT);
        txt(c, (fon ? "VOLUME $ · " : "VOLUME · ") + host.vpLabel(), w - pad, hy, textH);
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setColor(Ui.RULE);
        stroke.setStrokeWidth(1);
        c.drawLine(pad, y0 + hdrH - 1, w - pad, y0 + hdrH - 1, stroke);
        y0 += hdrH;

        // ── rows: bin the book + traded volumes to the current group ───────────────────────
        double mid = st.mid();
        if (mid <= 0) {
            if (chip != null) chip.hide();
            return;
        }
        int nRows = Math.max(1, (int) ((h - y0) / rowH));
        long tpg = Math.max(1, Math.round(g / TICK));
        if (anchorPx == null) {
            anchorPx = mid;                        // center ONCE (entry / double-tap)
        } else if (follow) {
            // AUTO-FOLLOW MARGIN (user 2026-09-06): the price must always keep FOLLOW_MARGIN levels of room above
            // AND below it. When it gets closer than that to an edge the ladder shifts by the MINIMUM amount that
            // restores the margin (the rest of the view stays put); a short ladder keeps the price centered.
            long mb = Math.floorDiv(Math.round(mid / TICK), tpg);
            long topB = Math.floorDiv(Math.round(anchorPx / TICK), tpg) + nRows / 2;
            int margin = Math.min(FOLLOW_MARGIN, Math.max(0, (nRows - 1) / 2));
            long minTop = mb + margin;             // >= margin rows between the top edge and the price
            long maxTop = mb + nRows - 1 - margin; // >= margin rows between the price and the bottom edge
            if (topB < minTop) topB = minTop;
            else if (topB > maxTop) topB = maxTop;
            anchorPx = (topB - nRows / 2) * g;     // anchor bin = top bin - nRows/2 (exact: bin * g / TICK is integral)
        }
        long centerBin = Math.floorDiv(Math.round(anchorPx / TICK), tpg);
        long topBin = centerBin + nRows / 2;
        gY0 = y0;                                  // share the frame's geometry with the touch helpers
        gNRows = nRows;
        gTopBin = topBin;

        // book: binned straight into the visible rows (no HashMap<Long,Double> boxing)
        double[] visB = new double[nRows], visA = new double[nRows];
        double maxSz = 0;
        for (double[] lv : bids) {
            long i = topBin - Math.floorDiv(Math.round(lv[0] / TICK), tpg);
            if (i >= 0 && i < nRows) visB[(int) i] += lv[1];
        }
        for (double[] lv : asks) {
            long i = topBin - Math.floorDiv(Math.round(lv[0] / TICK), tpg);
            if (i >= 0 && i < nRows) visA[(int) i] += lv[1];
        }
        for (int i = 0; i < nRows; i++) maxSz = Math.max(maxSz, Math.max(visB[i], visA[i]));
        if (maxSz <= 0) maxSz = 1;
        // traded volumes / player stats / thresholds: the INCREMENTAL window aggregate (DomAgg) — O(trades
        // that entered or left the window since the last frame), never a re-scan of the whole window
        long cutoff = host.vpCutoffMs();
        DomAgg agg = host.agg();
        st.aggregate(agg, cutoff, System.currentTimeMillis());
        double[][] vp = new double[nRows][];       // per visible row: [boughtQ, soldQ], null = never traded
        double[][] stats = fon ? new double[nRows][] : null;   // [totBuy$, totSell$, fltBuy$, fltSell$, cntB, cntS]
        double maxVp = 0;
        for (int i = 0; i < nRows; i++) {
            int k = agg.index(topBin - i);
            if (k < 0) continue;
            double bq = DomAgg.nz(agg.buyQ[k]), sq = DomAgg.nz(agg.sellQ[k]);
            if (bq + sq > 0) {
                vp[i] = new double[]{bq, sq};
                maxVp = Math.max(maxVp, bq + sq);
            }
            if (fon) {
                double ub = DomAgg.nz(agg.buyUsd[k]), us = DomAgg.nz(agg.sellUsd[k]);
                if (ub + us > 0)
                    stats[i] = new double[]{ub, us, DomAgg.nz(agg.fltBuy[k]), DomAgg.nz(agg.fltSell[k]),
                            Math.round(DomAgg.nz(agg.cntBuy[k])), Math.round(DomAgg.nz(agg.cntSell[k]))};
            }
        }
        if (maxVp <= 0) maxVp = 1;
        double[] sideThr = agg.sideGoldThresholds();
        double thrB = sideThr[0], thrS = sideThr[1];
        double[] vpThr = agg.vpThresholds();
        double goldThr = vpThr[0], lvnThr = vpThr[1];

        // wall emphasis threshold: P90 of the VISIBLE nonzero book levels (same index rule as the terminal)
        double[] nz = new double[nRows * 2];
        int nzc = 0;
        for (int i = 0; i < nRows; i++) {
            if (visB[i] > 0) nz[nzc++] = visB[i];
            if (visA[i] > 0) nz[nzc++] = visA[i];
        }
        double p90 = Double.POSITIVE_INFINITY;
        if (nzc > 0) {
            double[] s = new double[nzc];
            System.arraycopy(nz, 0, s, 0, nzc);
            java.util.Arrays.sort(s);
            p90 = s[Math.min(nzc - 1, (int) (nzc * 0.9))];
        }

        Long bestBidBin = bids.length > 0 ? Math.floorDiv(Math.round(bids[0][0] / TICK), tpg) : null;
        Long bestAskBin = asks.length > 0 ? Math.floorDiv(Math.round(asks[0][0] / TICK), tpg) : null;
        double lastPx = st.lastPrice();
        int lastSide = st.lastSide();
        long lastBin = lastPx > 0 ? Math.floorDiv(Math.round(lastPx / TICK), tpg) : Long.MIN_VALUE;

        // price column background (the rows draw their dim price text over it; the chip overlay covers the live one)
        fill.setColor(Ui.PRICE_BG);
        c.drawRect(cPrice0, y0, cPrice0 + priceW, h, fill);

        // ── rows: one cached RenderNode each, re-recorded only when the row's signature changed ─
        int hPx = (int) Math.ceil(rowH);
        ensureRowNodes(nRows, w, hPx);
        float barInset = dp2_5;
        for (int i = 0; i < nRows; i++) {
            long b = topBin - i;
            Row r = row;
            r.bin = b;
            long sig = mix(mix(mix(0x1234567L, b), fon ? 1 : 0), Double.doubleToLongBits(g));
            // BID
            double q = visB[i];
            if (q > 0 && (bestBidBin == null || b <= bestBidBin)) {
                r.bidPx = Math.max(1, Math.round((float) (q / maxSz * bidSpan)));
                r.bidHot = q >= p90;
                r.bidTxt = fon ? Fmt.usd(q * b * g) : Fmt.k(q);
            } else {
                r.bidPx = 0;
                r.bidHot = false;
                r.bidTxt = null;
            }
            // ASK
            q = visA[i];
            if (q > 0 && (bestAskBin == null || b >= bestAskBin)) {
                r.askPx = Math.max(1, Math.round((float) (q / maxSz * askSpan)));
                r.askHot = q >= p90;
                r.askTxt = fon ? Fmt.usd(q * b * g) : Fmt.k(q);
            } else {
                r.askPx = 0;
                r.askHot = false;
                r.askTxt = null;
            }
            // SOLD / BOUGHT — filtered: "7.0K (45%, 3)" (total invariant); ALL: plain SOL volume
            r.soldTxt = r.boughtTxt = null;
            r.soldHot = r.boughtHot = false;
            if (fon) {
                double[] stt = stats[i];
                if (stt != null) {
                    if (stt[1] > 0 && stt[5] > 0) {
                        r.soldHot = stt[3] >= thrS;
                        r.soldTxt = Fmt.k(stt[1]) + " (" + Fmt.pct(stt[3] / stt[1] * 100) + ", " + (long) stt[5] + ")";
                    }
                    if (stt[0] > 0 && stt[4] > 0) {
                        r.boughtHot = stt[2] >= thrB;
                        r.boughtTxt = Fmt.k(stt[0]) + " (" + Fmt.pct(stt[2] / stt[0] * 100) + ", " + (long) stt[4] + ")";
                    }
                }
            } else {
                double[] v = vp[i];
                if (v != null) {
                    if (v[1] > 0) {
                        r.soldHot = v[1] >= thrS;
                        r.soldTxt = Fmt.k(v[1]);
                    }
                    if (v[0] > 0) {
                        r.boughtHot = v[0] >= thrB;
                        r.boughtTxt = Fmt.k(v[0]);
                    }
                }
            }
            // VP (far right, gray, REVERSED: right-anchored, grows left; gold >= P90, LVN purple)
            double[] v = vp[i];
            if (v != null && v[0] + v[1] > 0) {
                double tv = v[0] + v[1];
                r.vpPx = Math.max(2, Math.round((float) (tv / maxVp * (vpW - dp44))));
                r.gold = tv >= goldThr;
                r.lvn = !r.gold && tv <= lvnThr;
                r.vpTxt = fon ? Fmt.usd(tv * b * g) : Fmt.k(tv);
            } else {
                r.vpPx = 0;
                r.gold = r.lvn = false;
                r.vpTxt = null;
            }
            r.priceTxt = priceStr(b, g);
            sig = mix(sig, r.bidPx); sig = mix(sig, r.askPx); sig = mix(sig, r.vpPx);
            sig = mix(sig, (r.bidHot ? 1 : 0) | (r.askHot ? 2 : 0) | (r.soldHot ? 4 : 0) | (r.boughtHot ? 8 : 0)
                    | (r.gold ? 16 : 0) | (r.lvn ? 32 : 0));
            sig = mix(sig, r.bidTxt); sig = mix(sig, r.askTxt); sig = mix(sig, r.soldTxt);
            sig = mix(sig, r.boughtTxt); sig = mix(sig, r.vpTxt); sig = mix(sig, r.priceTxt);

            RenderNode node = rows[i];
            if (sig != rowSig[i] || !node.hasDisplayList()) {
                RecordingCanvas rc = node.beginRecording(w, hPx);
                try {
                    recordRow(rc, r, w, fon, g, barInset, cBid1, cSold0, cPrice0, cBought0, cAsk0, tradedW, priceW);
                } finally {
                    node.endRecording();
                }
                rowSig[i] = sig;
                recorded++;
            }
            node.setTranslationY(y0 + i * rowH);
            c.drawRenderNode(node);
            drawn++;
        }

        // ── live-price chip: the overlay slides to the last trade's row (jumps when the ladder moved) ──
        if (chip != null) {
            long li = lastBin != Long.MIN_VALUE ? topBin - lastBin : Long.MIN_VALUE;
            if (lastBin != Long.MIN_VALUE && lastSide >= 0 && li >= 0 && li < nRows) {
                boolean animate = topBin == lastTopBin && lastChipBin != Long.MIN_VALUE && lastChipBin != lastBin;
                chip.show(cPrice0 + 3, y0 + li * rowH, priceW - 6, rowH, lastSide, priceStr(lastBin, g), animate);
            } else {
                chip.hide();
            }
            lastTopBin = topBin;
            lastChipBin = lastBin;
        }

        // level bands (hold+drag areas): translucent, price-anchored — pan/regroup keeps them on
        // their levels; the in-progress drag renders the same way as a live preview
        ArrayList<double[]> bands = new ArrayList<>(areas);
        if (areaDrag != null && Math.abs(areaDrag[1] - areaDrag[0]) >= dp3) {
            double[] bp = bandFromYs(areaDrag[0], areaDrag[1]);
            if (bp != null) bands.add(bp);
        }
        stroke.setStyle(Paint.Style.STROKE);
        for (double[] band : bands) {
            float yTop = (float) (y0 + (topBin + 1 - band[1] / g) * rowH);
            float yBot = (float) (y0 + (topBin + 1 - band[0] / g) * rowH);
            float yt = Math.max(y0, yTop), yb = Math.min(y0 + nRows * rowH, yBot);
            if (yb <= yt) continue;                // panned fully out of view
            fill.setColor(0x1AC8D2E1);             // rgba(200,210,225,26)
            c.drawRoundRect(rset(pad - 4, yt + 0.5f, w - pad + 4, yb - 1f), 3, 3, fill);
            stroke.setColor(0x82E1E6F0);           // rgba(225,230,240,130)
            stroke.setStrokeWidth(1);
            c.drawRoundRect(rset(pad - 4, yt + 0.5f, w - pad + 4, yb - 1f), 3, 3, stroke);
        }

        // stylus-hover row highlight: thin light-gray box around the FULL row under the pointer;
        // cleared the moment the pointer leaves the canvas — same rule as the terminal
        if (hoverY != null && hoverY >= y0) {
            int i = (int) ((hoverY - y0) / rowH);
            if (i >= 0 && i < nRows) {
                float ry = y0 + i * rowH;
                stroke.setColor(0x96C8D0DC);       // rgba(200,208,220,150)
                stroke.setStrokeWidth(1);
                c.drawRoundRect(rset(pad - 4, ry + 0.5f, w - pad + 4, ry + rowH - 1.5f), 3, 3, stroke);
            }
        }
    }

    /** Record ONE row's content into its node canvas (row-local y: 0..rowH). */
    private void recordRow(Canvas c, Row r, int w, boolean fon, double g, float barInset,
                           float cBid1, float cSold0, float cPrice0, float cBought0, float cAsk0,
                           float tradedW, float priceW) {
        float ry = 0;
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeWidth(1);
        stroke.setColor(Ui.GRID);
        c.drawLine(pad, ry + rowH - 1, w - pad, ry + rowH - 1, stroke);
        // BID bar (grows left from the SOLD column)
        if (r.bidTxt != null) {
            fill.setColor((Ui.BUY & 0x00FFFFFF) | ((r.bidHot ? 210 : 130) << 24));
            c.drawRoundRect(rset(cBid1 - r.bidPx, ry + barInset, cBid1, ry + rowH - barInset), 2, 2, fill);
            Paint tp = r.bidHot ? textB : text;
            tp.setColor(r.bidHot ? Ui.TXT : Ui.DIM_TXT);
            tp.setTextAlign(Paint.Align.RIGHT);
            txt(c, r.bidTxt, cBid1 - dp6, centerY(ry, tp), tp);
        }
        // ASK bar (grows right from the BOUGHT column)
        if (r.askTxt != null) {
            fill.setColor((Ui.SELL & 0x00FFFFFF) | ((r.askHot ? 210 : 130) << 24));
            c.drawRoundRect(rset(cAsk0, ry + barInset, cAsk0 + r.askPx, ry + rowH - barInset), 2, 2, fill);
            Paint tp = r.askHot ? textB : text;
            tp.setColor(r.askHot ? Ui.TXT : Ui.DIM_TXT);
            tp.setTextAlign(Paint.Align.LEFT);
            txt(c, r.askTxt, cAsk0 + dp6, centerY(ry, tp), tp);
        }
        // SOLD / BOUGHT
        if (r.soldTxt != null) {
            Paint tp = r.soldHot ? textB : text;
            tp.setColor((Ui.SELL & 0x00FFFFFF) | ((r.soldHot ? 235 : 150) << 24));
            tp.setTextAlign(Paint.Align.RIGHT);
            txt(c, r.soldTxt, cSold0 + tradedW - 4, centerY(ry, tp), tp);
        }
        if (r.boughtTxt != null) {
            Paint tp = r.boughtHot ? textB : text;
            tp.setColor((Ui.BUY & 0x00FFFFFF) | ((r.boughtHot ? 235 : 150) << 24));
            tp.setTextAlign(Paint.Align.LEFT);
            txt(c, r.boughtTxt, cBought0 + 4, centerY(ry, tp), tp);
        }
        // VP (far right, gray, REVERSED: right-anchored, grows left; gold >= P90, LVN purple)
        if (r.vpTxt != null) {
            if (r.gold) fill.setColor((Ui.GOLD & 0x00FFFFFF) | (200 << 24));
            else if (r.lvn) fill.setColor((Ui.LVN & 0x00FFFFFF) | (150 << 24));
            else fill.setColor((Ui.VP_GRAY & 0x00FFFFFF) | (90 << 24));
            float bx = r.vpPx;
            c.drawRect(w - pad - bx, ry + dp3_5, w - pad, ry + rowH - dp3_5, fill);
            Paint tp = (r.gold || r.lvn) ? textB : text;
            tp.setColor(r.gold ? Ui.GOLD : (r.lvn ? Ui.LVN : Ui.DIM_TXT));
            tp.setTextAlign(Paint.Align.RIGHT);
            txt(c, r.vpTxt, w - pad - bx - dp9, centerY(ry, tp), tp);
        }
        // PRICE (dim; the live one is covered by the chip overlay)
        text.setColor(Ui.DIM_TXT);
        text.setTextAlign(Paint.Align.CENTER);
        txt(c, r.priceTxt, cPrice0 + priceW / 2f, centerY(ry, text), text);
    }

    // ── PROFILE (dev): frame timing + row cache hit rate, logged every 25 frames ─────────────
    private int pFrames;
    private long pTot;

    private void profFrame(long total) {
        pTot += total;
        pFrames++;
        if (pFrames % 25 == 0) {
            android.util.Log.i("DOMPERF", String.format(java.util.Locale.US,
                    "frame %.1f ms (avg %.1f) | rows drawn %d re-recorded %d | glyph misses %d",
                    total / 1e6, pTot / 1e6 / pFrames, drawn, recorded, glN.misses + glB.misses + glH.misses));
        }
    }
}
