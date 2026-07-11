# SSS — Scalping Swing Strategy

**Status:** research spec, built 2026-07-10 from the session's independent-entry studies. Two setups (Reversal
Fade + Trend Pullback), both governed by one principle. **Independent of Pivot V3** (different engine). Only edit
this doc when explicitly told.

**Core principle (the one thing every test proved):**
> **Enter on CONFIRMED momentum, never the raw extreme/extension.** The extreme itself predicts nothing; the
> momentum having *turned* (Reversal) or *held* (Trend) is the whole edge. Every counter-trend test that faded a
> raw extreme lost with high significance; adding the confirmation filter flipped it to breakeven.

**Fee reality (read before trading either setup):** both setups land at **gross +0.06 … +0.09%/trade** →
**breakeven at taker (0.10% round-trip), profitable at maker (~0.02–0.05%)**. Maker execution is the biggest
lever, larger than any signal tweak. The only fully-taker-profitable variant is the *fully-stacked* Trend setup =
**Pivot V3 Path A** (+0.30%/trade net).

---

## Shared definitions (all values CAUSAL — read at bar `j` from data up to `j − LOCK`, LOCK = 7)

- **P0 = the composite SUM "blue line"** (`sum0`, `pivot_detect._p9_global`): abs+eff+ER lean + exhaustion, LOCK-
  smoothed. Golden dashed reference lines at **±50**. A **green cross** = up-cross / **red cross** = down-cross of a
  level (±50 / 0). "LOCKED P0" = `sum0[j−LOCK]`.
- **P2 = eff-agg** rolling share → spread `(2·share − 1)·100`. **LOCKED** = settled `(2·e_sh[j−LOCK] − 1)·100`;
  **NON-LOCKED** = first-print/live `(2·e_sh_c[j] − 1)·100`. **HM cycle** = a run of share on one side of 50%.
  **HMS** = harmonic-mean dominant-side spread over the last 2 LOCKED cycles.
- **4H wicks** (last COMPLETED 4h bucket, `bar_quantiles.vq`): **buy wick = [low, vq_lo]** (demand),
  **sell wick = [vq_hi, high]** (supply).
- **Candle flow border:** buy-led (`buy_vol>sell_vol`) closed **down** → **ABSORB orange** (absorbed buying, the
  bearish tell); sell-led closed **up** → **ABSORB blue** (absorbed selling, bullish tell); heavy one-sided → neon
  green/red; else std grey.
- **ZigZags:** **scalp = 0.15%**, **swing = 0.5%**; labels HH / HL / LH / LL.

---

## SETUP A — REVERSAL FADE  🔒 (fade a peak / trough)

Fixed R:R **1.5**. In-sample: n=124 · **51% win · net −0.020% (breakeven) · gross +0.080%** · short side net **+0.016%**.

### 🔻 SHORT (fade a peak) — ALL true at the trigger bar
1. **Location:** close in the **4H SELL wick** (`vq_hi ≤ close ≤ high`).
2. **Exhaustion:** LOCKED **P0 > +50**.
3. **Peak momentum:** LOCKED **P2 still bullish (> 0)** — fade *into* strength; do NOT wait for P2 to turn (it lags 7 bars).
4. **CONFIRMATION 🔑:** **P0 has crossed DOWN within the last 15 bars** (red cross of +50 / 0 / −50). *The single
   feature that separated winners from losers (Welch t +3.05).*
5. **Trigger (need one):** an **ABSORB-orange candle** (buy-led, closed down) **OR** the **scalp ZigZag prints a Lower-High**.
6. **Entry:** short at the trigger bar's close.
7. **Stop:** recent local high (max high over last **10 bars**) **× 1.001** (just above the extreme).
8. **Target:** entry − **1.5 × risk**.

### 🔺 LONG (fade a trough) — exact mirror
1. Close in the **4H BUY wick** (`low ≤ close ≤ vq_lo`).
2. LOCKED **P0 < −50**.
3. LOCKED **P2 still bearish (< 0)**.
4. **P0 crossed UP within 15 bars** (green cross).
5. Trigger: **ABSORB-blue candle** (sell-led, closed up) **OR** scalp **Higher-Low**.
6. Long at trigger close. **Stop:** recent 10-bar low **× 0.999**. **Target:** entry + **1.5 × risk**.

> **IGNORE (proven noise in the winner/loser t-table):** P2 confirmation direction, HMS, current-cycle HM, cycle
> age ("P2 minutes"), candle border *thickness*, candle body direction, and the P0 *level* — winners and losers are
> equally extreme. **Only the P0 CROSS matters.**

---

## SETUP B — TREND PULLBACK  🔒 (buy the dip / sell the rally, WITH the trend)

In-sample (long-only, selective): **gross +0.090%** (breakeven net at taker). Exit = *ride*; a trailing stop or a
fixed TP both HURT — let winners run.

### 🔺 LONG (buy the pullback in an uptrend)
1. **Trend:** LOCKED **HM cycle bullish** (stronger: cyan tier / P2 spread > 80).
2. **Location:** price **pulls back into the 4H BUY wick** (`low ≤ close ≤ vq_lo`).
3. **Momentum aligned:** LOCKED **P2 still bullish** — selective version **≥ 40** (not just > 0).
4. **Entry:** at the pullback touch of the wick.
5. **Stop:** below the 4H buy wick (structural).
6. **Exit:** **ride to the reversal = the opposite HM-cycle lock.** Do NOT trail (whipsaws) or fix a TP (caps it).

### 🔻 SHORT (sell the rally in a downtrend) — mirror
LOCKED HM cycle bearish; rally into the **4H SELL wick**; LOCKED P2 still bearish (≥ 40 aligned); short the touch;
stop above the wick; ride to the opposite-cycle lock. *(Weaker than the long on a bull tape.)*

---

## Status, results, and the road to taker-profitability

| Variant | gross/tr | net (taker) | t | note |
|---|---|---|---|---|
| A: Reversal fade + P0-cross (R:R 1.5) | +0.080% | −0.020% | −0.63 | breakeven; short side +0.016% net |
| B: Trend pullback, 4h-wick long, eff≥40 | +0.090% | −0.010% | −0.18 | breakeven; long-only |
| **B fully stacked = Pivot V3 Path A** | — | **+0.301%** | **+2.79** | **taker-profitable** (cyan tier + 5-leg D + zone) |

Each conviction filter stacked onto Setup B roughly **triples** the edge (4h-wick +9bp → Path A +30bp). Path A is
the existence proof that the Trend family clears taker once fully selective.

**Fees:** at maker (~0.02–0.05%) both A and B are outright profitable as-is (+0.03 … +0.06%/tr).

---

## Studies (reproduce)
- Reversal fade + detector + P0-cross: `study/peak_detector.py` (toggle `require_cross`).
- Winner/loser diagnosis (the t-table that found the P0-cross): `study/peak_wl_study.py`.
- Swing-extreme signature (why P0 leads, P2 lags): `study/swing_reversal.py`.
- Trend pullback + 4h-wick + exit variants: `study/pullback_trend.py`.
- Cycle-ride / eff+HMS / P0-reversion nulls (what NOT to do — fading raw extremes): `study/random_cycle_ride.py`,
  `study/eff_hms_ride.py` (loss records, kept for the record).

## Change log
- **2026-07-10** — SSS created. Setup A (Reversal Fade) recorded with the **P0-cross confirmation** (the winner/loser
  t-table's only significant separator, t +3.05): in-zone, P0>|50|, P2 at peak, absorb-or-scalp trigger, stop beyond
  the 10-bar extreme, R:R 1.5 → breakeven net / gross +0.08%. Setup B (Trend Pullback) recorded: locked-HM-cycle
  trend + 4h-wick pullback + ride-to-cycle-lock exit → gross +0.09%. Core principle locked: **confirmation over
  prediction**; **taker fee is the wall, maker flips both green.** Fully-stacked Setup B = Pivot V3 Path A (proven
  taker-profitable). Not forward-frozen yet — both A and B are in-sample on the ~11-day bull tape.
