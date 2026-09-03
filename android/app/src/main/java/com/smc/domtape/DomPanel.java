package com.smc.domtape;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;

/**
 * DOM panel: toolbar (GROUP + VP cycle chips, MIN SIZE log slider) over the painted ladder —
 * the Android port of dom_panel.DomPanel. Dropdowns became tap-to-cycle chips (touch-friendly);
 * double-tap on the ladder re-centers, exactly like the terminal's double-click.
 */
public class DomPanel extends LinearLayout implements DomView.Host {

    private static final double[] GROUPS = {0.01, 0.02, 0.05, 0.10};
    private static final int[] VP_SECS = {300, 900, 3600, 7200, 14400, 21600};
    private static final String[] VP_LBL = {"5M", "15M", "1H", "2H", "4H", "6H"};

    private final TradeStore store;
    private DomView canvas;
    private final TextView grpChip, vpChip, minLbl;
    private final SharedPreferences prefs;
    private int grpIdx = 0;
    private int vpIdx = 2;                          // 1H default, like the terminal
    private double minUsd;

    public DomPanel(Context ctx, TradeStore store) {
        super(ctx);
        this.store = store;
        prefs = ctx.getSharedPreferences("smcdom", Context.MODE_PRIVATE);
        grpIdx = clampIdx(prefs.getInt("dom_group_idx", 0), GROUPS.length);
        vpIdx = clampIdx(prefs.getInt("dom_vp_idx", 2), VP_SECS.length);
        minUsd = prefs.getFloat("dom_min_usd", 0f);
        setOrientation(VERTICAL);
        setBackgroundColor(Ui.BG);

        LinearLayout bar = new LinearLayout(ctx);
        bar.setOrientation(HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Ui.BG_TOOL);
        int padH = (int) Ui.dp(ctx, 14);
        bar.setPadding(padH, 0, padH, 0);
        addView(bar, new LayoutParams(LayoutParams.MATCH_PARENT, (int) Ui.dp(ctx, 44)));

        View rule = new View(ctx);
        rule.setBackgroundColor(Ui.RULE);
        addView(rule, new LayoutParams(LayoutParams.MATCH_PARENT, (int) Ui.dp(ctx, 1)));

        bar.addView(Ui.caption(ctx, "GROUP"));
        grpChip = new TextView(ctx);
        Ui.styleChip(grpChip, ctx);
        grpChip.setOnClickListener(v -> {
            grpIdx = (grpIdx + 1) % GROUPS.length;  // price-anchored ladder: a regroup never jumps
            prefs.edit().putInt("dom_group_idx", grpIdx).apply();
            applyLabels();
            canvas.invalidate();
        });
        LayoutParams gp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        gp.leftMargin = (int) Ui.dp(ctx, 8);
        bar.addView(grpChip, gp);

        TextView vpCap = Ui.caption(ctx, "VP");
        LayoutParams vc = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        vc.leftMargin = (int) Ui.dp(ctx, 14);
        bar.addView(vpCap, vc);
        vpChip = new TextView(ctx);
        Ui.styleChip(vpChip, ctx);
        vpChip.setOnClickListener(v -> {
            vpIdx = (vpIdx + 1) % VP_SECS.length;
            prefs.edit().putInt("dom_vp_idx", vpIdx).apply();
            applyLabels();
            canvas.invalidate();
        });
        bar.addView(vpChip, gp);

        TextView msCap = Ui.caption(ctx, "MIN SIZE");
        LayoutParams mc = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        mc.leftMargin = (int) Ui.dp(ctx, 14);
        bar.addView(msCap, mc);

        SeekBar slider = new SeekBar(ctx);
        slider.setMax(Ui.SLIDER_STEPS);
        slider.setProgress(Ui.usdToSlider(minUsd));
        slider.getProgressDrawable().setTint(Ui.GOLD);
        slider.getThumb().setTint(Color.parseColor("#e6ecf4"));
        LayoutParams sl = new LayoutParams((int) Ui.dp(ctx, 170), LayoutParams.WRAP_CONTENT);
        sl.leftMargin = (int) Ui.dp(ctx, 8);
        bar.addView(slider, sl);

        minLbl = new TextView(ctx);
        minLbl.setTextColor(Ui.GOLD);
        minLbl.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        minLbl.setTextSize(12);
        LayoutParams ml = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        ml.leftMargin = (int) Ui.dp(ctx, 8);
        bar.addView(minLbl, ml);

        canvas = new DomView(ctx, this);
        addView(canvas, new LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f));

        slider.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar sb, int v, boolean fromUser) {
                minUsd = Ui.sliderToUsd(v);
                prefs.edit().putFloat("dom_min_usd", (float) minUsd).apply();
                applyLabels();
                canvas.invalidate();
            }

            @Override
            public void onStartTrackingTouch(SeekBar sb) {
            }

            @Override
            public void onStopTrackingTouch(SeekBar sb) {
            }
        });
        applyLabels();
    }

    private static int clampIdx(int i, int n) {
        return Math.max(0, Math.min(n - 1, i));
    }

    private void applyLabels() {
        grpChip.setText(String.format(java.util.Locale.US, "%.2f", GROUPS[grpIdx]));
        vpChip.setText(VP_LBL[vpIdx]);
        minLbl.setText(minUsd <= 0 ? "ALL" : "≥ " + Ui.fmtUsd(minUsd));
    }

    public void tick() {
        canvas.invalidate();
    }

    // ── DomView.Host ────────────────────────────────────────────────────────────────────────
    @Override
    public double group() {
        return GROUPS[grpIdx];
    }

    @Override
    public long vpCutoffMs() {
        return System.currentTimeMillis() - VP_SECS[vpIdx] * 1000L;
    }

    @Override
    public String vpLabel() {
        return VP_LBL[vpIdx];
    }

    @Override
    public double minUsd() {
        return minUsd;
    }

    @Override
    public TradeStore store() {
        return store;
    }
}
