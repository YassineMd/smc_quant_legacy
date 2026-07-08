"""Calibration probe for the market-structure swing detector. The live overlay uses a k=5 fixed-bar fractal
(strict >) which MISSES visually-obvious swings on irregular volume bars (ties + fast moves where the extreme
isn't cleanly isolated by 5 bars each side). This compares swing DENSITY for: the fixed-bar fractal at k in
{2,3,5,8}, and a ZigZag %-retracement detector at a few thresholds. Density = swings per 100 bars (higher =
more labels, catches smaller swings; lower = only the big turns). Helps pick the calibration lever.
Run: python study/structure_calibrate.py
"""
import os, sys, glob, json, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)


def load_1m():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    return [by[b] for b in sorted(by)]


def fractal(H, L, k):
    """Fixed-bar fractal (strict >): pivot high at j if H[j] beats the k bars on each side. -> #highs, #lows."""
    n = len(H); ph = pl = 0
    for j in range(k, n - k):
        h = H[j]; l = L[j]
        if h > max(H[j - k:j]) and h > max(H[j + 1:j + k + 1]):
            ph += 1
        if l < min(L[j - k:j]) and l < min(L[j + 1:j + k + 1]):
            pl += 1
    return ph, pl


def zigzag(H, L, thr):
    """%-retracement ZigZag: ride the running extreme; when price reverses by thr FROM it, confirm the extreme
    as a pivot and flip. Confirmation lag = however long the reversal takes (event-based, not fixed bars)."""
    n = len(H)
    if n < 2:
        return []
    piv = []; dirn = 0
    hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        if dirn >= 0:
            if H[i] >= hi:
                hi, hi_i = H[i], i
            elif L[i] <= hi * (1 - thr):
                piv.append((hi_i, hi, True)); dirn = -1; lo, lo_i = L[i], i
        if dirn <= 0:
            if L[i] <= lo:
                lo, lo_i = L[i], i
            elif H[i] >= lo * (1 + thr):
                piv.append((lo_i, lo, False)); dirn = 1; hi, hi_i = H[i], i
    return piv


def main():
    raws = load_1m()
    H = [float(d.get("high", 0.0)) for d in raws]; L = [float(d.get("low", 0.0)) for d in raws]
    n = len(H)
    print("1m tape: %d bars\n" % n)
    print("FIXED-BAR FRACTAL (current detector; live uses k=5):")
    print("  k | swings | per-100-bars | avg bars between swings")
    for k in (2, 3, 5, 8):
        ph, pl = fractal(H, L, k)
        tot = ph + pl
        print("  %d | %5d  |    %5.2f     |  %5.1f" % (k, tot, 100.0 * tot / n, n / max(1, tot)))
    print("\nZIGZAG %%-retracement (event-based, matches the eye):")
    print("  thr%% | swings | per-100-bars | avg bars between | median swing size%%")
    import statistics
    for thr in (0.0008, 0.0012, 0.0018, 0.0025, 0.004):
        piv = zigzag(H, L, thr)
        sizes = [abs(piv[i][1] - piv[i - 1][1]) / piv[i - 1][1] * 100.0 for i in range(1, len(piv))]
        med = statistics.median(sizes) if sizes else 0.0
        print("  %.2f | %5d  |    %5.2f     |     %5.1f       |   %.3f"
              % (thr * 100, len(piv), 100.0 * len(piv) / n, n / max(1, len(piv)), med))


if __name__ == "__main__":
    main()
