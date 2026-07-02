# TP-vs-SL Barrier Study (Phase 1–2 · extraction)

Offline triple-barrier labeling + feature extraction over the SMC daemon's **1m** history. Fully offline
on Yassine's machine; **zero daemon changes, zero VM writes** (beyond `rm /tmp` in the pull). Spec:
[`STUDY_SPEC.md`](STUDY_SPEC.md) · column contract: `feature_registry.json` (1281 coded fields).

- **`pull_snapshot.ps1`** — Phase-1 consistent-snapshot pull off the VM (READ-ONLY, daemon untouched,
  datestamped to `data/`). The 10k/tf cap makes 1m history a **~4-day rolling window**; run every **~3 days**
  to accumulate non-overlapping windows: `powershell -ExecutionPolicy Bypass -File study\pull_snapshot.ps1`
- **`extract.py`** — the §2 labeler: episodes from index 16, barriers +0.5% / −0.3%, ambiguity→SL, 6h horizon.
- **`features.py` / `features_b.py`** — entry-legal feature engine + B-scope Mode-10 panels; every derived
  measure REUSES the repo's own pure functions (`full_snapshot`, `region_state`, `bucket_state`,
  `vpin_adaptive`, `quant_engine`) — bit-identical with production, no re-implementations.
- **`build_dataset.py`** — assembles `out/dataset.{parquet,csv}`, runs the §5 validation battery, and writes
  `out/extraction_report.md` + `out/deferred_codes.tsv`.

`data/` and the generated dataset files under `out/` are gitignored; the **report + `deferred_codes.tsv`**
are tracked (the banked deliverable + on-demand enrichment shopping list).

**Status: extraction CLOSED at 295/1281** (see `extraction_report.md` §9). The remaining codes are deferred
on-demand — they reopen only when the analysis phase pre-registers a feature subset.
