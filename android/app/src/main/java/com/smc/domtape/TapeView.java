package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.MotionEvent;
import android.view.View;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * The painted tape body — Android port of trades_tape._TapeCanvas: 60s pressure strip +
 * TIME / PRICE / AMOUNT header + newest-first rows with tier styling (tint -> accent bar ->
 * whale glow + gold). Touch drag scrolls back (pauses); the panel's pill resumes.
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
    private static final long PRESS_MS = 60_000;

    private final Host host;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final SimpleDateFormat timeFmt = new SimpleDateFormat("HH:mm:ss", Locale.US);
    private final float rowH, hdrH, pressH, pad;
    private float dragY = -1;
    private float dragAccum = 0;

    public TapeView(Context ctx, Host host) {
        super(ctx);
        this.host = host;
        setBackgroundColor(Ui.BG);
        rowH = Ui.dp(ctx, 21);
        hdrH = Ui.dp(ctx, 24);
        pressH = Ui.dp(ctx, 34);
        pad = Ui.dp(ctx, 12);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(Ui.dp(ctx, 12));
        textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textB.setTextSize(Ui.dp(ctx, 12));
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
        stroke.setStyle(Paint.Style.STROKE);
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

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        TradeStore st = host.store();
        float y0 = 0;

        // ── 60s pressure strip (raw magnitudes, never filtered) ────────────────────────────
        double[] pr = st.pressure(PRESS_MS);
        double tot = pr[0] + pr[1];
        if (tot > 0) {
            float barY = y0 + Ui.dp(getContext(), 19), barH = Ui.dp(getContext(), 5);
            float bw = (float) ((w - 2 * pad) * (pr[0] / tot));
            fill.setColor(Ui.BUY);
            c.drawRoundRect(new RectF(pad, barY, pad + Math.max(2, bw), barY + barH), 2.5f, 2.5f, fill);
            fill.setColor(Ui.SELL);
            c.drawRoundRect(new RectF(pad + bw + 2, barY, w - pad, barY + barH), 2.5f, 2.5f, fill);
            textH.setColor(Ui.BUY);
            textH.setTextAlign(Paint.Align.LEFT);
            c.drawText(String.format(Locale.US, "BUY %s  %.0f%%", Ui.fmtUsd(pr[0]), pr[0] / tot * 100),
                    pad, y0 + Ui.dp(getContext(), 13), textH);
            textH.setColor(Ui.SELL);
            textH.setTextAlign(Paint.Align.RIGHT);
            c.drawText(String.format(Locale.US, "%.0f%%  %s SELL", pr[1] / tot * 100, Ui.fmtUsd(pr[1])),
                    w - pad, y0 + Ui.dp(getContext(), 13), textH);
        }
        y0 += pressH;

        // ── header ─────────────────────────────────────────────────────────────────────────
        float cTime = pad;
        float cAmtR = w - pad;
        float cPrice = w * 0.40f;
        textH.setColor(Ui.HDR_TXT);
        textH.setTextAlign(Paint.Align.LEFT);
        c.drawText("TIME", cTime, centerY(y0) - (rowH - hdrH) / 2f, textH);
        textH.setTextAlign(Paint.Align.CENTER);
        c.drawText("PRICE (USDT)", cPrice + Ui.dp(getContext(), 10), centerY(y0) - (rowH - hdrH) / 2f, textH);
        textH.setTextAlign(Paint.Align.RIGHT);
        c.drawText("AMOUNT (USD)", cAmtR, centerY(y0) - (rowH - hdrH) / 2f, textH);
        stroke.setColor(Ui.RULE);
        stroke.setStrokeWidth(1);
        c.drawLine(pad, y0 + hdrH - 1, w - pad, y0 + hdrH - 1, stroke);
        y0 += hdrH;

        // ── rows: newest first, filtered, offset by the scroll position ────────────────────
        int nFit = Math.max(0, (int) ((h - y0) / rowH));
        double[][] rows = st.tapeRows(host.minUsd(), host.scrollRows(), nFit);

        if (rows.length == 0) {
            text.setColor(Ui.WAIT_TXT);
            text.setTextAlign(Paint.Align.CENTER);
            String msg = st.tradeCount() == 0
                    ? (st.isConnected() ? "waiting for trades…" : "connecting to bridge…")
                    : "no trades ≥ filter — lower MIN SIZE";
            c.drawText(msg, w / 2f, y0 + Ui.dp(getContext(), 40), text);
            return;
        }

        for (int k = 0; k < rows.length; k++) {
            float ry = y0 + k * rowH;
            long ts = (long) rows[k][0];
            double price = rows[k][1], usd = rows[k][2];
            boolean buy = rows[k][3] > 0;
            int sideCol = buy ? Ui.BUY : Ui.SELL;
            if ((k & 1) == 1) {
                fill.setColor(Ui.ZEBRA);
                c.drawRect(0, ry, w, ry + rowH, fill);
            }
            // tier emphasis: tint (T2) -> accent bar + bold (T3) -> whale glow + gold amount (T4)
            if (usd >= T2) {
                int alpha = usd < T3 ? 16 : (usd < T4 ? 30 : 46);
                fill.setColor((sideCol & 0x00FFFFFF) | (alpha << 24));
                c.drawRoundRect(new RectF(3, ry + 1, w - 3, ry + rowH - 1), 4, 4, fill);
            }
            if (usd >= T3) {
                fill.setColor((sideCol & 0x00FFFFFF) | (230 << 24));
                c.drawRect(3, ry + 3, 3 + Ui.dp(getContext(), 3), ry + rowH - 3, fill);
            }
            if (usd >= T4) {
                stroke.setColor((sideCol & 0x00FFFFFF) | (90 << 24));
                c.drawRoundRect(new RectF(3, ry + 1, w - 3, ry + rowH - 1), 4, 4, stroke);
            }

            float ty = centerY(ry);
            text.setColor(Ui.TIME_TXT);
            text.setTextAlign(Paint.Align.LEFT);
            c.drawText(timeFmt.format(new Date(ts)), cTime + (usd >= T3 ? 4 : 0), ty, text);

            Paint pp = usd >= T3 ? textB : text;
            pp.setColor(sideCol);
            pp.setTextAlign(Paint.Align.CENTER);
            c.drawText(String.format(Locale.US, "%,.2f", price), cPrice + Ui.dp(getContext(), 10), ty, pp);

            Paint ap = usd >= T3 ? textB : text;
            ap.setColor(usd >= T4 ? Ui.GOLD : (usd >= T1 ? Ui.AMT_TXT : Ui.DIM_TXT135));
            ap.setTextAlign(Paint.Align.RIGHT);
            c.drawText(Ui.fmtUsd(usd), cAmtR, ty, ap);
        }
    }
}
