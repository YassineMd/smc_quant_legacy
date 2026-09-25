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
        void onSmooth(String kind, int n);
    }

    public static final int PANE_PRICE = 0, PANE_FLOW = 1, PANE_LIQ = 2, PANE_IIMP = 3,
            PANE_CINT = 4, PANE_CIMP = 5, PANE_KEPT = 6;
    public static final int PANE_N = 7;
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
    private final RectF[] pane = {new RectF(), new RectF(), new RectF(), new RectF(), new RectF(), new RectF(), new RectF()};
    private final boolean[] paneOn = new boolean[PANE_N];
    // the panes' shares of the height (the splitter). The two LINES panes start small: they are off by
    // default, so these only matter once the user turns one on.
    private final float[] paneWt = {0.28f, 0.30f, 0.12f, 0.18f, 0.06f, 0.06f, 0.14f};
    private int rsUpper = -1, rsLower = -1; private float rsY0; private float rsW0, rsW1, rsTot, rsAvail;
    // each pane fits its own y until the user pans or zooms it (then it is theirs, like the terminal's
    // _px_yauto); a double tap on an axis hands every pane back to its fit
    private final boolean[] yAuto = {true, true, true, true, true, true, true};
    private final double[] yLo = new double[PANE_N], yHi = new double[PANE_N],
            lastLo = new double[PANE_N], lastHi = new double[PANE_N];
    private final double[] lineTop = new double[PANE_N];       // each line pane's HELD y scale (0 = not yet fitted)
    // ⚠ THE Y SCALE IS FITTED ONCE AND HELD (2026-09-23): fitting to what was on screen rescaled the pane on
    // every pan. Each latch is cleared only by a change in what the pane MEANS -- see the reset points below.
    private static final int FIT_MIN_N = 20;                   // cycles behind a fit before it may latch
    private String lastIimpMode = null; private int lastIimpSmn = -1, lastLb = -1;
    private final int[] lastLineSmooth = new int[PANE_N];
    private int panPane = -1; private float panAccY = 0;

    /** The pane's y range this frame: its fit, or the range the user panned / zoomed it to. */
    private double[] yRange(int p, double lo, double hi) {
        lastLo[p] = lo; lastHi[p] = hi;
        return yAuto[p] ? new double[]{lo, hi} : new double[]{yLo[p], yHi[p]};
    }

    private void takeManual(int p) { if (yAuto[p]) { yAuto[p] = false; yLo[p] = lastLo[p]; yHi[p] = lastHi[p]; } }
    private double selCycle = Double.NaN;                           // the candle marked by a tap here or in the feed
    // the crosshair (stylus hover). Each pane records the y mapping it drew with, so the cursor can be turned
    // back into that pane's own units -- price, dollars or a multiple.
    private final float[] paneTop = new float[PANE_N], paneHgt = new float[PANE_N];
    private final double[] paneLo = new double[PANE_N], paneHi = new double[PANE_N];
    private boolean crossOn = false, crossBadges = false;
    private double crossX = Double.NaN;
    private int crossPane = -1;
    private float crossY = 0;

    private void notePane(int p, float top, float hgt, double lo, double hi) {
        paneTop[p] = top; paneHgt[p] = hgt; paneLo[p] = lo; paneHi[p] = hi;
    }

    /** The stylus hovering IS the terminal's mouse: it moves the crosshair without touching anything. */
    @Override public boolean onHoverEvent(MotionEvent e) {
        int a = e.getActionMasked();
        if (a == MotionEvent.ACTION_HOVER_EXIT) { clearCross(); return true; }
        float x = e.getX(), y = e.getY();
        if (x >= plotR || y >= timeY) { clearCross(); return true; }
        int p = paneAt(x, y);
        if (p < 0) { clearCross(); return true; }
        crossOn = true; crossBadges = true; crossPane = p; crossY = y;
        crossX = vx0 + (x / Math.max(1f, plotR)) * (vx1 - vx0);
        invalidate();
        return true;
    }

    /** No pen in range, no crosshair (user 2026-09-22). The terminal's mouse never LEAVES the window, so it could
     *  afford to linger ("Badges off, lines linger", _px_hide_cursor); a pen lifts out of range constantly and a
     *  lingering line would just be a stale mark on the chart with nothing pointing at it. */
    private void clearCross() {
        if (!crossOn && !crossBadges) return;
        crossOn = false; crossBadges = false; crossPane = -1; crossX = Double.NaN;
        invalidate();
    }
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
                // INVERTED 2026-09-22 at the user's word: dragging RIGHT along the clock now zooms OUT. The
                // drag pulls the time axis the way a scrollbar moves, so right = more time on screen.
                double span = Math.max(30.0, Math.min(72 * 3600.0, axSpan0 * Math.exp((x - axX0) / (200 * d))));   // right = out
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
            // a file written before the two LINES panes existed has FOUR entries: keep them and leave the new
            // two at their defaults, rather than throwing the user's layout away
            for (int i = 0; i < Math.min(p.length, PANE_N); i++) paneWt[i] = Math.max(0.06f, Float.parseFloat(p[i]));
        } catch (Exception ignored) { }
    }

    private void saveWeights() {
        if (prefs == null) return;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < PANE_N; i++) sb.append(i == 0 ? "" : ",").append(paneWt[i]);
        prefs.edit().putString("pane_wt", sb.toString()).apply();
    }

    /** The boundary under (x, y) between two visible panes: {upper, lower}, or null. */
    private int[] boundaryAt(float x, float y) {
        if (fullscreen >= 0 || x >= plotR) return null;
        int prev = -1;
        for (int p = 0; p < PANE_N; p++) {
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
            rsTot = 0; for (int p = 0; p < PANE_N; p++) if (paneOn[p]) rsTot += paneWt[p];
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
    // one smoothing slider per LINE pane (I x I in its lines mode, plus the two split panes)
    private final float[] slX0 = new float[PANE_N], slX1 = new float[PANE_N],
            slY0 = new float[PANE_N], slY1 = new float[PANE_N];
    private int slDrag = -1;
    public boolean showCint = false, showCimp = false;
    public boolean showDomPrice = true;     // LINES IMPACT's areas on the PRICE chart (menu toggle, user 2026-09-23)
    // paints
    private final Paint pl = new Paint(Paint.ANTI_ALIAS_FLAG), pf = new Paint(Paint.ANTI_ALIAS_FLAG), pt = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path(), path2 = new Path(), path3 = new Path();
    private final GestureDetector gest; private final ScaleGestureDetector scale;
    private final Handler h = new Handler(Looper.getMainLooper());
    private boolean framePending = false;
    private final Runnable heartbeat = new Runnable() { @Override public void run() { if (follow) invalidate(); h.postDelayed(this, 250); } };
    private static final int TEAL = Color.parseColor("#26a69a"), RED = Color.parseColor("#ef5350"), ORANGE = Color.parseColor("#ff9f43");
    // LINES IMPACT, the bands the leader CLIMBED into: the user's own swatches (2026-09-23). The sellers'
    // is PURPLE, not red -- bright red against the dim band's #ef5350 was red on red, separating only by
    // weight, which the 38-vs-95 alpha was already doing. Purple separates it by HUE.
    private static final int DOM_HI_BUY = Color.parseColor("#66FF00"), DOM_HI_SELL = Color.parseColor("#BE03FD");
    private static final float DOM_BOX_PAD = 6f;     // dp: how far a price-chart area reaches past its candles' high / low
    private static final int LOSS_GREY = Color.parseColor("#7a828e");   // a side that LOST 0.3x of impact
    private static final int WALL_COL = Color.parseColor("#dcdcdc"), KEEP_COL = Color.parseColor("#3a4150");   // IIMP_WALL_COL / IIMP_KEEP_COL
    private static final int BG = Color.parseColor("#141414"), FG = Color.parseColor("#dcdcdc"), TITLE = Color.parseColor("#7d8492");
    private static final int WEAK = Color.parseColor("#8a919c"), GUIDE = Color.parseColor("#9aa4b2");
    private static final int B_UP = Color.rgb(26, 154, 96), B_DN = Color.rgb(208, 48, 48), B_CONTRA = Color.rgb(222, 130, 0), B_FLAT = Color.rgb(122, 130, 140);
    private static final int[] BAR_COL = {Color.parseColor("#FF9500"), Color.parseColor("#00C853"), Color.parseColor("#FF1F1F"), Color.parseColor("#E2574C"), Color.parseColor("#6B7A82"), Color.parseColor("#4E5C64"), Color.parseColor("#2979FF"), Color.parseColor("#76FF03"), Color.parseColor("#D500F9")};
    // 7 / 8 = a BREAKOUT whose I x I bar is orange (its leader is not the way price broke, user 2026-09-23): bright green
    // up through sellers, bright purple down through buyers -- flow_interp.C_BREAK_*_X, the engine's colour index
    private static final int C_AB_BUY = 0, C_BRK_BUY = 1, C_BRK_SELL = 2, C_AB_SELL = 6, C_BRK_BUY_X = 7, C_BRK_SELL_X = 8;
    // 9 / 10 = a VACUUM candle (user 2026-09-25: "use very low opacity red/green for vaccum"): the breakout green / red,
    // with the outline and wicks SOLID like a breakout's and the body FAINT -- flow_interp.C_VACUUM_UP / C_VACUUM_DN;
    // the body's alpha mirrors config.VAC_CANDLE_FILL_A
    private static final int C_VAC_UP = 9, C_VAC_DN = 10, VAC_FILL_A = 56;
    private static final float CONF_PAD = 3f;        // dp: how far a CONFLICT bar's red box stands off its body, each side
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
                if (fx != 1.0) {
                    double span = vx1 - vx0;
                    double ns = Math.max(30.0, Math.min(72 * 3600.0, span * fx));
                    double focal = vx0 + (sd.getFocusX() / Math.max(1f, plotR)) * span;
                    double frac = (focal - vx0) / span;
                    vx0 = focal - frac * ns; vx1 = vx0 + ns;
                }
                // y is never pinched (user 2026-09-22): a drag pans it, the axis drag scales it
                afterPan(); return true;
            }
        });
        h.postDelayed(heartbeat, 250);
    }

    public void setHost(Host host) { this.host = host; }

    /** The tools live on the PRICE pane: they map through its current axes. */
    public void initTools(android.content.SharedPreferences prefs, PriceTools.Events events) {
        this.prefs = prefs; loadWeights();
        keptCum = prefs.getBoolean("kept_cum", false);
        keptN = Math.max(1, Math.min(KEPT_N_MAX, prefs.getInt("kept_n", KEPT_N_DEFAULT)));
        keptNet = prefs.getBoolean("kept_net", false);
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
            @Override public int hvpFade() {
                synchronized (M.lock) { return M.hvpOn ? PriceTools.fadeMask(M.livePx, M.hvpLo, M.hvpHi, M.hvpDir, M.hvpPoc) : 0; }
            }
        }, events);
    }

    /** A data frame: coalesced onto the next vsync. */
    public void dataChanged() {
        if (framePending) return;
        framePending = true;
        postOnAnimation(() -> { framePending = false; invalidate(); });
    }

    public void recentre() {
        follow = true; pxLo = pxHi = Double.NaN; java.util.Arrays.fill(yAuto, true);
        iimpTop = 0; java.util.Arrays.fill(lineTop, 0);          // the HELD scales refit on the next frame
        sendView(true); invalidate();
    }

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
        // ⚠ THE STYLUS IS A POINTER WHILE IT HOVERS AND A FINGER WHILE IT TOUCHES (user 2026-09-22). The pen
        // once took over a DRAG to move the crosshair, which cost it panning -- the one thing the user reaches for
        // most. Hover already drives the crosshair with no conflict, so every touch, pen or finger, now falls
        // straight through to the same pan / pinch / tap path. A pen that cannot hover simply has no crosshair.
        if (ev.getActionMasked() == MotionEvent.ACTION_DOWN) clearCross();   // touching is not hovering
        if (smoothTouch(ev)) return true;      // a slider owns its own drag, before pan / pinch see it
        if (keptTouch(ev)) return true;        // the KEPT pane's toggle and Net: they sit in the splitter's grab band
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
        for (int p = 0; p < PANE_N; p++) if (paneOn[p] && pane[p].contains(x, y)) return p;
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
        boolean[] on = {showPrice, showFlow, showLiq, showIimp, showCint, showCimp, showKept};
        float[] wt = paneWt;
        if (fullscreen >= 0 && on[fullscreen]) { for (int p = 0; p < PANE_N; p++) on[p] = p == fullscreen; }
        float tot = 0; for (int p = 0; p < PANE_N; p++) if (on[p]) tot += wt[p];
        float y = 0; float avail = timeY;
        for (int p = 0; p < PANE_N; p++) {
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
            s.cMove = M.cMove; s.cO = M.cO; s.cH = M.cH; s.cL = M.cL; s.cC = M.cC; s.cLead = M.cLead; s.cConf = M.cConf;
            s.livePx = M.livePx; s.formCol = M.formCol; s.win = M.win; s.dec = M.dec; s.tick = M.tick;
            s.lqX = M.lqX; s.lqB = M.lqB; s.lqA = M.lqA;
            s.mode = M.iimpMode; s.iN = M.iN; s.iX0 = M.iX0; s.iX1 = M.iX1; s.iV = M.iV; s.iMult = M.iMult; s.iScore = M.iScore; s.iWall = M.iWall; s.iKept = M.iKept;
            s.iSbuy = M.iSbuy; s.iSsell = M.iSsell; s.iLiib = M.iLiib; s.iLiis = M.iLiis; s.iUp = M.iUp; s.iContra = M.iContra; s.iGood = M.iGood; s.iForm = M.iForm;
            s.iLyb = M.iLyb; s.iLys = M.iLys; s.iSmn = M.iSmn; s.iPback = M.iPback; s.iReach = M.iReach; s.iMv = M.iMv;
            s.connected = M.connected;
            s.cint = M.cint; s.cimp = M.cimp;
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
        domKeptBuild(s);                              // the bright areas' kept filter, for PRICE and LINES IMPACT alike
        if (paneOn[PANE_PRICE]) drawPrice(c, s, now);
        if (paneOn[PANE_FLOW]) drawFlow(c, s, now);
        if (paneOn[PANE_LIQ]) drawLiq(c, s);
        if (paneOn[PANE_IIMP]) drawIimp(c, s, now);
        if (paneOn[PANE_CINT]) drawLinesPane(c, s, PANE_CINT);
        if (paneOn[PANE_CIMP]) drawLinesPane(c, s, PANE_CIMP);
        if (paneOn[PANE_KEPT]) drawKept(c, s, now);
        if (showLines) drawCycleLines(c, s);
        drawSelection(c, s, now);
        drawCrosshair(c, s);
        drawTimeAxis(c);
        drawCrossClock(c);
        drawGrips(c);
        if (!s.connected) {
            pt.setTextSize(13 * d); pt.setColor(cTitle); pt.setTypeface(Typeface.MONOSPACE);
            c.drawText("connecting to the engine (adb reverse tcp:8766)...", 12 * d, getHeight() - TAXIS_H - 8 * d, pt);
        }
    }

    private static final class Snap {
        long binBase; float[] buy, sell; float[][] series;
        int n; double[] cT, cTe; byte[] cSide, cStrong, cDone, cCol, cSt, cLead, cConf; float[] cMove, cO, cH, cL, cC;
        double livePx, win, tick; int formCol, dec;
        double[] lqX; float[] lqB, lqA;
        String mode; int iN, iSmn; double[] iX0, iX1; float[] iV, iMult, iScore, iWall, iKept, iSbuy, iSsell, iLiib, iLiis, iLyb, iLys, iPback, iReach, iMv; byte[] iUp, iContra, iGood, iForm;
        boolean connected;
        FlowModel.Lines cint, cimp;
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
        notePane(PANE_PRICE, top, hgt, yl, yh);
        c.save(); c.clipRect(r.left, r.top, r.right, r.bottom);
        // LINES IMPACT's areas, behind everything else on the price chart (user 2026-09-23): a box around the area's
        // own candles, not the pane's full height. They come with the engine's LINES IMPACT data, which flows
        // whether or not that pane is shown here.
        if (showDomPrice) drawDomBoxes(c, s, r.left, top, hgt, yl, yh, forming, fh, fl);
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
        // THE CONFLICT BARS (user 2026-09-25: "conflict bars are where the tape of both side >=3x" / "the box should be
        // RED color 2px width from the high to the low of the conflict bar" / "if two or more consecutive conflict bars
        // we merge them"): the engine flags them (cf). Each RUN of consecutive flagged cycles gets ONE red frame, from
        // the first candle's left to the last one's right (CONF_PAD beyond the bodies) and from the run's highest high
        // to its lowest low -- over the candles, so a neighbour never hides it. A run the view cuts is still measured
        // whole. The forming candle counts on its live high / low while its tapes stand there.
        if (s.cConf != null) {
            pl.setColor(Color.RED); pl.setStrokeWidth(2 * d);
            int nC = Math.min(s.n, s.cConf.length);
            int a = Math.max(0, Math.min(i0, nC));
            while (a > 0 && a < nC && s.cConf[a] != 0 && s.cConf[a - 1] != 0) a--;   // back to the start of a run the view cuts
            for (int i = a; i < nC && i < i1; ) {
                if (s.cConf[i] == 0) { i++; continue; }
                int j = i;
                while (j + 1 < nC && s.cConf[j + 1] != 0) j++;             // the run [i, j], past the view if need be
                float xl = Float.MAX_VALUE, xr = -Float.MAX_VALUE;
                double rHi = -Double.MAX_VALUE, rLo = Double.MAX_VALUE;
                for (int k = i; k <= j; k++) {
                    boolean isForm = k == last && forming;
                    double hh = isForm ? fh : s.cH[k], ll = isForm ? fl : s.cL[k];
                    if (Double.isNaN(hh) || Double.isNaN(ll)) continue;
                    double te = isForm ? fte : s.cTe[k];
                    double dur = Math.max(1e-9, te - s.cT[k]);
                    float xm = xPx(s.cT[k] + dur * 0.5), hw = Math.max(0.6f * d, (float) (dur * 0.72 * 0.5 / (vx1 - vx0) * plotR));
                    xl = Math.min(xl, xm - hw); xr = Math.max(xr, xm + hw);
                    rHi = Math.max(rHi, hh); rLo = Math.min(rLo, ll);
                }
                if (xr >= xl) {
                    float yhi = (float) (top + (yh - rHi) / (yh - yl) * hgt), ylo = (float) (top + (yh - rLo) / (yh - yl) * hgt);
                    c.drawRect(xl - CONF_PAD * d, yhi, xr + CONF_PAD * d, ylo, pl);
                }
                i = j + 1;
            }
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
        // takeover marks (the rule: tkoBuild)
        if (showTakeover) {
            tkoBuild(s);
            drawTriangles(c, s, tkoBuy, true, top, hgt, yl, yh);
            drawTriangles(c, s, tkoSell, false, top, hgt, yl, yh);
            double[] tf = forming ? tkoForming(s) : null;
            if (tf != null) {
                boolean up = tf[2] > 0.5;
                float x = xPx(0.5 * (s.cT[last] + fte));
                float y = (float) (top + (yh - tf[1]) / (yh - yl) * hgt) + (up ? 9 * d : -9 * d);
                triangle(c, x, y, up, 110);
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
        else if (col == C_BRK_BUY_X || col == C_BRK_SELL_X) {       // the bright pair keeps a darker outline: neon on white washes out
            fill = BAR_COL[col]; pen = Color.rgb(Color.red(fill) * 2 / 3, Color.green(fill) * 2 / 3, Color.blue(fill) * 2 / 3);
        }
        else if (col == C_VAC_UP || col == C_VAC_DN) {              // a vacuum: the breakout's green / red, solid outline, faint body
            int h = BAR_COL[col == C_VAC_UP ? C_BRK_BUY : C_BRK_SELL];
            fill = Color.argb(VAC_FILL_A, Color.red(h), Color.green(h), Color.blue(h));
            pen = h;
        }
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

    // ------------------------------------------------------------------ THE TAKEOVER (user 2026-09-25: REPLACES the I x I rule)
    // A FINISHED cycle is marked for a side -- a green ▲ under the buyers', a red ▼ over the sellers' -- when both hold:
    //   1. KEPT TICKS, always ROLLING 3 (whatever the pane shows): that side's >= 0 and the other side's < 0. This also
    //      NAMES the side: the two can never hold at once;
    //   2. LINES IMPACT: that side's line has a THICK CLIMB at that cycle (runMark +1: the very segment drawn thick);
    //   3. THE CANDLE: a green badge is never on a BEARISH candle, a red one never on a BULLISH candle (user 2026-09-25)
    //      -- close vs open in whole ticks, like the kept ticks; a doji (0t) is neither, so it blocks nothing.
    // The Market Position BIAS was a third condition (97687ad: the HLH bloc rebuilt per past cycle by the engine) until
    // the user removed it the same day: "remove the bias from the takeover indicator". The FORMING cycle gets a lighter
    // mark from the kept it would give if it closed now and its forming Lines Impact step.
    private static final int TKO_KEPT_N = 3;
    private Object[] tkoKey = null; private long tkoLogAt = 0;
    private double[] tkoBuy = new double[0], tkoSell = new double[0];

    /** {buyers kept, sellers kept} of cycle i at close price `cl`: its leader's move in ticks, the seller's flipped. */
    private static float[] keptOf(Snap s, int i, double cl) {
        int ld = s.cLead != null && i < s.cLead.length ? s.cLead[i] : 0;
        double mv = (cl - s.cO[i]) / Math.max(1e-12, s.tick);
        long t = Double.isNaN(mv) ? 0 : Math.round(mv);
        return new float[]{ld > 0 ? t : 0, ld < 0 ? -t : 0};
    }

    /** Condition 3: may a badge of `side` (+1 green, -1 red) sit on a candle that moved `mvTicks` (close - open)? */
    private static boolean candleAllows(int side, long mvTicks) {
        return side > 0 ? mvTicks >= 0 : mvTicks <= 0;
    }

    /** +1 buyers, -1 sellers, 0 neither: the side whose rolling kept is >= 0 while the other's is < 0. */
    private static int keptSide(float sb, float ss) {
        return (sb >= 0 && ss < 0) ? 1 : ((ss >= 0 && sb < 0) ? -1 : 0);
    }

    private void tkoBuild(Snap s) {
        FlowModel.Lines L = s.cimp;
        double[] lx0 = L != null ? L.x0 : null, lx1 = L != null ? L.x1 : null;
        float[] lb = L != null ? L.b : null, ls = L != null ? L.s : null; byte[] lf = L != null ? L.form : null;
        Object[] key = {s.cT, s.cDone, s.cC, s.cO, s.cLead, s.n, s.tick, lx0, lb, ls, lf};
        if (tkoKey != null && java.util.Arrays.equals(tkoKey, key)) return;
        tkoKey = key;
        int n = s.n;
        java.util.ArrayList<Double> buy = new java.util.ArrayList<>(), sell = new java.util.ArrayList<>();
        int ln = lx0 == null ? 0 : Math.min(L.n, Math.min(lx0.length, Math.min(lx1.length, Math.min(lb.length, ls.length))));
        if (ln > 0 && n > 0) {
            byte[] mkB = runMark(lb, lx0, lx1, ln, lf, L.step), mkS = runMark(ls, lx0, lx1, ln, lf, L.step);
            float[] rb = new float[TKO_KEPT_N], rs = new float[TKO_KEPT_N];
            int cnt = 0, nKeptB = 0, nKeptS = 0, nLines = 0, nClimb = 0, nMark = 0;
            for (int i = 0; i < n; i++) {
                if (s.cDone == null || i >= s.cDone.length || s.cDone[i] == 0) continue;
                float[] kv = keptOf(s, i, s.cC[i]);
                rb[cnt % TKO_KEPT_N] = kv[0]; rs[cnt % TKO_KEPT_N] = kv[1]; cnt++;
                float sb = 0, ss = 0;
                for (int q = 0; q < Math.min(cnt, TKO_KEPT_N); q++) { sb += rb[q]; ss += rs[q]; }
                int b = keptSide(sb, ss);
                if (b == 0) continue;
                if (b > 0) nKeptB++; else nKeptS++;
                int j = nearest(lx0, s.cT[i]);
                if (j < 0 || j >= ln || Math.abs(lx0[j] - s.cT[i]) > 1.0) continue;
                nLines++;
                if ((b > 0 ? mkB[j] : mkS[j]) <= 0) continue;
                nClimb++;
                if (!candleAllows(b, Math.round((s.cC[i] - s.cO[i]) / Math.max(1e-12, s.tick)))) continue;
                nMark++; (b > 0 ? buy : sell).add(s.cT[i]);
            }
            long nowMs = System.currentTimeMillis();
            if (nowMs - tkoLogAt > 10000) {                        // what each condition keeps (logcat FLOW)
                tkoLogAt = nowMs;
                StringBuilder mk = new StringBuilder();           // the marks themselves, UTC start times
                java.text.SimpleDateFormat hf = new java.text.SimpleDateFormat("HH:mm:ss", Locale.US);
                hf.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
                for (double t : buy) mk.append(" B").append(hf.format(new java.util.Date((long) (t * 1000))));
                for (double t : sell) mk.append(" S").append(hf.format(new java.util.Date((long) (t * 1000))));
                android.util.Log.i("FLOW", String.format(Locale.US, "TKO: %d finished, kept names a side %d (buyers %d / sellers %d), with Lines Impact held %d, + thick climb %d, + candle agrees %d = marks (buy %d / sell %d); lines %d rows %.0f..%.0f;%s",
                        cnt, nKeptB + nKeptS, nKeptB, nKeptS, nLines, nClimb, nMark, buy.size(), sell.size(), ln, ln > 0 ? lx0[0] : 0.0, ln > 0 ? lx0[ln - 1] : 0.0, mk));
            }
        }
        tkoBuy = new double[buy.size()]; for (int i = 0; i < tkoBuy.length; i++) tkoBuy[i] = buy.get(i);
        tkoSell = new double[sell.size()]; for (int i = 0; i < tkoSell.length; i++) tkoSell[i] = sell.get(i);
    }

    /** The FORMING cycle's mark, {start, price to hang it from, 1 buy / 0 sell}, or null. */
    private double[] tkoForming(Snap s) {
        int n = s.n, last = n - 1;
        if (n == 0 || s.cDone == null || last >= s.cDone.length || s.cDone[last] != 0 || Double.isNaN(s.livePx)) return null;
        float[] kv = keptOf(s, last, s.livePx);                  // the kept rolling 3 if it closed now: names the side
        float sb = kv[0], ss = kv[1];
        int got = 1;
        for (int i = last - 1; i >= 0 && got < TKO_KEPT_N; i--) {
            if (s.cDone[i] == 0) continue;
            float[] q = keptOf(s, i, s.cC[i]); sb += q[0]; ss += q[1]; got++;
        }
        int b = keptSide(sb, ss);
        if (b == 0) return null;
        if (!candleAllows(b, Math.round((s.livePx - s.cO[last]) / Math.max(1e-12, s.tick)))) return null;   // the live body
        FlowModel.Lines L = s.cimp;
        double[] lx0 = L.x0, lx1 = L.x1; float[] y = b > 0 ? L.b : L.s; byte[] lf = L.form;
        int k = nearest(lx0, s.cT[last]);
        if (k < 1 || k >= y.length || k >= lx1.length || Math.abs(lx0[k] - s.cT[last]) > 1.0) return null;
        if (lf == null || k >= lf.length || lf[k] == 0 || y[k] < -900f || y[k - 1] < -900f || lx0[k] - lx1[k - 1] > 0.5) return null;
        if (!(Math.pow(2.0, y[k]) - Math.pow(2.0, y[k - 1]) >= L.step - 1e-9)) return null;   // its climb, runMark's rule
        return new double[]{s.cT[last], b > 0 ? Math.min(s.cL[last], s.livePx) : Math.max(s.cH[last], s.livePx), b > 0 ? 1 : 0};
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
        notePane(PANE_FLOW, top, hgt, ylo, yhi);
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
            if (t.length > 0) {                                   // the dashed rule from the last point to the axis (as LIMIT ORDERS)
                float xl = xPx((double) s.binBase + (double) t[t.length - 1]); float yl = (float) (top + (yhi - v[v.length - 1]) / range * hgt);
                pl.setStrokeWidth(1 * d); pl.setPathEffect(new DashPathEffect(new float[]{4 * d, 6 * d}, 0));
                c.drawLine(xl, yl, plotR, yl, pl); pl.setPathEffect(null);
            }
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
        notePane(PANE_LIQ, top, hgt, ylo, yhi);
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
        double lim, fitNow = 0;
        // the reset points: a new mode, a new smoothing window IN THE DATA, a new lookback
        if (lastIimpMode == null || !lastIimpMode.equals(s.mode)) { iimpTop = 0; lastIimpMode = s.mode; }
        if (lines && s.iSmn != lastIimpSmn) { iimpTop = 0; lastIimpSmn = s.iSmn; }
        if (M.lb != lastLb) { iimpTop = 0; java.util.Arrays.fill(lineTop, 0); lastLb = M.lb; }
        {
            float[] fit;
            if (lines && s.iN > 0) { fit = new float[2 * s.iN]; for (int i = 0; i < s.iN; i++) { fit[i] = (float) Math.abs(Math.max(-clip, Math.min(clip, s.iLyb[i]))); fit[s.iN + i] = (float) Math.abs(Math.max(-clip, Math.min(clip, s.iLys[i]))); } }
            else if (s.iN > 0) { fit = new float[s.iN]; for (int i = 0; i < s.iN; i++) fit[i] = Math.abs(s.iV[i]); }
            else fit = new float[0];
            if (fit.length > 0) {
                java.util.Arrays.sort(fit);
                double p95 = fit[Math.min(fit.length - 1, (int) (0.95 * (fit.length - 1)))] * 1.15;
                fitNow = Math.min(Math.max(Math.max(p95, Math.log(1.37) / LN2 * 1.4), 1.0), clip * 1.15);
                // ⚠ LATCH, do not follow: it was a dead-band refit on every frame, which is what rescaled the
                // pane as the user panned. Held once enough cycles back it; a boot-time handful may not freeze it.
                if (iimpTop <= 0 && fit.length >= (lines ? 2 : 1) * FIT_MIN_N) iimpTop = fitNow;
            }
        }
        lim = Math.max(1.0, iimpTop > 0 ? iimpTop : fitNow);
        double[] irg = yRange(PANE_IIMP, -lim, lim);
        double yhi = irg[1], ylo = irg[0], range = yhi - ylo;
        notePane(PANE_IIMP, top, hgt, ylo, yhi);
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
                    // ⚠ the SMOOTHED pair, not the raw one: the raw one is what the Takeover badge and the
                    // bar readouts use, and drawing it here made this pane's slider do nothing (2026-09-23)
                    float[] v = side == 0 ? s.iLyb : s.iLys; int col = side == 0 ? TEAL : RED;
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
                    int hx; int fillA, penA; boolean conv;       // conv: the bar's fill is the leader's "it converted"
                    if ("Buyer".equals(s.mode) || "Seller".equals(s.mode)) {
                        boolean buyer = "Buyer".equals(s.mode); hx = buyer ? TEAL : RED;
                        boolean led = buyer == up;
                        fillA = led ? (good ? 190 : 0) : 70; penA = led ? 255 : 130; conv = led && good;
                    } else if ("Delta".equals(s.mode)) {
                        hx = contra ? ORANGE : (v >= 0 ? TEAL : RED); fillA = good ? 190 : 0; penA = 255; conv = good;
                    } else {
                        hx = contra ? ORANGE : (up ? TEAL : RED); fillA = good ? 190 : 0; penA = 255; conv = good;
                    }
                    if (form) { fillA = good ? 70 : 0; penA = 150; }
                    // PARTIAL FILL (user 2026-09-24): a converted bar that went the leader's way is solid only up to the share
                    // of the push the leader KEPT, from the 1x line out; the rest is its outline. ORANGE keeps its whole fill
                    // (kept < 0 by definition; the bright candles read it) and an unread kept (-999) is never a downgrade --
                    // the terminal's _iimp_kept_frac
                    double kf = (conv && !contra && s.iKept[i] > -900) ? Math.max(0.0, Math.min(1.0, s.iKept[i])) : 1.0;
                    boolean partial = fillA > 0 && kf < 1.0;
                    float y0 = (float) (top + (yhi - Math.max(0, v)) / range * hgt), y1 = (float) (top + (yhi - Math.min(0, v)) / range * hgt);
                    if (y1 - y0 < 1) y1 = y0 + 1;
                    if (fillA > 0 && (!partial || kf > 0)) {
                        float fy0 = y0, fy1 = y1;
                        if (partial) {
                            double vk = v * kf;
                            fy0 = (float) (top + (yhi - Math.max(0, vk)) / range * hgt); fy1 = (float) (top + (yhi - Math.min(0, vk)) / range * hgt);
                        }
                        pf.setColor((hx & 0x00ffffff) | (fillA << 24)); c.drawRect(x0, fy0, x1, fy1, pf);
                        if (partial) { pl.setColor((hx & 0x00ffffff) | (penA << 24)); pl.setStrokeWidth(1.0f * d); c.drawRect(x0, fy0, x1, fy1, pl); }
                    }
                    pl.setColor((hx & 0x00ffffff) | (penA << 24)); pl.setStrokeWidth((good && !partial ? 1.0f : 1.4f) * d); c.drawRect(x0, y0, x1, y1, pl);
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
        // the smoothing slider serves Lines Buyer/Seller ONLY, and sits LEFT of the dropdown -- drawing it
        // first put it UNDER the button, which is what the device showed on the first run
        if (lines) drawSmoothSlider(c, PANE_IIMP, r, M.smIimp, ddX0 - 26 * d);
        else { slX0[PANE_IIMP] = slX1[PANE_IIMP] = 0; }
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
        // a SHORT push (under 4 ticks) quotes no multiple -- the terminal's _iimp_impact_txt (2026-09-24)
        double rch = (s.iReach != null && k < s.iReach.length && s.iReach[k] > -900) ? s.iReach[k] : Double.NaN;
        boolean shortPush = !Double.isNaN(rch) && rch < 4.0;
        if (shortPush) sb.append("  ·  impact ").append(rch >= 1 ? "short push" : "no push");
        else sb.append("  ·  impact ").append(String.format(Locale.US, "%.2g", Math.exp(s.iScore[k] > -900 ? s.iScore[k] : 0))).append('x');
        sb.append("  ·  wall ").append(s.iWall[k] > -900 ? String.format(Locale.US, "%.2gx", s.iWall[k]) : "-");
        // KEPT: a percent only for a real push that closed on the leader's side, else the signed ticks it held
        double lmv = (s.iMv != null && k < s.iMv.length && s.iMv[k] > -900) ? (s.iUp[k] != 0 ? s.iMv[k] : -s.iMv[k]) : Double.NaN;
        String keptTxt;
        if (s.iKept[k] > -900 && s.iKept[k] >= 0 && !Double.isNaN(rch) && rch >= 4.0) keptTxt = String.format(Locale.US, "%d%%", Math.round(100 * s.iKept[k]));
        else if (!Double.isNaN(lmv)) { long nt = Math.round(lmv); keptTxt = nt == 0 ? "0t" : (nt > 0 ? "+" + nt + "t" : "\u2212" + (-nt) + "t"); }
        else keptTxt = "-";
        sb.append("  ·  kept ").append(keptTxt);
        // the OTHER side's answer: how hard it pushed price back from the leader's extreme (2026-09-24)
        sb.append("  ·  ").append(s.iUp[k] != 0 ? "sell" : "buy").append(" push-back ")
          .append(s.iPback != null && k < s.iPback.length && s.iPback[k] > -900 ? String.format(Locale.US, "%.2gx", s.iPback[k]) : "-");
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
            for (int p = 0; p < PANE_N; p++) {
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
        for (int p = 0; p < PANE_N; p++) {
            if (!paneOn[p]) continue;
            float y0 = pane[p].top + TITLE_H, y1 = pane[p].bottom;
            pf.setColor(bw ? Color.argb(22, 0, 0, 0) : Color.argb(26, 255, 255, 255)); c.drawRect(sx0, y0, sx1, y1, pf);
            pl.setColor(Color.parseColor(bw ? "#0B4FA8" : "#7FB2FF")); pl.setStrokeWidth(1.5f * d); c.drawRect(sx0, y0, sx1, y1, pl);
        }
        c.restore();
    }

    /** The crosshair: the terminal's, pane for pane. */
    private void drawCrosshair(Canvas c, Snap s) {
        if (!crossOn || Double.isNaN(crossX) || fullscreen >= 0 && !paneOn[fullscreen]) return;
        float cx = xPx(crossX);
        int ink = bw ? Color.argb(150, 0, 0, 0) : Color.argb(150, 170, 170, 170);
        float on = 4 * d, off = 8 * d;
        // the VERTICAL through every visible pane -- dashes as segments, this file's rule
        if (cx >= 0 && cx <= plotR) {
            pl.setColor(ink); pl.setStrokeWidth(1 * d);
            for (int p = 0; p < PANE_N; p++) {
                if (!paneOn[p]) continue;
                float y0 = pane[p].top + TITLE_H, y1 = pane[p].bottom;
                int per = (int) Math.ceil((y1 - y0) / (on + off)) + 1;
                float[] seg = new float[per * 4]; int j = 0;
                for (float y = y0; y < y1 && j + 4 <= seg.length; y += on + off) {
                    seg[j++] = cx; seg[j++] = y; seg[j++] = cx; seg[j++] = Math.min(y1, y + on);
                }
                c.drawLines(seg, 0, j, pl);
            }
        }
        if (crossPane < 0 || !paneOn[crossPane]) return;
        RectF r = pane[crossPane];
        // the HORIZONTAL in the hovered pane only
        if (crossY > r.top + TITLE_H && crossY < r.bottom) {
            pl.setColor(ink); pl.setStrokeWidth(1 * d);
            int per = (int) Math.ceil(plotR / (on + off)) + 1;
            float[] seg = new float[per * 4]; int j = 0;
            for (float x = 0; x < plotR && j + 4 <= seg.length; x += on + off) {
                seg[j++] = x; seg[j++] = crossY; seg[j++] = Math.min(plotR, x + on); seg[j++] = crossY;
            }
            c.drawLines(seg, 0, j, pl);
        }
        if (!crossBadges) return;
        // the RIGHT-AXIS value badge, in the hovered pane's own units
        double hgt = Math.max(1f, paneHgt[crossPane]);
        double v = paneHi[crossPane] - (crossY - paneTop[crossPane]) / hgt * (paneHi[crossPane] - paneLo[crossPane]);
        String vt;
        if (crossPane == PANE_PRICE) vt = String.format(Locale.US, "%." + s.dec + "f", v);
        else if (crossPane == PANE_IIMP) {
            boolean signed = "Buyer".equals(s.mode) || "Seller".equals(s.mode) || "Lines Buyer/Seller".equals(s.mode);
            vt = fmtMult(Math.pow(2, signed ? v : Math.abs(v)));
        } else if (crossPane == PANE_CINT || crossPane == PANE_CIMP) {
            // ⚠ both LINES panes are SIGNED log2 MULTIPLES, like the axis they draw. Without this they fell
            // through to the dollar branch below and the badge read "$1.2M" over a 1.2x line (user 2026-09-23).
            // Any pane added here has to declare its units or it silently inherits dollars.
            vt = fmtMult(Math.pow(2, v));
        } else if (crossPane == PANE_KEPT) {
            vt = String.format(Locale.US, "%+dt", Math.round(v));      // KEPT TICKS BY LEADER: ticks, signed
        } else vt = (v < 0 ? "-" : "") + usdShort(Math.abs(v));
        tagBadge(c, plotR, crossY, vt, 1.0f, 0.5f);
    }

    /** The crosshair's CLOCK badge, on the time strip at the very BOTTOM whichever pane the pen is over (user
     *  2026-09-24: "it should show at the very bottom, currently it shows at the price chart" -- it sat at the foot of
     *  the hovered pane). Painted after the time axis so the axis labels do not cover it. */
    private void drawCrossClock(Canvas c) {
        if (!crossOn || !crossBadges || Double.isNaN(crossX) || fullscreen >= 0 && !paneOn[fullscreen]) return;
        if (crossPane < 0 || !paneOn[crossPane]) return;
        float cx = xPx(crossX);
        if (cx < 0 || cx > plotR) return;
        int tz; synchronized (M.lock) { tz = M.tzOff; }
        java.util.Calendar cal = tz == Integer.MIN_VALUE ? java.util.Calendar.getInstance()
                : java.util.Calendar.getInstance(new java.util.SimpleTimeZone(tz * 1000, "PC"));
        cal.setTimeInMillis((long) (crossX * 1000));
        String xt = String.format(Locale.US, "%s %02d, %d - %02d:%02d",
                new String[]{"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"}[cal.get(java.util.Calendar.DAY_OF_WEEK) - 1],
                cal.get(java.util.Calendar.DAY_OF_MONTH), cal.get(java.util.Calendar.YEAR),
                cal.get(java.util.Calendar.HOUR_OF_DAY), cal.get(java.util.Calendar.MINUTE));
        tagBadge(c, cx, timeY + TAXIS_H * 0.5f, xt, 0.5f, 0.5f);
    }

    /** The terminal's TextItem badge: #141414 on #dcdcdc, anchored by (ax, ay) as pyqtgraph anchors are. */
    private void tagBadge(Canvas c, float x, float y, String txt, float ax, float ay) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10.5f * d); pt.setFakeBoldText(false);
        float w = pt.measureText(txt) + 8 * d, h = 16 * d;
        float rx = x - ax * w, ry = y - ay * h;
        rx = Math.max(0, Math.min(getWidth() - w, rx));
        ry = Math.max(0, Math.min(getHeight() - h, ry));
        pf.setColor(Color.parseColor("#dcdcdc"));
        c.drawRect(rx, ry, rx + w, ry + h, pf);
        pt.setColor(Color.parseColor("#141414"));
        c.drawText(txt, rx + 4 * d, ry + h - 4.5f * d, pt);
    }


    // ------------------------------------------------------------------------------------------------
    // LINES INTEREST / LINES IMPACT -- one line per side, teal buyers and red sellers, on the same log2
    // axis and 1x midline the I x I pane uses. LINES IMPACT also carries the DOMINANCE bands: a tinted
    // full-height block wherever one side stands at least `spread` above the other, BRIGHT where that
    // side won the gap by climbing rather than by the other falling away under it.
    // ------------------------------------------------------------------------------------------------
    private void drawLinesPane(Canvas c, Snap s, int p) {
        boolean imp = (p == PANE_CIMP);
        FlowModel.Lines L = imp ? s.cimp : s.cint;
        RectF r = pane[p];
        // LINES IMPACT: reach where the side led, its PUSH-BACK where it did not (2026-09-24) -- config.pane_titles
        title(c, r, (imp ? "LINES IMPACT  ·  each side's reach (led) or push-back vs its own last "
                         : "LINES INTEREST  ·  each side's aggressive $/s vs its own last ") + M.lb);
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        double clip = Math.log(8.0) / LN2;
        double lim, fitNow = 0;
        if (L.smooth != lastLineSmooth[p]) { lineTop[p] = 0; lastLineSmooth[p] = L.smooth; }   // new window in the DATA
        if (L.n > 0) {
            float[] fit = new float[2 * L.n];
            for (int i = 0; i < L.n; i++) {
                fit[i] = (float) Math.abs(Math.max(-clip, Math.min(clip, L.b[i])));
                fit[L.n + i] = (float) Math.abs(Math.max(-clip, Math.min(clip, L.s[i])));
            }
            java.util.Arrays.sort(fit);
            double p95 = fit[Math.min(fit.length - 1, (int) (0.95 * (fit.length - 1)))] * 1.15;
            fitNow = Math.min(Math.max(Math.max(p95, Math.log(1.37) / LN2 * 1.4), 1.0), clip * 1.15);
            if (lineTop[p] <= 0 && fit.length >= 2 * FIT_MIN_N) lineTop[p] = fitNow;   // LATCH, do not follow
        }
        lim = Math.max(1.0, lineTop[p] > 0 ? lineTop[p] : fitNow);
        double[] rg = yRange(p, -lim, lim);
        double yhi = rg[1], ylo = rg[0], range = Math.max(1e-9, yhi - ylo);
        notePane(p, top, hgt, ylo, yhi);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        // the bands go down FIRST, under the guides and the lines
        if (imp) drawDomBands(c, L, r.left, top, r.bottom);
        pl.setColor(cGuide); pl.setStrokeWidth(1 * d);
        pl.setPathEffect(new DashPathEffect(new float[]{5 * d, 5 * d}, 0));
        for (double g : new double[]{Math.log(0.76) / LN2, Math.log(1.37) / LN2}) {
            float y = (float) (top + (yhi - g) / range * hgt);
            c.drawLine(r.left, y, plotR, y, pl);
        }
        pl.setPathEffect(null);
        pl.setColor(cMid); c.drawLine(r.left, (float) (top + yhi / range * hgt), plotR, (float) (top + yhi / range * hgt), pl);
        // the two lines: one point per cycle at its MIDDLE, broken where a cycle could not be rated
        // (the engine sends -999 there) and lighter over the cycle still forming
        for (int side = 0; side < 2; side++) {
            float[] y = side == 0 ? L.b : L.s;
            // LINES IMPACT only: the single STEPS where this side moved 0.3x between TWO cycles. A climb
            // is thick in the side's own colour; a FALL is thick in GREY, because a side losing its impact
            // is not a signal FOR that side and teal or red would read as one (user 2026-09-23).
            byte[] mk = imp ? runMark(y, L.x0, L.x1, L.n, L.form, L.step) : null;
            pl.setColor(side == 0 ? TEAL : RED); pl.setStrokeWidth(1.8f * d);
            path.reset(); path2.reset(); path3.reset();
            boolean open = false;
            float lx = 0, ly = 0;
            int pi = -1;
            for (int i = 0; i < L.n && i < y.length; i++) {
                boolean formi = L.form != null && i < L.form.length && L.form[i] != 0;
                if (formi || y[i] < -900f) { open = false; continue; }
                // a GAP in time is a cycle nobody rated -- each cycle ends exactly where the next begins -- so the
                // line BREAKS there (the terminal's rule). Held rows from several messages sit side by side now.
                if (open && pi >= 0 && L.x0[i] - L.x1[pi] > 0.5) open = false;
                float px = xPx(0.5 * (L.x0[i] + L.x1[i]));
                float py = (float) (top + (yhi - Math.max(-clip, Math.min(clip, y[i]))) / range * hgt);
                if (!open) { path.moveTo(px, py); open = true; } else {
                    path.lineTo(px, py);
                    // the thick passes are SEGMENTS, not one polyline: a run can be any stretch of them
                    if (mk != null && mk[i] > 0) { path2.moveTo(lx, ly); path2.lineTo(px, py); }
                    else if (mk != null && mk[i] < 0) { path3.moveTo(lx, ly); path3.lineTo(px, py); }
                }
                lx = px; ly = py; pi = i;
            }
            c.drawPath(path, pl);
            if (mk != null) {
                pl.setStrokeWidth(4.2f * d);
                c.drawPath(path2, pl);                        // the climb, still this side's colour
                pl.setColor(LOSS_GREY); c.drawPath(path3, pl);   // the fall, grey
                pl.setStrokeWidth(1.8f * d);
            }
            // the forming stretch, lighter, from the last finished point out to its own
            int k = -1;
            for (int i = L.n - 1; i >= 0; i--) if (L.form != null && i < L.form.length && L.form[i] != 0) { k = i; break; }
            if (k > 0 && y.length > k && y[k] > -900f && y[k - 1] > -900f && L.x0[k] - L.x1[k - 1] <= 0.5) {
                int col = side == 0 ? TEAL : RED;
                pl.setColor(Color.argb(150, Color.red(col), Color.green(col), Color.blue(col)));
                float x1p = xPx(0.5 * (L.x0[k - 1] + L.x1[k - 1])), x2p = xPx(0.5 * (L.x0[k] + L.x1[k]));
                float y1p = (float) (top + (yhi - Math.max(-clip, Math.min(clip, y[k - 1]))) / range * hgt);
                float y2p = (float) (top + (yhi - Math.max(-clip, Math.min(clip, y[k]))) / range * hgt);
                c.drawLine(x1p, y1p, x2p, y2p, pl);
            }
        }
        c.restore();
        axisMult(c, r, top, hgt, ylo, yhi);
        // LINES IMPACT: each line's CURRENT value (its newest point, the forming one included -- what the readout
        // prints) as an end label on the axis, the KEPT TICKS pane's style (user 2026-09-25)
        if (imp && L.n > 0) {
            int k = L.n - 1;
            float[] ys = new float[2]; String[] txt = new String[2]; int[] cl = {TEAL, RED}; int nl = 0;
            for (int side = 0; side < 2; side++) {
                float[] y = side == 0 ? L.b : L.s;
                if (k >= y.length || y[k] < -900f) continue;
                ys[nl] = (float) (top + (yhi - Math.max(-clip, Math.min(clip, y[k]))) / range * hgt);
                txt[nl] = fmtMult(Math.pow(2, y[k])); cl[nl] = side == 0 ? TEAL : RED; nl++;
            }
            endLabels(c, top, r.bottom, java.util.Arrays.copyOf(ys, nl), java.util.Arrays.copyOf(txt, nl), java.util.Arrays.copyOf(cl, nl));
        }
        drawSmoothSlider(c, p, r, imp ? M.smCimp : M.smCint);
        // the bottom-right readout, the terminal's line
        if (L.n > 0) {
            int k = L.n - 1;
            String txt = String.format(java.util.Locale.US, "buyers %s  ·  sellers %s  ·  %d-cycle mean",
                    fmtMult(Math.pow(2, L.b[k])), fmtMult(Math.pow(2, L.s[k])), imp ? M.smCimp : M.smCint);
            pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setFakeBoldText(false);
            pt.setColor(L.b[k] >= L.s[k] ? TEAL : RED);
            c.drawText(txt, plotR - pt.measureText(txt) - 6 * d, r.bottom - 5 * d, pt);
        }
    }

    /** LINES IMPACT's dominance AREAS: one band per cycle where a side's impact stands LIMP_DOM_SPREAD above the
     * other's, BRIGHT where the leader climbed LIMP_DOM_GAIN into it. Drawn in the LINES IMPACT pane and on the
     * PRICE pane -- ONE routine, so the two can never disagree about where an area is or what colour. */
    private void drawDomBands(Canvas c, FlowModel.Lines L, float left, float top, float bottom) {
        if (L == null || L.dside == null || L.n <= 0) return;
        for (int i = 0; i < L.n && i < L.dside.length; i++) {
            int sd = L.dside[i];
            if (sd == 0) continue;
            domPaint(sd, domBright(L, i));
            float bx0 = xPx(L.x0[i]), bx1 = xPx(L.x1[i]);
            if (bx1 < left || bx0 > plotR) continue;
            c.drawRect(Math.max(left, bx0), top, Math.min(plotR, bx1), bottom, pf);
        }
    }

    /** The same areas on the PRICE chart (user 2026-09-23: "the area should cover only the cycles candles and should
     * go beyond their high/low"): ONE box per run of consecutive cycles in the same area -- same side, same
     * brightness, no gap in time -- as wide as those cycles and as tall as THEIR candles, lowest low to highest
     * high, plus DOM_BOX_PAD beyond each. A run with no candle on the chart draws nothing. */
    private void drawDomBoxes(Canvas c, Snap s, float left, float top, float hgt, double yl, double yh,
                              boolean forming, double fh, double fl) {
        FlowModel.Lines L = s.cimp;
        if (L == null || L.dside == null || L.n <= 0 || s.n == 0 || !(yh > yl)) return;
        int last = s.n - 1, m = Math.min(L.n, L.dside.length);
        float pad = DOM_BOX_PAD * d;
        int i = 0;
        while (i < m) {
            int sd = L.dside[i];
            if (sd == 0) { i++; continue; }
            boolean bright = domBright(L, i);
            double hi = -Double.MAX_VALUE, lo = Double.MAX_VALUE;
            int j = i;
            while (true) {
                // the candle of this cycle: the price pane keys cycles by their start, as LINES IMPACT does
                int k = nearest(s.cT, L.x0[j]);
                if (k >= 0 && Math.abs(s.cT[k] - L.x0[j]) < 1.0) {
                    boolean isForm = k == last && forming;
                    hi = Math.max(hi, isForm ? fh : s.cH[k]);
                    lo = Math.min(lo, isForm ? fl : s.cL[k]);
                }
                int nx = j + 1;
                if (nx >= m || L.dside[nx] != sd || domBright(L, nx) != bright || L.x0[nx] - L.x1[j] > 1.0) break;
                j = nx;
            }
            float bx0 = xPx(L.x0[i]), bx1 = xPx(L.x1[j]);
            if (hi >= lo && bx1 >= left && bx0 <= plotR) {
                domPaint(sd, bright);
                float y0 = (float) (top + (yh - hi) / (yh - yl) * hgt) - pad;
                float y1 = (float) (top + (yh - lo) / (yh - yl) * hgt) + pad;
                c.drawRect(Math.max(left, bx0), y0, Math.min(plotR, bx1), y1, pf);
            }
            i = j + 1;
        }
    }

    // THE BRIGHT AREA'S KEPT FILTER (user 2026-09-25: "for the purple the kept ticks red line should be above the green
    // line / for the lime green the kept ticks green line should be above the red line"): an area is BRIGHT only when,
    // besides its leader's climb, the KEPT TICKS lines agree -- the area's side strictly above the other at the area's
    // own cycle (the value the Kept pane draws across that candle; the forming cycle's would-close-now value).
    // Otherwise the area stays, in its ordinary shade. Rolling 3 whatever the Kept pane shows, as the Takeover reads it.
    private Object[] domKeptKey = null;
    private byte[] domKept = new byte[0];            // per LINES IMPACT row: +1 buyers' kept above, -1 sellers', 0 a tie
    private int domKeptForm = 0;                     // the same for the forming cycle's row, every frame

    private void domKeptBuild(Snap s) {
        FlowModel.Lines L = s.cimp;
        double[] lx0 = L != null ? L.x0 : null;
        int ln = lx0 == null ? 0 : Math.min(L.n, lx0.length);
        Object[] key = {s.cT, s.cDone, s.cC, s.cO, s.cLead, s.n, s.tick, lx0};
        if (domKeptKey == null || !java.util.Arrays.equals(domKeptKey, key)) {
            domKeptKey = key;
            byte[] out = new byte[ln];
            float[] rb = new float[TKO_KEPT_N], rs = new float[TKO_KEPT_N];
            int cnt = 0;
            for (int i = 0; i < s.n && ln > 0; i++) {
                if (s.cDone == null || i >= s.cDone.length || s.cDone[i] == 0) continue;
                float[] kv = keptOf(s, i, s.cC[i]);
                rb[cnt % TKO_KEPT_N] = kv[0]; rs[cnt % TKO_KEPT_N] = kv[1]; cnt++;
                float sb = 0, ss = 0;
                for (int q = 0; q < Math.min(cnt, TKO_KEPT_N); q++) { sb += rb[q]; ss += rs[q]; }
                int j = nearest(lx0, s.cT[i]);
                if (j < 0 || j >= ln || Math.abs(lx0[j] - s.cT[i]) > 1.0) continue;
                out[j] = (byte) (sb > ss ? 1 : (ss > sb ? -1 : 0));
            }
            domKept = out;
        }
        domKeptForm = 0;                              // the forming cycle: its kept if it closed now + the last closed ones
        int n = s.n, last = n - 1;
        if (n > 0 && s.cDone != null && last < s.cDone.length && s.cDone[last] == 0 && !Double.isNaN(s.livePx)) {
            float[] kv = keptOf(s, last, s.livePx);
            float sb = kv[0], ss = kv[1];
            int got = 1;
            for (int i = last - 1; i >= 0 && got < TKO_KEPT_N; i--) {
                if (s.cDone[i] == 0) continue;
                float[] q = keptOf(s, i, s.cC[i]); sb += q[0]; ss += q[1]; got++;
            }
            domKeptForm = sb > ss ? 1 : (ss > sb ? -1 : 0);
        }
        long nowMs = System.currentTimeMillis();         // what the filter keeps, last 6 h (logcat FLOW)
        if (nowMs - domLogAt > 10000 && L != null && L.dside != null) {
            domLogAt = nowMs;
            java.text.SimpleDateFormat hf = new java.text.SimpleDateFormat("HH:mm:ss", Locale.US);
            hf.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
            StringBuilder out = new StringBuilder(); int nClimb = 0, nBright = 0;
            for (int i = 0; i < ln && i < L.dside.length; i++) {
                if (L.dside[i] == 0 || L.x0[i] < nowMs / 1000.0 - 6 * 3600.0) continue;
                if (!(L.dgain != null && i < L.dgain.length && L.dgain[i] > -900f && L.dgain[i] >= (float) L.gain)) continue;
                nClimb++;
                if (!domBright(L, i)) continue;
                nBright++;
                out.append(L.dside[i] > 0 ? " B" : " S").append(hf.format(new java.util.Date((long) (L.x0[i] * 1000))));
            }
            android.util.Log.i("FLOW", "DOM: last 6 h, climbed areas " + nClimb + ", bright after the kept filter " + nBright + ":" + out);
        }
    }
    private long domLogAt = 0;

    private boolean domBright(FlowModel.Lines L, int i) {
        if (!(L.dgain != null && i < L.dgain.length && L.dgain[i] > -900f && L.dgain[i] >= (float) L.gain)) return false;
        int sd = L.dside != null && i < L.dside.length ? L.dside[i] : 0;
        boolean form = L.form != null && i < L.form.length && L.form[i] != 0;
        int kv = form ? domKeptForm : (i < domKept.length ? domKept[i] : 0);
        return sd != 0 && kv == sd;                   // lime: buyers' kept above; purple: sellers' kept above
    }

    /** The area's paint. The BRIGHT band is the user's own pair (#66FF00 / #BE03FD), not teal / red at more alpha:
     * a band the leader CLIMBED into differs in HUE as well as in weight. */
    private void domPaint(int sd, boolean bright) {
        int col = bright ? (sd > 0 ? DOM_HI_BUY : DOM_HI_SELL) : (sd > 0 ? TEAL : RED);
        pf.setColor(Color.argb(bright ? 95 : 38, Color.red(col), Color.green(col), Color.blue(col)));
    }

    /** LINES IMPACT: which SEGMENTS of one side's line are a move of `thr` or more, and which way.
     *
     * Returns mk[i] for "the segment ending at point i": +1 it CLIMBED `thr` or more from the cycle before
     * it, -1 it FELL that far, 0 neither. ONE SEGMENT AT A TIME -- the step from one rated cycle to the next.
     *
     * ⚠ This replaced a whole-run version (094ea83) at the user's word: "it should be the increase /
     * decrease just from 2 cycles so we will not color the whole increase/decrease". The run version marked
     * every segment of a monotone climb that ended 0.3x above where it began, so a slow drift over eight
     * cycles came out as one long thick stretch. This marks only the steps that THEMSELVES moved 0.3x.
     *
     * ⚠ THE SMOOTHING WINDOW DECIDES HOW OFTEN THIS FIRES. These lines are a trailing mean, and a longer
     * window flattens exactly the single-cycle jumps this looks for: at 1 there is no smoothing and the steps
     * are large; by 20 a 0.3x step is rare. If the marks go missing, the slider is why. */
    private static byte[] runMark(float[] y, double[] x0, double[] x1, int n, byte[] form, double thr) {
        byte[] mk = new byte[Math.max(0, n)];
        int prev = -1;
        for (int i = 0; i < n && i < y.length; i++) {
            if (form != null && i < form.length && form[i] != 0) continue;   // the forming point is its own stroke
            if (y[i] < -900f) { prev = -1; continue; }                        // a cycle the engine could not rate
            if (prev >= 0 && x0[i] - x1[prev] > 0.5) prev = -1;                 // ... or a gap: no step across it
            if (prev >= 0) {
                // ⚠ a DIFFERENCE OF MULTIPLES, like every "Nx" in this family -- the first cut subtracted the
                // log2 values the line is drawn with, which left 2.0x -> 2.3x unmarked and marked 0.20x -> 0.25x
                double step = Math.pow(2.0, y[i]) - Math.pow(2.0, y[prev]);
                // a hair of tolerance: 2.3 - 2.0 is 0.29999999999999982, and an exact 0.3x move must count
                double t = thr - 1e-9;
                mk[i] = step >= t ? (byte) 1 : (step <= -t ? (byte) -1 : (byte) 0);
            }
            prev = i;
        }
        return mk;
    }

    /** The right axis as a MULTIPLE (log2), the way every pane in this family labels it. */
    private void axisMult(Canvas c, RectF r, float top, float hgt, double ylo, double yhi) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(9 * d); pt.setColor(cTitle); pt.setFakeBoldText(false);
        pl.setColor(cSep); pl.setStrokeWidth(1 * d);
        c.drawLine(plotR, top, plotR, r.bottom, pl);
        double range = Math.max(1e-9, yhi - ylo);
        int steps = Math.max(2, Math.min(6, (int) (hgt / (28 * d))));
        for (int i = 0; i <= steps; i++) {
            double v = ylo + range * i / steps;
            float y = (float) (top + (yhi - v) / range * hgt);
            if (y < top || y > r.bottom) continue;
            c.drawLine(plotR, y, plotR + 4 * d, y, pl);
            c.drawText(fmtMult(Math.pow(2, v)), plotR + 7 * d, y + 3 * d, pt);
        }
    }

    /** One in-canvas smoothing slider per LINE pane: a track, a handle and the number. */
    private void drawSmoothSlider(Canvas c, int p, RectF r, int val) {
        drawSmoothSlider(c, p, r, val, plotR - 34 * d);
    }

    private void drawSmoothSlider(Canvas c, int p, RectF r, int val, float xRight) {
        drawSmoothSlider(c, p, r, val, xRight, Math.max(1, M.smoothMin), Math.max(Math.max(1, M.smoothMin) + 1, M.smoothMax));
    }

    private void drawSmoothSlider(Canvas c, int p, RectF r, int val, float xRight, int lo, int hi) {
        float w = 78 * d, h = 14 * d;
        float x1 = xRight, x0 = x1 - w;
        float y0 = r.top + 4 * d, y1 = y0 + h;
        slX0[p] = x0 - 8 * d; slX1[p] = x1 + 8 * d; slY0[p] = y0 - 6 * d; slY1[p] = y1 + 6 * d;
        float cy = (y0 + y1) / 2;
        pl.setColor(Color.parseColor("#3a4150")); pl.setStrokeWidth(3 * d);
        c.drawLine(x0, cy, x1, cy, pl);
        float fr = (Math.max(lo, Math.min(hi, val)) - lo) / (float) Math.max(1, hi - lo);
        pl.setColor(Color.parseColor("#7a828e")); c.drawLine(x0, cy, x0 + fr * w, cy, pl);
        pf.setColor(FG); c.drawCircle(x0 + fr * w, cy, 4.5f * d, pf);
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setColor(FG); pt.setFakeBoldText(true);
        c.drawText(String.valueOf(val), x1 + 6 * d, cy + 4 * d, pt);
        pt.setFakeBoldText(false);
    }

    // ------------------------------------------------------------------ KEPT TICKS BY LEADER (user 2026-09-25)
    // Per FINISHED cycle, the ticks its LEADER kept: move_ticks (close - open) for a buyer-led cycle, -move_ticks for
    // a seller-led one -- flipped so a side's number is POSITIVE when it wins, and a cycle that closes against its
    // leader subtracts from that leader. The leader is the engine's ("lead" in the cycle rows: the I x I pane's own
    // rule, the side whose aggressive $/s runs further above its last N); an unrated cycle gives both sides 0.
    // ROLLING = each point the sum over the last N closed cycles; CUMULATIVE = the running total since 00:00 UTC,
    // back to 0 at every midnight. STEPPED, each CLOSED cycle's value drawn ACROSS ITS OWN CANDLE (user 2026-09-25:
    // the line under a Takeover badge must be the value the rule read -- "the green line is below the red line but
    // still the signal fired": the steps used to start at each close, i.e. under the NEXT candle). A cycle draws
    // nothing until it has closed. The FORMING cycle ("but i still wanna see the live one forming") is a thin dotted
    // step across the forming candle to the value it would give if it closed now -- never in the totals.
    private static final int KEPT_BUY = Color.parseColor("#1D9E75"), KEPT_SELL = Color.parseColor("#E24B4A"),
            KEPT_NET = Color.parseColor("#8a8a8a");
    private static final int KEPT_N_MAX = 60, KEPT_N_DEFAULT = 3;   // default 3 (user 2026-09-25: "smoothing 3 by default")
    public boolean showKept = true;
    private boolean keptCum = false, keptNet = false;
    private int keptN = KEPT_N_DEFAULT;
    private final RectF kmRoll = new RectF(), kmCum = new RectF(), kmNet = new RectF();
    // the CLOSED series, rebuilt only when the cycles, the mode or N change (not every frame)
    private Object[] keptKey = null;
    private double[] kTs = new double[0], kTe = new double[0]; private float[] kVb = new float[0], kVs = new float[0];
    private int kM = 0;

    private static double keptDay(double t) { return Math.floor(t / 86400.0); }

    private void keptBuild(Snap s) {
        Object[] key = {s.cT, s.cDone, s.cC, s.cO, s.cLead, s.n, keptCum, keptN, s.tick};
        if (keptKey != null && java.util.Arrays.equals(keptKey, key)) return;
        keptKey = key;
        int n = s.n;
        double[] ts = new double[n], te = new double[n]; float[] kb = new float[n], ks = new float[n];
        int m = 0;
        for (int i = 0; i < n; i++) {
            boolean dn = s.cDone != null && i < s.cDone.length && s.cDone[i] != 0;
            if (!dn) continue;                                      // only FINISHED cycles count
            int ld = s.cLead != null && i < s.cLead.length ? s.cLead[i] : 0;
            double mv = (s.cC[i] - s.cO[i]) / Math.max(1e-12, s.tick);
            long t = Double.isNaN(mv) ? 0 : Math.round(mv);
            ts[m] = s.cT[i]; te[m] = s.cTe[i];
            kb[m] = ld > 0 ? t : 0;
            ks[m] = ld < 0 ? -t : 0;
            m++;
        }
        float[] vb = new float[m], vs = new float[m];
        if (keptCum) {
            for (int j = 0; j < m; j++) {
                boolean same = j > 0 && keptDay(te[j]) == keptDay(te[j - 1]);
                vb[j] = (same ? vb[j - 1] : 0) + kb[j];
                vs[j] = (same ? vs[j - 1] : 0) + ks[j];
            }
        } else {
            double[] pb = new double[m + 1], ps = new double[m + 1];
            for (int j = 0; j < m; j++) { pb[j + 1] = pb[j] + kb[j]; ps[j + 1] = ps[j] + ks[j]; }
            for (int j = 0; j < m; j++) {
                int a = Math.max(0, j + 1 - keptN);
                vb[j] = (float) (pb[j + 1] - pb[a]);
                vs[j] = (float) (ps[j + 1] - ps[a]);
            }
        }
        kTs = ts; kTe = te; kVb = vb; kVs = vs; kM = m;
        // what the forming tail adds to: kept per closed cycle, for the rolling window's last N - 1
        kKb = kb; kKs = ks;
    }
    private float[] kKb = new float[0], kKs = new float[0];

    /** {buyers, sellers} if the forming cycle closed NOW, or null when there is none. */
    private double[] keptForming(Snap s, double now) {
        int n = s.n;
        if (n == 0 || s.cDone == null || n - 1 >= s.cDone.length || s.cDone[n - 1] != 0 || Double.isNaN(s.livePx)) return null;
        int ld = s.cLead != null && n - 1 < s.cLead.length ? s.cLead[n - 1] : 0;
        long t = Math.round((s.livePx - s.cO[n - 1]) / Math.max(1e-12, s.tick));
        double fb = ld > 0 ? t : 0, fs = ld < 0 ? -t : 0;
        int m = kM;
        if (keptCum) {
            boolean same = m > 0 && keptDay(kTe[m - 1]) == keptDay(now);
            return new double[]{(same ? kVb[m - 1] : 0) + fb, (same ? kVs[m - 1] : 0) + fs};
        }
        double sb = 0, ss = 0;
        for (int j = Math.max(0, m - (keptN - 1)); j < m; j++) { sb += kKb[j]; ss += kKs[j]; }
        return new double[]{sb + fb, ss + fs};
    }

    private void drawKept(Canvas c, Snap s, double now) {
        RectF r = pane[PANE_KEPT];
        title(c, r, keptCum ? "KEPT TICKS BY LEADER  ·  cumulative since 00:00 UTC"
                            : "KEPT TICKS BY LEADER  ·  sum of the last " + keptN + " closed cycles");
        float top = r.top + TITLE_H, hgt = r.bottom - top;
        keptBuild(s);
        int m = kM;
        double[] ts = kTs, te = kTe; float[] vb = kVb, vs = kVs;
        double[] fm = keptForming(s, now);
        // the closed cycles whose candle is on screen, plus one either side so a step enters and leaves the pane
        int j0 = 0; while (j0 < m && te[j0] < vx0) j0++;
        j0 = Math.max(0, j0 - 1);
        int j1 = m - 1; while (j1 >= 0 && ts[j1] > vx1) j1--;
        j1 = Math.min(m - 1, j1 + 1);
        // the fit: what is on screen, the tail, and zero
        double lo = 0, hi = 0;
        for (int j = j0; j <= j1 && j >= 0; j++) {
            lo = Math.min(lo, Math.min(vb[j], vs[j])); hi = Math.max(hi, Math.max(vb[j], vs[j]));
            if (keptNet) { double nv = vb[j] - vs[j]; lo = Math.min(lo, nv); hi = Math.max(hi, nv); }
        }
        if (fm != null) {
            lo = Math.min(lo, Math.min(fm[0], fm[1])); hi = Math.max(hi, Math.max(fm[0], fm[1]));
            if (keptNet) { lo = Math.min(lo, fm[0] - fm[1]); hi = Math.max(hi, fm[0] - fm[1]); }
        }
        double pad = Math.max(2.0, 0.12 * (hi - lo));
        double[] rg = yRange(PANE_KEPT, lo - pad, hi + pad);
        double ylo = rg[0], yhi = rg[1], range = Math.max(1e-9, yhi - ylo);
        notePane(PANE_KEPT, top, hgt, ylo, yhi);
        c.save(); c.clipRect(r.left, top, r.right, r.bottom);
        float yZero = (float) (top + (yhi - 0) / range * hgt);
        pl.setPathEffect(null); pl.setColor(cMid); pl.setStrokeWidth(1 * d);
        c.drawLine(r.left, yZero, plotR, yZero, pl);
        // CUMULATIVE: a faint mark at each midnight, where it goes back to 0
        if (keptCum) {
            pl.setColor(cSep);
            for (double dd = Math.ceil(vx0 / 86400.0) * 86400.0; dd <= vx1; dd += 86400.0) c.drawLine(xPx(dd), top, xPx(dd), r.bottom, pl);
        }
        double xNow = Math.min(now, vx1 + 1);
        // net first (behind), then sellers, then buyers
        for (int k = 0; k < 3; k++) {
            int kind = k == 0 ? 2 : (k == 1 ? 1 : 0);             // 0 buyers, 1 sellers, 2 net
            if (kind == 2 && !keptNet) continue;
            int col = kind == 0 ? KEPT_BUY : (kind == 1 ? KEPT_SELL : KEPT_NET);
            path.reset();
            boolean started = false; float prevY = 0;
            float lastX = 0, lastY = 0; boolean any = false;
            for (int j = j0; j <= j1 && j >= 0; j++) {
                double v = kind == 0 ? vb[j] : (kind == 1 ? vs[j] : vb[j] - vs[j]);
                float x0 = xPx(ts[j]), x1 = xPx(te[j]), y = (float) (top + (yhi - v) / range * hgt);
                if (!started) { path.moveTo(x0, y); started = true; any = true; }
                else { path.lineTo(x0, prevY); path.lineTo(x0, y); }   // the step at the candle's START ...
                path.lineTo(x1, y);                                     // ... held across the candle to its close
                prevY = y; lastX = x1; lastY = y;
            }
            pl.setColor(col);
            if (kind == 0) { pl.setStrokeWidth(1.8f * d); pl.setPathEffect(null); }
            else if (kind == 1) { pl.setStrokeWidth(1.8f * d); pl.setPathEffect(new DashPathEffect(new float[]{6 * d, 4 * d}, 0)); }
            else { pl.setStrokeWidth(1.4f * d); pl.setPathEffect(new DashPathEffect(new float[]{1.5f * d, 3 * d}, 0)); }
            if (any) c.drawPath(path, pl);
            // the FORMING candle: a thin dotted step at its start to what closing now would give, held to now
            // (keptForming already starts a new UTC day at 0 in Cumulative)
            if (fm != null && any && j1 == m - 1) {
                double fv = kind == 0 ? fm[0] : (kind == 1 ? fm[1] : fm[0] - fm[1]);
                float xs = xPx(s.cT[s.n - 1]), fx = xPx(xNow), fy = (float) (top + (yhi - fv) / range * hgt);
                pl.setPathEffect(new DashPathEffect(new float[]{1.5f * d, 3 * d}, 0)); pl.setStrokeWidth(1.1f * d);
                if (xs > lastX + 0.5f) c.drawLine(lastX, lastY, xs, lastY, pl);   // a gap before the forming candle
                c.drawLine(xs, lastY, xs, fy, pl);
                c.drawLine(xs, fy, Math.max(xs, fx), fy, pl);
                pl.setPathEffect(null);
                pf.setColor(Color.argb(170, Color.red(col), Color.green(col), Color.blue(col)));
                c.drawCircle(Math.max(xs, fx), fy, 2.4f * d, pf);
            }
            pl.setPathEffect(null);
        }
        c.restore();
        keptAxis(c, r, top, hgt, ylo, yhi);
        // the END LABELS, at the right axis, nudged apart: each line's CURRENT value -- the end of the forming tail
        // while a cycle is forming, so they move with it live (user 2026-09-25: "the badges on the y axis of the kept
        // ticks are not changing with the live/forming lines"), the last close otherwise. The lines themselves still
        // step only at closes: the forming cycle is in the badges and the tail, never in the stepped totals.
        if (m > 0 || fm != null) {
            int nl = keptNet ? 3 : 2;
            double cb = fm != null ? fm[0] : vb[m - 1], cs = fm != null ? fm[1] : vs[m - 1];
            double[] val = {cb, cs, cb - cs};
            int[] cols = {KEPT_BUY, KEPT_SELL, KEPT_NET};
            float[] ys = new float[nl]; String[] txt = new String[nl]; int[] cl = new int[nl];
            for (int i = 0; i < nl; i++) {
                ys[i] = (float) (top + (yhi - val[i]) / range * hgt);
                txt[i] = String.format(Locale.US, "%+dt", Math.round(val[i])); cl[i] = cols[i];
            }
            endLabels(c, top, r.bottom, ys, txt, cl);
        }
        keptControls(c, r);
    }

    /** END LABELS at the right axis: one badge per line at its value's y, in the line's colour, white bold text, nudged
     *  apart so two never overlap and kept inside the pane. The KEPT TICKS pane's, shared with LINES IMPACT (user
     *  2026-09-25: "add the value of the current lines impacts line, use the same style ... kept ticks"). */
    private void endLabels(Canvas c, float top, float bottom, float[] ys, String[] txt, int[] cols) {
        int nl = ys.length;
        if (nl == 0) return;
        float[] y2 = ys.clone();
        Integer[] ord = new Integer[nl];
        for (int i = 0; i < nl; i++) ord[i] = i;
        java.util.Arrays.sort(ord, (a, b) -> Float.compare(y2[a], y2[b]));
        float gap = 15 * d, yMin = top + 7 * d, yMax = bottom - 7 * d;
        for (int q = 0; q < nl; q++) y2[ord[q]] = Math.max(yMin, Math.min(yMax, y2[ord[q]]));
        for (int q = 1; q < nl; q++) if (y2[ord[q]] - y2[ord[q - 1]] < gap) y2[ord[q]] = y2[ord[q - 1]] + gap;
        if (y2[ord[nl - 1]] > yMax) {                              // pushed out at the bottom: stack up from there
            y2[ord[nl - 1]] = yMax;
            for (int q = nl - 2; q >= 0; q--) if (y2[ord[q + 1]] - y2[ord[q]] < gap) y2[ord[q]] = y2[ord[q + 1]] - gap;
        }
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(10 * d); pt.setFakeBoldText(true);
        for (int i = 0; i < nl; i++) {
            float w = pt.measureText(txt[i]) + 8 * d, h = 14 * d, y = y2[i];
            pf.setColor(cols[i]);
            c.drawRect(plotR + 1 * d, y - h / 2, plotR + 1 * d + w, y + h / 2, pf);
            pt.setColor(Color.WHITE);
            c.drawText(txt[i], plotR + 5 * d, y + 3.5f * d, pt);
        }
        pt.setFakeBoldText(false);
    }

    /** The right axis in ticks, on a round step. */
    private void keptAxis(Canvas c, RectF r, float top, float hgt, double ylo, double yhi) {
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(9 * d); pt.setColor(cTitle); pt.setFakeBoldText(false);
        pl.setColor(cSep); pl.setStrokeWidth(1 * d); pl.setPathEffect(null);
        c.drawLine(plotR, top, plotR, r.bottom, pl);
        double range = Math.max(1e-9, yhi - ylo);
        int want = Math.max(2, Math.min(6, (int) (hgt / (26 * d))));
        double[] steps = {1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000};
        double st = steps[steps.length - 1];
        for (double q : steps) if (range / q <= want) { st = q; break; }
        for (double v = Math.ceil(ylo / st) * st; v <= yhi; v += st) {
            float y = (float) (top + (yhi - v) / range * hgt);
            if (y < top + 4 * d || y > r.bottom - 2 * d) continue;
            c.drawLine(plotR, y, plotR + 4 * d, y, pl);
            c.drawText(v == 0 ? "0" : String.format(Locale.US, "%+d", Math.round(v)), plotR + 7 * d, y + 3 * d, pt);
        }
    }

    /** The controls, top right of the pane: [Rolling | Cumulative], the N slider (Rolling only), Net. */
    private void keptControls(Canvas c, RectF r) {
        float h = 14 * d, y0 = r.top + 1.5f * d, y1 = y0 + h, cy = (y0 + y1) / 2;
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(9.5f * d); pt.setFakeBoldText(false);
        // Net, rightmost
        float x = plotR - 8 * d;
        float nw = pt.measureText("Net");
        float bx1 = x - nw - 5 * d, bx0 = bx1 - 10 * d;
        kmNet.set(bx0, y0, x, y1);
        pl.setColor(cTitle); pl.setStrokeWidth(1.2f * d); pl.setPathEffect(null);
        RectF box = new RectF(bx0, cy - 5 * d, bx0 + 10 * d, cy + 5 * d);
        if (keptNet) { pf.setColor(KEPT_NET); c.drawRect(box, pf); pl.setColor(Color.WHITE);
                       c.drawLine(box.left + 2 * d, cy, box.left + 4.3f * d, cy + 2.5f * d, pl);
                       c.drawLine(box.left + 4.3f * d, cy + 2.5f * d, box.right - 2 * d, cy - 3 * d, pl); }
        else c.drawRect(box, pl);
        pt.setColor(cTitle); c.drawText("Net", bx1 + 5 * d, cy + 3.5f * d, pt);
        x = bx0 - 14 * d;
        // the N slider (its number sits just right of the track)
        if (!keptCum) {
            float trackRight = x - 18 * d;
            drawSmoothSlider(c, PANE_KEPT, r, keptN, trackRight, 1, KEPT_N_MAX);
            x = trackRight - 78 * d - 14 * d;
        } else {
            slX0[PANE_KEPT] = slX1[PANE_KEPT] = 0;
        }
        // the mode toggle
        pt.setTypeface(Typeface.MONOSPACE); pt.setTextSize(9.5f * d); pt.setFakeBoldText(false);
        float wc = pt.measureText("Cumulative") + 12 * d, wr = pt.measureText("Rolling") + 12 * d;
        kmCum.set(x - wc, y0, x, y1);
        kmRoll.set(x - wc - wr, y0, x - wc, y1);
        for (int k = 0; k < 2; k++) {
            RectF b = k == 0 ? kmRoll : kmCum;
            boolean on = (k == 1) == keptCum;
            pf.setColor(on ? Color.parseColor("#2962ff") : (bw ? Color.parseColor("#eceff3") : Color.parseColor("#20242c")));
            c.drawRect(b, pf);
            pl.setColor(cSep); pl.setStrokeWidth(1 * d); c.drawRect(b, pl);
            pt.setColor(on ? Color.WHITE : cTitle);
            String lab = k == 0 ? "Rolling" : "Cumulative";
            c.drawText(lab, b.left + (b.width() - pt.measureText(lab)) / 2, cy + 3.5f * d, pt);
        }
    }

    /** A touch on the pane's toggle or Net, taken BEFORE the splitter: they sit in the top 16 dp of the pane, inside
     *  the boundary's 14 dp grab band, so a tap there resized the panes instead (measured on the tablet, 2026-09-25).
     *  Acts on the finger's release, if it is still on the same control; the hit areas are a finger wider. */
    private int keptDown = 0;
    private boolean keptTouch(MotionEvent ev) {
        int a = ev.getActionMasked();
        float x = ev.getX(), y = ev.getY(), in = 6 * d;
        int which = !paneOn[PANE_KEPT] ? 0 : hit(kmRoll, x, y, in) ? 1 : hit(kmCum, x, y, in) ? 2 : hit(kmNet, x, y, in) ? 3 : 0;
        if (a == MotionEvent.ACTION_DOWN) { keptDown = which; return which != 0; }
        if (keptDown == 0) return false;
        if (a == MotionEvent.ACTION_UP) {
            if (which == keptDown) {
                if (which == 1 && keptCum) { keptCum = false; keptSave(); }
                else if (which == 2 && !keptCum) { keptCum = true; keptSave(); }
                else if (which == 3) { keptNet = !keptNet; keptSave(); }
            }
            keptDown = 0;
        } else if (a == MotionEvent.ACTION_CANCEL) keptDown = 0;
        return true;
    }

    private static boolean hit(RectF b, float x, float y, float in) {
        return b.width() > 0 && x >= b.left - in && x <= b.right + in && y >= b.top - in && y <= b.bottom + in;
    }

    private void keptSave() {
        if (prefs != null) prefs.edit().putBoolean("kept_cum", keptCum).putBoolean("kept_net", keptNet).apply();
        keptKey = null;
        invalidate();
    }

    /** A touch on a pane's smoothing slider: set it, tell the engine, keep the gesture. */
    private boolean smoothTouch(MotionEvent ev) {
        int a = ev.getActionMasked();
        float x = ev.getX(), y = ev.getY();
        if (a == MotionEvent.ACTION_DOWN) {
            slDrag = -1;
            for (int p = 0; p < PANE_N; p++) {
                if (!paneOn[p] || slX1[p] <= slX0[p]) continue;
                if (p == PANE_IIMP && !"Lines Buyer/Seller".equals(M.iimpMode)) continue;
                if (p != PANE_IIMP && p != PANE_CINT && p != PANE_CIMP && p != PANE_KEPT) continue;
                if (x >= slX0[p] && x <= slX1[p] && y >= slY0[p] && y <= slY1[p]) { slDrag = p; break; }
            }
            if (slDrag < 0) return false;
        }
        if (slDrag < 0) return false;
        if (a == MotionEvent.ACTION_MOVE || a == MotionEvent.ACTION_DOWN) {
            float x0 = slX0[slDrag] + 8 * d, x1 = slX1[slDrag] - 8 * d;
            if (slDrag == PANE_KEPT) {                             // KEPT TICKS BY LEADER: 1-60 cycles, the tablet's own
                int v = 1 + Math.round((KEPT_N_MAX - 1) * Math.max(0f, Math.min(1f, (x - x0) / Math.max(1f, x1 - x0))));
                if (v != keptN) { keptN = v; if (prefs != null) prefs.edit().putInt("kept_n", v).apply(); invalidate(); }
                return true;
            }
            int lo = Math.max(1, M.smoothMin), hi = Math.max(lo + 1, M.smoothMax);
            int v = lo + Math.round((hi - lo) * Math.max(0f, Math.min(1f, (x - x0) / Math.max(1f, x1 - x0))));
            String k = slDrag == PANE_IIMP ? "iimp" : (slDrag == PANE_CINT ? "cint" : "cimp");
            int cur = slDrag == PANE_IIMP ? M.smIimp : (slDrag == PANE_CINT ? M.smCint : M.smCimp);
            if (v != cur) {
                synchronized (M.lock) {
                    if (slDrag == PANE_IIMP) M.smIimp = v; else if (slDrag == PANE_CINT) M.smCint = v; else M.smCimp = v;
                }
                if (host != null) host.onSmooth(k, v);
                invalidate();
            }
            return true;
        }
        if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) { slDrag = -1; return true; }
        return true;
    }

    /** A small grip on each boundary between two panes: where a drag resizes them. */
    private void drawGrips(Canvas c) {
        if (fullscreen >= 0) return;
        int prev = -1;
        for (int p = 0; p < PANE_N; p++) {
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
