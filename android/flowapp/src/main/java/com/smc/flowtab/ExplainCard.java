package com.smc.flowtab;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.Html;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The I x I explain panel as a CARD: the terminal's words (_iimp_explain) re-set for reading -- a side chip and the
 * cycle's time in the header, the four summary numbers as tiles, each labelled sentence in a label / text row,
 * the WHY as a tinted callout, a forming warning in amber. Any tap on it closes it.
 */
public final class ExplainCard {
    private static final Pattern ROW_LABEL = Pattern.compile("^<span[^>]*><b>([A-Za-z]+)</b></span>:\\s*(.*)$", Pattern.DOTALL);
    private static final Pattern ROW_DIV = Pattern.compile("^<div style='color:(#[0-9a-fA-F]{6})'>(.*)</div>$", Pattern.DOTALL);

    private ExplainCard() { }

    private static int dp(Context c, float v) { return (int) (v * c.getResources().getDisplayMetrics().density + 0.5f); }

    private static String strip(String html) { return Html.fromHtml(html, Html.FROM_HTML_MODE_COMPACT).toString().trim(); }

    private static GradientDrawable rounded(int fill, float radiusPx) {
        GradientDrawable g = new GradientDrawable(); g.setColor(fill); g.setCornerRadius(radiusPx); return g;
    }

    private static int tint(int col, int alpha) { return (col & 0x00ffffff) | (alpha << 24); }

    private static TextView text(Context c, CharSequence s, float sp, int col, boolean bold) {
        TextView t = new TextView(c);
        t.setText(s); t.setTextSize(sp); t.setTextColor(col);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    public static View build(Context c, String html, View.OnClickListener close) {
        // ---- parse the terminal's rows
        String inner = html.replaceFirst("(?s)^\\s*<div[^>]*>", "").replaceFirst("(?s)</div>\\s*$", "");
        String[] rows = inner.split("<br>");
        String head = null, warn = null, sumCol = null, summary = null;
        List<String[]> sections = new ArrayList<>();
        for (String raw : rows) {
            String row = raw.trim();
            if (row.isEmpty() || row.toLowerCase().contains("click this panel")) continue;
            Matcher ml = ROW_LABEL.matcher(row);
            if (ml.matches()) { sections.add(new String[]{ml.group(1), ml.group(2)}); continue; }
            Matcher md = ROW_DIV.matcher(row);
            if (md.matches()) {
                String col = md.group(1), body = md.group(2);
                if (head == null && col.equalsIgnoreCase("#7d8492")) head = strip(body);
                else if (col.equalsIgnoreCase("#ffd479")) warn = strip(body);
                else { sumCol = col; summary = strip(body); }
                continue;
            }
            sections.add(new String[]{"", row});
        }
        int side = sumCol != null ? Color.parseColor(sumCol) : Color.parseColor("#26a69a");
        int ink = Color.parseColor("#1f2933"), muted = Color.parseColor("#8a94a6"), label = Color.parseColor("#6b7280");
        float d = c.getResources().getDisplayMetrics().density;

        LinearLayout card = new LinearLayout(c);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(rounded(Color.WHITE, 18 * d));
        card.setClipToOutline(true);
        // the accent bar in the side's colour
        View accent = new View(c); accent.setBackgroundColor(side);
        card.addView(accent, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, 5)));

        LinearLayout body = new LinearLayout(c);
        body.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(c, 24);
        body.setPadding(pad, dp(c, 18), pad, dp(c, 10));

        // ---- header: side chip, the cycle's time, its duration, a FORMING chip
        LinearLayout hdr = new LinearLayout(c);
        hdr.setOrientation(LinearLayout.HORIZONTAL); hdr.setGravity(Gravity.CENTER_VERTICAL);
        String sideWord = "", interest = "", impact = "", wall = "", kept = "";
        if (summary != null) {
            String[] parts = summary.replace(" ", " ").split("·");
            if (parts.length >= 1) { String[] a = parts[0].trim().split("\\s+"); sideWord = a.length > 0 ? a[0] : ""; interest = a.length > 1 ? a[1] : ""; }
            if (parts.length >= 2) impact = parts[1].replace("impact", "").trim();
            if (parts.length >= 3) wall = parts[2].replace("wall", "").trim();
            if (parts.length >= 4) kept = parts[3].replace("kept", "").trim();
        }
        if (!sideWord.isEmpty()) {
            TextView chip = text(c, sideWord, 13, Color.WHITE, true);
            chip.setBackground(rounded(side, 8 * d)); chip.setPadding(dp(c, 12), dp(c, 5), dp(c, 12), dp(c, 5));
            chip.setLetterSpacing(0.06f);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.rightMargin = dp(c, 14); hdr.addView(chip, lp);
        }
        boolean forming = head != null && head.toUpperCase().contains("STILL FORMING");
        String when = head == null ? "" : head.replace("STILL FORMING", "").replace(" ", " ").trim();
        String dur = "";
        int cut = when.lastIndexOf('·');
        if (cut > 0) { dur = when.substring(cut + 1).replace("·", "").trim(); when = when.substring(0, cut).trim(); }
        TextView tw = text(c, when, 16, ink, true);
        hdr.addView(tw, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (!dur.isEmpty()) {
            TextView td = text(c, dur, 13, muted, false);
            td.setBackground(rounded(Color.parseColor("#f1f3f6"), 8 * d)); td.setPadding(dp(c, 10), dp(c, 4), dp(c, 10), dp(c, 4));
            hdr.addView(td);
        }
        if (forming) {
            TextView tf = text(c, "FORMING", 11, Color.parseColor("#9a6b00"), true);
            tf.setBackground(rounded(Color.parseColor("#fff1cc"), 8 * d)); tf.setPadding(dp(c, 10), dp(c, 4), dp(c, 10), dp(c, 4)); tf.setLetterSpacing(0.08f);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.leftMargin = dp(c, 8); hdr.addView(tf, lp);
        }
        body.addView(hdr);

        // ---- the four numbers as tiles
        if (summary != null) {
            LinearLayout tiles = new LinearLayout(c);
            tiles.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            tlp.topMargin = dp(c, 18); tlp.bottomMargin = dp(c, 6);
            String[][] spec = {{interest, "interest"}, {impact, "impact"}, {wall, "wall"}, {kept, "kept"}};
            for (int i = 0; i < spec.length; i++) {
                LinearLayout tile = new LinearLayout(c);
                tile.setOrientation(LinearLayout.VERTICAL); tile.setGravity(Gravity.CENTER);
                tile.setBackground(rounded(i == 0 ? tint(side, 24) : Color.parseColor("#f4f6f9"), 12 * d));
                tile.setPadding(dp(c, 8), dp(c, 12), dp(c, 8), dp(c, 10));
                TextView v = text(c, spec[i][0].isEmpty() ? "-" : spec[i][0], 22, i == 0 ? side : ink, true);
                TextView k = text(c, spec[i][1].toUpperCase(), 10.5f, muted, false);
                k.setLetterSpacing(0.12f);
                LinearLayout.LayoutParams klp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                klp.topMargin = dp(c, 2);
                tile.addView(v); tile.addView(k, klp);
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                if (i > 0) lp.leftMargin = dp(c, 10);
                tiles.addView(tile, lp);
            }
            body.addView(tiles, tlp);
        }

        // ---- the labelled sentences; WHY as a callout
        for (String[] sec : sections) {
            boolean why = "Why".equalsIgnoreCase(sec[0]);
            LinearLayout row = new LinearLayout(c);
            row.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            if (why) {
                rlp.topMargin = dp(c, 16);
                row.setBackground(rounded(tint(side, 22), 12 * d));
                row.setPadding(dp(c, 16), dp(c, 14), dp(c, 16), dp(c, 14));
                View bar = new View(c); bar.setBackground(rounded(side, 2 * d));
                LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(dp(c, 4), ViewGroup.LayoutParams.MATCH_PARENT);
                blp.rightMargin = dp(c, 14); row.addView(bar, blp);
            } else {
                rlp.topMargin = dp(c, 12);
            }
            TextView lb = text(c, sec[0].toUpperCase(), 11.5f, why ? side : label, true);
            lb.setLetterSpacing(0.1f);
            lb.setPadding(0, dp(c, 3), 0, 0);
            LinearLayout.LayoutParams llp = new LinearLayout.LayoutParams(dp(c, 74), ViewGroup.LayoutParams.WRAP_CONTENT);
            row.addView(lb, llp);
            TextView tx = text(c, Html.fromHtml(sec[1], Html.FROM_HTML_MODE_COMPACT), 15, ink, false);
            tx.setLineSpacing(0, 1.3f);
            row.addView(tx, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            body.addView(row, rlp);
            if (!why) {
                View hair = new View(c); hair.setBackgroundColor(Color.parseColor("#eef0f3"));
                LinearLayout.LayoutParams hlp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, 1));
                hlp.topMargin = dp(c, 12); body.addView(hair, hlp);
            }
        }
        if (warn != null) {
            TextView tw2 = text(c, warn, 13.5f, Color.parseColor("#8a5a00"), false);
            tw2.setBackground(rounded(Color.parseColor("#fff4d6"), 12 * d)); tw2.setPadding(dp(c, 16), dp(c, 12), dp(c, 16), dp(c, 12));
            tw2.setLineSpacing(0, 1.25f);
            LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            wlp.topMargin = dp(c, 14); body.addView(tw2, wlp);
        }
        TextView foot = text(c, "tap to close", 12, Color.parseColor("#a0a8b4"), false);
        foot.setGravity(Gravity.CENTER); foot.setPadding(0, dp(c, 18), 0, dp(c, 4));
        body.addView(foot, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        ScrollView sv = new ScrollView(c);
        sv.addView(body);
        card.addView(sv, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        // any tap closes it: every view that could eat the touch gets the listener
        for (View v : new View[]{card, body, sv, hdr, foot}) v.setOnClickListener(close);
        for (int i = 0; i < body.getChildCount(); i++) {
            View ch = body.getChildAt(i); ch.setOnClickListener(close);
            if (ch instanceof ViewGroup) for (int j = 0; j < ((ViewGroup) ch).getChildCount(); j++) ((ViewGroup) ch).getChildAt(j).setOnClickListener(close);
        }
        return card;
    }
}
