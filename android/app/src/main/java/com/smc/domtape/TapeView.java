package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RecordingCanvas;
import android.graphics.RectF;
import android.graphics.RenderNode;
import android.graphics.Typeface;
import android.view.MotionEvent;
import android.view.View;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;

/**
 * The painted tape body — Android port of trades_tape._TapeCanvas: TIME / PRICE / AMOUNT header +
 * newest-first rows with tier styling (tint -> accent bar -> whale glow + gold). Touch drag scrolls
 * back (pauses); the panel's pill resumes. The 60 s pressure strip is {@link PressureStrip}, its own view.
 *
 * RENDERING (2026-09-06): every trade row is recorded ONCE into a {@link RenderNode} keyed by the trade
 * itself (time, price, size, side) and kept in a small pool; a new trade adds one node at the top and the
 * existing ones just move down (translationY) — no row is ever re-recorded because its neighbours changed.
 * The zebra stripes depend on the row index, so they are drawn directly under the nodes (cheap rects).
 */
public class TapeView extends View {

    interface Host {
        double minUsd();

        int scrollRows();

        void scrollBy(int rows);

        TradeStore store();
    }

    // USD styling tiers (trades_tape.py)
    private static final double T1 = 1_000, T2 = 10_000, T3 = 50_000, T4 = 100_000;

    private final Host host;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final SimpleDateFormat timeFmt = new SimpleDateFormat("HH:mm:ss", Locale.US);
    private final float rowH, hdrH, pad;
    private final float dp3, dp10, dp40;
    private final RectF rf = new RectF();
    private final GlyphCache glN, glB, glH;
    private final HashMap<Long, String> timeCache = new HashMap<>();
    private double[][] lastRows;                   // rows of the last paint (identity: tapeRows is memoized)
    private float dragY = -1;
    private float dragAccum = 0;

    // row node pool: trade key -> node; `stamp` marks the nodes used by the current frame (LRU eviction)
    private final HashMap<Long, RenderNode> nodes = new HashMap<>();
    private final HashMap<Long, Integer> nodeStamp = new HashMap<>();
    private int stamp = 0;
    private int nodeW = -1;

    public TapeView(Context ctx, Host host) {
        super(ctx);
        this.host = host;
        setBackgroundColor(Ui.BG);
        rowH = Ui.dp(ctx, 21);
        hdrH = Ui.dp(ctx, 24);
        pad = Ui.dp(ctx, 12);
        dp3 = Ui.dp(ctx, 3);
        dp10 = Ui.dp(ctx, 10);
        dp40 = Ui.dp(ctx, 40);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(Ui.dp(ctx, 12));
        textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textB.setTextSize(Ui.dp(ctx, 12));
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
        stroke.setStyle(Paint.Style.STROKE);
        glN = new GlyphCache(text);
        glB = new GlyphCache(textB);
        glH = new GlyphCache(textH);
    }

    private GlyphCache gl(Paint p) {
        return p == textB ? glB : (p == textH ? glH : glN);
    }

    private void txt(Canvas c, String s, float x, float y, Paint p) {
        gl(p).draw(c, s, x, y, p);
    }

    /**
     * Data heartbeat: repaint ONLY when the visible rows changed (tapeRows is memoized on the store version /
     * filter / scroll / height, so an unchanged frame returns the same array).
     */
    public void maybeInvalidate() {
        int nFit = Math.max(0, (int) ((getHeight() - hdrH) / rowH));
        double[][] rows = host.store().tapeRows(host.minUsd(), host.scrollRows(), nFit);
        if (rows != lastRows) invalidate();
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragY = ev.getY();
                dragAccum = 0;
                return true;
            case MotionEvent.ACTION_MOVE:
                if (dragY >= 0) {
                    dragAccum += dragY - ev.getY();      // content follows the finger: swipe UP digs
                    dragY = ev.getY();                   // into OLDER trades, swipe DOWN returns to live
                    int rows = (int) (dragAccum / rowH);
                    if (rows != 0) {
                        dragAccum -= rows * rowH;
                        host.scrollBy(rows);             // + = older
                        invalidate();
                    }
                }
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                dragY = -1;
                return true;
        }
        return super.onTouchEvent(ev);
    }

    private float centerY(float top) {
        return top + rowH / 2f - (text.descent() + text.ascent()) / 2f;
    }

    /** HH:mm:ss of an epoch ms, cached per second. */
    private String timeStr(long ts) {
        long sec = ts / 1000L;
        String s = timeCache.get(sec);
        if (s == null) {
            if (timeCache.size() > 512) timeCache.clear();
            s = timeFmt.format(new Date(ts));
            timeCache.put(sec, s);
        }
        return s;
    }

    private static long tradeKey(double[] r) {
        long k = (long) r[0];
        k = k * 1000003L + Double.doubleToLongBits(r[1]);
        k = k * 1000003L + Double.doubleToLongBits(r[2]);
        return k * 31 + (long) r[3];
    }

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        TradeStore st = host.store();
        float y0 = 0;

        // ── header ─────────────────────────────────────────────────────────────────────────
        float cTime = pad;
        float cAmtR = w - pad;
        float cPrice = w * 0.40f;
        textH.setColor(Ui.HDR_TXT);
        textH.setTextAlign(Paint.Align.LEFT);
        txt(c, "TIME", cTime, centerY(y0) - (rowH - hdrH) / 2f, textH);
        textH.setTextAlign(Paint.Align.CENTER);
        txt(c, "PRICE (USDT)", cPrice + dp10, centerY(y0) - (rowH - hdrH) / 2f, textH);
        textH.setTextAlign(Paint.Align.RIGHT);
        txt(c, "AMOUNT (USD)", cAmtR, centerY(y0) - (rowH - hdrH) / 2f, textH);
        stroke.setColor(Ui.RULE);
        stroke.setStrokeWidth(1);
        c.drawLine(pad, y0 + hdrH - 1, w - pad, y0 + hdrH - 1, stroke);
        y0 += hdrH;

        // ── rows: newest first, filtered, offset by the scroll position ────────────────────
        int nFit = Math.max(0, (int) ((h - y0) / rowH));
        double[][] rows = st.tapeRows(host.minUsd(), host.scrollRows(), nFit);
        lastRows = rows;

        if (rows.length == 0) {
            text.setColor(Ui.WAIT_TXT);
            text.setTextAlign(Paint.Align.CENTER);
            String msg = st.tradeCount() == 0
                    ? (st.isConnected() ? "waiting for trades…" : "connecting to bridge…")
                    : "no trades ≥ filter — lower MIN SIZE";
            txt(c, msg, w / 2f, y0 + dp40, text);
            return;
        }

        if (nodeW != w) {                          // width change: every cached row was recorded for another width
            for (RenderNode n : nodes.values()) n.discardDisplayList();
            nodes.clear();
            nodeStamp.clear();
            nodeW = w;
        }
        stamp++;
        int hPx = (int) Math.ceil(rowH);
        // zebra stripes (index-dependent -> not part of a row's node)
        fill.setColor(Ui.ZEBRA);
        for (int k = 1; k < rows.length; k += 2) {
            float ry = y0 + k * rowH;
            c.drawRect(0, ry, w, ry + rowH, fill);
        }
        for (int k = 0; k < rows.length; k++) {
            double[] r = rows[k];
            long key = tradeKey(r);
            RenderNode node = nodes.get(key);
            if (node == null || !node.hasDisplayList()) {
                if (node == null) {
                    node = new RenderNode("tape-row");
                    node.setPosition(0, 0, w, hPx);
                    node.setUseCompositingLayer(true, null);
                    nodes.put(key, node);
                }
                RecordingCanvas rc = node.beginRecording(w, hPx);
                try {
                    recordRow(rc, r, w, cTime, cPrice, cAmtR);
                } finally {
                    node.endRecording();
                }
            }
            nodeStamp.put(key, stamp);
            node.setTranslationY(y0 + k * rowH);
            c.drawRenderNode(node);
        }
        // evict rows that scrolled out (keep a bounded pool so a scroll back is cheap)
        if (nodes.size() > Math.max(64, rows.length * 3)) {
            ArrayList<Long> dead = new ArrayList<>();
            for (HashMap.Entry<Long, Integer> e : nodeStamp.entrySet())
                if (e.getValue() != stamp) dead.add(e.getKey());
            for (Long k : dead) {
                RenderNode n = nodes.remove(k);
                if (n != null) n.discardDisplayList();
                nodeStamp.remove(k);
            }
        }
    }

    /** Record ONE trade row (row-local y: 0..rowH): tier styling + TIME / PRICE / AMOUNT. */
    private void recordRow(Canvas c, double[] r, int w, float cTime, float cPrice, float cAmtR) {
        float ry = 0;
        long ts = (long) r[0];
        double price = r[1], usd = r[2];
        boolean buy = r[3] > 0;
        int sideCol = buy ? Ui.BUY : Ui.SELL;
        // tier emphasis: tint (T2) -> accent bar + bold (T3) -> whale glow + gold amount (T4)
        if (usd >= T2) {
            int alpha = usd < T3 ? 16 : (usd < T4 ? 30 : 46);
            fill.setColor((sideCol & 0x00FFFFFF) | (alpha << 24));
            rf.set(3, ry + 1, w - 3, ry + rowH - 1);
            c.drawRoundRect(rf, 4, 4, fill);
        }
        if (usd >= T3) {
            fill.setColor((sideCol & 0x00FFFFFF) | (230 << 24));
            c.drawRect(3, ry + 3, 3 + dp3, ry + rowH - 3, fill);
        }
        if (usd >= T4) {
            stroke.setColor((sideCol & 0x00FFFFFF) | (90 << 24));
            stroke.setStrokeWidth(1);
            rf.set(3, ry + 1, w - 3, ry + rowH - 1);
            c.drawRoundRect(rf, 4, 4, stroke);
        }
        float ty = centerY(ry);
        text.setColor(Ui.TIME_TXT);
        text.setTextAlign(Paint.Align.LEFT);
        txt(c, timeStr(ts), cTime + (usd >= T3 ? 4 : 0), ty, text);

        Paint pp = usd >= T3 ? textB : text;
        pp.setColor(sideCol);
        pp.setTextAlign(Paint.Align.CENTER);
        txt(c, Fmt.price(price), cPrice + dp10, ty, pp);

        Paint ap = usd >= T3 ? textB : text;
        ap.setColor(usd >= T4 ? Ui.GOLD : (usd >= T1 ? Ui.AMT_TXT : Ui.DIM_TXT135));
        ap.setTextAlign(Paint.Align.RIGHT);
        txt(c, Fmt.usd(usd), cAmtR, ty, ap);
    }
}
