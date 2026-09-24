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
    // the card's I x I strip wears the I x I PANE's own colours (flow_interp.IIMP_BAR): buy / sell / contra, + text tints
    private static final int[] IIMP_BAR = {Color.parseColor("#26a69a"), Color.parseColor("#ef5350"), Color.parseColor("#ff9f43")};
    private static final int[] IIMP_TXT_DARK = {Color.parseColor("#4dd0c1"), Color.parseColor("#ff7b78"), Color.parseColor("#ffb366")};
    private static final int[] IIMP_TXT_LIGHT = {Color.parseColor("#00796b"), Color.parseColor("#c62828"), Color.parseColor("#c96a00")};
    private static final double WALL_HIGH = 1.06, WALL_LOW = 0.94;     // config.IIMP_WALL_HIGH / _LOW
    private static final double KEEP_MIN = 4.0;                         // config.IIMP_KEEP_MIN_TICKS: a SHORT push below
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
        CARD_H = 262 * d; CARD_GAP = 7 * d; SEP_H = 24 * d;       // 114 + the I x I strip + the AUCTION strip (2026-09-24)
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
        drawStrip(c, r, x, y, cw, dim, det);
        drawAuction(c, r, x, y, cw, dim, det);
    }

    /** Greedy word wrap into at most `maxLines`, the last one cut with an ellipsis if text remains. */
    private List<String> wrap(String s, Typeface tf, float size, float w, int maxLines) {
        pText.setTypeface(tf); pText.setTextSize(size * d);
        String[] words = s.trim().split("\\s+");
        List<String> out = new ArrayList<>(); StringBuilder cur = new StringBuilder();
        for (int i = 0; i < words.length; i++) {
            String t = cur.length() == 0 ? words[i] : cur + " " + words[i];
            if (pText.measureText(t) <= w) { cur.setLength(0); cur.append(t); continue; }
            if (cur.length() > 0) out.add(cur.toString());
            cur.setLength(0); cur.append(words[i]);
            if (out.size() == maxLines - 1) {
                for (int j = i + 1; j < words.length; j++) cur.append(' ').append(words[j]);
                break;
            }
        }
        if (cur.length() > 0) out.add(cur.toString());
        while (out.size() > maxLines) out.remove(out.size() - 1);
        if (!out.isEmpty()) {
            String last = out.get(out.size() - 1);
            if (pText.measureText(last) > w) {
                while (last.length() > 1 && pText.measureText(last + "…") > w) last = last.substring(0, last.length() - 1);
                out.set(out.size() - 1, last.trim() + "…");
            }
        }
        return out;
    }

    private static String fx(double v) {
        if (!ok(v)) return "–";
        return v >= 10 ? String.format(Locale.US, "%.0f×", v) : String.format(Locale.US, "%.2f×", v);
    }

    /** THE I x I READING of the cycle under the book row (user 2026-09-24): the pane's own bar in MINIATURE (side
     * colour, orange when price went the other way; outline = it reached, solid share from the 1x line = what was
     * kept; the wall dot past its tip), four tiles -- interest, impact, wall, kept -- and the why in two lines at most.
     * The terminal's flow_interp._draw_iimp_strip, in dp. */
    private void drawStrip(Canvas c, FlowModel.Row r, float x, float y, float cw, int dim, int det) {
        float sy = y + 113 * d;
        pLine.setStyle(Paint.Style.STROKE); pLine.setPathEffect(null); pLine.setStrokeWidth(1 * d);
        pLine.setColor(col(dark ? "#262d34" : "#e6e9ec"));
        c.drawLine(x + 14 * d, sy, x + cw - 14 * d, sy, pLine);
        if (r.iLead == 0) {
            text(c, "I×I  not rated yet -- it needs a few cycles of history and the book at the open", x + 16 * d, sy + 30 * d, sans, 10.5f, dim);
            return;
        }
        boolean buy = r.iLead > 0;
        int key = r.iContra ? 2 : (buy ? 0 : 1);
        int bc = IIMP_BAR[key], tc = (dark ? IIMP_TXT_DARK : IIMP_TXT_LIGHT)[key];
        int ink = col(dark ? "#e6ebf0" : "#1a1a1a");
        // ---- the bar in miniature, around its own 1x line
        float mx = x + 17 * d, mid = sy + 28 * d, half = 19 * d, bw = 12 * d;
        pLine.setColor(col(dark ? "#4a545c" : "#b4bcc3")); pLine.setStrokeWidth(1 * d);
        c.drawLine(mx - 5 * d, mid, mx + bw + 4 * d, mid, pLine);
        float hb = 3 * d;
        if (ok(r.iMult) && r.iMult > 0)
            hb = (float) Math.max(3 * d, Math.min(half - 4 * d, (half - 4 * d) * Math.abs(Math.log(r.iMult) / Math.log(2)) / 1.4));
        float top = buy ? mid - hb : mid;
        if (r.iGood) {
            double f = (!r.iContra && ok(r.iKept)) ? Math.max(0.0, Math.min(1.0, r.iKept)) : 1.0;
            if (f > 0) {
                float sh = (float) (hb * f);
                pFill.setColor(alpha(bc, 205));
                if (buy) c.drawRect(mx, mid - sh, mx + bw, mid, pFill); else c.drawRect(mx, mid, mx + bw, mid + sh, pFill);
            }
            pLine.setStrokeWidth((f >= 1.0 ? 1.0f : 1.4f) * d);
        } else {
            pLine.setStrokeWidth(1.4f * d);
        }
        pLine.setColor(bc); c.drawRect(mx, top, mx + bw, top + hb, pLine);
        if (ok(r.iWall) && (r.iWall >= WALL_HIGH || r.iWall <= WALL_LOW)) {
            float wy = buy ? top - 5 * d : top + hb + 5 * d;
            int wc = col(dark ? "#dcdcdc" : "#8a939b");
            if (r.iWall >= WALL_HIGH) { pFill.setColor(wc); c.drawCircle(mx + bw / 2, wy, 2.8f * d, pFill); }
            pLine.setColor(wc); pLine.setStrokeWidth(1.2f * d); c.drawCircle(mx + bw / 2, wy, 2.8f * d, pLine);
        }
        // ---- four tiles
        float tx0 = x + 46 * d, tw = (x + cw - 14 * d - tx0) / 4f;
        String wtag = ok(r.iWall) ? (r.iWall >= WALL_HIGH ? "wall" : (r.iWall <= WALL_LOW ? "open" : "")) : "";
        // IMPACT: a SHORT push quotes no multiple ("no push" at 0 ticks, "short push" otherwise). KEPT: a PERCENT only
        // for a real push that closed on the leader's side, else the SIGNED TICKS the leader held (2026-09-24) --
        // the terminal's _iimp_impact_txt / _iimp_kept_txt
        String impTxt = r.iShort ? ((ok(r.iReach) && r.iReach >= 1) ? "short push" : "no push") : fx(r.iImp);
        boolean pct = ok(r.iKept) && r.iKept >= 0 && ok(r.iReach) && r.iReach >= KEEP_MIN;
        String keptTxt; int keptCol;
        if (pct) { keptTxt = Math.round(100 * r.iKept) + "%"; keptCol = ink; }
        else if (ok(r.iLmv)) {
            long nt = Math.round(r.iLmv);
            keptTxt = nt == 0 ? "0t" : (nt > 0 ? "+" + nt + "t" : "\u2212" + (-nt) + "t");
            keptCol = nt > 0 ? ink : dim;
        } else { keptTxt = "–"; keptCol = dim; }
        String[] labs = {"INTEREST", "IMPACT", "WALL", "KEPT"};
        String[] vals = {(buy ? "BUY " : "SELL ") + fx(r.iMult), impTxt, fx(r.iWall), keptTxt};
        int[] cols = {tc, (r.iGood && !r.iShort) ? ink : dim, ink, keptCol};
        for (int i = 0; i < 4; i++) {
            float lx = tx0 + i * tw;
            pText.setLetterSpacing(0.09f);
            text(c, labs[i], lx, sy + 17 * d, bold, 9f, dim);
            pText.setLetterSpacing(0f);
            text(c, vals[i], lx, sy + 35 * d, bold, 13f, cols[i]);
            if (i == 2 && !wtag.isEmpty()) text(c, wtag, lx + width(vals[i], bold, 13f) + 4 * d, sy + 35 * d, sans, 9.5f, dim);
            if (i == 3 && pct) {                                 // the bar only measures a percent
                float kw = Math.max(12 * d, tw - 18 * d);
                pFill.setColor(col(dark ? "#262d34" : "#eceef0"));
                rf.set(lx, sy + 40 * d, lx + kw, sy + 43.5f * d); c.drawRoundRect(rf, 1.7f * d, 1.7f * d, pFill);
                if (ok(r.iKept) && r.iKept > 0) {
                    pFill.setColor(bc);
                    rf.set(lx, sy + 40 * d, lx + kw * (float) Math.min(1.0, r.iKept), sy + 43.5f * d); c.drawRoundRect(rf, 1.7f * d, 1.7f * d, pFill);
                }
            }
        }
        // ---- the why, two lines at most
        if (r.iWhy != null && !r.iWhy.isEmpty()) {
            List<String> ln = wrap(r.iWhy, sans, 10.5f, cw - 32 * d, 2);
            for (int j = 0; j < ln.size(); j++) text(c, ln.get(j), x + 16 * d, sy + 59 * d + j * 14.5f * d, sans, 10.5f, det);
        }
    }

    // the card's short zone names (app/auction.py ZONES, same order) and each zone's dot height on a mini ladder
    private static final String[] AU_ZONE = {"below VA", "lower VA", "at POC", "upper VA", "above VA"};
    private static final float[] AU_ZONE_Y = {0.90f, 0.63f, 0.50f, 0.37f, 0.10f};

    private String elide(String s, Typeface tf, float size, float maxW) {
        if (width(s, tf, size) <= maxW) return s;
        String t = s;
        while (t.length() > 1 && width(t + "…", tf, size) > maxW) t = t.substring(0, t.length() - 1);
        return t + "…";
    }

    private void miniLadder(Canvas c, float lx, float ay, int zone, int dotCol) {
        float top = ay + 10 * d, hgt = 36 * d, w = 8 * d;
        pFill.setColor(col(dark ? "#262d34" : "#eceef0"));
        rf.set(lx, top, lx + w, top + hgt); c.drawRoundRect(rf, 2 * d, 2 * d, pFill);
        pFill.setColor(col(dark ? "#3a444d" : "#cfd5da"));
        c.drawRect(lx, top + hgt * 0.25f, lx + w, top + hgt * 0.75f, pFill);
        pLine.setStyle(Paint.Style.STROKE); pLine.setPathEffect(null); pLine.setStrokeWidth(1 * d);
        pLine.setColor(col(dark ? "#9aa3aa" : "#6f7a82"));
        c.drawLine(lx - 1 * d, top + hgt * 0.5f, lx + w + 1 * d, top + hgt * 0.5f, pLine);
        if (zone >= 0 && zone < 5) { pFill.setColor(dotCol); c.drawCircle(lx + w / 2, top + hgt * AU_ZONE_Y[zone], 3.2f * d, pFill); }
    }

    /** LAYER 1 OF THE AUCTION READING, under the I x I strip (user 2026-09-24): where the cycle traded against TODAY's
     * value and the MULTI-DAY value, and what each side did there -- responsive (where it is expected: sellers above
     * the POC, buyers below) or initiative (where it should lose interest), effective or absorbed, or quiet. Two mini
     * value ladders (shaded middle = value area, line = POC, dot = the cycle), four tiles, the cycle in one phrase.
     * The terminal's flow_interp._draw_auction_strip, in dp. */
    private void drawAuction(Canvas c, FlowModel.Row r, float x, float y, float cw, int dim, int det) {
        float ay = y + 192 * d;
        pLine.setStyle(Paint.Style.STROKE); pLine.setPathEffect(null); pLine.setStrokeWidth(1 * d);
        pLine.setColor(col(dark ? "#262d34" : "#e6e9ec"));
        c.drawLine(x + 14 * d, ay, x + cw - 14 * d, ay, pLine);
        int ink = col(dark ? "#e6ebf0" : "#1a1a1a");
        if (!r.hasAuction) { text(c, "AUCTION  needs the HLH Volume Profile on (its day klines)", x + 16 * d, ay + 30 * d, sans, 10.5f, dim); return; }
        if (r.aVerdict == null || r.aVerdict.isEmpty()) { text(c, "AUCTION  not rated yet -- the I×I reading comes first", x + 16 * d, ay + 30 * d, sans, 10.5f, dim); return; }
        int bcol = (dark ? IIMP_TXT_DARK : IIMP_TXT_LIGHT)[0], scol = (dark ? IIMP_TXT_DARK : IIMP_TXT_LIGHT)[1];
        // the dot takes the colour of the side that was EFFECTIVE there (initiative first), else the ink
        int dc = ink;
        String[] kinds = {"initiative effective", "responsive effective", "at value effective"};
        for (String k : kinds) {
            boolean b = k.equals(r.aBuy), s = k.equals(r.aSell);
            if (b && !s) { dc = bcol; break; }
            if (s && !b) { dc = scol; break; }
        }
        miniLadder(c, x + 15 * d, ay, r.aZone, dc);
        miniLadder(c, x + 28 * d, ay, r.aMzone, dc);
        float tx0 = x + 46 * d, tw = (x + cw - 14 * d - tx0) / 4f;
        String[] labs = {"TODAY", "MULTI-DAY", "BUYERS", "SELLERS"};
        String[] vals = new String[4], subs = new String[4];
        int[] cols = new int[4], subCols = new int[4];
        int[] zs = {r.aZone, r.aMzone}; double[] ds = {r.aDist, r.aMdist};
        for (int i = 0; i < 2; i++) {
            if (zs[i] >= 0 && zs[i] < 5) {
                vals[i] = AU_ZONE[zs[i]];
                long n = ok(ds[i]) ? Math.round(ds[i]) : Long.MIN_VALUE;
                subs[i] = n == Long.MIN_VALUE ? "" : (n == 0 ? "POC" : (n > 0 ? "+" + n + "t from POC" : "−" + (-n) + "t from POC"));
            } else { vals[i] = "–"; subs[i] = ""; }
            cols[i] = ink; subCols[i] = dim;
        }
        String[] lbls = {r.aBuy, r.aSell}; int[] sc = {bcol, scol};
        for (int i = 0; i < 2; i++) {
            String l = lbls[i] == null ? "" : lbls[i];
            if (l.isEmpty() || l.equals("unrated")) { vals[2 + i] = "–"; subs[2 + i] = ""; cols[2 + i] = dim; subCols[2 + i] = dim; continue; }
            if (l.equals("quiet")) { vals[2 + i] = "quiet"; subs[2 + i] = "under its normal"; cols[2 + i] = dim; subCols[2 + i] = dim; continue; }
            int sp = l.lastIndexOf(' ');
            String what = sp > 0 ? l.substring(0, sp) : l, how = sp > 0 ? l.substring(sp + 1) : "";
            boolean eff = how.equals("effective");
            vals[2 + i] = what; subs[2 + i] = how; cols[2 + i] = eff ? sc[i] : ink; subCols[2 + i] = eff ? sc[i] : dim;
        }
        for (int i = 0; i < 4; i++) {
            float lx = tx0 + i * tw;
            pText.setLetterSpacing(0.09f);
            text(c, labs[i], lx, ay + 17 * d, bold, 9f, dim);
            pText.setLetterSpacing(0f);
            text(c, elide(vals[i], bold, 13f, tw - 4 * d), lx, ay + 35 * d, bold, 13f, cols[i]);
            if (!subs[i].isEmpty()) text(c, elide(subs[i], sans, 9.5f, tw - 4 * d), lx, ay + 48 * d, sans, 9.5f, subCols[i]);
        }
        // the cycle in one phrase: in the effective side's colour when value is being re-priced
        int vc = det;
        if (r.aVerdict.contains("initiative, effective")) vc = r.aVerdict.startsWith("Buyers ") ? bcol : (r.aVerdict.startsWith("Sellers ") ? scol : ink);
        else if (r.aVerdict.contains("Quiet") || r.aVerdict.contains("Rotation")) vc = dim;
        text(c, elide(r.aVerdict, sans, 10.5f, cw - 32 * d), x + 16 * d, ay + 63 * d, sans, 10.5f, vc);
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
