# S4-GEO Stage 1 — Bar-Geometry Groups vs Next-Bar Direction (1m)

_**FRAMING (mandated): the 1m dataset is SPENT (~35+ cumulative trials across this program). This stage is
CHARACTERIZATION / HYPOTHESIS GENERATION only. The identical pipeline runs later, unchanged, on the
never-analyzed 1h/4h tables — THAT is the real exam.** Outcome = next closed bar's direction (UP if next
O<C): an INFORMATION measure — no barriers, no fees, not profitability._

## Data & definitions
M = volume-weighted median, P = POC — both from the SHARED `app.bar_quantiles` module (the exact 'W'-mode
implementation, proven identical on 3,000 real ladders). 10,000 bars -> **9,192 usable rows** (65 no-ladder
excluded, 692 next-bar dojis excluded). Split: first 70% discovery (n=6,337) / last 30% comparison
(n=2,854), 1-bucket embargo. Baselines: **disc 49.64% up / comp 51.86% up** (near-symmetric — the
next-bar target carries far less regime skew than the barrier labels). Day-block bootstrap over **5 UTC
days** (CIs honest but wide by construction).

## Tie exclusions (strict >)
L1-high 26.9% · L1-low 27.0% · L2 47.9% · **L3 75.6%** (full orderings are THIN — M==P on ~49% of
bars: on balanced ladders the median and POC coincide, wiping most 4-way orderings). L4 pair ties:
O-C 6.8 / O-M 15.9 / O-P 19.3 / C-M 15.0 / C-P 19.3 / **M-P 49.2**%.

## Multiplicity counter
**50 cells screened this stage** (L1 8 + L2 12 + L3 24 + L4 6), on top of ~35+ prior trials against this
snapshot. Survivor bar: disc n>=100, |lift|>=5pp, CI clear of 0; comparison confirmation -> CANDIDATE
(not PASS — spent data).

## L1 — highest / lowest of {O,C,M,P} (8 cells)
| cell | disc n | P(up) | lift | 90% CI | comp n | lift | flags |
|---|---|---|---|---|---|---|---|
| L1:high_O | 1960 | 46.84 | -2.81 | [-4.86,-1.37] | 873 | 0.03 |  |
| L1:low_O | 1895 | 53.3 | 3.65 | [3.23,4.21] | 917 | 2.56 |  |
| L1:high_C | 1984 | 52.92 | 3.28 | [1.61,4.53] | 926 | 0.84 |  |
| L1:low_C | 1987 | 46.4 | -3.24 | [-3.65,-2.78] | 866 | -1.86 |  |
| L1:high_M | 156 | 46.15 | -3.49 | [-8.3,6.23] | 78 | 7.12 |  |
| L1:low_M | 129 | 55.81 | 6.17 | [0.03,13.98] | 86 | -4.18 | SURV |
| L1:high_P | 496 | 46.37 | -3.27 | [-5.82,-0.74] | 242 | -5.58 |  |
| L1:low_P | 579 | 49.22 | -0.42 | [-4.24,3.89] | 255 | 0.69 |  |

## L2 — (highest, lowest) pairs (12 cells)
| cell | disc n | P(up) | lift | 90% CI | comp n | lift | flags |
|---|---|---|---|---|---|---|---|
| L2:O>C | 1052 | 47.24 | -2.4 | [-4.66,-1.21] | 494 | 0.37 |  |
| L2:O>M | 32 | 68.75 | 19.11 | [11.3,28.04] | 22 | -6.4 |  |
| L2:O>P | 250 | 51.2 | 1.56 | [-5.51,10.46] | 104 | 6.8 |  |
| L2:C>O | 1038 | 53.95 | 4.3 | [1.7,6.25] | 525 | 2.43 |  |
| L2:C>M | 43 | 51.16 | 1.52 | [-13.66,11.14] | 26 | -5.7 |  |
| L2:C>P | 267 | 49.06 | -0.58 | [-4.22,2.42] | 119 | -1.44 |  |
| L2:M>O | 44 | 45.45 | -4.19 | [-11.27,4.59] | 20 | -1.86 |  |
| L2:M>C | 60 | 50.0 | 0.36 | [-4.38,8.57] | 23 | 4.66 |  |
| L2:M>P | 4 | 25.0 | -24.64 | [nan,nan] | 3 | 14.81 |  |
| L2:P>O | 209 | 49.76 | 0.12 | [-6.65,4.92] | 110 | -7.31 |  |
| L2:P>C | 231 | 42.86 | -6.79 | [-9.82,-1.04] | 101 | -4.33 | SURV **CANDIDATE** |
| L2:P>M | 5 | 20.0 | -29.64 | [-50.52,-15.68] | 5 | -11.86 |  |

## L3 — full orderings (24 cells; mostly thin, see tie note)
| cell | disc n | P(up) | lift | 90% CI | comp n | lift | flags |
|---|---|---|---|---|---|---|---|
| L3:O>C>M>P | 105 | 56.19 | 6.55 | [-2.22,12.48] | 42 | 7.67 |  |
| L3:O>C>P>M | 6 | 66.67 | 17.02 | [-10.41,17.16] | 3 | -51.86 | thin |
| L3:O>M>C>P | 54 | 57.41 | 7.76 | [0.55,19.23] | 25 | 4.14 | thin |
| L3:O>M>P>C | 225 | 50.22 | 0.58 | [-0.82,2.2] | 104 | 0.07 |  |
| L3:O>P>C>M | 2 | 0.0 | -49.64 | [nan,nan] | 3 | -51.86 | thin |
| L3:O>P>M>C | 188 | 42.55 | -7.09 | [-14.49,-2.16] | 100 | -1.86 | SURV |
| L3:C>O>M>P | 118 | 49.15 | -0.49 | [-4.53,4.14] | 42 | -6.62 |  |
| L3:C>O>P>M | 9 | 55.56 | 5.91 | [-29.01,29.13] | 6 | -18.52 | thin |
| L3:C>M>O>P | 54 | 59.26 | 9.61 | [1.0,17.74] | 29 | -10.48 | thin |
| L3:C>M>P>O | 203 | 53.69 | 4.05 | [0.26,10.71] | 107 | -7.0 |  |
| L3:C>P>O>M | 10 | 50.0 | 0.36 | [-26.66,20.89] | 3 | -51.86 | thin |
| L3:C>P>M>O | 219 | 50.23 | 0.58 | [-4.49,3.64] | 123 | 5.05 |  |
| L3:M>O>C>P | 1 | 0.0 | -49.64 | [nan,nan] | 1 | 48.14 | thin |
| L3:M>O>P>C | 8 | 62.5 | 12.86 | [-0.39,50.89] | 3 | -18.52 | thin |
| L3:M>C>O>P | 2 | 50.0 | 0.36 | [nan,nan] | 0 | nan | thin |
| L3:M>C>P>O | 4 | 0.0 | -49.64 | [nan,nan] | 4 | -1.86 | thin |
| L3:M>P>O>C | 12 | 58.33 | 8.69 | [-8.85,22.32] | 5 | 28.14 | thin |
| L3:M>P>C>O | 9 | 66.67 | 17.02 | [-0.39,41.9] | 8 | -26.86 | thin |
| L3:P>O>C>M | 0 | nan | nan | [nan,nan] | 2 | -1.86 | thin |
| L3:P>O>M>C | 55 | 43.64 | -6.01 | [-12.11,3.67] | 25 | -15.86 | thin |
| L3:P>C>O>M | 1 | 100.0 | 50.36 | [nan,nan] | 1 | -51.86 | thin |
| L3:P>C>M>O | 49 | 42.86 | -6.79 | [-13.34,-0.39] | 27 | -3.71 | thin |
| L3:P>M>O>C | 83 | 42.17 | -7.48 | [-21.78,0.16] | 40 | 5.64 | thin |
| L3:P>M>C>O | 81 | 50.62 | 0.97 | [-11.74,10.7] | 44 | -4.13 | thin |

## L4 — elementary pairs, cell = LEFT > RIGHT (6 cells)
| cell | disc n | P(up) | lift | 90% CI | comp n | lift | flags |
|---|---|---|---|---|---|---|---|
| L4:O>C | 2980 | 46.68 | -2.97 | [-3.87,-2.09] | 1293 | -1.35 |  |
| L4:O>M | 2695 | 47.38 | -2.26 | [-3.46,-1.34] | 1173 | -1.3 |  |
| L4:O>P | 2592 | 47.42 | -2.23 | [-3.85,-1.14] | 1140 | -0.37 |  |
| L4:C>M | 2682 | 52.8 | 3.15 | [2.64,3.57] | 1250 | 1.5 |  |
| L4:C>P | 2574 | 51.94 | 2.3 | [1.58,3.03] | 1185 | 1.22 |  |
| L4:M>P | 1659 | 49.31 | -0.34 | [-1.43,0.66] | 741 | -0.31 |  |

## Survivors & the candidate list
3/50 discovery survivors; **1 CANDIDATE** confirmed on the comparison slice:

- **`L2:P>C` — POC is the HIGHEST of {O,C,M,P} and Close the LOWEST** -> next bar leans **DOWN**
  (disc lift **-6.8pp** CI[-9.8,-1.0], n=231; comp **-4.3pp** same-sign, n=101). Read: the bar closed below
  its entire acceptance structure — continuation, not mean-reversion, at the 1-minute scale.
- Not confirmed: `L1:low_M` (+6.2 disc, sign-flipped comp), `L3:O>P>M>C` (-7.1 disc, -1.9 comp <50%).

**Pre-registered list for the 1h/4h exam: exactly one hypothesis — `L2:P>C` -> next bar DOWN.**
(Its mirror `L2:C>P`... not a survivor; only the stated cell is registered.)

## HARD STOP
The 1h/4h tables were NOT touched in this stage, per mandate.
