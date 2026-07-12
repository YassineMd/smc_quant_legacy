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

## Candidate refinement — forming-VP edge STAR (VPFADE, both sides)  ⚠ CANDIDATE / in-sample

**Added 2026-07-11 (scope-corrected same day). Pure-highlight overlay — it changes NO trades, so the frozen Steps 1–4,
the freeze, and the forward audit are all literally unchanged. It only *annotates* which cyan/orange D-entries sit at a
good forming-VP location.**

**SCOPE = the V3 D-ENTRY only: cyan/orange tier + Step-3 directional 4H zone (`step3`).** NOT green/hollow tiers, and
NOT Path-B New-E anchors (those are E-entries). A qualifying **D-entry** earns a **gold star ★** (good forming-VP bin)
or a **red ✕ trap** (the one bad bin). The forming VP = the D close vs the value area of the 4h bucket *being built* at
the D bar, reconstructed **causally** from the primary footprints since the last completed 4h close. Both value-area
**edges** (above-VAH, below-VAL) always star; the interior leans to the trade's own break:

- **BUY** ★ = **above VAH · upper VA · below VAL** · **✕ trap = lower VA**.  *(data-backed — see table.)*
- **SELL** ★ = **above VAH · below VAL · lower VA** · **✕ trap = upper VA**.  *(⚠ **NOT data-backed** — user's structural choice; the study says otherwise, see below.)*

Plus a **wait-CLOCK 🕐** on every cyan/orange D that **FAILS Step 3** (non-directional zone → it drops to Path B and
hunts a New-E). It answers "why does a strong-tier D not enter at the D": its 4H zone doesn't confirm its direction, so
it is an E-hunter, not a D-entry (see the Step 3 → Step 4 routing). It is normal and follows V3 exactly.

Nothing is faded, skipped, or re-priced — the marks are a visual guide only; the frozen detection is untouched.

Where it comes from (study `study/de_zone_effectiveness.py`, CAUSAL, in-sample ~13-day tape; **cyan+step3 D-entries**
split by forming-VP position — the corrected scope, cyan/orange tier ONLY):

| forming VP | BUY-D non-faded (n=24) | | SELL-D non-faded (n=28) | |
|------------|---|---|---|---|
| | n · net/tr | verdict (rule) | n · net/tr | verdict (rule) |
| above VAH | 5 · **+0.578%** | ★ (edge, data ✓) | 5 · **−0.108%** | ★ (edge) — **but data says WORST ✗** |
| upper VA  | 13 · +0.141% | ★ (data ✓) | 5 · +0.014% | **✕ trap** — **but data ~flat-positive ✗** |
| lower VA  | 3 · **−0.138%** | **✕ trap (data ✓)** | 7 · −0.018% | ★ (own-side interior) |
| below VAL | 3 · +0.148% | ★ (edge, data ✓) | 11 · **+0.285%** | ★ (edge, data ✓) |

Cohort nets: Buy-D non-faded **+0.198%/tr** (62% RR-win), Sell-D non-faded **+0.091%/tr** (50% RR-win).

**The BUY-D rule is data-backed** — all three star bins are net-positive and the lower-VA trap is net-negative; skipping
it lifts the Buy-D book **+0.198→~+0.246%/tr** (n 24→21). **The SELL-D rule is NOT data-backed** (recorded per the
user's explicit structural choice): the study's actual sell numbers make **above-VAH the worst** (−0.108%, which the
rule *stars*) and **upper-VA net-positive** (+0.014%, which the rule *traps*). The rule instead imposes a clean symmetry
— **both value-area edges star; the interior half leans to the trade's own break** (Buy keeps upper-VA / traps lower-VA;
Sell keeps lower-VA / traps upper-VA). **It needs a dedicated study to validate** (n per sell bin is 5–11, in-sample).
Terminal **only marks**, does not enforce. FADED (cyan, non-directional) confirmation: both sides net-negative (Buy-D
faded n=55 −0.048%/tr; Sell-D faded n=36 −0.110%/tr).

> **Scope-correction note (2026-07-11):** the FIRST cut of this study pooled ALL D tiers (cyan+green+hollow) and even
> folded Path-B New-E anchors into "non-faded" — WRONG (n=30 buy / 36 sell). V3 D-entries are cyan/orange-tier Step-3
> only. Re-run on that scope: cohorts shrank (buy 30→24, sell 36→28) but the star's traps/stars are the SAME buckets, so
> the rule stood. Reports `study/buy_d_report.py` + `study/sell_d_report.py` now filter `d_tier=="cyan" & d_step3`.

**Status: in-sample / POST-HOC** on the study's ZZTRAIL-at-D framing, shown as a decision aid only. A tradeable
forward-audit variant is **pending**. Terminal: toggle **`m10_vpfade`** (hamburger "D VP star/trap + clock (V3)", **ON
by default**) draws, on **`step3` D-entries only**, a gold **★** (good bin) or red **✕** (trap bin), plus a **🕐 clock**
on cyan/orange Path-B D's (non-directional). `_vpform_bin_at` mirrors the study `forming()/vp_bin()` — **parity proven**
(forming-VP bin identical on all 118 Buy-D + 95 Sell-D fires; 1 Sell opening-region None, never live). Scatter layers
`bc_pivot_stars` (gold star), `bc_pivot_traps` (red ✕), `bc_pivot_clocks` (custom clock symbol). Because the marks
remove no trades, frozen V3 is byte-for-byte the same whether the toggle is on or off. In-sample counts: Buy 21★/3✕ +
55🕐, Sell 23★/5✕ + 36🕐.

### E-entry VP star / trap  ⚠⚠ UNVALIDATED — NOT backed by data, study aid ONLY (added 2026-07-11)

The star/trap is also drawn on **every V3 E-entry** (Path-B New-E, **recorded OR faded**) as a study overlay. **E is
the MIRROR of the D**: it earns a **★** in its OWN value-half and a **red ✕ (trap)** in the opposite half —

| side | ★ star (own value-half) | ✕ trap (opposite half) |
|------|-------------------------|------------------------|
| **BUY-E**  | **lower VA · below VAL** | upper VA · above VAH |
| **SELL-E** | **above VAH · upper VA** | lower VA · below VAL |

**⚠ THIS IS NOT BACKED BY DATA — it is a HYPOTHESIS to study, not a validated rule.** Unlike the D-star (which sits on
net-positive cohorts), the E cohorts are **breakeven-to-negative** (pooled Buy-E −0.011%/tr, Sell-E −0.125%/tr; recorded
non-faded Buy-E n=6 / Sell-E n=8 are far too small; faded Buy-E −0.021%, Sell-E −0.110%). The ONLY genuinely positive E
pocket was **Buy-E lower-VA** (~+0.11%, n=13, in-sample). The own-half/opp-half split is the cleanest *structure*
observed (E likes the trade's own value-half, opp of the D-breakout; forming VP discriminates, filled VP does not) but
it **needs a dedicated study to validate before it means anything.** Terminal: separate toggle **`m10_estar`** ("E
VP-edge star/trap (V3, unvalidated)", ON by default); gold ★ = own-half, red ✕ = opposite-half; drawn on all E's;
**pure highlight, no trade change, frozen V3 untouched.** Study `study/e_report.py` → `{buy,sell}_e_{nonfaded,faded}.pdf`;
recs carry `e_tier/e_idx/e_zform/e_fill/e_mae/e_mfe`.

---

## Candidate refinement — VPIN confluence (adaptive threshold)  ⚠ CANDIDATE / in-sample

**Added 2026-07-12. The first clean EXTERNAL filter for V3 entries. A confluence/sizing layer — does NOT replace the
zones (they are complementary — see below). Study `study/de_vpin.py`.**

**The measure: VPIN ≥ its own adaptive WARN threshold (ratio ≥ 1.0).** At each entry bar, compare the trailing-50 VPIN
to its CAUSAL adaptive warn cutpoint (p75 of the trailing 240 VPIN, the same tier the terminal now displays after the
causal-tier fix). "Elevated" = VPIN at/above warn = a **yellow or red** VPIN bar. This normalisation matters:

- **Absolute VPIN is useless** — AUC ≈ 0.50, winner-VPIN ≈ loser-VPIN. There is no absolute line.
- **VPIN relative to its warn threshold is the best single VPIN measure** — AUC ≈ 0.62, with a clean **step at
  ratio 1.0** (win-rate jumps at the warn line, then flat above it — deeper toxic adds nothing). Regime-invariant.

**Zones and VPIN are COMPLEMENTARY — each roughly DOUBLES the other's per-trade edge (so do NOT drop the zones):**

| variant | n | W/BE/L | net/tr | win% |
|---------|---|--------|--------|------|
| **zones only** (V3 non-faded, no VPIN) | 66 | 31/12/23 | +0.126% | 57% |
| **VPIN only** (zones dropped, cyan D + all New-E, VPIN≥warn) | 62 | 26/11/25 | +0.121% | 51% |
| **zones AND VPIN** (V3 non-faded + VPIN≥warn) | 16 | 9/3/4 | **+0.283%** | **69%** |

Either filter alone turns the base into ~+0.12%/tr; together they hit **+0.283%/tr, 69% win** — only possible if their
edges are largely INDEPENDENT (VPIN is not re-capturing the zones). So the zone+VPIN entries are the **A+ setups**. The
trade-off is selectivity: both filters = 16 trades ($+452); either alone = ~62–66 trades ($+750–834). For total $, a
single filter banks more (more opportunities); for per-trade quality, the double filter wins big.

**VPIN-only W/BE/L (drop tier/zone/VP, gate on VPIN≥warn):** all D + all E = **72 trades, 28W/13BE/31L, +0.064%/tr,
47%**; the rejected side (VPIN<warn) is where the bleeding is (−0.074% D / −0.104% E). VPIN alone flips both books
positive — a legitimate alternative gate, but the zones still add ~+0.16%/tr on top.

**Terminal cue:** an **electric-purple ring** (`bc_pivot_vpin`) encircles **EVERY drawn D and E badge — faded or
non-faded** — whose VPIN is ≥ warn at that bar (ratio ≥ 1.0 = yellow/red VPIN bar). Toggle **`m10_vpinring`** (ON
default). Pure highlight; frozen V3 untouched; parity-checked (52 D + 20 E rings in-sample = every elevated-flow fire).
A ring on a **non-faded** (recorded) entry = the A+ confluence setup to size up; a ring on a **faded** D/E = a
VPIN-elevated setup the zones rejected (the study says VPIN-only still nets positive there, so it's watch-worthy).

**Status: in-sample / POST-HOC**, ~13-day one-regime tape; the zones+VPIN cell is n=16 (D carries it at n=14/+0.321%;
E is n=2). The *direction* is solid (both filters independently beat base and stack across all cohorts) but the exact
+0.283% is thin. Candidate for a forward-test of "V3 + VPIN confluence" beside the frozen V3.

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
- **2026-07-11** — Candidate refinement **VPFADE** added, then reworked from a FADE into a **STAR** (pure highlight,
  changes NO trades → frozen Steps 1–4 / freeze / audit byte-identical): a **golden ★** marks any NON-FADED **BUY** D
  whose CURRENT forming-VP MEETS the criteria (above-VAH / upper-VA / below-VAL — everything but the lower-VA trap,
  n=3, 33% W, −0.138%). Skipping the trap would be +0.163→+0.197/tr, but the terminal only marks, doesn't enforce;
  edges-only (+0.347, n=9) not adopted — user stars upper-VA too. Terminal toggle `m10_vpfade` (ON default),
  `_vpform_bin_at` parity-proven vs the study (0/118 Buy-D mismatch). Star = decision aid; a tradeable-variant audit
  is pending.
- **2026-07-11** — VPFADE star extended to the **SELL** side (exact mirror): a **golden ★** marks any NON-FADED Sell-D
  whose forming-VP is **not the above-VAH trap** (star below-VAL / lower-VA / upper-VA). Sell study (`de_zone_effectiveness`,
  n=36): below-VAL = strongest short pocket (64% RR-win, +0.285%), above-VAH = the trap (17% RR-win, −0.122%) — mirrors
  the Buy edge/trap. Star drawn above the sell coin (below the buy coin). Parity-proven: star decision identical on all
  95 Sell-D fires (1 opening-region None, never live). Report: `study/sell_d_report.py` → `sell_d_nonfaded.pdf`.
- **2026-07-11 (SCOPE FIX)** — the D-entry study had pooled ALL tiers (cyan+green+hollow) + Path-B anchors. **Corrected
  to the V3 D-entry scope: cyan/orange tier + Step-3 directional zone ONLY.** Cohorts re-cut (Buy non-faded 30→24, Sell
  28; Buy faded 55, Sell faded 36); recs now carry `d_tier`/`d_step3`; reports filter `d_tier=="cyan" & d_step3`. Traps
  & stars are the SAME buckets (Buy trap lower-VA, Sell trap above-VAH), so the rule held; numbers refreshed (Buy
  non-faded +0.198%/tr 62%RR; Sell +0.091%/tr 50%RR). **Terminal star tightened to `step3` D-entries only** (was
  `not faded` = also Path-B New-E anchors). Faded cohorts both net-negative. All 4 PDFs regenerated.
- **2026-07-11** — D star/trap rule **updated + trap ✕ and cyan-Path-B 🕐 clock icons added** (per user). Rule: both
  value-area edges (above-VAH/below-VAL) star; interior leans to the trade's own break — **Buy** ✕trap = lower-VA (star
  above-VAH/upper-VA/below-VAL) **[data-backed]**; **Sell** ✕trap = **upper-VA** (star above-VAH/below-VAL/lower-VA)
  **[NOT data-backed** — changed from the prior data-derived Sell trap = above-VAH; user's structural symmetry choice].
  Clock 🕐 marks cyan/orange D's that fail Step 3 (non-directional → Path-B E-hunter; normal per V3). Toggle `m10_vpfade`
  renamed "D VP star/trap + clock (V3)". New scatters `bc_pivot_traps`, `bc_pivot_clocks` (custom symbol). Pure highlight.
- **2026-07-11** — E-entry study run (Path-B New-E; `study/e_report.py`, 4 PDFs). Combo selection is the edge
  (non-faded Buy-E +0.069%/Sell +0.079% n=6/8; faded net-neg). No net-positive VP pocket except Buy-E lower-VA
  (~+0.11%, n=13). **E★/✕ overlay added — UNVALIDATED, per user, NOT data-backed:** ★ on an E's own value-half
  (Buy lower-VA/below-VAL, Sell above-VAH/upper-VA), red ✕ on the opposite half; drawn on ALL E's (faded+non-faded);
  toggle `m10_estar` (ON). Pure highlight. Needs a dedicated study to validate. Mirror of the D-star.
- **2026-07-12** — **VPIN adaptive-tier display FIXED to be causal** (`vpin_adaptive.vpin_tiers_from_series`): each bar
  tiered against ONLY its trailing 240-VPIN window, so colours FREEZE on close and never repaint (was: latest-window
  cutpoints applied to all bars → past reds turned gray as the day's distribution drifted). Both VPIN panes + hover +
  a causal stepped toxic line. Proven 0 repaint (vs 91 with the old approach).
- **2026-07-12** — **VPIN confluence filter recorded** (`study/de_vpin.py`, §"Candidate refinement — VPIN confluence").
  Measure = VPIN ≥ its adaptive warn threshold (ratio ≥ 1.0 = yellow/red bar); absolute VPIN useless (AUC 0.5), the
  ratio is the best measure (AUC 0.62, clean step at 1.0). **Zones & VPIN are COMPLEMENTARY** — each ~doubles the
  other: zones-only n=66 +0.126%, VPIN-only n=62 +0.121%, zones+VPIN n=16 **+0.283% 69%W**. So keep the zones; VPIN is
  a confluence/sizing layer. **Terminal: electric-purple ring** `bc_pivot_vpin` on **EVERY** drawn D/E badge (faded or
  non-faded) with VPIN≥warn, toggle `m10_vpinring` (ON), parity-checked (52 D + 20 E rings = all elevated-flow fires).
  In-sample, zones+VPIN cell n=16 — forward-test candidate.
