package com.smc.flowtab;

import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

/**
 * Everything the engine has told us, as plain arrays. Written by the feed thread under the model's lock, read by
 * the UI thread under the same lock (every reader copies what it needs and lets go). `version` bumps on every
 * change so a frame can be skipped when nothing moved.
 *
 * The one-second bins are the store's (index = second): the flow lines, the price path and the forming candle
 * are derived from them here, exactly as the terminal derives them from its own store.
 */
public final class FlowModel {
    public final Object lock = new Object();
    public long version = 0;

    // ---- hello
    public String sym = "";
    public double tick = 0.01;
    public int dec = 2;
    public double win = 60.0;
    public int lb = 20;
    public JSONObject cfg = new JSONObject();
    public double clockOffset = 0.0;        // engine now - local now (the tablet clock runs ahead of the PC)
    public int tzOff = Integer.MIN_VALUE;   // the PC's UTC offset in seconds: the clock axis reads as the terminal's
    public boolean connected = false;

    // ---- bins (absolute bin index = seconds since the epoch)
    public long binBase = 0;
    public float[] buy = new float[0], sell = new float[0], px = new float[0], pxh = new float[0], pxl = new float[0];

    // ---- cycles (the whole store, uncapped)
    public int nCyc = 0;
    public double[] cT = new double[0], cTe = new double[0];
    public byte[] cSide = new byte[0], cStrong = new byte[0], cDone = new byte[0], cCol = new byte[0], cSt = new byte[0], cPickB = new byte[0];
    public float[] cMove = new float[0], cBuy = new float[0], cSell = new float[0], cO = new float[0], cH = new float[0], cL = new float[0], cC = new float[0], cRate = new float[0];

    // ---- live
    public double now = 0.0;
    public double livePx = Double.NaN;
    public int formCol = -1;

    // ---- interest x impact (the engine's view)
    public String iimpMode = "None";
    public int iN = 0;
    public double[] iX0 = new double[0], iX1 = new double[0];
    public float[] iV, iMult, iScore, iWall, iReach, iMv, iArb, iArs, iKept, iSbuy, iSsell, iLiib, iLiis, iPliib, iPliis;
    public byte[] iUp, iContra, iGood, iForm, iVac, iQuiet;
    public long iimpVersion = 0;

    // ---- LINES INTEREST / LINES IMPACT (the two halves of the I x I reading, panes of their own)
    public static final class Lines {
        public int n = 0;
        public double[] x0 = new double[0], x1 = new double[0];
        public float[] b = new float[0], s = new float[0];
        public byte[] form = new byte[0];
        public byte[] dside = null;                 // LINES IMPACT only: +1 buyers band, -1 sellers, 0 none
        public float[] dgain = null, dgap = null;
        public double spread = 0.3, gain = 0.3;
        public int smooth = 20;
    }
    public final Lines cint = new Lines(), cimp = new Lines();
    public int smoothMin = 1, smoothMax = 30;
    public int smIimp = 1, smCint = 20, smCimp = 20;   // the three windows, as the engine last reported them

    // ---- interpretation rows
    public static final class Row {
        public double t0, t1; public String head, name, d1, d2a, d2b, mvTxt, mvWord; public int st, col, mvSign; public boolean strong, forming;
    }
    public List<Row> rows = new ArrayList<>();

    // ---- limit orders (the curves as drawn)
    public double[] lqX = new double[0];
    public float[] lqB = new float[0], lqA = new float[0];
    public int lqRadius = 100;

    // ---- takeover marks
    public double[] tkBuy = new double[0], tkSell = new double[0];
    public double[] tkForm = null;          // [x, y, isBuy]

    // ---- HLH Volume Profile geometry (the terminal's build_period, recorded) and Big Player marks
    public static final class HlhOp { public char t; public int pen, brush; public float w; public double[] v; }
    public static final class HlhPic { public String k; public double x0, x1; public List<HlhOp> ops; }
    public static final class HlhLabel { public double x, y; public String text, anchor, font; public int bg, fg; }
    public static final class HlhDash { public double xa, xb, y; public int col; }
    public boolean hlhOn = false; public String hlhNote = null;
    public List<HlhPic> hlhPics = new ArrayList<>(); public List<HlhLabel> hlhLabels = new ArrayList<>(); public List<HlhDash> hlhDashes = new ArrayList<>();
    private final java.util.HashMap<String, List<HlhOp>> hlhCache = new java.util.HashMap<>();
    public boolean bpOn = false, bpSw = false; public int bpLmax = 60;
    public double[][] bpBub = new double[0][], bpDia = new double[0][];

    // ---- explain replies (k -> html), consumed by the UI
    public double explainK = Double.NaN;
    public String explainHtml = null;

    // ------------------------------------------------------------------ decoding
    static float[] f32(String b64) {
        if (b64 == null || b64.isEmpty()) return new float[0];
        byte[] raw = Base64.decode(b64, Base64.DEFAULT);
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        float[] out = new float[raw.length / 4];
        bb.asFloatBuffer().get(out);
        return out;
    }

    static double[] f64(String b64) {
        if (b64 == null || b64.isEmpty()) return new double[0];
        byte[] raw = Base64.decode(b64, Base64.DEFAULT);
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        double[] out = new double[raw.length / 8];
        bb.asDoubleBuffer().get(out);
        return out;
    }

    static byte[] i8(String b64) {
        if (b64 == null || b64.isEmpty()) return new byte[0];
        return Base64.decode(b64, Base64.DEFAULT);
    }

    private static float[] replace(float[] dst, int dstOff, float[] src, int total) {
        float[] out = dst.length == total ? dst : java.util.Arrays.copyOf(dst, total);
        System.arraycopy(src, 0, out, dstOff, Math.min(src.length, total - dstOff));
        return out;
    }

    private static double[] replace(double[] dst, int dstOff, double[] src, int total) {
        double[] out = dst.length == total ? dst : java.util.Arrays.copyOf(dst, total);
        System.arraycopy(src, 0, out, dstOff, Math.min(src.length, total - dstOff));
        return out;
    }

    private static byte[] replace(byte[] dst, int dstOff, byte[] src, int total) {
        byte[] out = dst.length == total ? dst : java.util.Arrays.copyOf(dst, total);
        System.arraycopy(src, 0, out, dstOff, Math.min(src.length, total - dstOff));
        return out;
    }

    // ------------------------------------------------------------------ ingest (feed thread)
    public void onHello(JSONObject m) {
        synchronized (lock) {
            sym = m.optString("sym", ""); tick = m.optDouble("tick", 0.01); dec = m.optInt("dec", 2);
            win = m.optDouble("win", 60.0); lb = m.optInt("lb", 20); cfg = m.optJSONObject("cfg");
            if (m.has("tz")) tzOff = m.optInt("tz");
            if (cfg == null) cfg = new JSONObject();
            clockOffset = m.optDouble("now", 0.0) - System.currentTimeMillis() / 1000.0;
            // the three smoothing windows and their bounds, as the ENGINE has them -- the tablet's sliders
            // start where the terminal's are rather than at a guess of their own
            smoothMin = m.optInt("smooth_min", smoothMin); smoothMax = m.optInt("smooth_max", smoothMax);
            JSONObject sm = m.optJSONObject("smooth");
            if (sm != null) {
                smIimp = sm.optInt("iimp", smIimp); smCint = sm.optInt("cint", smCint); smCimp = sm.optInt("cimp", smCimp);
            }
            connected = true; version++;
        }
    }

    public void onBins(JSONObject m) {
        long base = m.optLong("base"); int n = m.optInt("n"); boolean full = m.optBoolean("full", false);
        float[] b = f32(m.optString("buy")), s = f32(m.optString("sell")), p = f32(m.optString("px")), h = f32(m.optString("pxh")), l = f32(m.optString("pxl"));
        synchronized (lock) {
            if (full || buy.length == 0) {
                binBase = base; buy = b; sell = s; px = p; pxh = h; pxl = l;
            } else {
                long lo = Math.min(binBase, base), hi = Math.max(binBase + buy.length, base + n);
                int total = (int) (hi - lo);
                if (lo != binBase) {                                   // a prepend: shift what we hold
                    int shift = (int) (binBase - lo);
                    buy = shiftRight(buy, shift, total); sell = shiftRight(sell, shift, total); px = shiftRight(px, shift, total);
                    pxh = shiftRight(pxh, shift, total); pxl = shiftRight(pxl, shift, total);
                    binBase = lo;
                }
                int off = (int) (base - binBase);
                buy = replace(buy, off, b, total); sell = replace(sell, off, s, total); px = replace(px, off, p, total);
                pxh = replace(pxh, off, h, total); pxl = replace(pxl, off, l, total);
            }
            version++;
        }
    }

    private static float[] shiftRight(float[] a, int shift, int total) {
        float[] out = new float[total];
        System.arraycopy(a, 0, out, shift, Math.min(a.length, total - shift));
        return out;
    }

    public void onCycles(JSONObject m) {
        int i0 = m.optInt("i0"); int total = m.optInt("total");
        double[] t = f64(m.optString("ts")), te = f64(m.optString("te"));
        byte[] side = i8(m.optString("side")), strong = i8(m.optString("strong")), done = i8(m.optString("done")), col = i8(m.optString("col")), st = i8(m.optString("st")), pickb = i8(m.optString("pickb"));
        float[] mv = f32(m.optString("move")), cb = f32(m.optString("cbuy")), cs = f32(m.optString("csell")), o = f32(m.optString("o")), h = f32(m.optString("h")), l = f32(m.optString("l")), c = f32(m.optString("c")), rate = f32(m.optString("rate"));
        synchronized (lock) {
            if (i0 == 0 || i0 > nCyc) {                                   // a full table, or a tail we cannot splice onto
                if (i0 > nCyc) return;
                cT = t; cTe = te; cSide = side; cStrong = strong; cDone = done; cCol = col; cSt = st; cPickB = pickb;
                cMove = mv; cBuy = cb; cSell = cs; cO = o; cH = h; cL = l; cC = c; cRate = rate;
            } else {
                cT = replace(cT, i0, t, total); cTe = replace(cTe, i0, te, total);
                cSide = replace(cSide, i0, side, total); cStrong = replace(cStrong, i0, strong, total); cDone = replace(cDone, i0, done, total);
                cCol = replace(cCol, i0, col, total); cSt = replace(cSt, i0, st, total); cPickB = replace(cPickB, i0, pickb, total);
                cMove = replace(cMove, i0, mv, total); cBuy = replace(cBuy, i0, cb, total); cSell = replace(cSell, i0, cs, total);
                cO = replace(cO, i0, o, total); cH = replace(cH, i0, h, total); cL = replace(cL, i0, l, total); cC = replace(cC, i0, c, total);
                cRate = replace(cRate, i0, rate, total);
            }
            nCyc = total; version++;
        }
    }

    public void onLive(JSONObject m) {
        synchronized (lock) {
            now = m.optDouble("now", now);
            clockOffset = now - System.currentTimeMillis() / 1000.0;
            livePx = m.isNull("px") ? Double.NaN : m.optDouble("px");
            formCol = m.optInt("fcol", -1);
            version++;
        }
    }

    public void onIimp(JSONObject m) {
        int n = m.optInt("n");
        double[] x0 = f64(m.optString("x0")), x1 = f64(m.optString("x1"));
        float[] v = f32(m.optString("v")), mult = f32(m.optString("mult")), score = f32(m.optString("score")), wall = f32(m.optString("wall")), reach = f32(m.optString("reach")), mv = f32(m.optString("mv"));
        float[] arb = f32(m.optString("arb")), ars = f32(m.optString("ars")), kept = f32(m.optString("kept")), sbuy = f32(m.optString("sbuy")), ssell = f32(m.optString("ssell"));
        float[] liib = f32(m.optString("liib")), liis = f32(m.optString("liis")), pliib = f32(m.optString("pliib")), pliis = f32(m.optString("pliis"));
        byte[] up = i8(m.optString("up")), contra = i8(m.optString("contra")), good = i8(m.optString("good")), form = i8(m.optString("form")), vac = i8(m.optString("vac")), quiet = i8(m.optString("quiet"));
        synchronized (lock) {
            iimpMode = m.optString("mode", "None"); iN = n; iX0 = x0; iX1 = x1;
            iV = v; iMult = mult; iScore = score; iWall = wall; iReach = reach; iMv = mv; iArb = arb; iArs = ars; iKept = kept; iSbuy = sbuy; iSsell = ssell;
            iLiib = liib; iLiis = liis; iPliib = pliib; iPliis = pliis;
            iUp = up; iContra = contra; iGood = good; iForm = form; iVac = vac; iQuiet = quiet;
            iimpVersion++; version++;
        }
    }

    /** LINES INTEREST / LINES IMPACT. Sent whole, one point per drawn cycle. */
    public void onLines(String kind, JSONObject m) {
        Lines L = "cimp".equals(kind) ? cimp : cint;
        int n = m.optInt("n");
        double[] x0 = f64(m.optString("x0")), x1 = f64(m.optString("x1"));
        float[] b = f32(m.optString("b")), s2 = f32(m.optString("s"));
        byte[] fm = i8(m.optString("form"));
        byte[] ds = m.has("dside") ? i8(m.optString("dside")) : null;
        float[] dg = m.has("dgain") ? f32(m.optString("dgain")) : null;
        float[] gp = m.has("dgap") ? f32(m.optString("dgap")) : null;
        synchronized (lock) {
            L.n = n; L.x0 = x0; L.x1 = x1; L.b = b; L.s = s2; L.form = fm;
            L.dside = ds; L.dgain = dg; L.dgap = gp;
            if (m.has("spread")) L.spread = m.optDouble("spread", 0.3);
            if (m.has("gain")) L.gain = m.optDouble("gain", 0.3);
            L.smooth = m.optInt("smooth", L.smooth);
            if ("cimp".equals(kind)) smCimp = L.smooth; else smCint = L.smooth;
            version++;
        }
    }

    public void onInterp(JSONObject m) {
        JSONArray a = m.optJSONArray("rows");
        List<Row> out = new ArrayList<>();
        if (a != null) {
            for (int i = 0; i < a.length(); i++) {
                JSONArray r = a.optJSONArray(i);
                if (r == null || r.length() < 13) continue;
                Row row = new Row();
                row.t0 = r.optDouble(0); row.t1 = r.optDouble(1); row.head = r.optString(2); row.name = r.optString(3); row.d1 = r.optString(4);
                JSONArray d2 = r.optJSONArray(5); row.d2a = d2 == null ? "" : d2.optString(0); row.d2b = d2 == null ? "" : d2.optString(1);
                row.st = r.optInt(6); row.strong = r.optBoolean(7); row.forming = r.optBoolean(8); row.col = r.optInt(9);
                row.mvTxt = r.optString(10); row.mvSign = r.optInt(11); row.mvWord = r.optString(12);
                out.add(row);
            }
        }
        synchronized (lock) { rows = out; version++; }
    }

    public void onLiq(JSONObject m) {
        double[] x = f64(m.optString("x")); float[] b = f32(m.optString("b")), a = f32(m.optString("a"));
        synchronized (lock) { lqX = x; lqB = b; lqA = a; lqRadius = m.optInt("radius", 100); version++; }
    }

    public void onTko(JSONObject m) {
        double[] b = f64(m.optString("buy")), s = f64(m.optString("sell"));
        JSONArray f = m.optJSONArray("form");
        double[] form = null;
        if (f != null && f.length() >= 3) form = new double[]{f.optDouble(0), f.optDouble(1), f.optBoolean(2) ? 1.0 : 0.0};
        synchronized (lock) { tkBuy = b; tkSell = s; tkForm = form; version++; }
    }

    public void onHlh(JSONObject m) {
        boolean on = m.optBoolean("on", false);
        List<HlhPic> pics = new ArrayList<>(); List<HlhLabel> labels = new ArrayList<>(); List<HlhDash> dashes = new ArrayList<>();
        java.util.HashMap<String, List<HlhOp>> cache = new java.util.HashMap<>();
        if (on) {
            JSONArray pa = m.optJSONArray("pics");
            if (pa != null) for (int i = 0; i < pa.length(); i++) {
                JSONObject po = pa.optJSONObject(i); if (po == null) continue;
                HlhPic pic = new HlhPic(); pic.k = po.optString("k"); pic.x0 = po.optDouble("x0"); pic.x1 = po.optDouble("x1");
                JSONArray oa = po.optJSONArray("ops");
                if (oa != null) {
                    List<HlhOp> ops = new ArrayList<>(oa.length());
                    for (int j = 0; j < oa.length(); j++) {
                        JSONArray a = oa.optJSONArray(j); if (a == null || a.length() < 4) continue;
                        HlhOp op = new HlhOp(); op.t = a.optString(0, "L").charAt(0); op.pen = (int) a.optLong(1); op.w = (float) a.optDouble(2, 1.0);
                        if (op.t == 'P') { op.brush = (int) a.optLong(3); JSONArray pts = a.optJSONArray(4); int n = pts == null ? 0 : pts.length(); op.v = new double[n]; for (int q = 0; q < n; q++) op.v[q] = pts.optDouble(q); }
                        else if (op.t == 'R') { op.brush = (int) a.optLong(3); op.v = new double[]{a.optDouble(4), a.optDouble(5), a.optDouble(6), a.optDouble(7)}; }
                        else { op.v = new double[]{a.optDouble(3), a.optDouble(4), a.optDouble(5), a.optDouble(6)}; }
                        ops.add(op);
                    }
                    pic.ops = ops;
                } else {
                    synchronized (lock) { pic.ops = hlhCache.get(pic.k); }
                    if (pic.ops == null) pic.ops = new ArrayList<>();
                }
                cache.put(pic.k, pic.ops); pics.add(pic);
            }
            JSONArray la = m.optJSONArray("labels");
            if (la != null) for (int i = 0; i < la.length(); i++) {
                JSONArray a = la.optJSONArray(i); if (a == null || a.length() < 7) continue;
                HlhLabel l = new HlhLabel(); l.x = a.optDouble(0); l.y = a.optDouble(1); l.text = a.optString(2); l.anchor = a.optString(3); l.bg = (int) a.optLong(4); l.fg = (int) a.optLong(5); l.font = a.optString(6);
                labels.add(l);
            }
            JSONArray da = m.optJSONArray("dashes");
            if (da != null) for (int i = 0; i < da.length(); i++) {
                JSONArray a = da.optJSONArray(i); if (a == null || a.length() < 4) continue;
                HlhDash dd = new HlhDash(); dd.xa = a.optDouble(0); dd.xb = a.optDouble(1); dd.y = a.optDouble(2); dd.col = (int) a.optLong(3);
                dashes.add(dd);
            }
        }
        synchronized (lock) {
            hlhOn = on; hlhNote = m.isNull("note") ? null : m.optString("note", null);
            hlhPics = pics; hlhLabels = labels; hlhDashes = dashes;
            hlhCache.clear(); hlhCache.putAll(cache);
            version++;
        }
    }

    private static double[][] rows(JSONArray a) {
        if (a == null) return new double[0][];
        double[][] out = new double[a.length()][];
        for (int i = 0; i < a.length(); i++) {
            JSONArray r = a.optJSONArray(i); int n = r == null ? 0 : r.length();
            out[i] = new double[n];
            for (int j = 0; j < n; j++) { Object o = r.opt(j); out[i][j] = o instanceof Boolean ? ((Boolean) o ? 1 : 0) : r.optDouble(j); }
        }
        return out;
    }

    public void onBp(JSONObject m) {
        boolean on = m.optBoolean("on", false);
        double[][] bub = on ? rows(m.optJSONArray("bub")) : new double[0][], dia = on ? rows(m.optJSONArray("dia")) : new double[0][];
        synchronized (lock) { bpOn = on; bpSw = m.optBoolean("sw", false); bpLmax = m.optInt("lmax", 60); bpBub = bub; bpDia = dia; version++; }
    }

    public void onExplain(JSONObject m) {
        synchronized (lock) { explainK = m.optDouble("k"); explainHtml = m.optString("html", ""); version++; }
    }

    /** The engine's clock, as the tablet best knows it. */
    public double nowEngine() {
        return System.currentTimeMillis() / 1000.0 + clockOffset;
    }

    /** The rolling-window flow at bin i (absolute index): the $ of the `win` seconds ending at that bin. */
    public void series(double t0, double t1, int maxPts, float[][] out) {
        // out[0] = t, out[1] = buy, out[2] = sell -- allocated here at the decimated length
        int w = Math.max(1, (int) Math.round(win));
        int n = buy.length;
        int i0 = (int) (Math.floor(t0) - binBase), i1 = (int) (Math.floor(t1) - binBase);
        i0 = Math.max(0, i0); i1 = Math.min(n - 1, i1);
        if (i1 < i0 || n == 0) { out[0] = out[1] = out[2] = new float[0]; return; }
        int step = Math.max(1, (int) Math.ceil((i1 - i0 + 1) / (double) Math.max(16, maxPts)));
        int m = (i1 - i0) / step + 1;
        float[] t = new float[m], b = new float[m], s = new float[m];
        // one running sum, stepped: the sum over (i-w, i] for each sampled i
        double sb = 0, ss = 0; int lo = Math.max(0, i0 - w + 1);
        for (int k = lo; k <= i0; k++) { sb += buy[k]; ss += sell[k]; }
        int cur = i0; int j = 0;
        t[0] = (float) (i0 + 1); b[0] = (float) sb; s[0] = (float) ss; j = 1;
        for (int i = i0 + step; i <= i1; i += step) {
            // move the window from cur to i
            for (int k = cur + 1; k <= i; k++) {
                sb += buy[k]; ss += sell[k];
                int drop = k - w;
                if (drop >= 0) { sb -= buy[drop]; ss -= sell[drop]; }
            }
            cur = i;
            t[j] = (float) (i + 1); b[j] = (float) sb; s[j] = (float) ss; j++;
        }
        out[0] = t; out[1] = b; out[2] = s;      // t is the bin END, relative to binBase (add binBase for epoch)
    }
}
