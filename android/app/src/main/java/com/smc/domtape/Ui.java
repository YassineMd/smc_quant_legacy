package com.smc.domtape;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.widget.TextView;

import java.util.Locale;

/** Shared palette + formatting — the terminal panels' design language, verbatim. */
public final class Ui {

    // Binance-inspired dark scanner palette (trades_tape.py / dom_panel.py)
    public static final int BG = Color.rgb(10, 13, 18);
    public static final int BG_TOOL = Color.parseColor("#0e1218");
    public static final int PRICE_BG = Color.rgb(13, 17, 23);
    public static final int RULE = Color.rgb(27, 34, 45);
    public static final int GRID = Color.argb(7, 255, 255, 255);
    public static final int ZEBRA = Color.argb(5, 255, 255, 255);
    public static final int HDR_TXT = Color.rgb(122, 132, 150);
    public static final int TIME_TXT = Color.argb(200, 148, 158, 175);
    public static final int TXT = Color.rgb(212, 220, 232);
    public static final int AMT_TXT = Color.rgb(212, 220, 232);
    public static final int DIM_TXT = Color.argb(120, 212, 220, 232);
    public static final int DIM_TXT135 = Color.argb(135, 212, 220, 232);
    public static final int BUY = Color.rgb(46, 189, 133);
    public static final int SELL = Color.rgb(246, 70, 93);
    public static final int GOLD = Color.rgb(240, 185, 11);
    public static final int VP_GRAY = Color.rgb(150, 160, 175);
    public static final int LVN = Color.rgb(168, 110, 240);
    public static final int WAIT_TXT = Color.argb(160, 120, 130, 148);
    public static final int CHIP_BG = Color.parseColor("#161b24");
    public static final int CHIP_BORDER = Color.parseColor("#242c3a");

    // MIN SIZE log slider: 0 = ALL, else $10 -> $500K over 1000 steps (trades_tape.py mapping)
    public static final int SLIDER_STEPS = 1000;
    private static final double USD_LO = 10.0, USD_HI = 500_000.0;

    private Ui() {
    }

    public static float dp(Context c, float v) {
        return v * c.getResources().getDisplayMetrics().density;
    }

    public static double sliderToUsd(int v) {
        if (v <= 0) return 0.0;
        double t = v / (double) SLIDER_STEPS;
        return Math.pow(10.0, Math.log10(USD_LO) + t * (Math.log10(USD_HI) - Math.log10(USD_LO)));
    }

    public static int usdToSlider(double usd) {
        if (usd <= 0) return 0;
        double c = Math.max(USD_LO, Math.min(USD_HI, usd));
        double t = (Math.log10(c) - Math.log10(USD_LO)) / (Math.log10(USD_HI) - Math.log10(USD_LO));
        return (int) Math.round(t * SLIDER_STEPS);
    }

    public static String fmtUsd(double a) {
        return Fmt.usd(a);                         // hand-rolled: String.format cost ~15 us + garbage per call
    }

    public static String kfmt(double v) {
        return Fmt.k(v);
    }

    /** Rounded chip styling shared by the toolbar buttons (the terminal's combo-pill look). */
    public static void styleChip(TextView tv, Context c) {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(CHIP_BG);
        bg.setCornerRadius(dp(c, 10));
        bg.setStroke((int) dp(c, 1), CHIP_BORDER);
        tv.setBackground(bg);
        tv.setTextColor(Color.parseColor("#e6ecf4"));
        tv.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        tv.setTextSize(12);
        int px = (int) dp(c, 10), py = (int) dp(c, 4);
        tv.setPadding(px, py, px, py);
    }

    /** Small gray caption label ("GROUP", "VP", "MIN SIZE"). */
    public static TextView caption(Context c, String text) {
        TextView tv = new TextView(c);
        tv.setText(text);
        tv.setTextColor(HDR_TXT);
        tv.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        tv.setTextSize(10);
        tv.setLetterSpacing(0.1f);
        return tv;
    }
}
