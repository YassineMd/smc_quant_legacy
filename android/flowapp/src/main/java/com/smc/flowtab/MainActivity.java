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
        chart.showTakeover = prefs.getBoolean("takeover", true);
        interp.setVisibility(prefs.getBoolean("interp", true) ? View.VISIBLE : View.GONE);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.addView(chart, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.74f));
        row.addView(interp, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.26f));
        FrameLayout root = new FrameLayout(this);
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
        popupAnchor = new View(this);
        root.addView(popupAnchor, new FrameLayout.LayoutParams(1, 1, Gravity.TOP | Gravity.START));
        menu.setOnClickListener(v -> showMenu());
        setContentView(root);

        feed = new EngineClient(model, this);
        feed.start();
        // the engine learns the tablet's toggles that concern it
        feed.sendToggle("lines", chart.showLines);
        feed.sendToggle("takeover", chart.showTakeover);
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
            chart.dataChanged(); interp.refresh();
            if (connected) {
                feed.sendToggle("lines", chart.showLines);
                feed.sendToggle("takeover", chart.showTakeover);
            }
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
    }

    private void showExplain(String html) {
        if (explainDlg != null && explainDlg.isShowing()) explainDlg.dismiss();
        TextView tv = new TextView(this);
        tv.setText(Html.fromHtml(html, Html.FROM_HTML_MODE_COMPACT));
        tv.setTextColor(Color.parseColor("#dcdcdc"));
        tv.setTextSize(13);
        tv.setPadding((int) Ui.dp(this, 16), (int) Ui.dp(this, 12), (int) Ui.dp(this, 16), (int) Ui.dp(this, 12));
        tv.setMovementMethod(LinkMovementMethod.getInstance());
        ScrollView sv = new ScrollView(this);
        sv.addView(tv);
        sv.setBackgroundColor(Color.parseColor("#20242c"));
        explainDlg = new AlertDialog.Builder(this).setView(sv).create();
        explainDlg.setOnDismissListener(dlg -> chart.clearSelection());
        explainDlg.show();
        if (explainDlg.getWindow() != null) explainDlg.getWindow().setLayout((int) Ui.dp(this, 560), ViewGroup.LayoutParams.WRAP_CONTENT);
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
        toggle(col, "Interpretation", "interp", interp.getVisibility() == View.VISIBLE, v -> interp.setVisibility(v && chart.getFullscreen() < 0 ? View.VISIBLE : View.GONE));
        section(col, "Indicator  ›  Cycle Chart");
        toggle(col, "Takeover ▲▼  (one side owns the cycle)", "takeover", chart.showTakeover, v -> { chart.showTakeover = v; feed.sendToggle("takeover", v); });
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
