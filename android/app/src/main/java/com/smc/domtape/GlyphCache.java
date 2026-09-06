package com.smc.domtape;

import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.fonts.Font;
import android.graphics.text.PositionedGlyphs;
import android.graphics.text.TextRunShaper;
import android.os.Build;

import java.util.HashMap;

/**
 * Text SHAPING cache (2026-09-06, the lag fix's second half). Profiled on the tablet: 203 drawText calls cost
 * 5-8 ms of a ~15 ms ladder frame — Canvas.drawText shapes its string (HarfBuzz) on EVERY call. Here each
 * distinct string is shaped ONCE per paint (TextRunShaper, API 31+) into glyph ids + positions, and every
 * later frame draws it with Canvas.drawGlyphs (no shaping, ~4 us). Alignment is applied from the run's
 * advance (drawGlyphs ignores Paint.Align). Below API 31 it is a plain drawText passthrough.
 *
 * One cache per PAINT (typeface / size / letter-spacing decide the shaping; colour does not, so the drawing
 * paint may carry any colour / alpha). Bounded: the map is dropped past MAX entries (the vocabulary of
 * "12.3K"-style numbers is small, so misses are rare after warm-up).
 */
final class GlyphCache {

    static final boolean NATIVE = Build.VERSION.SDK_INT >= 31;
    private static final int MAX = 4000;

    private static final class Run {
        final int[] ids;
        final float[] pos;                          // (x, y) pairs relative to the run origin
        final int n;
        final float adv;
        final Font font;

        Run(int[] ids, float[] pos, int n, float adv, Font font) {
            this.ids = ids;
            this.pos = pos;
            this.n = n;
            this.adv = adv;
            this.font = font;
        }
    }

    private final Paint shaper;                     // a copy of the paint's shaping attributes
    private final HashMap<String, Run> runs = new HashMap<>();
    private float[] scratch = new float[128];
    int hits, misses;                               // profiling counters (PROFILE builds)

    GlyphCache(Paint p) {
        shaper = new Paint(p);
    }

    private Run shape(String s) {
        Run r = runs.get(s);
        if (r != null) {
            hits++;
            return r;
        }
        misses++;
        if (runs.size() >= MAX) runs.clear();
        PositionedGlyphs pg = TextRunShaper.shapeTextRun(s, 0, s.length(), 0, s.length(), 0f, 0f, false, shaper);
        int n = pg.glyphCount();
        Font f = n > 0 ? pg.getFont(0) : null;
        for (int i = 1; i < n; i++) {
            if (pg.getFont(i) != f) {               // mixed fonts (fallback glyphs): not worth the multi-run path
                runs.put(s, null);
                return null;
            }
        }
        int[] ids = new int[n];
        float[] pos = new float[n * 2];
        for (int i = 0; i < n; i++) {
            ids[i] = pg.getGlyphId(i);
            pos[i * 2] = pg.getGlyphX(i);
            pos[i * 2 + 1] = pg.getGlyphY(i);
        }
        r = new Run(ids, pos, n, pg.getAdvance(), f);
        runs.put(s, r);
        return r;
    }

    /** Draw `s` at (x, y) honouring p's text alignment, with p's colour / alpha. */
    void draw(Canvas c, String s, float x, float y, Paint p) {
        if (!NATIVE || s.isEmpty()) {
            c.drawText(s, x, y, p);
            return;
        }
        Run r = runs.containsKey(s) ? runs.get(s) : shape(s);
        if (r == null) {
            c.drawText(s, x, y, p);
            return;
        }
        Paint.Align al = p.getTextAlign();
        float x0 = al == Paint.Align.RIGHT ? x - r.adv : (al == Paint.Align.CENTER ? x - r.adv / 2f : x);
        int need = r.n * 2;
        if (scratch.length < need) scratch = new float[Math.max(need, scratch.length * 2)];
        float[] sc = scratch;
        float[] ps = r.pos;
        for (int i = 0; i < r.n; i++) {
            sc[i * 2] = ps[i * 2] + x0;
            sc[i * 2 + 1] = ps[i * 2 + 1] + y;
        }
        c.drawGlyphs(r.ids, 0, sc, 0, r.n, r.font, p);
    }
}
