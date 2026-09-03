package com.smc.domtape;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.zip.InflaterInputStream;

/**
 * Background thread reading newline-delimited JSON from the feed bridge (android/bridge.py).
 * Two paths, tried in order, forever:
 *   1. USB — 127.0.0.1:8765 through `adb reverse` to the PC bridge (plain stream).
 *   2. VM  — BuildConfig.VM_HOST:8765 over Wi-Fi/4G to the bridge next to the daemon
 *      (token auth first line, then the server side of the stream is one zlib stream).
 * Folds everything into the shared {@link TradeStore}; client->server stays plain on both.
 */
public class FeedClient extends Thread {

    public final TradeStore store;
    private volatile boolean stopFlag;
    private volatile OutputStream out;             // live socket's write side (control requests)

    public FeedClient(TradeStore store) {
        super("feed-client");
        this.store = store;
        setDaemon(true);
    }

    public void shutdown() {
        stopFlag = true;
        interrupt();
    }

    @Override
    public void run() {
        boolean haveVm = !BuildConfig.VM_HOST.isEmpty();
        int which = 0;                              // 0 = USB, 1 = VM; USB gets first shot each cycle
        while (!stopFlag) {
            boolean vm = haveVm && which == 1;
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress(vm ? BuildConfig.VM_HOST : "127.0.0.1", 8765),
                        vm ? 6000 : 2500);
                s.setTcpNoDelay(true);
                s.setSoTimeout(15000);              // bridge pushes books every 0.4s — silence = dead
                out = s.getOutputStream();
                InputStream raw = s.getInputStream();
                if (vm) {                           // authenticate, then everything downstream is zlib
                    byte[] auth = ("{\"t\":\"auth\",\"k\":\"" + BuildConfig.FEED_TOKEN
                            + "\",\"z\":1}\n").getBytes(StandardCharsets.UTF_8);
                    out.write(auth);
                    out.flush();
                    raw = new InflaterInputStream(raw);
                }
                store.reset();                      // reconnect heal: the fresh tw rebuilds the whole
                store.setConnected(true);           // store (duplicate- and gap-free by construction)
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(raw, StandardCharsets.UTF_8), 1 << 16);
                String line;
                while (!stopFlag && (line = in.readLine()) != null) {
                    handle(line);
                }
            } catch (Exception ignored) {
                // fall through to the other path
            }
            out = null;
            store.setConnected(false);
            if (!stopFlag) {
                which = haveVm ? (which + 1) % 2 : 0;
                try {
                    Thread.sleep(which == 0 ? 1200 : 300);   // quick USB->VM handoff, calmer cycles
                } catch (InterruptedException e) {
                    // loop re-checks stopFlag
                }
            }
        }
    }

    /** Custom-VP deep fetch: ask the bridge for tape history down to t0 (epoch ms). Best-effort. */
    public void requestFetch(long t0Ms) {
        OutputStream o = out;
        if (o == null) return;
        try {
            byte[] req = ("{\"t\":\"fetch\",\"t0\":" + t0Ms + "}\n").getBytes(StandardCharsets.UTF_8);
            synchronized (this) {
                o.write(req);
                o.flush();
            }
        } catch (Exception ignored) {
            // reconnect loop will heal; the user can re-pick the custom start
        }
    }

    private void handle(String line) {
        try {
            JSONObject m = new JSONObject(line);
            String t = m.optString("t");
            if ("book".equals(t)) {
                JSONArray b = m.getJSONArray("b");
                JSONArray a = m.getJSONArray("a");
                double[][] bids = parseLevels(b);
                double[][] asks = parseLevels(a);
                store.setBook(bids, asks, m.optDouble("px", 0.0));
            } else if ("tb".equals(t)) {
                Trades tr = parseTrades(m);
                if (tr != null) store.ingestLive(tr);
            } else if ("tw".equals(t)) {
                Trades tr = parseTrades(m);
                if (tr != null) store.ingestWindow(tr);
            }
            // "hello"/"thr" carry nothing the panels need yet (tick is a fixed 0.01)
        } catch (Exception ignored) {
            // one garbled line never kills the feed
        }
    }

    private static double[][] parseLevels(JSONArray arr) throws Exception {
        double[][] out = new double[arr.length()][2];
        for (int i = 0; i < arr.length(); i++) {
            JSONArray lv = arr.getJSONArray(i);
            out[i][0] = lv.getDouble(0);
            out[i][1] = lv.getDouble(1);
        }
        return out;
    }

    /** Decoded parallel trade arrays: ts epoch ms, price, qty, side (1 = taker buy). */
    static final class Trades {
        final long[] tsMs;
        final double[] px;
        final double[] qty;
        final byte[] side;

        Trades(long[] tsMs, double[] px, double[] qty, byte[] side) {
            this.tsMs = tsMs;
            this.px = px;
            this.qty = qty;
            this.side = side;
        }
    }

    private static Trades parseTrades(JSONObject m) throws Exception {
        byte[] ts = Base64.getDecoder().decode(m.optString("ts", ""));
        byte[] px = Base64.getDecoder().decode(m.optString("px", ""));
        byte[] q = Base64.getDecoder().decode(m.optString("q", ""));
        byte[] sd = Base64.getDecoder().decode(m.optString("sd", ""));
        int n = Math.min(Math.min(ts.length / 8, px.length / 8), Math.min(q.length / 8, sd.length));
        if (n <= 0) return null;
        long[] tsA = new long[n];
        double[] pxA = new double[n];
        double[] qA = new double[n];
        byte[] sdA = new byte[n];
        ByteBuffer tb = ByteBuffer.wrap(ts).order(ByteOrder.LITTLE_ENDIAN);
        ByteBuffer pb = ByteBuffer.wrap(px).order(ByteOrder.LITTLE_ENDIAN);
        ByteBuffer qb = ByteBuffer.wrap(q).order(ByteOrder.LITTLE_ENDIAN);
        for (int i = 0; i < n; i++) {
            tsA[i] = tb.getLong(i * 8);
            pxA[i] = pb.getDouble(i * 8);
            qA[i] = qb.getDouble(i * 8);
            sdA[i] = sd[i];
        }
        return new Trades(tsA, pxA, qA, sdA);
    }
}
