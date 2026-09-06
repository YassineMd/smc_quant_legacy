package com.smc.domtape;

/**
 * Allocation-light number formatting for the paint loops (2026-09-06). {@code String.format} builds a
 * Formatter + parses the pattern on EVERY call (~10-20 µs and several objects each); the ladder and the
 * tape call a formatter a few hundred times per frame, so these hand-rolled versions matter. Pure Java —
 * no Android imports — so the outputs are verified on the JVM against String.format on random inputs
 * (scratchpad test_fmt.java).
 */
final class Fmt {

    private Fmt() {
    }

    /** value rounded to `dec` decimals, plain digits (no grouping), HALF_UP like %.Nf on these magnitudes. */
    static String fixed(double v, int dec) {
        if (Double.isNaN(v) || Double.isInfinite(v)) return String.valueOf(v);
        long scale = 1;
        for (int i = 0; i < dec; i++) scale *= 10;
        long r = Math.round(Math.abs(v) * scale);
        boolean neg = v < 0 && r != 0;
        long ip = r / scale, fp = r % scale;
        StringBuilder sb = new StringBuilder(24);
        if (neg) sb.append('-');
        sb.append(ip);
        if (dec > 0) {
            sb.append('.');
            String f = Long.toString(fp);
            for (int i = f.length(); i < dec; i++) sb.append('0');
            sb.append(f);
        }
        return sb.toString();
    }

    /** value rounded to `dec` decimals with thousands grouping ("%,.Nf"). */
    static String grouped(double v, int dec) {
        String s = fixed(v, dec);
        int dot = s.indexOf('.');
        int end = dot < 0 ? s.length() : dot;
        int start = s.charAt(0) == '-' ? 1 : 0;
        int digits = end - start;
        if (digits <= 3) return s;
        StringBuilder sb = new StringBuilder(s.length() + digits / 3);
        sb.append(s, 0, start);
        int first = digits % 3;
        if (first == 0) first = 3;
        sb.append(s, start, start + first);
        for (int i = start + first; i < end; i += 3) {
            sb.append(',');
            sb.append(s, i, i + 3);
        }
        sb.append(s, end, s.length());
        return sb.toString();
    }

    /** trades_tape.py _fmt_usd: $1.23M / $123K / $1.2K / $1,234. */
    static String usd(double a) {
        if (a >= 1_000_000) return "$" + fixed(a / 1_000_000, 2) + "M";
        if (a >= 100_000) return "$" + fixed(a / 1_000, 0) + "K";
        if (a >= 1_000) return "$" + fixed(a / 1_000, 1) + "K";
        return "$" + grouped(a, 0);
    }

    /** dom_panel k-formatting: 1.23M / 1.2K / 12 / 1.2. */
    static String k(double v) {
        if (v >= 1_000_000) return fixed(v / 1_000_000, 2) + "M";
        if (v >= 1_000) return fixed(v / 1_000, 1) + "K";
        return v >= 10 ? fixed(v, 0) : fixed(v, 1);
    }

    /** "%,.2f" — the price column / tape price. */
    static String price(double p) {
        return grouped(p, 2);
    }

    /** "%.0f%%" pct as an int string + "%". */
    static String pct(double pct) {
        return fixed(pct, 0) + "%";
    }
}
