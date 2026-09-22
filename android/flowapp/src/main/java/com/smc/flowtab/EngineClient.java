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
    private final LinkedBlockingQueue<String> out = new LinkedBlockingQueue<>();
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

    public void sendMode(String v) {
        try { JSONObject o = new JSONObject(); o.put("t", "mode"); o.put("v", v); send(o); } catch (Exception ignored) { }
    }

    public void sendToggle(String k, boolean v) {
        try { JSONObject o = new JSONObject(); o.put("t", "tog"); o.put("k", k); o.put("v", v); send(o); } catch (Exception ignored) { }
    }

    public void sendExplain(double k) {
        try { JSONObject o = new JSONObject(); o.put("t", "explain"); o.put("k", k); send(o); } catch (Exception ignored) { }
    }

    @Override public void run() {
        boolean haveVm = !BuildConfig.VM_HOST.isEmpty();
        int which = 0;                                   // 0 = USB, 1 = VM; USB gets the first shot each cycle
        while (!stop) {
            boolean vm = haveVm && which == 1;
            try (Socket s = new Socket()) {
                sock = s;
                s.setTcpNoDelay(true);
                s.connect(new InetSocketAddress(vm ? BuildConfig.VM_HOST : "127.0.0.1", 8766), vm ? 6000 : 2500);
                s.setSoTimeout(30000);
                OutputStream os = s.getOutputStream();
                Inflater inf = null;
                if (vm) {
                    os.write(("{\"t\":\"auth\",\"k\":\"" + BuildConfig.FLOW_TOKEN + "\",\"z\":1}\n").getBytes(StandardCharsets.UTF_8));
                    os.flush();
                    inf = new Inflater();
                }
                path = vm ? "VM" : "USB";
                out.clear();
                out.offer("{\"t\":\"hi\"}");
                if (lastViewX1 > lastViewX0) sendView(lastViewX0, lastViewX1, lastFollow);
                Thread writer = new Thread(() -> writeLoop(s, os), "engine-writer");
                writer.setDaemon(true); writer.start();
                readLines(s.getInputStream(), inf);
                if (inf != null) inf.end();
                writer.interrupt();
            } catch (Exception e) {
                Log.i("FLOW", "engine (" + (vm ? "VM" : "USB") + "): " + e);
            }
            path = "";
            synchronized (model.lock) { model.connected = false; model.version++; }
            listener.onState(false);
            if (stop) break;
            which = haveVm ? (which + 1) % 2 : 0;
            try { Thread.sleep(which == 0 ? 1200 : 300); } catch (InterruptedException ignored) { }
        }
    }

    private void writeLoop(Socket s, OutputStream os) {
        try {
            while (!stop && !s.isClosed()) {
                String line = out.take();
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
                case "interp": model.onInterp(m); break;
                case "liq": model.onLiq(m); break;
                case "tko": model.onTko(m); break;
                case "explain": model.onExplain(m); break;
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
