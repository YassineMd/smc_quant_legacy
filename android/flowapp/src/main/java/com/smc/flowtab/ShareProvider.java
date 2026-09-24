package com.smc.flowtab;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

/**
 * The files of the "send to Claude" button (user 2026-09-24: "my tablet can communicate the info with the Claude app
 * on my tablet"), served to the Claude app as content:// URIs.
 *
 * A plain ContentProvider rather than androidx's FileProvider: the app has no androidx dependency. It serves ONLY
 * the files in cache/claude_share (a bare name, no path), READ-ONLY, and it is not exported: the Claude app can open
 * a file only through the read grant carried by the share intent. Each share deletes the previous pack first.
 */
public final class ShareProvider extends ContentProvider {
    public static final String AUTH = "com.smc.flowtab.share";
    private static final String DIR = "claude_share";

    private static File dir(Context c) {
        File d = new File(c.getCacheDir(), DIR);
        //noinspection ResultOfMethodCallIgnored
        d.mkdirs();
        return d;
    }

    /** Write one pack -- the screenshot (when there is one) and ONE markdown file: the request of this share, the
     *  reading instructions, then the data snapshot -- and return the content URIs, screenshot first. The request is
     *  IN the file because the Claude app does not prefill its composer from a multi-file share (seen 2026-09-24):
     *  the user can press send on the attachments alone. */
    static ArrayList<Uri> write(Context c, String gen, Bitmap shot, String ask, String prompt, String snap) throws IOException {
        File d = dir(c);
        File[] old = d.listFiles();
        if (old != null) for (File f : old) //noinspection ResultOfMethodCallIgnored
            f.delete();
        String stamp = (gen == null || gen.isEmpty() ? String.valueOf(System.currentTimeMillis() / 1000) : gen)
                .replace(" ", "_").replace(":", "");
        ArrayList<Uri> out = new ArrayList<>();
        if (shot != null) {
            File p = new File(d, "smc_flow_screen_" + stamp + ".png");
            try (FileOutputStream fo = new FileOutputStream(p)) { shot.compress(Bitmap.CompressFormat.PNG, 100, fo); }
            out.add(uri(p));
        }
        File m = new File(d, "smc_flow_auction_" + stamp + ".md");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(m), StandardCharsets.UTF_8)) {
            if (ask != null && !ask.isEmpty()) w.write("# What I am asking now\n\n" + ask + "\n\n---\n\n");
            w.write(prompt == null ? "" : prompt);
            w.write("\n\n---\n\n## The data snapshot (generated " + gen + " UTC)\n\n```json\n");
            w.write(snap == null ? "{}" : snap);
            w.write("```\n");
        }
        out.add(uri(m));
        return out;
    }

    private static Uri uri(File f) {
        return new Uri.Builder().scheme("content").authority(AUTH).appendPath(f.getName()).build();
    }

    private File fileFor(Uri u) throws FileNotFoundException {
        String name = u.getLastPathSegment();
        if (name == null || name.isEmpty() || name.contains("/") || name.contains("\\") || name.startsWith("."))
            throw new FileNotFoundException(String.valueOf(u));
        File f = new File(dir(getContext()), name);
        if (!f.isFile()) throw new FileNotFoundException(name);
        return f;
    }

    @Override public boolean onCreate() { return true; }

    @Override public String getType(Uri u) {
        String n = String.valueOf(u.getLastPathSegment());
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".md")) return "text/markdown";
        if (n.endsWith(".json")) return "application/json";
        return "application/octet-stream";
    }

    @Override public ParcelFileDescriptor openFile(Uri u, String mode) throws FileNotFoundException {
        if (mode == null || !mode.startsWith("r") || mode.contains("w")) throw new SecurityException("read only");
        return ParcelFileDescriptor.open(fileFor(u), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    /** The name and size a receiving app asks for (the attachment chip). */
    @Override public Cursor query(Uri u, String[] projection, String selection, String[] args, String sort) {
        File f;
        try { f = fileFor(u); } catch (FileNotFoundException e) { return null; }
        String[] cols = projection != null ? projection : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override public Uri insert(Uri u, ContentValues v) { throw new UnsupportedOperationException("read only"); }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}
