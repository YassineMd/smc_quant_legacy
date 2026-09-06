package com.smc.domtape;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;
import android.view.animation.DecelerateInterpolator;

/**
 * The live-price chip of the DOM ladder as its OWN overlay view (2026-09-06): a rounded pill in the taker
 * side's colour with the last price in white bold, sitting over the price column of the last trade's row.
 * Being a separate view it moves as a hardware-animated property (translationY) — the ladder's cached rows
 * are never re-recorded for it — so a price tick SLIDES smoothly to its new level (160 ms, decelerating)
 * instead of jumping. The ladder tells it where to be every frame; when the ladder itself scrolled the chip
 * jumps (animating there would lag the rows). Opaque fill = the old translucent chip blended over the price
 * column background, so the row's dim price text underneath never shows through.
 */
public class PriceChip extends View {

    private static final long SLIDE_MS = 160;
    private static final boolean LOG = false;        // dev: slide / snap / keep events to logcat (CHIP)

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final GlyphCache glyphs;
    private final RectF rf = new RectF();
    private String price = "";
    private int side = -1;
    private float w = 0, h = 0;
    private float targetY = Float.NaN;
    private static final int BUY_BG = blend(Ui.BUY, Ui.PRICE_BG, 175);
    private static final int SELL_BG = blend(Ui.SELL, Ui.PRICE_BG, 175);

    public PriceChip(Context ctx) {
        super(ctx);
        text.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        text.setTextSize(Ui.dp(ctx, 11));
        text.setColor(Color.WHITE);
        text.setTextAlign(Paint.Align.CENTER);
        glyphs = new GlyphCache(text);
        setClickable(false);
        setFocusable(false);
        setVisibility(INVISIBLE);
    }

    /** `fg` over `bg` at alpha a/255 -> an opaque colour. */
    private static int blend(int fg, int bg, int a) {
        int r = (Color.red(fg) * a + Color.red(bg) * (255 - a)) / 255;
        int g = (Color.green(fg) * a + Color.green(bg) * (255 - a)) / 255;
        int b = (Color.blue(fg) * a + Color.blue(bg) * (255 - a)) / 255;
        return Color.rgb(r, g, b);
    }

    /** Place the chip over the price column at (x, y) with size (w, h); `animate` slides it from where it is. */
    public void show(float x, float y, float w, float h, int side, String price, boolean animate) {
        boolean resized = this.w != w || this.h != h;
        if (resized) {
            this.w = w;
            this.h = h;
            setLayoutParams(new android.widget.FrameLayout.LayoutParams(Math.round(w), Math.round(h)));
        }
        boolean restyled = this.side != side || !price.equals(this.price);
        this.side = side;
        this.price = price;
        setX(x);
        if (getVisibility() != VISIBLE) {
            animate = false;
            setVisibility(VISIBLE);
        }
        boolean sameTarget = !Float.isNaN(targetY) && Math.abs(targetY - y) <= 0.5f;
        if (sameTarget) {
            // already there, or already sliding there: a book / heartbeat frame landing mid-slide must NOT
            // cancel the animation (that is why the slide "sometimes" did not show)
            if (LOG) android.util.Log.i("CHIP", "keep  y=" + y + (animate().getDuration() > 0 ? "" : ""));
        } else if (animate && !Float.isNaN(targetY)) {
            float from = getTranslationY();
            targetY = y;
            animate().cancel();
            animate().translationY(y).setDuration(SLIDE_MS).setInterpolator(new DecelerateInterpolator()).start();
            if (LOG) android.util.Log.i("CHIP", "slide " + from + " -> " + y + " (" + price + ")");
        } else {
            animate().cancel();
            targetY = y;
            setTranslationY(y);
            if (LOG) android.util.Log.i("CHIP", "snap  y=" + y + " (" + price + ")" + (animate ? " [first]" : " [ladder moved]"));
        }
        if (restyled || resized) invalidate();
    }

    public void hide() {
        if (getVisibility() == VISIBLE) {
            animate().cancel();
            setVisibility(INVISIBLE);
            targetY = Float.NaN;
        }
    }

    @Override
    protected void onDraw(Canvas c) {
        if (side < 0 || w <= 0) return;
        fill.setColor(side > 0 ? BUY_BG : SELL_BG);
        rf.set(0, 1.5f, w, h - 1.5f);
        c.drawRoundRect(rf, 3, 3, fill);
        float ty = h / 2f - (text.descent() + text.ascent()) / 2f;
        glyphs.draw(c, price, w / 2f, ty, text);
    }
}
