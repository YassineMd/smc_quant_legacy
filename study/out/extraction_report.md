# TP-vs-SL Barrier Study — Extraction Report (Phase 1–2) · **EXTRACTION CLOSED at 295/1281**

_Generated 2026-07-02 12:47 UTC · hard stop after this report (no analysis)._


## 1. Snapshot & row counts

| item | value |
|---|---|
| snapshot UTC | 2026-07-02T11:17:07Z |
| file | history_snapshot_20260702.db, 67.3 MB |
| 1m buckets | 10000 (cap) · 5m 5961 · 15m 4496 · 1h 3086 · 4h 2733 |
| 1m span | 2026-06-28 11:03:57.228000 → 2026-07-02 11:17:01.139000 UTC |
| episodes | 19968 (idx 16→end, ×2 dir) · index-16 start honored |
| output | dataset.parquet + dataset.csv (1282 cols) |

## 2. Outcome distribution vs nulls

| joint (per bucket) | n | %% |
|---|---|---|
| UP-resolve | 3927 | 39.3 |
| DOWN-resolve | 3652 | 36.6 |
| WHIPSAW | 2380 | 23.8 |
| unresolved | 25 | 0.3 |

Per-direction TP rate (of resolved): **long 39.4%**, **short 36.7%** vs the **37.5% random-walk null** (= SL_dist/(TP+SL) = 0.3/0.8).  WHIPSAW **23.8%**.  UNRESOLVED long 0.1% / short 0.3% (all end-of-data).  Single-bucket ambiguous-hit (L.07) **0.000%** — ~0 because the 0.8%-wide barrier pair exceeds any 1m bar's range.

## 3. Conservation spot-checks (200 random buckets, §5.3)

| check | result |
|---|---|
| V·s + V·(1−s) == dominant V | 129/129 within 1e-6 (max err 3.02e-09) |
| Σ size-hist vol == buy+sell | 112/112 within 1e-3 (sz_* present only post-2026-06-30) |
| classify_bucket determinism | 200/200 reproduce stored state |
| T2 B-scope stats ↔ T1 per-bucket sums (S.03/05/06) | 180/180 exact |

## 4. NULL / mask coverage per family

| family | NULL cells | total | %% NULL |
|---|---|---|---|
| B- | 1517568 | 2575872 | 58.9 |
| C | 0 | 239616 | 0.0 |
| E | 11009214 | 14456832 | 76.2 |
| G | 2097108 | 2236416 | 93.8 |
| J- | 2575872 | 2575872 | 100.0 |
| K | 1852 | 239616 | 0.8 |
| L | 0 | 179712 | 0.0 |
| O | 185355 | 499200 | 37.1 |
| X- | 2575872 | 2575872 | 100.0 |

## 5. Column-contract audit

- **1281 / 1281** contract codes present + `bucket_id` identity col (schema hash `6b72e3478ee4289f`, 1282 cols total).

- **295 / 1281** codes carry ≥1 computed value (entry-legal core + labels + O-tail); **986** are fully NULL (deferred / not-computable / T2–T3), enumerated in deferred_codes.tsv.


Deferral reasons (top):

| reason | E-deriv count |
|---|---|
| catalog-transform-deferred | 327 |
| bespoke composite | 87 |
| T2b B-scope pending | 76 |
| six-hour store (depth.db / trade tape) — NOT COMPUTABLE (excluded by design) | 70 |
| order-block / cross-tf structure — deferred (needs order_blocks reconstruction) | 30 |
| structure/cross-tf composite | 15 |
| descriptive display anomaly (side E/R vs trailing-30 border) — deferred | 10 |
| descriptive neon-border flag — deferred | 10 |

**Not-computable (structural, will never fill from this snapshot):** E41 + E66–E72 (six-hour depth.db/tape, 80 derivs), E53 (size_thr — engine_state anchor), G12.4/G19.2-3 (tape sequencing). **Deferred to enrichment:** compound/bespoke E-derivations, most G composites, E63–E65 (order-block reconstruction).

**T2 B-scope (this tranche):** S stats + P1 ABSORPTION + P2 EFF-AGG + P3 E/R + P4 EXHAUSTION-core (**53/129** B- fields) computed FAITHFULLY by replicating the terminal's _refresh_selection_stats math (region_state pure fns + exact badge locked-index). **T2b pending:** phase panels P5–P7 (segmentation state machine), P8L/P8S (need the daemon's live size_thr anchor, not persisted), P9 composite lean, P0 smoothed twin, and P4 fire-counts/weakest-leg. **T3:** J-/X- scopes (post-hoc).


### Enrichment policy (architect-set, ON-DEMAND)

The registry is a catalog; deferred codes are **not** ground out speculatively. When the analysis phase pre-registers its feature subset, any deferred code on that list gets its exact formula from the architect and is then computed. The full deferral inventory is persisted to **`study/out/deferred_codes.tsv`** (code · family · reason) to shop from. No-guessing stands: a wrong-but-filled column is worse than an honest NULL.


## 6. Files & timing

- `study/out/dataset.parquet` — 19.24 MB
- `study/out/dataset.csv` — 97.07 MB
- `study/out/deferred_codes.tsv` — 0.06 MB
- extraction wall time: 74.1s


## 7. Deviations from spec (flagged, accepted)

1. **Phase-1 DB path** — `~/OrderFlowPlatform/data/history.db` (not `~/OrderFlowPlatform/history.db`). *Accepted by architect.*

2. **Phase-1 VACUUM** — no `sqlite3` CLI on VM → `python3 "VACUUM INTO"` (transaction-consistent, identical). *Accepted by architect.*

3. **Registry is a descriptive CATALOG, not a formula spec.** Base fields (72) map to production quantities reused bit-identically (full_snapshot + region_state/bucket_state/vpin/quant_engine). Derivations are filled ONLY where the text names one unambiguous generic transform (raw/z-trailing30/percentile/day-rank/streak/slope/sign/log) of the field's canonical scalar; compound or sub-quantity-specific texts are NULL+reason, never guessed (upholds the no-reimplementation rule).

4. **C.* window** = the 15 buckets strictly before entry `[i-15, i-1]` (pre-entry context). **O.* excursion** interpreted per §2 directional rule; magnitudes are %% of entry.

5. **KC/POC** frozen params used verbatim: EMA-20, 2.0×ATR-20 (SMA true-range), rolling-POC-240.


## 8. Leakage guard

Enforced mechanically by prefix: entry-legal = E*/G*/C.*/K.* (+ B-*, T2); post-hoc = L.*/O.* (+ J-*/X-*, T3). O.* filled this tranche are excursion outcomes — descriptive only, never entry-side.


## 9. Extraction closure (architect ruling — T1+T2 gates PASS)

Extraction is **CLOSED at 295/1281** computed. The remaining 986 codes are NOT built now, by ruling; they reopen **strictly on-demand** from the analysis phase's pre-registered feature subset (`deferred_codes.tsv` is the shopping list):

1. **On-demand catalog transforms** — 547 E + 105 G derivations whose text isn't one unambiguous generic transform; exact formula supplied when a code is pre-registered.

2. **Composites derivable from the banked P1–P4 series** — P9 composite lean, P0 smoothed twin: reconstructable on demand from the already-extracted panel columns, no new primitives.

3. **Blocked on the unpersisted `size_thr` anchor** — B-P8L/P8S (+ E52/E53). A histogram-derived threshold **PROXY is possible later, flagged APPROXIMATE**, on demand — not the daemon's live anchor.

4. **Post-hoc-only** — J-/X- scopes (258): belong to the outcome-unfolding analysis, never entry-side.


**Snapshot cadence.** The 10k/tf cap makes 1m history a **~4-day ROLLING window**; each snapshot freezes one window permanently. `study/pull_snapshot.ps1` runs the Phase-1 pull (datestamped, read-only, daemon untouched) — run every ~3 days to accumulate non-overlapping windows offline.


**Hard stop holds. The analysis phase opens only on Yassine's explicit instruction.**
