# S1 — Scalp Geometry Study

_Generated 2026-07-02 · snapshot 2026-07-02 (4 days, 9,984 episodes idx 16+, both regimes spent/known) ·
no feature screening in this phase · **HARD STOP after this report** — architect + Yassine pick 1–2
geometries before any re-labeling (S2)._

## 0. Parity gate — PASS (exact)
The path-walker at the ORIGINAL geometry (TP 0.5% / SL 0.3%, 6h) reproduces the frozen dataset labels
**bucket-for-bucket**: long TP/SL/UN = 3927/6046/11, short = 3652/6307/25 — identical to `dataset.parquet`.
Every grid cell below uses the same conventions (high/low touch, same-bucket span → SL, horizon from entry
close, touch counts if its bucket starts within the horizon).

## 1. MFE/MAE — how far price actually travels (percentiles, % of entry)

| horizon | MFE p25 | p50 | p75 | p90 | MAE p50 | p75 |
|---|---|---|---|---|---|---|
| 5 min | 0.07 | 0.17 | 0.33 | 0.58 | 0.16 | 0.29 |
| 15 min | 0.13 | 0.30 | 0.60 | 1.01 | 0.26 | 0.45 |
| 30 min | 0.19 | 0.45 | 0.94 | 1.59 | 0.35 | 0.60 |
| 60 min | 0.30 | 0.68 | 1.41 | 2.13 | 0.47 | 0.86 |

(long side shown; short is statistically mirror-symmetric — full table `scalp_mfe_mae.csv`.)
Read: the **median** favorable run is ~0.30% by 15 min and ~0.45% by 30 min — so TP targets of
0.25–0.30% at 15–30 min horizons align with what the tape typically delivers; 0.10–0.15% TPs are
reached almost always (resolution ≥95%) but their spans are too small to pay for (below).

## 2. The payability map — the decisive result
Required lift is **analytic**: `breakeven − null = fee / (TP+SL)`. It does not depend on the data at all —
only on the geometry and your fee tier. Consequences:

| fee scenario (round trip) | required lift range over the grid | payable cells (≤10 pp) |
|---|---|---|
| taker/taker 0.09% | 18–60 pp | **0 / 75** |
| maker/taker 0.065% | 13–43 pp | **0 / 75** |
| maker/maker 0.04% | 8–27 pp | **18 / 75** |

- **Taker fills kill scalping outright.** Even the widest grid cell (0.30/0.20) needs an 18 pp edge — double
  the best lift any feature has ever shown here (~5–10 pp).
- **True micro-scalp cells (span ≤ 0.20%) are unpayable even maker/maker** (16–27 pp required).
- The payable zone (maker/maker) starts at **span ≥ 0.40%**: TP ≥ 0.20% with SL ≥ 0.15–0.20%, h ≥ 15 min —
  "fast swing" geometry, not micro-scalp.

## 3. Shortlist (payable + sane whipsaw, 15/30 min horizons)

| cell (TP/SL @ h) | null | TP-first L/S (of resolved) | whipsaw | unresolved | required lift (mm) |
|---|---|---|---|---|---|
| **0.25/0.15 @ 15m** | 37.5 | 37.9 / 37.8 | 20.2% | 4.3% | 10.0 pp |
| **0.25/0.20 @ 15m** | 44.4 | 44.7 / 44.0 | 8.0% | 7.2% | 8.9 pp |
| **0.30/0.15 @ 30m** | 33.3 | 33.9 / 33.8 | 30.6% | 1.4% | 8.9 pp |
| 0.30/0.20 @ 30m | 40.0 | 40.5 / 39.5 | 17.8% | 2.5% | 8.0 pp |
| (0.20/0.20 @ any) | 50.0 | 50.0 / 50.0 | 0% | — | 10.0 pp — degenerate: symmetric barriers = pure first-passage coin-flip; whipsaw impossible by construction |

Note the empirical TP-first rates sit **on the null** everywhere (±0.5 pp) — expected: this phase measures
the fee/null landscape, not edge. High-TP/low-SL cells buy a low null at the cost of huge whipsaw
(0.30/0.10 → 44–47% whipsaw): tight stops convert directional noise into double-stops.

## 4. Ambiguity — non-issue (surprising, verified)
Same-bucket both-barrier spans: **max 0.02% of episodes across all 75 cells; zero cells above the 15%
tape-resolution threshold.** The 1m volume buckets are ~34 s with tiny ranges, so even 0.15% spans almost
never fit inside one bucket. **No tape resolution needed for any grid geometry.**

## 5. Add-on — time-of-day tradeability map (current 0.5/0.3/6h label)
`timeofday_map.csv` (TP% of resolved + whipsaw% by UTC hour × direction, n per cell). Spot highlights:
long-friendly hours 02/13/21 UTC (TP 54–56%), short-friendly 00/04/12/22–23 UTC (TP 51–56%), whipsaw peaks
09/15 UTC (30–34%), calmest 22/02 UTC (12–16%).
**CAVEAT (printed in the CSV too): 4 days ≈ 4 samples per hour-cell — indicative only; rebuild as snapshots
accumulate. The clock stays BANNED as a model feature; this is a descriptive trading-hours map.**

## 6. Honest scope
Rates come from the same spent 4-day snapshot (bear→bull); they are **geometry-selection material, not edge
evidence**. The analytic payability results (fees vs null) are data-independent and final. Anything S2
produces on a chosen geometry is judged by forward snapshots only.

**Recommendation for the S2 pick:** `0.25/0.15 @ 15m` (classic 1.67R, null 37.5 — comparable to the original
study's shape at scalp speed) and `0.25/0.20 @ 15m` (lowest whipsaw among payable cells, 8.9 pp lift). Both
assume maker entries; if Yassine's real tier is taker-side, S2 re-labeling is moot until fills change.

**HARD STOP.**
