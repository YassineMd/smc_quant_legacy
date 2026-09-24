package com.smc.flowtab;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.text.Html;
import android.text.method.LinkMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * SMC Flow -- the Buy/Sell Flow $ scanner mode on the tablet. The panes (ChartView) on the left, the INTERPRETATION
 * feed on the right, the hamburger top right. Everything is computed by android/flow_engine.py on the PC.
 */
public final class MainActivity extends Activity implements EngineClient.Listener, ChartView.Host {
    private final FlowModel model = new FlowModel();
    private EngineClient feed;
    private ChartView chart;
    private InterpView interp;
    private SharedPreferences prefs;
    private double pendingExplain = Double.NaN;
    private AlertDialog explainDlg;
    private Button paperBtn;                  // the Paper LIVE ledger, next to the hamburger, only with Market Position on
    private Button claudeBtn;                 // "send to Claude": the screen + the market, shared with the Claude app
    private int claudeReq = 0;                // the pending request's id, 0 = none
    private android.graphics.Bitmap claudeShot;   // the screen as it was at the tap
    private final android.os.Handler ui = new android.os.Handler(android.os.Looper.getMainLooper());
    private Grip divider;                     // between the chart and the feed: drag it to resize the feed
    private float splitX0, splitW0;

    /** The divider before the feed: a hairline with a grip, draggable. */
    private static final class Grip extends View {
        boolean dark = true;
        private final android.graphics.Paint p = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        Grip(android.content.Context c) { super(c); }
        @Override protected void onDraw(android.graphics.Canvas c) {
            float d = getResources().getDisplayMetrics().density, w = getWidth(), h = getHeight();
            c.drawColor(Color.parseColor(dark ? "#141414" : "#ffffff"));
            p.setColor(Color.parseColor(dark ? "#2a2f36" : "#d0d0d0"));
            c.drawRect(w / 2 - 0.5f * d, 0, w / 2 + 0.5f * d, h, p);
            p.setColor(Color.parseColor(dark ? "#5a6470" : "#9aa0a6"));
            for (int i = -1; i <= 1; i++) c.drawCircle(w / 2, h / 2 + i * 8 * d, 2 * d, p);
        }
    }
    private FrameLayout root;
    private boolean bw = true;
    private View popupAnchor;                 // a 1x1 view moved under the I x I dropdown button so the menu drops from it
    private static final String[] MODES = {"None", "Buyer", "Seller", "Delta", "Lines Buyer/Seller"};

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("smcflow", MODE_PRIVATE);
        chart = new ChartView(this, model);
        chart.setHost(this);
        interp = new InterpView(this, model);
        chart.showLines = prefs.getBoolean("lines", true);
        chart.showPrice = prefs.getBoolean("price", true);
        chart.showFlow = prefs.getBoolean("flow", true);
        chart.showLiq = prefs.getBoolean("liq", true);
        chart.showIimp = prefs.getBoolean("iimp", true);
        chart.showCint = prefs.getBoolean("cint", false);   // new panes, off until the user asks
        chart.showCimp = prefs.getBoolean("cimp", false);
        chart.showDomPrice = prefs.getBoolean("domprice", true);
        chart.showTakeover = prefs.getBoolean("takeover", true);
        chart.showHlh = prefs.getBoolean("hlh", false);
        chart.showBp = prefs.getBoolean("bigplayer", false);
        chart.showRz = prefs.getBoolean("rz", false);
        chart.initTools(prefs, new PriceTools.Events() {
            @Override public void toast(String msg) { Toast.makeText(MainActivity.this, msg, Toast.LENGTH_SHORT).show(); }
            @Override public void changed() { chart.dataChanged(); }
        });
        chart.tools.showMarket = prefs.getBoolean("market", true);
        chart.tools.showBar = prefs.getBoolean("drawbar", true);
        bw = prefs.getBoolean("bw", true);
        interp.setVisibility(prefs.getBoolean("interp", true) ? View.VISIBLE : View.GONE);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        float split = prefs.getFloat("split", 0.74f);
        row.addView(chart, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, split));
        divider = new Grip(this);
        row.addView(divider, new LinearLayout.LayoutParams((int) Ui.dp(this, 12), ViewGroup.LayoutParams.MATCH_PARENT));
        row.addView(interp, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f - split));
        divider.setVisibility(interp.getVisibility());
        divider.setOnTouchListener((v, ev) -> {
            LinearLayout.LayoutParams cp = (LinearLayout.LayoutParams) chart.getLayoutParams(), ip = (LinearLayout.LayoutParams) interp.getLayoutParams();
            switch (ev.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN: splitX0 = ev.getRawX(); splitW0 = cp.weight; return true;
                case android.view.MotionEvent.ACTION_MOVE: {
                    float avail = Math.max(1f, row.getWidth() - divider.getWidth());
                    float f = Math.max(0.4f, Math.min(0.92f, splitW0 + (ev.getRawX() - splitX0) / avail));
                    cp.weight = f; ip.weight = 1f - f; row.requestLayout(); return true; }
                case android.view.MotionEvent.ACTION_UP: case android.view.MotionEvent.ACTION_CANCEL:
                    prefs.edit().putFloat("split", cp.weight).apply(); return true;
            }
            return false;
        });
        interp.setListener(r -> { chart.focusCycle(r.t0, r.t1); markChanged(); });
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#141414"));
        root.addView(row, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        Button menu = new Button(this);
        menu.setText("☰");
        menu.setTextColor(Color.parseColor("#dcdcdc"));
        menu.setTextSize(18);
        menu.setBackgroundColor(Color.parseColor("#20242c"));
        int sz = (int) Ui.dp(this, 40);
        FrameLayout.LayoutParams mp = new FrameLayout.LayoutParams(sz, sz, Gravity.TOP | Gravity.END);
        mp.setMargins(0, (int) Ui.dp(this, 6), (int) Ui.dp(this, 6), 0);
        root.addView(menu, mp);
        paperBtn = new Button(this);
        paperBtn.setText("\ud83d\udcc4");
        paperBtn.setTextSize(16);
        paperBtn.setBackgroundColor(Color.parseColor("#20242c"));
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(sz, sz, Gravity.TOP | Gravity.END);
        pp.setMargins(0, (int) Ui.dp(this, 6), (int) Ui.dp(this, 6) + sz + (int) Ui.dp(this, 6), 0);
        root.addView(paperBtn, pp);
        paperBtn.setOnClickListener(v -> showPaper());
        claudeBtn = new Button(this);
        claudeBtn.setText("\u2733");
        claudeBtn.setTextColor(Color.parseColor("#D97757"));
        claudeBtn.setTextSize(18);
        claudeBtn.setBackgroundColor(Color.parseColor("#20242c"));
        root.addView(claudeBtn, new FrameLayout.LayoutParams(sz, sz, Gravity.TOP | Gravity.END));
        claudeBtn.setOnClickListener(v -> askClaude());
        applyStyle();
        popupAnchor = new View(this);
        root.addView(popupAnchor, new FrameLayout.LayoutParams(1, 1, Gravity.TOP | Gravity.START));
        menu.setOnClickListener(v -> showMenu());
        setContentView(root);

        feed = new EngineClient(model, this);
        feed.start();
        sendToggles();
    }

    /** The engine learns the tablet's toggles that concern it (its offscreen terminal runs those layers). */
    private void sendToggles() {
        feed.sendToggle("lines", chart.showLines);
        feed.sendToggle("takeover", chart.showTakeover);
        feed.sendToggle("hlh", chart.showHlh);
        feed.sendToggle("bigplayer", chart.showBp);
        feed.sendToggle("rz", chart.showRz);
        feed.sendToggle("bw", bw);
        markKnown = false;                          // the engine may have restarted: tell it the mark again
        if (interp != null) markChanged();
    }

    /** Chart Style + which top-right buttons show: Simple BW themes every pane, the feed and the ground. */
    private void applyStyle() {
        chart.bw = bw; interp.setDark(!bw);
        if (divider != null) { divider.dark = !bw; divider.invalidate(); }
        root.setBackgroundColor(Color.parseColor(bw ? "#ffffff" : "#141414"));
        paperBtn.setVisibility(chart.tools.showMarket ? View.VISIBLE : View.GONE);
        // the Claude button sits left of the paper button, or of the hamburger when the paper button is hidden
        int sz = (int) Ui.dp(this, 40), g = (int) Ui.dp(this, 6);
        FrameLayout.LayoutParams cp = (FrameLayout.LayoutParams) claudeBtn.getLayoutParams();
        cp.setMargins(0, g, g + (sz + g) * (chart.tools.showMarket ? 2 : 1), 0);
        claudeBtn.setLayoutParams(cp);
        chart.dataChanged();
    }

    private void toast(String msg) { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show(); }

    /** THE "SEND TO CLAUDE" BUTTON (user 2026-09-24: "my tablet can communicate the info with the Claude app on my
     *  tablet"). The screen is captured at the tap; the engine answers with the reading instructions (the user's
     *  auction doctrine, the screen, the fields) and a FRESH snapshot of the market; both go to the Claude app as a
     *  screenshot + one markdown file, with a first message ready in its composer. Nothing is sent to Claude until the
     *  user presses send there. A marked card / candle makes the message about that cycle. */
    private void askClaude() {
        boolean conn;
        synchronized (model.lock) { conn = model.connected; }
        if (!conn) { toast("Not connected to the engine"); return; }
        if (claudeReq != 0) { toast("Already preparing\u2026"); return; }
        claudeShot = snapshotScreen();
        final int id = (int) (System.currentTimeMillis() & 0x3fffffffL) | 1;
        claudeReq = id;
        feed.sendClaude(id, interp.selT0);
        toast("Preparing the market for Claude\u2026");
        ui.postDelayed(() -> {
            if (claudeReq == id) { claudeReq = 0; claudeShot = null; toast("The engine did not answer \u2014 try again"); }
        }, 20000);
    }

    private android.graphics.Bitmap snapshotScreen() {
        try {
            if (root.getWidth() <= 0 || root.getHeight() <= 0) return null;
            android.graphics.Bitmap bm = android.graphics.Bitmap.createBitmap(root.getWidth(), root.getHeight(), android.graphics.Bitmap.Config.ARGB_8888);
            root.draw(new android.graphics.Canvas(bm));
            return bm;
        } catch (Throwable t) {
            return null;                           // the share still goes, without the picture
        }
    }

    /** The engine's reply is in: write the pack (off the UI thread) and hand it to the Claude app. */
    private void shareToClaude() {
        boolean ok; String prompt, snap, gen, err, hlh, sel;
        synchronized (model.lock) {
            ok = model.claudeOk; prompt = model.claudePrompt; snap = model.claudeSnap; gen = model.claudeGen;
            err = model.claudeErr; hlh = model.claudeHlh; sel = model.claudeSel;
        }
        final android.graphics.Bitmap shot = claudeShot;
        claudeShot = null;
        if (!ok) { toast("Engine: " + err); return; }
        final String msg = (sel != null && sel.length() >= 19)
                ? "Look at the cycle I marked on my SMC Flow tablet \u2014 it started at " + sel.substring(11) + " UTC. "
                  + "What happened there, in my auction terms? My instructions and the market data are in the attached "
                  + "file; the screenshot is my screen at the same moment."
                : "Read my SOLUSDT auction right now. My instructions and the market data are in the attached file; "
                  + "the screenshot is my SMC Flow tablet at the same moment.";
        new Thread(() -> {
            try {
                final java.util.ArrayList<android.net.Uri> uris = ShareProvider.write(this, gen, shot, msg, prompt, snap);
                runOnUiThread(() -> launchClaude(uris, msg, hlh));
            } catch (Exception e) {
                runOnUiThread(() -> toast("Could not prepare the files: " + e.getMessage()));
            }
        }, "claude-share").start();
    }

    private void launchClaude(java.util.ArrayList<android.net.Uri> uris, String msg, String hlh) {
        android.content.Intent it = new android.content.Intent(android.content.Intent.ACTION_SEND_MULTIPLE);
        it.setType("*/*");
        it.putParcelableArrayListExtra(android.content.Intent.EXTRA_STREAM, uris);
        // ⚠ the Claude app does NOT prefill its composer from a multi-file share -- a String or a CharSequence list
        // both tried on the device (2026-09-24). The request is written at the top of the markdown file instead;
        // this stays for the chooser fallback (other apps do read it)
        it.putExtra(android.content.Intent.EXTRA_TEXT, msg);
        android.content.ClipData cd = android.content.ClipData.newRawUri("", uris.get(0));
        for (int i = 1; i < uris.size(); i++) cd.addItem(new android.content.ClipData.Item(uris.get(i)));
        it.setClipData(cd);
        it.addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
        it.setPackage("com.anthropic.claude");
        try {
            startActivity(it);
        } catch (android.content.ActivityNotFoundException e) {
            it.setPackage(null);                   // no Claude app: let the user pick where it goes
            startActivity(android.content.Intent.createChooser(it, "Send the market to\u2026"));
        }
        if (!"on".equals(hlh)) toast("HLH Volume Profile is off \u2014 the share has no value reference");
    }

    /** The Paper LIVE panel: title, balance, Clear, one line per closed trade (newest first). */
    private void showPaper() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setBackgroundColor(Color.parseColor("#141a22"));
        int p = (int) Ui.dp(this, 10);
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setPadding(p, p, p, p);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = new TextView(this);
        title.setText("Paper \u2014 LIVE"); title.setTextColor(Color.parseColor("#dcdcdc")); title.setTextSize(15); title.setTypeface(null, android.graphics.Typeface.BOLD);
        TextView bal = new TextView(this);
        bal.setText(String.format(java.util.Locale.US, "%,.0f$", chart.tools.balance)); bal.setTextColor(Color.parseColor("#9aa0a6")); bal.setTextSize(14);
        bal.setPadding(p, 0, p, 0);
        Button clear = new Button(this);
        clear.setText("Clear"); clear.setTextSize(12); clear.setTextColor(Color.parseColor("#dcdcdc")); clear.setBackgroundColor(Color.parseColor("#2a3140"));
        head.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        head.addView(bal); head.addView(clear);
        col.addView(head);
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(p, 0, p, p);
        java.util.List<String[]> rows = new java.util.ArrayList<>(chart.tools.ledger);
        if (rows.isEmpty()) {
            TextView tv = new TextView(this); tv.setText("no closed paper trades yet"); tv.setTextColor(Color.parseColor("#6f7a82")); tv.setTextSize(13); tv.setTypeface(android.graphics.Typeface.MONOSPACE);
            list.addView(tv);
        }
        for (String[] r : rows) {
            TextView tv = new TextView(this);
            tv.setText(r[0]); tv.setTextColor(Color.parseColor(r[1])); tv.setTextSize(13); tv.setTypeface(android.graphics.Typeface.MONOSPACE);
            tv.setPadding(0, (int) Ui.dp(this, 3), 0, (int) Ui.dp(this, 3));
            list.addView(tv);
        }
        ScrollView sv = new ScrollView(this);
        sv.addView(list);
        col.addView(sv, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, (int) Ui.dp(this, 420)));
        AlertDialog dlg = new AlertDialog.Builder(this).setView(col).create();
        clear.setOnClickListener(v -> { chart.tools.clearPaper(); dlg.dismiss(); showPaper(); });
        dlg.show();
        if (dlg.getWindow() != null) dlg.getWindow().setLayout((int) Ui.dp(this, 640), ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        if (feed != null) feed.shutdown();
    }

    // ---- EngineClient.Listener (feed thread)
    @Override public void onData() {
        runOnUiThread(() -> {
            chart.dataChanged();
            interp.refresh();
            if (claudeReq != 0) {
                int cid;
                synchronized (model.lock) { cid = model.claudeId; }
                if (cid == claudeReq) { claudeReq = 0; shareToClaude(); }
            }
            String html; double k;
            synchronized (model.lock) { html = model.explainHtml; k = model.explainK; }
            if (html != null && !Double.isNaN(pendingExplain) && Math.abs(k - pendingExplain) < 1.0) {
                pendingExplain = Double.NaN;
                showExplain(html);
            }
        });
    }

    @Override public void onState(boolean connected) {
        runOnUiThread(() -> {
            chart.dataChanged(); interp.refresh();
            if (connected) sendToggles();
        });
    }

    // ---- ChartView.Host (UI thread)
    @Override public void onViewChanged(double x0, double x1, boolean follow) { feed.sendView(x0, x1, follow); }

    @Override public void onExplain(double k) {
        pendingExplain = k;
        synchronized (model.lock) { model.explainHtml = null; }
        feed.sendExplain(k);
    }

    @Override public void onModeMenu(float x, float y) {
        FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) popupAnchor.getLayoutParams();
        lp.leftMargin = (int) (chart.getLeft() + x); lp.topMargin = (int) (chart.getTop() + y);
        popupAnchor.setLayoutParams(lp);
        PopupMenu pm = new PopupMenu(this, popupAnchor, Gravity.START);
        String cur; synchronized (model.lock) { cur = model.iimpMode; }
        for (String m : MODES) pm.getMenu().add(m.equals(cur) ? "✓ " + m : "    " + m);
        pm.setOnMenuItemClickListener(item -> { feed.sendMode(item.getTitle().toString().substring(2).trim()); return true; });
        pm.show();
    }

    @Override public void onFullscreen(boolean on) {
        interp.setVisibility(on || !prefs.getBoolean("interp", true) ? View.GONE : View.VISIBLE);
        divider.setVisibility(interp.getVisibility());
    }

    @Override public void onCycleTap(double t0) { interp.select(t0); markChanged(); }

    /** The marked cycle (interp.selT0) goes to the engine whenever it changes -- the Claude connector's "the cycle I
     *  marked". Sent only on a change, and again on every reconnect (sendToggles). */
    private double markSent = Double.NaN;
    private boolean markKnown = false;
    private void markChanged() {
        double m = interp.selT0;
        boolean same = markKnown && (Double.isNaN(m) ? Double.isNaN(markSent) : (!Double.isNaN(markSent) && Math.abs(m - markSent) < 1e-6));
        if (same) return;
        markSent = m; markKnown = true;
        feed.sendMark(m);
    }

    @Override public void onSmooth(String kind, int n) { feed.sendSmooth(kind, n); }

    /** The I x I explain panel: the terminal's words re-set as a card (ExplainCard); any tap on it closes it. */
    private void showExplain(String html) {
        if (explainDlg != null && explainDlg.isShowing()) explainDlg.dismiss();
        explainDlg = new AlertDialog.Builder(this).create();
        View card = ExplainCard.build(this, html, v -> explainDlg.dismiss());
        explainDlg.setView(card);
        explainDlg.setOnDismissListener(dlg -> chart.clearSelection());
        explainDlg.show();
        if (explainDlg.getWindow() != null) {
            explainDlg.getWindow().setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
            explainDlg.getWindow().setLayout((int) Ui.dp(this, 700), ViewGroup.LayoutParams.WRAP_CONTENT);
        }
    }

    private void showMenu() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding((int) Ui.dp(this, 12), (int) Ui.dp(this, 8), (int) Ui.dp(this, 12), (int) Ui.dp(this, 8));
        col.setBackgroundColor(Color.parseColor("#141a22"));
        section(col, "Panes");
        toggle(col, "Cycle start lines", "lines", chart.showLines, v -> { chart.showLines = v; feed.sendToggle("lines", v); });
        toggle(col, "Price", "price", chart.showPrice, v -> chart.showPrice = v);
        toggle(col, "Buy/Sell Flow", "flow", chart.showFlow, v -> chart.showFlow = v);
        toggle(col, "Limit orders", "liq", chart.showLiq, v -> chart.showLiq = v);
        toggle(col, "Interest × Impact", "iimp", chart.showIimp, v -> chart.showIimp = v);
        toggle(col, "Lines Interest", "cint", chart.showCint, v -> chart.showCint = v);
        toggle(col, "Lines Impact", "cimp", chart.showCimp, v -> chart.showCimp = v);
        toggle(col, "Interpretation", "interp", interp.getVisibility() == View.VISIBLE, v -> { interp.setVisibility(v && chart.getFullscreen() < 0 ? View.VISIBLE : View.GONE); divider.setVisibility(interp.getVisibility()); });
        section(col, "Sub-widgets");
        toggle(col, "Market Position  (BUY / SELL)", "market", chart.tools.showMarket, v -> { chart.tools.showMarket = v; applyStyle(); });
        toggle(col, "Drawing toolbar", "drawbar", chart.tools.showBar, v -> { chart.tools.showBar = v; if (!v) chart.tools.tool = null; });
        section(col, "Indicator");
        toggle(col, "Big Player", "bigplayer", chart.showBp, v -> { chart.showBp = v; feed.sendToggle("bigplayer", v); });
        toggle(col, "HLH Volume Profile", "hlh", chart.showHlh, v -> { chart.showHlh = v; feed.sendToggle("hlh", v); });
        toggle(col, "Responsive Zones  (where each side held today)", "rz", chart.showRz, v -> { chart.showRz = v; feed.sendToggle("rz", v); });
        toggle(col, "Lines Impact areas on Price", "domprice", chart.showDomPrice, v -> chart.showDomPrice = v);
        section(col, "Indicator  ›  Cycle Chart");
        toggle(col, "Takeover ▲▼  (one side owns the cycle)", "takeover", chart.showTakeover, v -> { chart.showTakeover = v; feed.sendToggle("takeover", v); });
        section(col, "Chart Style");
        toggle(col, "Simple BW", "bw", bw, v -> { bw = v; applyStyle(); feed.sendToggle("bw", v); });
        ScrollView sv = new ScrollView(this);
        sv.addView(col);
        AlertDialog dlg = new AlertDialog.Builder(this).setView(sv).create();
        dlg.show();
        if (dlg.getWindow() != null) dlg.getWindow().setLayout((int) Ui.dp(this, 420), ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private interface OnTog { void set(boolean v); }

    private void section(LinearLayout col, String name) {
        TextView tv = new TextView(this);
        tv.setText(name);
        tv.setTextColor(Color.parseColor("#7d8492"));
        tv.setTextSize(12);
        tv.setPadding(0, (int) Ui.dp(this, 10), 0, (int) Ui.dp(this, 2));
        col.addView(tv);
    }

    private void toggle(LinearLayout col, String label, String key, boolean cur, OnTog on) {
        CheckBox cb = new CheckBox(this);
        cb.setText(label);
        cb.setTextColor(Color.parseColor("#cfd3da"));
        cb.setTextSize(15);
        cb.setChecked(cur);
        cb.setOnCheckedChangeListener((btn, v) -> { prefs.edit().putBoolean(key, v).apply(); on.set(v); chart.dataChanged(); });
        col.addView(cb);
    }
}
