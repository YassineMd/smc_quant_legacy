# MMXSKEW v1.2 — CANDIDATE GATE SPECIFICATIONS (forward-test only)

**Status: CANDIDATE, registered 2026-07-20. NOT frozen, NOT live.** An overlay on the frozen v1.1 signal
(`study/MMXSKEW.md`) — v1.1 entry/exit is UNCHANGED. Everything below is **in-sample and partly mined** from ~44
features on 149 signals in ONE regime (a SOL uptrend); magnitudes **will regress**. The *direction and the
split-half consistency* are the finding, not the numbers. Graduate/kill only on forward tape.

## Primary gate (v1.2)
Take a v1.1 MM×Skew signal ONLY if BOTH hold:
- **Sequence gate — `run_pos <= 4`**: skip the 5th+ consecutive same-side signal (late-run fatigue).
  `run_pos` = count of consecutive same-side v1.1 signals up to & including this one (over ALL raw signals).
- **Expansion gate — `mov_mag >= 39`**: skip low-displacement churn buckets. `mov_mag` = the signal-bar squared
  %-move ×100 = `((close·100/ref − 100)^2)·100`, ref = LOW (bull) / HIGH (bear) / OPEN (doji).

## In-sample metrics (`study/mm_skew_gate_v12.py`, one-at-a-time, net @0.08% fee)
| RR | cohort | n | win% (L/S) | cumNet% | totR | maxDD | PF | exp/trade |
|----|--------|---|-----------|---------|------|-------|----|-----------|
| 1:1.0 | baseline | 90 | 53.3 (67/46) | +2.8% | −6.3 | 5.0% | 1.10 | +0.034% |
| 1:1.0 | **v1.2 gate** | 14 | **78.6** (83/75) | +9.6% | +7.0 | 1.8% | 3.90 | +0.660% |
| 1:1.5 | baseline | 80 | 42.5 (55/35) | +7.5% | −6.1 | 6.2% | 1.24 | +0.096% |
| 1:1.5 | **v1.2 gate** | 13 | **84.6** (83/86) | +18.6% | +13.5 | 1.8% | 10.3 | +1.324% |

Retention ≈ **16%** (~1 trade / 2 days). Split-half OOS lift (win% gated − baseline, within each time-half):
1:1.0 → H1 +22pp / H2 +30pp; 1:1.5 → H1 +35pp / H2 +55pp — same sign in every cell. Fixes the short side.

## The 4 verified edges behind the gate (2026-07-20, adversarial split-half + confound-controlled)
Mined from `study/mm_skew_feature_matrix.py` (44 causal features, 149 signals), verified by a multi-agent
adversarial pass (split-half sign-consistency + within-side confound control):
1. **`run_pos <= 4`** — win-gap +0.265/+0.248, **z≈3.3, p<0.01**. Survives WITHIN shorts (not just an is_long
   artifact). Threshold, not monotone (rp≥9 recovers). *Two-sided, significant.*
2. **`mov_mag >= ~39`** — win-gap +0.154/+0.222, **p=0.009 at 1:1.5**. Both sides; beats raw range_pct; not
   is_long/run_pos. *Two-sided, significant.*
3. **skip chase-outside-prior-day-value** (`chase_outside_prevVA==0`) — win-gap +0.174/+0.101, p=0.027 at 1:1.0.
   **Short-side, RR1.0 only** (RR1.5 directional but weak). Location filter.
4. **short & Panel-3 E/R high** (`side<0 & p3_er_dir > ~short-median`) — win-gap +0.29/+0.22 on shorts,
   p=0.006/0.04, monotone terciles. **Short-only** side×feature interaction. *(Panel 3 = Effort/Result; on ALL
   signals it's null, but WITHIN shorts the directionalised value separates.)*

## Secondary watch-flags (confirmors, NOT primary gates yet — underpowered)
- **`delta_accel_2`** (sub-bucket H2-vs-H1 delta acceleration, `study/mm_skew_subbucket.py`). Reconstructed from
  the 1m archive (no daemon change; reconstruction corr **0.997** vs stored delta). Directionalised >0 =
  aggression accelerating WITH the trade into the close. Split-half ROBUST at both RR; top tercile 71%/58% win;
  underpowered (p≈0.37, n=114). Coarse halves-split works, the finer tercile-slope FAILS split-half.
- **τ-ratio** (bucket duration ÷ EMA-15(duration)): climactic buckets `τ<0.3` (filled >3× faster) win ~9pp less;
  split-half robust both RR, underpowered (p≈0.35, n=35). Tail effect, not a gradient. Climactic-blow-off flag.
- **Panel-3 E/R & prior-day-value location** — short-side context overlays (edges #3, #4 above).

## VWAP (recorded 2026-07-20) — location-reference finding
Daily-anchored VWAP (`(H+L+C)/3` cum within the UTC day, causal) tested as a swap for the POC baseline
(`study/mm_skew_vwap.py`): **marginally better than POC ONLY on the short side at wide RR** (1:1.0, 1:1.5),
split-half consistent; 20/50-EMA worse; 9-EMA better only at tight RR (regime-fragile). For MMXSKEW-ORB,
POC≡VWAP (breakout gate dominates). Adaptive-TP-by-VWAP-distance and value-area (VAH/VAL) stops both FAILED
OOS / were worse. **VWAP is a useful short-side location reference, not a strategy change.** The strongest
prior-value result is *don't SHORT below yesterday's 70% value area* (chase filter, edge #3).

## Caveats (the dominant one is SAMPLE SIZE)
- Combined gate **n=13–14 trades**, split-half cells n=5–8. 85% win / PF 10 **will not repeat.**
- Both thresholds **mined in-sample**, one regime, short-heavy dataset. Trust direction + consistency, not magnitude.
- Forward validation is slow (16% retention ≈ n≥20 takes ~5–6 weeks of tape).

## Reproduce
```
python study/mm_skew_gate_v12.py        # the combined gate + split-half OOS
python study/mm_skew_feature_matrix.py  # the 44-feature causal ranking (-> CSV)
python study/mm_skew_subbucket.py       # delta_accel_2 from the 1m archive
python study/mm_skew_vwap.py            # VWAP vs POC location comparison
```
