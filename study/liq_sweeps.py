"""PHASE 1 — LIQUIDITY SWEEP DETECTOR (offline, stored snapshots, both sides). Frozen Tier-A rule.

A SWEEP = a recent swing level pierced by a bar's wick, price closing back inside, with a forced-flow
climax and a vacuum ladder beyond the level (thin trading past it = a stop-hunt, not real acceptance).

TIER-A (ALL of), frozen:
 1. STRUCTURE — a confirmed pivot (k=5 each side) is pierced by the bar high/low AND the bar closes
    back INSIDE it. Upside: high > pivotHigh AND close <= pivotHigh. Downside: low < pivotLow AND
    close >= pivotLow. Swept level = the most-recent confirmed pivot the bar wicks through.
 2. FORCED-FLOW z >= 2 vs the 30-bucket trailing baseline on the FORCED-CLOSE side (ClS for an upside
    sweep = shorts covering into the spike; ClL for a downside sweep = longs stopped out) OR the
    liquidation field (liq_short/liq_long — UNPOPULATED in these snapshots, so ClS/ClL carries it),
    AND OI delta < 0 (positions net CLOSING = deleveraging, the signature of a stop/liq event).
 3. VACUUM LADDER — volume traded BEYOND the swept level (ladder b+s at prices past it) < 10% of the
    bar's total volume (price wicked through a void, not through real size).

TIER-B (decoys) = STRUCTURE + exactly ONE of {forced-flow, vacuum} — looks like a sweep, misses the
full signature. Flagged for the blind calibration pack.

Intent = the harvest side: an upside sweep traps longs / stops shorts and the mover SELLS -> "S";
a downside sweep traps shorts / stops longs and the mover BUYS -> "B".

Outputs: study/out/liq_sweeps.csv (Tier-A + Tier-B rows) + a blind CALIBRATION PACK (30 events:
Tier-A + 6 Tier-B decoys, shuffled, tier hidden) for operator eyeball grading -> measured precision.
No outcome study.
"""
import os, sys, csv, random
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from m10_sweep_s5b import load_merged                    # noqa: E402  (merged deduped 1m tape)

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
K = 5                    # pivot half-width (5 bars each side)
LOOKBACK = 100           # a swept pivot must be within this many bars back
Z_BASE = 30              # trailing baseline for the forced-flow z-score
Z_MIN = 2.0
VACUUM_MAX = 0.10        # volume beyond the level < 10% of bar volume
SEED = 13


def pivots(H, L):
    """Confirmed pivot highs/lows (strict local extreme over +-K). Returns index->price dicts;
    a pivot at j is only 'confirmed' K bars later (used causally below)."""
    n = len(H); ph, pl = {}, {}
    for j in range(K, n - K):
        w = slice(j - K, j + K + 1)
        if H[j] > max(H[j - K:j]) and H[j] > max(H[j + 1:j + K + 1]):
            ph[j] = H[j]
        if L[j] < min(L[j - K:j]) and L[j] < min(L[j + 1:j + K + 1]):
            pl[j] = L[j]
    return ph, pl


def main():
    bids, raws, per_db, gaps, dup = load_merged()
    n = len(raws)
    H = np.array([float(d["high"]) for d in raws]); L = np.array([float(d["low"]) for d in raws])
    C = np.array([float(d["close_price"]) for d in raws]); ts = np.array([float(d["end_time"]) for d in raws])
    vol = np.array([float(d.get("curr_vol", 0.0)) for d in raws])
    clS = np.array([float(d.get("clS", 0.0)) for d in raws]); clL = np.array([float(d.get("clL", 0.0)) for d in raws])
    oi = np.array([(float(d.get("opL", 0.0)) + float(d.get("opS", 0.0)))
                   - (float(d.get("clL", 0.0)) + float(d.get("clS", 0.0))) for d in raws])
    bid_arr = np.array(bids)
    ph, pl = pivots(H, L)
    ph_bars = sorted(ph); pl_bars = sorted(pl)
    print("merged %d bars | %d pivot highs / %d pivot lows" % (n, len(ph), len(pl)), flush=True)

    def zscore(arr, i):
        base = arr[i - Z_BASE:i]
        m, s = base.mean(), base.std()
        return (arr[i] - m) / s if s > 1e-9 else 0.0

    def beyond_frac(d, level, upside):
        lv = d.get("levels") or {}
        tot = sum(float(v.get("b", 0.0)) + float(v.get("s", 0.0)) for v in lv.values())
        if tot <= 0:
            return 1.0                                   # no ladder -> treat as NOT a vacuum (fail-safe)
        beyond = sum(float(v.get("b", 0.0)) + float(v.get("s", 0.0))
                     for p, v in lv.items() if (float(p) > level if upside else float(p) < level))
        return beyond / tot

    rows = []
    for i in range(max(Z_BASE, K + 1), n):
        for upside in (True, False):
            bars = ph_bars if upside else pl_bars
            # most-recent confirmed pivot the bar wicks through and closes back inside
            level = jlev = None
            for j in reversed(bars):
                if j + K > i - 1:                        # not confirmed before bar i yet
                    continue
                if j < i - LOOKBACK:                     # too old
                    break
                P = ph[j] if upside else pl[j]
                pierced = (H[i] > P and C[i] <= P) if upside else (L[i] < P and C[i] >= P)
                inside = (C[i] <= P) if upside else (C[i] >= P)
                if pierced and inside:
                    level, jlev = P, j
                    break
            if level is None:
                continue
            # signatures
            fz = zscore(clS if upside else clL, i)
            forced = fz >= Z_MIN and oi[i] < 0
            vac = beyond_frac(raws[i], level, upside)
            vacuum = vac < VACUUM_MAX
            sigs = int(forced) + int(vacuum)
            if sigs == 0:
                continue                                 # structure only -> not emitted
            tier = "A" if (forced and vacuum) else "B"
            wick = (H[i] - level) if upside else (level - L[i])
            rows.append(dict(ts=round(float(ts[i]), 3), bucket_id=int(bid_arr[i]),
                             side_label="S" if upside else "B",
                             swept_level=round(level, 4), wick_extent=round(wick, 4),
                             wick_pct=round(wick / level * 100.0, 4),
                             forced_z=round(float(fz), 2), oi_delta=round(float(oi[i]), 1),
                             vacuum_frac=round(float(vac), 4), tier=tier,
                             forced=int(forced), vacuum=int(vacuum)))

    rows.sort(key=lambda r: r["ts"])
    with open(os.path.join(OUT, "liq_sweeps.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# PHASE-1 liquidity sweeps (offline, merged 1m snapshots). Tier-A = structure + forced-flow "
                "(clS/clL z>=2 & OI<0) + vacuum (<10%% beyond level). Tier-B = structure + exactly one. "
                "side_label = harvest intent (upside sweep -> S, downside -> B). No outcome study.\n")
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
        for r in rows:
            w.writerow(r)
    nA = sum(1 for r in rows if r["tier"] == "A"); nB = len(rows) - nA
    print("emitted %d rows: Tier-A %d, Tier-B %d" % (len(rows), nA, nB), flush=True)

    # ---- blind calibration pack: 24 Tier-A + 6 Tier-B decoys, shuffled, tier hidden -------------
    rng = random.Random(SEED)
    A = [r for r in rows if r["tier"] == "A"]; B = [r for r in rows if r["tier"] == "B"]
    pick_A = rng.sample(A, min(24, len(A))); pick_B = rng.sample(B, min(6, len(B)))
    pack = pick_A + pick_B; rng.shuffle(pack)
    import time as _t
    with open(os.path.join(OUT, "liq_calibration_pack.md"), "w", encoding="utf-8") as f:
        f.write("# Liquidity-sweep calibration pack (BLIND — %d events)\n\n" % len(pack))
        f.write("_Grade each as **sweep** or **not** by eyeball in the terminal (Ctrl+F to the Idx, look at "
                "the wick vs the swept level, the close-back-inside, the flow). Tier is HIDDEN. Fill the "
                "verdict column; the answer key (liq_calibration_key.csv) reveals Tier-A vs Tier-B decoy "
                "afterwards so we can measure precision. Idx = terminal bucket Idx; ts shown UTC and UTC+1._\n\n")
        f.write("| # | Idx (Ctrl+F) | UTC | your local (UTC+1) | side | swept level | wick % | verdict (sweep? y/n) |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for e, r in enumerate(pack, 1):
            utc = _t.strftime("%m-%d %H:%M", _t.gmtime(r["ts"]))
            loc = _t.strftime("%m-%d %H:%M", _t.gmtime(r["ts"] + 3600))
            f.write("| %d | %s | %s | %s | %s | %.2f | %.3f |  |\n"
                    % (e, "%d" % r["bucket_id"], utc, loc, r["side_label"], r["swept_level"], r["wick_pct"]))
    with open(os.path.join(OUT, "liq_calibration_key.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["event", "bucket_id", "tier", "side_label", "forced_z", "vacuum_frac"])
        for e, r in enumerate(pack, 1):
            w.writerow([e, r["bucket_id"], r["tier"], r["side_label"], r["forced_z"], r["vacuum_frac"]])
    print("calibration pack: %d events (%d Tier-A + %d Tier-B decoys), key hidden separately"
          % (len(pack), len(pick_A), len(pick_B)), flush=True)


if __name__ == "__main__":
    main()
