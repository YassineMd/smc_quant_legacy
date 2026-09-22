package com.smc.flowtab;

import android.content.SharedPreferences;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.MotionEvent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * What the user puts ON the PRICE pane: the drawing toolbar (Trendline with its always-positive % label, Select /
 * Edit, Delete All) and the Market Position pair (BUY / SELL -> a maker-limit bracket 1 tick off the live price,
 * SL 0.5% off the entry, TP +0.6%, break-even by the round-trip fee, paper-simulated on the live price). The rules
 * and the badges are the terminal's (drawing_tools.py, paper_account.py); the shapes persist in the app's prefs.
 */
public final class PriceTools {
    public interface Map {
        float xPx(double t); float yPx(double p); double xVal(float px); double yVal(float py);
        RectF pane(); float plotRight(); double tick(); int dec(); double now(); double live();
        float viewRight(); int tzOff(); boolean bw();
    }
    public interface Events { void toast(String msg); void changed(); }

    // ---- the paper account (paper_account.py)
    static final double START_BALANCE = 200000.0, RISK_FRAC = 0.10, LEVERAGE = 10.0, FEE_RATE = 0.0002;
    double balance = START_BALANCE;

    static final class Pos { double entry; int side; double margin, notional, qty, entryFee; }

    static final class Bracket {
        String kind; double entry, stop, target, x0; String state = "PENDING"; Pos pos; double lastPx = Double.NaN, entryTs = Double.NaN;
        int side() { return "long".equals(kind) ? 1 : -1; }
    }

    static final class Trend { double x0, y0, x1, y1; }

    private final Map m; private final Events ev; private final SharedPreferences prefs; private final float d;
    public boolean showBar = true, showMarket = true;
    public String tool = null;                       // null | "trend" | "select"
    final List<Trend> trends = new ArrayList<>();
    final List<Bracket> brackets = new ArrayList<>();
    final List<String[]> ledger = new ArrayList<>();       // [text, colour] newest first -- the Paper LIVE panel
    private int selected = -1;
    // in-progress touch
    private int mode = 0;                            // 0 none, 1 drawing a trend, 2 dragging a trend end, 3 moving a trend, 4 dragging a bracket line
    private Trend live; private int dragEnd; private Bracket dragBk; private int dragLine; private float lastX, lastY; private boolean moved;
    private final RectF[] barBtn = {new RectF(), new RectF(), new RectF()};
    private final RectF buyBtn = new RectF(), sellBtn = new RectF();
    private final List<float[]> closeBtns = new ArrayList<>();    // [x, y, bracket index]
    private final Paint pl = new Paint(Paint.ANTI_ALIAS_FLAG), pf = new Paint(Paint.ANTI_ALIAS_FLAG), pt = new Paint(Paint.ANTI_ALIAS_FLAG);
    private static final int C_TREND = Color.WHITE, C_ENTRY = Color.parseColor("#2962ff"), C_SL = Color.parseColor("#e74c3c"), C_TP = Color.parseColor("#1abc9c");

    public PriceTools(float density, SharedPreferences prefs, Map map, Events events) {
        d = density; this.prefs = prefs; m = map; ev = events;
        pl.setStyle(Paint.Style.STROKE); pf.setStyle(Paint.Style.FILL); pt.setTypeface(Typeface.MONOSPACE);
        load();
    }

    // ------------------------------------------------------------------ persistence
    private void load() {
        try {
            JSONObject o = new JSONObject(prefs.getString("tools", "{}"));
            balance = o.optDouble("balance", START_BALANCE);
            JSONArray ts = o.optJSONArray("trends");
            if (ts != null) for (int i = 0; i < ts.length(); i++) {
                JSONArray a = ts.getJSONArray(i); Trend t = new Trend();
                t.x0 = a.getDouble(0); t.y0 = a.getDouble(1); t.x1 = a.getDouble(2); t.y1 = a.getDouble(3); trends.add(t);
            }
            JSONArray lg = o.optJSONArray("ledger");
            if (lg != null) for (int i = 0; i < lg.length(); i++) { JSONArray a = lg.getJSONArray(i); ledger.add(new String[]{a.getString(0), a.getString(1)}); }
            JSONArray bs = o.optJSONArray("brackets");
            if (bs != null) for (int i = 0; i < bs.length(); i++) {
                JSONObject b = bs.getJSONObject(i); Bracket k = new Bracket();
                k.kind = b.getString("kind"); k.entry = b.getDouble("entry"); k.stop = b.getDouble("stop"); k.target = b.getDouble("target");
                k.x0 = b.optDouble("x0", 0); k.state = b.optString("state", "PENDING"); k.lastPx = b.optDouble("lastPx", Double.NaN); k.entryTs = b.optDouble("entryTs", Double.NaN);
                JSONObject p = b.optJSONObject("pos");
                if (p != null) { k.pos = new Pos(); k.pos.entry = p.getDouble("entry"); k.pos.side = p.getInt("side"); k.pos.margin = p.getDouble("margin"); k.pos.notional = p.getDouble("notional"); k.pos.qty = p.getDouble("qty"); k.pos.entryFee = p.getDouble("entryFee"); }
                brackets.add(k);
            }
        } catch (Exception ignored) { }
    }

    private void save() {
        try {
            JSONObject o = new JSONObject(); o.put("balance", balance);
            JSONArray ts = new JSONArray();
            for (Trend t : trends) { JSONArray a = new JSONArray(); a.put(t.x0); a.put(t.y0); a.put(t.x1); a.put(t.y1); ts.put(a); }
            o.put("trends", ts);
            JSONArray bs = new JSONArray();
            for (Bracket k : brackets) {
                JSONObject b = new JSONObject(); b.put("kind", k.kind); b.put("entry", k.entry); b.put("stop", k.stop); b.put("target", k.target); b.put("x0", k.x0); b.put("state", k.state);
                if (!Double.isNaN(k.lastPx)) b.put("lastPx", k.lastPx);
                if (!Double.isNaN(k.entryTs)) b.put("entryTs", k.entryTs);
                if (k.pos != null) { JSONObject p = new JSONObject(); p.put("entry", k.pos.entry); p.put("side", k.pos.side); p.put("margin", k.pos.margin); p.put("notional", k.pos.notional); p.put("qty", k.pos.qty); p.put("entryFee", k.pos.entryFee); b.put("pos", p); }
                bs.put(b);
            }
            o.put("brackets", bs);
            JSONArray lg = new JSONArray();
            for (String[] e : ledger) { JSONArray a = new JSONArray(); a.put(e[0]); a.put(e[1]); lg.put(a); }
            o.put("ledger", lg);
            prefs.edit().putString("tools", o.toString()).apply();
        } catch (Exception ignored) { }
        ev.changed();
    }

    // ------------------------------------------------------------------ the paper account
    private Pos open(double entry, int side) {
        Pos p = new Pos(); p.entry = entry; p.side = side;
        p.margin = Math.max(0, balance) * RISK_FRAC; p.notional = p.margin * LEVERAGE; p.qty = entry > 0 ? p.notional / entry : 0; p.entryFee = p.notional * FEE_RATE;
        return p;
    }

    private double[] livePnl(Pos p, double px) {
        double gross = p.qty * (px - p.entry) * p.side, exitFee = p.qty * px * FEE_RATE;
        double net = gross - p.entryFee - exitFee;
        return new double[]{net, p.margin > 0 ? net / p.margin * 100 : 0};
    }

    private static double breakeven(double entry, int side) {
        double f = FEE_RATE; return side > 0 ? entry * (1 + f) / (1 - f) : entry * (1 - f) / (1 + f);
    }

    private double snap(double v) { double tk = m.tick() > 0 ? m.tick() : 0.01; return Math.round(Math.round(v / tk) * tk * Math.pow(10, m.dec())) / Math.pow(10, m.dec()); }

    /** The BUY / SELL button: a maker-limit entry 1 tick off the live price, SL 0.5% off it, TP +0.6% (place_market). */
    public void placeMarket(String kind, double live) {
        if (!(live > 0)) { ev.toast("no live price yet"); return; }
        Bracket k = new Bracket(); k.kind = kind;
        double tk = m.tick();
        k.entry = snap("long".equals(kind) ? live - tk : live + tk);
        k.stop = snap("long".equals(kind) ? k.entry * (1 - 0.005) : k.entry * (1 + 0.005));
        k.target = snap("long".equals(kind) ? k.entry * 1.006 : k.entry * 0.994);
        k.x0 = m.now();
        brackets.add(k); save();
    }

    /** The live price drives every bracket's state machine (PositionBracket.on_price): fills AT the entry, closes AT
     * SL / TP, SL checked first. */
    public void onPrice(double px, double ts) {
        if (!(px > 0)) return;
        boolean changed = false;
        for (int i = brackets.size() - 1; i >= 0; i--) {
            Bracket k = brackets.get(i); int side = k.side();
            if ("PENDING".equals(k.state)) {
                if (!Double.isNaN(k.lastPx) && (k.lastPx - k.entry) * (px - k.entry) <= 0) {
                    k.pos = open(k.entry, side); k.state = "ACTIVE"; k.entryTs = ts; changed = true;
                    ev.toast(String.format(Locale.US, "%s filled at %." + m.dec() + "f", k.kind.toUpperCase(Locale.US), k.entry));
                }
                k.lastPx = px; continue;
            }
            k.lastPx = px;
            boolean slHit = side > 0 ? px <= k.stop : px >= k.stop, tpHit = side > 0 ? px >= k.target : px <= k.target;
            if (slHit || tpHit) {
                double exit = slHit ? k.stop : k.target;
                double[] r = livePnl(k.pos, exit); balance += r[0];
                record(k, exit, slHit ? "SL" : "TP", r[0], r[1]);
                ev.toast(String.format(Locale.US, "%s %s at %." + m.dec() + "f: %+,.0f$ (%+.2f%%)  balance %,.0f$", slHit ? "SL" : "TP", k.kind, exit, r[0], r[1], balance));
                brackets.remove(i); changed = true;
            }
        }
        if (changed) save();
    }

    public void deleteAll() { trends.clear(); selected = -1; live = null; mode = 0; save(); }

    /** One CLOSED paper trade in the ledger, the terminal's line: entry date-time (the PC's zone), win / loss,
     * L / S, the reason, the signed price % in favour, the net $ and % on margin, the balance. */
    private void record(Bracket k, double exit, String reason, double net, double pct) {
        int side = k.side(); boolean win = net >= 0;
        double pctPx = k.entry != 0 ? (exit - k.entry) / k.entry * side * 100 : 0;
        String stamp;
        if (Double.isNaN(k.entryTs)) stamp = new java.text.SimpleDateFormat("HH:mm:ss", Locale.US).format(new java.util.Date());
        else {
            java.util.Calendar cal = java.util.Calendar.getInstance(m.tzOff() == Integer.MIN_VALUE ? java.util.TimeZone.getDefault() : new java.util.SimpleTimeZone(m.tzOff() * 1000, "PC"));
            cal.setTimeInMillis((long) (k.entryTs * 1000));
            stamp = String.format(Locale.US, "%d/%d/%02d - %02d:%02d", cal.get(java.util.Calendar.DAY_OF_MONTH), cal.get(java.util.Calendar.MONTH) + 1, cal.get(java.util.Calendar.YEAR) % 100, cal.get(java.util.Calendar.HOUR_OF_DAY), cal.get(java.util.Calendar.MINUTE));
        }
        String txt = String.format(Locale.US, "%s  %s %s %s  %+.2f%%  %+,.0f$ (%+.1f%%)  bal %,.0f$", stamp, win ? "\u2705" : "\u274c", side > 0 ? "L" : "S", reason, pctPx, net, pct, balance);
        ledger.add(0, new String[]{txt, win ? "#27ae60" : "#e74c3c"});
        while (ledger.size() > 300) ledger.remove(ledger.size() - 1);
    }

    /** The panel's Clear: the LIVE paper account back to its start balance and the list wiped. */
    public void clearPaper() { balance = START_BALANCE; ledger.clear(); save(); }

    public int count() { return trends.size() + brackets.size(); }

    // ------------------------------------------------------------------ touch
    private static float distSeg(float px, float py, float x0, float y0, float x1, float y1) {
        float dx = x1 - x0, dy = y1 - y0; float l2 = dx * dx + dy * dy;
        float t = l2 > 0 ? Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / l2)) : 0;
        float cx = x0 + t * dx, cy = y0 + t * dy;
        return (float) Math.hypot(px - cx, py - cy);
    }

    /** Buttons first: the toolbar and the BUY / SELL pair. True when the tap was one of them. */
    public boolean tapButton(float x, float y) {
        if (showBar) {
            if (barBtn[0].contains(x, y)) { tool = "trend".equals(tool) ? null : "trend"; selected = -1; return true; }
            if (barBtn[1].contains(x, y)) { tool = "select".equals(tool) ? null : "select"; return true; }
            if (barBtn[2].contains(x, y)) { ev.toast(trends.isEmpty() ? "no drawings" : "long-press Delete All to confirm"); return true; }
        }
        if (showMarket) {
            if (buyBtn.contains(x, y)) { placeMarket("long", m.live()); return true; }
            if (sellBtn.contains(x, y)) { placeMarket("short", m.live()); return true; }
        }
        for (float[] c : closeBtns) {
            if (Math.hypot(x - c[0], y - c[1]) < 16 * d) {
                int i = (int) c[2];
                if (i < brackets.size()) {
                    Bracket k = brackets.get(i);
                    if ("ACTIVE".equals(k.state) && k.pos != null && !Double.isNaN(k.lastPx)) {
                        double[] r = livePnl(k.pos, k.lastPx); balance += r[0];
                        record(k, k.lastPx, "\u00d7", r[0], r[1]);
                        ev.toast(String.format(Locale.US, "closed %s at %." + m.dec() + "f: %+,.0f$ (%+.2f%%)", k.kind, k.lastPx, r[0], r[1]));
                    } else ev.toast("order cancelled");
                    brackets.remove(i); save();
                }
                return true;
            }
        }
        return false;
    }

    public boolean longPress(float x, float y) {
        if (showBar && barBtn[2].contains(x, y)) { deleteAll(); ev.toast("all drawings deleted"); return true; }
        return false;
    }

    /** A tap on the pane with the select tool: pick the nearest trend line, or drop the selection. */
    public void tapPane(float x, float y) {
        if (!"select".equals(tool)) return;
        selected = hitTrend(x, y, 16 * d);
    }

    private int hitTrend(float x, float y, float tol) {
        int best = -1; float bd = tol;
        for (int i = trends.size() - 1; i >= 0; i--) {
            Trend t = trends.get(i);
            float dd = distSeg(x, y, m.xPx(t.x0), m.yPx(t.y0), m.xPx(t.x1), m.yPx(t.y1));
            if (dd < bd) { bd = dd; best = i; }
        }
        return best;
    }

    /** The view hands every touch here first; true = consumed (a drawing or a drag in progress). */
    public boolean onTouch(MotionEvent e) {
        int a = e.getActionMasked(); float x = e.getX(), y = e.getY();
        RectF r = m.pane();
        if (a == MotionEvent.ACTION_DOWN) {
            mode = 0; moved = false; lastX = x; lastY = y;
            if (!r.contains(x, y) || x > m.plotRight()) return false;
            if (isButton(x, y)) return false;                                  // the tap path handles buttons
            // a bracket line under the finger is always draggable (the terminal's lines are movable)
            for (Bracket k : brackets) {
                double[] lv = {k.entry, k.stop, k.target};
                for (int j = 0; j < 3; j++) if (Math.abs(m.yPx(lv[j]) - y) < 14 * d) { mode = 4; dragBk = k; dragLine = j; return true; }
            }
            if ("trend".equals(tool)) {
                live = new Trend(); live.x0 = live.x1 = m.xVal(x); live.y0 = live.y1 = m.yVal(y); mode = 1; return true;
            }
            if ("select".equals(tool) && selected >= 0 && selected < trends.size()) {
                Trend t = trends.get(selected);
                if (Math.hypot(m.xPx(t.x0) - x, m.yPx(t.y0) - y) < 22 * d) { mode = 2; dragEnd = 0; return true; }
                if (Math.hypot(m.xPx(t.x1) - x, m.yPx(t.y1) - y) < 22 * d) { mode = 2; dragEnd = 1; return true; }
                if (distSeg(x, y, m.xPx(t.x0), m.yPx(t.y0), m.xPx(t.x1), m.yPx(t.y1)) < 14 * d) { mode = 3; return true; }
            }
            return false;
        }
        if (mode == 0) return false;
        if (a == MotionEvent.ACTION_MOVE) {
            if (Math.hypot(x - lastX, y - lastY) > 3 * d) moved = true;
            if (mode == 1) { live.x1 = m.xVal(x); live.y1 = m.yVal(y); }
            else if (mode == 2) { Trend t = trends.get(selected); if (dragEnd == 0) { t.x0 = m.xVal(x); t.y0 = m.yVal(y); } else { t.x1 = m.xVal(x); t.y1 = m.yVal(y); } }
            else if (mode == 3) { Trend t = trends.get(selected); double dx = m.xVal(x) - m.xVal(lastX), dy = m.yVal(y) - m.yVal(lastY); t.x0 += dx; t.x1 += dx; t.y0 += dy; t.y1 += dy; lastX = x; lastY = y; }
            else if (mode == 4) { double v = snap(m.yVal(y)); if (dragLine == 0) dragBk.entry = v; else if (dragLine == 1) dragBk.stop = v; else dragBk.target = v; }
            return true;
        }
        if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) {
            if (mode == 1) {
                if (a == MotionEvent.ACTION_UP && Math.hypot(m.xPx(live.x1) - m.xPx(live.x0), m.yPx(live.y1) - m.yPx(live.y0)) > 12 * d) { trends.add(live); save(); }
                live = null;
            } else if (mode == 2 || mode == 3 || mode == 4) save();
            mode = 0; return true;
        }
        return true;
    }

    private boolean isButton(float x, float y) {
        if (showBar) for (RectF b : barBtn) if (b.contains(x, y)) return true;
        if (showMarket && (buyBtn.contains(x, y) || sellBtn.contains(x, y))) return true;
        for (float[] c : closeBtns) if (Math.hypot(x - c[0], y - c[1]) < 16 * d) return true;
        return false;
    }

    // ------------------------------------------------------------------ draw
    private void dashed(Canvas c, float x0, float x1, float y, float on, float off, int col, float w) {
        pl.setColor(col); pl.setStrokeWidth(w);
        int n = (int) ((x1 - x0) / (on + off)) + 1; float[] seg = new float[n * 4]; int j = 0;
        for (float x = x0; x < x1; x += on + off) { seg[j++] = x; seg[j++] = y; seg[j++] = Math.min(x1, x + on); seg[j++] = y; }
        c.drawLines(seg, 0, j, pl);
    }

    private void pill(Canvas c, float xr, float y, String a, int ca, String b, int cb, int border) {
        pt.setTextSize(12 * d); pt.setFakeBoldText(true);
        float wa = pt.measureText(a); pt.setFakeBoldText(false); float wb = b == null ? 0 : pt.measureText(b);
        float w = wa + wb + 12 * d, h = 20 * d;
        RectF r = new RectF(xr - w, y - h / 2, xr, y + h / 2);
        pf.setColor(Color.rgb(22, 24, 30)); c.drawRoundRect(r, 4 * d, 4 * d, pf);
        pl.setColor(border); pl.setStrokeWidth(1 * d); c.drawRoundRect(r, 4 * d, 4 * d, pl);
        pt.setColor(ca); pt.setFakeBoldText(true); c.drawText(a, r.left + 6 * d, y + 4.5f * d, pt); pt.setFakeBoldText(false);
        if (b != null) { pt.setColor(cb); c.drawText(b, r.left + 6 * d + wa, y + 4.5f * d, pt); }
    }

    /** Everything under the candles' badges: the trend lines and the brackets, clipped to the pane. */
    public void drawShapes(Canvas c, double livePx) {
        RectF r = m.pane(); float pr = m.plotRight();
        c.save(); c.clipRect(r.left, r.top, pr, r.bottom);
        // trend lines: the terminal's default style (white, 2 px) and the always-positive % at the second point
        int trendCol = m.bw() ? Color.BLACK : C_TREND;
        pl.setStrokeWidth(2 * d * 0.75f); pt.setTextSize(11 * d);
        for (int i = 0; i < trends.size() + (this.live != null ? 1 : 0); i++) {
            Trend t = i < trends.size() ? trends.get(i) : this.live;
            if (t == null) continue;
            float x0 = m.xPx(t.x0), y0 = m.yPx(t.y0), x1 = m.xPx(t.x1), y1 = m.yPx(t.y1);
            pl.setColor(trendCol); c.drawLine(x0, y0, x1, y1, pl);
            double pct = t.y0 != 0 ? Math.abs(t.y1 - t.y0) / t.y0 * 100 : 0;
            pt.setColor(trendCol); c.drawText(String.format(Locale.US, "%.2f%%", pct), x1 + 6 * d, y1 + 4 * d, pt);
            if (i == selected) {
                pf.setColor(Color.parseColor("#3498db")); pl.setColor(Color.WHITE); pl.setStrokeWidth(1 * d);
                c.drawCircle(x0, y0, 6 * d, pf); c.drawCircle(x0, y0, 6 * d, pl); c.drawCircle(x1, y1, 6 * d, pf); c.drawCircle(x1, y1, 6 * d, pl);
                pl.setStrokeWidth(2 * d * 0.75f);
            }
        }
        // brackets
        closeBtns.clear();
        float labelX = pr - 26 * d;
        for (int i = 0; i < brackets.size(); i++) {
            Bracket k = brackets.get(i); int side = k.side();
            float ye = m.yPx(k.entry), ys = m.yPx(k.stop), yt = m.yPx(k.target), yb = m.yPx(breakeven(k.entry, side));
            dashed(c, r.left, pr, ye, 3 * d, 7 * d, C_ENTRY, 1 * d);
            dashed(c, r.left, pr, ys, 3 * d, 7 * d, C_SL, 1 * d);
            dashed(c, r.left, pr, yt, 3 * d, 7 * d, C_TP, 1 * d);
            dashed(c, r.left, pr, yb, 3 * d, 7 * d, Color.argb(150, 255, 255, 255), 1 * d);
            double feePct = FEE_RATE * 200, slPct = (k.stop - k.entry) / k.entry * 100 * side - feePct, tpPct = (k.target - k.entry) / k.entry * 100 * side - feePct;
            double risk = Math.abs(k.entry - k.stop), rr = risk > 1e-9 ? Math.abs(k.target - k.entry) / risk : 0;
            String fmt = "%." + m.dec() + "f";
            pill(c, labelX, ys, String.format(Locale.US, fmt + " (%+.2f%%)", k.stop, slPct), Color.parseColor("#ff8a80"), null, 0, C_SL);
            pill(c, labelX, yt, String.format(Locale.US, "TP " + fmt + " (%+.2f%%)", k.target, tpPct), Color.parseColor("#69f0ae"), String.format(Locale.US, "  1:%.2f", rr), Color.parseColor("#9aa0a6"), C_TP);
            String eb = null; int ec = 0;
            if ("ACTIVE".equals(k.state) && k.pos != null && !Double.isNaN(k.lastPx)) {
                double[] pn = livePnl(k.pos, k.lastPx); double chg = (k.lastPx - k.entry) / k.entry * 100 * side - feePct;
                eb = String.format(Locale.US, "  %+,.0f$ (%+.2f%%)", pn[0], chg); ec = pn[0] >= 0 ? Color.parseColor("#69f0ae") : Color.parseColor("#ff5252");
            }
            pill(c, labelX, ye, String.format(Locale.US, fmt, k.entry), Color.parseColor("#82b1ff"), eb, ec, C_ENTRY);
            // the x close / cancel button, right of the entry badge
            float cx = labelX + 13 * d;
            pf.setColor(Color.rgb(231, 76, 60)); c.drawCircle(cx, ye, 8 * d, pf);
            pl.setColor(Color.argb(210, 255, 255, 255)); pl.setStrokeWidth(1.5f * d);
            c.drawLine(cx - 4 * d, ye - 4 * d, cx + 4 * d, ye + 4 * d, pl); c.drawLine(cx - 4 * d, ye + 4 * d, cx + 4 * d, ye - 4 * d, pl);
            closeBtns.add(new float[]{cx, ye, i});
        }
        c.restore();
    }

    /** The toolbar (top left of the pane) and the BUY / SELL pair (bottom left), on top of everything. */
    public void drawButtons(Canvas c) {
        RectF r = m.pane();
        pt.setTextSize(11 * d); pt.setFakeBoldText(true);
        if (showBar) {
            String[] lab = {"╱ Trend", "↖ Select", "✕ All"};
            float x = r.left + 6 * d, y = r.top + 20 * d;
            for (int i = 0; i < 3; i++) {
                float w = pt.measureText(lab[i]) + 16 * d;
                barBtn[i].set(x, y, x + w, y + 26 * d);
                boolean on = (i == 0 && "trend".equals(tool)) || (i == 1 && "select".equals(tool));
                pf.setColor(on ? Color.parseColor("#2962ff") : Color.parseColor("#20242c")); c.drawRoundRect(barBtn[i], 4 * d, 4 * d, pf);
                pl.setColor(Color.parseColor("#3a4150")); pl.setStrokeWidth(1 * d); c.drawRoundRect(barBtn[i], 4 * d, 4 * d, pl);
                pt.setColor(Color.parseColor("#dcdcdc")); c.drawText(lab[i], x + 8 * d, y + 18 * d, pt);
                x += w + 6 * d;
            }
        } else for (RectF b : barBtn) b.setEmpty();
        if (showMarket) {
            float w = 70 * d, h = 28 * d, y = r.bottom - h - 6 * d, xr = m.viewRight() - 6 * d;
            sellBtn.set(xr - w, y, xr, y + h); buyBtn.set(xr - 2 * w - 6 * d, y, xr - w - 6 * d, y + h);
            pf.setColor(Color.parseColor("#1e8f5a")); c.drawRoundRect(buyBtn, 4 * d, 4 * d, pf);
            pf.setColor(Color.parseColor("#b63a3a")); c.drawRoundRect(sellBtn, 4 * d, 4 * d, pf);
            pt.setColor(Color.WHITE); pt.setTextSize(12 * d);
            c.drawText("▲ BUY", buyBtn.left + (w - pt.measureText("▲ BUY")) / 2, y + 19 * d, pt);
            c.drawText("▼ SELL", sellBtn.left + (w - pt.measureText("▼ SELL")) / 2, y + 19 * d, pt);
        } else { buyBtn.setEmpty(); sellBtn.setEmpty(); }
        pt.setFakeBoldText(false);
    }
}
