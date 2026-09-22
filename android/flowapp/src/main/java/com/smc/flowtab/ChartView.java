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
        void onCycleTap(double t0);
    }

    public static final int PANE_PRICE = 0, PANE_FLOW = 1, PANE_LIQ = 2, PANE_IIMP = 3;
    private final FlowModel M;
    private Host host;
    private final float d;
    // toggles (the hamburger's)
    public boolean showPrice = true, showFlow = true, showLiq = true, showIimp = true, showLines = true, showTakeover = true;
    public boolean showHlh = false, showBp = false;
    public boolean bw = true;                       // Chart Style "Simple BW": white canvas, black ink, black / white candles
    private int cBg, cFg, cTitle, cGuide, cSep, cMid, cInk;

    private void theme() {
        // the I x I guides, zero line, wall dots and kept caps keep the terminal's own colours in BOTH styles
        // (its _theme_sub_panes morphs only the ground, the axes and the crosshair)
        if (bw) { cBg = Color.WHITE; cFg = Color.BLACK; cTitle = Color.parseColor("#303030"); cGuide = GUIDE; cSep = Color.parseColor("#d0d0d0"); cMid = Color.parseColor("#8a8a8a"); cInk = Color.BLACK; }
        else { cBg = BG; cFg = FG; cTitle = TITLE; cGuide = GUIDE; cSep = Color.parseColor("#2a2f36"); cMid = Color.parseColor("#8a8a8a"); cInk = FG; }
    }
    public PriceTools tools;                        // the drawing toolbar + Market Position on the PRICE pane
    private float pxTop, pxHgt;                     // the PRICE pane's plot rect (for the tools' map)
    private double pxYl, pxYh;
    private static final float PCPX = 0.85f;        // one of the terminal's pixels, on this screen (x density)
    // the view
    private double vx0, vx1; private boolean follow = true;
    private int fullscreen = -1;
    private long lastViewSent = 0;
    // y fits with dead-bands, per pane
    private double pxLo = Double.NaN, pxHi = Double.NaN, flowTop = 0, liqTop = 0, iimpTop = 0;
    private final RectF[] pane = {new RectF(), new RectF(), new RectF(), new RectF()};
    private final boolean[] paneOn = new boolean[4];
    private final float[] paneWt = {0.30f, 0.34f, 0.14f, 0.22f};   // the panes' shares of the height (the splitter)
    private int rsUpper = -1, rsLower = -1; private float rsY0; private float rsW0, rsW1, rsTot, rsAvail;
    // each pane fits its own y until the user pans or zooms it (then it is theirs, like the terminal's
    // _px_yauto); a double tap on an axis hands every pane back to its fit
    private final boolean[] yAuto = {true, true, true, true};
    private final double[] yLo = new double[4], yHi = new double[4], lastLo = new double[4], lastHi = new double[4];
    private int panPane = -1; private float panAccY = 0;

    /** The pane's y range this frame: its fit, or the range the user panned / zoomed it to. */
    private double[] yRange(int p, double lo, double hi) {
        lastLo[p] = lo; lastHi[p] = hi;
        return yAuto[p] ? new double[]{lo, hi} : new double[]{yLo[p], yHi[p]};
    }

    private void takeManual(int p) { if (yAuto[p]) { yAuto[p] = false; yLo[p] = lastLo[p]; yHi[p] = lastHi[p]; } }
    private double selCycle = Double.NaN;                           // the candle marked by a tap here or in the feed
    private double lcFrom = Double.NaN, lcTo = Double.NaN, lcCur = Double.NaN, lcCycle = Double.NaN; private long lcT0 = 0;

    /** The feed's row was tapped: centre its cycle and mark the candle. */
    public void focusCycle(double t0, double t1) {
        selCycle = t0;
        double now = M.nowEngine(), span = vx1 - vx0, te = Math.min(t1, now);
        if (t0 >= vx0 && te <= vx1) { invalidate(); return; }         // already on screen: just mark it
        double mid = 0.5 * (t0 + te);
        vx0 = mid - span / 2; vx1 = vx0 + span;
        follow = (now - vx1) >= -1.0 && (now - vx1) <= 3.0;
        sendView(true); invalidate();
    }

    /** A tap on the PRICE pane away from the tools: the candle under it is marked and the feed is told. */
    private void cycleTap(float x) {
        double t = vx0 + (x / Math.max(1f, plotR)) * (vx1 - vx0);
        double[] cT, cTe; int n; synchronized (M.lock) { cT = M.cT; cTe = M.cTe; n = M.nCyc; }
        int i = upperBound(cT, t) - 1;
        if (i < 0 || i >= n) return;
        if (i < n - 1 && t > Math.max(cTe[i], cT[i] + 1) + 1.0) { selCycle = Double.NaN; invalidate(); return; }
        if (!Double.isNaN(selCycle) && Math.abs(selCycle - cT[i]) < 1.0) { selCycle = Double.NaN; invalidate(); return; }
        selCycle = cT[i];
        if (host != null) host.onCycleTap(cT[i]);
        invalidate();
    }
    private int axPane = -1; private boolean axX = false; private float axY0, axX0; private double axSpan0, axLo0, axHi0;

    /** A touch that starts on an axis strip: a vertical drag on a pane's axis zooms its y, a horizontal drag on the
     * clock strip zooms the x span. The gesture detector still sees it (for the double tap), pan and pinch do not. */
    private boolean axisTouch(MotionEvent ev) {
        int a = ev.getActionMasked(); float x = ev.getX(), y = ev.getY();
        if (a == MotionEvent.ACTION_DOWN) {
            axPane = -1; axX = false;
            if (y >= timeY) { axX = true; axX0 = x; axSpan0 = vx1 - vx0; return true; }
            if (x >= plotR) { int p = paneAt(plotR - 1, y); if (p < 0) return false; axPane = p; axY0 = y; takeManual(p); axLo0 = yLo[p]; axHi0 = yHi[p]; return true; }
            return false;
        }
        if (axPane < 0 && !axX) return false;
        if (a == MotionEvent.ACTION_MOVE) {
            if (axPane >= 0) {
                double mid = 0.5 * (axLo0 + axHi0), half = 0.5 * (axHi0 - axLo0) * Math.exp((y - axY0) / (150 * d));   // down = out
                yLo[axPane] = mid - half; yHi[axPane] = mid + half;
            } else {
                double span = Math.max(30.0, Math.min(72 * 3600.0, axSpan0 * Math.exp(-(x - axX0) / (200 * d))));  // right = in
                if (follow) { vx1 = M.nowEngine(); vx0 = vx1 - span; }
                else { double mid = 0.5 * (vx0 + vx1); vx0 = mid - span / 2; vx1 = mid + span / 2; }
                long t = System.currentTimeMillis();
                if (t - lastViewSent > 150) sendView(false);
            }
            invalidate(); return true;
        }
        if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) { if (axX) sendView(true); axPane = -1; axX = false; invalidate(); return true; }
        return true;
    }
    private android.content.SharedPreferences prefs;

    private void loadWeights() {
        if (prefs == null) return;
        try {
            String[] p = prefs.getString("pane_wt", "").split(",");
            if (p.length == 4) for (int i = 0; i < 4; i++) paneWt[i] = Math.max(0.06f, Float.parseFloat(p[i]));
        } catch (Exception ignored) { }
    }

    private void saveWeights() {
        if (prefs != null) prefs.edit().putString("pane_wt", paneWt[0] + "," + paneWt[1] + "," + paneWt[2] + "," + paneWt[3]).apply();
    }

    /** The boundary under (x, y) between two visible panes: {upper, lower}, or null. */
    private int[] boundaryAt(float x, float y) {
        if (fullscreen >= 0 || x >= plotR) return null;
        int prev = -1;
        for (int p = 0; p < 4; p++) {
            if (!paneOn[p]) continue;
            if (prev >= 0 && Math.abs(pane[prev].bottom - y) < 14 * d) return new int[]{prev, p};
            prev = p;
        }
        return null;
    }

    private boolean resizeTouch(MotionEvent ev) {
        int a = ev.getActionMasked();
        if (a == MotionEvent.ACTION_DOWN) {
            int[] b = boundaryAt(ev.getX(), ev.getY());
            if (b == null) return false;
            rsUpper = b[0]; rsLower = b[1]; rsY0 = ev.getY(); rsW0 = paneWt[rsUpper]; rsW1 = paneWt[rsLower];
            rsTot = 0; for (int p = 0; p < 4; p++) if (paneOn[p]) rsTot += paneWt[p];
            rsAvail = Math.max(1f, timeY);
            return true;
        }
        if (rsUpper < 0) return false;
        if (a == MotionEvent.ACTION_MOVE) {
            float dw = (ev.getY() - rsY0) / rsAvail * rsTot;          // px moved -> weight moved between the two
            float min = 0.06f * rsTot;
            dw = Math.max(min - rsW0, Math.min(rsW1 - min, dw));
            paneWt[rsUpper] = rsW0 + dw; paneWt[rsLower] = rsW1 - dw;
            invalidate(); return true;
        }
        if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) { rsUpper = rsLower = -1; saveWeights(); invalidate(); return true; }
        return true;
    }
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
    private static final int WALL_COL = Color.parseColor("#dcdcdc"), KEEP_COL = Color.parseColor("#3a4150");   // IIMP_WALL_COL / IIMP_KEEP_COL
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
            @Override public boolean onDown(MotionEvent e) { panPane = paneAt(e.getX(), e.getY()); panAccY = 0; return true; }
            @Override public boolean onScroll(MotionEvent e1, MotionEvent e2, float dx, float dy) {
                if (scale.isInProgress() || axPane >= 0 || axX) return true;
                double span = vx1 - vx0; double dt = dx / Math.max(1f, plotR) * span;
                vx0 += dt; vx1 += dt;
                panAccY += Math.abs(dy);
                if (panPane >= 0 && paneOn[panPane] && panAccY > 6 * d) {
                    takeManual(panPane);
                    float hgt = pane[panPane].bottom - (pane[panPane].top + TITLE_H);
                    double upp = (yHi[panPane] - yLo[panPane]) / Math.max(1f, hgt);
                    yLo[panPane] -= dy * upp; yHi[panPane] -= dy * upp;       // the content follows the finger
                }
                afterPan(); return true;
            }
            @Override public boolean onSingleTapConfirmed(MotionEvent e) { tap(e.getX(), e.getY()); return true; }
            @Override public void onLongPress(MotionEvent e) { if (tools != null && paneOn[PANE_PRICE] && tools.longPress(e.getX(), e.getY())) invalidate(); }
            @Override public boolean onDoubleTap(MotionEvent e) { doubleTap(e.getX(), e.getY()); return true; }
        });
        scale = new ScaleGestureDetector(ctx, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override public boolean onScale(ScaleGestureDetector sd) {
                float minSpan = 40 * d;
                double fx = (sd.getPreviousSpanX() > minSpan && sd.getCurrentSpanX() > minSpan) ? Math.max(0.2, Math.min(5.0, sd.getPreviousSpanX() / sd.getCurrentSpanX())) : 1.0;
                double fy = (sd.getPreviousSpanY() > minSpan && sd.getCurrentSpanY() > minSpan) ? Math.max(0.2, Math.min(5.0, sd.getPreviousSpanY() / sd.getCurrentSpanY())) : 1.0;
                if (fx != 1.0) {
                    double span = vx1 - vx0;
                    double ns = Math.max(30.0, Math.min(72 * 3600.0, span * fx));
                    double focal = vx0 + (sd.getFocusX() / Math.max(1f, plotR)) * span;
                    double frac = (focal - vx0) / span;
                    vx0 = focal - frac * ns; vx1 = vx0 + ns;
                }
                int p = paneAt(Math.min(sd.getFocusX(), plotR - 1), sd.getFocusY());
                if (fy != 1.0 && p >= 0) {
                    takeManual(p);
                    float top = pane[p].top + TITLE_H, hgt = Math.max(1f, pane[p].bottom - top);
                    double yv = yHi[p] - (sd.getFocusY() - top) / hgt * (yHi[p] - yLo[p]);
                    yLo[p] = yv - (yv - yLo[p]) * fy; yHi[p] = yv + (yHi[p] - yv) * fy;
                }
                afterPan(); return true;
            }
        });
        h.postDelayed(heartbeat, 250);
    }

    public void setHost(Host host) { this.host = host; }

    /** The tools live on the PRICE pane: they map through its current axes. */
    public void initTools(android.content.SharedPreferences prefs, PriceTools.Events events) {
        this.prefs = prefs; loadWeights();
        tools = new PriceTools(d, prefs, new PriceTools.Map() {
            @Override public float xPx(double t) { return ChartView.this.xPx(t); }
            @Override public float yPx(double p) { return (float) (pxTop + (pxYh - p) / Math.max(1e-12, pxYh - pxYl) * pxHgt); }
            @Override public double xVal(float px) { return vx0 + (px / Math.max(1f, plotR)) * (vx1 - vx0); }
            @Override public double yVal(float py) { return pxYh - (py - pxTop) / Math.max(1f, pxHgt) * (pxYh - pxYl); }
            @Override public RectF pane() { return pane[PANE_PRICE]; }
            @Override public float plotRight() { return plotR; }
            @Override public double tick() { synchronized (M.lock) { return M.tick; } }
            @Override public int dec() { synchronized (M.lock) { return M.dec; } }
            @Override public double now() { return M.nowEngine(); }
            @Override public double live() { synchronized (M.lock) { return M.livePx; } }
            @Override public float viewRight() { return getWidth(); }
            @Override public int tzOff() { synchronized (M.lock) { return M.tzOff; } }
            @Override public boolean bw() { return bw; }
        }, events);
    }

    /** A data frame: coalesced onto the next vsync. */
    public void dataChanged() {
        if (framePending) return;
        framePending = true;
        postOnAnimation(() -> { framePending = false; invalidate(); });
    }

    public void recentre() { follow = true; pxLo = pxHi = Double.NaN; java.util.Arrays.fill(yAuto, true); sendView(true); invalidate(); }

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
        if (axisTouch(ev)) { gest.onTouchEvent(ev); return true; }   // the axes: zoom drags, and the double tap
        if (resizeTouch(ev)) return true;                         // a pane boundary under the finger: the splitter
        if (tools != null && paneOn[PANE_PRICE] && tools.onTouch(ev)) { invalidate(); return true; }
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
        if (tools != null && paneOn[PANE_PRICE]) {
            if (tools.tapButton(x, y)) { invalidate(); return; }
            if (pane[PANE_PRICE].contains(x, y) && x < plotR && (tools.tapPane(x, y) || "trend".equals(tools.tool))) { invalidate(); return; }
        }
        if (paneOn[PANE_PRICE] && pane[PANE_PRICE].contains(x, y) && x < plotR) { cycleTap(x); return; }
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
        float[] wt = paneWt;
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
        theme();
        c.drawColor(cBg);
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
            s.hlhOn = M.hlhOn && showHlh; s.hlhNote = M.hlhNote; s.hlhPics = M.hlhPics; s.hlhLabels = M.hlhLabels; s.hlhDashes = M.hlhDashes;
            s.bpOn = M.bpOn && showBp; s.bpBub = M.bpBub; s.bpDia = M.bpDia; s.bpLmax = M.bpLmax;
            s.series = new float[3][];
            if (paneOn[PANE_FLOW] && s.buy.length > 0) M.series(vx0 - 1, vx1 + 1, (int) (2 * plotR), s.series);
        }
        // the forming candle's close, the live line and the pill SLIDE to a new tick over 160 ms with the terminal's
        // decelerating ease (_lc_tick); a NEW forming bar shows at once, no slide
        if (!Double.isNaN(s.livePx) && s.n > 0) {
            double cyc = s.cT[s.n - 1];
            if (Double.isNaN(lcTo) || cyc != lcCycle) { lcCycle = cyc; lcFrom = lcTo = lcCur = s.livePx; lcT0 = 0; }
            else if (s.livePx != lcTo) { lcFrom = Double.isNaN(lcCur) ? s.livePx : lcCur; lcTo = s.livePx; lcT0 = System.nanoTime(); }
            if (lcT0 != 0) {
                double tt = (System.nanoTime() - lcT0) / 1.6e8;
                if (tt >= 1.0) { lcCur = lcTo; lcT0 = 0; }
                else { double e = 1.0 - (1.0 - tt) * (1.0 - tt); lcCur = lcFrom + (lcTo - lcFrom) * e; postInvalidateOnAnimation(); }
            } else lcCur = lcTo;
            s.liveAnim = lcCur;
        } else s.liveAnim = s.livePx;
        if (tools != null && !Double.isNaN(s.livePx)) tools.onPrice(s.livePx, now);
        if (paneOn[PANE_PRICE]) drawPrice(c, s, now);
        if (paneOn[PANE_FLOW]) drawFlow(c, s, now);
        if (paneOn[PANE_LIQ]) drawLiq(c, s);
        if (paneOn[PANE_IIMP]) drawIimp(c, s, now);
        if (showLines) drawCycleLines(c, s);
        drawSelection(c, s, now);
        drawTimeAxis(c);
        drawGrips(c);
        if (!s.connected) {
            pt.setTextSize(13 * d); pt.setColor(cTitle); pt.setTypeface(Typeface.MONOSPACE);
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
        boolean hlhOn; String hlhNote; List<FlowModel.HlhPic> hlhPics; List<FlowModel.HlhLabel> hlhLabels; List<FlowModel.HlhDash> hlhDashes;
        boolean bpOn; double[][] bpBub, bpDia; int bpLmax;
        double liveAnim;
    }

    private void title(Canvas c, RectF r, String txt) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d); pt.setColor(cTitle);
        c.drawText(txt, r.left + 6 * d, r.top + 12 * d, pt);
        pf.setColor(cSep);
        c.drawRect(r.left, r.top, r.right + AXIS_W, r.top + 1, pf);
    }

    private static String usdShort(double v) {
        if (v >= 1e9) return String.format(Locale.US, "$%.2fB", v / 1e9);
        if (v >= 1e6) return String.format(Locale.US, "$%.2fM", v / 1e6);
        if (v >= 1e3) return String.format(Locale.US, "$%.0fK", v / 1e3);
        return String.format(Locale.US, "$%.0f", v);
    }

    private void axisLine(Canvas c, RectF r) { pf.setColor(cSep); c.drawRect(plotR, r.top, plotR + 1 * d, r.bottom, pf); }

    private void tick(Canvas c, float y) { pf.setColor(cFg); c.drawRect(plotR, y - 0.5f * d, plotR + 4 * d, y + 0.5f * d, pf); }

    private void yAxisMoney(Canvas c, RectF r, double lo, double hi) {
        double range = hi - lo; if (range <= 0) return;
        double step = niceStep(range / Math.max(2.0, r.height() / (28 * d)));
        axisLine(c, r);
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(cFg);
        for (double v = Math.ceil(lo / step) * step; v <= hi; v += step) {
            float y = (float) (r.bottom - (v - lo) / range * r.height());
            if (y < r.top + 6 * d || y > r.bottom - 2 * d) continue;
            tick(c, y);
            c.drawText(Math.abs(v) < 1e-9 ? "0" : (v < 0 ? "-" + usdShort(-v) : usdShort(v)), plotR + 6 * d, y + 4 * d, pt);
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
        double fo = s.cO[last], fc = Double.isNaN(s.livePx) ? s.cC[last] : s.liveAnim;
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
        double[] prg = yRange(PANE_PRICE, pxLo, pxHi);
        double yl = prg[0], yh = prg[1];
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        pxTop = top; pxHgt = hgt; pxYl = yl; pxYh = yh;
        c.save(); c.clipRect(r.left, r.top, r.right, r.bottom);
        if (s.hlhOn) drawHlh(c, s, r, top, hgt, yl, yh);
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
        // the marked candle: a tap here or on its feed row
        if (!Double.isNaN(selCycle)) {
            int k = nearest(s.cT, selCycle);
            if (k >= 0 && Math.abs(s.cT[k] - selCycle) < 1.0) {
                double te = (k == last && forming) ? fte : s.cTe[k];
                float sx0 = xPx(s.cT[k]), sx1 = xPx(te);
                pf.setColor(bw ? Color.argb(22, 0, 0, 0) : Color.argb(26, 255, 255, 255)); c.drawRect(sx0, top, sx1, r.bottom, pf);
                pl.setColor(Color.parseColor(bw ? "#0B4FA8" : "#7FB2FF")); pl.setStrokeWidth(1.5f * d); c.drawRect(sx0, top, sx1, r.bottom, pl);
            }
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
        if (s.bpOn) drawBp(c, s, top, hgt, yl, yh);
        c.restore();
        if (tools != null) tools.drawShapes(c, s.livePx);
        c.save(); c.clipRect(r.left, r.top, r.right, r.bottom);
        // the live price line + pill
        if (!Double.isNaN(s.livePx)) {
            float y = (float) (top + (yh - s.liveAnim) / (yh - yl) * hgt);
            pl.setColor(bw ? Color.BLACK : Color.parseColor("#e8eaed")); pl.setStrokeWidth(1.2f * d); pl.setPathEffect(new DashPathEffect(new float[]{4 * d, 4 * d}, 0));
            c.drawLine(r.left, y, plotR, y, pl); pl.setPathEffect(null);
            c.restore();
            boolean up = s.livePx >= fo;
            int pc = up ? Color.parseColor("#28e65a") : Color.parseColor("#ef4444");
            String p1 = String.format(Locale.US, "%." + s.dec + "f", s.livePx);
            String p2 = forming ? durText(Math.max(0, fte - s.cT[last])) : "";
            pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d);
            float w = Math.max(pt.measureText(p1), pt.measureText(p2)) + 10 * d;
            y = Math.max(top + 14 * d, Math.min(r.bottom - (forming ? 14 : 2) * d, y));   // the pill stays on its pane
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
        double range = yh - yl; double step = niceStep(range / Math.max(2.0, hgt / (28 * d)));
        axisLine(c, r);
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(cFg);
        for (double v = Math.ceil(yl / step) * step; v <= yh; v += step) {
            float y = (float) (top + (yh - v) / range * hgt);
            if (!Double.isNaN(s.livePx) && Math.abs(y - (float) (top + (yh - s.livePx) / range * hgt)) < 16 * d) continue;
            if (y < top + 4 * d || y > r.bottom - 2 * d) continue;
            tick(c, y);
            c.drawText(String.format(Locale.US, "%." + s.dec + "f", v), plotR + 6 * d, y + 4 * d, pt);
        }
        if (tools != null) tools.drawButtons(c);
    }

    // ------------------------------------------------------------------ HLH Volume Profile (the terminal's geometry)
    private static String fmtUsdBp(double a) {
        if (a >= 1e6) return String.format(Locale.US, "$%.2fM", a / 1e6);
        if (a >= 1e5) return String.format(Locale.US, "$%.0fK", a / 1e3);
        if (a >= 1e3) return String.format(Locale.US, "$%.1fK", a / 1e3);
        return String.format(Locale.US, "$%,.0f", a);
    }

    private void drawHlh(Canvas c, Snap s, RectF r, float top, float hgt, double yl, double yh) {
        double ky = hgt / Math.max(1e-12, yh - yl);
        for (FlowModel.HlhPic pic : s.hlhPics) {
            if (pic.x1 < vx0 || pic.x0 > vx1 || pic.ops == null) continue;
            for (FlowModel.HlhOp op : pic.ops) {
                if (op.t == 'P') {
                    int n = op.v.length / 2; if (n < 2) continue;
                    path.reset();
                    for (int i = 0; i < n; i++) { float x = xPx(op.v[2 * i]), y = (float) (top + (yh - op.v[2 * i + 1]) * ky); if (i == 0) path.moveTo(x, y); else path.lineTo(x, y); }
                    path.close();
                    if (op.brush != 0) { pf.setColor(op.brush); c.drawPath(path, pf); }
                    if (op.pen != 0) { pl.setColor(op.pen); pl.setStrokeWidth(Math.max(1f, op.w * d * PCPX)); c.drawPath(path, pl); }
                } else if (op.t == 'R') {
                    float x0 = xPx(op.v[0]), x1 = xPx(op.v[0] + op.v[2]);
                    float yb = (float) (top + (yh - op.v[1]) * ky), yt = (float) (top + (yh - (op.v[1] + op.v[3])) * ky);
                    if (x1 < r.left || x0 > plotR) continue;
                    float t0 = Math.min(yt, yb), t1 = Math.max(yt, yb); if (t1 - t0 < 0.5f) t1 = t0 + 0.5f;
                    if (op.brush != 0) { pf.setColor(op.brush); c.drawRect(x0, t0, x1, t1, pf); }
                    if (op.pen != 0) { pl.setColor(op.pen); pl.setStrokeWidth(Math.max(1f, op.w * d * PCPX)); c.drawRect(x0, t0, x1, t1, pl); }
                } else {
                    if (op.pen == 0) continue;
                    pl.setColor(op.pen); pl.setStrokeWidth(Math.max(1f, op.w * d * PCPX));
                    c.drawLine(xPx(op.v[0]), (float) (top + (yh - op.v[1]) * ky), xPx(op.v[2]), (float) (top + (yh - op.v[3]) * ky), pl);
                }
            }
        }
        // the outer value areas: dash segments in device space, phase-anchored at each line's own start
        float on = 5 * d * PCPX, off = 4 * d * PCPX;
        for (FlowModel.HlhDash dd : s.hlhDashes) {
            float y = (float) (top + (yh - dd.y) * ky); if (y < top || y > r.bottom) continue;
            float x0 = xPx(Math.min(dd.xa, dd.xb)), x1 = xPx(Math.max(dd.xa, dd.xb));
            if (x1 < r.left || x0 > plotR) continue;
            float xs = Math.max(x0, r.left - 2), xe = Math.min(x1, plotR);
            int k0 = (int) Math.floor((xs - x0) / (on + off));
            int cnt = (int) ((xe - x0) / (on + off)) - k0 + 2; if (cnt <= 0) continue;
            float[] seg = new float[cnt * 4]; int j = 0;
            for (int k = k0; k * (on + off) + x0 < xe; k++) { float xa = x0 + k * (on + off); seg[j++] = xa; seg[j++] = y; seg[j++] = Math.min(xa + on, x1); seg[j++] = y; if (j >= seg.length) break; }
            pl.setColor(dd.col); pl.setStrokeWidth(1 * d); c.drawLines(seg, 0, j, pl);
        }
        // labels: pixel-sized boxes at plot points, the terminal's anchors
        for (FlowModel.HlhLabel lb : s.hlhLabels) {
            float px = xPx(lb.x), py = (float) (top + (yh - lb.y) * ky);
            boolean small = "small".equals(lb.font); boolean mono = "mono".equals(lb.font);
            pt.setTypeface(small ? Typeface.create(Typeface.MONOSPACE, Typeface.BOLD) : Typeface.MONOSPACE);
            pt.setTextSize((small ? 10.5f : 9.5f) * d);
            String[] lines = lb.text.split("\n"); float lh = pt.getTextSize() * 1.25f;
            float w = 0; for (String ln : lines) w = Math.max(w, pt.measureText(ln)); w += 6 * d;
            float h = lines.length * lh + 4 * d;
            float rx, ry;
            switch (lb.anchor) {
                case "left": rx = px + 4 * d; ry = py - h / 2; break;
                case "up": rx = px - w / 2; ry = py + 3 * d; break;
                case "down": rx = px - w / 2; ry = py - h - 3 * d; break;
                case "upper_left": rx = px; ry = py; break;
                default: rx = px + 2 * d; ry = py - h - 2 * d; break;
            }
            if (rx > plotR || rx + w < r.left || ry > r.bottom || ry + h < top) continue;
            if ((lb.bg >>> 24) > 0) { pf.setColor(lb.bg); c.drawRoundRect(new RectF(rx, ry, rx + w, ry + h), 3 * d, 3 * d, pf); }
            pt.setColor(lb.fg);
            float ty = ry + 2 * d - pt.ascent();
            for (String ln : lines) { c.drawText(ln, rx + 3 * d, ty, pt); ty += lh; }
            if (mono) pt.setTypeface(Typeface.MONOSPACE);
        }
        pt.setTypeface(Typeface.MONOSPACE);
        if (s.hlhNote != null && !s.hlhNote.isEmpty()) {
            pt.setTextSize(10.5f * d); float w = pt.measureText(s.hlhNote) + 12 * d;
            pf.setColor(Color.argb(140, 0, 0, 0)); c.drawRoundRect(new RectF(r.left + 8 * d, top + 6 * d, r.left + 8 * d + w, top + 24 * d), 3 * d, 3 * d, pf);
            pt.setColor(Color.rgb(255, 200, 80)); c.drawText(s.hlhNote, r.left + 14 * d, top + 19 * d, pt);
        }
    }

    // ------------------------------------------------------------------ Big Player marks (bubbles, diamonds, amounts)
    private void drawBp(Canvas c, Snap s, float top, float hgt, double yl, double yh) {
        double ky = hgt / Math.max(1e-12, yh - yl);
        List<float[]> labels = new ArrayList<>();
        for (double[] b : s.bpBub) {
            if (b.length < 5 || b[0] < vx0 || b[0] > vx1) continue;
            float x = xPx(b[0]), y = (float) (top + (yh - b[1]) * ky), rad = (float) (b[4] * d * PCPX / 2);
            boolean buy = b[3] > 0;
            pf.setColor(buy ? Color.argb(120, 40, 230, 120) : Color.argb(120, 240, 70, 90)); c.drawCircle(x, y, rad, pf);
            pl.setColor(buy ? Color.argb(235, 40, 230, 120) : Color.argb(235, 240, 70, 90)); pl.setStrokeWidth(1.5f * d * PCPX); c.drawCircle(x, y, rad, pl);
            labels.add(new float[]{x, y, (float) b[2]});
        }
        for (double[] q : s.bpDia) {
            if (q.length < 6 || q[0] < vx0 || q[0] > vx1) continue;
            float x = xPx(q[0]); float ylo = (float) (top + (yh - q[1]) * ky), yhi = (float) (top + (yh - q[2]) * ky);
            float mid = 0.5f * (ylo + yhi), hh = Math.max(Math.abs(ylo - yhi), 12 * d * PCPX);
            float hw = (float) ((5.0 + 4.0 * Math.max(0, Math.min(1, (q[5] - 10.0) / 36.0))) * d * PCPX);
            boolean buy = q[4] > 0;
            path.reset(); path.moveTo(x, mid + 0.5f * hh); path.lineTo(x + hw, mid); path.lineTo(x, mid - 0.5f * hh); path.lineTo(x - hw, mid); path.close();
            pf.setColor(buy ? Color.argb(120, 40, 230, 120) : Color.argb(120, 240, 70, 90)); c.drawPath(path, pf);
            pl.setColor(buy ? Color.argb(235, 40, 230, 120) : Color.argb(235, 240, 70, 90)); pl.setStrokeWidth(1.5f * d * PCPX); c.drawPath(path, pl);
            labels.add(new float[]{x, mid, (float) q[3]});
        }
        if (labels.size() > s.bpLmax) labels = labels.subList(labels.size() - s.bpLmax, labels.size());
        pt.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)); pt.setTextSize(10.5f * d); pt.setColor(bw ? Color.BLACK : Color.WHITE);
        for (float[] l : labels) { String t = fmtUsdBp(l[2]); c.drawText(t, l[0] - pt.measureText(t) / 2, l[1] + 4 * d, pt); }
        pt.setTypeface(Typeface.MONOSPACE);
    }

    private void drawCandle(Canvas c, float xm, float hw, double o, double hh, double ll, double cl, int col, float top, float hgt, double yl, double yh) {
        float yo = (float) (top + (yh - o) / (yh - yl) * hgt), yc = (float) (top + (yh - cl) / (yh - yl) * hgt);
        float yhh = (float) (top + (yh - hh) / (yh - yl) * hgt), yll = (float) (top + (yh - ll) / (yh - yl) * hgt);
        boolean down = cl < o;
        int fill, pen; boolean hollow = false; int hiWick = 0, loWick = 0;
        if (col == C_AB_BUY && !down) { col = -1; hiWick = BAR_COL[C_AB_BUY]; }
        else if (col == C_AB_SELL && !(cl > o)) { col = -1; loWick = BAR_COL[C_AB_SELL]; }
        if (col == C_BRK_BUY || col == C_BRK_SELL || col == C_AB_BUY || col == C_AB_SELL) { fill = BAR_COL[col]; pen = fill; }
        else if (bw) { fill = down ? Color.BLACK : Color.WHITE; pen = Color.BLACK; }
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
        double[] frg = yRange(PANE_FLOW, -room, yt);
        double ylo = frg[0], yhi = frg[1]; double range = yhi - ylo;
        float zeroY = (float) (top + (yhi - 0) / range * hgt);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        pl.setColor(cSep); pl.setStrokeWidth(1 * d); c.drawLine(r.left, zeroY, plotR, zeroY, pl);
        for (int side = 0; side < 2; side++) {
            float[] v = side == 0 ? b : sl;
            path.reset();
            for (int i = 0; i < t.length; i++) {
                // ⚠ (double): binBase is a long and t a float -- their sum was a FLOAT, 128 s steps at 1.79e9
                float x = xPx((double) s.binBase + (double) t[i]); float y = (float) (top + (yhi - v[i]) / range * hgt);
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
        badgePane = r;
        double bn = b[b.length - 1], sn = sl[sl.length - 1], tot = Math.max(1e-9, bn + sn);
        String sb = String.format(Locale.US, "%s (%.0f%%)", usdShort(bn), 100 * bn / tot), ss = String.format(Locale.US, "%s (%.0f%%)", usdShort(sn), 100 * sn / tot);
        float yb = (float) (top + (yhi - bn) / range * hgt), ys = (float) (top + (yhi - sn) / range * hgt);
        badge(c, plotR - 4 * d, yb, sb, TEAL, bn >= sn);
        badge(c, plotR - 4 * d, ys, ss, RED, sn > bn);
        yAxisMoney(c, new RectF(r.left, top, r.right, r.bottom), ylo, yhi);
    }

    private RectF badgePane;                       // the pane a badge must stay inside (set by the pane drawing it)

    private void badge(Canvas c, float xr, float y, String txt, int col, boolean above) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(11 * d); pt.setFakeBoldText(true);
        float w = pt.measureText(txt) + 10 * d, hh = 16 * d;
        if (badgePane != null) y = Math.max(badgePane.top + TITLE_H + (above ? hh + 2 * d : 0), Math.min(badgePane.bottom - (above ? 0 : hh + 2 * d), y));
        RectF pill = above ? new RectF(xr - w, y - hh - 2 * d, xr, y - 2 * d) : new RectF(xr - w, y + 2 * d, xr, y + hh + 2 * d);
        if (!bw) { pf.setColor(Color.parseColor("#1c2128")); c.drawRoundRect(pill, 4 * d, 4 * d, pf); }
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
        double[] lrg = yRange(PANE_LIQ, 0.0, Math.max(1.0, liqTop));
        double ylo = lrg[0], yhi = lrg[1], lrange = Math.max(1e-9, yhi - ylo);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        for (int side = 0; side < 2; side++) {
            float[] v = side == 0 ? s.lqB : s.lqA;
            path.reset(); int n = Math.min(v.length, s.lqX.length);
            for (int i = 0; i < n; i++) {
                float x = xPx(s.lqX[i]); float y = (float) (top + (yhi - v[i]) / lrange * hgt);
                if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
            }
            pl.setColor(side == 0 ? TEAL : RED); pl.setStrokeWidth(2 * d); c.drawPath(path, pl);
            // the dashed rule from the last point to the right edge
            if (n > 0) {
                float xl = xPx(s.lqX[n - 1]); float y = (float) (top + (yhi - v[n - 1]) / lrange * hgt);
                pl.setStrokeWidth(1 * d); pl.setPathEffect(new DashPathEffect(new float[]{4 * d, 6 * d}, 0));
                c.drawLine(xl, y, plotR, y, pl); pl.setPathEffect(null);
            }
        }
        c.restore();
        badgePane = r;
        int n = Math.min(s.lqB.length, s.lqA.length);
        if (n > 0) {
            double bv = s.lqB[n - 1], av = s.lqA[n - 1], tot = bv + av;
            String sb = tot > 0 ? String.format(Locale.US, "%s (%.0f%%)", usdShort(bv), 100 * bv / tot) : usdShort(bv);
            String sa = tot > 0 ? String.format(Locale.US, "%s (%.0f%%)", usdShort(av), 100 * av / tot) : usdShort(av);
            badge(c, plotR - 4 * d, (float) (top + (yhi - bv) / lrange * hgt), sb, TEAL, bv >= av);
            badge(c, plotR - 4 * d, (float) (top + (yhi - av) / lrange * hgt), sa, RED, av > bv);
        }
        yAxisMoney(c, new RectF(r.left, top, r.right, r.bottom), ylo, yhi);
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
        double[] irg = yRange(PANE_IIMP, -lim, lim);
        double yhi = irg[1], ylo = irg[0], range = yhi - ylo;
        float zeroY = (float) (top + (yhi) / range * hgt);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        // guides: the imbalance terciles, dashed; the midline solid
        pl.setColor(cGuide); pl.setStrokeWidth(1 * d); pl.setPathEffect(new DashPathEffect(new float[]{5 * d, 5 * d}, 0));
        for (double g : new double[]{Math.log(0.76) / LN2, Math.log(1.37) / LN2}) { float y = (float) (top + (yhi - g) / range * hgt); c.drawLine(r.left, y, plotR, y, pl); }
        pl.setPathEffect(null); pl.setColor(cMid); c.drawLine(r.left, zeroY, plotR, zeroY, pl);
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
                        if (s.iWall[i] >= 1.06) { pf.setColor(WALL_COL); c.drawCircle(xm, v >= 0 ? tipY - dy : tipY + dy, 3 * d, pf); pl.setColor(WALL_COL); pl.setStrokeWidth(1.2f * d); c.drawCircle(xm, v >= 0 ? tipY - dy : tipY + dy, 3 * d, pl); }
                        else if (s.iWall[i] <= 0.94 && s.iWall[i] > 0) { pl.setColor(WALL_COL); pl.setStrokeWidth(1.2f * d); c.drawCircle(xm, v >= 0 ? tipY - dy : tipY + dy, 3 * d, pl); }
                    }
                    if (!form && s.iKept[i] > -900 && s.iKept[i] <= 0.375) { pl.setColor(KEEP_COL); pl.setStrokeWidth(2.5f * d); c.drawLine(x0, tipY, x1, tipY, pl); }
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
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(cFg);
        double stp = 0.25; while (stp / range * hgt < 26 * d) stp *= 2;
        axisLine(c, r);
        for (double v = Math.ceil(ylo / stp) * stp; v <= yhi + 1e-9; v += stp) {
            float y = (float) (top + (yhi - v) / range * hgt);
            if (y < top + 6 * d || y > r.bottom - 14 * d) continue;
            double x = signed ? Math.pow(2, v) : Math.pow(2, Math.abs(v));
            tick(c, y);
            c.drawText(fmtMult(x), plotR + 6 * d, y + 4 * d, pt);
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

    /** The marked cycle (a tap on its candle or its feed row): one band through EVERY visible pane. */
    private void drawSelection(Canvas c, Snap s, double now) {
        if (Double.isNaN(selCycle) || s.n == 0) return;
        int k = nearest(s.cT, selCycle);
        if (k < 0 || Math.abs(s.cT[k] - selCycle) >= 1.0) return;
        double te = s.cDone[k] == 0 ? Math.max(s.cT[k] + 1e-3, Math.min(now, s.cTe[k])) : s.cTe[k];
        float sx0 = xPx(s.cT[k]), sx1 = xPx(te);
        if (sx1 < 0 || sx0 > plotR) return;
        c.save(); c.clipRect(0, 0, plotR, timeY);
        for (int p = 0; p < 4; p++) {
            if (!paneOn[p]) continue;
            float y0 = pane[p].top + TITLE_H, y1 = pane[p].bottom;
            pf.setColor(bw ? Color.argb(22, 0, 0, 0) : Color.argb(26, 255, 255, 255)); c.drawRect(sx0, y0, sx1, y1, pf);
            pl.setColor(Color.parseColor(bw ? "#0B4FA8" : "#7FB2FF")); pl.setStrokeWidth(1.5f * d); c.drawRect(sx0, y0, sx1, y1, pl);
        }
        c.restore();
    }

    /** A small grip on each boundary between two panes: where a drag resizes them. */
    private void drawGrips(Canvas c) {
        if (fullscreen >= 0) return;
        int prev = -1;
        for (int p = 0; p < 4; p++) {
            if (!paneOn[p]) continue;
            if (prev >= 0) {
                float y = pane[prev].bottom, xm = plotR / 2;
                boolean hot = rsUpper == prev;
                pf.setColor(hot ? Color.parseColor("#3a6ea5") : cSep);
                c.drawRoundRect(new RectF(xm - 22 * d, y - 2.5f * d, xm + 22 * d, y + 2.5f * d), 2.5f * d, 2.5f * d, pf);
            }
            prev = p;
        }
    }

    // ------------------------------------------------------------------ the clock axis
    private void drawTimeAxis(Canvas c) {
        pf.setColor(cSep); c.drawRect(0, timeY, getWidth(), timeY + 1, pf);
        double span = vx1 - vx0; if (span <= 0) return;
        double[] steps = {60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200};
        double step = steps[steps.length - 1];
        for (double st : steps) { if (st / span * plotR >= 90 * d) { step = st; break; } }
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10.5f * d); pt.setColor(cFg);
        int tz; synchronized (M.lock) { tz = M.tzOff; }
        Calendar cal = tz == Integer.MIN_VALUE ? Calendar.getInstance() : Calendar.getInstance(new java.util.SimpleTimeZone(tz * 1000, "PC"));
        for (double t = Math.ceil(vx0 / step) * step; t <= vx1; t += step) {
            float x = xPx(t);
            cal.setTimeInMillis((long) (t * 1000));
            String lab = String.format(Locale.US, "%02d:%02d", cal.get(Calendar.HOUR_OF_DAY), cal.get(Calendar.MINUTE));
            if (step >= 43200) lab = String.format(Locale.US, "%02d/%02d %s", cal.get(Calendar.DAY_OF_MONTH), cal.get(Calendar.MONTH) + 1, lab);
            float w = pt.measureText(lab);
            c.drawText(lab, x - w / 2, timeY + 15 * d, pt);
            pf.setColor(cSep); c.drawRect(x, timeY, x + 1, timeY + 4 * d, pf);
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
