# FORWARD LEDGER — Pivot V3 (sole active line)

**As of 2026-07-10, the ONLY strategy under forward test is PIVOT-V3.** Every prior config (the S5-series and the
PIVOT v1/v2 + zone-overlay line) is **RETIRED** — the entire pre-V3 PIVOT line was a look-ahead artifact (the tier
repainted off the settled/centered value), and V3 is the corrected rebuild on the frozen **first-print** tier basis.
The full pre-V3 table is preserved verbatim in `study/out/forward_ledger_archive_pre_v3.md`; nothing below re-tunes it.

Freeze discipline (unchanged): the active row is **NEVER re-tuned**. Each new snapshot re-runs it unchanged and
appends one dated line to the forward log; it graduates or dies on accumulated **forward** sample only.

---

## ACTIVE

| id | frozen | fires | entry | exit | fee | status |
|---|---|---|---|---|---|---|
| **PIVOT-V3** | 2026-07-10 · freeze_ts 1783623654 (Jul09 19:00 UTC) | **Step 1** S5j-r5 5-leg confluence (`app/pivot_detect.detect_pivots`; leg-2 LOCKED eff-agg spread ≥65), independent per-side walk. **Step 2** tier = **FROZEN first-print** (non-locked) aligned P2 spread @D: `>80` cyan/orange, `>63&≤80` red/green, `≤63` hollow (read once at the fire bar, never repaints) | **Path A (direct-D):** cyan/orange **AND** directional 4H zone → enter at D close (Buy@buy-area, Sell@sell-area, Buy@above-sell, Sell@below-buy). **Path B (New-E), all other D's:** first bar strictly after D (≤4h) where LOCKED P2 spread ≥15 **AND** HMS-favour (last 2 locked cycles, window 100 before D, noise<4) **AND** current-forming-HM favour; E=D skipped; one E per bar/side; **take only** the 6 side·Dzone→Ezone combos (4 any-tier + 2 cyan-only) | ZZTRAIL: no TP; SL 0.1% below last LL / above last HH; trail 0.05% beyond each new HL/LH; +0.4% MFE → lock +0.1% | taker/taker 0.10 | **in-sample (Jun28–Jul09, n=56): 29W / 10BE / 17L · +0.135%/tr · +$75 · t+2.06.** Path A n=43 (21W/9BE/13L, +$63, t1.85) = robust core; Path B n=13 (8W/1BE/4L, +$13, t0.87), the 6 E combos + tier/zone rules are **POST-HOC on this tape → forward is the only honest test.** Pre-declared: **PASS** fwd n≥40 & net>0 & t≥1.5; **FAIL** n≥40 & net≤0; **degrade-warn** n≥30 & net<+0.068. Freeze: `study/out/pivot_v3_freeze.json`; spec: `study/PIVOT_V3.md`; forward log: `study/out/pivot_v3_forward_log.md` (forward n=0 at freeze). |

---

## CANDIDATE — DA2-REVERSION v1.0 (independent MEAN-REVERSION line, registered 2026-07-21)

| id | frozen | fires | entry | exit | fee | status |
|---|---|---|---|---|---|---|
| **DA2-REVERSION-v1.0** | 2026-07-21 · freeze_ts 1784534492 (Jul20 08:01:32 UTC) · `study/out/da2_reversion_freeze.json` | **One condition.** Mature 1h volume buckets only (`i >= FM.build().first`, i.e. target_vol≥100k — the pre-2618 backfill burst is EXCLUDED). **da2 OPPOSED to the candle:** bearish candle (c<o) AND `da2>0` → **LONG**; bullish candle (c>o) AND `da2<0` → **SHORT**; doji never fires. `da2 = (buy_vol−sell_vol−2·delta_h1)/curr_vol`, delta_h1 = running delta at the bucket's **50%-VOLUME** mark (daemon field when present, else reconstructed from the 1m stream). NO skew / eff-agg / run_pos / POC — deliberately independent of the MMXSKEW family. | signal bucket's **close** | **FIXED percentages off entry** (NOT bucket-extreme-derived): stop **0.8%**, target **1.0%** (RR 1:1.25). Same-bar TP+SL → stop (conservative). Unresolved → dropped, never booked a loss. Non-overlap convention A (`i <= last` skipped). | taker/taker 0.08 | **in-sample n=123 · win 56.1% vs 48.9% break-even · +0.1298%/tr · +16.0% · maxDD 5.9% · t+1.60.** SPLIT-HALF **both positive** (H1 +0.064 / H2 +0.194); drop-best-5 still +0.096; permutation **p=0.0030**; MC P(profit) 93.9%, 95%CI [−0.031, +0.291] **straddles 0**. ⚠ **SHORT-CARRIED** (SHORT +0.230 n=60 vs LONG +0.034 n=63). ⚠ **SL/TP GRID-SELECTED on this tape** (~110 in-sample cells that session) — permutation p controls which *buckets* were picked, never how many *configurations* were tried; magnitude WILL regress. Registered because it is the ONLY cell from that session to survive split-half at both RRs — five other p<0.05 cells dissolved. Win rate rises monotonically as TP tightens (49.1%@1.2 → 78.8%@0.3, 7 cells ordered) but break-even rises faster; 1.0% sits mid-plateau of a positive 0.6–1.2% band. Pre-declared: **PASS** fwd n≥40 & net>0 & t≥1.5; **FAIL** n≥40 & net≤0; **degrade-warn** n≥25 & exp≤0. (n≥40 not 20: ~0.9% per-trade SD cannot be separated from zero at n=20.) Spec/validate: `study/da2_reversion_validate.py`; audit: `study/da2_reversion_forward_audit.py`; log: `study/out/da2_reversion_forward_log.md` (**forward n=0 at freeze**). **⚠ 1m DEPENDENCY:** the GCS archive carries no `delta_h1` (0/3843), so da2 is reconstructed from the 1m stream — forward scoring is limited to buckets with 1m coverage until the daemon's `delta_h1` reaches the archive. |

---

## CANDIDATE — PIVOT-V3-VPFADE (Buy-D VP-edge STAR — highlight only, does NOT change the ACTIVE row's trades)

| id | frozen | delta vs PIVOT-V3 | status |
|---|---|---|---|
| **PIVOT-V3-VPFADE** | 2026-07-11 | **Pure highlight — no trade removed, BOTH sides.** SCOPE = **V3 D-entries only: cyan/orange tier + Step-3 directional zone (`step3`)** — NOT green/hollow, NOT Path-B New-E anchors. A qualifying D-entry earns a **golden ★** when its **CURRENT forming 4h VP MEETS its side's criteria**: **BUY** ★ = above-VAH / upper-VA / below-VAL (avoid the **lower-VA** trap); **SELL** ★ = below-VAL / lower-VA / upper-VA (avoid the **above-VAH** trap) — exact mirror. Nothing faded/skipped; frozen V3 book byte-identical on/off. | **In-sample only** (study `de_zone_effectiveness.py`, cyan+step3 scope, ZZTRAIL-at-D). Cohorts: Buy-D non-faded n=24 **+0.198%/tr** (62% RR); Sell-D non-faded n=28 **+0.091%/tr** (50% RR). Star pockets: Buy above-VAH (80% RR, +0.578%), Sell below-VAL (64% RR, +0.285%); traps: Buy lower-VA (−0.138%), Sell above-VAH (−0.108%). Faded (cyan non-directional) both net-neg (Buy −0.048, Sell −0.110). Terminal overlay `m10_vpfade` LIVE (ON default), star on `step3` D-entries only; `_vpform_bin_at` **parity-proven** (forming-VP bin identical on all 118 Buy-D + 95 Sell-D fires). **Forward-audit variant PENDING.** Reports: `study/buy_d_report.py`, `study/sell_d_report.py`. *(First cut wrongly pooled all tiers + Path-B; corrected to cyan+step3 — same traps/stars, refreshed n.)* |

---

**Re-run recipe per new snapshot:** pull a fresh tape (`study/pull_snapshot.ps1`, read-only), then
**`python study/pivot_v3_forward_audit.py`** — reads `study/out/pivot_v3_freeze.json`, runs the frozen V3 rule
(`pivot_v3_de_zone_pdf.build_records` + the frozen 6-combo E TAKE set), splits every taken trade at `freeze_ts`
(entry end_time > freeze = forward), prints in-sample vs forward W/BE/L + net + t + a PASS/FAIL/CONTINUE verdict,
and APPENDS one dated row to `study/out/pivot_v3_forward_log.md` (append-only, never edited). NEVER re-tune — any
param change means a NEW freeze (bump `pivot_v3_freeze.json`'s freeze_ts, discard the prior forward log).

---

## RETIRED (pre-V3 — see `forward_ledger_archive_pre_v3.md`)

All superseded/invalidated 2026-07-10; kept for record only, none under active test:

- **S5E-SIGDEATH, S5H-CONDROUTER** — S5-series confluence lines; superseded by the pivot rebuild.
- **PIVOT-P2HELD, PIVOT-ABSORB-E, PIVOT-4HZONE, PIVOT-E2-TIER** — pivot entry-filter candidates on the old
  settled-tier basis.
- **PIVOT-ZZTRAIL, PIVOT-ZZTRAIL-v2, PIVOT-DTIER-ZONE, PIVOT-COMPOSITE-ZONE / -v2** — the v1/v2 working-strategy
  line + zone overlays. **Invalidated as look-ahead** (tier read off the settled/centered P2 value, which repaints);
  their frozen freezes (`pivot_freeze.json`, `pivot_freeze_v2.json`) and forward logs are dead. V3 replaces them.
