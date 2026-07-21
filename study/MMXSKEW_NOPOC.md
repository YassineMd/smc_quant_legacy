# MMXSKEW — POC baseline DROPPED (2026-07-21) + the no-POC candidate family

**Decision: the POC-baseline filter is removed from every CANDIDATE.** v1.1 stays frozen/live with POC (it is the
registered baseline — untouched). All candidates now share one base: **v1.1 minus POC**.

## Why — the ablation (every version, WITH vs WITHOUT POC)
Base signals: **WITH POC = 149 · WITHOUT POC = 174** (+17%; the extra signals are **predominantly shorts** —
v1.1 goes 33L/57S → 34L/69S).

| Version | POC | n (L/S) | win 1.0/1.5 | exp 1.0/1.5 | PF 1.0/1.5 | SplitHalf |
|---|---|---|---|---|---|---|
| v1.1 | WITH | 90 (33L/57S) | 53% / 42% | +0.03% / +0.10% | 1.1/1.2 | PASS/PASS |
| v1.1 | without | 103 (34L/69S) | 52% / 43% | +0.03% / +0.10% | 1.1/1.3 | PASS/PASS |
| v1.2 | WITH | 14 (6L/8S) | 79% / 85% | +0.66% / +1.32% | 3.9/10.3 | PASS/PASS |
| v1.2 | without | 25 (9L/16S) | 72% / 67% | +0.46% / +0.72% | 2.5/3.0 | PASS/PASS |
| v1.2-Relaxed | WITH | 25 (8L/17S) | 64% / 50% | +0.31% / +0.45% | 2.0/2.2 | PASS/PASS |
| v1.2-Relaxed | without | 35 (11L/24S) | 60% / 50% | +0.23% / +0.34% | 1.6/1.8 | PASS/PASS |
| v1.2-Dynamic | WITH | 16 (6L/10S) | 69% / 73% | +0.48% / +1.03% | 2.7/5.8 | PASS/PASS |
| v1.2-Dynamic | without | 23 (8L/15S) | 70% / 67% | +0.43% / +0.75% | 2.4/3.2 | PASS/PASS |
| v1.3 | WITH (added) | 20 (5L/15S) | 70% / 68% | +0.32% / +0.69% | 1.9/3.1 | PASS/PASS |
| v1.3 | without *(as registered)* | 26 (6L/20S) | 73% / 70% | +0.36% / +0.67% | 2.2/3.2 | PASS/PASS |

**Verdict — POC offers NO edge.** The decisive row is **v1.1** (most-powered, n=90 vs 103, no gates to muddy it):
**53% vs 52% win, identical expectancy, identical PF.** Gated differences are small and **directionally
inconsistent** (WITH slightly better on v1.2/v1.2-Relaxed; WITHOUT better on v1.3; a tie on v1.2-Dynamic @1:1.0)
— i.e. noise. **Every variant PASSES split-half both ways**, so POC isn't what separates robust from fragile.
POC is a **sample-size lever, not an edge lever**: it costs 17–79% of trades for no measurable gain. Since sample
size is the binding constraint on every candidate, it is dropped.

## The no-POC family (all share base = v1.1 minus POC)
`LONG = bull + skew>0 + eff-agg spread >= +35 + delta < +15%` · `SHORT = bear + skew<0 + spread <= -35`
Exit unchanged everywhere: stop 0.1% beyond the bucket extreme, TP = RR x stop (1:1.0 / 1:1.5), win-or-lose.

| Version | Added gate | validate |
|---|---|---|
| **v1.1-NP** | *(none — the new research base, 174 signals)* | — |
| **v1.2-Relaxed** | `run_pos<=4` + `mov_mag>=25` | `study/mm_skew_v12r_validate.py` |
| **v1.2-Dynamic** | `run_pos<=4` + `mov_mag_ratio>=1.30` | `study/mm_skew_v12d_validate.py` |
| **v1.3** | `mov_mag>=39` + asymmetric Δ-accel (`raw da2>0`) | `study/mm_skew_v13_validate.py` |

## Re-validated no-POC numbers (in-sample)
| Candidate | n (L/S) | win 1.0/1.5 | exp 1.0/1.5 | split-half | MC P(profit) | MC edge-CI @1:1.5 |
|---|---|---|---|---|---|---|
| **v1.2-Relaxed** | 35/34 (11L/24S) | 60% / 50% | +0.23% / +0.34% | 59→61 / 41→59 (both net+) | 90.8% / 93.4% | [-0.084, +0.783] straddles 0 |
| **v1.2-Dynamic** | 23/21 (8L/15S) | 70% / 67% | +0.43% / +0.75% | 64→75 / 60→73 | 97.3% / 99.4% | **[+0.175, +1.303] clears 0** |
| **v1.3** | 26/23 (6L/20S) | 73% / 70% | +0.36% / +0.67% | 69→77 / 82→58 | 97.3% / 99.6% | [+0.181, +1.140] clears 0 |

Both v1.2 variants gained ~40% more trades and **more stable split-halves** (v1.2-Dynamic 57→88 became 60→73 —
the old H2 spike was small-n; v1.2-Relaxed is now 59→61 at 1:1.0). Headline win dips slightly — that is the cost
of the extra (mostly short) signals, **not** a loss of edge. Drop-best-3 stays positive on all.

## Forward audits RE-FROZEN
Gate changed ⇒ new freeze (per the never-re-tune rule). Safe to do now: **every audit was still at forward n=0**,
so no out-of-sample data was discarded. All three candidates now share one common OOS start line:

**freeze_ts 1784516167 = 2026-07-20 02:56:07 UTC** · forward n=0 · PASS at fwd n>=20 & net>0 & t>=1.5.

Check the board: `python study/mmxskew_audit_all.py` (after `study/pull_archive.ps1`).

## NY-session position-sizing overlay — PRE-DECLARED FORWARD HYPOTHESIS (2026-07-21), NOT validated

**Status: recorded, NOT deployed.** Signal generation stays **24/7 on every candidate** — the window is a
*dynamic position-sizing overlay*, never a hard gate. Adding it as a gate would be a NEW gate and would
invalidate that candidate's freeze under the never-re-tune rule.

Proposed rule (fixed now so forward tape can judge it): **1.5× size when the signal bucket CLOSES inside
14:00–21:00 UTC · 1.0× otherwise.**

### Canonical numbers (each candidate's own `build()`+`taken()`, non-overlap ON, hour = UTC hour of `end_time`)
The `ALL` column reproduces the freeze baselines exactly, so this split is on the registered basis.

| Candidate | RR | IN n (L/S) | IN win | IN exp | OFF n (L/S) | OFF win | OFF exp |
|---|---|---|---|---|---|---|---|
| v1.1-NP base | 1:1.0 | 25 (13/12) | 60.0% | +0.219% | 78 (21/57) | 50.0% | −0.025% |
| v1.1-NP base | 1:1.5 | 22 (13/9) | 59.1% | +0.519% | 68 (18/50) | 38.2% | −0.034% |
| v1.2-Relaxed | 1:1.0 | 15 (7/8) | 66.7% | +0.449% | 20 (4/16) | 55.0% | +0.070% |
| v1.2-Relaxed | 1:1.5 | 15 (7/8) | 66.7% | +0.811% | 19 (4/15) | 36.8% | −0.037% |
| v1.2-Dynamic | 1:1.0 | 12 (6/6) | 83.3% | +0.750% | 11 (2/9) | 54.5% | +0.090% |
| v1.2-Dynamic | 1:1.5 | 11 (5/6) | 81.8% | +1.197% | 10 (2/8) | 50.0% | +0.265% |
| v1.3 | 1:1.0 | 8 (4/4) | 87.5% | +0.859% | 18 (2/16) | 66.7% | +0.137% |
| v1.3 | 1:1.5 | 7 (3/4) | 85.7% | +1.409% | 16 (2/14) | 62.5% | +0.348% |

### Why this is a hypothesis and not a finding — six reasons, all measured
1. **Nothing survives multiplicity adjustment.** 14:00–21:00 is rank 1 of 24 rolling 7h windows in 5 of 8 cells
   — it was *selected on this data*. Permutation p adjusted for best-of-24: **0.152 / 0.585 / 0.770 / 0.392**
   @1:1.5 (base / Relaxed / Dynamic / v1.3). Not one cell clears 0.15. The encouraging fixed-window p-values
   (0.013–0.138) are the wrong test.
2. **The blend-vs-flat "lift" is algebraically guaranteed and proves nothing.** Exactly
   `blend − flat = 0.5 · (n_in/n_total) · exp_in`, so *any* positive-expectancy subset shows the same lift.
   The only real test is `exp_in > exp_off`, and that is what fails (1).
3. **Four rows are one sample.** The in-window RR1.5 trade sets are strictly nested (Dynamic's 11 ⊂ Relaxed's
   15 ⊂ base's 22); the union is **22 distinct trades**. Four agreeing rows = one 22-trade sample re-filtered.
4. **Side is confounded with session.** In-window long-share 0.43–0.59 vs off-peak 0.12–0.26 — off-peak is
   overwhelmingly short. Split by side, the size-up cells are n=3–6.
5. **Not uniform inside the window.** Base @1:1.5 per hour: h15 **+1.316%** (n=6) carries the entire mean;
   h19 −0.284%, h17 +0.017%, h18 empty. And v1.2-Relaxed's *second*-best window is **01–08 UTC (+0.787%)** —
   an Asia window nearly as good, which kills the "institutional liquidity" story.
6. **Thin and fragile.** n_in @1:1.5 = 22/15/11/7. Drop-best-3 takes in-window expectancy from +0.519→+0.254
   (base) and +1.409→+0.883 (v1.3); v1.3's PF 13.1 rests on **one** loser. Cost side: the 1.5× deepens the
   worst single trade from −1.84% to −2.77% on three of four candidates.

**Correction to the 2026-07-21 hour-of-day sweep.** That sweep evaluated all 174 signals *independently*
(no non-overlap filter), giving n_in=39 and best-of-24 permutation p=0.006. On the tradeable canonical basis
the sample is 174→90 trades (n_in=22) and the same test gives **p=0.152**. The earlier "survives every control"
read was **overstated** — it rested on trades that cannot all be taken.

**Verdict:** do not deploy on this evidence. Window (14–21 UTC by bucket close) and multiplier (1.5×) are fixed
here so forward trades can test it. Require **exp_in > exp_off** to hold forward — not the blend-vs-flat delta.

## Execution contract — SAME-BUCKET RE-ENTRY (declared 2026-07-21, hard-locked)

**THE RULE: a signal firing on the bucket in which the prior trade exited is NOT taken.**
`taken()` / `gate_v12.walk()`: `if sg["i"] <= last: continue`, where `last` = the prior trade's **exit bar**.
All three candidates already implement this identically (6 independent sites, 0 disagreement) — it was simply
never written down. It is a **declared trading rule, not an artifact**, and it binds on every registered baseline.

### ⚠ The live path does NOT enforce it
`app/mmxskew_detect.py` emits badges only and encodes **no trade sequencing whatsoever**. A live or forward
implementation will therefore *not* arrive at this rule by accident: it must enforce the skip explicitly, or the
forward tape accumulates trades the freeze never priced and the audit diverges silently while every baseline
guard still passes.

### Why locked — the permissive variant (`< last`) was audited and rejected
| candidate | RR 1:1.0 A→B exp | RR 1:1.5 A→B exp |
|---|---|---|
| v1.3 | +0.359 → +0.399 (**+0.040**) | +0.671 → +0.584 (**−0.087**) |
| v1.2-Relaxed | +0.232 → +0.358 (**+0.125**) | +0.337 → +0.201 (**−0.136**) |
| v1.2-Dynamic | +0.434 → +0.570 (**+0.136**) | +0.753 → +0.561 (**−0.192**) |

- **Effect sizes 0.22–0.74 SE** (5% significance needs ~2.0). Every delta sits at the 24.8th–78.2nd percentile
  of convention A's own bootstrap. Nothing to choose on performance.
- **The sign flips with RR** — positive at 1:1.0 in all three, negative at 1:1.5 in all three. RR is *downstream*
  of the entry rule, so a genuine entry-convention effect has no mechanism to reverse with TP distance. That
  reversal is the signature of path-dependent luck.
- **It is a RE-CHAINING, not "19 extra trades."** B also *removes* 7 trades that A takes, because each admitted
  trade moves `last` and displaces everything downstream. v1.3 @1:1.0's apparent +1.43% is **+2.37% from the
  shuffle dropping 2 cascade losers** and **−0.94% from the marginal trades themselves**. Optimising over that
  is optimising over a reshuffle.
- **Neither convention is look-ahead.** B is causally implementable: across 13/13 affected cases the exit touch
  is demonstrably before the bucket close (1m sub-bucket reconstruction — median **87s** of slack, 79.8% of
  bucket volume already traded at the touch; 6/13 additionally proved by a pure retrace argument). So causality
  is *neutral* — it neither forbids B nor mandates it. The decision rests on evidence, and the evidence is noise.

### What convention A actually does — an outcome-conditioned deletion
Of 103 TP exits, **18 land on a bar that also carries a gated signal; of 59 SL exits, 0 do** (Fisher one-sided
p = **1.59e-04**). This is structurally forced: a short's TP is hit on a down-bucket, which is exactly what
generates the next short signal; its SL is hit on an up-bucket, which cannot. So **all 19 blocked signals are
post-TAKE-PROFIT, same-side re-loads** (19 same-side / 0 reversals) — operationally, pyramiding back into an
extended leg at a worse price. Consequence: A does **not** bias the expectancy estimate (deduped permutation
p = 0.387) but it **understates tradeable frequency by ~11%** (19/174). The freeze must not be read as
"there were no other opportunities".

### Hypotheses TESTED AND REJECTED (do not re-raise without new data)
- *"Re-entries reuse the just-stopped-out extreme as their stop"* — **false**. All 19 follow a TP, not an SL;
  there is no stopped-out level. Prior exit and new stop sit on **opposite** sides of entry, mean gap 1.111%.
- *"Their stops are tighter"* — **false, they are wider** (1.069% vs 1.005%). Structural: `corr(stop distance,
  sqrt(mov_mag)) = 0.9993` and every gate carries a mov_mag floor, so the gate mechanically forbids tight stops.
- *"The stop-side extreme is freshly touched"* — **false, it is stale**: printed at cum-volume fraction median
  0.049 (first ~5% of the bucket), staler than baseline (MW p=0.0214, significant in the *opposite* direction).
- *"The cohort is genuinely worse"* — **not established**. The 19 collapse to 13 distinct trades / 11 buckets /
  **7 market episodes**. Deduped: diff −0.275pp, p=0.408; cluster bootstrap 95% CI [−0.447, +0.525] contains the
  baseline mean. The naive p=0.03 was pseudo-replication counting the same trade up to 6×.
- *"A drops signals after volatile buckets"* — tested, not significant (range% MW p=0.063, mov_mag p=0.253).

## v1.2-Dynamic RE-FROZEN: T 1.25 -> 1.30 (2026-07-21)

**A robustness edit, not a performance one — no in-sample number improved.** The taken set is trade-for-trade
IDENTICAL (23/21, same nets, same win%, same freeze_ts), because the only gated signal between 1.25 and 1.30
(`i=3263`, ratio 1.29990) was already overlap-skipped. Gated count 30 -> 29; **taken unchanged**.

Why move at all — the re-sweep of T on the no-POC population (the sweep that had never been run):
- **No T in [1.10, 1.55] is statistically separable from any other.** Nesting-aware permutation, 41 nested
  comparisons per RR (a two-sample test is invalid — higher-T sets are strict subsets): min p = **0.080 / 0.086**.
  The whole T-curve is noise, so there is no performance basis on which to choose T. Only robustness is left.
- The old "flat plateau [1.25,1.40]" justification is **RETIRED**: it came from the with-POC population, and the
  apparent flatness is a mechanical artifact — the signals in the band were already overlap-skipped, so dropping
  them is a no-op. Strip the overlap filter and the curve inverts to monotone-declining with 1.25 a local *peak*.
- 1.25 sat **0.23% above a material breakpoint**. Under mov_mag jitter its taken set changed in **35.9%** of
  draws at sd 0.5% and **52.4%** at sd 1.0%; T=1.30 changed in **0.0%** of both. Across 9 EMA periods 1.30 is
  never worse and strictly better at three (EMA 30/35/80); 1.25 better at none. `sqrt(1.24716 x 1.35539) =
  1.300149` — 1.30 is the geometric midpoint of the two material cliffs.
- T=1.45/1.50 scored higher (+0.826/+0.817) and were **rejected**: the lift is one excluded loser swapped for an
  excluded winner (nesting-aware p 0.510/0.524; a random 2-trade drop beats it 95% of the time).

Done at **forward n=0, so no out-of-sample data was discarded**. `app/mmxskew_detect.RATIO_MIN` moved in
lockstep (parity re-verified: **0/174** disagreements at every scan anchor 6h->168h). Prior T=1.25 freeze and
logs archived under `study/out/superseded/`. Per the never-re-tune rule this is the **last discretionary edit**
to this gate — forward tape decides from here.

**Guard hardened.** `study/mmxskew_audit_all.py` now records the gate CONSTANTS in each freeze (`gate_params`)
and asserts them alongside the baseline numbers, because a threshold can move while the trade set stays
bit-identical — exactly this case — and the numeric baseline alone would pass it silently. Gate descriptions in
the board are now derived from the modules' own constants rather than hardcoded.

## Live-terminal parity fix (2026-07-21)
`app/mmxskew_detect.detect()` restarts its EMA-50, `run_pos` **and** eff-agg share at index 0 of whatever list it
receives, but the study evaluates against full history. `_draw_mmxskew` was passing only the scan window (median
~41 volume buckets at the now-24h default), so **14 of 174 in-sample badges were wrong (10 false-positive,
4 false-negative)** at the real now-24h window (12 wrong at a fixed 44-bucket window) — biased toward over-firing, since a truncated window also understates `run_pos`.
Fixed by prepending `mmxskew_detect.WARMUP_MIN = 500` buckets and shifting indices back. Measured after the fix:
**0/174 wrong at every scan anchor 6h→168h**; full-history `detect()` reproduces the frozen gate exactly.

**Repaint fix (same day).** `detect()` also evaluated the still-forming active bucket, which the study never does
(`range(first, len(A) - 1)`). Against 1m-reconstructed partials the v1.2-Dynamic verdict differs from the closed
verdict on **25% of signals at 25% formation, 21% at 50%, 16% at 75%** — i.e. badges could appear and vanish, and
a trade could be taken on a signal that never existed at close. `detect()` now loops `range(n - 1)`, so the last
bucket is never emitted. Verified: while the signal bucket is still forming it is correctly **not** badged; once
it closes (next bucket forming) the badge is right on **0/174** at every anchor. Safe for earlier bars because
`eff_causal_share` is genuinely causal (appending a bucket changes no earlier share by >0.0) and the trailing EMA
excludes the current bucket. **Visible effect: a badge now appears one bucket later than before — that delay is
the bug being fixed, not a regression.**

Neither fix changed a gate, threshold or feature — both freezes are untouched; the live badge now *matches* the
registered gate. `study/mmxskew_audit_all.py` additionally asserts each candidate's in-sample half still
reproduces `fz["baseline"]` and refuses to append a forward log row on drift.

## Caveats
In-sample, one 30-day regime, short-heavy (dropping POC makes it more so). v1.2-Dynamic's T=1.30 is not
performance-tuned (no T in [1.10,1.55] is separable) but the magnitude will still regress. v1.3's long cell is small. Forward tape is the only real test.
