package com.smc.domtape;

import android.app.DatePickerDialog;
import android.app.TimePickerDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.SeekBar;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

/**
 * DOM panel: toolbar (GROUP + VP dropdowns incl. Custom…, MIN SIZE log slider + size-dist popup)
 * over the painted ladder — the Android port of dom_panel.DomPanel. Double-tap re-centers;
 * hold-then-drag marks a level band, double-tap on it deletes it.
 */
public class DomPanel extends LinearLayout implements DomView.Host, SizeDistDialog.Owner {

    private static final double[] GROUPS = {0.01, 0.02, 0.05, 0.10};
    private static final int[] VP_SECS = {300, 900, 3600, 7200, 14400, 21600};
    private static final String[] VP_LBL = {"5M", "15M", "1H", "2H", "4H", "6H"};

    private final TradeStore store;
    private final FeedClient feed;
    private DomView canvas;
    private TextView grpChip, vpChip, minLbl;
    private SeekBar slider;
    private final SharedPreferences prefs;
    private int grpIdx;
    private int vpIdx;                              // 1H default, like the terminal
    private long customT0Ms = 0;                    // custom VP start (epoch ms); 0 = preset window
    private double minUsd;
    private SizeDistDialog dist;
    private boolean p50Done;                       // launch default applied (MIN SIZE = tape P50)
    private boolean userAdjusted;                  // the user moved the slider this session

    public DomPanel(Context ctx, TradeStore store, FeedClient feed) {
        super(ctx);
        this.store = store;
        this.feed = feed;
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
        grpChip.setOnClickListener(this::showGroupMenu);
        LayoutParams gp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        gp.leftMargin = (int) Ui.dp(ctx, 8);
        bar.addView(grpChip, gp);

        TextView vpCap = Ui.caption(ctx, "VP");
        LayoutParams vc = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        vc.leftMargin = (int) Ui.dp(ctx, 14);
        bar.addView(vpCap, vc);
        vpChip = new TextView(ctx);
        Ui.styleChip(vpChip, ctx);
        vpChip.setOnClickListener(this::showVpMenu);
        bar.addView(vpChip, gp);

        TextView msCap = Ui.caption(ctx, "MIN SIZE");
        msCap.setPaintFlags(msCap.getPaintFlags() | android.graphics.Paint.UNDERLINE_TEXT_FLAG);
        msCap.setOnClickListener(v -> openSizeDist());
        LayoutParams mc = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        mc.leftMargin = (int) Ui.dp(ctx, 14);
        bar.addView(msCap, mc);

        slider = new SeekBar(ctx);
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
                if (fromUser) userAdjusted = true; // a manual move wins over the P50 launch default
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

    // ── dropdowns ───────────────────────────────────────────────────────────────────────────
    private void showGroupMenu(View anchor) {
        PopupMenu menu = new PopupMenu(getContext(), anchor);
        for (int i = 0; i < GROUPS.length; i++)
            menu.getMenu().add(0, i, i, String.format(Locale.US, "%.2f", GROUPS[i]));
        menu.setOnMenuItemClickListener(item -> {
            grpIdx = item.getItemId();              // price-anchored ladder: a regroup never jumps
            prefs.edit().putInt("dom_group_idx", grpIdx).apply();
            applyLabels();
            canvas.invalidate();
            return true;
        });
        menu.show();
    }

    private void showVpMenu(View anchor) {
        PopupMenu menu = new PopupMenu(getContext(), anchor);
        for (int i = 0; i < VP_LBL.length; i++) menu.getMenu().add(0, i, i, VP_LBL[i]);
        menu.getMenu().add(0, VP_LBL.length, VP_LBL.length, "Custom…");
        menu.setOnMenuItemClickListener(item -> {
            if (item.getItemId() < VP_LBL.length) {
                vpIdx = item.getItemId();
                customT0Ms = 0;                     // a preset always clears the custom start
                store.setCustomKeep(0);
                prefs.edit().putInt("dom_vp_idx", vpIdx).apply();
                applyLabels();
                canvas.invalidate();
            } else {
                pickCustomStart();
            }
            return true;
        });
        menu.show();
    }

    /** Custom VP start: date then hour+minute pickers -> fixed window start, deep-fetched if needed. */
    private void pickCustomStart() {
        Calendar cal = Calendar.getInstance();
        if (customT0Ms > 0) cal.setTimeInMillis(customT0Ms);
        new DatePickerDialog(getContext(), (dp, y, mo, d) -> {
            Calendar c2 = Calendar.getInstance();
            if (customT0Ms > 0) c2.setTimeInMillis(customT0Ms);
            new TimePickerDialog(getContext(), (tp, hh, mm) -> {
                Calendar pick = Calendar.getInstance();
                pick.set(y, mo, d, hh, mm, 0);
                pick.set(Calendar.MILLISECOND, 0);
                setCustom(pick.getTimeInMillis());
            }, c2.get(Calendar.HOUR_OF_DAY), c2.get(Calendar.MINUTE), true).show();
        }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).show();
    }

    private void setCustom(long t0Ms) {
        long now = System.currentTimeMillis();
        customT0Ms = Math.min(t0Ms, now - 60_000);  // a future start would be an empty window
        store.setCustomKeep(customT0Ms);
        long oldest = store.oldestTs();
        if (oldest > 0 && customT0Ms < oldest - 1000)
            feed.requestFetch(customT0Ms);          // bridge fetches older tape (clamped to 72h retention)
        applyLabels();
        canvas.invalidate();
    }

    private void openSizeDist() {
        if (dist == null) dist = new SizeDistDialog(getContext(), this);
        dist.show();
    }

    private void applyLabels() {
        grpChip.setText(String.format(Locale.US, "%.2f ▾", GROUPS[grpIdx]));
        vpChip.setText(vpLabel() + " ▾");
        minLbl.setText(minUsd <= 0 ? "ALL" : "≥ " + Ui.fmtUsd(minUsd));
    }

    public void tick() {
        if (!p50Done && !userAdjusted && store.tradeCount() >= 500) {
            p50Done = true;                        // launch default: the 50%-of-volume size split
            setMin(store.volumeHalfUsd());
        }
        canvas.invalidate();
    }

    // ── DomView.Host ────────────────────────────────────────────────────────────────────────
    @Override
    public double group() {
        return GROUPS[grpIdx];
    }

    @Override
    public long vpCutoffMs() {
        if (customT0Ms > 0) return customT0Ms;      // fixed start; presets trail 'now'
        return System.currentTimeMillis() - VP_SECS[vpIdx] * 1000L;
    }

    @Override
    public String vpLabel() {
        if (customT0Ms > 0)
            return new SimpleDateFormat("dd/MM HH:mm", Locale.US).format(new Date(customT0Ms)) + " →";
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

    // ── SizeDistDialog.Owner ────────────────────────────────────────────────────────────────
    @Override
    public TradeStore.SizeSamples samples() {
        return store.sizeSamples(vpCutoffMs());
    }

    @Override
    public String scope() {
        return "DOM · " + vpLabel();
    }

    @Override
    public double getMin() {
        return minUsd;
    }

    @Override
    public void setMin(double usd) {
        slider.setProgress(Ui.usdToSlider(usd));    // moves the slider, which re-derives everything
    }
}
