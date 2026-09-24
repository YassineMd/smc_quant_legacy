---
name: auction-read
description: Read the live SOLUSDT auction from the terminal's AUCTION SNAPSHOT (today's HLH value, the multi-day value, the last ~80 flow cycles with interest / impact / push-back and their responsive / initiative labels) and describe, in the user's auction-market vocabulary, what buyers and sellers are doing NOW. Use when the user types /auction-read, asks "what is the auction doing", "read the market", "who is in control", or pastes an auction_snapshot.json.
---

# /auction-read -- describe the auction, now

The reading instructions (the user's doctrine, the screen, every field, the rules, the output format) live in ONE
file shared with the tablet's "send to Claude" button: **`app/auction_read_prompt.md`**. Read it first and follow
it. This route has no screenshot -- only the data.

## Get the snapshot (read-only; nothing else)

Try in this order, and say which one you used and how old its `generated_utc` is:

1. **Pasted** -- if the user pasted JSON, or shared a file from the tablet, use it.
2. **The PC terminal** -- `data/auction_snapshot.json` in this repo, if `generated_utc` is less than 3 minutes old
   (the terminal writes it every 20 s while the Flow Interpretation feed is showing).
3. **The cloud tablet engine** (runs all day on the daemon VM):
   ```
   gcloud compute ssh smc-quant-eu --project=yass-chart --zone=europe-west9-b --command "cat /home/yassine_mdouari/smcflow/data/auction_snapshot.json"
   ```
   (Git Bash: filter the `Invalid characters in local username` warning out of the output.)

Read it as UTF-8. **Do not** fetch anything else: no Binance downloads, no database scans, no other VM files, no
history pulls. If nothing is fresh, say so and stop. Older than ~5 minutes = stale: say it before reading.
