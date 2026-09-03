package com.smc.domtape;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Background thread reading newline-delimited JSON from the PC bridge (android/bridge.py) at
 * 127.0.0.1:8765 — reached through the USB cable via `adb reverse tcp:8765 tcp:8765`.
 * Folds everything into the shared {@link TradeStore}; reconnects forever on any error.
 */
public class FeedClient extends Thread {

    public final TradeStore store;
    private volatile boolean stopFlag;

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
        while (!stopFlag) {
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress("127.0.0.1", 8765), 4000);
                s.setTcpNoDelay(true);
                s.setSoTimeout(15000);              // bridge pushes books every 0.4s — silence = dead
                store.setConnected(true);
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8), 1 << 16);
                String line;
                while (!stopFlag && (line = in.readLine()) != null) {
                    handle(line);
                }
            } catch (Exception ignored) {
                // fall through to reconnect
            }
            store.setConnected(false);
            if (!stopFlag) {
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException e) {
                    // loop re-checks stopFlag
                }
            }
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
