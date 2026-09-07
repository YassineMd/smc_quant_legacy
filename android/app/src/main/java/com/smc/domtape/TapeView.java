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
import java.util.HashSet;
import java.util.Locale;

/**
 * The painted tape body — Android port of trades_tape._TapeCanvas: TIME / PRICE / AMOUNT header +
 * newest-first rows with tier styling (tint -> accent bar -> whale glow + gold). Touch drag scrolls
 * back (pauses); the panel's pill resumes. The 60 s pressure strip is {@link PressureStrip}, its own view.
 *
 * MERGED PLAYERS (user 2026-09-07): a row with n > 1 fills is ONE order that ate through the book (the
 * terminal's diamonds): "hh:mm:ss" | "price (±N)" | the summed amount. TAP the row to drop
 * down its fills (time to the ms, price, size), tap again to fold.
 *
 * RENDERING (2026-09-06): every row is recorded ONCE into a {@link RenderNode} keyed by its content and kept
 * in a small pool; new rows add one node at the top and the rest just move (translationY). Zebra stripes
 * and the transient detail rows are drawn directly (cheap rects / a few texts only while expanded).
 */
public class TapeView extends View {

    interface Host {
        double minUsd();

        int scrollRows();

        void scrollBy(int rows);

        TradeStore store();

        /** The rows to show (newest first, `skip` rows scrolled, at most `nFit`). Default: the live tape. */
        default double[][] rows(TradeStore st, double minUsd, int skip, int nFit) {
            return st.tapeRows(minUsd, skip, nFit);
        }

        /** Text for an empty list; null = the live tape's own messages. */
        default String emptyText() {
            return null;
        }
    }

    // USD styling tiers (trades_tape.py)
    private static final double T1 = 1_000, T2 = 10_000, T3 = 50_000, T4 = 100_000;

    private final Host host;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textS = new Paint(Paint.ANTI_ALIAS_FLAG);   // detail rows (smaller)
    private final SimpleDateFormat timeFmt = new SimpleDateFormat("HH:mm:ss", Locale.US);
    private final SimpleDateFormat timeFmtMs = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US);
    private final float rowH, hdrH, pad, detH;
    private final float dp3, dp10, dp40, tapSlop;
    private final RectF rf = new RectF();
    private final GlyphCache glN, glB, glH, glS;
    private final HashMap<Long, String> timeCache = new HashMap<>();
    private double[][] lastRows;                   // rows of the last paint (identity: tapeRows is memoized)
    private float dragY = -1, downX = -1, downY = -1;
    private float dragAccum = 0;
    private boolean dragged;

    // expanded merged rows (by row key) + the y-layout of the last paint for the tap hit test
    private final HashSet<Long> expanded = new HashSet<>();
    private final ArrayList<float[]> hit = new ArrayList<>();   // [top, bottom, isMerged]
    private final ArrayList<Long> hitKeys = new ArrayList<>();  // the row key (a long: never squeeze it into a float)
    private final HashMap<Long, double[][]> detailCache = new HashMap<>();

    // row node pool: row key -> node; `stamp` marks the nodes used by the current frame (LRU eviction)
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
        detH = Ui.dp(ctx, 18);
        dp3 = Ui.dp(ctx, 3);
        dp10 = Ui.dp(ctx, 10);
        dp40 = Ui.dp(ctx, 40);
        tapSlop = Ui.dp(ctx, 8);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(Ui.dp(ctx, 12));
        textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textB.setTextSize(Ui.dp(ctx, 12));
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
        textS.setTypeface(Typeface.MONOSPACE);
        textS.setTextSize(Ui.dp(ctx, 10.5f));
        stroke.setStyle(Paint.Style.STROKE);
        glN = new GlyphCache(text);
        glB = new GlyphCache(textB);
        glH = new GlyphCache(textH);
        glS = new GlyphCache(textS);
    }

    private GlyphCache gl(Paint p) {
        return p == textB ? glB : (p == textH ? glH : (p == textS ? glS : glN));
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
        double[][] rows = host.rows(host.store(), host.minUsd(), host.scrollRows(), nFit);
        if (rows != lastRows) invalidate();
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragY = ev.getY();
                downX = ev.getX();
                downY = ev.getY();
                dragAccum = 0;
                dragged = false;
                return true;
            case MotionEvent.ACTION_MOVE:
                if (dragY >= 0) {
                    if (Math.abs(ev.getY() - downY) > tapSlop || Math.abs(ev.getX() - downX) > tapSlop) dragged = true;
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
                if (!dragged && downY >= 0) toggleAt(downY);   // a TAP on a merged row drops its fills down
                dragY = -1;
                downY = -1;
                return true;
            case MotionEvent.ACTION_CANCEL:
                dragY = -1;
                downY = -1;
                return true;
        }
        return super.onTouchEvent(ev);
    }

    private void toggleAt(float y) {
        for (int i = 0; i < hit.size(); i++) {
            float[] h = hit.get(i);
            if (y >= h[0] && y < h[1]) {
                if (h[2] > 0) {
                    long key = hitKeys.get(i);
                    if (!expanded.remove(key)) expanded.add(key);
                    invalidate();
                }
                return;
            }
        }
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

    private static boolean merged(double[] r) {
        return r.length > 4 && r[4] > 1;
    }

    private static long rowKey(double[] r) {
        long k = (long) r[0];
        k = k * 1000003L + Double.doubleToLongBits(r[1]);
        k = k * 1000003L + Double.doubleToLongBits(r[2]);
        k = k * 31 + (long) r[3];
        if (r.length > 4) k = k * 1000003L + (long) r[4];
        return k;
    }

    private static String ticksStr(double ticks) {          // "+4" / "-9" -- the unit is obvious (user 2026-09-07)
        long t = Math.round(ticks);
        return (t > 0 ? "+" : "") + t;
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
        double[][] rows = host.rows(st, host.minUsd(), host.scrollRows(), nFit);
        lastRows = rows;
        hit.clear();
        hitKeys.clear();

        if (rows.length == 0) {
            text.setColor(Ui.WAIT_TXT);
            text.setTextAlign(Paint.Align.CENTER);
            String msg = host.emptyText();
            if (msg == null)
                msg = st.tradeCount() == 0
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
        float y = y0;
        for (int k = 0; k < rows.length && y < h; k++) {
            double[] r = rows[k];
            long key = rowKey(r);
            boolean mg = merged(r);
            if ((k & 1) == 1) {                    // zebra (index-dependent -> not part of a row's node)
                fill.setColor(Ui.ZEBRA);
                c.drawRect(0, y, w, y + rowH, fill);
            }
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
            node.setTranslationY(y);
            c.drawRenderNode(node);
            hit.add(new float[]{y, y + rowH, mg ? 1 : 0});
            hitKeys.add(key);
            y += rowH;
            if (mg && expanded.contains(key)) y = drawDetails(c, r, key, y, h, w, cTime, cPrice, cAmtR);
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
            if (detailCache.size() > 64) detailCache.clear();
        }
    }

    /** The dropped-down fills of a merged row: "  ↳ hh:mm:ss.mmm   price   $size", newest first, dim. */
    private float drawDetails(Canvas c, double[] r, long key, float y, int h, int w, float cTime, float cPrice, float cAmtR) {
        double[][] det = detailCache.get(key);
        if (det == null) {
            det = host.store().groupTrades((long) r[0], (long) r[7], (int) r[3]);
            detailCache.put(key, det);
        }
        boolean buy = r[3] > 0;
        int sideCol = buy ? Ui.BUY : Ui.SELL;
        fill.setColor((sideCol & 0x00FFFFFF) | (14 << 24));
        float y1 = Math.min(h, y + det.length * detH);
        c.drawRect(0, y, w, y1, fill);
        for (double[] d : det) {
            if (y + detH > h) break;
            float ty = y + detH / 2f - (textS.descent() + textS.ascent()) / 2f;
            textS.setColor(Ui.TIME_TXT);
            textS.setTextAlign(Paint.Align.LEFT);
            txt(c, "↳ " + timeFmtMs.format(new Date((long) d[0])), cTime + dp10, ty, textS);
            textS.setColor((sideCol & 0x00FFFFFF) | (200 << 24));
            textS.setTextAlign(Paint.Align.CENTER);
            txt(c, Fmt.price(d[1]), cPrice + dp10, ty, textS);
            textS.setColor(Ui.DIM_TXT135);
            textS.setTextAlign(Paint.Align.RIGHT);
            txt(c, Fmt.usd(d[2]), cAmtR, ty, textS);
            y += detH;
        }
        stroke.setColor(Ui.RULE);
        stroke.setStrokeWidth(1);
        c.drawLine(pad, y - 0.5f, w - pad, y - 0.5f, stroke);
        return y;
    }

    /** Record ONE row (row-local y: 0..rowH): tier styling + TIME / PRICE / AMOUNT (merged: "price (±N)"). */
    private void recordRow(Canvas c, double[] r, int w, float cTime, float cPrice, float cAmtR) {
        float ry = 0;
        long ts = (long) r[0];
        double price = r[1], usd = r[2];
        boolean buy = r[3] > 0;
        boolean mg = merged(r);
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
        String tstr = timeStr(ts);                 // no "(+Ns)" span: a player is a few ms wide (user 2026-09-07)
        txt(c, tstr, cTime + (usd >= T3 ? 4 : 0), ty, text);

        Paint pp = usd >= T3 ? textB : text;
        pp.setColor(sideCol);
        pp.setTextAlign(Paint.Align.CENTER);
        String pstr = Fmt.price(price);
        if (mg) pstr = pstr + " (" + ticksStr(r[5]) + ")";
        txt(c, pstr, cPrice + dp10, ty, pp);

        Paint ap = usd >= T3 ? textB : text;
        ap.setColor(usd >= T4 ? Ui.GOLD : (usd >= T1 ? Ui.AMT_TXT : Ui.DIM_TXT135));
        ap.setTextAlign(Paint.Align.RIGHT);
        txt(c, Fmt.usd(usd), cAmtR, ty, ap);
    }
}
