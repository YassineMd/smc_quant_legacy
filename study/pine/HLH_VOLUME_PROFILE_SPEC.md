# HLH Volume Profile — D-areas, Blocs & Block Lines

**Handoff spec for porting the TradingView indicator into the SMC terminal.**
Reference implementation: `study/pine/hlh_volume_profile.pine` (Pine v6, ~1000 lines, never compiled
by the assistant — the user ran each version on TradingView and validated it visually).

This document explains **what** the indicator computes and **in which order**, step by step, in the
exact order it was built with the user. The order matters: every step consumes the state left by the
previous one (which rows are "used", which Ds are alive, which LOWs are red). Re-ordering steps changes
the output.

Where the user **corrected** an earlier version, the correction is called out (⚠ CORRECTION) — those
are the places a fresh implementation is most likely to get wrong.

---

## 0. Vocabulary

| Term | Meaning |
|---|---|
| **Period** | One Day (00:00 → 23:59) or one Week (Monday 00:00 → Sunday 23:59), in the session timezone. Each period is computed independently. |
| **Profile / VP** | Volume profile of the period: `rows` equal price rows between the period low and high. |
| **Row** | One price bucket of the profile. Row 0 = lowest price. "Width" of a row = its volume. |
| **POC** | Widest row. A run of equal max rows counts as one POC. |
| **LOW** | A row (or run of equal rows) with a **bigger** row on both sides — High-Low-High. |
| **HIGH** | A row (or run of equal rows) with a **smaller** row on both sides — Low-High-Low. |
| **D** | A shape: an **apex** (POC for D1, a HIGH for the others) with an **upper leg** and a **lower leg**, each ending on a LOW (or on a fallback "lowest point"). |
| **Used rows** | Rows consumed by a D. LOWs/HIGHs on used rows are faded and ignored by all later Ds. |
| **D area** | The price band between a D's two level lines (§8). Each D area is isolated from the others. |
| **Bloc** | A run of consecutive time bins during which price was inside a D area (§9). |
| **Block Lines** | The VAH / VAL of one bloc, spanning the bloc's time only (§11). |

---

## 1. Build the volume profile (per period)

1. Collect the period's candles at **intrabar resolution** (Day: 1-minute, Week: 5-minute by default).
   Keep per candle: high, low, close, volume, open time, length in minutes.
   - Candles with zero/NA volume are skipped.
   - If intrabar data is unavailable for a chart bar, the chart bar itself is used (a known mixing quirk —
     the user was told; fine to use pure 1m data in the terminal).
2. `lo` = min low, `hi` = max high of the period, `step = (hi − lo) / rows` (default `rows = 60`).
3. Each candle's volume is spread **evenly across every row its high–low range touches**
   (`iB = floor((low−lo)/step)`, `iT = min(rows−1, floor((high−lo)/step))`, each row gets `vol / (iT−iB+1)`).
4. `maxVol` = POC volume. POC = first max row `pS`, extended upward over equal rows to `pE`.

Total volume only (no buy/sell split). Row height varies per period (range / 60).

---

## 2. Step 1 — Mark LOWs and HIGHs

Scan rows `r = 1 … rows−2`. Group equal consecutive rows `[r, e]` into one bar.
With `below = vp[r−1]`, `above = vp[e+1]`, `v = vp[r]`:

- **LOW**  if `below > v` and `above > v`
- **HIGH** if `below < v` and `above < v`

The first and last row can never be a LOW/HIGH (only one neighbour).

## 3. Step 2 — Filter

- Keep a LOW only if `v < 50 % × POC` (`lowMaxPct`).
- Keep a HIGH only if `v > 50 % × POC` (`highMinPct`).

**From here on only the filtered LOWs and HIGHs exist.**

---

## 4. Step 3 — D1 (the POC D)

Two legs leave the POC's right edge: one up (start at row `pE+1`), one down (start at row `pS−1`).
The **leg walk** (`walkLeg`) is identical for every D:

1. Walk away from the apex row by row.
2. The **closest LOW is ALWAYS connected first**, even if a HIGH lies between the apex and it.
3. After that, keep extending to the next LOW **only if it is LOWER (smaller volume)** than the last
   connected LOW. A LOW that is equal or higher **stops** the leg.
4. **Once at least one LOW is connected, a HIGH stops the leg** (nothing beyond it is connected).
5. A leg never enters a row already used by a previous D (not relevant for D1).
6. The leg ends at the **last connected LOW**.

**No LOW on a side** → the leg goes to the **lowest point** on that side: the thinnest row between the
apex and the profile edge (or the first used row). Ties → the one farthest from the apex.

Drawing: straight line from the apex (POC right edge, POC vertical middle) to the end LOW (its right edge,
its vertical middle). The LOWs a leg walks through are numbered 1, 2, 3… (closest first).

> ⚠ CORRECTION 1 — the first version stopped at a HIGH *before* connecting any LOW and claimed the whole
> side. The user's rule: the POC **must** connect to the closest LOW; the HIGH-interruption rule only
> applies **after** that first LOW.
>
> ⚠ CORRECTION 2 — "no LOW" does not mean "no leg": it connects to the lowest point available.
>
> Equal-volume LOW counts as NOT lower → stops the leg (assistant's choice, not contested).

## 5. Step 4 — What D1 has used up

Used rows = every row **strictly between** D1's two end points:
- top = (upper end LOW's first row − 1), or (fallback row − 1), or the profile top if no leg;
- bottom = (lower end LOW's last row + 1), or (fallback row + 1), or row 0.

The **end LOWs themselves are NOT used** (they stay full strength and can be shared with the next D).
LOWs and HIGHs on used rows are drawn faded (transparency 80) and are **never considered again**.

---

## 6. Step 5 — D2, D3, D4 … one at a time (loop)

Repeat until no candidate HIGH is left (optional cap `maxDs`):

1. **Apex** = the widest HIGH that is **not on a used row** and is not the POC.
   Tie → the first found (lower price).
2. Build its two legs with the **same walk** as D1 (upper leg from the HIGH's last row + 1, lower leg from
   its first row − 1). Legs **stop at used rows** → Ds never overlap.
3. Fallback lowest point, same as D1, bounded by used rows.
4. Mark used: every row between its end points (end LOWs excluded) — **and the apex rows themselves**
   (so the loop always terminates). The apex HIGH is drawn at full strength, not faded.
5. Look again for the next widest unused HIGH.

> ⚠ The user insisted: **step by step** — build ONE D, mark its rows used, THEN pick the next apex.
> Picking all apexes first gives a different (wrong) result.

Colours: D1 pink, D2 cyan, D3 purple, D4 green, D5 orange, then cycle.

---

## 7. Step 6 — Red LOWs and merging

### 7a. Red LOW (colouring)
A LOW is **red** when:
- it is the **end LOW of 2 Ds** (shared: it is the lower end of the D above it and the upper end of the
  D below it), **and**
- its width is **> 66 %** of **at least one** of those two Ds' apex widths (POC for D1, HIGH otherwise)
  → compare against the thinner apex.

> ⚠ CORRECTION — first version: > 50 % and "more opaque". User changed to **66 %** and **red colour**.
> Strict `>` is used.

### 7b. Merge (after ALL Ds are built)
For each red LOW, with X = the D above (red LOW is X's lower end) and Y = the D below (red LOW is Y's
upper end):
- If `X.apex ≥ Y.apex`: X keeps its apex and upper leg; **X's lower leg is replaced by Y's lower leg**
  (connects to Y's lower LOW). Y dies.
- Else: Y keeps its apex and lower leg; **Y's upper leg is replaced by X's upper leg**. X dies.
- The losing apex HIGH and the red LOW become **inside** the merged D → faded (red LOW rows marked used;
  losing apex no longer flagged as an apex).
- Merged name: `"D2+D4"` (winner first). Winner's colour.
- **Repeat** until no mergeable red LOW remains (a merge can create a new red LOW with a third D).

D1's apex is the POC, so D1 always wins its merges.

---

## 8. Step 7 — Uncovered areas

**Covered rows** = for every alive D, every row from its lower end to its upper end **inclusive**
(end LOWs, apex, interior), plus all used rows.

Each run of rows NOT covered = one **uncovered area**. For each:
1. Its LOWs are **not coloured** at all (removed from the display).
2. Its **widest Low-High-Low bar** (any width — NOT restricted to the 50 % filter) is coloured **purple**.
   Only the single widest one per area.

> ⚠ CORRECTION — the user briefly asked for *all* HIGHs purple, then reverted: **only the highest one**.

## 9. Step 8 — D from each purple HIGH ("U1, U2 …")

For each purple HIGH, build legs by walking **through the uncovered rows** (their LOWs were removed):
- Each leg attaches to the **first EXISTING LOW** it reaches when it hits a covered row
  (normally the end LOW of the neighbouring D).
- If it reaches the profile edge, or a covered row that is not a LOW → fallback to the lowest point of the
  uncovered rows on that side.
- Drawn purple, labelled `U1, U2 …`. It does not change the covered set.

Red LOW rule (§7a) applies here too: a LOW shared by a U-D and a D, wider than 66 % of either apex → red.

> ⚠ The user explicitly asked: **first just colour, don't merge** — then, as the next step, apply merging.

## 10. Step 9 — Merge again, now including the U-Ds

Run the **same merge loop** (§7b) over all Ds + U-Ds. In practice a regular D always wins (a purple HIGH is
< 50 % of POC, otherwise it would have been a filtered HIGH). A losing purple HIGH is faded. Names like
`"D2+U1"`.

---

## 11. D levels (the "D area" lines)

For every **final alive** D (including merged and U-Ds), two horizontal lines from the period start to the
period end (23:59 / Sunday 23:59), D's colour, width 3:

- **Upper end** (the LOW above the apex) → line at that LOW's **UPPER extreme** (top edge of its top row).
- **Lower end** (the LOW below the apex) → line at that LOW's **LOWER extreme** (bottom edge).
- Fallback lowest-point ends use the same top/bottom rule.

So a **shared LOW gets two lines**: the D above marks its bottom edge, the D below marks its top edge.

> The user drew this on a screenshot: D2's below-HIGH LOW → line at its lower extreme; D1's above-POC LOW
> (shared) → line at its upper extreme; D2's upper end at the very top of the VP → its upper extreme.

**The band between the two lines is the D area.** Each D area is unique and isolated.

---

## 12. Time profile per D area

For each D area, an independent **horizontal time profile** over the period:

- Time bins from period start (Day 00:00 / Week Monday 00:00): Day 30 min, Week 240 min (configurable).
- A candle counts for a D area **only if its CLOSE is inside the area** (`yBot ≤ close ≤ yTop`,
  inclusive — a close exactly on a shared LOW counts for both Ds).
- Per bin: minutes = sum of those candles' lengths; volume = sum of their **whole** volumes.
- Drawn as columns standing on the area's bottom line; the busiest bin reaches the top line; each D
  scaled to its **own** max.

> ⚠ CORRECTION — first version counted a candle whose range merely *touched* the area and split volume by
> overlap. User: **"the candle needs to close inside the D"**. Close-only is now the only rule.

## 13. Blocs

- **Bloc** = a run of consecutive non-empty time bins in one D area. An empty bin ends it (so bloc splitting
  depends on bin size).
- **Bloc time** = minutes actually in the area (sum of counted candles), NOT wall-clock span.
- **Bloc volume** = volume of those candles.
- Label per bloc: `B1 / 58min / 312K` (duration format: `58min`, `1h34m`, `1d2h30m`).
- Area total label: `D1 area: 3h33m | vol 1.42M | 3 blocs`.

## 14. MAX / MIN bloc comparison

Compare **all blocs of the period across all Ds**:
- **MAX** = the bloc with the most time AND most volume.
- **MIN** = the bloc with the least time AND least volume.
- These two stay normal opacity; **every other bloc is faded** (transparency 90).
- If most-time and most-volume are different blocs: setting "Keep both" (default, tags `MAX time` /
  `MAX vol`) or "Keep neither". Same for MIN. (Assistant's choice — user did not contest.)
- One bloc only → it is both.

## 15. Block Lines — VAH / VAL per bloc

> ⚠ CORRECTION — first attempt computed VAH/VAL for the **whole D area** over the full day. The user
> rejected it: **each bloc has its own VAH/VAL, starting where the bloc starts and ending where it ends.**

For each bloc:
1. Build a mini volume profile from **only that bloc's candles** (open time within the bloc's bins AND close
   inside the D area), on the period's rows, restricted to the area's rows. Each candle's volume is spread
   evenly over the area rows its range touches.
2. Standard **70 % value area**: start at the bloc's busiest row, repeatedly add the bigger neighbouring row
   (above vs below; tie → above) until ≥ 70 % of the bloc's volume.
3. VAH = top edge of the highest VA row; VAL = bottom edge of the lowest VA row.
4. Draw two **solid** lines (user changed dashed → solid) from bloc start to bloc end, in the D's colour;
   MAX/MIN blocs full colour, other blocs lighter.

---

## 16. Display modes & toggles (final state)

| Toggle | Behaviour |
|---|---|
| **Day profile** | on/off, 00:00 → 23:59. |
| **Week profile** | on/off, Monday 00:00 → Sunday 23:59. Day and Week run fully independently; week labels prefixed `W `. |
| **Block Lines Only** | Hides EVERYTHING except the bloc VAH/VAL lines **and their bloc labels**. Lines drawn **2 px** in this mode. Bloc label text is `small` (user asked bigger). All computation unchanged. |

Other settings: rows 60, low/high filter 50 %, shared-LOW 66 %, merge on/off, uncovered areas on/off,
value area 70 %, time-bin sizes, colours.

---

## 17. Stability requirement (user was explicit)

> "When the chart loads the indicator should print over all available data and STAY even if I zoom/pan.
> Old days should stay fixed after the first time — no recompute."

- A **finished** period is computed **once**, when it closes, and never recomputed.
- Only the **forming** period updates live.
- Never tie computation to the visible range (the Pine version originally did, causing vanish/repaint).
- In the terminal: cache results per period key (day `yyyymmdd`, week = Monday's `yyyymmdd`, computed from
  noon so DST can't shift the date).

## 18. Pine-only artefacts — do NOT port

- Drawing-count caps (100 polylines / 500 boxes/lines/labels), `pruneOld`, "Days/Weeks to draw" budgets.
- `request.security_lower_tf` history limits and the chart-bar fallback — use real 1m (or finer) data.
- `dev` vs finished drawing arrays (just "forming period = recompute on update").

## 19. Open points never decided by the user (flag, don't invent)

- Equal-volume LOW during the leg walk treated as "not lower" (stops).
- Two unused HIGHs of equal width → lower-price one first.
- Red-LOW threshold strict `>` 66 %.
- Shared LOW's price band counts for both adjacent D areas (time + close tests).
- "Keep both" when MAX-time ≠ MAX-volume.
- Value area tie-break → expand upward.
