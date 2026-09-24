package com.smc.flowtab;

import android.util.Log;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.zip.Inflater;

/**
 * The line to the engine (android/flow_engine.py). Two paths, tried in turn, forever:
 *   1. USB -- 127.0.0.1:8766 through `adb reverse` to the engine on the PC (plain stream).
 *   2. VM  -- BuildConfig.VM_HOST:8766 over Wi-Fi / 4G to the engine next to the daemon: the first line is
 *      {"t":"auth","k":token,"z":1}, then everything the engine sends is ONE zlib stream, inflated as it arrives
 *      (a streaming Inflater on raw socket reads -- the DOM app's FeedClient fix, never a reader chain).
 * One reader thread (lines -> FlowModel), one writer thread (commands), the last view re-sent on reconnect.
 */
public final class EngineClient extends Thread {
    public interface Listener { void onData(); void onState(boolean connected); }

    private final FlowModel model;
    private final Listener listener;
    private volatile boolean stop = false;
    private volatile Socket sock;
    // ⚠ ONE OUTBOX PER CONNECTION (2026-09-23). The outbox used to be a single queue shared by every connection's
    // writer thread, and a writer was only interrupted when its connection ended CLEANLY. A connection that died
    // with an exception -- the 30 s read timeout after the tablet slept, a network change -- left its writer
    // alive, blocked on the shared queue; it then took the NEXT connection's "hi" and wrote it into the dead
    // socket. The engine saw the auth line and never a "hi", sent nothing, the read timed out again, and every
    // reconnect after that lost its "hi" the same way: stuck "connecting" until the app was restarted (seen
    // live, 5 reconnects 31 s apart). Now each connection gets a fresh queue that only its own writer reads,
    // and the writer is interrupted however the connection ends.
    private volatile LinkedBlockingQueue<String> out = new LinkedBlockingQueue<>();
    private volatile double lastViewX0 = 0, lastViewX1 = 0; private volatile boolean lastFollow = true;
    public volatile String path = "";                // "USB" / "VM" while connected, for the UI

    public EngineClient(FlowModel model, Listener listener) {
        super("engine-client");
        this.model = model; this.listener = listener; setDaemon(true);
    }

    public void shutdown() {
        stop = true;
        Socket s = sock;
        if (s != null) try { s.close(); } catch (Exception ignored) { }
        interrupt();
    }

    // ---- commands (any thread)
    public void send(JSONObject o) { out.offer(o.toString()); }

    public void sendView(double x0, double x1, boolean follow) {
        lastViewX0 = x0; lastViewX1 = x1; lastFollow = follow;
        try {
            JSONObject o = new JSONObject();
            o.put("t", "view"); o.put("x0", x0); o.put("x1", x1); o.put("follow", follow);
            send(o);
        } catch (Exception ignored) { }
    }

    /** A smoothing slider moved: "iimp" (Lines Buyer/Seller), "cint" or "cimp". */
    public void sendSmooth(String kind, int n) {
        try {
            send(new JSONObject().put("t", "smooth").put("k", kind).put("n", n));
        } catch (Exception ignored) { }
    }

    public void sendMode(String v) {
        try { JSONObject o = new JSONObject(); o.put("t", "mode"); o.put("v", v); send(o); } catch (Exception ignored) { }
    }

    public void sendToggle(String k, boolean v) {
        try { JSONObject o = new JSONObject(); o.put("t", "tog"); o.put("k", k); o.put("v", v); send(o); } catch (Exception ignored) { }
    }

    public void sendExplain(double k) {
        try { JSONObject o = new JSONObject(); o.put("t", "explain"); o.put("k", k); send(o); } catch (Exception ignored) { }
    }

    /** The marked card / candle changed (NaN = cleared): the engine keeps it in the snapshot the Claude connector
     *  reads, so "explain the cycle I marked" works from the Claude app. */
    public void sendMark(double t0) {
        try {
            JSONObject o = new JSONObject(); o.put("t", "mark");
            o.put("t0", Double.isNaN(t0) ? JSONObject.NULL : (Object) t0);
            send(o);
        } catch (Exception ignored) { }
    }

    /** The "send to Claude" button: ask the engine for the reading instructions + a fresh auction snapshot. sel = the
     *  marked cycle's start (NaN = none). The reply carries the same id. */
    public void sendClaude(int id, double sel) {
        try {
            JSONObject o = new JSONObject(); o.put("t", "claude"); o.put("id", id);
            if (!Double.isNaN(sel)) o.put("sel", sel);
            send(o);
        } catch (Exception ignored) { }
    }

    @Override public void run() {
        boolean haveVm = !BuildConfig.VM_HOST.isEmpty();
        int which = 0;                                   // 0 = USB, 1 = VM; USB gets the first shot each cycle
        while (!stop) {
            boolean vm = haveVm && which == 1;
            Thread writer = null;
            Inflater inf = null;
            try (Socket s = new Socket()) {
                sock = s;
                s.setTcpNoDelay(true);
                s.connect(new InetSocketAddress(vm ? BuildConfig.VM_HOST : "127.0.0.1", 8766), vm ? 6000 : 2500);
                s.setSoTimeout(30000);
                OutputStream os = s.getOutputStream();
                if (vm) {
                    os.write(("{\"t\":\"auth\",\"k\":\"" + BuildConfig.FLOW_TOKEN + "\",\"z\":1}\n").getBytes(StandardCharsets.UTF_8));
                    os.flush();
                    inf = new Inflater();
                }
                path = vm ? "VM" : "USB";
                // this connection's own outbox: "hi" first, then the last view; nothing an older writer can reach
                final LinkedBlockingQueue<String> q = new LinkedBlockingQueue<>();
                q.offer("{\"t\":\"hi\"}");
                out = q;
                if (lastViewX1 > lastViewX0) sendView(lastViewX0, lastViewX1, lastFollow);
                writer = new Thread(() -> writeLoop(s, os, q), "engine-writer");
                writer.setDaemon(true); writer.start();
                readLines(s.getInputStream(), inf);
            } catch (Exception e) {
                Log.i("FLOW", "engine (" + (vm ? "VM" : "USB") + "): " + e);
            } finally {
                // however the connection ended -- a clean close, a timeout, a dead network -- its writer goes too
                if (writer != null) writer.interrupt();
                if (inf != null) inf.end();
            }
            path = "";
            synchronized (model.lock) { model.connected = false; model.version++; }
            listener.onState(false);
            if (stop) break;
            which = haveVm ? (which + 1) % 2 : 0;
            try { Thread.sleep(which == 0 ? 1200 : 300); } catch (InterruptedException ignored) { }
        }
    }

    private void writeLoop(Socket s, OutputStream os, LinkedBlockingQueue<String> q) {
        try {
            while (!stop && !s.isClosed()) {
                String line = q.take();
                os.write((line + "\n").getBytes(StandardCharsets.UTF_8));
                os.flush();
            }
        } catch (Exception ignored) { }
    }

    // ---- the reader: raw socket reads, (inflated,) split on newlines the moment they land
    private byte[] acc = new byte[1 << 20];
    private int accLen = 0;

    private void readLines(InputStream raw, Inflater inf) throws Exception {
        byte[] net = new byte[1 << 16];
        byte[] plain = new byte[1 << 17];
        accLen = 0;
        while (!stop) {
            int n = raw.read(net, 0, net.length);
            if (n < 0) return;
            if (n == 0) continue;
            if (inf == null) { if (!scan(net, n)) return; continue; }
            inf.setInput(net, 0, n);
            while (!inf.needsInput()) {
                int m = inf.inflate(plain, 0, plain.length);
                if (m == 0) { if (inf.finished() || inf.needsDictionary()) return; break; }
                if (!scan(plain, m)) return;
            }
        }
    }

    /** Append n bytes and dispatch every complete line; false = a runaway line (garbage stream). */
    private boolean scan(byte[] src, int n) {
        if (accLen + n > acc.length) {
            if (accLen + n > (1 << 26)) return false;            // a 64 MB line: not ours
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
        if (start > 0) { System.arraycopy(acc, start, acc, 0, accLen - start); accLen -= start; }
        return true;
    }

    private void handle(String line) {
        try {
            JSONObject m = new JSONObject(line);
            String t = m.optString("t");
            switch (t) {
                case "hello": model.onHello(m); listener.onState(true); break;
                case "bins": model.onBins(m); break;
                case "cyc": model.onCycles(m); break;
                case "live": model.onLive(m); break;
                case "iimp": model.onIimp(m); break;
                case "cint": model.onLines("cint", m); break;
                case "cimp": model.onLines("cimp", m); break;
                case "interp": model.onInterp(m); break;
                case "liq": model.onLiq(m); break;
                case "tko": model.onTko(m); break;
                case "explain": model.onExplain(m); break;
                case "claude": model.onClaude(m); break;
                case "hlh": model.onHlh(m); break;
                case "bp": model.onBp(m); break;
                default: return;
            }
            listener.onData();
        } catch (Exception e) {
            Log.w("FLOW", "bad line: " + e);
        }
    }
}
