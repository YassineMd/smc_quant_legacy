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
    // ⚠ SPEED (2026-09-26, "it has become a little bit slow"): the store grows by one bin a SECOND, and the arrays used to
    // be exactly that long -- so every second all of them were copied whole into new ones: 72 h x 5 arrays = ~5 MB of
    // garbage a second, the 10 MB large-object GCs in the log. They now keep BIN_SLACK of spare room and binN says how
    // many bins are held: the live edge is written in place and a copy happens about once an hour. px / pxh / pxl ride
    // along on the wire but nothing on the tablet draws them, so they are neither decoded nor kept.
    public long binBase = 0;
    public int binN = 0;                         // bins held: buy / sell [0, binN); the arrays are longer (spare room)
    public float[] buy = new float[0], sell = new float[0];
    public int binsVer = 0;                      // bumps whenever a bin changes (the Flow pane's series cache)
    private static final int BIN_SLACK = 3600;

    // ---- cycles (the whole store, uncapped)
    public int nCyc = 0;
    public double[] cT = new double[0], cTe = new double[0];
    public byte[] cSide = new byte[0], cStrong = new byte[0], cDone = new byte[0], cCol = new byte[0], cSt = new byte[0], cPickB = new byte[0];
    public byte[] cLead = new byte[0];      // the I x I leader: +1 buyers, -1 sellers, 0 unrated (KEPT TICKS BY LEADER)
    public byte[] cConf = new byte[0];      // 1 = a CONFLICT bar: both sides' tapes >= config.CONFLICT_TAPE_MIN (red box)
    public float[] cCfh = new float[0], cCfl = new float[0];   // ... its box's high / low (terminal._conflict_boxes), NaN off one
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
    public float[] iPback;      // the NON-leading side's push-back multiple (-999 = not read), 2026-09-24
    // the pair the LINES mode draws: the raw one through the slider's trailing mean (raw when it is at 1)
    public float[] iLyb = new float[0], iLys = new float[0];
    public int iSmn = 1;        // the smoothing window the I x I DATA was computed with (not the slider's position)
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
        public double spread = 0.3, gain = 0.3, step = 0.3;
        public int smooth = 20;
        String keepKey = null;                      // what the held rows MEAN: smoothing, lookback, window
    }
    public final Lines cint = new Lines(), cimp = new Lines();
    public int smoothMin = 1, smoothMax = 30;
    public int smIimp = 5, smCint = 5, smCimp = 3;     // the three windows, as the engine last reported them
                                                       // (these are only what shows before `hello` lands)

    // ---- interpretation rows
    public static final class Row {
        public double t0, t1; public String head, name, d1, d2a, d2b, mvTxt, mvWord; public int st, col, mvSign; public boolean strong, forming;
        // the NUMBERS behind the row (card redesign 2026-09-23): the card draws them, NaN where the engine had none
        public String side = ""; public boolean flat;
        public double mv = Double.NaN, px0 = Double.NaN, px1 = Double.NaN, hi = Double.NaN, lo = Double.NaN,
                vr = Double.NaN, sr = Double.NaN, buy = Double.NaN, sell = Double.NaN, bid = Double.NaN, ask = Double.NaN,
                push = Double.NaN, gb = Double.NaN;
        // the I x I PANE's reading of the cycle (2026-09-24): lead +1 buyers / -1 sellers / 0 not rated
        public int iLead; public boolean iGood, iContra; public String iWhy = "";
        public double iMult = Double.NaN, iImp = Double.NaN, iWall = Double.NaN, iKept = Double.NaN, iPb = Double.NaN, iGive = Double.NaN;
        public double iReach = Double.NaN, iLmv = Double.NaN; public boolean iShort;     // a push under 4 ticks (2026-09-24)
        // a NORMAL card that sat in the breakout or vacuum square: which condition failed (flow_interp.gate_why_not)
        public String whyNot = "";
    }
    private static double num(JSONObject o, String k) {
        return (o == null || o.isNull(k) || !o.has(k)) ? Double.NaN : o.optDouble(k, Double.NaN);
    }
    public List<Row> rows = new ArrayList<>();

    // ---- limit orders (the curves as drawn)
    public double[] lqX = new double[0];
    public float[] lqB = new float[0], lqA = new float[0];
    public int lqRadius = 100;

    // ---- takeover marks

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

    private static float[] nans(int n) { float[] a = new float[n]; java.util.Arrays.fill(a, Float.NaN); return a; }

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
        long base = m.optLong("base"); boolean full = m.optBoolean("full", false);
        float[] b = f32(m.optString("buy")), s = f32(m.optString("sell"));
        int n = Math.min(b.length, s.length);
        synchronized (lock) {
            if (full || binN == 0) {
                binBase = base; binN = n;
                buy = java.util.Arrays.copyOf(b, n + BIN_SLACK); sell = java.util.Arrays.copyOf(s, n + BIN_SLACK);
            } else {
                long lo = Math.min(binBase, base), hi = Math.max(binBase + binN, base + n);
                int total = (int) (hi - lo);
                if (lo != binBase || total > buy.length) {             // a prepend (a backfill chunk) or out of room: ONE copy
                    int shift = (int) (binBase - lo);
                    float[] nb = new float[total + BIN_SLACK], ns = new float[total + BIN_SLACK];
                    System.arraycopy(buy, 0, nb, shift, binN); System.arraycopy(sell, 0, ns, shift, binN);
                    buy = nb; sell = ns; binBase = lo;
                }
                int off = (int) (base - binBase);
                System.arraycopy(b, 0, buy, off, n); System.arraycopy(s, 0, sell, off, n);
                binN = total;
            }
            binsVer++; version++;
        }
    }

    public void onCycles(JSONObject m) {
        int i0 = m.optInt("i0"); int total = m.optInt("total");
        double[] t = f64(m.optString("ts")), te = f64(m.optString("te"));
        byte[] side = i8(m.optString("side")), strong = i8(m.optString("strong")), done = i8(m.optString("done")), col = i8(m.optString("col")), st = i8(m.optString("st")), pickb = i8(m.optString("pickb"));
        float[] mv = f32(m.optString("move")), cb = f32(m.optString("cbuy")), cs = f32(m.optString("csell")), o = f32(m.optString("o")), h = f32(m.optString("h")), l = f32(m.optString("l")), c = f32(m.optString("c")), rate = f32(m.optString("rate"));
        byte[] lead = m.has("lead") ? i8(m.optString("lead")) : new byte[t.length];   // an older engine: all unrated
        byte[] conf = m.has("cf") ? i8(m.optString("cf")) : new byte[t.length];       // ... and no conflict bars
        float[] cfh = m.has("cfh") ? f32(m.optString("cfh")) : nans(t.length), cfl = m.has("cfl") ? f32(m.optString("cfl")) : nans(t.length);
        synchronized (lock) {
            if (i0 == 0 || i0 > nCyc) {                                   // a full table, or a tail we cannot splice onto
                if (i0 > nCyc) return;
                cT = t; cTe = te; cSide = side; cStrong = strong; cDone = done; cCol = col; cSt = st; cPickB = pickb;
                cMove = mv; cBuy = cb; cSell = cs; cO = o; cH = h; cL = l; cC = c; cRate = rate; cLead = lead; cConf = conf;
                cCfh = cfh; cCfl = cfl;
            } else {
                cT = replace(cT, i0, t, total); cTe = replace(cTe, i0, te, total);
                cSide = replace(cSide, i0, side, total); cStrong = replace(cStrong, i0, strong, total); cDone = replace(cDone, i0, done, total);
                cCol = replace(cCol, i0, col, total); cSt = replace(cSt, i0, st, total); cPickB = replace(cPickB, i0, pickb, total);
                cMove = replace(cMove, i0, mv, total); cBuy = replace(cBuy, i0, cb, total); cSell = replace(cSell, i0, cs, total);
                cO = replace(cO, i0, o, total); cH = replace(cH, i0, h, total); cL = replace(cL, i0, l, total); cC = replace(cC, i0, c, total);
                cRate = replace(cRate, i0, rate, total);
                cLead = replace(cLead, i0, lead, total);
                cConf = replace(cConf, i0, conf, total);
                cCfh = replace(cCfh, i0, cfh, total); cCfl = replace(cCfl, i0, cfl, total);
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

    // ---- WHAT HAS BEEN PAINTED STAYS (user 2026-09-23: "whatever have been loaded and calculated and painted should
    // staaay no matter if i zoom in out or pane left right"). The engine sends the I x I pane and the two LINES panes
    // for its CURRENT view only; each message used to REPLACE what the tablet held, so everything outside the new
    // view vanished and came back only after a round trip. Now a message is SPLICED in, keyed by cycle start -- the
    // way onBins and onCycles already are: rows inside the stretch it covers are replaced (a recomputed or re-keyed
    // cycle wins), rows outside it stay, and a forming row only ever lives in the newest message.
    // Everything held is dropped when what it MEANS changes: the lookback, the flow window, the smoothing, the mode.
    static final int KEEP_MAX = 6000;                  // cycles held per pane (~72 h); the furthest from the news go

    /** The merge PLAN: for each output row, an old index (>= 0) or a new one (-1 - j). Sorted by cycle start. */
    static int[] keepPlan(double[] ox, byte[] oform, int on, double[] nx, byte[] nform, int nn) {
        double lo = nn > 0 ? nx[0] - 0.5 : Double.POSITIVE_INFINITY;
        boolean forming = false; double formX = Double.POSITIVE_INFINITY;
        for (int j = 0; j < nn; j++) if (nform != null && j < nform.length && nform[j] != 0) { forming = true; formX = Math.min(formX, nx[j]); }
        // the stretch this message covered: its first row to its last -- or on to the live edge while a cycle forms,
        // since nothing after a forming cycle's start can be finished
        double hi = forming ? Double.POSITIVE_INFINITY : (nn > 0 ? nx[nn - 1] + 0.5 : Double.NEGATIVE_INFINITY);
        int[] tmp = new int[on + nn]; int k = 0, i = 0, j = 0;
        while (i < on || j < nn) {
            if (i < on) {
                boolean oldForm = oform != null && i < oform.length && oform[i] != 0;
                boolean punched = (ox[i] >= lo && ox[i] <= hi) || oldForm || ox[i] >= formX - 0.5;
                if (punched) { i++; continue; }
                if (j >= nn || ox[i] < nx[j]) { tmp[k++] = i++; continue; }
            }
            tmp[k++] = -1 - j++;
        }
        int from = 0, to = k;
        if (k > KEEP_MAX) {                            // keep the KEEP_MAX rows nearest the message just received
            int c0 = 0; for (int q = 0; q < k; q++) if (tmp[q] < 0) { c0 = q; break; }
            from = Math.max(0, Math.min(k - KEEP_MAX, c0 - KEEP_MAX / 2)); to = from + KEEP_MAX;
        }
        return java.util.Arrays.copyOfRange(tmp, from, to);
    }
    static double[] pick(double[] o, double[] nw, int[] p) {
        double[] out = new double[p.length];
        for (int q = 0; q < p.length; q++) { int x = p[q]; out[q] = x >= 0 ? (o != null && x < o.length ? o[x] : Double.NaN) : (nw != null && -1 - x < nw.length ? nw[-1 - x] : Double.NaN); }
        return out;
    }
    static float[] pick(float[] o, float[] nw, int[] p) {
        float[] out = new float[p.length];
        for (int q = 0; q < p.length; q++) { int x = p[q]; out[q] = x >= 0 ? (o != null && x < o.length ? o[x] : Float.NaN) : (nw != null && -1 - x < nw.length ? nw[-1 - x] : Float.NaN); }
        return out;
    }
    static byte[] pick(byte[] o, byte[] nw, int[] p) {
        byte[] out = new byte[p.length];
        for (int q = 0; q < p.length; q++) { int x = p[q]; out[q] = x >= 0 ? (o != null && x < o.length ? o[x] : 0) : (nw != null && -1 - x < nw.length ? nw[-1 - x] : 0); }
        return out;
    }
    private String iKeepKey = null;                    // what the held I x I rows MEAN: mode, smoothing, lookback, window

    public void onIimp(JSONObject m) {
        int n = m.optInt("n");
        double[] x0 = f64(m.optString("x0")), x1 = f64(m.optString("x1"));
        float[] v = f32(m.optString("v")), mult = f32(m.optString("mult")), score = f32(m.optString("score")), wall = f32(m.optString("wall")), reach = f32(m.optString("reach")), mv = f32(m.optString("mv"));
        float[] arb = f32(m.optString("arb")), ars = f32(m.optString("ars")), kept = f32(m.optString("kept")), sbuy = f32(m.optString("sbuy")), ssell = f32(m.optString("ssell"));
        float[] liib = f32(m.optString("liib")), liis = f32(m.optString("liis")), pliib = f32(m.optString("pliib")), pliis = f32(m.optString("pliis"));
        float[] lyb = f32(m.optString("lyb")), lys = f32(m.optString("lys"));
        float[] pback = f32(m.optString("pback"));
        if (pback.length != n) { pback = new float[n]; java.util.Arrays.fill(pback, -999f); }   // an older engine
        byte[] up = i8(m.optString("up")), contra = i8(m.optString("contra")), good = i8(m.optString("good")), form = i8(m.optString("form")), vac = i8(m.optString("vac")), quiet = i8(m.optString("quiet"));
        if (lyb.length != n) lyb = liib;
        if (lys.length != n) lys = liis;
        String mode = m.optString("mode", "None");
        int smn = m.optInt("smn", iSmn);
        synchronized (lock) {
            // SPLICED, not replaced (see keepPlan) -- unless what the rows mean has changed
            String key = mode + "|" + smn + "|" + lb + "|" + win;
            int on = key.equals(iKeepKey) && iX0 != null ? iN : 0;
            iKeepKey = key;
            int[] p = keepPlan(iX0, iForm, on, x0, form, n);
            if (on == 0) { iX0 = null; iX1 = null; iV = null; iMult = null; iScore = null; iWall = null; iReach = null; iMv = null; iArb = null; iArs = null; iKept = null; iSbuy = null; iSsell = null; iLiib = null; iLiis = null; iPliib = null; iPliis = null; iLyb = null; iLys = null; iPback = null; iUp = null; iContra = null; iGood = null; iForm = null; iVac = null; iQuiet = null; }
            iimpMode = mode; iN = p.length;
            iX0 = pick(iX0, x0, p); iX1 = pick(iX1, x1, p);
            iV = pick(iV, v, p); iMult = pick(iMult, mult, p); iScore = pick(iScore, score, p); iWall = pick(iWall, wall, p);
            iReach = pick(iReach, reach, p); iMv = pick(iMv, mv, p); iArb = pick(iArb, arb, p); iArs = pick(iArs, ars, p);
            iKept = pick(iKept, kept, p); iSbuy = pick(iSbuy, sbuy, p); iSsell = pick(iSsell, ssell, p);
            iLiib = pick(iLiib, liib, p); iLiis = pick(iLiis, liis, p); iPliib = pick(iPliib, pliib, p); iPliis = pick(iPliis, pliis, p);
            iLyb = pick(iLyb, lyb, p); iLys = pick(iLys, lys, p); iPback = pick(iPback, pback, p);
            smIimp = m.optInt("smn", smIimp);
            iSmn = smn;
            iUp = pick(iUp, up, p); iContra = pick(iContra, contra, p); iGood = pick(iGood, good, p); iForm = pick(iForm, form, p);
            iVac = pick(iVac, vac, p); iQuiet = pick(iQuiet, quiet, p);
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
        int smooth = m.optInt("smooth", L.smooth);
        synchronized (lock) {
            // SPLICED, not replaced (see keepPlan) -- unless what the rows mean has changed
            String key = smooth + "|" + lb + "|" + win;
            int on = key.equals(L.keepKey) ? L.n : 0;
            L.keepKey = key;
            int[] p = keepPlan(L.x0, L.form, on, x0, fm, n);
            if (on == 0) { L.x0 = null; L.x1 = null; L.b = null; L.s = null; L.form = null; L.dside = null; L.dgain = null; L.dgap = null; }
            boolean bands = ds != null || L.dside != null;
            L.n = p.length; L.x0 = pick(L.x0, x0, p); L.x1 = pick(L.x1, x1, p);
            L.b = pick(L.b, b, p); L.s = pick(L.s, s2, p); L.form = pick(L.form, fm, p);
            L.dside = bands ? pick(L.dside, ds, p) : null;
            L.dgain = bands ? pick(L.dgain, dg, p) : null;
            L.dgap = bands ? pick(L.dgap, gp, p) : null;
            if (m.has("spread")) L.spread = m.optDouble("spread", 0.3);
            if (m.has("gain")) L.gain = m.optDouble("gain", 0.3);
            if (m.has("step")) L.step = m.optDouble("step", 0.3);
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
                JSONObject x = r.optJSONObject(13);
                if (x != null) {
                    row.side = x.optString("side", ""); row.flat = x.optBoolean("flat", false);
                    row.mv = num(x, "mv"); row.px0 = num(x, "px0"); row.px1 = num(x, "px1"); row.hi = num(x, "hi"); row.lo = num(x, "lo");
                    row.vr = num(x, "vr"); row.sr = num(x, "sr"); row.buy = num(x, "buy"); row.sell = num(x, "sell");
                    row.bid = num(x, "bid"); row.ask = num(x, "ask"); row.push = num(x, "push"); row.gb = num(x, "gb");
                    row.iLead = x.optInt("i_lead", 0); row.iGood = x.optBoolean("i_good", false); row.iContra = x.optBoolean("i_contra", false);
                    row.iWhy = x.isNull("i_why") ? "" : x.optString("i_why", "");
                    row.iMult = num(x, "i_mult"); row.iImp = num(x, "i_imp"); row.iWall = num(x, "i_wall");
                    row.iKept = num(x, "i_kept"); row.iPb = num(x, "i_pb"); row.iGive = num(x, "i_give");
                    row.iReach = num(x, "i_reach"); row.iLmv = num(x, "i_lmv"); row.iShort = x.optBoolean("i_short", false);
                    row.whyNot = x.isNull("why_not") ? "" : x.optString("why_not", "");
                }
                out.add(row);
            }
        }
        synchronized (lock) { rows = out; version++; }
    }

    public void onLiq(JSONObject m) {
        double[] x = f64(m.optString("x")); float[] b = f32(m.optString("b")), a = f32(m.optString("a"));
        synchronized (lock) { lqX = x; lqB = b; lqA = a; lqRadius = m.optInt("radius", 100); version++; }
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

    // THE MARKET POSITION BIAS (user 2026-09-26), as the engine's "mpb" sends it (tick_mpb): +1 bullish / -1 bearish =
    // the last closed candle that broke the conflict VP closed above its high / below its low (0 = none yet), and the
    // CURRENT conflict VP's yellow midline. NaN / 0 until the engine has said.
    public int mpbBias = 0; public double mpbMid = Double.NaN;

    public void onMpb(JSONObject m) {
        synchronized (lock) {
            mpbBias = m.optInt("bias", 0);
            mpbMid = m.isNull("mid") ? Double.NaN : m.optDouble("mid", Double.NaN);
            version++;
        }
    }

    // THE CONFLICT VPs (user 2026-09-26): the engine's "cvp" (tick_cvp), newest first, one row per VP:
    // [t0, t1, lo, hi, poc, vah, val, vah2, val2, live, cur, up, dn, c2t0, c2lo, ut] -- drawn from t0 to t1, low / high of
    // the two red boxes, the HLH VP's lines. cur = THE Conflict VP, the others the PREVIOUS ones (a chain back in time
    // that never overlaps); up / dn = the green / red arrow, under conflict 2's box (c2t0, c2lo); ut = 1: a FINISHED VP
    // whose area below (green) / above (red) its yellow midline price has not come back to since t1, no time limit
    // (the UNTESTED AREAS sub-toggle keeps only these and the current one).
    public static final int CVP_T0 = 0, CVP_T1 = 1, CVP_LO = 2, CVP_HI = 3, CVP_POC = 4, CVP_VAH = 5, CVP_VAL = 6,
            CVP_VAH2 = 7, CVP_VAL2 = 8, CVP_LIVE = 9, CVP_CUR = 10, CVP_UP = 11, CVP_DN = 12, CVP_C2T0 = 13, CVP_C2LO = 14,
            CVP_UT = 15, CVP_N = 16;
    public boolean cvpOn = false;
    public double[][] cvpVps = new double[0][];
    // EXPECTED TEST (user 2026-09-26): the engine's "exp" -- [x0 of its VP (its row's T0), side +1 lime / -1 purple,
    // area t0, area t1, low, high, low_cut, high_cut] per LINES IMPACT area of a VP's arrow colour that starts inside
    // that VP, only its part in the VP's expected half (below the yellow line for green, above for red); *_cut = that
    // edge is the half's (the midline / the VP's end), not the area's own
    public static final int EXP_VP = 0, EXP_SIDE = 1, EXP_T0 = 2, EXP_T1 = 3, EXP_LO = 4, EXP_HI = 5, EXP_LOCUT = 6,
            EXP_HICUT = 7, EXP_N = 8;
    public double[][] cvpExp = new double[0][];

    public void onCvp(JSONObject m) {
        boolean on = m.optBoolean("on", false);
        double[][] vps = new double[0][];
        org.json.JSONArray a = on ? m.optJSONArray("vps") : null;
        if (a != null) {
            vps = new double[a.length()][];
            for (int i = 0; i < a.length(); i++) {
                org.json.JSONArray r = a.optJSONArray(i);
                double[] v = new double[CVP_N];
                for (int k = 0; k < CVP_N; k++) v[k] = (r != null && k < r.length()) ? r.optDouble(k, Double.NaN) : Double.NaN;
                vps[i] = v;
            }
        }
        double[][] exp = new double[0][];
        org.json.JSONArray e = on ? m.optJSONArray("exp") : null;
        if (e != null) {
            exp = new double[e.length()][];
            for (int i = 0; i < e.length(); i++) {
                org.json.JSONArray r = e.optJSONArray(i);
                double[] v = new double[EXP_N];
                for (int k = 0; k < EXP_N; k++) v[k] = (r != null && k < r.length()) ? r.optDouble(k, Double.NaN) : Double.NaN;
                exp[i] = v;
            }
        }
        synchronized (lock) { cvpOn = on && vps.length > 0; cvpVps = vps; cvpExp = exp; version++; }
    }

    public void onExplain(JSONObject m) {
        synchronized (lock) { explainK = m.optDouble("k"); explainHtml = m.optString("html", ""); version++; }
    }

    /** The engine's clock, as the tablet best knows it. */
    public double nowEngine() {
        return System.currentTimeMillis() / 1000.0 + clockOffset;
    }

    /** The rolling-window flow at bin i (absolute index): the $ of the `win` seconds ending at that bin. */
    // the last series handed out, and what it was cut from: a frame that asks for the same whole seconds of the same bins
    // gets the same arrays -- the chart draws 20-30 frames a second, the bins move about 10 times
    private float[][] serOut = null; private int serVer = -1, serI0, serI1, serMax, serW;

    public void series(double t0, double t1, int maxPts, float[][] out) {
        // out[0] = t, out[1] = buy, out[2] = sell -- allocated here at the decimated length
        int w = Math.max(1, (int) Math.round(win));
        int n = binN;
        int i0 = (int) (Math.floor(t0) - binBase), i1 = (int) (Math.floor(t1) - binBase);
        i0 = Math.max(0, i0); i1 = Math.min(n - 1, i1);
        if (i1 < i0 || n == 0) { out[0] = out[1] = out[2] = new float[0]; return; }
        if (serOut != null && serVer == binsVer && serI0 == i0 && serI1 == i1 && serMax == maxPts && serW == w) {
            out[0] = serOut[0]; out[1] = serOut[1]; out[2] = serOut[2]; return;
        }
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
        serOut = new float[][]{t, b, s}; serVer = binsVer; serI0 = i0; serI1 = i1; serMax = maxPts; serW = w;
    }
}
