# smc_quant — TP-vs-SL Barrier Study: Extraction Spec (Phase 1–2)

**Status:** frozen. Companion file: `feature_registry.json` (1,281 coded fields — the column contract).
**Scope of this spec:** data download + episode labeling + feature extraction + validation report. **HARD STOP after that.** Analysis is a separate, later phase on Yassine's explicit instruction.

---

## 0. Ground rules

- Fully **offline** study on Yassine's Windows machine inside the repo (`study/` folder, `study/data/` gitignored). Zero daemon changes, zero deploys, zero writes to the VM.
- Timeframe: **1m buckets only.**
- Reuse the repo's own pure functions (`quant_engine`, `bucket_state`) for every derived measure — bit-identical with production, no re-implementations.
- Registry codes are the only column names. Do not invent, rename, or silently drop codes; a field that cannot be computed gets a NULL column + an entry in the report's "not computable" table with the reason.

## 1. Data download (consistent snapshot, daemon undisturbed)

1. On the VM via SSH (read-only footprint):
   `sqlite3 ~/OrderFlowPlatform/history.db "VACUUM INTO '/tmp/history_snapshot.db'"`
   (transaction-consistent copy; WAL readers don't block the daemon).
2. Pull it down: `gcloud compute scp smc-quant-eu:/tmp/history_snapshot.db study/data/history_snapshot_YYYYMMDD.db --zone=europe-west9-b --project=yass-chart`
3. Remove the temp file on the VM: `rm /tmp/history_snapshot.db` (the one permitted remote write — it's /tmp).
4. Record in the report: snapshot UTC time, file size, per-tf bucket counts, 1m date span.

`depth.db` / trade tape are **not** downloaded (6h retention; excluded from this pass by design).

## 2. Episode construction (the labeler)

- Universe: 1m closed buckets, chronological. **First studied bucket = index 16** (needs 15 priors for the base selection). Windowed fields needing deeper lookback (trailing-30, trailing-240, KC-20, POC-240) are NULL until their window fills — masked, never zero-filled.
- **Entry** = the studied bucket's close price, at its close timestamp.
- Barriers (gross, fee-free by design; fees enter at evaluation): **TP = ±0.5%**, **SL = ∓0.3%** from entry. Long: TP above, SL below. Short: mirrored.
- **Two rows per bucket**: `direction ∈ {long, short}`. Entry-legal features are identical across the pair (analysis dedupes on `bucket_id`).
- Barrier scan: walk subsequent 1m buckets; a barrier is touched when the bucket's high/low reaches it. **Ambiguity rule:** if one bucket's range spans BOTH barriers, outcome = **SL** (conservative), `L.07 = true`. Report the ambiguous fraction prominently.
- **Horizon:** 6h (360 min) from entry close. Neither barrier hit in time → `L.02 = UNRESOLVED`, censor reason `6h-window`. Entries too close to the dataset's end for a full 6h → censor reason `end-of-data`.
- Excursions (O.06–O.25): per the registry texts. **Directional rule:** TP episodes measure the post-TP max only in the position's favor; SL episodes only the adverse continuation. Max at the window edge → `O.11` censored flag.

## 3. The three selections (Mode-10 scopes)

For every episode, extract the full panel + stats set (`P*.*`, `S.*`) three times:
- **B-** BASE: the 15 buckets before entry + the entry bucket (16). ENTRY-LEGAL.
- **J-** JOURNEY: entry bucket → the barrier-touch bucket (per direction). POST-HOC. NULL when UNRESOLVED.
- **X-** EXCURSION: entry bucket → the directional 6h-max bucket. POST-HOC. NULL when UNRESOLVED.

Panel semantics come from the terminal's own Mode-10 code paths (badges = percentages/spread; P4 = value + last-exhausted side; P8 thresholds = the daemon's broad size_thr anchors, not selection-local; P9/P0 = composite lean and its smoothed twin incl. confirmed ±50/0/−50 crosses). Where a panel value is display-smoothed, extract BOTH the smoothed and the raw per-bucket series value where the registry item says "raw".

## 4. Output artifacts

- `study/out/dataset.parquet` — canonical, typed. One row per (bucket_id, direction).
- `study/out/dataset.csv` — same columns, for human inspection.
- `study/out/extraction_report.md` — the validation report (below).
- Key columns first: `L.09 episode_id, bucket_id, L.06 entry_ts, L.01 direction, L.02 outcome`, then registry order.

## 5. Validation report (required before the stop)

1. Row counts (episodes, per direction), 1m date span, index-16 start honored.
2. **Outcome distribution vs the nulls:** TP rate per direction vs the 37.5% random-walk null; WHIPSAW rate; UNRESOLVED rate; ambiguous-hit fraction.
3. Conservation spot-checks on ≥100 random buckets: V·s + V·(1−s) == V; size-histogram sums == counts/volumes; `classify_bucket` recompute matches stored-era behavior.
4. NULL/mask coverage per column family (window warm-up, sz_* age, liq_* age).
5. Column-contract check: every registry code accounted for (present, or in the not-computable table with reason). Schema hash of final column list.
6. File sizes + timings.

## 6. Leakage guard (verbatim from the registry)

`J-*`, `X-*`, `L.*`, `O.*` contain post-entry information. They may be compared between TP and SL groups to understand HOW outcomes unfold — they must NEVER enter an entry-decision rule or model. Entry-legal universe: `E*`, `G*`, `C.*`, `K.*`, `B-*` only. Enforce mechanically by prefix.

## 7. Hard stop

After the dataset + report: **stop.** No pattern mining, no correlations, no model. Yassine reviews the report and dictates the analysis phase separately.
