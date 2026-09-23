package com.smc.flowtab;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;
import android.widget.OverScroller;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * The INTERPRETATION feed: one CARD per cycle, newest first, with an hour divider where the clock's hour changes --
 * the terminal's flow_interp card painter (_draw_card) drawn with the same geometry, in dp. Redesigned 2026-09-23
 * (user: "completely redesign it ... beautifully designed so that it facilitates reading"): the state as a chip,
 * the price move as the headline, the tape as two bars around each side's own normal, the book as arrows, and the
 * effort x result QUADRANT the state is read from. Light = Chart Style Simple BW: white cards on a white page.
 */
public final class InterpView extends View {
    private final FlowModel model;
    private List<FlowModel.Row> rows = new ArrayList<>();
    private float[] ys = new float[0];                // each card's top, from the first card's top
    private float total = 0f;
    private float scroll = 0f;
    private double topT0 = Double.NaN;                // the newest cycle, to keep a reader's place as cards arrive
    private final GestureDetector gest;
    private final OverScroller fling;
    private final Paint pFill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pLine = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pText = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path card = new Path();
    private final RectF rf = new RectF();
    private final float d, PAD, TOPY, CARD_H, CARD_GAP, SEP_H;
    private final DashPathEffect dashed;
    private final Typeface sans = Typeface.DEFAULT, bold = Typeface.DEFAULT_BOLD, mono = Typeface.MONOSPACE;
    // 7 / 8 = a breakout against its leader (the I x I orange bar): bright green up, bright purple down (flow_interp)
    private static final String[] BAR_COL = {"#FF9500", "#00C853", "#FF1F1F", "#E2574C", "#6B7A82", "#4E5C64", "#2979FF", "#76FF03", "#D500F9"};
    private static final String[] TXT_DARK = {"#FFB84D", "#2BE86B", "#FF5A5A", "#F0857C", "#9AAAB2", "#6B7A82", "#7FB2FF", "#9CFF57", "#E57BFF"};
    private static final String[] MOVE_DARK = {"#FF5A5A", "#7A828C", "#2BE86B"};
    private static final String[] TXT_LIGHT = {"#A85C00", "#00822F", "#C40D0D", "#A8382F", "#5A666D", "#6B7A82", "#0B4FA8", "#3F7F00", "#8E00B0"};
    private static final String[] MOVE_LIGHT = {"#C40D0D", "#77808A", "#00822F"};
    private static final int ST_ABSORB = 0, ST_BREAK = 1, ST_VACUUM = 2, ST_QUIET = 3;
    private static final int C_ABSORB_BUY = 0, C_BREAK_BUY = 1, C_VACUUM = 3, C_QUIET = 4;
    private static final int BUY_BAR = Color.parseColor("#26A69A"), SELL_BAR = Color.parseColor("#EF5350");
    public boolean dark = true;

    public void setDark(boolean dark) { this.dark = dark; setBackgroundColor(Color.parseColor(dark ? "#141414" : "#ffffff")); invalidate(); }
    public String title = "INTERPRETATION  ·  one card per cycle";
    public interface Listener { void onRowTap(FlowModel.Row r); }
    private Listener listener;
    public void setListener(Listener l) { listener = l; }
    public double selT0 = Double.NaN;                 // the marked card: the cycle tapped here or on the chart

    public InterpView(Context ctx, FlowModel model) {
        super(ctx);
        this.model = model;
        d = getResources().getDisplayMetrics().density;
        PAD = 10 * d; TOPY = PAD + 40 * d;             // the first card clears the hamburger button (46 dp)
        CARD_H = 114 * d; CARD_GAP = 7 * d; SEP_H = 24 * d;
        dashed = new DashPathEffect(new float[]{4 * d, 3 * d}, 0);
        setBackgroundColor(Color.parseColor("#141414"));
        fling = new OverScroller(ctx);
        gest = new GestureDetector(ctx, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent e) { fling.forceFinished(true); return true; }
            @Override public boolean onScroll(MotionEvent e1, MotionEvent e2, float dx, float dy) {
                scroll = clampScroll(scroll + dy); invalidate(); return true;
            }
            @Override public boolean onFling(MotionEvent e1, MotionEvent e2, float vx, float vy) {
                fling.fling(0, (int) scroll, 0, (int) -vy, 0, 0, 0, (int) maxScroll());
                postInvalidateOnAnimation(); return true;
            }
            @Override public boolean onDoubleTap(MotionEvent e) { fling.forceFinished(true); scroll = 0f; invalidate(); return true; }
            @Override public boolean onSingleTapUp(MotionEvent e) {
                int i = rowAt(e.getY());
                if (e.getY() > 21 * d && i >= 0) {
                    FlowModel.Row r = rows.get(i);
                    selT0 = r.t0;
                    if (listener != null) listener.onRowTap(r);
                    invalidate();
                }
                return true;
            }
        });
    }

    // ------------------------------------------------------------------ layout
    private static String hour(FlowModel.Row r) { return r.head != null && r.head.length() >= 2 ? r.head.substring(0, 2) : ""; }

    private void layout(List<FlowModel.Row> rs) {
        float[] out = new float[rs.size()]; float y = 0f;
        for (int i = 0; i < rs.size(); i++) {
            if (i > 0 && !hour(rs.get(i)).equals(hour(rs.get(i - 1)))) y += SEP_H;
            out[i] = y; y += CARD_H + CARD_GAP;
        }
        ys = out; total = y;
    }

    private float maxScroll() { return Math.max(0f, TOPY + total + PAD - getHeight()); }
    private float clampScroll(float s) { return Math.max(0f, Math.min(s, maxScroll())); }

    private int rowAt(float y) {
        float yy = y - (TOPY - scroll);
        int lo = 0, hi = ys.length;                   // the last card whose top is at or above yy
        while (lo < hi) { int m = (lo + hi) >>> 1; if (ys[m] <= yy) lo = m + 1; else hi = m; }
        int i = lo - 1;
        return (i >= 0 && i < rows.size() && yy < ys[i] + CARD_H) ? i : -1;
    }

    /** Bring the card of the cycle starting nearest t0 into view and mark it (its candle was tapped). */
    public void select(double t0) {
        selT0 = t0;
        List<FlowModel.Row> rs = rows; int best = -1; double bd = 0;
        for (int i = 0; i < rs.size(); i++) {
            double dd = Math.abs(rs.get(i).t0 - t0);
            if (best < 0 || dd < bd) { best = i; bd = dd; }
        }
        if (best >= 0 && best < ys.length) {
            selT0 = rs.get(best).t0;
            fling.forceFinished(true);
            scroll = clampScroll(TOPY + ys[best] - Math.max(0f, (getHeight() - CARD_H) / 2f));
        }
        invalidate();
    }

    public void refresh() {
        List<FlowModel.Row> rs;
        synchronized (model.lock) { rs = model.rows; }
        // keep the reader's place: if they have scrolled into history, move by however far the cards they were
        // reading were pushed down by the new ones on top
        int added = 0;
        if (scroll > 0 && !Double.isNaN(topT0)) {
            for (FlowModel.Row r : rs) { if (r.t0 <= topT0 + 1e-6) break; added++; }
        }
        rows = rs;
        layout(rs);
        if (added > 0 && added < ys.length) scroll += ys[added];
        scroll = clampScroll(scroll);
        topT0 = rs.isEmpty() ? Double.NaN : rs.get(0).t0;
        invalidate();
    }

    @Override public boolean onTouchEvent(MotionEvent ev) {
        gest.onTouchEvent(ev);
        return true;
    }

    @Override public void computeScroll() {
        if (fling.computeScrollOffset()) { scroll = clampScroll(fling.getCurrY()); postInvalidateOnAnimation(); }
    }

    // ------------------------------------------------------------------ paint
    private static int alpha(int c, int a) { return (c & 0x00ffffff) | (a << 24); }
    private static int col(String hex) { return Color.parseColor(hex); }
    private static boolean ok(double v) { return !Double.isNaN(v) && !Double.isInfinite(v); }

    private void text(Canvas c, String s, float x, float y, Typeface tf, float size, int color) {
        pText.setTypeface(tf); pText.setTextSize(size * d); pText.setColor(color);
        c.drawText(s, x, y, pText);
    }

    private float width(String s, Typeface tf, float size) {
        pText.setTypeface(tf); pText.setTextSize(size * d);
        return pText.measureText(s);
    }

    @Override protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        int dim = col(dark ? "#6f7a82" : "#7a7a7a"), det = col(dark ? "#9aa8b0" : "#303030");
        text(c, title, PAD, 15 * d, bold, 11, col(dark ? "#7d8492" : "#303030"));
        pFill.setStyle(Paint.Style.FILL);
        pFill.setColor(col(dark ? "#2a3138" : "#dddddd"));
        c.drawRect(PAD, 20 * d, w - PAD, 21 * d, pFill);
        List<FlowModel.Row> rs = rows;
        if (ys.length != rs.size()) layout(rs);
        c.save();
        c.clipRect(0, 21 * d, w, h);
        float base = TOPY - scroll, x = PAD, cw = w - 2 * PAD;
        for (int i = 0; i < rs.size(); i++) {
            float y = base + ys[i];
            if (y > h) break;
            if (i > 0 && !hour(rs.get(i)).equals(hour(rs.get(i - 1))) && y - SEP_H < h && y > 0) drawSep(c, rs.get(i), x, y - SEP_H, cw, dim);
            if (y + CARD_H < 20 * d) continue;
            drawCard(c, rs.get(i), x, y, cw, dim, det);
        }
        c.restore();
        if (rs.isEmpty()) text(c, model.connected ? "waiting for the first cycles" : "connecting to the engine...", PAD, 44 * d, sans, 11, dim);
        boolean live = !rs.isEmpty() && rs.get(0).forming && TOPY - scroll + CARD_H > 0;
        if (live) postInvalidateDelayed(1000);        // the live dot breathes with the second
    }

    /** The hour divider: this card's hour, then a hairline. */
    private void drawSep(Canvas c, FlowModel.Row r, float x, float y, float cw, int dim) {
        String lab = hour(r) + ":00";
        text(c, lab, x + 2 * d, y + 16 * d, mono, 10, dim);
        pLine.setStyle(Paint.Style.STROKE); pLine.setPathEffect(null); pLine.setStrokeWidth(1 * d);
        pLine.setColor(col(dark ? "#2a3138" : "#e3e3e3"));
        float lx = x + 10 * d + width(lab, mono, 10);
        c.drawLine(lx, y + 12 * d, x + cw, y + 12 * d, pLine);
    }

    private String chipText(FlowModel.Row r) {
        String side = r.side != null ? r.side : "";
        if (side.isEmpty() && r.name != null) {
            String[] p = r.name.trim().split("\\s+");
            String last = p.length > 0 ? p[p.length - 1] : "";
            if (last.equals("buy") || last.equals("sell")) side = last;
        }
        if ("-".equals(r.name)) return "Warming up";
        if (r.st == ST_BREAK) return "Breakout · " + side;
        if (r.st == ST_ABSORB) return "buy".equals(side) ? "Buyer absorbed" : "Seller absorbed";
        if (r.st == ST_VACUUM) return "Vacuum · " + side;
        if (r.st == ST_QUIET) return "Quiet";
        return "Forming";
    }

    private void drawCard(Canvas c, FlowModel.Row r, float x, float y, float cw, int dim, int det) {
        int ci = Math.max(0, Math.min(BAR_COL.length - 1, r.col));
        int scol = col(BAR_COL[ci]);
        int tcol = col((dark ? TXT_DARK : TXT_LIGHT)[ci]);
        String[] mvp = dark ? MOVE_DARK : MOVE_LIGHT;
        boolean sel = !Double.isNaN(selT0) && Math.abs(r.t0 - selT0) < 1.0;
        boolean warm = "-".equals(r.name);

        // ---- the card itself: white on the white page in Simple BW, held apart by a hairline
        rf.set(x, y, x + cw, y + CARD_H);
        card.reset(); card.addRoundRect(rf, 8 * d, 8 * d, Path.Direction.CW);
        pFill.setStyle(Paint.Style.FILL);
        pFill.setColor(col(dark ? "#1a1f25" : "#ffffff"));
        c.drawPath(card, pFill);
        pLine.setStyle(Paint.Style.STROKE);
        if (sel) {
            pLine.setPathEffect(null); pLine.setStrokeWidth(1.6f * d); pLine.setColor(col(dark ? "#7FB2FF" : "#0B4FA8"));
        } else {
            pLine.setPathEffect(r.forming ? dashed : null); pLine.setStrokeWidth(1 * d); pLine.setColor(col(dark ? "#262d34" : "#d9dde1"));
        }
        c.drawPath(card, pLine);
        pLine.setPathEffect(null);
        // the state rail, clipped to the rounded card: solid and 4 dp for a confident reading, faded and narrower
        // for a weak one, dashed while the cycle is still forming
        c.save(); c.clipPath(card);
        pFill.setColor(r.strong ? scol : alpha(scol, 110));
        float rw = (r.strong ? 4 : 3) * d;
        if (r.forming) {
            for (float yy = y + 2 * d; yy < y + CARD_H - 2 * d; yy += 10 * d) c.drawRect(x, yy, x + rw, yy + 6 * d, pFill);
        } else {
            c.drawRect(x, y, x + rw, y + CARD_H, pFill);
        }
        c.restore();

        float L = x + 14 * d;
        float q = 46 * d, qx = x + cw - 12 * d - q, qy = y + 32 * d;
        float R = qx - 10 * d;                        // the left column's right edge

        // ---- line 1: the state chip, then the clock and the duration; live / weak at the right
        String chip = chipText(r);
        float chw = width(chip, bold, 10.5f) + 16 * d;
        rf.set(L, y + 8 * d, L + chw, y + 28 * d);
        boolean filled = r.st == ST_BREAK || r.st == ST_ABSORB;
        if (filled) {
            pFill.setColor(alpha(scol, dark ? 58 : 44)); c.drawRoundRect(rf, 10 * d, 10 * d, pFill);
        } else {
            pLine.setStrokeWidth(1 * d); pLine.setColor(alpha(scol, 170)); c.drawRoundRect(rf, 10 * d, 10 * d, pLine);
        }
        pText.setTypeface(bold); pText.setTextSize(10.5f * d);
        Paint.FontMetrics fm = pText.getFontMetrics();
        text(c, chip, L + 8 * d, rf.centerY() - (fm.ascent + fm.descent) / 2f, bold, 10.5f, tcol);
        String[] parts = r.head != null ? r.head.split(" - ") : new String[0];
        String clock = parts.length > 0 ? parts[0] : "", dur = parts.length > 1 ? parts[parts.length - 1] : "";
        text(c, dur.isEmpty() ? clock : clock + " · " + dur, rf.right + 8 * d, y + 22 * d, mono, 10.5f, dim);
        if (r.forming) {
            int lc = col(mvp[0]);
            if ((System.currentTimeMillis() / 1000L) % 2 == 0) { pFill.setColor(lc); c.drawCircle(x + cw - 42 * d, y + 18 * d, 3.4f * d, pFill); }
            text(c, "live", x + cw - 35 * d, y + 22 * d, sans, 10.5f, lc);
        } else if (!r.strong && !warm) {
            text(c, "weak", x + cw - 12 * d - width("weak", sans, 10.5f), y + 22 * d, sans, 10.5f, dim);
        }

        // ---- the headline: the move in ticks -- or, for an absorbed cycle, how much of its push was given back
        c.save(); c.clipRect(x, y, R, y + CARD_H);
        float hb = y + 54 * d;
        if (r.st == ST_ABSORB && ok(r.gb) && ok(r.push)) {
            String big = Math.round(r.gb * 100) + "%";
            text(c, big, L, hb, bold, 20, tcol);
            float bx = L + width(big, bold, 20) + 8 * d;
            boolean buy = "buy".equals(r.side);
            double ext = buy ? r.hi : r.lo;
            String t = "of a " + Math.round(r.push) + "t push";
            String more = (ok(ext) && ok(r.px1)) ? String.format(Locale.US, "  %s %.2f → %.2f", buy ? "hi" : "lo", ext, r.px1) : "";
            if (!more.isEmpty() && bx + width(t + more, mono, 10.5f) <= R) t += more;   // the prices only when they fit
            text(c, t, bx, hb - 1 * d, mono, 10.5f, det);
            // the give-back bar: the whole track is the push, the filled part (from its tip) what was handed back
            float tw = Math.max(10 * d, R - L), ty = y + 61 * d;
            pFill.setColor(alpha(scol, 60)); rf.set(L, ty, L + tw, ty + 5 * d); c.drawRoundRect(rf, 2.5f * d, 2.5f * d, pFill);
            float fw = tw * (float) Math.max(0.0, Math.min(1.0, r.gb));
            pFill.setColor(scol); rf.set(L + tw - fw, ty, L + tw, ty + 5 * d); c.drawRoundRect(rf, 2.5f * d, 2.5f * d, pFill);
        } else if (warm) {
            text(c, "—", L, hb, bold, 20, dim);
            text(c, "not enough history yet", L + 30 * d, hb - 1 * d, mono, 10.5f, det);
        } else {
            String big;
            if (ok(r.mv)) { long n = Math.round(r.mv); big = n > 0 ? "+" + n + "t" : (n < 0 ? "−" + (-n) + "t" : "0t"); }
            else big = "—";
            text(c, big, L, hb, bold, 20, col(mvp[Math.max(0, Math.min(2, r.mvSign + 1))]));
            float bx = L + width(big, bold, 20) + 8 * d;
            if (ok(r.px0) && ok(r.px1)) text(c, String.format(Locale.US, "%.2f → %.2f", r.px0, r.px1), bx, hb - 1 * d, mono, 10.5f, det);
            else if (r.mvTxt != null && !r.mvTxt.isEmpty()) text(c, r.mvTxt, bx, hb - 1 * d, mono, 10.5f, det);
        }
        c.restore();

        // ---- tape: each side's aggressive $/s against ITS OWN normal, as a bar either side of 1x
        float tb = y + 83 * d;
        text(c, "tape", L, tb, sans, 10.5f, dim);
        ratioBar(c, L + 36 * d, tb - 7 * d, r.buy, BUY_BAR, dim, det);
        ratioBar(c, L + 36 * d + 44 * d + 46 * d, tb - 7 * d, r.sell, SELL_BAR, dim, det);

        // ---- book: the resting orders on each side, as a change against their own recent level
        float kb = y + 104 * d;
        text(c, "book", L, kb, sans, 10.5f, dim);
        float nx = pct(c, L + 36 * d, kb, "buyers", r.bid, mvp, dim, det);
        pct(c, Math.max(nx, L + 36 * d + 44 * d + 46 * d), kb, "sellers", r.ask, mvp, dim, det);

        // ---- the quadrant: the two numbers the state is READ from -- flow (effort, left -> right) against speed
        // (result, bottom -> top). Breakout top-right, absorbed bottom-right, vacuum top-left, quiet bottom-left.
        int[][] cells = {{0, 0, C_VACUUM}, {1, 0, C_BREAK_BUY}, {0, 1, C_QUIET}, {1, 1, C_ABSORB_BUY}};
        for (int[] cl : cells) {
            pFill.setColor(alpha(col(BAR_COL[cl[2]]), dark ? 46 : 38));
            c.drawRect(qx + cl[0] * q / 2, qy + cl[1] * q / 2, qx + (cl[0] + 1) * q / 2, qy + (cl[1] + 1) * q / 2, pFill);
        }
        pLine.setStrokeWidth(1 * d); pLine.setColor(col(dark ? "#4a545c" : "#c3c9ce"));
        c.drawLine(qx + q / 2, qy, qx + q / 2, qy + q, pLine);
        c.drawLine(qx, qy + q / 2, qx + q, qy + q / 2, pLine);
        if (ok(r.vr) && r.vr > 0 && ok(r.sr) && r.sr > 0) {
            float half = q / 2 - 4 * d;
            float ex = (float) (Math.max(-1.5, Math.min(1.5, Math.log(r.vr) / Math.log(2))) / 1.5) * half;
            float ey = (float) (Math.max(-1.5, Math.min(1.5, Math.log(r.sr) / Math.log(2))) / 1.5) * half;
            if (r.flat) ey = Math.min(ey, -2 * d);    // a flat move is "small" however fast its few ticks were
            float dx = qx + q / 2 + ex, dy = qy + q / 2 - ey;
            if (r.strong) { pFill.setColor(scol); c.drawCircle(dx, dy, 4.2f * d, pFill); }
            else { pLine.setStrokeWidth(1.6f * d); pLine.setColor(scol); c.drawCircle(dx, dy, 3.8f * d, pLine); }
        }
        String ft = ok(r.vr) ? String.format(Locale.US, "flow %.2f×", r.vr) : "flow –";
        text(c, ft, qx + q / 2 - width(ft, sans, 9.5f) / 2, qy + q + 13 * d, sans, 9.5f, dim);
        if (r.mvWord != null && !r.mvWord.isEmpty() && r.st != ST_ABSORB)
            text(c, r.mvWord, qx + q / 2 - width(r.mvWord, sans, 9.5f) / 2, qy + q + 25 * d, sans, 9.5f, det);
    }

    private void ratioBar(Canvas c, float bx, float by, double v, int fill, int dim, int det) {
        float bw = 40 * d, bh = 6 * d, r3 = 3 * d;
        pFill.setColor(col(dark ? "#262d34" : "#eceef0"));
        rf.set(bx, by, bx + bw, by + bh); c.drawRoundRect(rf, r3, r3, pFill);
        if (ok(v) && v > 0) {
            float ext = (float) (Math.max(-1.5, Math.min(1.5, Math.log(v) / Math.log(2))) / 1.5) * (bw / 2);
            pFill.setColor(fill);
            if (ext >= 0) rf.set(bx + bw / 2, by, bx + bw / 2 + ext, by + bh);
            else rf.set(bx + bw / 2 + ext, by, bx + bw / 2, by + bh);
            c.drawRoundRect(rf, r3, r3, pFill);
        }
        pLine.setStrokeWidth(1 * d); pLine.setColor(col(dark ? "#6f7a82" : "#9aa3aa"));
        c.drawLine(bx + bw / 2, by - 2 * d, bx + bw / 2, by + bh + 2 * d, pLine);
        text(c, ok(v) ? String.format(Locale.US, "%.2f×", v) : "–", bx + bw + 4 * d, by + bh + 0.5f * d, mono, 10.5f, det);
    }

    private float pct(Canvas c, float px, float py, String label, double v, String[] mvp, int dim, int det) {
        text(c, label, px, py, sans, 10.5f, det);
        px += width(label, sans, 10.5f) + 4 * d;
        if (!ok(v)) { text(c, "–", px, py, sans, 10.5f, dim); return px + 14 * d; }
        long n = Math.round((v - 1.0) * 100.0);
        String s; int cc;
        if (n == 0) { s = "0%"; cc = dim; }
        else { s = n > 0 ? "▲" + n + "%" : "▼" + (-n) + "%"; cc = col(n > 0 ? mvp[2] : mvp[0]); }
        text(c, s, px, py, mono, 10.5f, cc);
        return px + width(s, mono, 10.5f) + 14 * d;
    }
}
