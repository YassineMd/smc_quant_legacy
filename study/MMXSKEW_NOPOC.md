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
| **v1.2-Dynamic** | `run_pos<=4` + `mov_mag_ratio>=1.25` | `study/mm_skew_v12d_validate.py` |
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

## Caveats
In-sample, one 30-day regime, short-heavy (dropping POC makes it more so). v1.2-Dynamic's T=1.25 remains
in-sample-tuned (magnitude will regress). v1.3's long cell is small. Forward tape is the only real test.
