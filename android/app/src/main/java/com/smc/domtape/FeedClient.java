package com.smc.domtape;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.zip.Inflater;

/**
 * Background thread reading newline-delimited JSON from the feed bridge (android/bridge.py).
 * Two paths, tried in order, forever:
 *   1. USB — 127.0.0.1:8765 through `adb reverse` to the PC bridge (plain stream).
 *   2. VM  — BuildConfig.VM_HOST:8765 over Wi-Fi/4G to the bridge next to the daemon
 *      (token auth first line, then the server side of the stream is one zlib stream).
 * Folds everything into the shared {@link TradeStore}; client->server stays plain on both.
 *
 * READER (2026-09-07 — the "3.5 s clumps" fix): messages are split from RAW socket reads, each chunk
 * inflated with a streaming {@link Inflater} as soon as it arrives. The old BufferedReader(InputStreamReader(
 * InflaterInputStream)) chain delivered the VM stream in ~3.5 s bursts: InputStreamReader keeps pulling
 * bytes while the stream says data is available, and InflaterInputStream.available() answers 1 until EOF —
 * so the reader only returned once its 64 KB buffer was full (~3 s of feed at ~20 KB/s). Measured on the
 * tablet: newest trade 2.7 s old at arrival (p90 4.1 s) on the VM path vs 0.9 s on the plain USB path.
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
                Inflater inf = null;
                if (vm) {                           // authenticate, then everything downstream is zlib
                    byte[] auth = ("{\"t\":\"auth\",\"k\":\"" + BuildConfig.FEED_TOKEN
                            + "\",\"z\":1}\n").getBytes(StandardCharsets.UTF_8);
                    out.write(auth);
                    out.flush();
                    inf = new Inflater();
                }
                store.reset();                      // reconnect heal: the fresh tw rebuilds the whole
                store.setConnected(true);           // store (duplicate- and gap-free by construction)
                readLines(raw, inf);
                if (inf != null) inf.end();
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

    /**
     * Streaming line splitter: every raw socket read is (inflated and) scanned for '\n' immediately, so a
     * message is handled the moment its last byte lands — never held for a full reader buffer.
     */
    private void readLines(InputStream raw, Inflater inf) throws Exception {
        byte[] net = new byte[1 << 16];
        byte[] plain = new byte[1 << 16];
        byte[] acc = new byte[1 << 16];             // pending partial line
        int accLen = 0;
        while (!stopFlag) {
            int n = raw.read(net, 0, net.length);
            if (n < 0) return;                      // EOF -> reconnect
            if (n == 0) continue;
            if (inf == null) {
                accLen = scan(net, n, acc, accLen);
                if (accLen < 0) return;
                acc = grownAcc;                     // (may have grown)
                continue;
            }
            inf.setInput(net, 0, n);
            while (!inf.needsInput()) {
                int m = inf.inflate(plain, 0, plain.length);
                if (m == 0) {
                    if (inf.finished() || inf.needsDictionary()) return;
                    break;
                }
                accLen = scan(plain, m, acc, accLen);
                if (accLen < 0) return;
                acc = grownAcc;
            }
        }
    }

    private byte[] grownAcc;

    /** Append `n` bytes of `src` to the pending buffer, dispatch every complete line; returns the new pending length. */
    private int scan(byte[] src, int n, byte[] acc, int accLen) {
        if (accLen + n > acc.length) {
            if (accLen + n > (1 << 22)) return -1;  // a 4 MB line = garbage stream -> reconnect
            byte[] bigger = new byte[Math.max(acc.length * 2, accLen + n)];
            System.arraycopy(acc, 0, bigger, 0, accLen);
            acc = bigger;
        }
        System.arraycopy(src, 0, acc, accLen, n);
        accLen += n;
        int start = 0;
        for (int i = 0; i < accLen; i++) {
            if (acc[i] == '\n') {
                if (i > start) handle(new String(acc, start, i - start, StandardCharsets.UTF_8));
                start = i + 1;
            }
        }
        if (start > 0) {
            System.arraycopy(acc, start, acc, 0, accLen - start);
            accLen -= start;
        }
        grownAcc = acc;
        return accLen;
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
