# S5b-r — Corrected Confluence Study + M10 Sweep (1m, merged span)

_**Pre-registered CORRECTION of S5's leg 1 (mistranslation from the operator's screenshot, bucket idx 20977), RE-JUDGED once more (S5b-r) after an operator-mandated leg-2 fix: the rule reads the LOCKED eff-agg badge SPREAD |bull-bear| >= 65 points (dominant share >= 82.5%) exactly as the terminal's confluence alert does — the first S5b run mistranslated it as share >= 65%. Multiplicity: +2 (S5b) +2 (this re-judgment) -> program counter 456.** Legs 3-4 unchanged (START-DURING dominant / +15pp locked spread, frozen). The sweep table is INSTRUMENTATION — any rule found by browsing it must be pre-registered and judged on forward tape before it counts. Excursions are an information measure — no fees, no execution model. P8 is EXCLUDED from the sweep (size_thr never persisted)._

## Data
Merged 12575 bars (frozen 9686..19685 + fresh pull 12261..22260, dedup by bucket_id removed 7425 overlap rows), continuous bids 9686..22260, span 06-28 11:04 -> 07-03 17:25 UTC (5.26 days), gaps: none. Evaluable rows 12559 (first 16 excluded for lookback).

## Step 0 — anchor parity (gate PASSED)
Anchor = bucket idx 20977, end 2026-07-03 03:20:49 UTC (= 04:20:49 operator local, UTC+1); identity verified by idx + exact OHLC match (O 80.79 H 80.80 L 80.77 C 80.79).

| quantity | computed | screenshot |
|---|---|---|
| eff-agg LOCKED badge spread | +78.5 (share 89.3%) | 81% (the badge IS the spread) |
| P1 abs spread / P3 E/R spread | 76.1 / 33.7 | 26% (2nd badge) |
| P0 smoothed sum @lock | +52.2 | +55.6 |
| phase UP row | 11 / 51 / 38 | 13 / 52 / 35 |
| confirmed markers | up@50.0, up@0.0 | -50/0/+50 all green |
| fire_long | True | TRUE |

**Leg 1' semantics (the correction):** Panel-0's confirmed cross MARKERS — one most-recent confirmed cross per level {+50, 0, -50}, ZERO LINE INCLUDED, detected inside the locked slice of the 16-bar selection exactly as `_draw_level_crosses` draws its X's (settling/forming dots excluded; confirmed = new side holds >= 2 buckets within the locked slice). >= 2 markers required; the two most recent both up -> LONG, both down -> SHORT. S5's leg used S3's A2 instead (±50 only, 0-line excluded, both levels required) — that mistranslation made the leg nearly unfireable.

## Leg attrition (all 12559 evaluable bars)

| side | leg | standalone | cumulative (1..k) |
|---|---|---|---|
| long | 1' two most-recent markers on-side | 308 (2.45%) | 308 (2.452%) |
| long | 2 eff-agg LOCKED spread >= 65 (share >= 82.5%) | 1232 (9.81%) | 126 (1.003%) |
| long | 3 phase dominant START/DURING | 8202 (65.31%) | 118 (0.940%) |
| long | 4 P6 locked spread >= 15pp | 5036 (40.10%) | 107 (0.852%) |
| short | 1' two most-recent markers on-side | 314 (2.50%) | 314 (2.500%) |
| short | 2 eff-agg LOCKED spread >= 65 (share >= 82.5%) | 1000 (7.96%) | 100 (0.796%) |
| short | 3 phase dominant START/DURING | 6330 (50.40%) | 59 (0.470%) |
| short | 4 P6 locked spread >= 15pp | 3019 (24.04%) | 29 (0.231%) |

## Episodes (non-overlapping 30-min windows; 88 skipped inside open windows, 0 end-of-data excluded)

### LONG — 35 episodes (6.65 fires/day)

| metric | mean | median | p25 | p75 | p90 | max |
|---|---|---|---|---|---|---|
| MFE % | 0.562 | 0.418 | 0.170 | 0.668 | 1.554 | 2.119 |
| \|MAE\| % | 0.463 | 0.370 | 0.160 | 0.637 | 1.101 | 1.474 |

Time-to-max: up median 15.3 min / down median 10.2 min. Ratio view: median MFE **0.418%** vs median \|MAE\| **0.370%**; MFE > \|MAE\| in **54.3%** of episodes; end-of-window mean +0.082% / median +0.000%.

Control (null: 35 random non-overlapping 30-min windows, 200 seeded draws, seed 13):

| stat | actual | control (mean ± sd) |
|---|---|---|
| median MFE % | 0.418 | 0.383 ± 0.083 |
| median \|MAE\| % | 0.370 | 0.327 ± 0.069 |
| % MFE > \|MAE\| | 54.3 | 53.9 ± 8.8 |
| mean end % | +0.082 | +0.116 ± 0.110 |
| median end % | +0.000 | +0.040 ± 0.107 |

Regime split (cut 2026-06-30 00:00 UTC):

| regime | n | med MFE | med \|MAE\| | % MFE>\|MAE\| | med end |
|---|---|---|---|---|---|
| pre (chop) | 6 | 1.514 | 0.179 | 83.3 | +1.169 |
| post (bull) | 29 | 0.366 | 0.396 | 48.3 | -0.054 |

### SHORT — 13 episodes (2.47 fires/day)

**UNDERPOWERED (n = 13 < 20): counts only, per protocol. Episodes in the CSV; verdict deferred to forward tape.**

## Honest flags
- The merged span is 5.26 days; the pre-Jun-30 regime is only ~1.5 days of it.
- 1m is spent for MINING; this study evaluates one pre-registered corrected rule. The sweep table exists for instrumentation, and anything derived from browsing it needs pre-registration + forward judgment.
- Per-bar fwd30 columns in the sweep OVERLAP — never use them as independent samples; the episode CSV is the non-overlapping view.

## HARD STOP
No variants; judged once on this tape; forward snapshots are the judge.
