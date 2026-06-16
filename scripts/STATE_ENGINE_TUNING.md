# Mode 10 State Engine — Tuning Reference

The verdict on **Mode 10 line 12** comes from `app/bucket_state.py` (A3b). Every state
scores in `[0,1]` as the **geometric mean** of its factors; the highest-scoring state
wins and its score *is* the confidence %. The chip's background opacity scales to that
confidence, and a ★ marks ≥80%.

**These constant values are FIRST GUESSES** — proven on the synthetic test
(`scripts/test_a3b_state_engine.py`, 13 verdicts) but **never watched on real buckets.**
This file is the map for calibration day: when a live verdict feels wrong, find the
state, find the knob, nudge it. We change **constants only — no logic.**

---

## How to read a knob

For a normal factor `ramp(x; LO, HI)` (0 below LO, full credit at HI):
- **↑ LO → pickier** (the state needs a stronger signal → fires less / lower %).
- **↓ LO → looser** (fires more, on weaker signals). HI sets where it saturates.

Factors marked **⟲** are inverse (`1 − ramp`, i.e. "low‑X" or "kill" terms) — the
direction flips, noted inline. Because confidence = the winning geomean, tightening a
**core** knob lowers both how often that state wins *and* its confidence.

---

## Per-state knobs

### STRONG BULL / BEAR — clean directional conviction
| constant | value | role | nudge |
|---|---|---|---|
| `DELTA_LO/HI` | 0.10 / 0.55 | net aggression `(buy−sell)/vol` | ↑LO if STRONG fires on weak/choppy pushes; ↓LO if it misses clean trends *(shared: TRAP, ROTATION, CHOP)* |
| `OPEN_OI_LO/HI` | 0.10 / 0.50 | fresh OI `opL`(bull)/`opS`(bear) /vol | ↑LO if STRONG fires when OI isn't really building (should be EXHAUSTION/SQUEEZE); ↓LO if it misses real breakouts |
| `TRANSLATE_LO/HI` | 1.30 / 2.00 | **⟲** absorption-kill (effort translating) | **↓LO if STRONG fires on absorbed buckets that should read EXHAUSTION**; ↑LO if STRONG gets demoted while price clearly runs |
| `VEL_LO/HI` | 1.00 / 2.20 | soft pace bonus | minor; ↑ if you want fast-only STRONG *(shared: CHOP)* |

### BULL / BEAR EXHAUSTION — Step-5 z firing + OI draining / closing flow
| constant | value | role | nudge |
|---|---|---|---|
| `ABSORB_LO/HI` | 1.20 / 2.00 | the Step-5 z core (`b_mult`/`s_mult`) | ↑LO if EXHAUSTION calls every pullback; ↓LO if it misses obvious stalls *(also soft in TRAP)* |
| `DRAIN_OI_LO/HI` | 0.05 / 0.45 | OI draining `−ΔOI/vol` | ↑LO to demand clearer position-closing *(shared: SQUEEZE)* |
| `CLOSE_FLOW_LO/HI` | 0.15 / 0.50 | cover/puke flow `clS`/`clL` /vol (alt path in) | ↑LO to require more closing flow |

### BULL / BEAR TRAP — effort absorbed → reversal
Hard gate = the close sign (`result<0` bull / `result>0` bear) — **not tunable**, only the magnitude via `FAIL`.
| constant | value | role | nudge |
|---|---|---|---|
| `FAIL_LO/HI` | 0.05 / 0.50 | reversal depth (core) | **↑LO if TRAP fires on tiny wicks/shallow reversals**; ↓LO if it misses real failed breakouts |
| `SWEEP_LO/HI` | 0.03 / 0.50 | soft swept-level boost | ↑ to demand a deeper liquidity grab for full credit |
| `POC_LO/HI` | 0.50 / 0.85 | soft POC-stacked-high (the folded-in factor) | ↑LO to require POC higher in the candle |
| *(aggression)* | — | shared `DELTA`; `ABSORB` soft confirm | — |

### SHORT / LONG SQUEEZE — forced covering (your wrong-star hotspot)
| constant | value | role | nudge |
|---|---|---|---|
| `SQUEEZE_FLOOR_LIQ` | 0.10 | liq fraction that **triggers the ≥0.80 floor (star gate)** | **↑ if squeezes STAR too easily on modest liqs** *(top knob for wrong stars)*; ↓ if real squeezes don't get floored |
| `LIQ_LO/HI` | 0.03 / 0.20 | forced-flow fraction `liq_side/vol` (pre-floor) | ↑LO if it scores high on minor liqs |
| `SQUEEZE_FLOOR` | 0.80 | the locked floor level | ↑/↓ the confidence a decisive squeeze locks to |
| `SQUEEZE_FLOOR_SAT` | 0.30 | liq fraction that reaches 100% | ↑ for a gentler gradient (slower to 100%) |
| `VEL_SQUEEZE_LO/HI` | 1.30 / 3.00 | violence `vol_mult` | ↑ to require a faster bucket |
| `RECLAIM_LO/HI` | 0.00 / 0.30 | soft swept-then-reclaimed boost | ↑ to require a cleaner reclaim *(shared: DRAIN_OI)* |

### LIQUIDITY COIL — inside-bar compression absorbing forced flow
| constant | value | role | nudge |
|---|---|---|---|
| `RANGE_COMPRESS_LO/HI` | 0.60 / 1.20 | **⟲** compression `range/atr` | ↑ to call COIL on less compression |
| `LIQ_COIL_LO/HI` | 0.03 / 0.15 | liqs absorbed `liq_total/vol` | ↑LO to require more forced flow *(also suppresses CHOP)* |

### ROTATION / CHURN — OI-neutral breathing
| constant | value | role | nudge |
|---|---|---|---|
| `CHURN_LO/HI` | 0.55 / 0.90 | churn fraction core | ↑LO if ROTATION over-fires on normal two-sided buckets; ↓LO if it misses churn |
| `OI_NEUTRAL_LO/HI` | 0.05 / 0.35 | **⟲** low net OI | ↑ to tolerate more OI drift and still call rotation |

### CHOP — inside-bar + quiet
Falls out of inside-bar + quiet; tune via shared `VEL_LO/HI` (**⟲**), `DELTA` (**⟲**), and
`LIQ_COIL` (liqs present → suppressed → it's a COIL).

---

## Global / shaping

| constant | value | role | nudge |
|---|---|---|---|
| `SOFT_FLOOR` | 0.55 | how hard soft factors bite (they range `[SOFT_FLOOR,1]`) | **↓ if confidences feel uniformly high** (more spread); ↑ to flatten |
| `NEUTRAL_SCORE` | 0.20 | the bar every state must clear | ↑ → more NEUTRAL (states need more evidence); ↓ → fewer |
| `STAR_THRESHOLD` | 0.80 | ★ gate (score ≥ this) | ↑ → stars rarer |
| `SWEEP_WINDOW` | 10 | buckets back for sweep extremes + ATR | ↑ → "swept a level" means exceeding a longer-range high/low (rarer, stronger); smoother ATR |
| `ALPHA_MIN/SPAN` | 0.12 / 0.75 | opacity map (cosmetic) | ↑MIN = faint chips more visible; ↑SPAN = more low/high contrast |
| `PANEL_BG` | (10,12,16) | blend target for the chip tint (matches StatsOverlay bg) | leave unless the overlay bg changes |

---

## Debugging a wrong (starred) verdict — top priority

When a confident/starred verdict is wrong, capture: **the state + %, and the bucket's
rough numbers** — delta sign, OI direction, the Buyer/Seller E/R anomaly %, any liq,
green/red, and where POC sat. The fix is almost always one of:
1. **Raise the winner's core LO** (it needs more evidence to score that high), or
2. for a wrong **squeeze** star specifically, **raise `SQUEEZE_FLOOR_LIQ`**, or
3. the *right* state lost → loosen **its** core (↓LO) or tighten the wrong winner.

`bucket_state.state_scores()` returns **all** states' scores (not just the winner) — use
it (or the optional top-3 debug line) to see *why* X beat Y and how close the runner-up was.

After any constant change: re-run the suite. If a shift pushes a synthetic confidence
out of its band, **widen that band** — the live read is the source of truth; the test
only guards the winning state + a rough range.

---

## Calibration-day setup

```
python -m app.daemon      # terminal 1  (RESTART if one is already running)
python -m app.terminal    # terminal 2  → Mode 10, hover
```
- **Restart the daemon** — the A3b-pre liq feed lives in `feeds.py` (daemon side); a stale
  daemon won't feed liqs, so SQUEEZE/COIL stay inert even live.
- **SQUEEZE / COIL only fire on buckets formed *after* launch, and only when real
  SOLUSDT liquidations hit** (sporadic) — older buckets have no liq data. The other 7
  states read immediately on any bucket.
- **The forming bucket's verdict is noisy + faint early** (thin bucket → low confidence)
  and sharpens as it fills — that's the calibrated-confidence design. Judge **closed**
  buckets for stable verdicts; watch the **forming** one for the breathe.
