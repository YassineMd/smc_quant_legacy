package com.smc.domtape;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;

/**
 * Trades tape panel: toolbar (MIN SIZE log slider + LIVE/PAUSED pill) over the painted tape —
 * the Android port of trades_tape.TradesTapePanel.
 */
public class TapePanel extends LinearLayout implements TapeView.Host, SizeDistDialog.Owner {

    private final TradeStore store;
    private final TapeView canvas;
    private final TextView valLbl, pill;
    private final SeekBar slider;
    private final SharedPreferences prefs;
    private double minUsd;
    private int scroll;                            // rows scrolled back (0 = follow live)
    private SizeDistDialog dist;
    private boolean p50Done;                       // launch default applied (MIN SIZE = tape P50)
    private boolean userAdjusted;                  // the user moved the slider this session

    public TapePanel(Context ctx, TradeStore store) {
        super(ctx);
        this.store = store;
        prefs = ctx.getSharedPreferences("smcdom", Context.MODE_PRIVATE);
        minUsd = prefs.getFloat("tape_min_usd", 0f);
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

        TextView msCap = Ui.caption(ctx, "MIN SIZE");
        msCap.setPaintFlags(msCap.getPaintFlags() | android.graphics.Paint.UNDERLINE_TEXT_FLAG);
        msCap.setOnClickListener(v -> openSizeDist());
        bar.addView(msCap);

        slider = new SeekBar(ctx);
        slider.setMax(Ui.SLIDER_STEPS);
        slider.setProgress(Ui.usdToSlider(minUsd));
        slider.getProgressDrawable().setTint(Ui.GOLD);
        slider.getThumb().setTint(Color.parseColor("#e6ecf4"));
        LayoutParams sl = new LayoutParams((int) Ui.dp(ctx, 230), LayoutParams.WRAP_CONTENT);
        sl.leftMargin = (int) Ui.dp(ctx, 10);
        bar.addView(slider, sl);

        valLbl = new TextView(ctx);
        valLbl.setTextColor(Ui.GOLD);
        valLbl.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        valLbl.setTextSize(12);
        LayoutParams vl = new LayoutParams((int) Ui.dp(ctx, 92), LayoutParams.WRAP_CONTENT);
        vl.leftMargin = (int) Ui.dp(ctx, 10);
        bar.addView(valLbl, vl);

        View spacer = new View(ctx);
        bar.addView(spacer, new LayoutParams(0, 1, 1f));

        pill = new TextView(ctx);
        pill.setGravity(Gravity.CENTER);
        pill.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        pill.setTextSize(11);
        int ppx = (int) Ui.dp(ctx, 14), ppy = (int) Ui.dp(ctx, 4);
        pill.setPadding(ppx, ppy, ppx, ppy);
        pill.setOnClickListener(v -> resumeLive());
        bar.addView(pill);

        canvas = new TapeView(ctx, this);
        addView(canvas, new LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f));

        slider.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar sb, int v, boolean fromUser) {
                if (fromUser) userAdjusted = true; // a manual move wins over the P50 launch default
                minUsd = Ui.sliderToUsd(v);
                scroll = 0;                        // a new filter re-anchors to the live edge
                applyLabels();
                prefs.edit().putFloat("tape_min_usd", (float) minUsd).apply();
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

    private void applyLabels() {
        valLbl.setText(minUsd <= 0 ? "ALL" : "≥ " + Ui.fmtUsd(minUsd));
        boolean live = scroll == 0;
        pill.setText(live ? "● LIVE" : "⏸ PAUSED — tap to follow");
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(Ui.dp(getContext(), 12));
        int col = live ? Ui.BUY : Ui.GOLD;
        bg.setColor((col & 0x00FFFFFF) | (26 << 24));
        bg.setStroke((int) Ui.dp(getContext(), 1), (col & 0x00FFFFFF) | (115 << 24));
        pill.setBackground(bg);
        pill.setTextColor(col);
    }

    public void tick() {
        if (!p50Done && !userAdjusted && store.tradeCount() >= 500) {
            p50Done = true;                        // launch default: the 50%-of-volume size split
            setMin(store.volumeHalfUsd());
        }
        canvas.invalidate();
    }

    private void resumeLive() {
        scroll = 0;
        applyLabels();
        canvas.invalidate();
    }

    private void openSizeDist() {
        if (dist == null) dist = new SizeDistDialog(getContext(), this);
        dist.show();
    }

    // ── SizeDistDialog.Owner ────────────────────────────────────────────────────────────────
    @Override
    public TradeStore.SizeSamples samples() {
        return store.sizeSamples(0);               // the whole retained tape, like the terminal
    }

    @Override
    public String scope() {
        long a = store.oldestTs(), b = store.latestTs();
        if (a <= 0 || b <= a) return "Trades · (empty)";
        return String.format(java.util.Locale.US, "Trades · last %.0f min",
                Math.max(1.0, (b - a) / 60000.0));
    }

    @Override
    public double getMin() {
        return minUsd;
    }

    @Override
    public void setMin(double usd) {
        slider.setProgress(Ui.usdToSlider(usd));   // moves the slider, which re-derives everything
    }

    // ── TapeView.Host ───────────────────────────────────────────────────────────────────────
    @Override
    public double minUsd() {
        return minUsd;
    }

    @Override
    public int scrollRows() {
        return scroll;
    }

    @Override
    public void scrollBy(int rows) {
        int was = scroll;
        scroll = Math.max(0, scroll + rows);
        if (scroll != was) applyLabels();
    }

    @Override
    public TradeStore store() {
        return store;
    }
}
