# MMXSKEW v1.3-Dynamic — NEGATIVE result (do NOT register)

**Date:** 2026-07-20 · **Verdict: the dynamic volatility ratio does NOT improve v1.3 — keep v1.3 on static `mov_mag ≥ 39`.**

## Test
Replace the static `mov_mag ≥ 39` in MMXSKEW v1.3 with the volatility-normalized ratio
`mov_mag_ratio = mov_mag / trailing-EMA50(mov_mag) ≥ T`.

**Base = v1.3 architecture:** v1.1 rules **minus the POC filter**, + eff-agg spread ≥ +35 (long) / ≤ −35 (short),
+ long `delta < +15%`, + **asymmetric Δ-accel** (raw da2 > 0 for both sides: longs = buying accelerates, shorts =
selling decelerates/absorbs). Fine sweep **T ∈ [0.80, 1.50] step 0.05** on the **114 sub-bucket-covered** signal set.

## Sweep results
| rule | n (L/S) | win 1.0/1.5 | exp 1.0/1.5 | PF 1.0/1.5 | SplitHalf |
|---|---|---|---|---|---|
| **static mov_mag≥39 (v1.3)** | **26 (6L/20S)** | **73% / 70%** | **+0.36% / +0.67%** | **2.2/3.2** | **PASS/PASS** |
| ratio≥0.80 | 40 (11L/29S) | 60% / 59% | +0.13% / +0.38% | 1.4/2.1 | FAIL/PASS |
| ratio≥0.85 | 38 (10L/28S) | 58% / 53% | +0.11% / +0.27% | 1.3/1.7 | FAIL/FAIL |
| ratio≥0.90 | 38 (10L/28S) | 58% / 53% | +0.11% / +0.27% | 1.3/1.7 | FAIL/FAIL |
| ratio≥0.95 | 37 (10L/27S) | 57% / 52% | +0.11% / +0.27% | 1.3/1.6 | FAIL/FAIL |
| ratio≥1.00 | 36 (9L/27S) | 56% / 50% | +0.10% / +0.26% | 1.2/1.6 | FAIL/FAIL |
| ratio≥1.05 | 34 (9L/25S) | 56% / 50% | +0.09% / +0.25% | 1.2/1.6 | FAIL/FAIL |
| ratio≥1.10 | 30 (7L/23S) | 60% / 54% | +0.15% / +0.33% | 1.4/1.8 | FAIL/FAIL |
| ratio≥1.15 | 30 (7L/23S) | 60% / 54% | +0.15% / +0.33% | 1.4/1.8 | FAIL/FAIL |
| ratio≥1.20 | 27 (6L/21S) | 59% / 52% | +0.14% / +0.30% | 1.4/1.7 | FAIL/FAIL |
| ratio≥1.25 | 26 (6L/20S) | 62% / 55% | +0.18% / +0.35% | 1.5/1.8 | FAIL/FAIL |
| ratio≥1.30 | 26 (6L/20S) | 62% / 55% | +0.18% / +0.35% | 1.5/1.8 | FAIL/FAIL |
| ratio≥1.35 | 22 (5L/17S) | 73% / 63% | +0.35% / +0.52% | 2.2/2.4 | PASS/PASS |
| ratio≥1.40 | 18 (3L/15S) | 67% / 65% | +0.28% / +0.59% | 1.8/2.6 | PASS/PASS |
| ratio≥1.45 | 18 (3L/15S) | 67% / 65% | +0.28% / +0.59% | 1.8/2.6 | PASS/PASS |
| ratio≥1.50 | 17 (3L/14S) | 65% / 62% | +0.26% / +0.56% | 1.7/2.5 | PASS/FAIL |

## Conclusion
- **No dynamic T beats the static baseline.** The middle band (T ≈ 0.85–1.30) **FAILS split-half** (thin, ~56–62%);
  the quality region (T ≈ 1.35–1.45) **PASSES** but is *worse* than static (fewer trades, 1:1.5 win 63–65% vs 70%).
- **Why:** the dynamic ratio is a *substitute* selectivity filter. It helps a **loose** base threshold
  (v1.2's `mov_mag ≥ 25`, where it improved 64→69/73%) but **not a tight one already da2-gated** (v1.3's
  `mov_mag ≥ 39`). Two selectivity mechanisms don't compound — the ratio fights the da2 filter and dilutes at low T.
- **Contrast:** the *same* ratio swap **improved** v1.2 (→ v1.2-Dynamic, registered) but **did nothing** for v1.3.
  The vol-ratio is **v1.2-specific**.

## Decision
**Keep v1.3 on static `mov_mag ≥ 39`. Do not register v1.3-Dynamic. Do not re-test.**

Reproduce: v1.3 base = `study/mm_skew_v13_validate.py`; ratio calc = `study/mm_skew_v12d_validate.py` /
`study/mm_skew_sprint.py`. Gate swept = `da2 > 0 (asym) AND mov_mag_ratio ≥ T`.
