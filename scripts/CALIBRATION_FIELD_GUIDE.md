# Mode 10 — State-Engine Calibration Field Guide

Tuning `app/bucket_state.py` against live buckets. **Setup:** restart the daemon (fresh liq feed →
SQUEEZE/COIL can fire), Mode 10, **State Debug** toggle ON. **Loop:** spot a verdict that disagrees
with your read → paste **STATE + DBG + why** → map it to one constant → nudge (constants only) →
re-watch. Knob map: [`STATE_ENGINE_TUNING.md`](STATE_ENGINE_TUNING.md).

## 1. Reading the debug (one glance)
- **STATE** — the verdict chip. `★` = ≥80% (confident).
- **DBG** — top-3 states + scores. The **gap #1→#2** is decisiveness: big gap = confident; near-tie = a coin-flip between two reads (note which one your eyes pick).
- **why** — the winner's factors, each `0.00–1.00` (higher = more satisfied). The **[bracketed/bold]** one is the **binding** (lowest) factor = the lever: raising its `LO` makes the verdict **pickier** (score drops). Names → constants via the tuning sheet.
- **⌊floor⌋** — a squeeze whose **gradient floor** set the score (not the geomean) → the knob is **`SQUEEZE_FLOOR_LIQ`** (floored too easily).

## 2. What to flag (priority)
1. **Confident-WRONG first** — `★`/80%+ verdicts that disagree with your read. A confident lie is the dangerous one; fix these before anything else.
2. **Then mid-confidence** (≈40–80%) where **DBG #2** matches your read better than #1 — a near-tie the constants resolved the wrong way.
3. **Ignore faint/low-conf** (≲35%) — the engine is honestly unsure; that's working as designed, not a bug. Don't tune these.

## 3. The ONE question per state (right vs wrong, by eye)
| state | ask yourself |
|---|---|
| STRONG BULL / BEAR | Did price actually **break and hold** on **fresh** OI opening (clean push) — or was it churny / absorbed? |
| BULL / BEAR EXHAUSTION | Is the move **stalling / being absorbed** (effort not translating, OI draining) — or still running clean (→ STRONG)? |
| BULL / BEAR TRAP | Did the aggressor actually **FAIL** (poke the level, close back through) — or was it a **real** break that just paused? |
| SHORT / LONG SQUEEZE | Were positions actually **forced out** (liqs + OI dropping on a violent move) — or did I just see fast buying / selling? |
| LIQUIDITY COIL | Tight **inside-bar absorbing forced flow** (coiling) — or just a quiet bar? |
| ROTATION | Two-sided **churn, no net OI** (rotating, not committing) — or did one side actually win? |
| CHOP | Genuinely **quiet & directionless** (low signal, correct) — or did it miss a real move? |
| NEUTRAL | Honestly ambiguous — or did it **miss** an obvious read (a state's `LO` set too high)? |

## 4. When to stop (a state is "trusted")
- Its **confident** verdicts (`★`/80%+) match your read across **different regimes** — a trend, a chop stretch, and at least one real squeeze/trap. One bucket isn't calibration.
- You **stop being surprised** by its confident calls. Faint calls needn't be perfect — only the confident ones must be honest.
- **Don't over-tune.** A nudge that fixes one bucket but breaks the synthetic suite → widen the test band only if the live read is truly right (per the tuning sheet). Move on once a state is trustworthy; chasing every faint wobble is how you overfit.
