# S5b — Corrected Confluence Study + M10 Sweep (1m, merged span)

_**Pre-registered CORRECTION of S5's leg 1 (mistranslation from the operator's screenshot, bucket idx 20977) — not a tweak: multiplicity +2 (long, short) -> program counter 454.** Legs 2-4 unchanged (65% share / START-DURING dominant / +15pp spread, frozen). The sweep table is INSTRUMENTATION — any rule found by browsing it must be pre-registered and judged on forward tape before it counts. Excursions are an information measure — no fees, no execution model. P8 is EXCLUDED from the sweep (size_thr never persisted)._

## Data
Merged 12575 bars (frozen 9686..19685 + fresh pull 12261..22260, dedup by bucket_id removed 7425 overlap rows), continuous bids 9686..22260, span 06-28 11:04 -> 07-03 17:25 UTC (5.26 days), gaps: none. Evaluable rows 12559 (first 16 excluded for lookback).

## Step 0 — anchor parity (gate PASSED)
Anchor = bucket idx 20977, end 2026-07-03 03:20:49 UTC (= 04:20:49 operator local, UTC+1); identity verified by idx + exact OHLC match (O 80.79 H 80.80 L 80.77 C 80.79).

| quantity | computed | screenshot |
|---|---|---|
| eff-agg badge (leg2 path) | 89.3% | 81% |
| absorption badge P1.01 | 12.0% | 26% (2nd badge) |
| P0 smoothed sum @lock | +52.2 | +55.6 |
| phase UP row | 11 / 51 / 38 | 13 / 52 / 35 |
| confirmed markers | up@50.0, up@0.0 | -50/0/+50 all green |
| fire_long | True | TRUE |

**Leg 1' semantics (the correction):** Panel-0's confirmed cross MARKERS — one most-recent confirmed cross per level {+50, 0, -50}, ZERO LINE INCLUDED, detected inside the locked slice of the 16-bar selection exactly as `_draw_level_crosses` draws its X's (settling/forming dots excluded; confirmed = new side holds >= 2 buckets within the locked slice). >= 2 markers required; the two most recent both up -> LONG, both down -> SHORT. S5's leg used S3's A2 instead (±50 only, 0-line excluded, both levels required) — that mistranslation made the leg nearly unfireable.

## Leg attrition (all 12559 evaluable bars)

| side | leg | standalone | cumulative (1..k) |
|---|---|---|---|
| long | 1' two most-recent markers on-side | 308 (2.45%) | 308 (2.452%) |
| long | 2 eff-agg >= 65% | 3638 (28.97%) | 239 (1.903%) |
| long | 3 phase dominant START/DURING | 8202 (65.31%) | 231 (1.839%) |
| long | 4 P6 spread >= 15pp | 5036 (40.10%) | 191 (1.521%) |
| short | 1' two most-recent markers on-side | 314 (2.50%) | 314 (2.500%) |
| short | 2 eff-agg >= 65% | 3257 (25.93%) | 238 (1.895%) |
| short | 3 phase dominant START/DURING | 6330 (50.40%) | 187 (1.489%) |
| short | 4 P6 spread >= 15pp | 3019 (24.04%) | 68 (0.541%) |

## Episodes (non-overlapping 30-min windows; 178 skipped inside open windows, 0 end-of-data excluded)

### LONG — 55 episodes (10.45 fires/day)

| metric | mean | median | p25 | p75 | p90 | max |
|---|---|---|---|---|---|---|
| MFE % | 0.422 | 0.261 | 0.080 | 0.561 | 0.801 | 2.500 |
| \|MAE\| % | 0.495 | 0.433 | 0.188 | 0.637 | 1.115 | 1.716 |

Time-to-max: up median 8.7 min / down median 12.0 min. Ratio view: median MFE **0.261%** vs median \|MAE\| **0.433%**; MFE > \|MAE\| in **41.8%** of episodes; end-of-window mean -0.026% / median -0.066%.

Control (null: 55 random non-overlapping 30-min windows, 200 seeded draws, seed 13):

| stat | actual | control (mean ± sd) |
|---|---|---|
| median MFE % | 0.261 | 0.372 ± 0.066 |
| median \|MAE\| % | 0.433 | 0.322 ± 0.050 |
| % MFE > \|MAE\| | 41.8 | 53.4 ± 6.7 |
| mean end % | -0.026 | +0.101 ± 0.077 |
| median end % | -0.066 | +0.025 ± 0.082 |

Regime split (cut 2026-06-30 00:00 UTC):

| regime | n | med MFE | med \|MAE\| | % MFE>\|MAE\| | med end |
|---|---|---|---|---|---|
| pre (chop) | 13 | 0.265 | 0.488 | 30.8 | -0.243 |
| post (bull) | 42 | 0.260 | 0.421 | 45.2 | -0.064 |

### SHORT — 26 episodes (4.94 fires/day)

| metric | mean | median | p25 | p75 | p90 | max |
|---|---|---|---|---|---|---|
| MFE % | 0.566 | 0.380 | 0.152 | 0.690 | 1.160 | 2.764 |
| \|MAE\| % | 0.444 | 0.322 | 0.193 | 0.759 | 0.963 | 1.006 |

Time-to-max: up median 17.0 min / down median 8.5 min. Ratio view: median MFE **0.380%** vs median \|MAE\| **0.322%**; MFE > \|MAE\| in **53.8%** of episodes; end-of-window mean +0.201% / median +0.090%.

Control (null: 26 random non-overlapping 30-min windows, 200 seeded draws, seed 13):

| stat | actual | control (mean ± sd) |
|---|---|---|
| median MFE % | 0.380 | 0.394 ± 0.093 |
| median \|MAE\| % | 0.322 | 0.330 ± 0.074 |
| % MFE > \|MAE\| | 53.8 | 54.0 ± 9.2 |
| mean end % | +0.201 | +0.126 ± 0.115 |
| median end % | +0.090 | +0.043 ± 0.108 |

Regime split (cut 2026-06-30 00:00 UTC):

| regime | n | med MFE | med \|MAE\| | % MFE>\|MAE\| | med end |
|---|---|---|---|---|---|
| pre (chop) | 9 | 0.394 | 0.911 | 44.4 | -0.113 |
| post (bull) | 17 | 0.336 | 0.257 | 58.8 | +0.109 |

## Honest flags
- The merged span is 5.26 days; the pre-Jun-30 regime is only ~1.5 days of it.
- 1m is spent for MINING; this study evaluates one pre-registered corrected rule. The sweep table exists for instrumentation, and anything derived from browsing it needs pre-registration + forward judgment.
- Per-bar fwd30 columns in the sweep OVERLAP — never use them as independent samples; the episode CSV is the non-overlapping view.

## HARD STOP
No variants; judged once on this tape; forward snapshots are the judge.
