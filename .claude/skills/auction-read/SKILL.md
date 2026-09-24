---
name: auction-read
description: Read the live SOLUSDT auction from the terminal's AUCTION SNAPSHOT (today's HLH value, the multi-day value, the last ~80 flow cycles with interest / impact / push-back and their responsive / initiative labels) and describe, in the user's auction-market vocabulary, what buyers and sellers are doing NOW. Use when the user types /auction-read, asks "what is the auction doing", "read the market", "who is in control", or pastes an auction_snapshot.json.
---

# /auction-read -- describe the auction, now

A DESCRIPTIVE read of the market as it is. Never a prediction, never a trade call, never "buy / sell / enter /
target / stop". The user's research is in its study phase: say what the auction IS doing and what would CHANGE
the read, never what price WILL do.

## 1. Get the snapshot (read-only; nothing else)

Try in this order, and say which one you used and its `generated_utc` age:

1. **Pasted** -- if the user pasted JSON, use it.
2. **The PC terminal** -- `data/auction_snapshot.json` in this repo. Use it only if `generated_utc` is less than
   3 minutes old (the terminal writes it every 20 s while the Flow interpretation feed is showing and the HLH
   Volume Profile layer is on).
3. **The cloud tablet engine** (runs all day on the daemon VM):
   ```
   gcloud compute ssh smc-quant-eu --project=yass-chart --zone=europe-west9-b --command "cat /home/yassine_mdouari/smcflow/data/auction_snapshot.json"
   ```
   (Git Bash: filter the `Invalid characters in local username` warning out of the output.)

Read the file as UTF-8. **Do not** fetch anything else: no Binance downloads, no database scans, no other files
from the VM, no history pulls. If no snapshot is fresh, say so and stop -- the user must turn on the Flow feed +
HLH, or the engine must be running. A snapshot older than ~5 minutes is stale: say it plainly before reading it.

## 2. The doctrine -- the user's own terms

- The market is an **auction**. Buyers try to buy as cheap as possible, sellers to sell as expensive as possible.
  Together they build a **fair value** where both are content to trade -- what the **HLH Volume Profile** finds
  (its POC, its value area VAL..VAH, its D-blocs).
- Inside value: **sellers are expected to sell ABOVE the POC** (it is expensive there) while buyers lose interest;
  **buyers are expected to buy BELOW the POC** (it is cheap there) while sellers lose interest. That is
  **RESPONSIVE** activity -- it **defends** value.
- When a side stays **interested AND keeps its impact** where it is supposed to lose it -- **sellers below the
  POC, buyers above it** -- its idea of cheap / expensive has changed: what was cheap is now expensive to them (or
  the reverse). That is **INITIATIVE** activity -- value is being **re-priced**.
- Price outside value happens when most participants, or the big players, decide the current value is too
  expensive or too cheap. Whether the side that pushed it out keeps its interest and impact out there is the
  question that matters.
- That is why the terminal measures **interest** (aggressive $/s vs that side's own last N cycles) and **impact**
  (how far it moved price vs its usual reach, given the time, the dollars and the wall it met).

## 3. The fields

Top level: `live_price`, `tick` (0.01), `cycle_lookback_n` (the N every "x" ratio is against).

`value.today` -- the day's HLH profile as it stands (60 rows, 70% value area, POC = middle of the busiest run):
`poc`, `vah`, `val`, `minutes_of_profile`, `poc_60min_ago`, `poc_moved_ticks_last_hour` (value migrating?),
and `blocs`: the day's HLH D-blocs (name, poc, vah, val, from/to, `tag` MAX/MIN = the biggest/smallest by volume)
-- the day's STRUCTURE: where value was built and when.

`value.multi_day` -- the latest HLH bloc merged across 2+ FINISHED days (`poc`, `vah`, `val`, `days`,
`from_utc`..`to_utc`): the established multi-day value. May be null.

`summary` -- over the last `last_n` finished rated cycles, how often each label occurred per side; and
`time_share_pct_last_hour` -- % of the last hour's time spent in each zone of TODAY's value.

`cycles_oldest_first` -- one entry per flow cycle (a cycle = the buy-flow and sell-flow lines crossing; one
dominant side each). The last one may be `finished: false` (forming).
- `open/high/low/close`, `move_ticks`, `dur_s`
- `today_zone` / `multi_day_zone`: below value | lower value (VAL..POC) | at POC (within 1 tick) | upper value
  (POC..VAH) | above value -- located by the cycle's MID price against the value AS IT STOOD WHEN THE CYCLE STARTED
  (no hindsight). `ticks_from_today_poc`, `ticks_from_multi_day_poc`.
- `interest_buyers_x` / `interest_sellers_x`: each side's aggressive $/s vs its own last N. >= 1 = ACTIVE.
- `leader`: the side with more interest; `leader_impact`: its reach vs its usual ("short push" / "no push" = under
  4 ticks, no multiple); `leader_converted`: reach at or above its usual; `leader_reach_ticks`; `leader_kept`:
  share of the reach it held to the close (a % for a real push, else the signed ticks it held);
  `price_went_against_leader`.
- `other_side_push_back`: the non-leader's push back from the leader's extreme vs its own usual push-back, and
  the ticks (e.g. "2.1x (18t)").
- `wall_vs_normal_x`: resting liquidity in the leader's way vs normal (> 1.06 heavy, < 0.94 thin).
- `buyers` / `sellers` -- the side's label: `quiet` (under its normal and moved nothing),
  `responsive|initiative|at value` + `effective` (active and moved price its way) / `absorbed` (active, did not) /
  `passive` (moved price its way WITHOUT extra aggression -- resting orders took the push, or price gave way).
- `auction`: the cycle in one phrase (initiative effective = "value being re-priced", initiative absorbed =
  "value defended", "Contested: ..." = both sides moved price their way, leader first).
- `why`: the I x I pane's own sentence for the cycle.

## 4. How to read it

Work from the numbers, not the labels alone. Quote numbers (prices, ticks, x-ratios, counts, times in UTC).

1. **Where is price vs value?** `live_price` against today's POC / VAL / VAH (in ticks), which D-bloc it sits in,
   and against the multi-day value. How long has it been there (`time_share_pct_last_hour`, the zones of the last
   cycles)? Is today's POC migrating (`poc_moved_ticks_last_hour`, the blocs' times)?
2. **Who is doing what there?** Over the last ~12-20 cycles: which side is initiative vs responsive, effective vs
   absorbed vs passive. Name the sequences, e.g. "sellers initiative-effective in 4 of the last 6 cycles below
   VAL, buyers responsive but absorbed each time" or "buyers responsive-passive: their resting orders keep
   handing sellers' pushes back (18t, 11t, 12t)".
3. **Is the doctrine's expectation holding?** Below the POC: are sellers losing interest (quiet, absorbed) as
   expected -- value defended -- or staying interested and impactful -- value being re-priced? Above: the same
   for buyers. Contested cycles: who led, who pushed back, how much was kept.
4. **What changed?** Compare the most recent cycles with the ones before (the snapshot holds ~80): a shift from
   initiative to responsive, interest fading, impact flipping sides, pushes getting shorter, kept shares falling.
5. **What would change this read** -- stated as observable conditions, not forecasts: e.g. "if buyers turn
   responsive-effective below VAL for several cycles and sellers go quiet, the reading becomes 'value defended'".

Output format -- short, scannable, the user's words:

```
AUCTION READ -- <generated_utc> UTC (<age>s old, <source>)
Price <live_price>: <zone> today (<n>t from POC <poc>, VA <val>-<vah>), <zone> multi-day (<n>t from <poc>), in <bloc>.

WHERE: ...
WHO: buyers ... / sellers ...
DOCTRINE CHECK: ...
CHANGED: ...
WOULD CHANGE THE READ: ...
```

Keep it under ~250 words unless the user asks for more. Do not invent fields that are not in the snapshot; if
something the read needs is missing (no multi-day value, few rated cycles, a stale snapshot), say so.

## 5. Limits -- say them when they matter

- The labels are DESCRIPTIVE. Forward tests of reach, net and push-back as predictors came back null: nothing
  here says what price does next. Never turn the read into a direction call.
- "Initiative" / "responsive" come from the cycle's MID price vs the day's value at the cycle's start; a cycle
  straddling the POC can be labelled by a few ticks.
- The multi-day value only exists once HLH has merged blocs across finished days.
