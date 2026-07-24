# Skew Divergence — extreme-half DOMINANCE filter (forward-test candidate)

Registered 2026-07-23. **Not** in-sample-significant — a HIGHLIGHT to watch on the tape, not a gate.

**FOUR FROZEN FILTERS 2026-07-23 (user decision):** the working Skew Divergence setup requires **dom > 0.55**
AND **climax close** AND **R2-vacuum** AND **move-expansion** (all below). `pass_full` = all four. **Terminal render (2026-07-23): a triangle prints
ONLY on the core setup (dom>0.55 AND climax) — no more hollow — and a GOLD STAR is overlaid when all four pass.**
So star = 4-filter (≈75% in-sample, fewer signals), bare triangle = core dom+climax (≈69%, ~46% more signals);
core-fail draws nothing. Pure highlight, trades unchanged.
⚠ **Read the funnel honestly:** the in-sample climb (Base 60.8% → all four 75.0%) is MOSTLY MECHANICAL — each
filter is chosen to drop in-sample losers, so the win% must rise. **None of the four is individually significant**
(dom p=0.14, climax p=0.17, R2-vacuum post-hoc, expansion p=0.34), reject cells are n=6–9, and n falls 51→24 on
one 30-day regime. Expect heavy regression; forward tape is the only judge.

## Base strategy (unchanged)
Skew Divergence, 1h, exploratory (`app/skew_divergence_detect.py`):
- LONG  = candles i-1 AND i both bearish + profile skew ≥ +0.5 → fade up.
- SHORT = both bullish + skew ≤ −0.5 → fade down.
- Entry candle-i close; fixed **0.8% SL / 0.8% TP (1:1)**.
- Standalone in-sample: n=51, **60.8% win**, shuffled-skew null **p=0.069** (not significant, not frozen).

## The filter
Split the entry candle at its price midpoint **(high+low)/2**. `dom` = the EXPECTED aggressor's share of the
**EXTREME half**:
- LONG  → **lower-half SELL share** (buyers absent at the lows).
- SHORT → **upper-half BUY share** (sellers absent at the highs).

`dom → 1.0` means the counter-side has walked away from that half. Threshold **DOM_MIN = 0.55**
(`skew_divergence_detect.half_dom` / `pass_dom`).

## In-sample result (native 0.8/0.8 exit, canonical taken() basis, n=51)
| split | n | win% | mean/tr |
|---|---|---|---|
| dom > 0.55 | 43 | 65.1% | +0.162% |
| dom ≤ 0.55 |  8 | 37.5% | −0.280% |
| gap | | **+27.6pp** | one-sided p=**0.141**, two-sided 0.236 |

Both sides agree (LONG 62.5% vs 50%, SHORT 68.4% vs 25%). Full-sample corr(dom,win)=+0.192 (p=0.177);
winners mean dom 0.665 vs losers 0.630.

## Why it is NOT frozen
- **Not significant**: p=0.14; the gap rides on an **n=8** reject cell.
- **Mined threshold**: 0.55 chosen after sweeping 0.55/0.60/0.70/0.80/0.90; 0.60 already collapses to +6.6pp (p=0.44).
- **Extreme form rarely occurs**: dom ≥ 0.80 ("counter-side < 19%") happens only n=2 at signals (both won = noise),
  though it fires on ~9% of buckets generally.
- **Filter on an unestablished edge**: the base signal itself is only p=0.069.
- Disjoint terciles are non-monotone (55.6% / 64.7% / 62.5%) — really "the bottom ~16% are junk", not a gradient.

## Forward test
Keep only **dom > 0.55**; watch whether the **dom ≤ 0.55** setups keep failing (~37% in-sample). Terminal draws
`pass_dom` **solid** triangles / fail **hollow**, and the footprint pane's **½dom** row shows the live per-half
buy/sell composition. Judge forward; do not re-tune the threshold on in-sample data.

## 2nd FROZEN filter — candle-2 CLIMAX CLOSE (2026-07-23, on the frozen dom>0.55 base)
Entry candle closes hard WITH the prior move: LONG closes in the **bottom third** of its range, SHORT in the
**top third** (`ca = 1-(c-l)/(h-l)` for long / `(c-l)/(h-l)` for short; pass = ca ≥ **CLIMAX_MIN = 2/3**).
Exhaustion signature. `pass_full` = pass_dom AND pass_climax.

On dom>0.55 (n=44): climax-third **68.6%** (n=35, +0.217%) vs NOT **44.4%** (n=9, −0.169%), gap **+24.1pp**,
one-sided p=0.170. Both sides agree (LONG 66.7% vs 25%, SHORT 71.4% vs 60%). Holds without dom too (+19.5pp),
and it removes 9 signals dom had KEPT → the two filters catch **different** junk (semi-independent). Same
caveats as dom: not significant, n=9 reject cell, mined threshold, stacking two filters on n=51.

## 3rd FROZEN filter — R2-VACUUM (2026-07-23, on the frozen dom+climax base) ⚠ WEAKEST
Per-half absorption (`absorption.absorption_halves`, A>0 = that half's aggressor ABSORBED). The fade wants the
2nd half **NOT more absorbed** than the 1st — a thin-book vacuum, not support forming: **pass_absorb** = dA
(= A_h2 − A_h1) ≤ **ABSORB_MAX = 0.0**, or dA unavailable (no `price_h1` / <20 baselined priors → NOT penalised,
degrades to dom+climax). This is the FROZEN REVERSE of the tested-and-rejected "R2 more absorbed" hypothesis.

In-sample on the pass_full base (R1/R2 available 24/35): dA≤0 **77.8%** (n=18, +0.364%) vs dA>0 **50.0%** (n=6),
gap +27.8pp; winners mean A2 −0.56 (EASY) vs losers −0.22. Coherent with dom (aggressor absent) + climax close —
one exhaustion-vacuum mechanism. **⚠ WEAKEST filter: n=6 vs 18, a post-hoc flip of a rejected hypothesis, a 3rd
filter stacked on two mined ones, coverage bounded by reconstructable `price_h1`.** Forward tape is the only test.

## 4th FROZEN filter — MOVE EXPANSION (2026-07-23, on the frozen base) ⚠ WEAKEST p, full coverage
The whole-candle move EXPANDS from candle 1 to candle 2 in the fade direction — a LONG wants candle 2 the bigger
DOWN move (**dP1 > dP2**), a SHORT the bigger UP move (**dP1 < dP2**); `pass_expand` = side·(dP1 − dP2) > 0,
dP = (close−open)/open. Needs no `price_h1` → **full coverage** (unlike R2). On the pass_full base (n=30): pass
**73.9%** (n=23) vs **57.1%** (n=7), +16.8pp, one-sided **p=0.34** — the weakest p of the four.

## Honest cumulative funnel (taken(), one 30-day window, break-even 55%)
| stack | keeps | win% | mean/tr |
|---|---|---|---|
| Base (no filters) | 51 | 60.8% | +0.093% |
| + dom > 0.55 | 44 | 63.6% | +0.138% |
| + climax close | 35 | 68.6% | +0.217% |
| + R2-vacuum (= pass_full v3) | 30 | 70.0% | +0.240% |
| + move-expansion (= pass_full v4) | 24 | 75.0% | +0.320% |

The 60.8→75.0 climb is **mostly by construction** (each filter trims in-sample losers); it is NOT four
independent confirmations and will regress — and per the winners/losers decomposition, only dom+climax cut MORE
losers than winners (R2-vacuum & expansion each cut more winners than losers → win% rises by denominator effect,
not edge). Account sim ($200k, 10%×10x, compounding, in-sample): dom+climax +7.8% ≈ all-4 +7.9% (the extra two
filters add ~$250). **Terminal** (`_draw_skewdiv`): triangle prints on core dom+climax, GOLD STAR overlaid on
pass_full (all four), core-fail draws nothing. Study forward; do not re-tune thresholds.

## Rejected co-hypotheses (do NOT re-run)
- **eff-agg dropped** (dE = eff-agg(c2)−eff-agg(c1) < 0 → winner), n=51: backwards & null. dE<0 58.3% vs dE≥0
  63.0%, gap −4.6pp, p=0.78.
- **da2-accel aligned** (LONG da2 c2>c1 / SHORT c2<c1 → winner), n=51: backwards & null (rarely met, 6/33).
  PASS 50.0% vs FAIL 63.0%, gap −13pp, p=0.66.
- **R2 more absorbed than R1** (dA > 0 → winner): tested, **backwards** (p=0.96) — its REVERSE is now FROZEN
  filter #3 (R2-vacuum, above). Do not re-test this direction.
