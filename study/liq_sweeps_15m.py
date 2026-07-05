"""LIQUIDITY SWEEP DETECTOR — 15m timeframe (was 1m). Same FROZEN Tier-A rule, run via the terminal's own
app.liq_detect.detect_sweeps (pivot k=5 pierced + close-back-inside + forced-flow clS/clL z>=2 & OI<0 +
vacuum <10% beyond the level), computed on 15m buckets and their ladders/close-flow fields from the study
snapshots. TIER-A ONLY output.

Why 15m is fully gradeable: the daemon caps closed_buckets at 10k/tf, but total_closed_15m is well under 10k
(4984 live @ 2026-07-05), so the terminal loads the ENTIRE 15m history — every event is viewable, unlike 1m
(which rolls a ~4-day window). GRADEABLE floor = max(1, live_edge - 10000) = 1.

Outputs:
  study/out/liq_sweeps_15m.csv          — Tier-A 15m sweeps (same columns as the 1m table)
  study/out/liq_calibration_pack_15m.md — up to 30 Tier-A, sequence # + Idx + blank verdict (no tier/signature)
  study/out/liq_calibration_key_15m.csv — matching answer key (signatures, for after grading)
"""
import os, sys, csv, json, sqlite3, random
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from app import liq_detect                                # the SAME frozen detector the terminal runs live

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
DBS = ("study/data/history_snapshot_20260702.db", "study/data/history_snapshot_20260703.db")
TF = "15m"
SEED = 13
GRADEABLE_MIN_IDX = 1     # 15m loaded window = max(1, total_closed_15m - 10000); total (4984) < 10k -> all of it


def load_tf(tf):
    """Merge the snapshot DBs for `tf` into one continuous per-tf-Idx span (same scheme as
    m10_sweep_s5b.load_merged: Idx = total_closed_<tf> - len + j + 1; the later db wins on dupes)."""
    by_bid = {}
    for db in DBS:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(REPO, db), uri=True)
        raw = [json.loads(x[0]) for x in con.execute(
            "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,))]
        row = con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_%s" % tf,)).fetchone()
        tc = int(row[0]) if row and row[0] is not None else len(raw)
        con.close()
        base = tc - len(raw)
        for j, d in enumerate(raw):
            by_bid[base + j + 1] = d
    bids = sorted(by_bid)
    return bids, [by_bid[b] for b in bids]


def main():
    bids, raws = load_tf(TF)
    print("loaded %d %s buckets, Idx %d..%d" % (len(raws), TF, bids[0], bids[-1]), flush=True)
    evs = liq_detect.detect_sweeps(raws)                  # emits Tier-A + Tier-B; we keep Tier-A
    rows = []
    for e in (x for x in evs if x["tier"] == "A"):
        i = e["i"]; d = raws[i]
        hi = float(d.get("high", 0.0)); lo = float(d.get("low", 0.0)); lvl = e["level"]
        wick_extent = (hi - lvl) if e["side"] == "S" else (lvl - lo)
        rows.append(dict(ts=round(float(d.get("end_time", 0.0)), 3), bucket_id=int(bids[i]),
                         side_label=e["side"], swept_level=round(lvl, 4),
                         wick_extent=round(wick_extent, 4), wick_pct=e["wick_pct"],
                         forced_z=e["forced_z"], oi_delta=e["oi_delta"], vacuum_frac=e["vacuum_frac"],
                         tier="A", forced=e["forced"], vacuum=e["vacuum"]))
    rows.sort(key=lambda r: r["bucket_id"])
    with open(os.path.join(OUT, "liq_sweeps_15m.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# PHASE-1 liquidity sweeps, 15m timeframe (study snapshots). Frozen Tier-A rule = structure "
                "(k=5 pivot pierced + close back inside) + forced-flow (clS/clL z>=2 & OI<0) + vacuum "
                "(<10%% beyond level). TIER-A ONLY. side_label = harvest intent (upside sweep -> S, downside -> B).\n")
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
        for r in rows:
            w.writerow(r)
    print("emitted %d Tier-A 15m sweeps" % len(rows), flush=True)

    # ---- calibration pack (15m Tier-A): gradeable (Idx >= floor), up to 30, all if fewer ----------------
    rng = random.Random(SEED)
    gradeable = sorted((r for r in rows if r["bucket_id"] >= GRADEABLE_MIN_IDX), key=lambda r: r["bucket_id"])
    pack = (gradeable if len(gradeable) <= 30
            else sorted(rng.sample(gradeable, 30), key=lambda r: r["bucket_id"]))
    with open(os.path.join(OUT, "liq_calibration_pack_15m.md"), "w", encoding="utf-8") as f:
        f.write("# Liquidity-sweep calibration pack — 15m, TIER-A ONLY (%d events)\n\n" % len(pack))
        f.write("_%d gradeable Tier-A 15m sweeps (Idx >= %d; the 15m chart loads its full history, so every "
                "event is viewable). Grade each by eyeball in the terminal: Ctrl+F to the Idx and judge whether "
                "it is a genuine sweep (wick pierces the swept level, close back inside, flow climax). Fill the "
                "verdict; no tier or signature is shown — this measures 15m Tier-A precision directly._\n\n"
                % (len(pack), GRADEABLE_MIN_IDX))
        f.write("| # | Idx (Ctrl+F) | verdict (sweep? y/n) |\n")
        f.write("|---|---|---|\n")
        for e, r in enumerate(pack, 1):
            f.write("| %d | %d |  |\n" % (e, r["bucket_id"]))
    with open(os.path.join(OUT, "liq_calibration_key_15m.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["event", "bucket_id", "side_label", "forced_z", "vacuum_frac"])
        for e, r in enumerate(pack, 1):
            w.writerow([e, r["bucket_id"], r["side_label"], r["forced_z"], r["vacuum_frac"]])
    print("15m calibration pack: %d Tier-A events" % len(pack), flush=True)


if __name__ == "__main__":
    main()
