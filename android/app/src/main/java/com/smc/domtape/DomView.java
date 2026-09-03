package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Locale;

/**
 * The painted DOM ladder — Android port of dom_panel._DomCanvas:
 * [ BID | SOLD | PRICE | BOUGHT | ASK | VP(right, gray, grows left) ] under a whole-book
 * imbalance strip. Same rules as the terminal: integer-tick binning, P90 wall emphasis on the
 * visible book, whole-window P90 gold VP + bottom-decile purple LVN, per-side P90 bold on
 * SOLD/BOUGHT, single last-trade price highlight, follow armed by centering and disarmed by
 * any manual pan. Touch: drag = pan, double-tap = re-center (+ re-arm follow).
 */
public class DomView extends View {

    interface Host {
        double group();

        long vpCutoffMs();

        String vpLabel();

        double minUsd();

        TradeStore store();
    }

    private static final double TICK = TradeStore.TICK;

    private final Host host;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float rowH, hdrH, stripH, pad;
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

    public DomView(Context ctx, Host host) {
        super(ctx);
        this.host = host;
        setBackgroundColor(Ui.BG);
        rowH = Ui.dp(ctx, 17);
        hdrH = Ui.dp(ctx, 24);
        stripH = Ui.dp(ctx, 34);
        pad = Ui.dp(ctx, 12);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(Ui.dp(ctx, 11));
        textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textB.setTextSize(Ui.dp(ctx, 11));
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
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
        if (ad == null || Math.abs(ad[1] - ad[0]) < Ui.dp(getContext(), 3)) {
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

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        TradeStore st = host.store();
        double[][] bids = st.bidsCopy();
        double[][] asks = st.asksCopy();

        if (bids.length == 0 && asks.length == 0) {
            text.setColor(Ui.WAIT_TXT);
            text.setTextAlign(Paint.Align.CENTER);
            c.drawText(st.isConnected() ? "waiting for order book…" : "connecting to bridge…",
                    w / 2f, h / 2f, text);
            return;
        }

        float y0 = 0;
        // ── whole-book imbalance strip (all levels, SOL) ───────────────────────────────────
        double tb = 0, ta = 0;
        for (double[] lv : bids) tb += lv[1];
        for (double[] lv : asks) ta += lv[1];
        double tot = tb + ta;
        if (tot > 0) {
            float barY = y0 + Ui.dp(getContext(), 19), barH = Ui.dp(getContext(), 5);
            float bw = (float) ((w - 2 * pad) * (tb / tot));
            fill.setColor(Ui.BUY);
            c.drawRoundRect(new RectF(pad, barY, pad + Math.max(2, bw), barY + barH), 2.5f, 2.5f, fill);
            fill.setColor(Ui.SELL);
            c.drawRoundRect(new RectF(pad + bw + 2, barY, w - pad, barY + barH), 2.5f, 2.5f, fill);
            textH.setColor(Ui.BUY);
            textH.setTextAlign(Paint.Align.LEFT);
            c.drawText(String.format(Locale.US, "BIDS %s  %.0f%%", Ui.kfmt(tb), tb / tot * 100),
                    pad, y0 + Ui.dp(getContext(), 13), textH);
            textH.setColor(Ui.SELL);
            textH.setTextAlign(Paint.Align.RIGHT);
            c.drawText(String.format(Locale.US, "%.0f%%  %s ASKS", ta / tot * 100, Ui.kfmt(ta)),
                    w - pad, y0 + Ui.dp(getContext(), 13), textH);
        }
        y0 += stripH;

        // ── column geometry: [BID | SOLD | PRICE | BOUGHT | ASK | VP(right)] ───────────────
        double g = host.group();
        float tradedW = Ui.dp(getContext(), 108);
        float priceW = Ui.dp(getContext(), 84);
        float vpW = Math.max(Ui.dp(getContext(), 110), w * 0.20f);
        float cVp0 = w - pad - vpW;
        float span = Math.max(Ui.dp(getContext(), 54),
                (cVp0 - 2 * tradedW - priceW - 2 * pad - Ui.dp(getContext(), 40)) / 2f);
        float cBid1 = pad + span;
        float cSold0 = cBid1 + Ui.dp(getContext(), 10);
        float cPrice0 = cSold0 + tradedW + 4;
        float cBought0 = cPrice0 + priceW + 4;
        float cAsk0 = cBought0 + tradedW + Ui.dp(getContext(), 10);
        float askSpan = Math.max(Ui.dp(getContext(), 54), cVp0 - Ui.dp(getContext(), 14) - cAsk0);
        float bidSpan = span;

        // ── header ─────────────────────────────────────────────────────────────────────────
        boolean fon = host.minUsd() > 0;
        float hy = y0 + hdrH / 2f - (textH.descent() + textH.ascent()) / 2f;
        textH.setColor(Ui.HDR_TXT);
        textH.setTextAlign(Paint.Align.RIGHT);
        c.drawText("BIDS", pad + span, hy, textH);
        textH.setTextAlign(Paint.Align.CENTER);
        c.drawText(fon ? "SOLD $" : "SOLD", cSold0 + tradedW / 2f, hy, textH);
        c.drawText("PRICE", cPrice0 + priceW / 2f, hy, textH);
        c.drawText(fon ? "BOUGHT $" : "BOUGHT", cBought0 + tradedW / 2f, hy, textH);
        textH.setTextAlign(Paint.Align.LEFT);
        c.drawText("ASKS", cAsk0, hy, textH);
        textH.setTextAlign(Paint.Align.RIGHT);
        c.drawText("VOLUME · " + host.vpLabel(), w - pad, hy, textH);
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setColor(Ui.RULE);
        stroke.setStrokeWidth(1);
        c.drawLine(pad, y0 + hdrH - 1, w - pad, y0 + hdrH - 1, stroke);
        y0 += hdrH;

        // ── rows: bin the book + traded volumes to the current group ───────────────────────
        double mid = st.mid();
        if (mid <= 0) return;
        int nRows = Math.max(1, (int) ((h - y0) / rowH));
        long tpg = Math.max(1, Math.round(g / TICK));
        if (anchorPx == null) {
            anchorPx = mid;                        // center ONCE (entry / double-tap)
        } else if (follow) {
            long tbn = Math.floorDiv(Math.round(anchorPx / TICK), tpg) + nRows / 2;
            long mb = Math.floorDiv(Math.round(mid / TICK), tpg);
            if (mb > tbn || mb < tbn - nRows + 1) anchorPx = mid;   // snapped back on crossing an extreme
        }
        long centerBin = Math.floorDiv(Math.round(anchorPx / TICK), tpg);
        long topBin = centerBin + nRows / 2;
        long loBin = topBin - nRows + 1;
        gY0 = y0;                                  // share the frame's geometry with the touch helpers
        gNRows = nRows;
        gTopBin = topBin;

        HashMap<Long, Double> bidBins = new HashMap<>(), askBins = new HashMap<>();
        for (double[] lv : bids) {
            long b = Math.floorDiv(Math.round(lv[0] / TICK), tpg);
            Double cur = bidBins.get(b);
            bidBins.put(b, (cur == null ? 0 : cur) + lv[1]);
        }
        for (double[] lv : asks) {
            long b = Math.floorDiv(Math.round(lv[0] / TICK), tpg);
            Double cur = askBins.get(b);
            askBins.put(b, (cur == null ? 0 : cur) + lv[1]);
        }
        long cutoff = host.vpCutoffMs();
        HashMap<Long, double[]> vp = st.vpBins(g, loBin, topBin, cutoff);
        HashMap<Long, double[]> stats = fon ? st.tradeStats(g, loBin, topBin, host.minUsd(), cutoff) : null;

        double[] visB = new double[nRows], visA = new double[nRows];
        double maxSz = 0;
        for (int i = 0; i < nRows; i++) {
            Double b = bidBins.get(topBin - i), a = askBins.get(topBin - i);
            visB[i] = b == null ? 0 : b;
            visA[i] = a == null ? 0 : a;
            maxSz = Math.max(maxSz, Math.max(visB[i], visA[i]));
        }
        if (maxSz <= 0) maxSz = 1;
        double maxVp = 0;
        for (double[] v : vp.values()) maxVp = Math.max(maxVp, v[0] + v[1]);
        if (maxVp <= 0) maxVp = 1;

        double[] sideThr = st.sideGoldThresholds(g, host.minUsd(), cutoff);
        double thrB = sideThr[0], thrS = sideThr[1];
        double[] vpThr = st.vpThresholds(g, cutoff);
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
        Long lastBin = lastPx > 0 ? Math.floorDiv(Math.round(lastPx / TICK), tpg) : null;

        float barInset = Ui.dp(getContext(), 2.5f);
        for (int i = 0; i < nRows; i++) {
            long b = topBin - i;
            float ry = y0 + i * rowH;
            stroke.setColor(Ui.GRID);
            c.drawLine(pad, ry + rowH - 1, w - pad, ry + rowH - 1, stroke);

            // BID bar (grows left from the SOLD column)
            double q = visB[i];
            if (q > 0 && (bestBidBin == null || b <= bestBidBin)) {
                float bw = (float) (q / maxSz * bidSpan);
                fill.setColor((Ui.BUY & 0x00FFFFFF) | ((q >= p90 ? 210 : 130) << 24));
                c.drawRoundRect(new RectF(cBid1 - bw, ry + barInset, cBid1, ry + rowH - barInset), 2, 2, fill);
                Paint tp = q >= p90 ? textB : text;
                tp.setColor(q >= p90 ? Ui.TXT : Ui.DIM_TXT);
                tp.setTextAlign(Paint.Align.RIGHT);
                c.drawText(Ui.kfmt(q), cBid1 - Ui.dp(getContext(), 6), centerY(ry, tp), tp);
            }
            // ASK bar (grows right from the BOUGHT column)
            q = visA[i];
            if (q > 0 && (bestAskBin == null || b >= bestAskBin)) {
                float bw = (float) (q / maxSz * askSpan);
                fill.setColor((Ui.SELL & 0x00FFFFFF) | ((q >= p90 ? 210 : 130) << 24));
                c.drawRoundRect(new RectF(cAsk0, ry + barInset, cAsk0 + bw, ry + rowH - barInset), 2, 2, fill);
                Paint tp = q >= p90 ? textB : text;
                tp.setColor(q >= p90 ? Ui.TXT : Ui.DIM_TXT);
                tp.setTextAlign(Paint.Align.LEFT);
                c.drawText(Ui.kfmt(q), cAsk0 + Ui.dp(getContext(), 6), centerY(ry, tp), tp);
            }

            // SOLD / BOUGHT — filtered: "7.0K (45%, 3)" (total invariant); ALL: plain SOL volume
            if (fon) {
                double[] stt = stats.get(b);
                if (stt != null) {
                    if (stt[1] > 0 && stt[5] > 0) {
                        boolean hot = stt[3] >= thrS;
                        Paint tp = hot ? textB : text;
                        tp.setColor((Ui.SELL & 0x00FFFFFF) | ((hot ? 235 : 150) << 24));
                        tp.setTextAlign(Paint.Align.RIGHT);
                        c.drawText(String.format(Locale.US, "%s (%.0f%%, %d)", Ui.kfmt(stt[1]),
                                        stt[3] / stt[1] * 100, (long) stt[5]),
                                cSold0 + tradedW - 4, centerY(ry, tp), tp);
                    }
                    if (stt[0] > 0 && stt[4] > 0) {
                        boolean hot = stt[2] >= thrB;
                        Paint tp = hot ? textB : text;
                        tp.setColor((Ui.BUY & 0x00FFFFFF) | ((hot ? 235 : 150) << 24));
                        tp.setTextAlign(Paint.Align.LEFT);
                        c.drawText(String.format(Locale.US, "%s (%.0f%%, %d)", Ui.kfmt(stt[0]),
                                        stt[2] / stt[0] * 100, (long) stt[4]),
                                cBought0 + 4, centerY(ry, tp), tp);
                    }
                }
            } else {
                double[] v = vp.get(b);
                if (v != null) {
                    if (v[1] > 0) {
                        boolean hot = v[1] >= thrS;
                        Paint tp = hot ? textB : text;
                        tp.setColor((Ui.SELL & 0x00FFFFFF) | ((hot ? 235 : 150) << 24));
                        tp.setTextAlign(Paint.Align.RIGHT);
                        c.drawText(Ui.kfmt(v[1]), cSold0 + tradedW - 4, centerY(ry, tp), tp);
                    }
                    if (v[0] > 0) {
                        boolean hot = v[0] >= thrB;
                        Paint tp = hot ? textB : text;
                        tp.setColor((Ui.BUY & 0x00FFFFFF) | ((hot ? 235 : 150) << 24));
                        tp.setTextAlign(Paint.Align.LEFT);
                        c.drawText(Ui.kfmt(v[0]), cBought0 + 4, centerY(ry, tp), tp);
                    }
                }
            }

            // VP (far right, gray, REVERSED: right-anchored, grows left; gold >= P90, LVN purple)
            double[] v = vp.get(b);
            if (v != null && v[0] + v[1] > 0) {
                double tv = v[0] + v[1];
                float bw = (float) (tv / maxVp * (vpW - Ui.dp(getContext(), 44)));
                boolean goldRow = tv >= goldThr;
                boolean lvnRow = !goldRow && tv <= lvnThr;
                if (goldRow) fill.setColor((Ui.GOLD & 0x00FFFFFF) | (200 << 24));
                else if (lvnRow) fill.setColor((Ui.LVN & 0x00FFFFFF) | (150 << 24));
                else fill.setColor((Ui.VP_GRAY & 0x00FFFFFF) | (90 << 24));
                float bx = Math.max(1.5f, bw);
                c.drawRect(w - pad - bx, ry + Ui.dp(getContext(), 3.5f),
                        w - pad, ry + rowH - Ui.dp(getContext(), 3.5f), fill);
                Paint tp = (goldRow || lvnRow) ? textB : text;
                tp.setColor(goldRow ? Ui.GOLD : (lvnRow ? Ui.LVN : Ui.DIM_TXT));
                tp.setTextAlign(Paint.Align.RIGHT);
                c.drawText(Ui.kfmt(tv), w - pad - bx - Ui.dp(getContext(), 9), centerY(ry, tp), tp);
            }
        }

        // ── price column LAST so chips overlay the grid cleanly ────────────────────────────
        fill.setColor(Ui.PRICE_BG);
        c.drawRect(cPrice0, y0, cPrice0 + priceW, h, fill);
        for (int i = 0; i < nRows; i++) {
            long b = topBin - i;
            float ry = y0 + i * rowH;
            double price = b * g;
            boolean isLast = lastBin != null && b == lastBin && lastSide >= 0;
            if (isLast) {
                fill.setColor(((lastSide > 0 ? Ui.BUY : Ui.SELL) & 0x00FFFFFF) | (175 << 24));
                c.drawRoundRect(new RectF(cPrice0 + 3, ry + 1.5f, cPrice0 + priceW - 3, ry + rowH - 1.5f),
                        3, 3, fill);
            }
            Paint tp = isLast ? textB : text;
            tp.setColor(isLast ? 0xFFFFFFFF : Ui.DIM_TXT);
            tp.setTextAlign(Paint.Align.CENTER);
            c.drawText(String.format(Locale.US, "%,.2f", price), cPrice0 + priceW / 2f, centerY(ry, tp), tp);
        }

        // level bands (hold+drag areas): translucent, price-anchored — pan/regroup keeps them on
        // their levels; the in-progress drag renders the same way as a live preview
        ArrayList<double[]> bands = new ArrayList<>(areas);
        if (areaDrag != null && Math.abs(areaDrag[1] - areaDrag[0]) >= Ui.dp(getContext(), 3)) {
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
            c.drawRoundRect(new RectF(pad - 4, yt + 0.5f, w - pad + 4, yb - 1f), 3, 3, fill);
            stroke.setColor(0x82E1E6F0);           // rgba(225,230,240,130)
            stroke.setStrokeWidth(1);
            c.drawRoundRect(new RectF(pad - 4, yt + 0.5f, w - pad + 4, yb - 1f), 3, 3, stroke);
        }

        // stylus-hover row highlight: thin light-gray box around the FULL row under the pointer;
        // cleared the moment the pointer leaves the canvas — same rule as the terminal
        if (hoverY != null && hoverY >= y0) {
            int i = (int) ((hoverY - y0) / rowH);
            if (i >= 0 && i < nRows) {
                float ry = y0 + i * rowH;
                stroke.setColor(0x96C8D0DC);       // rgba(200,208,220,150)
                stroke.setStrokeWidth(1);
                c.drawRoundRect(new RectF(pad - 4, ry + 0.5f, w - pad + 4, ry + rowH - 1.5f), 3, 3, stroke);
            }
        }
    }
}
