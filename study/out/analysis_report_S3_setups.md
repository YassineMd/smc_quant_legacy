# S3 — Trader-Setup Composite Study

_External pre-registered hypotheses (Yassine's live strategies), frozen thresholds, screened on the FULL
4 days against the existing 0.5/0.3/6h labels. **Characterization — forward data is the judge.** Slices:
discovery (bear-leaning) / spent-holdout (bull, known regime). References per slice: that direction's
baseline · 37.5% geometric null · **~50.0% real breakeven** (taker in+out 0.10% RT, VIP0 assumption —
flagged; = (0.3+0.1)/0.8 exactly)._

## STEP 0 — Channel identification (from source)
The price-chart bands **are** the code's Keltner overlay (`_keltner_bands` → `kc_upper`/`kc_lower`,
[terminal.py:4863](../../app/terminal.py)): **EMA(close, 20) ± 2.25 × Wilder-ATR(20)**, computed over the
rendered bucket history ([config.py:323-324](../../app/config.py): `KELTNER_LENGTH=20`,
`KELTNER_ATR_MULT=2.25`). The EMA **midline is hidden** on screen (operator pref) — the visible center line
is a different object: the **POC 5% EMA baseline** (`baseline = poc·0.05 + prev·0.95`).
⚠ Discrepancy for the record: the registry froze K.* as 2.0×ATR; the terminal renders **2.25**. All channel
legs here use the terminal's 2.25 per the ruling.

## Frozen conventions (judgment calls — flagged, not tuned)
A2/A4/A5 use a trailing **W=64** selection with the terminal's pre-rolls and LOCKED index (last−7); P0
crosses evaluated on the global series (long-selection limit). A1: "traded-through" = earlier same-UTC-day
bucket with low<level<high; retest-from-above = low ≤ level·1.0005 AND close ≥ level; VA = standard 70%
expansion. A6: top decile = p90 of large-side volume over all sz-present rows (fixed-273c). A7: candle BODY
vs band. B2: strictly monotone highs from overshoot+1 through i−1 (≥2); B3: LS line through those highs,
close above at i. KC warm-up from snapshot start (converged before entries). Full details in
[setups_S3.py](../setups_S3.py) header.

## Setup A "VP-level reversal" — the 7-leg AND: **0 fires in 4 days**
Survival of the long-side AND, leg by leg: A1 → 199 rows · +A2 → 20 · +A3 → 17 · +A4 → 15 · +A5 → 15 ·
+A6 → **0** (A6 undefined on 46% of rows — sz began 2026-06-30 — and its p90+side condition rarely
coincides with the rest). Honest verdict: **the full recipe is too strict to characterize on 4 days** —
at this survival rate it fires ≪1/day. Not evidence against the setup; evidence the sample can't see it.
No thresholds were loosened (per spec).

## Ablation A1–A7 (each leg alone, full 4 days; per-direction baseline in brackets)

| leg | long TP% (Δ) | long whip | short TP% (Δ) | short whip | n L/S |
|---|---|---|---|---|---|
| **A1 VP-retest** | **47.7 (+8.4)** CI[34,61] | **11.6** | 29.9 (−6.8) | 31.8 | 199/214 |
| A2 P0 2-bull-crosses | 37.2 (−2.1) | 23.6 | 36.7 (+0.0) | 26.3 | 779/676 |
| A3 eff-agg ≥65 | 42.9 (+3.5) | 23.7 | 41.1 (+4.4) | 24.2 | 2928/2632 |
| A4 phase=START/DURING | 40.8 (+1.5) | 23.5 | 38.5 (+1.8) | 24.0 | 6469/4970 |
| A5 P6 spread ≥15 | 42.8 (+3.4) | 23.5 | 39.4 (+2.8) | 25.0 | 4021/2420 |
| A6 big-player | 39.4 (+0.1) | 26.0 | 36.3 (−0.4) | 21.3 | 1547/1481 |
| A7 channel stretch | 42.1 (+2.7) | 28.8 | 37.7 (+1.0) | 24.0 | 1133/1570 |

**A1 long is the standout — and it survives the slice split** (the only leg that does):

| A1 slice | n | TP% (Δ vs slice baseline) | whipsaw |
|---|---|---|---|
| long · discovery | 155 | 45.2 (**+9.5**) | **7.1** (baseline ~24) |
| long · spent-holdout | 23 | 69.6 (**+19.0**) | 8.7 |
| short · discovery | 177 | 36.2 (−3.9) | 26.6 |
| short · spent-holdout | 12 | 0.0 (−27.2) | 75.0 |

Same sign in both regimes long, whipsaw **~3× below baseline** — the cleanest anti-chop conditioning seen in
this entire program. The short mirror is *negative* in both slices (n tiny on holdout). A3 is the only other
leg positive in all four cells (+0.9…+6.3). Nothing clears the 50% breakeven except A1-long-holdout (n=23 —
indicative only).

## Setup B "overshoot → correction-line break" (composite)

| slice | n | rate | TP% (Δ vs baseline) | 90% CI | blocks | whip (base) |
|---|---|---|---|---|---|---|
| long · disc | 141 | 2.1% | 34.8 (−0.9) | [25,43] | 21 | 19.9 (24.2) |
| long · hold | 52 | 2.0% | 53.8 (+3.2) | [43,64] | 8 | 28.8 (21.8) |
| short · disc | 108 | 1.6% | 38.9 (−1.2) | [28,48] | 22 | 25.0 (24.2) |
| short · hold | 27 | 1.1% | 14.8 (−12.4) | [5,30] | 8 | 18.5 (21.9) |

Sign-inconsistent across slices, CIs span the baselines everywhere — **uncharacterized on this sample**; the
long-holdout +3.2 (CI spans breakeven) is the only cell worth watching forward.

## Deliverables & stop
[setups_S3_fires.csv](setups_S3_fires.csv) — all 340 fires (ts, bucket_id, side, legs bitmap, outcome) for
eyeballing actual fire moments. Everything above is **characterization on spent/known-regime data**; the
judge is forward snapshots. **HARD STOP.**
