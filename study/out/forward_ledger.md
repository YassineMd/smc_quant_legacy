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
