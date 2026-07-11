# PIVOT V3 — strategy record

**Status:** live spec, built incrementally. Created 2026-07-09.
**Rule:** this document is only updated when explicitly instructed. Steps are added as tests validate them.

Pivot V3 currently has **two steps**. Nothing else is decided yet — we are re-running the whole test suite on this
common, locked basis and will append steps as we go.

---

## Step 1 — Find the D (all 5 legs must fire)

The frozen **S5j-r5** confluence. Per bar `b`, for a **LONG** (short mirrors every condition). A D exists only
when **all five are true at the same bar**:

| Leg | Panel | Condition (LONG) |
|-----|-------|------------------|
| **Leg 1″** | P0 (composite lean) | On the 100-bar window `[b−99, b]`, the **two most-recent LOCKED P0 crosses are both UP**, and the newest is the **+50 EXTREME** cross. (Settling/unconfirmed dots never count.) |
| **Leg 2** | P2 (eff-agg) | **LOCKED** eff-agg badge spread `(2·e_sh − 1)·100 ≥ 65`. |
| **Leg 3** | P6 (phase) | Dominant phase is **START/DURING on the buy table AND NOT START/DURING on the sell table** (two-sided). |
| **Leg 4** | P6 (phase) | **LOCKED** P6 phase spread `up − down ≥ 15`. |
| **Leg 5** | range context | `close < max(open)` over the zone `[b−99, b−59]` (some N in **60–100** bars back) — a pullback from underneath, not a chase. |

**Fire = all five AND-ed.** Source of truth: `app/pivot_detect.py` (`detect_pivots`).

---

## Step 2 — Categorize the D into its tier  🔒 LOCKED DEFINITION

At the **exact bar the D fires**, go to P2. Leg 2 already guarantees the **LOCKED** spread is `≥ 65` (that's what
fired it). At that same instant, read the **NON-LOCKED (first-print) aligned P2 spread — once — and freeze it.**
The tier **never** recomputes afterward.

| Tier | Non-locked (first-print) aligned spread @ D |
|------|---------------------------------------------|
| **cyan/orange** | `> 80` |
| **red/green** | `> 63` and `≤ 80` |
| **hollow** | `≤ 63` |

### What "non-locked (first-print)" means — and why it's the only one we use
- It's the **left-clamped** rolling share `[det−7 … det]` — it uses **only data up to the fire bar**, so it is the
  value the P2 line actually reads the instant the D prints, and it **cannot repaint**.
- It is **not** the centered/settled value (`e_sh[det]`), which drifts as the forward buckets fill in — that one
  changes the tier as you scrub, which is the bug we removed.
- It is **not** the leg-2 **locked** value either: leg 2's locked `≥65` *fires* the D; the tier is read off the
  separate **non-locked** value.
- Implementation: `pivot_detect.eff_causal_share()` → `detect_pivots(return_eff=True)` returns `e_sh_c`; the
  terminal reads the tier (and E-held / E2 / the v2 fade / the hover) off `e_sh_c`, frozen.

**Every Pivot V3 test uses this frozen first-print tier as the common basis.** Any older result that tiered on the
settled value (e.g. the original `pivot_entry_timing` decision) is **not** on the V3 basis and must be re-run.

---

## Step 3 — Entry (D-entry: cyan/orange + directional 4H-zone)

Enter **at the D bar close**, but **only** when **both** hold:

1. **Tier = cyan/orange** (first-print spread `> 80`). Red/green and hollow are skipped entirely.
2. The D's **position in the last completed non-merged 4H candle CONFIRMS its direction** — one of:

| Take | Position | Meaning |
|------|----------|---------|
| **Buy D** | in the **buy area** (inzone-buy, 4H demand wick `low…vq_lo`) | long bouncing off support |
| **Sell D** | in the **sell area** (inzone-sell, 4H supply wick `vq_hi…high`) | short rejecting resistance |
| **Buy D** | **above sell area** (beyond-up, `px > 4H high`) | long breaking out above the 4H high |
| **Sell D** | **below buy area** (beyond-down, `px < 4H low`) | short breaking down below the 4H low |

**Everything else is skipped** — any non-cyan tier, and any cyan D whose location contradicts its direction
(Buy D in sell area / body / below buy area; Sell D in buy area / body / above sell area).

> Note: **body is deliberately excluded.** Buy@body was the largest in-sample pocket (+$44) but it favours longs
> (Buy@body +$29 vs Sell@body −$8) — a likely mild up-trend artifact, so it's left out in favour of clean
> directional confirmation. In-sample basis (Test 3, `study/pivot_v3_d_zone_pdf.py`), CAUSAL: **n=40 · W 50% /
> BE 20% / L 30% · +0.153%/trade · +$61 · t=+1.82** (the strongest causal signal in V3 so far, but in-sample and
> <2σ — forward tape decides). Workhorses = Buy@buy-area (n=18) + Sell@sell-area (n=13); the two breakout cells are
> thin (n=2, n=7). Exit = the **Default exit** below.

---

## Step 4 — E entry, for the OTHER (non-Step-3) D's  ⚠ UNDER TEST (not finalised)

D's that do NOT qualify for Step 3 (non-cyan, or cyan in a non-directional zone) are **not** entered at D. They
instead wait for the **NEW E** — the first bar **strictly after the D** (within 4h) where all three hold:
1. aligned **LOCKED** P2 eff-agg spread (settled badge, LOCK buckets back) **≥ 15**
2. **HMS in favour** — net side of the last **2 LOCKED cycles** (window 100 before D, noise <4 merged) agrees
3. the **current (forming) HM cycle** is also in favour (causal first-print net side of the cycle at the bar)

**🔒 Skip rule — if E = D, SKIP.** If the confluence is already true **on the D bar** (the first qualifying bar is
the D itself), the setup is **filtered out — no trade.** A same-candle E is a direct-D-like entry on a
non-directional D, which loses; only take E's that **develop strictly after the D**.
**🔒 Dedup — one E per bar per side.** If several D's converge on the same E bar, keep the first, drop the rest.

### E-entry SELECTION — take the E ONLY on these `side · D-zone → E-zone` combos:

**Any tier (the 4 that carry it):**
| Take | D zone | → | E zone |
|------|--------|---|--------|
| Buy  | buy area | → | body |
| Sell | sell area | → | body |
| Buy  | below buy area | → | buy area |
| Sell | above sell area | → | sell area |

**cyan/orange ONLY (weak — forward-only):**
| Take | D zone | → | E zone | note |
|------|--------|---|--------|------|
| Buy  | body | → | sell area | +$2, n=1 (marginal) |
| Sell | body | → | buy area | **−$5, n=2 (in-sample loser)** |

Entry = the E bar's close. Exit = the **Default exit** below.

Status: **under test / POST-HOC.** These 6 combos were selected from the in-sample migration table → **forward-only.**
The 4 any-tier combos carry the edge (E book +$20, t1.67, 78% W); the 2 cyan combos are marginal/negative (kept per
mandate, drop first on forward review). In-sample combined book (Step-3 direct-D + all 6 E's) = **+$78, t+2.15**
(vs Step-3-only +$61, t+1.82). Study: `study/pivot_v3_de_zone_pdf.py`.

---

## Exit — PER PATH  🔒 (Path A = D-EXIT · Path B = fixed bracket)

The exit is **different for each entry path** (recorded 2026-07-10, replacing the old ZZTRAIL default):

### Path A (direct-D) exit — **D-EXIT**

- **No fixed TP.** The **take profit is a signal**: the trade closes at that bar's close the moment an
  **OPPOSITE-side D prints** (a Sell-D closes a long, a Buy-D closes a short). Riding to the opposite-D is the edge.
- **Initial SL is FIXED (no auto-trail):** `0.1%` below the last **confirmed swing low** (long) / above the last
  **confirmed swing high** (short) known at entry — HL *or* LL / HH *or* LH, ZigZag 0.20%, confirm-bar ≤ entry.
- **Trail ONLY on a same-side D print:** when a **SAME-side D** prints, ratchet the stop to `0.1%` below the last
  confirmed swing low (long) / above the last confirmed swing high (short). Tighten only, never loosens. Between
  D-prints the stop stays put. **No** per-swing ZigZag trail, **no** +0.4%→+0.1% breakeven lock.
- **D-print timeline** = the scan-gated D's the terminal draws (one per side, sequential), both sides.
- **Fees:** `0.10%` taker/taker, netted per trade. Three-outcome on NET (winner > +0.05% / breakeven |·| ≤ 0.05% / loser < −0.05%).
- **Causal only:** the opposite-D / same-D events are live detections; the stop reads only confirmed swings. Only
  the price walk between events is forward (the trade's result, not a decision).

### Path B (New-E) exit — **FIXED BRACKET  🔒 SL 0.2% / TP 0.6%**

Path B does **not** ride to the opposite-D. Each New-E entry is a pure fixed bracket:
- **Stop loss:** flat **`0.2%`** below entry (long) / above entry (short) — a *flat* stop from the entry price, **not** the structural swing stop.
- **Take profit:** flat **`+0.6%`** from entry.
- No trail, no opposite-D, no breakeven lock. Whichever level price reaches first ends the trade; SL checked first intrabar. Fee `0.10%`.

> Why 0.2%/0.6%: the E entries are quick, weaker signals — scalp them, don't ride. The **0.2% stop beats 0.1%**
> (the tight stop whipsaws: 6/13 stop-outs → 4/13) and the **0.6% target is the MFE plateau** — 69% of trades reach
> both 0.5% *and* 0.6% (no winners lost widening 0.5→0.6), then reach-rate falls off (54% at 0.75%, 0% at 1.5%);
> avg MAX favourable 0.74%, runners avg ~1.0%. TP 0.6% is the peak of the wide-TP sweep. All 6 combos kept (the
> Sell body→buy loser is n=2, its mirror Buy body→sell works — data-starved, not dropped; a structure/trend filter
> is a future test). Study: `study/pivot_v3_dexit.py`.

> **Combined in-sample (causal):** Path A on D-EXIT + Path B on SL0.2/TP0.6 = **n=56 · +$163 · t+3.37** (Path A
> +$130/t2.79 ride-to-opp-D; **Path B +$33/t2.38** fixed bracket) — vs the old both-paths-ride +$134/t2.55 and the
> ZZTRAIL +$75/t2.06. Post-hoc / in-sample on a ~10-day bull tape → forward tape is the only test.

---

## Change log
- **2026-07-09** — V3 created. Steps 1–2 recorded. D-tier definition **locked** to the non-locked/first-print
  frozen value (`>80` / `>63…≤80` / `≤63`).
- **2026-07-09** — Default exit recorded (frozen ZZTRAIL: no TP, 0.1% LL/HH stop, 0.05% HL/LH trail, +0.4%→+0.1%
  lock). All V3 tests are CAUSAL ONLY.
- **2026-07-09** — Step 3 (entry) recorded: enter at D only if cyan/orange AND directionally-confirmed 4H-zone
  position (Buy@buy-area, Sell@sell-area, Buy@above-sell, Sell@below-buy). Body excluded (long-bias). HMS filter
  NOT used — too sparse at D to combine with zone.
- **2026-07-09** — Step 4 (E entry for non-Step-3 D's) added UNDER TEST: LOCKED spread ≥15 + HMS favour + current
  HM favour, strictly after D. **Skip rule locked: if E=D (same candle), skip the setup** (raised combined book
  +$25 → +$48). E definition still being tuned.
- **2026-07-10** — Step 4 E-entry SELECTION recorded (6 `side · D-zone → E-zone` combos, POST-HOC/forward-only):
  4 any-tier + 2 cyan-only. Dedup rule locked. Combined V3 = +$78, t+2.15. The 2 cyan combos are marginal/negative
  (Sell body→buy = −$5), kept per mandate. Terminal fades everything outside the recorded D + E entries.
- **2026-07-10** — **Exit changed: ZZTRAIL → D-EXIT** for BOTH paths (fixed structural stop + take-profit-on-
  opposite-D + trail-only-on-same-D; no fixed TP, no breakeven lock). In-sample Combined +$134/t2.55 (Path A +$130/
  t2.79; Path B +$4, weak). Breakeven-lock swept and rejected (can't rescue the adverse stop-outs). Terminal entry
  overlay now simulates the D-EXIT (opposite-D TP + fixed stop + same-D ratchets + a TP@opp-D/stop tag). **Path B
  entry is being reworked (user)** — its edge under this exit is pending that fix. **Forward-audit freeze to be
  re-cut once Path B lands** (freeze/ledger still show the ZZTRAIL baseline until then). Study: `study/pivot_v3_dexit.py`.
- **2026-07-10** — **Exit is now PER-PATH. Path B locked to a FIXED BRACKET: SL 0.2% / TP 0.6%** (flat from entry,
  SL-first, no trail/opp-D/lock). Chosen from the SL×TP sweep (0.2% stop beats 0.1% whipsaw; 0.6% TP = the MFE
  plateau, 69% reach 0.5% AND 0.6%). Path A stays D-EXIT (ride opp-D). Combined **+$163, t+3.37** (Path A +$130 +
  Path B +$33). All 6 E combos KEPT (Sell body→buy not dropped — n=2, mirror of Buy body→sell; structure check
  mixed). Terminal: per-path overlay, Path B TP drawn as a light-blue no-border zone. Freeze re-cut still pending.
