# -*- coding: utf-8 -*-
"""Load the CYCLE ARCHIVE: every harvest in study/cycle_archive/data/ merged into one table, with the honesty numbers a
strategy test needs before it starts -- coverage, gaps, and how much the same cycle's numbers MOVED between harvests.

    from study.cycle_archive.load_cycles import load_archive
    A = load_archive()            # dict of arrays, one row per cycle; A["report"] is the text printed below
    python study/cycle_archive/load_cycles.py            # print the report
    python study/cycle_archive/load_cycles.py --pull     # first mirror gs://.../cycles/ into data/ (additive)

MERGE RULE. Harvests overlap (each is the daemon's rolling 72 h). A cycle seen by several is taken from a harvest
whose WALL GRID was complete when its views were read (2026-09-24 on; the earlier collector read its oldest views before
the grid had reached them, and 22 of 793 shared cycles came out different), and among those from the one in which it
sits DEEPEST past that harvest's own window start: its baselines (the previous N cycles, the previous N same-side
cycles) then stand on the most history, and a number read a few hours into a window is the one the live terminal would
have shown. The first `lookback` hours of every harvest carry no pane rows at all, by construction.

⚠ The pane numbers are those of the harvest's OWN commit (cycle rule, impact coefficients): harvests before 2026-09-23
used the old cycle rule and harvests before the 2026-09-24 refit the old coefficients -- their numbers differ from later
ones for the SAME cycle (all 73 shared cycles of the 09-21 and 09-24 harvests do). A test must stay within one rule, or
recompute every number from the harvested 1 s bins and wall grid.

⚠ A GAP is any stretch no harvest covered with pane rows. Cycles there are in the table (from the uncapped reads of
the neighbouring harvests, where those reach) but have no interest x impact numbers -- and a Takeover mark that could
not be drawn is a signal that is MISSING, not one that did not fire. The report lists every gap; a test must exclude
them rather than count them as quiet tape."""
import os, sys, glob, json, time, shutil, subprocess
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
GCS = "gs://smc-quant-archive/solusdt/cycles/"
CYC = ("t", "t_end", "done", "is_buy", "strong", "move", "cbuy", "csell", "o", "c", "h", "l")


def pull():
    """Mirror the bucket's harvests into data/ (additive: never deletes a local file)."""
    gs = shutil.which("gsutil") or shutil.which("gsutil.cmd")
    if not gs:
        raise RuntimeError("gsutil not found")
    os.makedirs(DATA, exist_ok=True)
    subprocess.run([gs, "-m", "rsync", GCS, DATA], check=True)


def _harvests():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "cycles_*.npz"))):
        try:
            z = np.load(f, allow_pickle=False)
            meta = json.loads(str(z["meta"]))
            out.append((f, z, meta))
        except Exception as ex:                                   # a truncated upload must not take the archive down
            print("  skipped %s: %s" % (os.path.basename(f), ex))
    return out


def load_archive(verbose=False):
    hs = _harvests()
    if not hs:
        raise RuntimeError("no harvest in %s" % DATA)
    rows = {}          # cycle start -> (depth into its harvest, harvest index, cycle tuple, iimp row or None, colour, badge)
    seen = {}          # cycle start -> [(harvest index, liib, liis)] for the stability read
    cols = None
    spans = []
    for hi, (f, z, meta) in enumerate(hs):
        cols = cols or list(meta["iimp_cols"])
        quality = 1 if meta.get("wall_grid_complete") else 0
        a0 = float(meta["store"][0])
        ci = {c: i for i, c in enumerate(meta["iimp_cols"])}
        R = z["iimp"]
        ri = {round(float(x), 2): j for j, x in enumerate(R[:, ci["x0"]])} if R.size else {}
        colr = {round(float(k), 2): int(v) for k, v in z["colours"]} if z["colours"].size else {}
        bdg = {round(float(k), 2): int(v) for k, v in z["badges"]} if z["badges"].size else {}
        if R.size:
            spans.append((float(R[:, ci["x0"]].min()), float(R[:, ci["x1"]].max()), os.path.basename(f)))
        t = z["t"]
        for j in range(int(t.size)):
            if not bool(z["done"][j]):
                continue
            k = round(float(t[j]), 2)
            r = R[ri[k]] if k in ri else None
            if r is not None:
                seen.setdefault(k, []).append((hi, float(r[ci["liib"]]), float(r[ci["liis"]])))
            depth = float(t[j]) - a0
            cur = rows.get(k)
            # a row WITH pane numbers beats one without; then a harvest with a complete wall grid; then the deeper one
            rank = (1 if r is not None else 0, quality, depth)
            if cur is None or rank > cur[0]:
                rows[k] = (rank, hi, tuple(float(z[c][j]) for c in CYC), r, colr.get(k, -9), bdg.get(k, 0))
    ks = sorted(rows)
    n = len(ks)
    out = {c: np.array([rows[k][2][i] for k in ks], dtype=np.float64) for i, c in enumerate(CYC)}
    out["harvest"] = np.array([rows[k][1] for k in ks], dtype=np.int64)
    out["rated"] = np.array([rows[k][3] is not None for k in ks], dtype=bool)
    for i, c in enumerate(cols):
        out["ii_" + c] = np.array([(rows[k][3][i] if rows[k][3] is not None else np.nan) for k in ks], dtype=np.float64)
    out["colour"] = np.array([rows[k][4] for k in ks], dtype=np.int64)          # -9 = never seen by the PRICE pane's cache
    out["takeover"] = np.array([rows[k][5] for k in ks], dtype=np.int64)        # +1 / -1 as the terminal DREW it, 0 none
    out["harvest_files"] = [os.path.basename(f) for f, _z, _m in hs]
    # ---- coverage: stretches with pane rows, and the holes between them
    spans.sort()
    merged = []
    for a, b, _f in spans:
        if merged and a <= merged[-1][1] + 600.0:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps = [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]
    out["covered"] = merged; out["gaps"] = gaps
    # ---- stability: the same cycle read by two harvests
    d = []
    for k, v in seen.items():
        if len(v) >= 2:
            d.append(max(abs(v[0][1] - x[1]) for x in v[1:]) if True else 0.0)
            d.append(max(abs(v[0][2] - x[2]) for x in v[1:]))
    d = np.array(d, dtype=np.float64)
    hms = lambda x: time.strftime("%Y-%m-%d %H:%M", time.localtime(x))
    tk = out["takeover"]
    cov_h = sum(b - a for a, b in merged) / 3600.0
    rep = ["CYCLE ARCHIVE: %d harvest(s), %d cycles, %d with interest x impact numbers (%.0f%%)" % (len(hs), n, int(out["rated"].sum()), 100.0 * out["rated"].mean()),
           "  covered with pane rows: %.1f h in %d stretch(es): %s" % (cov_h, len(merged), "; ".join("%s -> %s" % (hms(a), hms(b)) for a, b in merged)),
           "  GAPS (exclude them from any test): %s" % ("none" if not gaps else "; ".join("%s -> %s (%.1f h)" % (hms(a), hms(b), (b - a) / 3600.0) for a, b in gaps)),
           "  TAKEOVER marks as the terminal drew them: %d (buy %d / sell %d) = %.1f per 24 h of covered tape"
           % (int(np.sum(tk != 0)), int(np.sum(tk > 0)), int(np.sum(tk < 0)), (np.sum(tk != 0) / cov_h * 24.0) if cov_h > 0 else 0.0)]
    if d.size:
        rep.append("  the same cycle in two harvests: |I x I difference| median %.4f, p90 %.4f, max %.3f log2 (%d readings) -- how far a number moves with the window it is read in"
                   % (float(np.median(d)), float(np.percentile(d, 90)), float(d.max()), int(d.size)))
    else:
        rep.append("  no cycle has been read by two harvests yet -- the stability read starts with the second one")
    out["report"] = "\n".join(rep)
    if verbose:
        print(out["report"])
    return out


def load_wall_grid(verbose=False):
    """Every harvest's 15 s WALL GRID merged: {"col", "ask", "bid", "mid"} arrays sorted by column (column k covers
    [k*C, (k+1)*C)), "C", "radius", and "gaps" (runs of missing columns, in unix seconds). A FINAL reading beats a
    provisional one; among equals the later harvest wins. A cycle's wall is the terminal's rule: column floor(t / C) - 1,
    asks for a buy cycle, bids for a sell one (see wall_at). Harvests before 2026-09-24 carry no grid."""
    best = {}
    C = None; radius = None
    for hi, (f, z, meta) in enumerate(_harvests()):
        if "wall_grid" not in z.files or not z["wall_grid"].size:
            continue
        C = float(meta.get("wall_col_secs", 15.0)) if C is None else C
        radius = int(meta.get("wall_radius", 25)) if radius is None else radius
        prov = set(int(k) for k in z["wall_prov"]) if "wall_prov" in z.files else set()
        for k, ask, bid, mid in z["wall_grid"]:
            k = int(k)
            rank = (0 if k in prov else 1, hi)
            if k not in best or rank > best[k][0]:
                best[k] = (rank, float(ask), float(bid), float(mid))
    ks = sorted(best)
    out = {"col": np.array(ks, dtype=np.int64), "C": C, "radius": radius}
    for i, nm in enumerate(("ask", "bid", "mid")):
        out[nm] = np.array([best[k][i + 1] for k in ks], dtype=np.float64)
    gaps = []
    for a, b in zip(ks[:-1], ks[1:]):
        if b - a > 1:
            gaps.append(((a + 1) * C, b * C))
    out["gaps"] = gaps
    if verbose:
        hms = lambda x: time.strftime("%Y-%m-%d %H:%M", time.gmtime(x))
        print("WALL GRID: %d columns%s; gaps: %s" % (len(ks), "" if not ks else " %s -> %s UTC" % (hms(ks[0] * C), hms((ks[-1] + 1) * C)),
              "none" if not gaps else "; ".join("%s -> %s" % (hms(a), hms(b)) for a, b in gaps)))
    return out


def wall_at(grid, t, is_buy):
    """The far side's resting $ at each cycle's OPEN from a merged grid -- the terminal's _iimp_wall rule."""
    tt = np.asarray(t, dtype=np.float64)
    out = np.full(tt.size, np.nan)
    if not grid["col"].size:
        return out
    cols = np.floor(tt / grid["C"]).astype(np.int64) - 1
    j = np.searchsorted(grid["col"], cols)
    ok = (j < grid["col"].size) & (grid["col"][np.minimum(j, grid["col"].size - 1)] == cols)
    jj = j[ok]
    val = np.where(np.asarray(is_buy, bool)[ok], grid["ask"][jj], grid["bid"][jj])
    val[grid["mid"][jj] <= 0] = np.nan
    out[ok] = val
    return out


if __name__ == "__main__":
    if "--pull" in sys.argv:
        pull()
    load_archive(verbose=True)
    load_wall_grid(verbose=True)
