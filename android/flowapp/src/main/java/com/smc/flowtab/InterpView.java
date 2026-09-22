package com.smc.flowtab;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;

import java.util.ArrayList;
import java.util.List;

/**
 * The INTERPRETATION feed: one row per cycle, newest first -- the terminal's flow_interp panel row for row (its
 * _paint_event, dark palette), scrolled by touch. The strings are the engine's; nothing is re-worded here.
 */
public final class InterpView extends View {
    private final FlowModel model;
    private List<FlowModel.Row> rows = new ArrayList<>();
    private float scroll = 0f;
    private final GestureDetector gest;
    private final Paint pFill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pText = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float ROW_H, PAD, GAP;
    private final float fHead, fName, fMove, fDet, fTitle;
    private static final String[] BAR_COL = {"#FF9500", "#00C853", "#FF1F1F", "#E2574C", "#6B7A82", "#4E5C64", "#2979FF"};
    private static final String[] TXT_DARK = {"#FFB84D", "#2BE86B", "#FF5A5A", "#F0857C", "#9AAAB2", "#6B7A82", "#7FB2FF"};
    private static final String[] MOVE_DARK = {"#FF5A5A", "#7A828C", "#2BE86B"};
    private static final int ST_FORMING = 4;
    public String title = "INTERPRETATION  ·  one row per cycle";

    public InterpView(Context ctx, FlowModel model) {
        super(ctx);
        this.model = model;
        float d = getResources().getDisplayMetrics().density;
        ROW_H = 78 * d; PAD = 10 * d; GAP = 16 * d;
        fHead = 11 * d; fName = 14 * d; fMove = 11 * d; fDet = 11 * d; fTitle = 11 * d;
        setBackgroundColor(Color.parseColor("#141414"));
        gest = new GestureDetector(ctx, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent e) { return true; }
            @Override public boolean onScroll(MotionEvent e1, MotionEvent e2, float dx, float dy) {
                scroll = Math.max(0f, Math.min(scroll + dy, Math.max(0f, rows.size() * ROW_H - getHeight() + 40 * d)));
                invalidate(); return true;
            }
            @Override public boolean onDoubleTap(MotionEvent e) { scroll = 0f; invalidate(); return true; }
        });
    }

    public void refresh() {
        synchronized (model.lock) { rows = model.rows; }
        invalidate();
    }

    @Override public boolean onTouchEvent(MotionEvent ev) {
        gest.onTouchEvent(ev);
        return true;
    }

    @Override protected void onDraw(Canvas c) {
        float d = getResources().getDisplayMetrics().density;
        int w = getWidth(), h = getHeight();
        int dim = Color.parseColor("#6f7a82"), det = Color.parseColor("#8FA0A8");
        pText.setTypeface(Typeface.DEFAULT_BOLD); pText.setTextSize(fTitle); pText.setColor(Color.parseColor("#7d8492"));
        c.drawText(title, PAD, 15 * d, pText);
        pFill.setColor(Color.parseColor("#2a3138"));
        c.drawRect(PAD, 20 * d, w - PAD, 21 * d, pFill);
        c.save();
        c.clipRect(0, 21 * d, w, h);
        float yTop = PAD + 40 * d - scroll;          // the first row clears the hamburger button (46 dp)
        List<FlowModel.Row> rs = rows;
        int first = Math.max(0, (int) ((scroll - PAD - 40 * d) / ROW_H));
        int last = Math.min(rs.size(), first + (int) (h / ROW_H) + 2);
        float x = PAD;
        for (int i = first; i < last; i++) {
            FlowModel.Row r = rs.get(i);
            float y = yTop + i * ROW_H;
            if (y > h || y + ROW_H < 20 * d) continue;
            int col = Math.max(0, Math.min(BAR_COL.length - 1, r.col));
            int bar = Color.parseColor(BAR_COL[col]);
            if (!r.strong) bar = (bar & 0x00ffffff) | (105 << 24);
            pFill.setColor(bar);
            if (r.forming) {
                float bw = (r.strong ? 4 : 2) * d; float yy = y + 3 * d;
                while (yy < y + ROW_H - 12 * d) { c.drawRect(x, yy, x + bw, yy + 5 * d, pFill); yy += 9 * d; }
            } else {
                c.drawRect(x, y + 3 * d, x + (r.strong ? 4 : 2) * d, y + ROW_H - 9 * d, pFill);
            }
            pText.setTypeface(Typeface.DEFAULT); pText.setTextSize(fHead); pText.setColor(dim);
            c.drawText(r.head, x + 12 * d, y + 14 * d, pText);
            String tag = r.forming ? "forming" : ((r.strong || r.st == ST_FORMING || "-".equals(r.name)) ? "" : "weak");
            if (!tag.isEmpty()) {
                pText.setColor(r.forming ? Color.parseColor(TXT_DARK[col]) : dim);
                c.drawText(tag, w - PAD - pText.measureText(tag), y + 14 * d, pText);
            }
            int tc = Color.parseColor(TXT_DARK[col]);
            if (!r.strong) tc = (tc & 0x00ffffff) | (165 << 24);
            pText.setTypeface(Typeface.DEFAULT_BOLD); pText.setTextSize(fName); pText.setColor(tc);
            c.drawText(r.name, x + 12 * d, y + 30 * d, pText);
            float nameW = pText.measureText(r.name);
            int mvc = Color.parseColor(MOVE_DARK[Math.max(0, Math.min(2, r.mvSign + 1))]);
            if (r.mvWord != null && !r.mvWord.isEmpty()) {
                pText.setTypeface(Typeface.DEFAULT_BOLD); pText.setTextSize(fMove); pText.setColor(mvc);
                c.drawText(r.mvWord, x + 12 * d + nameW + GAP, y + 30 * d, pText);
            }
            if (r.mvTxt != null && !r.mvTxt.isEmpty()) {
                pText.setTypeface(Typeface.DEFAULT_BOLD); pText.setTextSize(fMove); pText.setColor(mvc);
                c.drawText(r.mvTxt, x + 12 * d, y + 46 * d, pText);
            }
            pText.setTypeface(Typeface.DEFAULT); pText.setTextSize(fDet);
            if (r.d1 != null && !r.d1.isEmpty()) { pText.setColor(det); c.drawText(r.d1, x + 12 * d, y + 60 * d, pText); }
            if (r.d2a != null && !r.d2a.isEmpty()) {
                pText.setColor(dim); c.drawText(r.d2a, x + 12 * d, y + 72 * d, pText);
                if (r.d2b != null && !r.d2b.isEmpty()) c.drawText(r.d2b, x + 12 * d + pText.measureText(r.d2a) + GAP, y + 72 * d, pText);
            }
            pFill.setColor(Color.parseColor("#20262b"));
            c.drawRect(x, y + ROW_H - 5 * d, w - PAD, y + ROW_H - 4 * d, pFill);
        }
        c.restore();
        if (rs.isEmpty()) {
            pText.setTypeface(Typeface.DEFAULT); pText.setTextSize(fDet); pText.setColor(dim);
            c.drawText(model.connected ? "waiting for the first cycles" : "connecting to the engine...", PAD, 44 * d, pText);
        }
    }
}
