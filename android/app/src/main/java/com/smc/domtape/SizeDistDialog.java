package com.smc.domtape;

import android.app.Dialog;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.view.MotionEvent;
import android.view.View;
import android.view.Window;

import java.util.Locale;

/**
 * Aggressor size-distribution popup — Android port of size_dist.SizeDistPopup: BUYERS green /
 * SELLERS red count curves on a shared log-x ($10..$500K, the MIN SIZE slider's domain), header
 * stats per side, decade gridlines, the current filter as a gold dashed line, and a hover/tap
 * readout. TAP sets the owner's MIN SIZE filter at that size; refreshes 1/s while open.
 */
public class SizeDistDialog extends Dialog {

    public interface Owner {
        TradeStore.SizeSamples samples();

        String scope();

        double getMin();

        void setMin(double usd);
    }

    private static final double LOG_LO = 1.0, LOG_HI = Math.log10(500_000.0);
    private static final int NBINS = 56;

    private final Owner owner;
    private final PlotView plot;

    public SizeDistDialog(Context ctx, Owner owner) {
        super(ctx);
        this.owner = owner;
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        plot = new PlotView(ctx);
        setContentView(plot);
        if (getWindow() != null) {
            getWindow().setBackgroundDrawable(new ColorDrawable(Ui.BG));
            getWindow().setLayout((int) Ui.dp(ctx, 640), (int) Ui.dp(ctx, 400));
        }
    }

    private static String fmt(double v) {
        if (v >= 1_000_000) return String.format(Locale.US, "$%.2fM", v / 1_000_000);
        if (v >= 1_000) return String.format(Locale.US, "$%.1fK", v / 1_000);
        return String.format(Locale.US, "$%.0f", v);
    }

    private class PlotView extends View {

        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint line = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint textB = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float padL, padR, padT, padB;
        private Float hoverX = null;
        private final Runnable refresh = new Runnable() {
            @Override
            public void run() {
                invalidate();
                postDelayed(this, 1000);
            }
        };

        PlotView(Context ctx) {
            super(ctx);
            padL = Ui.dp(ctx, 16);
            padR = Ui.dp(ctx, 16);
            padT = Ui.dp(ctx, 66);
            padB = Ui.dp(ctx, 34);
            text.setTypeface(Typeface.MONOSPACE);
            text.setTextSize(Ui.dp(ctx, 11));
            textB.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
            textB.setTextSize(Ui.dp(ctx, 11));
            line.setStyle(Paint.Style.STROKE);
        }

        @Override
        protected void onAttachedToWindow() {
            super.onAttachedToWindow();
            postDelayed(refresh, 1000);
        }

        @Override
        protected void onDetachedFromWindow() {
            removeCallbacks(refresh);
            super.onDetachedFromWindow();
        }

        private RectF plotRect() {
            return new RectF(padL, padT, getWidth() - padR, getHeight() - padB);
        }

        private double usdAt(float x) {
            RectF r = plotRect();
            double t = Math.min(1.0, Math.max(0.0, (x - r.left) / Math.max(1f, r.width())));
            return Math.pow(10.0, LOG_LO + t * (LOG_HI - LOG_LO));
        }

        private float xOf(double usd) {
            RectF r = plotRect();
            double t = (Math.log10(Math.max(10.0, Math.min(500_000.0, usd))) - LOG_LO) / (LOG_HI - LOG_LO);
            return (float) (r.left + t * r.width());
        }

        @Override
        public boolean onTouchEvent(MotionEvent ev) {
            if (ev.getActionMasked() == MotionEvent.ACTION_DOWN
                    || ev.getActionMasked() == MotionEvent.ACTION_MOVE) {
                hoverX = ev.getX();
                if (ev.getActionMasked() == MotionEvent.ACTION_DOWN)
                    owner.setMin(usdAt(ev.getX()));    // tap = set the filter (gold line follows)
                invalidate();
                return true;
            }
            return super.onTouchEvent(ev);
        }

        @Override
        public boolean onHoverEvent(MotionEvent ev) {
            if (ev.getActionMasked() == MotionEvent.ACTION_HOVER_EXIT) hoverX = null;
            else hoverX = ev.getX();
            invalidate();
            return true;
        }

        @Override
        protected void onDraw(Canvas c) {
            int w = getWidth(), h = getHeight();
            RectF r = plotRect();
            TradeStore.SizeSamples s = owner.samples();

            // header: scope + per-side stats (n / median / p90 — factual, no fit claimed)
            textB.setColor(Ui.TXT);
            textB.setTextAlign(Paint.Align.LEFT);
            c.drawText("AGGRESSOR TRADE SIZES · " + owner.scope(), padL, Ui.dp(getContext(), 18), textB);
            int nb = 0;
            for (boolean b : s.buy) if (b) nb++;
            for (int side = 0; side < 2; side++) {
                boolean isBuy = side == 0;
                int cnt = isBuy ? nb : s.usd.length - nb;
                String txt;
                if (cnt > 0) {
                    double[] v = new double[cnt];
                    double sum = 0;
                    int k = 0;
                    for (int i = 0; i < s.usd.length; i++)
                        if (s.buy[i] == isBuy) {
                            v[k++] = s.usd[i];
                            sum += s.usd[i];
                        }
                    java.util.Arrays.sort(v);
                    double med = v[cnt / 2];
                    double p90 = v[Math.min(cnt - 1, (int) (cnt * 0.9))];
                    txt = String.format(Locale.US, "%s  n %,d   median %s   p90 %s   vol %s",
                            isBuy ? "BUYERS " : "SELLERS", cnt, fmt(med), fmt(p90), fmt(sum));
                } else {
                    txt = (isBuy ? "BUYERS " : "SELLERS") + "  (none in window)";
                }
                text.setColor(isBuy ? Ui.BUY : Ui.SELL);
                text.setTextAlign(Paint.Align.LEFT);
                c.drawText(txt, padL, Ui.dp(getContext(), isBuy ? 36 : 52), text);
            }

            // axes: decade gridlines + labels
            for (double dec : new double[]{10, 100, 1_000, 10_000, 100_000}) {
                float x = xOf(dec);
                line.setColor(Color.argb(14, 255, 255, 255));
                line.setStrokeWidth(1);
                line.setPathEffect(null);
                c.drawLine(x, r.top, x, r.bottom, line);
                text.setColor(Ui.HDR_TXT);
                text.setTextAlign(Paint.Align.CENTER);
                c.drawText(fmt(dec), x, r.bottom + Ui.dp(getContext(), 16), text);
            }
            line.setColor(Color.argb(30, 255, 255, 255));
            c.drawLine(r.left, r.bottom, r.right, r.bottom, line);

            if (s.usd.length == 0) {
                text.setColor(Ui.HDR_TXT);
                text.setTextAlign(Paint.Align.CENTER);
                c.drawText("no trades in the window yet…", r.centerX(), r.centerY(), text);
                return;
            }

            // log-binned counts per side, lightly smoothed -> the two distribution curves
            double[] hb = new double[NBINS], hs = new double[NBINS];
            for (int i = 0; i < s.usd.length; i++) {
                double lg = Math.log10(Math.max(10.0, Math.min(500_000.0, s.usd[i])));
                int bi = Math.min(NBINS - 1, (int) ((lg - LOG_LO) / (LOG_HI - LOG_LO) * NBINS));
                if (s.buy[i]) hb[bi]++;
                else hs[bi]++;
            }
            double[] kern = {1, 2, 3, 2, 1};
            hb = smooth(hb, kern);
            hs = smooth(hs, kern);
            double top = 1;
            for (int i = 0; i < NBINS; i++) top = Math.max(top, Math.max(hb[i], hs[i]));
            float[] xs = new float[NBINS];
            for (int i = 0; i < NBINS; i++) {
                double center = LOG_LO + (i + 0.5) * (LOG_HI - LOG_LO) / NBINS;
                xs[i] = (float) (r.left + (center - LOG_LO) / (LOG_HI - LOG_LO) * r.width());
            }
            for (int side = 0; side < 2; side++) {             // sellers first, buyers on top
                double[] hist = side == 0 ? hs : hb;
                int col = side == 0 ? Ui.SELL : Ui.BUY;
                Path path = new Path();
                path.moveTo(xs[0], r.bottom);
                for (int i = 0; i < NBINS; i++)
                    path.lineTo(xs[i], (float) (r.bottom - hist[i] / top * (r.height() - 8)));
                path.lineTo(xs[NBINS - 1], r.bottom);
                path.close();
                fill.setColor((col & 0x00FFFFFF) | (60 << 24));
                c.drawPath(path, fill);
                line.setColor(col);
                line.setStrokeWidth(Ui.dp(getContext(), 1.6f));
                line.setPathEffect(null);
                c.drawPath(path, line);
            }

            // current MIN SIZE filter — gold dashed, tracks the slider both ways
            double cur = owner.getMin();
            if (cur > 0) {
                float x = xOf(cur);
                line.setColor(Ui.GOLD);
                line.setStrokeWidth(1.5f);
                line.setPathEffect(new DashPathEffect(new float[]{8, 6}, 0));
                c.drawLine(x, r.top, x, r.bottom, line);
                line.setPathEffect(null);
                textB.setColor(Ui.GOLD);
                textB.setTextAlign(Paint.Align.CENTER);
                c.drawText("min " + fmt(cur), x, r.top + Ui.dp(getContext(), 14), textB);
            }

            // hover/tap readout: vline + bin counts + cumulative volume share at-or-above
            if (hoverX != null && hoverX >= r.left && hoverX <= r.right) {
                float hx = hoverX;
                double husd = usdAt(hx);
                line.setColor(Color.argb(150, 200, 208, 220));
                line.setStrokeWidth(1);
                line.setPathEffect(new DashPathEffect(new float[]{6, 5}, 0));
                c.drawLine(hx, r.top, hx, r.bottom, line);
                line.setPathEffect(null);
                int bi = Math.min(NBINS - 1, Math.max(0,
                        (int) ((Math.log10(Math.max(10.0, husd)) - LOG_LO) / (LOG_HI - LOG_LO) * NBINS)));
                double above = 0, tot = 0;
                int nAbove = 0;
                for (double v : s.usd) {
                    tot += v;
                    if (v >= husd) {
                        above += v;
                        nAbove++;
                    }
                }
                double share = tot > 0 ? above / tot * 100.0 : 0;
                text.setColor(Ui.TXT);
                text.setTextAlign(Paint.Align.LEFT);
                c.drawText(String.format(Locale.US,
                                "%s   bin: buy %.0f / sell %.0f   ≥ here: %,d trades · %.0f%% of volume   (tap = set filter)",
                                fmt(husd), hb[bi], hs[bi], nAbove, share),
                        padL, h - Ui.dp(getContext(), 10), text);
            }
        }

        private double[] smooth(double[] a, double[] kern) {
            double ks = 0;
            for (double v : kern) ks += v;
            double[] out = new double[a.length];
            int half = kern.length / 2;
            for (int i = 0; i < a.length; i++) {
                double acc = 0;
                for (int j = 0; j < kern.length; j++) {
                    int idx = i + j - half;
                    if (idx >= 0 && idx < a.length) acc += a[idx] * kern[j];
                }
                out[i] = acc / ks;
            }
            return out;
        }
    }
}
