package com.smc.domtape;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * SMC DOM — one screen: DOM ladder (left) | Trades tape (right), draggable divider between.
 * Data: the PC bridge (android/bridge.py) over `adb reverse tcp:8765 tcp:8765`, or the VM bridge.
 *
 * Frames (2026-09-06): EVENT-DRIVEN. The feed thread pokes the store listener after every book / trade
 * batch; that posts ONE coalesced frame to the main looper (a pending flag drops duplicates), so the
 * ladder repaints within one vsync of the data landing instead of up to 400 ms later on a polling
 * timer. A slow 1 s heartbeat still runs for the clocks (VP window edge, 60 s pressure strip) and
 * costs nothing when nothing changed (the panels skip the repaint on an unchanged store version).
 */
public class MainActivity extends Activity {

    private static final long HEARTBEAT_MS = 1000;

    private FeedClient feed;
    private DomPanel dom;
    private TapePanel tape;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final AtomicBoolean pending = new AtomicBoolean(false);
    private boolean resumed;

    private final Runnable dataFrame = new Runnable() {
        @Override
        public void run() {
            pending.set(false);
            if (!resumed) return;
            dom.tick(false);
            tape.tick(false);
        }
    };
    private final Runnable heartbeat = new Runnable() {
        @Override
        public void run() {
            dom.tick(true);
            tape.tick(true);
            ui.postDelayed(this, HEARTBEAT_MS);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TradeStore store = new TradeStore();
        feed = new FeedClient(store);
        dom = new DomPanel(this, store, feed);
        tape = new TapePanel(this, store);
        setContentView(new SplitPane(this, dom, tape));
        store.setListener(new Runnable() {
            @Override
            public void run() {                    // feed thread: one frame per burst of arrivals
                if (pending.compareAndSet(false, true)) ui.post(dataFrame);
            }
        });
        feed.start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        ui.post(heartbeat);
    }

    @Override
    protected void onPause() {
        super.onPause();
        resumed = false;
        ui.removeCallbacks(heartbeat);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (feed != null) feed.shutdown();
    }
}
