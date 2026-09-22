package com.smc.flowtab;

import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * The line to android/flow_engine.py on the PC: 127.0.0.1:8766 through `adb reverse tcp:8766 tcp:8766`.
 * One reader thread (lines -> FlowModel), one writer thread (commands), reconnects forever.
 */
public final class EngineClient extends Thread {
    public interface Listener { void onData(); void onState(boolean connected); }

    private final FlowModel model;
    private final Listener listener;
    private volatile boolean stop = false;
    private volatile Socket sock;
    private final LinkedBlockingQueue<String> out = new LinkedBlockingQueue<>();
    private volatile double lastViewX0 = 0, lastViewX1 = 0; private volatile boolean lastFollow = true;

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
        while (!stop) {
            try (Socket s = new Socket()) {
                sock = s;
                s.setTcpNoDelay(true);
                s.connect(new InetSocketAddress("127.0.0.1", 8766), 4000);
                s.setSoTimeout(30000);
                out.clear();
                out.offer("{\"t\":\"hi\"}");
                if (lastViewX1 > lastViewX0) sendView(lastViewX0, lastViewX1, lastFollow);
                Thread writer = new Thread(() -> writeLoop(s), "engine-writer");
                writer.setDaemon(true); writer.start();
                readLoop(s);
                writer.interrupt();
            } catch (Exception e) {
                Log.i("FLOW", "engine: " + e);
            }
            synchronized (model.lock) { model.connected = false; model.version++; }
            listener.onState(false);
            if (stop) break;
            try { Thread.sleep(1500); } catch (InterruptedException ignored) { }
        }
    }

    private void writeLoop(Socket s) {
        try {
            OutputStream os = s.getOutputStream();
            while (!stop && !s.isClosed()) {
                String line = out.take();
                os.write((line + "\n").getBytes(StandardCharsets.UTF_8));
                os.flush();
            }
        } catch (Exception ignored) { }
    }

    private void readLoop(Socket s) throws Exception {
        BufferedReader br = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8), 1 << 20);
        String line;
        while (!stop && (line = br.readLine()) != null) {
            if (line.isEmpty()) continue;
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
                    default: continue;
                }
                listener.onData();
            } catch (Exception e) {
                Log.w("FLOW", "bad line: " + e);
            }
        }
    }
}
