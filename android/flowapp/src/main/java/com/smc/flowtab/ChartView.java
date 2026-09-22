package com.smc.flowtab;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Locale;

/**
 * The stacked Flow-mode panes on one shared clock axis: PRICE (one candle per cycle), BUY/SELL FLOW (the two $
 * lines + the cycle start lines and their badges), LIMIT ORDERS, INTEREST x IMPACT (every dropdown option). The
 * numbers are the engine's (FlowModel); the drawing rules are the terminal's, pane by pane.
 *
 * Touch: one finger pans the clock, a pinch zooms it about the fingers; a double tap on a pane makes it the only
 * pane (and again restores the stack); a double tap on the right axis or the clock strip re-centres on the live
 * edge. Panning back to the live edge re-arms the follow, as on the terminal.
 */
public final class ChartView extends View {
    public interface Host {
        void onViewChanged(double x0, double x1, boolean follow);
        void onExplain(double k);
        void onModeMenu(float x, float y);
        void onFullscreen(boolean on);
    }

    public static final int PANE_PRICE = 0, PANE_FLOW = 1, PANE_LIQ = 2, PANE_IIMP = 3;
    private final FlowModel M;
    private Host host;
    private final float d;
    // toggles (the hamburger's)
    public boolean showPrice = true, showFlow = true, showLiq = true, showIimp = true, showLines = true, showTakeover = true;
    // the view
    private double vx0, vx1; private boolean follow = true;
    private int fullscreen = -1;
    private long lastViewSent = 0;
    // y fits with dead-bands, per pane
    private double pxLo = Double.NaN, pxHi = Double.NaN, flowTop = 0, liqTop = 0, iimpTop = 0;
    private final RectF[] pane = {new RectF(), new RectF(), new RectF(), new RectF()};
    private final boolean[] paneOn = new boolean[4];
    private float plotR, timeY;
    private final float AXIS_W, TAXIS_H, TITLE_H;
    // selection on the I x I pane
    private double selT = Double.NaN;
    private float ddX0, ddX1, ddY0, ddY1;         // the dropdown button
    // paints
    private final Paint pl = new Paint(Paint.ANTI_ALIAS_FLAG), pf = new Paint(Paint.ANTI_ALIAS_FLAG), pt = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path(), path2 = new Path();
    private final GestureDetector gest; private final ScaleGestureDetector scale;
    private final Handler h = new Handler(Looper.getMainLooper());
    private boolean framePending = false;
    private final Runnable heartbeat = new Runnable() { @Override public void run() { if (follow) invalidate(); h.postDelayed(this, 250); } };
    private static final int TEAL = Color.parseColor("#26a69a"), RED = Color.parseColor("#ef5350"), ORANGE = Color.parseColor("#ff9f43");
    private static final int BG = Color.parseColor("#141414"), FG = Color.parseColor("#dcdcdc"), TITLE = Color.parseColor("#7d8492");
    private static final int WEAK = Color.parseColor("#8a919c"), GUIDE = Color.parseColor("#9aa4b2");
    private static final int B_UP = Color.rgb(26, 154, 96), B_DN = Color.rgb(208, 48, 48), B_CONTRA = Color.rgb(222, 130, 0), B_FLAT = Color.rgb(122, 130, 140);
    private static final int[] BAR_COL = {Color.parseColor("#FF9500"), Color.parseColor("#00C853"), Color.parseColor("#FF1F1F"), Color.parseColor("#E2574C"), Color.parseColor("#6B7A82"), Color.parseColor("#4E5C64"), Color.parseColor("#2979FF")};
    private static final int C_AB_BUY = 0, C_BRK_BUY = 1, C_BRK_SELL = 2, C_AB_SELL = 6;
    private static final double LN2 = Math.log(2.0);

    public ChartView(Context ctx, FlowModel model) {
        super(ctx);
        M = model;
        d = getResources().getDisplayMetrics().density;
        AXIS_W = 64 * d; TAXIS_H = 22 * d; TITLE_H = 16 * d;
        double now = System.currentTimeMillis() / 1000.0;
        vx1 = now; vx0 = now - 3600.0;
        pl.setStyle(Paint.Style.STROKE); pf.setStyle(Paint.Style.FILL); pt.setTypeface(Typeface.MONOSPACE);
        gest = new GestureDetector(ctx, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent e) { return true; }
            @Override public boolean onScroll(MotionEvent e1, MotionEvent e2, float dx, float dy) {
                if (scale.isInProgress()) return true;
                double span = vx1 - vx0; double dt = dx / Math.max(1f, plotR) * span;
                vx0 += dt; vx1 += dt;
                afterPan(); return true;
            }
            @Override public boolean onSingleTapConfirmed(MotionEvent e) { tap(e.getX(), e.getY()); return true; }
            @Override public boolean onDoubleTap(MotionEvent e) { doubleTap(e.getX(), e.getY()); return true; }
        });
        scale = new ScaleGestureDetector(ctx, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override public boolean onScale(ScaleGestureDetector sd) {
                double span = vx1 - vx0; double f = 1.0 / Math.max(0.2f, Math.min(5f, sd.getScaleFactor()));
                double ns = Math.max(30.0, Math.min(72 * 3600.0, span * f));
                double focal = vx0 + (sd.getFocusX() / Math.max(1f, plotR)) * span;
                double frac = (focal - vx0) / span;
                vx0 = focal - frac * ns; vx1 = vx0 + ns;
                afterPan(); return true;
            }
        });
        h.postDelayed(heartbeat, 250);
    }

    public void setHost(Host host) { this.host = host; }

    /** A data frame: coalesced onto the next vsync. */
    public void dataChanged() {
        if (framePending) return;
        framePending = true;
        postOnAnimation(() -> { framePending = false; invalidate(); });
    }

    public void recentre() { follow = true; pxLo = pxHi = Double.NaN; sendView(true); invalidate(); }

    public void setFullscreen(int p) { fullscreen = p; if (host != null) host.onFullscreen(p >= 0); invalidate(); }

    public int getFullscreen() { return fullscreen; }

    public void clearSelection() { selT = Double.NaN; invalidate(); }

    private void afterPan() {
        double now = M.nowEngine();
        follow = (now - vx1) >= -1.0 && (now - vx1) <= 3.0;
        long t = System.currentTimeMillis();
        if (t - lastViewSent > 150) sendView(false);
        invalidate();
    }

    private void sendView(boolean force) {
        lastViewSent = System.currentTimeMillis();
        if (host != null) host.onViewChanged(vx0, vx1, follow);
    }

    @Override public boolean onTouchEvent(MotionEvent ev) {
        scale.onTouchEvent(ev);
        gest.onTouchEvent(ev);
        if (ev.getActionMasked() == MotionEvent.ACTION_UP || ev.getActionMasked() == MotionEvent.ACTION_CANCEL) sendView(true);
        return true;
    }

    // ------------------------------------------------------------------ taps
    private int paneAt(float x, float y) {
        for (int p = 0; p < 4; p++) if (paneOn[p] && pane[p].contains(x, y)) return p;
        return -1;
    }

    private void doubleTap(float x, float y) {
        boolean onAxis = x >= plotR || y >= timeY;
        if (onAxis) { recentre(); return; }
        int p = paneAt(x, y);
        if (p < 0) return;
        setFullscreen(fullscreen == p ? -1 : p);
    }

    private void tap(float x, float y) {
        if (paneOn[PANE_IIMP] && x >= ddX0 && x <= ddX1 && y >= ddY0 && y <= ddY1) {
            if (host != null) host.onModeMenu(ddX0, ddY1);
            return;
        }
        if (paneOn[PANE_IIMP] && pane[PANE_IIMP].contains(x, y) && x < plotR) {
            double t = vx0 + (x / plotR) * (vx1 - vx0);
            double[] x0, x1; synchronized (M.lock) { x0 = M.iX0; x1 = M.iX1; }
            int hit = -1; double best = Double.MAX_VALUE;
            for (int i = 0; i < x0.length; i++) {
                if (x0[i] <= t && x1[i] >= t) { hit = i; break; }
                double dm = Math.abs(0.5 * (x0[i] + x1[i]) - t);
                if (dm < best) { best = dm; hit = -2 - i; }
            }
            if (hit < -1) {
                int i = -2 - hit; double span = x1[i] - x0[i];
                if (best > Math.max(2.0 * span, 30.0)) { selT = Double.NaN; invalidate(); return; }
                hit = i;
            }
            if (hit >= 0) {
                if (!Double.isNaN(selT) && Math.abs(selT - x0[hit]) < 1.0) { selT = Double.NaN; invalidate(); return; }
                selT = x0[hit];
                if (host != null) host.onExplain(selT);
                invalidate();
            }
        }
    }

    // ------------------------------------------------------------------ layout
    private void layoutPanes() {
        int W = getWidth(), H = getHeight();
        plotR = W - AXIS_W; timeY = H - TAXIS_H;
        boolean[] on = {showPrice, showFlow, showLiq, showIimp};
        float[] wt = {0.30f, 0.34f, 0.14f, 0.22f};
        if (fullscreen >= 0 && on[fullscreen]) { for (int p = 0; p < 4; p++) on[p] = p == fullscreen; }
        float tot = 0; for (int p = 0; p < 4; p++) if (on[p]) tot += wt[p];
        float y = 0; float avail = timeY;
        for (int p = 0; p < 4; p++) {
            paneOn[p] = on[p];
            if (!on[p]) { pane[p].set(0, 0, 0, 0); continue; }
            float hgt = tot > 0 ? avail * wt[p] / tot : 0;
            pane[p].set(0, y, plotR, y + hgt); y += hgt;
        }
    }

    private float xPx(double t) { return (float) ((t - vx0) / (vx1 - vx0) * plotR); }

    // ------------------------------------------------------------------ draw
    private long drawNs = 0, drawMaxNs = 0, drawN = 0, drawLogAt = 0;

    @Override protected void onDraw(Canvas c) {
        long t0 = System.nanoTime();
        drawFrame(c);
        long dt = System.nanoTime() - t0;
        drawNs += dt; drawMaxNs = Math.max(drawMaxNs, dt); drawN++;
        long now = System.currentTimeMillis();
        if (now - drawLogAt > 5000) {
            if (drawLogAt > 0) android.util.Log.i("FLOW", String.format(Locale.US, "draw: n=%d avg=%.1fms max=%.1fms", drawN, drawNs / 1e6 / Math.max(1, drawN), drawMaxNs / 1e6));
            drawLogAt = now; drawNs = drawMaxNs = drawN = 0;
        }
    }

    private void drawFrame(Canvas c) {
        layoutPanes();
        c.drawColor(BG);
        double now = M.nowEngine();
        if (follow) { double span = vx1 - vx0; vx1 = now; vx0 = now - span; }
        // snapshot what every pane needs, under the lock, then draw without it
        Snap s = new Snap();
        synchronized (M.lock) {
            s.binBase = M.binBase; s.buy = M.buy; s.sell = M.sell;
            s.n = M.nCyc; s.cT = M.cT; s.cTe = M.cTe; s.cSide = M.cSide; s.cStrong = M.cStrong; s.cDone = M.cDone; s.cCol = M.cCol; s.cSt = M.cSt;
            s.cMove = M.cMove; s.cO = M.cO; s.cH = M.cH; s.cL = M.cL; s.cC = M.cC;
            s.livePx = M.livePx; s.formCol = M.formCol; s.win = M.win; s.dec = M.dec; s.tick = M.tick;
            s.lqX = M.lqX; s.lqB = M.lqB; s.lqA = M.lqA;
            s.tkBuy = M.tkBuy; s.tkSell = M.tkSell; s.tkForm = M.tkForm;
            s.mode = M.iimpMode; s.iN = M.iN; s.iX0 = M.iX0; s.iX1 = M.iX1; s.iV = M.iV; s.iMult = M.iMult; s.iScore = M.iScore; s.iWall = M.iWall; s.iKept = M.iKept;
            s.iSbuy = M.iSbuy; s.iSsell = M.iSsell; s.iLiib = M.iLiib; s.iLiis = M.iLiis; s.iUp = M.iUp; s.iContra = M.iContra; s.iGood = M.iGood; s.iForm = M.iForm;
            s.connected = M.connected;
            s.series = new float[3][];
            if (paneOn[PANE_FLOW] && s.buy.length > 0) M.series(vx0 - 1, vx1 + 1, (int) (2 * plotR), s.series);
        }
        if (paneOn[PANE_PRICE]) drawPrice(c, s, now);
        if (paneOn[PANE_FLOW]) drawFlow(c, s, now);
        if (paneOn[PANE_LIQ]) drawLiq(c, s);
        if (paneOn[PANE_IIMP]) drawIimp(c, s, now);
        if (showLines) drawCycleLines(c, s);
        drawTimeAxis(c);
        if (!s.connected) {
            pt.setTextSize(13 * d); pt.setColor(TITLE); pt.setTypeface(Typeface.MONOSPACE);
            c.drawText("connecting to the engine (adb reverse tcp:8766)...", 12 * d, getHeight() - TAXIS_H - 8 * d, pt);
        }
    }

    private static final class Snap {
        long binBase; float[] buy, sell; float[][] series;
        int n; double[] cT, cTe; byte[] cSide, cStrong, cDone, cCol, cSt; float[] cMove, cO, cH, cL, cC;
        double livePx, win, tick; int formCol, dec;
        double[] lqX; float[] lqB, lqA;
        double[] tkBuy, tkSell, tkForm;
        String mode; int iN; double[] iX0, iX1; float[] iV, iMult, iScore, iWall, iKept, iSbuy, iSsell, iLiib, iLiis; byte[] iUp, iContra, iGood, iForm;
        boolean connected;
    }

    private void title(Canvas c, RectF r, String txt) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d); pt.setColor(TITLE);
        c.drawText(txt, r.left + 6 * d, r.top + 12 * d, pt);
        pf.setColor(Color.parseColor("#2a2f36"));
        c.drawRect(r.left, r.top, r.right + AXIS_W, r.top + 1, pf);
    }

    private static String usdShort(double v) {
        if (v >= 1e9) return String.format(Locale.US, "$%.2fB", v / 1e9);
        if (v >= 1e6) return String.format(Locale.US, "$%.2fM", v / 1e6);
        if (v >= 1e3) return String.format(Locale.US, "$%.0fK", v / 1e3);
        return String.format(Locale.US, "$%.0f", v);
    }

    private void yAxisMoney(Canvas c, RectF r, double lo, double hi) {
        double range = hi - lo; if (range <= 0) return;
        double px = range / r.height();
        double step = niceStep(px * 42 * d);
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(FG);
        for (double v = Math.ceil(lo / step) * step; v <= hi; v += step) {
            float y = (float) (r.bottom - (v - lo) / range * r.height());
            if (y < r.top + 6 * d || y > r.bottom - 2 * d) continue;
            c.drawText(v == 0 ? "0" : usdShort(v), plotR + 4 * d, y + 4 * d, pt);
        }
    }

    private static double niceStep(double raw) {
        double p = Math.pow(10, Math.floor(Math.log10(Math.max(1e-12, raw))));
        double m = raw / p;
        double s = m <= 1 ? 1 : (m <= 2 ? 2 : (m <= 5 ? 5 : 10));
        return s * p;
    }

    // ------------------------------------------------------------------ PRICE
    private void drawPrice(Canvas c, Snap s, double now) {
        RectF r = pane[PANE_PRICE];
        title(c, r, "PRICE  ·  one candle per cycle");
        if (s.n == 0) return;
        // which cycles overlap the view
        int i0 = lowerBound(s.cTe, vx0), i1 = upperBound(s.cT, vx1);
        i0 = Math.max(0, i0 - 1); i1 = Math.min(s.n, i1 + 1);
        int last = s.n - 1;
        boolean forming = s.cDone[last] == 0 && vx1 >= now - 600;
        double fo = s.cO[last], fc = Double.isNaN(s.livePx) ? s.cC[last] : s.livePx;
        double fh = Math.max(Math.max(s.cH[last], fc), fo), fl = Math.min(Math.min(s.cL[last], fc), fo);
        double fte = Math.max(s.cT[last] + 1e-3, Math.min(now, s.cTe[last]));
        // y fit to what is on screen, with the terminal's dead-band
        double lo = Double.MAX_VALUE, hi = -Double.MAX_VALUE;
        for (int i = i0; i < i1; i++) {
            if (i == last && forming) continue;
            if (s.cTe[i] < vx0 || s.cT[i] > vx1) continue;
            lo = Math.min(lo, s.cL[i]); hi = Math.max(hi, s.cH[i]);
        }
        if (forming && s.cT[last] <= vx1 && fte >= vx0) { lo = Math.min(lo, fl); hi = Math.max(hi, fh); }
        if (hi >= lo) {
            double pad = Math.max(s.tick, (hi - lo) * 0.06);
            double wl = lo - pad, wh = hi + pad;
            if (Double.isNaN(pxLo) || Math.abs(wl - pxLo) + Math.abs(wh - pxHi) > 0.18 * Math.max(1e-9, pxHi - pxLo)) { pxLo = wl; pxHi = wh; }
        }
        if (Double.isNaN(pxLo)) return;
        double yl = pxLo, yh = pxHi;
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        c.save(); c.clipRect(r.left, r.top, r.right, r.bottom);
        for (int i = i0; i < i1; i++) {
            boolean isForm = i == last && forming;
            double o = s.cO[i], cl = isForm ? fc : s.cC[i], hh = isForm ? fh : s.cH[i], ll = isForm ? fl : s.cL[i];
            double te = isForm ? fte : s.cTe[i];
            double dur = Math.max(1e-9, te - s.cT[i]);
            float xm = xPx(s.cT[i] + dur * 0.5), hw = (float) (dur * 0.72 * 0.5 / (vx1 - vx0) * plotR);
            hw = Math.max(0.6f * d, hw);
            int col = isForm ? s.formCol : s.cCol[i];
            drawCandle(c, xm, hw, o, hh, ll, cl, col, top, hgt, yl, yh);
        }
        // takeover marks
        if (showTakeover) {
            drawTriangles(c, s, s.tkBuy, true, top, hgt, yl, yh);
            drawTriangles(c, s, s.tkSell, false, top, hgt, yl, yh);
            if (s.tkForm != null) {
                float x = xPx(s.tkForm[0]); float y = (float) (top + (yh - s.tkForm[1]) / (yh - yl) * hgt);
                triangle(c, x, y, s.tkForm[2] > 0.5, 110);
            }
        }
        // the live price line + pill
        if (!Double.isNaN(s.livePx)) {
            float y = (float) (top + (yh - s.livePx) / (yh - yl) * hgt);
            pl.setColor(Color.parseColor("#e8eaed")); pl.setStrokeWidth(1.2f * d); pl.setPathEffect(new DashPathEffect(new float[]{4 * d, 4 * d}, 0));
            c.drawLine(r.left, y, plotR, y, pl); pl.setPathEffect(null);
            c.restore();
            boolean up = s.livePx >= fo;
            int pc = up ? Color.parseColor("#28e65a") : Color.parseColor("#ef4444");
            String p1 = String.format(Locale.US, "%." + s.dec + "f", s.livePx);
            String p2 = forming ? durText(Math.max(0, fte - s.cT[last])) : "";
            pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d);
            float w = Math.max(pt.measureText(p1), pt.measureText(p2)) + 10 * d;
            RectF pill = new RectF(plotR + 2 * d, y - 14 * d, plotR + 2 * d + w, y + (forming ? 14 : 2) * d);
            pf.setColor(Color.parseColor("#1c2128")); c.drawRoundRect(pill, 5 * d, 5 * d, pf);
            pl.setColor(pc); pl.setStrokeWidth(1.3f * d); c.drawRoundRect(pill, 5 * d, 5 * d, pl);
            pt.setColor(Color.parseColor("#eef2f8")); pt.setFakeBoldText(true); c.drawText(p1, pill.left + 5 * d, y - 3 * d, pt);
            if (forming) { pt.setColor(pc); pt.setTextSize(9.5f * d); c.drawText(p2, pill.left + 5 * d, y + 10 * d, pt); }
            pt.setFakeBoldText(false);
        } else {
            c.restore();
        }
        // price axis
        double range = yh - yl; double step = niceStep(range / hgt * 34 * d);
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(FG);
        for (double v = Math.ceil(yl / step) * step; v <= yh; v += step) {
            float y = (float) (top + (yh - v) / range * hgt);
            if (!Double.isNaN(s.livePx) && Math.abs(y - (float) (top + (yh - s.livePx) / range * hgt)) < 16 * d) continue;
            if (y < top + 4 * d || y > r.bottom - 2 * d) continue;
            c.drawText(String.format(Locale.US, "%." + s.dec + "f", v), plotR + 4 * d, y + 4 * d, pt);
        }
    }

    private void drawCandle(Canvas c, float xm, float hw, double o, double hh, double ll, double cl, int col, float top, float hgt, double yl, double yh) {
        float yo = (float) (top + (yh - o) / (yh - yl) * hgt), yc = (float) (top + (yh - cl) / (yh - yl) * hgt);
        float yhh = (float) (top + (yh - hh) / (yh - yl) * hgt), yll = (float) (top + (yh - ll) / (yh - yl) * hgt);
        boolean down = cl < o;
        int fill, pen; boolean hollow = false; int hiWick = 0, loWick = 0;
        if (col == C_AB_BUY && !down) { col = -1; hiWick = BAR_COL[C_AB_BUY]; }
        else if (col == C_AB_SELL && !(cl > o)) { col = -1; loWick = BAR_COL[C_AB_SELL]; }
        if (col == C_BRK_BUY || col == C_BRK_SELL || col == C_AB_BUY || col == C_AB_SELL) { fill = BAR_COL[col]; pen = fill; }
        else { fill = down ? RED : TEAL; pen = Color.parseColor("#9aa4ae"); }
        pl.setStrokeWidth(1 * d); pl.setColor(pen);
        c.drawLine(xm, yhh, xm, Math.min(yo, yc), pl); c.drawLine(xm, Math.max(yo, yc), xm, yll, pl);
        if (hiWick != 0) { pl.setColor(hiWick); pl.setStrokeWidth(2.8f * d); c.drawLine(xm, yhh, xm, Math.min(yo, yc), pl); }
        if (loWick != 0) { pl.setColor(loWick); pl.setStrokeWidth(2.8f * d); c.drawLine(xm, Math.max(yo, yc), xm, yll, pl); }
        float y0 = Math.min(yo, yc), y1 = Math.max(yo, yc); if (y1 - y0 < 1) y1 = y0 + 1;
        pf.setColor(fill); c.drawRect(xm - hw, y0, xm + hw, y1, pf);
        pl.setColor(pen); pl.setStrokeWidth(1 * d); c.drawRect(xm - hw, y0, xm + hw, y1, pl);
    }

    private void drawTriangles(Canvas c, Snap s, double[] keys, boolean buy, float top, float hgt, double yl, double yh) {
        if (keys == null) return;
        for (double k : keys) {
            int i = nearest(s.cT, k);
            if (i < 0 || Math.abs(s.cT[i] - k) > 1.0 || s.cTe[i] < vx0 || s.cT[i] > vx1) continue;
            float x = xPx(0.5 * (s.cT[i] + s.cTe[i]));
            double yv = buy ? s.cL[i] : s.cH[i];
            float y = (float) (top + (yh - yv) / (yh - yl) * hgt) + (buy ? 9 * d : -9 * d);
            triangle(c, x, y, buy, 255);
        }
    }

    private void triangle(Canvas c, float x, float y, boolean up, int alpha) {
        float r = 5 * d;
        path.reset();
        if (up) { path.moveTo(x, y - r); path.lineTo(x + r, y + r); path.lineTo(x - r, y + r); }
        else { path.moveTo(x, y + r); path.lineTo(x + r, y - r); path.lineTo(x - r, y - r); }
        path.close();
        int col = up ? Color.parseColor("#00C853") : Color.parseColor("#FF1F1F");
        pf.setColor((col & 0x00ffffff) | (alpha << 24)); c.drawPath(path, pf);
    }

    private static String durText(double secs) {
        int s = (int) Math.round(secs);
        if (s < 60) return s + "s";
        if (s < 3600) return (s / 60) + "m" + (s % 60 > 0 ? (s % 60) + "s" : "");
        return (s / 3600) + "h" + ((s % 3600) / 60) + "m";
    }

    // ------------------------------------------------------------------ FLOW
    private void drawFlow(Canvas c, Snap s, double now) {
        RectF r = pane[PANE_FLOW];
        title(c, r, "BUY / SELL FLOW  ·  taker $ per " + (int) s.win + " s");
        float[] t = s.series[0], b = s.series[1], sl = s.series[2];
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        float badgePx = showLines ? 32 * d : 0;
        if (t == null || t.length == 0) { yAxisMoney(c, new RectF(r.left, top, r.right, r.bottom), 0, 1); return; }
        // y: 99th percentile of what is drawn, the live value always in frame, with the dead-band
        float[] all = new float[b.length + sl.length]; System.arraycopy(b, 0, all, 0, b.length); System.arraycopy(sl, 0, all, b.length, sl.length);
        java.util.Arrays.sort(all);
        double p99 = all[Math.min(all.length - 1, (int) (0.99 * (all.length - 1)))];
        double tp = Math.max(p99, Math.max(b[b.length - 1], sl[sl.length - 1]));
        if (tp > 0 && (tp > flowTop * 0.98 || tp < flowTop * 0.55)) flowTop = tp * 1.18;
        double yt = Math.max(1.0, flowTop);
        double room = yt * badgePx / Math.max(1f, hgt - badgePx);
        double ylo = -room, yhi = yt; double range = yhi - ylo;
        float zeroY = (float) (top + (yhi - 0) / range * hgt);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        pl.setColor(Color.parseColor("#3a3f46")); pl.setStrokeWidth(1 * d); c.drawLine(r.left, zeroY, plotR, zeroY, pl);
        for (int side = 0; side < 2; side++) {
            float[] v = side == 0 ? b : sl;
            path.reset();
            for (int i = 0; i < t.length; i++) {
                float x = xPx(s.binBase + t[i]); float y = (float) (top + (yhi - v[i]) / range * hgt);
                if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
            }
            pl.setColor(side == 0 ? TEAL : RED); pl.setStrokeWidth(2 * d); pl.setStrokeCap(Paint.Cap.ROUND); pl.setStrokeJoin(Paint.Join.ROUND);
            c.drawPath(path, pl);
        }
        // the cycle badges under the zero line: how far PRICE went, coloured by the outcome for the side that owned the cycle
        if (showLines && s.n > 0) {
            int i0 = Math.max(0, lowerBound(s.cT, vx0) - 1), i1 = Math.min(s.n, upperBound(s.cT, vx1) + 1);
            pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(9.5f * d); pt.setFakeBoldText(true);
            float lastX = -1e9f; float minPx = 34 * d;
            for (int i = i0; i < i1; i++) {
                if (s.cDone[i] == 0) continue;
                float x = xPx(s.cT[i]);
                if (x - lastX < minPx) continue;
                lastX = x;
                int mv = Math.round(s.cMove[i]);
                String txt = (mv == 0 ? "0" : String.format(Locale.US, "%+d", mv)) + "t";
                int col = s.cSt[i] == 1 ? (s.cSide[i] != 0 ? B_UP : B_DN) : (s.cSt[i] == 2 ? B_CONTRA : B_FLAT);
                float w = pt.measureText(txt) + 8 * d;
                RectF pill = new RectF(x + 2 * d, zeroY + 4 * d, x + 2 * d + w, zeroY + 18 * d);
                pf.setColor(col); c.drawRoundRect(pill, 4 * d, 4 * d, pf);
                pt.setColor(Color.WHITE); c.drawText(txt, pill.left + 4 * d, pill.bottom - 4 * d, pt);
            }
            pt.setFakeBoldText(false);
        }
        c.restore();
        // right-edge badges: the last value of each line and its share
        double bn = b[b.length - 1], sn = sl[sl.length - 1], tot = Math.max(1e-9, bn + sn);
        String sb = String.format(Locale.US, "%s (%.0f%%)", usdShort(bn), 100 * bn / tot), ss = String.format(Locale.US, "%s (%.0f%%)", usdShort(sn), 100 * sn / tot);
        float yb = (float) (top + (yhi - bn) / range * hgt), ys = (float) (top + (yhi - sn) / range * hgt);
        badge(c, plotR - 4 * d, yb, sb, TEAL, bn >= sn);
        badge(c, plotR - 4 * d, ys, ss, RED, sn > bn);
        yAxisMoney(c, new RectF(r.left, top, r.right, r.bottom), ylo, yhi);
    }

    private void badge(Canvas c, float xr, float y, String txt, int col, boolean above) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d); pt.setFakeBoldText(true);
        float w = pt.measureText(txt) + 10 * d, hh = 16 * d;
        RectF pill = above ? new RectF(xr - w, y - hh - 2 * d, xr, y - 2 * d) : new RectF(xr - w, y + 2 * d, xr, y + hh + 2 * d);
        pf.setColor(Color.parseColor("#1c2128")); c.drawRoundRect(pill, 4 * d, 4 * d, pf);
        pt.setColor(col); c.drawText(txt, pill.left + 5 * d, pill.bottom - 4 * d, pt);
        pt.setFakeBoldText(false);
    }

    // ------------------------------------------------------------------ LIMIT ORDERS
    private void drawLiq(Canvas c, Snap s) {
        RectF r = pane[PANE_LIQ];
        title(c, r, "LIMIT ORDERS  ·  resting bid / ask $");
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        if (s.lqX == null || s.lqX.length == 0) return;
        double mx = 0; for (float v : s.lqB) mx = Math.max(mx, v); for (float v : s.lqA) mx = Math.max(mx, v);
        if (mx > 0 && (mx > liqTop * 0.98 || mx < liqTop * 0.55)) liqTop = mx * 1.15;
        double yhi = Math.max(1.0, liqTop);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        for (int side = 0; side < 2; side++) {
            float[] v = side == 0 ? s.lqB : s.lqA;
            path.reset(); int n = Math.min(v.length, s.lqX.length);
            for (int i = 0; i < n; i++) {
                float x = xPx(s.lqX[i]); float y = (float) (top + (yhi - v[i]) / yhi * hgt);
                if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
            }
            pl.setColor(side == 0 ? TEAL : RED); pl.setStrokeWidth(2 * d); c.drawPath(path, pl);
            // the dashed rule from the last point to the right edge
            if (n > 0) {
                float xl = xPx(s.lqX[n - 1]); float y = (float) (top + (yhi - v[n - 1]) / yhi * hgt);
                pl.setStrokeWidth(1 * d); pl.setPathEffect(new DashPathEffect(new float[]{4 * d, 6 * d}, 0));
                c.drawLine(xl, y, plotR, y, pl); pl.setPathEffect(null);
            }
        }
        c.restore();
        int n = Math.min(s.lqB.length, s.lqA.length);
        if (n > 0) {
            double bv = s.lqB[n - 1], av = s.lqA[n - 1], tot = bv + av;
            String sb = tot > 0 ? String.format(Locale.US, "%s (%.0f%%)", usdShort(bv), 100 * bv / tot) : usdShort(bv);
            String sa = tot > 0 ? String.format(Locale.US, "%s (%.0f%%)", usdShort(av), 100 * av / tot) : usdShort(av);
            badge(c, plotR - 4 * d, (float) (top + (yhi - bv) / yhi * hgt), sb, TEAL, bv >= av);
            badge(c, plotR - 4 * d, (float) (top + (yhi - av) / yhi * hgt), sa, RED, av > bv);
        }
        yAxisMoney(c, new RectF(r.left, top, r.right, r.bottom), 0, yhi);
    }

    // ------------------------------------------------------------------ INTEREST x IMPACT
    private void drawIimp(Canvas c, Snap s, double now) {
        RectF r = pane[PANE_IIMP];
        title(c, r, "INTEREST × IMPACT  ·  who leads vs last " + M.lb);
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        boolean lines = "Lines Buyer/Seller".equals(s.mode);
        boolean signed = "Buyer".equals(s.mode) || "Seller".equals(s.mode) || lines;
        double clip = Math.log(8.0) / LN2;
        // y: p95 of what is drawn, floored, capped
        double lim;
        {
            float[] fit;
            if (lines && s.iN > 0) { fit = new float[2 * s.iN]; for (int i = 0; i < s.iN; i++) { fit[i] = (float) Math.abs(Math.max(-clip, Math.min(clip, s.iLiib[i]))); fit[s.iN + i] = (float) Math.abs(Math.max(-clip, Math.min(clip, s.iLiis[i]))); } }
            else if (s.iN > 0) { fit = new float[s.iN]; for (int i = 0; i < s.iN; i++) fit[i] = Math.abs(s.iV[i]); }
            else fit = new float[0];
            if (fit.length > 0) {
                java.util.Arrays.sort(fit);
                double p95 = fit[Math.min(fit.length - 1, (int) (0.95 * (fit.length - 1)))] * 1.15;
                lim = Math.min(Math.max(Math.max(p95, Math.log(1.37) / LN2 * 1.4), 1.0), clip * 1.15);
                if (lim > iimpTop * 0.98 || lim < iimpTop * 0.55) iimpTop = lim;
            }
        }
        lim = Math.max(1.0, iimpTop);
        double yhi = lim, ylo = -lim, range = yhi - ylo;
        float zeroY = (float) (top + (yhi) / range * hgt);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        // guides: the imbalance terciles, dashed; the midline solid
        pl.setColor(GUIDE); pl.setStrokeWidth(1 * d); pl.setPathEffect(new DashPathEffect(new float[]{5 * d, 5 * d}, 0));
        for (double g : new double[]{Math.log(0.76) / LN2, Math.log(1.37) / LN2}) { float y = (float) (top + (yhi - g) / range * hgt); c.drawLine(r.left, y, plotR, y, pl); }
        pl.setPathEffect(null); pl.setColor(Color.parseColor("#8a8a8a")); c.drawLine(r.left, zeroY, plotR, zeroY, pl);
        if (s.iN > 0) {
            int last = s.iN - 1;
            if (lines) {
                for (int side = 0; side < 2; side++) {
                    float[] v = side == 0 ? s.iLiib : s.iLiis; int col = side == 0 ? TEAL : RED;
                    path.reset(); boolean pen = false; float px = 0, py = 0;
                    for (int i = 0; i < s.iN; i++) {
                        if (s.iForm[i] != 0) continue;
                        float x = xPx(0.5 * (s.iX0[i] + s.iX1[i])); float y = (float) (top + (yhi - Math.max(-clip, Math.min(clip, v[i]))) / range * hgt);
                        boolean joined = pen && i > 0 && Math.abs(s.iX1[i - 1] - s.iX0[i]) < 1e-6;
                        if (joined) path.lineTo(x, y); else path.moveTo(x, y);
                        pen = true; px = x; py = y;
                    }
                    pl.setColor(col); pl.setStrokeWidth(1.8f * d); c.drawPath(path, pl);
                    if (s.iForm[last] != 0) {
                        float x = xPx(0.5 * (s.iX0[last] + s.iX1[last])); float y = (float) (top + (yhi - Math.max(-clip, Math.min(clip, v[last]))) / range * hgt);
                        pl.setColor((col & 0x00ffffff) | (150 << 24));
                        boolean adj = last > 0 && Math.abs(s.iX1[last - 1] - s.iX0[last]) < 1e-6 && s.iForm[last - 1] == 0;
                        if (adj) c.drawLine(px, py, x, y, pl); else c.drawLine(xPx(s.iX0[last]), y, xPx(s.iX1[last]), y, pl);
                    }
                }
            } else {
                double pad = 0.06 * Math.max(1.0, p99abs(s.iV));
                for (int i = 0; i < s.iN; i++) {
                    float x0 = xPx(s.iX0[i]), x1 = xPx(s.iX1[i]);
                    if (x1 < r.left || x0 > plotR) continue;
                    double v = s.iV[i]; boolean up = s.iUp[i] != 0, contra = s.iContra[i] != 0, good = s.iGood[i] != 0, form = s.iForm[i] != 0;
                    int hx; int fillA, penA;
                    if ("Buyer".equals(s.mode) || "Seller".equals(s.mode)) {
                        boolean buyer = "Buyer".equals(s.mode); hx = buyer ? TEAL : RED;
                        boolean led = buyer == up;
                        fillA = led ? (good ? 190 : 0) : 70; penA = led ? 255 : 130;
                    } else if ("Delta".equals(s.mode)) {
                        hx = contra ? ORANGE : (v >= 0 ? TEAL : RED); fillA = good ? 190 : 0; penA = 255;
                    } else {
                        hx = contra ? ORANGE : (up ? TEAL : RED); fillA = good ? 190 : 0; penA = 255;
                    }
                    if (form) { fillA = good ? 70 : 0; penA = 150; }
                    float y0 = (float) (top + (yhi - Math.max(0, v)) / range * hgt), y1 = (float) (top + (yhi - Math.min(0, v)) / range * hgt);
                    if (y1 - y0 < 1) y1 = y0 + 1;
                    if (fillA > 0) { pf.setColor((hx & 0x00ffffff) | (fillA << 24)); c.drawRect(x0, y0, x1, y1, pf); }
                    pl.setColor((hx & 0x00ffffff) | (penA << 24)); pl.setStrokeWidth((good ? 1.0f : 1.4f) * d); c.drawRect(x0, y0, x1, y1, pl);
                    // the wall dot past the tip, the "handed it back" cap across it
                    float xm = 0.5f * (x0 + x1); float tipY = v >= 0 ? y0 : y1;
                    if (!form && s.iWall[i] > -900) {
                        float dy = (float) (pad / range * hgt);
                        if (s.iWall[i] >= 1.06) { pf.setColor(FG); c.drawCircle(xm, v >= 0 ? tipY - dy : tipY + dy, 3 * d, pf); }
                        else if (s.iWall[i] <= 0.94 && s.iWall[i] > 0) { pl.setColor(FG); pl.setStrokeWidth(1.2f * d); c.drawCircle(xm, v >= 0 ? tipY - dy : tipY + dy, 3 * d, pl); }
                    }
                    if (!form && s.iKept[i] > -900 && s.iKept[i] <= 0.375) { pl.setColor(FG); pl.setStrokeWidth(2.5f * d); c.drawLine(x0, tipY, x1, tipY, pl); }
                }
            }
            // the selected bar
            if (!Double.isNaN(selT)) {
                int k = nearest(s.iX0, selT);
                if (k >= 0 && Math.abs(s.iX0[k] - selT) < 1.0) {
                    float x0 = xPx(s.iX0[k]), x1 = xPx(s.iX1[k]);
                    pf.setColor(Color.argb(26, 255, 255, 255)); c.drawRect(x0, top, x1, r.bottom, pf);
                    double v = s.iV[k];
                    float y0 = (float) (top + (yhi - Math.max(0, v)) / range * hgt), y1 = (float) (top + (yhi - Math.min(0, v)) / range * hgt);
                    pl.setColor(Color.WHITE); pl.setStrokeWidth(2 * d); c.drawRect(x0, y0, x1, Math.max(y1, y0 + 1), pl);
                }
            }
            // the readout, bottom right
            String rd = readout(s, last);
            pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setFakeBoldText(true);
            int rc = s.iContra[last] != 0 ? ORANGE : (s.iUp[last] != 0 ? TEAL : RED);
            pt.setColor(rc);
            float tw = pt.measureText(rd);
            c.drawText(rd, Math.max(r.left + 4 * d, plotR - 6 * d - tw), r.bottom - 5 * d, pt);
            pt.setFakeBoldText(false);
        }
        c.restore();
        // the axis: mirrored multiples in None / Delta, signed in Buyer / Seller / Lines
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(FG);
        double stp = lim <= 1.5 ? 0.5 : (lim <= 3 ? 1 : 2);
        for (double v = -Math.floor(lim / stp) * stp; v <= lim + 1e-9; v += stp) {
            float y = (float) (top + (yhi - v) / range * hgt);
            if (y < top + 6 * d || y > r.bottom - 14 * d) continue;
            double x = signed ? Math.pow(2, v) : Math.pow(2, Math.abs(v));
            c.drawText(fmtMult(x), plotR + 4 * d, y + 4 * d, pt);
        }
        // the dropdown button, top right of the pane
        String lab = s.mode + " ▾";
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10.5f * d); pt.setFakeBoldText(true);
        float w = pt.measureText(lab) + 14 * d;
        ddX1 = plotR - 6 * d; ddX0 = ddX1 - w; ddY0 = r.top + 3 * d; ddY1 = ddY0 + 20 * d;
        pf.setColor(Color.parseColor("#20242c")); c.drawRoundRect(new RectF(ddX0, ddY0, ddX1, ddY1), 4 * d, 4 * d, pf);
        pl.setColor(Color.parseColor("#3a4150")); pl.setStrokeWidth(1 * d); c.drawRoundRect(new RectF(ddX0, ddY0, ddX1, ddY1), 4 * d, 4 * d, pl);
        pt.setColor(FG); c.drawText(lab, ddX0 + 7 * d, ddY1 - 6 * d, pt); pt.setFakeBoldText(false);
    }

    private static String fmtMult(double x) {
        String s = x >= 10 ? String.format(Locale.US, "%.0f", x) : (Math.abs(x - Math.round(x)) < 1e-9 ? String.format(Locale.US, "%.0f", x) : String.format(Locale.US, "%.2g", x));
        return s + "x";
    }

    private static double p99abs(float[] v) {
        if (v == null || v.length == 0) return 1.0;
        float[] a = new float[v.length]; for (int i = 0; i < v.length; i++) a[i] = Math.abs(v[i]);
        java.util.Arrays.sort(a);
        return a[Math.min(a.length - 1, (int) (0.99 * (a.length - 1)))];
    }

    private String readout(Snap s, int k) {
        StringBuilder sb = new StringBuilder();
        sb.append("B ").append(s.iSbuy[k] > -900 ? String.valueOf(Math.round(s.iSbuy[k])) : "-").append(" / S ").append(s.iSsell[k] > -900 ? String.valueOf(Math.round(s.iSsell[k])) : "-");
        sb.append("  ·  ").append(s.iUp[k] != 0 ? "BUY" : "SELL").append(' ').append(String.format(Locale.US, "%.2g", s.iMult[k])).append('x');
        sb.append("  ·  impact ").append(String.format(Locale.US, "%.2g", Math.exp(s.iScore[k] > -900 ? s.iScore[k] : 0))).append('x');
        sb.append("  ·  wall ").append(s.iWall[k] > -900 ? String.format(Locale.US, "%.2gx", s.iWall[k]) : "-");
        sb.append("  ·  kept ").append(s.iKept[k] > -900 ? String.format(Locale.US, "%d%%", Math.round(100 * s.iKept[k])) : "-");
        if (s.iForm[k] != 0) sb.append("  ·  still forming");
        if (s.iContra[k] != 0) sb.append("  ·  price went the other way");
        if ("Buyer".equals(s.mode)) sb.append(String.format(Locale.US, "  ·  buyers I×I %.2gx", Math.pow(2, s.iLiib[k])));
        else if ("Seller".equals(s.mode)) sb.append(String.format(Locale.US, "  ·  sellers I×I %.2gx", Math.pow(2, s.iLiis[k])));
        else if ("Delta".equals(s.mode)) { double dl = s.iLiib[k] - s.iLiis[k]; sb.append(String.format(Locale.US, "  ·  delta %s %.2gx", dl >= 0 ? "B" : "S", Math.pow(2, Math.abs(dl)))); }
        else if ("Lines Buyer/Seller".equals(s.mode)) sb.append(String.format(Locale.US, "  ·  buyers I×I %.2gx  ·  sellers I×I %.2gx", Math.pow(2, s.iLiib[k]), Math.pow(2, s.iLiis[k])));
        return sb.toString();
    }

    // ------------------------------------------------------------------ cycle start lines (every visible pane)
    private void drawCycleLines(Canvas c, Snap s) {
        if (s.n == 0) return;
        int i0 = Math.max(0, lowerBound(s.cT, vx0) - 1), i1 = Math.min(s.n, upperBound(s.cT, vx1));
        float minPx = 7 * d; float lastStrong = -1e9f, lastAny = -1e9f;
        List<float[]> kept = new ArrayList<>();
        for (int i = i0; i < i1; i++) {
            float x = xPx(s.cT[i]);
            boolean strong = s.cStrong[i] != 0;
            if (strong) { if (x - lastStrong < minPx) continue; lastStrong = x; }
            else if (x - lastAny < minPx) continue;
            lastAny = Math.max(lastAny, x);
            kept.add(new float[]{x, strong ? (s.cSide[i] != 0 ? 1 : 2) : 0});
        }
        pl.setStrokeWidth(1.2f * d);
        float dash = 9 * d, gap = 7 * d;
        for (int kind = 0; kind < 3; kind++) {
            // one drawLines call per colour: the dashes are emitted as segments, never as a path effect
            int cnt = 0;
            for (float[] k : kept) if ((int) k[1] == kind) cnt++;
            if (cnt == 0) continue;
            pl.setColor(kind == 1 ? TEAL : (kind == 2 ? RED : WEAK));
            for (int p = 0; p < 4; p++) {
                if (!paneOn[p]) continue;
                float y0 = pane[p].top + TITLE_H, y1 = pane[p].bottom;
                if (kind == 0) {
                    float[] seg = new float[cnt * 4]; int j = 0;
                    for (float[] k : kept) if ((int) k[1] == kind) { seg[j++] = k[0]; seg[j++] = y0; seg[j++] = k[0]; seg[j++] = y1; }
                    c.drawLines(seg, pl);
                } else {
                    int per = (int) Math.ceil((y1 - y0) / (dash + gap)) + 1;
                    float[] seg = new float[cnt * per * 4]; int j = 0;
                    for (float[] k : kept) {
                        if ((int) k[1] != kind) continue;
                        for (float y = y0; y < y1; y += dash + gap) { seg[j++] = k[0]; seg[j++] = y; seg[j++] = k[0]; seg[j++] = Math.min(y1, y + dash); }
                    }
                    c.drawLines(seg, 0, j, pl);
                }
            }
        }
    }

    // ------------------------------------------------------------------ the clock axis
    private void drawTimeAxis(Canvas c) {
        pf.setColor(Color.parseColor("#2a2f36")); c.drawRect(0, timeY, getWidth(), timeY + 1, pf);
        double span = vx1 - vx0; if (span <= 0) return;
        double[] steps = {60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200};
        double step = steps[steps.length - 1];
        for (double st : steps) { if (st / span * plotR >= 90 * d) { step = st; break; } }
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10.5f * d); pt.setColor(FG);
        int tz; synchronized (M.lock) { tz = M.tzOff; }
        Calendar cal = tz == Integer.MIN_VALUE ? Calendar.getInstance() : Calendar.getInstance(new java.util.SimpleTimeZone(tz * 1000, "PC"));
        for (double t = Math.ceil(vx0 / step) * step; t <= vx1; t += step) {
            float x = xPx(t);
            cal.setTimeInMillis((long) (t * 1000));
            String lab = String.format(Locale.US, "%02d:%02d", cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE));
            if (step >= 43200) lab = String.format(Locale.US, "%02d/%02d %s", cal.get(Calendar.DAY_OF_MONTH), cal.get(Calendar.MONTH) + 1, lab);
            float w = pt.measureText(lab);
            c.drawText(lab, x - w / 2, timeY + 15 * d, pt);
            pf.setColor(Color.parseColor("#3a3f46")); c.drawRect(x, timeY, x + 1, timeY + 4 * d, pf);
        }
    }

    // ------------------------------------------------------------------ helpers
    private static int lowerBound(double[] a, double v) { int lo = 0, hi = a.length; while (lo < hi) { int m = (lo + hi) >>> 1; if (a[m] < v) lo = m + 1; else hi = m; } return lo; }
    private static int upperBound(double[] a, double v) { int lo = 0, hi = a.length; while (lo < hi) { int m = (lo + hi) >>> 1; if (a[m] <= v) lo = m + 1; else hi = m; } return lo; }
    private static int nearest(double[] a, double v) {
        if (a == null || a.length == 0) return -1;
        int i = lowerBound(a, v);
        if (i >= a.length) return a.length - 1;
        if (i > 0 && Math.abs(a[i - 1] - v) < Math.abs(a[i] - v)) return i - 1;
        return i;
    }
}
