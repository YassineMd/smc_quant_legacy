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
        // ⚠ SPEED (2026-09-26): the feed is text-heavy and changes about once a second, while the chart beside it redraws
        // 20-30 times a second -- and every frame re-issued all of the feed's text to the GPU. As a hardware layer it is
        // one texture per frame until the feed itself changes.
        interp.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        chart.showLines = prefs.getBoolean("lines", true);
        chart.showPrice = prefs.getBoolean("price", true);
        chart.showFlow = prefs.getBoolean("flow", true);
        chart.showLiq = prefs.getBoolean("liq", true);
        chart.showIimp = prefs.getBoolean("iimp", true);
        chart.showCint = prefs.getBoolean("cint", false);   // new panes, off until the user asks
        chart.showCimp = prefs.getBoolean("cimp", false);
        chart.showKept = prefs.getBoolean("kept", true);    // KEPT TICKS BY LEADER (user 2026-09-25)
        chart.showDomPrice = prefs.getBoolean("domprice", true);
        chart.showTakeover = prefs.getBoolean("takeover", true);
        chart.showHlh = prefs.getBoolean("hlh", false);
        chart.showCvp = prefs.getBoolean("cvp", true);      // CONFLICT VP (user 2026-09-26), on until the user turns it off
        chart.showCvpPrev = prefs.getBoolean("cvp_prev", false);   // ... its previous ones, off until the user asks
        chart.showBp = prefs.getBoolean("bigplayer", false);
        bpMin = prefs.getFloat("bp_min", (float) BP_DEFAULT);
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
        feed.sendBpMin(bpMin);
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
        chart.dataChanged();
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
            chart.dataChanged(); interp.refresh(true);
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
        toggle(col, "Kept ticks by leader", "kept", chart.showKept, v -> chart.showKept = v);
        toggle(col, "Interpretation", "interp", interp.getVisibility() == View.VISIBLE, v -> { interp.setVisibility(v && chart.getFullscreen() < 0 ? View.VISIBLE : View.GONE); divider.setVisibility(interp.getVisibility()); });
        section(col, "Sub-widgets");
        toggle(col, "Market Position  (BUY / SELL)", "market", chart.tools.showMarket, v -> { chart.tools.showMarket = v; applyStyle(); });
        toggle(col, "Drawing toolbar", "drawbar", chart.tools.showBar, v -> { chart.tools.showBar = v; if (!v) chart.tools.tool = null; });
        section(col, "Indicator");
        toggle(col, "Big Player", "bigplayer", chart.showBp, v -> { chart.showBp = v; feed.sendToggle("bigplayer", v); });
        bpSlider(col);
        toggle(col, "HLH Volume Profile", "hlh", chart.showHlh, v -> { chart.showHlh = v; feed.sendToggle("hlh", v); });
        // THE PREVIOUS CONFLICT VPs (user 2026-09-26: "it should be under the conflict VP indicator, inside it so we
        // seperate it from the other indicators"): an indented sub-toggle, greyed out while the Conflict VP is off
        CheckBox[] cvpPrev = new CheckBox[1];
        toggle(col, "Conflict VP  (the last two conflict boxes)", "cvp", chart.showCvp, v -> {
            chart.showCvp = v;
            if (cvpPrev[0] != null) cvpPrev[0].setEnabled(v);
        });
        cvpPrev[0] = toggle(col, "Previous Conflict VPs  (one colour each)", "cvp_prev", chart.showCvpPrev, v -> chart.showCvpPrev = v);
        cvpPrev[0].setTextSize(13);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.leftMargin = (int) Ui.dp(this, 30);
        cvpPrev[0].setLayoutParams(lp);
        cvpPrev[0].setEnabled(chart.showCvp);
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

    // THE BIG PLAYER MIN PRINT slider (user 2026-09-25: "add a slider for the big player under its toggle"): the terminal's
    // own (hamburger.bp_slider) -- log $50K .. $10M over 1000 steps, default the user's $500K -- so a value round-trips
    // exactly. It sets the ENGINE's threshold (its offscreen terminal draws the marks), which the Claude connector's
    // big_player_min_usd also reads. Saved in prefs ("bp_min") and re-sent on every (re)connect.
    private static final double BP_LO = 50_000.0, BP_HI = 10_000_000.0, BP_DEFAULT = 500_000.0;
    private double bpMin = BP_DEFAULT;

    private static int bpStep(double usd) {
        double c = Math.max(BP_LO, Math.min(BP_HI, usd));
        return (int) Math.round(1000.0 * (Math.log10(c) - Math.log10(BP_LO)) / (Math.log10(BP_HI) - Math.log10(BP_LO)));
    }

    private static double bpUsd(int step) {
        return Math.pow(10.0, Math.log10(BP_LO) + (step / 1000.0) * (Math.log10(BP_HI) - Math.log10(BP_LO)));
    }

    private static String bpFmt(double a) {                  // hamburger._bub_fmt_usd
        return a >= 1e6 ? String.format(java.util.Locale.US, "$%.2fM", a / 1e6) : String.format(java.util.Locale.US, "$%.0fK", a / 1e3);
    }

    private void bpSlider(LinearLayout col) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding((int) Ui.dp(this, 34), 0, 0, (int) Ui.dp(this, 4));
        TextView cap = new TextView(this);
        cap.setText("MIN PRINT");
        cap.setTextColor(Color.parseColor("#7a8496"));
        cap.setTextSize(11);
        cap.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        TextView val = new TextView(this);
        val.setText("≥ " + bpFmt(bpUsd(bpStep(bpMin))));
        val.setTextColor(Color.parseColor("#f0b90b"));
        val.setTextSize(13);
        val.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        val.setMinWidth((int) Ui.dp(this, 84));
        android.widget.SeekBar sb = new android.widget.SeekBar(this);
        sb.setMax(1000);
        sb.setProgress(bpStep(bpMin));
        android.content.res.ColorStateList gold = android.content.res.ColorStateList.valueOf(Color.parseColor("#f0b90b"));
        sb.setProgressTintList(gold); sb.setThumbTintList(gold);
        sb.setOnSeekBarChangeListener(new android.widget.SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(android.widget.SeekBar s, int p, boolean fromUser) {
                if (!fromUser) return;
                bpMin = bpUsd(p);
                val.setText("≥ " + bpFmt(bpMin));
            }
            @Override public void onStartTrackingTouch(android.widget.SeekBar s) { }
            @Override public void onStopTrackingTouch(android.widget.SeekBar s) {   // one rebuild per drag, not one per step
                prefs.edit().putFloat("bp_min", (float) bpMin).apply();
                feed.sendBpMin(bpMin);
            }
        });
        row.addView(cap);
        row.addView(sb, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(val);
        col.addView(row);
    }

    private void section(LinearLayout col, String name) {
        TextView tv = new TextView(this);
        tv.setText(name);
        tv.setTextColor(Color.parseColor("#7d8492"));
        tv.setTextSize(12);
        tv.setPadding(0, (int) Ui.dp(this, 10), 0, (int) Ui.dp(this, 2));
        col.addView(tv);
    }

    private CheckBox toggle(LinearLayout col, String label, String key, boolean cur, OnTog on) {
        CheckBox cb = new CheckBox(this);
        cb.setText(label);
        cb.setTextColor(new android.content.res.ColorStateList(
                new int[][]{new int[]{-android.R.attr.state_enabled}, new int[]{}},
                new int[]{Color.parseColor("#5d6470"), Color.parseColor("#cfd3da")}));   // a greyed-out sub-toggle reads as such
        cb.setTextSize(15);
        cb.setChecked(cur);
        cb.setOnCheckedChangeListener((btn, v) -> { prefs.edit().putBoolean(key, v).apply(); on.set(v); chart.dataChanged(); });
        col.addView(cb);
        return cb;
    }
}
