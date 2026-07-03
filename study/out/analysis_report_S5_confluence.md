# S5 — Confluence Excursion Study (Yassine's 4-leg pivot core, 1m)

_**External pre-registered hypothesis** from the live strategy; thresholds FROZEN (65% eff-agg share, +15pp phase spread), no tuning, no variants. **Characterization framing:** the 1m dataset is SPENT for mining, but this exact confluence has never been evaluated. Multiplicity: **+2 cells (long, short) -> running counter 452**. Excursions are an information measure — no fees, no execution model._

**Verbatim reuse:** `p9_global` / `confirmed_crosses` / `phase_traj` imported from `setups_S3` (built on `app.region_state`); S3's closure legs A2 (P0 crosses) and A3 (P2 share) carried line-for-line. Selection [b-15, b] = 16 bars (`S3.W_SEL = 16`); terminal lock 7 and all panel constants untouched. Leg semantics: P0 = most-recent confirmed cross per ±50 level, both levels present in the pre-lock selection, both on the fire side; confirmed = new side holds >= 2 buckets; 0-line crosses excluded.

## Data & exclusions
10000 1m bars, 06-28 11:04 -> 07-02 11:17 UTC (4.01 days). Evaluable bars 9985 (first 15 excluded for lookback); fires skipped inside an open 30-min window: 0; end-of-data windows excluded: 0. Confirmed ±50/0 crosses on the P0 sum line over the full series: 1007.

## Leg attrition (trigger selectivity, all evaluable bars)

| side | leg | standalone pass | cumulative (1..k) |
|---|---|---|---|
| long | 1 P0 both ±50 crosses on-side | 0 (0.00%) | 0 (0.000%) |
| long | 2 P2 eff-agg share >= 65% | 2928 (29.32%) | 0 (0.000%) |
| long | 3 phase dominant START/DURING | 6477 (64.87%) | 0 (0.000%) |
| long | 4 P6 spread >= 15pp | 4019 (40.25%) | 0 (0.000%) |
| short | 1 P0 both ±50 crosses on-side | 1 (0.01%) | 1 (0.010%) |
| short | 2 P2 eff-agg share >= 65% | 2636 (26.40%) | 1 (0.010%) |
| short | 3 phase dominant START/DURING | 4983 (49.90%) | 1 (0.010%) |
| short | 4 P6 spread >= 15pp | 2422 (24.26%) | 1 (0.010%) |

**Why leg 1 gates everything (verified structurally, two independent code paths):** the P0 sum line is the smoothed lean (averaged with its 7-bar-lagged value), so crossing BOTH -50 and +50 inside one 8-bar pre-lock span demands a >=100-point sweep of a deliberately sluggish line. Confirmed crosses on this tape: +50 x282, 0 x456, -50 x269 — yet a +50 and a -50 cross fall within 8 bars of each other only 4 times (minimum observed gap 7 bars), and exactly 1 bar(s) hold both levels inside their pre-lock window at all (of which 1 same-side). At the 16-bar selection, leg 1 alone caps the confluence near one fire per 4-day tape; the 65/15 legs never get to filter.

## Episodes (non-overlapping, 30-min wall-clock windows)

### LONG — 0 episodes (0.00 fires/day)

**UNDERPOWERED (n = 0 < 20): no distribution / control / regime analysis for this side — the study stops here per protocol. Episodes are in the CSV; verdict deferred to forward data.**

### SHORT — 1 episodes (0.25 fires/day)

**UNDERPOWERED (n = 1 < 20): no distribution / control / regime analysis for this side — the study stops here per protocol. Episodes are in the CSV; verdict deferred to forward data.**

## Honest flags
- 4-leg AND at frozen 65/15 thresholds -> few fires by design; sides under n=20 are reported as UNDERPOWERED and not interpreted.
- The tape is 4.01 days of one market phase (plus the Jun-30 turn); fires/day is not a stable estimate at this n.
- 1m mining credibility is spent; whatever appears here is a hypothesis for forward snapshots, not a verdict.

## HARD STOP
No threshold variants were run. Judged once, characterization only.
