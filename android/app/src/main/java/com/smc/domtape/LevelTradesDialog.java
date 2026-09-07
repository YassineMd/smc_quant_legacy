package com.smc.domtape;

import android.app.Dialog;
import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The popup behind a DOM diamond (user 2026-09-07): the trades of ONE price level -- the merged players that
 * STARTED there plus the plain trades at it, inside the DOM's VP window and MIN SIZE -- rendered by the very same
 * {@link TapeView} as the Trades window (tiers, "+Ns", "(±N)", tap a merged row to drop its fills down). Swipe up
 * digs into older rows. Refreshes once a second while open (the window trails 'now').
 */
public class LevelTradesDialog extends Dialog implements TapeView.Host {

    private final DomView.Host dom;
    private final long bin;
    private final double g;
    private final TapeView tape;
    private int scroll;
    // memo of the last row build (the walk covers the whole VP window: once per store change, not per paint)
    private long memoVer = -1;
    private double memoMin = Double.NaN;
    private int memoSkip = -1, memoFit = -1;
    private double[][] memo;
    private final Runnable refresh = new Runnable() {
        @Override
        public void run() {
            tape.maybeInvalidate();
            tape.postDelayed(this, 1000);
        }
    };

    public LevelTradesDialog(Context ctx, DomView.Host dom, long bin) {
        super(ctx);
        this.dom = dom;
        this.bin = bin;
        this.g = dom.group();
        requestWindowFeature(Window.FEATURE_NO_TITLE);

        LinearLayout root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Ui.BG);

        LinearLayout head = new LinearLayout(ctx);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setBackgroundColor(Ui.BG_TOOL);
        int padH = (int) Ui.dp(ctx, 14);
        head.setPadding(padH, 0, padH, 0);
        TextView title = new TextView(ctx);
        title.setText("◆ " + Fmt.price(bin * g));
        title.setTextColor(Ui.TXT);
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(14);
        head.addView(title);
        double min = dom.minUsd();
        TextView scope = Ui.caption(ctx, "LEVEL · " + dom.vpLabel() + (min > 0 ? " · ≥ " + Ui.fmtUsd(min) : ""));
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        sp.leftMargin = (int) Ui.dp(ctx, 14);
        head.addView(scope, sp);
        root.addView(head, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (int) Ui.dp(ctx, 44)));
        View rule = new View(ctx);
        rule.setBackgroundColor(Ui.RULE);
        root.addView(rule, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (int) Ui.dp(ctx, 1)));

        tape = new TapeView(ctx, this);
        root.addView(tape, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
        if (getWindow() != null) {
            getWindow().setBackgroundDrawable(new ColorDrawable(Ui.BG));
            getWindow().setLayout((int) Ui.dp(ctx, 560), (int) Ui.dp(ctx, 520));
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        tape.postDelayed(refresh, 1000);
    }

    @Override
    protected void onStop() {
        tape.removeCallbacks(refresh);
        super.onStop();
    }

    // ── TapeView.Host: this level's rows instead of the live tape ───────────────────────────
    @Override
    public double minUsd() {
        return dom.minUsd();
    }

    @Override
    public int scrollRows() {
        return scroll;
    }

    @Override
    public void scrollBy(int rows) {
        scroll = Math.max(0, scroll + rows);
    }

    @Override
    public TradeStore store() {
        return dom.store();
    }

    @Override
    public double[][] rows(TradeStore st, double minUsd, int skip, int nFit) {
        long ver = st.version();
        if (memo != null && ver == memoVer && minUsd == memoMin && skip == memoSkip && nFit == memoFit) return memo;
        long tpg = Math.max(1, Math.round(g / TradeStore.TICK));
        double[][] all = st.levelRows(bin, tpg, dom.vpCutoffMs(), minUsd, skip + nFit);
        double[][] out;
        if (skip <= 0) out = all;
        else {
            int k = Math.max(0, all.length - skip);
            out = new double[k][];
            System.arraycopy(all, skip, out, 0, k);
        }
        memoVer = ver; memoMin = minUsd; memoSkip = skip; memoFit = nFit; memo = out;
        return out;
    }

    @Override
    public String emptyText() {
        return "no trades at " + Fmt.price(bin * g) + (dom.minUsd() > 0 ? " ≥ filter" : "") + " in " + dom.vpLabel();
    }
}
