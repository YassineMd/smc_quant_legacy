package com.smc.domtape;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

/**
 * SMC DOM — one screen: DOM ladder (left) | Trades tape (right), draggable divider between.
 * Data: the PC bridge (android/bridge.py) over `adb reverse tcp:8765 tcp:8765`.
 */
public class MainActivity extends Activity {

    private static final long FRAME_MS = 400;      // the terminal's pulse/repaint cadence

    private FeedClient feed;
    private DomPanel dom;
    private TapePanel tape;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Runnable frame = new Runnable() {
        @Override
        public void run() {
            dom.tick();
            tape.tick();
            ui.postDelayed(this, FRAME_MS);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TradeStore store = new TradeStore();
        dom = new DomPanel(this, store);
        tape = new TapePanel(this, store);
        setContentView(new SplitPane(this, dom, tape));
        feed = new FeedClient(store);
        feed.start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        ui.post(frame);
    }

    @Override
    protected void onPause() {
        super.onPause();
        ui.removeCallbacks(frame);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (feed != null) feed.shutdown();
    }
}
