package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;

/**
 * The tape's 60 s buy-vs-sell pressure strip as its OWN view (2026-09-06). It changes every frame (trades age
 * out of the 60 s window), while the rows below it change only when a trade above the MIN SIZE filter lands
 * — with the strip inside the rows view every heartbeat re-recorded ~130 text ops for nothing. Now the strip
 * repaints alone (2 bars + 2 labels) and TapeView repaints only when its rows actually changed.
 */
public class PressureStrip extends View {

    private static final long PRESS_MS = 60_000;

    private final TradeStore store;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textH = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final GlyphCache glyphs;
    private final RectF rf = new RectF();
    private final float pad, dp5, dp13, dp19, stripH;

    public PressureStrip(Context ctx, TradeStore store) {
        super(ctx);
        this.store = store;
        setBackgroundColor(Ui.BG);
        pad = Ui.dp(ctx, 12);
        dp5 = Ui.dp(ctx, 5);
        dp13 = Ui.dp(ctx, 13);
        dp19 = Ui.dp(ctx, 19);
        stripH = Ui.dp(ctx, 34);
        textH.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        textH.setTextSize(Ui.dp(ctx, 10));
        textH.setLetterSpacing(0.12f);
        glyphs = new GlyphCache(textH);
    }

    @Override
    protected void onMeasure(int w, int h) {
        setMeasuredDimension(MeasureSpec.getSize(w), (int) stripH);
    }

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth();
        double[] pr = store.pressure(PRESS_MS);
        double tot = pr[0] + pr[1];
        if (tot <= 0) return;
        float barY = dp19, barH = dp5;
        float bw = (float) ((w - 2 * pad) * (pr[0] / tot));
        fill.setColor(Ui.BUY);
        rf.set(pad, barY, pad + Math.max(2, bw), barY + barH);
        c.drawRoundRect(rf, 2.5f, 2.5f, fill);
        fill.setColor(Ui.SELL);
        rf.set(pad + bw + 2, barY, w - pad, barY + barH);
        c.drawRoundRect(rf, 2.5f, 2.5f, fill);
        textH.setColor(Ui.BUY);
        textH.setTextAlign(Paint.Align.LEFT);
        glyphs.draw(c, "BUY " + Fmt.usd(pr[0]) + "  " + Fmt.pct(pr[0] / tot * 100), pad, dp13, textH);
        textH.setColor(Ui.SELL);
        textH.setTextAlign(Paint.Align.RIGHT);
        glyphs.draw(c, Fmt.pct(pr[1] / tot * 100) + "  " + Fmt.usd(pr[1]) + " SELL", w - pad, dp13, textH);
    }
}
