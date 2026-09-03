package com.smc.domtape;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;

/** Two panels side by side with a draggable vertical divider (position persisted). */
public class SplitPane extends ViewGroup {

    private static final float MIN_FRAC = 0.15f, MAX_FRAC = 0.85f;

    private final View left, right;
    private final Paint linePaint = new Paint();
    private final Paint gripPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final SharedPreferences prefs;
    private float frac;                       // divider x as a fraction of width
    private final float touchSlopPx;          // half-width of the grab strip
    private boolean dragging;

    public SplitPane(Context ctx, View leftChild, View rightChild) {
        super(ctx);
        left = leftChild;
        right = rightChild;
        addView(left);
        addView(right);
        prefs = ctx.getSharedPreferences("smcdom", Context.MODE_PRIVATE);
        frac = prefs.getFloat("split_frac", 0.5f);
        float dp = ctx.getResources().getDisplayMetrics().density;
        touchSlopPx = 16f * dp;
        linePaint.setColor(0xFF3A3F4B);
        linePaint.setStrokeWidth(Math.max(2f, 1.2f * dp));
        gripPaint.setColor(0xFF6B7280);
        setWillNotDraw(false);
        setBackgroundColor(0xFF0B0E13);
    }

    private int dividerX() {
        return Math.round(getWidth() * frac);
    }

    @Override
    protected void onMeasure(int widthSpec, int heightSpec) {
        int w = MeasureSpec.getSize(widthSpec), h = MeasureSpec.getSize(heightSpec);
        int dx = Math.round(w * frac);
        left.measure(MeasureSpec.makeMeasureSpec(dx, MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(h, MeasureSpec.EXACTLY));
        right.measure(MeasureSpec.makeMeasureSpec(w - dx, MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(h, MeasureSpec.EXACTLY));
        setMeasuredDimension(w, h);
    }

    @Override
    protected void onLayout(boolean changed, int l, int t, int r, int b) {
        int dx = dividerX();
        left.layout(0, 0, dx, getHeight());
        right.layout(dx, 0, getWidth(), getHeight());
    }

    @Override
    protected void dispatchDraw(Canvas c) {
        super.dispatchDraw(c);
        float x = dividerX();
        c.drawLine(x, 0, x, getHeight(), linePaint);
        // small grip dots at mid-height so the handle is discoverable
        float cy = getHeight() / 2f, r = linePaint.getStrokeWidth() * 1.6f;
        for (int i = -1; i <= 1; i++) c.drawCircle(x, cy + i * r * 4f, r, gripPaint);
    }

    @Override
    public boolean onInterceptTouchEvent(MotionEvent ev) {
        if (ev.getActionMasked() == MotionEvent.ACTION_DOWN
                && Math.abs(ev.getX() - dividerX()) <= touchSlopPx) {
            dragging = true;
            return true;
        }
        return false;
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragging = Math.abs(ev.getX() - dividerX()) <= touchSlopPx;
                return dragging;
            case MotionEvent.ACTION_MOVE:
                if (dragging && getWidth() > 0) {
                    frac = Math.max(MIN_FRAC, Math.min(MAX_FRAC, ev.getX() / getWidth()));
                    requestLayout();
                    invalidate();
                }
                return dragging;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (dragging) {
                    dragging = false;
                    prefs.edit().putFloat("split_frac", frac).apply();
                    return true;
                }
                return false;
        }
        return false;
    }
}
