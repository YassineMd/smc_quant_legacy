# NEGATIVE RESULTS — sub-candle / tape / τ session, 2026-07-21

**Do NOT re-test anything below without NEW data or a materially different design.** Seven hypotheses, ~110
in-sample cells. One survivor was registered (`DA2-REVERSION v1.0`, see `study/da2_reversion_validate.py`);
everything else is filed here with the reason it died.

## THE HEADLINE META-RESULT — read this before proposing the next variant

| | |
|---|---|
| in-sample cells evaluated | **~110** |
| cells reaching p<0.05 | **6** |
| of those, surviving split-half | **1** |
| expected false positives at 110 cells | **~5.5** |

**The false-positive count matched the null expectation almost exactly.** Every p≈0.02–0.04 found in this
session was consistent with chance, and five of six behaved exactly like chance when split in half. A 111th cell
is not more trustworthy than those were — it is *less*, because the multiplicity tally keeps growing.

### The "isolated spike" signature — how each false positive looked
Three separate times a cell hit p<0.05 and showed the identical shape. Learn it:
1. **Neighbours bad on both sides** (a plateau would have good neighbours).
2. **Split-half reverses** (or the two RRs reverse in opposite directions).
3. **Drop-best-1 to -3 erases it.**

Instances: the T=1.25 "flat plateau" (actually two already-overlap-skipped signals — a mechanical no-op, see
`study/mm_skew_v12d_validate.py` THRESHOLD PROVENANCE); tape-ALIGNED at hold=3 (`+0.021` between `−0.090` and
`−0.201`); stair-step with a candle-1 stop at RR 1:1.0 (`+0.078` gross between `−0.032` and `+0.054`).

### The wall every one of these hit
Round-trip fee is a **flat 0.08%**. Every rule here resolves inside one or two buckets, where the *gross* edge
is 0.01–0.08%. Filtering cannot fix that — it only shrinks n while the fee stays fixed. Two independent routes
out were tested and both failed (longer holds §5; tighter targets §7).

---

## 1. DELTA-ACCEL v1.0 — 30-minute wall-clock halves
**Script:** `study/delta_accel_explore.py` · **Verdict: GENUINE NULL (construction verified to machine precision).**

Hypothesis A (momentum: `|d1|<|d2|<|d3|` same-sign + efficiency) and B (absorption: `|Δaccel|` top-quartile +
low efficiency, faded). All six cells negative; best p=0.294. Baseline (every eligible sequence) also negative
at −0.065 to −0.153%/trade, i.e. the whole universe is fee-negative.

**Do not re-run because:** the "1h" tape is CONSTANT-VOLUME buckets (8s–95min, median ~33min), so it has no
30-minute halves. The study built true wall-clock 30m bars from the 1m stream, which caps coverage at 22 days
→ n=17 (A) and n=13 (B). Verified: OHLC exact vs an independent rebuild, 1m tiles with 0 gap/overlap, volume
reconciles to 1e-15, and 1h/5m/15m never print a close outside the containing 30m bar.

**Caveat worth knowing:** the trigger set is fragile to the bar-boundary convention — start/end/midpoint
assignment gives A = 17/14/18 with only 10 shared; a 5m-sourced rebuild gives Jaccard 0.56. Two variant cells
flip positive. At this n the design cannot resolve the sign in either direction.

## 2. KINETIC-DELTA — 50%-volume stair-step
**Script:** `study/kinetic_delta_explore.py` · **Verdict: NULL; the volume variant is STRUCTURALLY IMPOSSIBLE.**

Bull `Δ(C1.h1)<Δ(C1.h2)<Δ(C2.h1)` and bear mirror, on halves cut at each bucket's 50%-volume mark. n_taken
118–193 (a healthy sample). Every cell negative, lowest p=0.174.

**The volume-escalation variant fired 0 of 921 times and always will.** The cut is the FIRST sub-bucket crossing
50%, so h1 always overshoots: h1's volume share is min 0.5000 / median 0.5085 / max 0.5210 — never below 0.5.
`V(C1.h1) < V(C1.h2)` is unsatisfiable on equal-volume halves by construction. Testing it needs a different
split rule (equal TIME), not a different threshold.

**Real finding kept:** monotone 3-chains occur on **40.2%** of sequences vs 16.7% expected by chance, so delta
genuinely trends across halves. The pattern is real; it just carries no forward information.

**Best gross edge anywhere in it: +0.0099%** against a 0.08% fee — ~8× too small. All nine mirrors also lose.

## 3. Step MAGNITUDE as a winner/loser separator
**Verdict: NULL, and the direction is OPPOSITE to the hypothesis.**

Tested whether the *size* of each stair-step (not just the ordering) separates winners from losers, on
delta-normalised-by-half-volume. 18 tests (6 features × 3 cohorts). **Zero reached p<0.05.**

On the BULL side, winners had **smaller** steps than losers on 4 of 6 features (step1 +0.1441 vs +0.1484,
step2 +0.1145 vs +0.1242, weakest +0.0639 vs +0.0697). Quartile win rates are non-monotone and inverted-U:
step1 gives 34% → 71% → 69% → **34%** — the biggest steps do as badly as the smallest.

**Arithmetic reason it could never have worked:** cohort gross was +0.078% against a 0.08% fee. To make a subset
profitable, the strong half would need ~double the cohort's gross while the weak half went negative — a ~15pp
quartile spread. Largest observed: 8pp, in the wrong shape.

## 4. Buyer/seller TAPE direction + EMA veto
**Verdict: best-of-day components, still not significant. Filters all made it WORSE.**

Tape = taker prints/sec per side (`sum(sz_cb)/duration`), the terminal's Tape-B/S readout. Genuinely independent
of delta (65.4% agreement, corr +0.23), so it is real new information — not a delta restatement.

| rule | n | correct% | binom p |
|---|---|---|---|
| **ALIGNED + EMA20 veto** | 502 | **53.6%** | 0.108 |
| ALIGNED, no veto | 565 | 52.4% | 0.256 |
| CONTINUATION (tape ignored) | 846 | 51.4% | 0.409 |
| OPPOSED (absorption) | 281 | 49.5% | 0.858 |

Three things pointed the right way: ALIGNED beat CONTINUATION (tape adds something), OPPOSED is dead (follow
the tape, don't fade it), and the veto helped **every** row it touched. The veto's own mechanism checks out —
buckets with BOTH tapes >1.5× their EMA20 continue only **46.5%** of the time (n=86), i.e. they do lean reversal
as hypothesised.

**But every filter degraded it**, including eff-agg — the *essential* filter in MMXSKEW:

| filter | n | correct% | vs unfiltered |
|---|---|---|---|
| none | 502 | 53.6% | — |
| eff-agg \|spr\|≥35 | 135 | 51.1% | −2.5 |
| da2>0 (v1.3 rule) | 251 | 52.6% | −1.0 |
| eff-agg + delta | 121 | **47.9%** | −5.7 |

**Structural lesson: tape is a SUBSTITUTE for eff-agg, not a complement.** Both read aggressor dominance, so
stacking them demands the same evidence twice and just cuts n. Do not gate a tape rule on aggressor-share.

**Why it stops here:** needs 54.4% at n=502, has 53.6%. The gap closes with sample, not cleverness — and the
tape fields exist on only 847 of 3880 buckets (22%) because they postdate the archive. No reconstruction path
exists (unlike da2), so this is purely elapsed time. ~10 weeks to n≈2000.

## 5. Longer holds — REFUTED for both stair-step and tape
**Verdict: the premise fails. Gross does not grow with hold time.**

The idea was that a flat 0.08% fee amortises over a longer hold. Measured gross by hold length (tape-ALIGNED):

```
hold:    1      2      3      5      8     13     21     34   buckets
gross: -0.018 -0.009 +0.058 -0.165 -0.063 -0.089 -0.618 -0.265
```

No accumulation. At 21 buckets (~11h) it is **−0.62%/trade** vs **−0.08%** for a side-matched random entry —
holding longer only gives the position more time to be wrong. Drift control was clean: random entries earn
≈−0.08% (exactly the fee) at *every* horizon, so no trend confound inflated the long holds.

The hold=3 cell (+0.021, p=0.026) is a textbook isolated spike — see the signature above.

## 6. 35-feature winner/loser scan — with NULL CALIBRATION
**Verdict: NULL, and this is the most decisive result in the file.**

Scanned every bucket stat (35 features) on the tape cohort, winners vs losers. **1 feature hit p<0.05**
(`f_dur`, p=0.0382). Then the same 35-feature scan was re-run 300× on **shuffled win/loss labels**:

| | |
|---|---|
| hits at p<0.05 on **pure noise** | mean **1.7**, median 1, p90 5, max 12 |
| P(noise produces ≥1 hit) | **60.7%** |
| P(noise produces a p ≤ 0.0382) | **50.3%** |

**The scan produced FEWER discoveries than random labels do.** Split-half (find on H1, confirm on H2):
`f_dur` went 0.0232 → **0.4738**. **0 of 35 features confirmed.**

**This is the standing answer to "scan everything, we'll definitely find something."** Yes — and noise finds
more. Any future broad scan must ship this null calibration alongside it or it means nothing.

## 7. τ-ratio YELLOW trend-following
**Verdict: properly tested at last; sits ~1.4pp under break-even; UNRESOLVABLE in-sample.**

τ = bucket duration ÷ trailing EMA-15 of durations; YELLOW at τ<0.3 (`app/terminal.py:6097-6107`). The terminal
already filed it as a display-only caution flag (n=35, p≈0.35).

**⚠ FIRST RUN WAS CONTAMINATED — the lesson from this session.** Built on the raw archive without the maturity
cut, **465 of 602 fires (77%) came from an 11.6-hour backfill burst** at `target_vol=5000` (median duration 7s),
and 319 had τ degenerate (duration pinned to the 1s floor). **ALWAYS apply `FM.build()`'s `first` (=2618).**

Clean (mature, doji excluded): YELLOW n=78 win **55.1%** vs **56.5%** break-even, net −0.031%, t=−0.36;
at RR1.5 n=68, 44.1%. Yellow *does* beat the baseline at RR1.5 (gross +0.025 vs −0.060).

**Why it cannot be settled:** 1 SE on a win rate at n=78 is ±5.7pp; the 1.4pp deficit is 0.25 SE. Resolving it
at 2 SE needs ~5,100 trades ≈ **62 months** at the observed fire rate. Not a waiting problem — unfalsifiable at
this effect size. The only lever that moves it is **execution cost**: halve the fee and break-even drops to
~53.3%, below the observed 55.1%.

**Also documented (τ definition warts):** the EMA includes the current bucket (α weight 0.125), so the effective
trailing threshold is **0.273, not 0.3** — the code comment's "3× faster" is wrong. And the two terminal sites
(`:2011` skips `wd<=0`, `:6104` floors at `max(1.0,·)`) give different τ for the same bucket, agreeing on only
0.3% of buckets overall (they converge on mature data, 136/137).

## 8. Mean-reversion: tape ≥3.9 + da2 opposed
**Verdict: the TAPE half added nothing; da2 alone was the active ingredient → became DA2-REVERSION v1.0.**

Full rule (either tape side ≥3.9 + da2 opposed, fade the candle, 0.5% fixed stop): n=34, win 58.8% vs 58.0%
break-even, net **+0.008%** — break-even to four decimals, t=+0.10.

Ablation showed why: **tape alone is clearly negative** (−0.080, −0.033) and the "TOTAL ≥3.9" reading is worse
than per-side. Dropping the tape condition entirely leaves da2-opposed, which carried the whole effect.

**Also disproved here:** the mirror control is NOT independent evidence. With a fixed stop and 1:1 RR it is
arithmetically forced — if the rule wins 58.8%, the mirror wins 41.2% and nets −0.168%. It adds zero
information. Do not cite a mirror as confirmation under a fixed bracket.

**The 0.5% stop was the binding constraint**, not either condition: it puts the fee at 16% of risk and demands
58% accuracy. Widening the stop lowers the bar AND raises the win rate simultaneously — which is how
DA2-REVERSION landed on 0.8%.

---

## Methodology carried forward
1. **Apply `FM.build()`'s `first` (=2618)** to anything using the 1h archive. 68% of rows are a degenerate
   half-day backfill burst at `target_vol=5000`.
2. **Quote stats from `taken()` only** (non-overlap ON). See `[[canonical-taken-basis]]` / `MMXSKEW_NOPOC.md`.
3. **Split-half is the gate**, not the p-value. Five of six p<0.05 cells died here.
4. **Report the cell count** with every p-value. A p=0.02 at 110 cells is noise.
5. **Broad scans require null calibration** (shuffle the labels, re-scan, count).
6. **A mirror is not a control** under a fixed bracket; a side-matched random selection is.
7. **Check gross vs fee first.** If gross < 0.08%, no filter can save it — stop and say so.
