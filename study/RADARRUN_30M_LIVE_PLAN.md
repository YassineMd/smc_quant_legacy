# Live 30m Radar Runner — implementation plan

**Why:** native (volume-paced) 30m Radar Runner ≈ 1h edge (+0.185R, 84% win, both-year robust @6bps) at ~2.5× the
frequency, and passes a prop-firm drawdown MC at R=0.5–0.75% (see memory `radarrun-30m-quicktp`). Only tested on RECON so
far, and the edge is **sensitive to how 30m bars are built** → must confirm on a real, volume-paced 30m feed. That means
a daemon change (the daemon owns bucket construction).

**Good news from the code:** the system is config-driven. `app/config.py` `TIMEFRAMES` + `TF_SECONDS` drive per-TF
volume bucketing; each TF's `target_vol` is derived from its nominal seconds, so **30m = TF_SECONDS 1800 ≈ 2× the 15m
target automatically** — the exact native construction that worked. Adding a TF is mostly declarative + an audit of any
"exactly five" hardcoding (`config.py:111` comment: "spec §7.2.1 — exactly five" is the red flag).

## Phase 1 — Daemon (VM `smc-quant-eu`) emits a 30m stream
1. `app/config.py`: add `"30m"` to `TIMEFRAMES`, `TF_SECONDS["30m"]=1800`, `TF_PANDAS_FREQ["30m"]="30min"`; fix the
   "exactly five" comment/spec ref.
2. **Audit hardcoded-TF assumptions** (main risk): grep the engine/persistence/pipe for the literal 5-TF set, positional
   indexing, or fixed-width schemas instead of iterating `config.TIMEFRAMES`. Touch points: the bucketer loop, per-tf
   `closed_buckets` + `meta.total_closed_<tf>` counter (see memory `bucket-index-resolution`), snapshot/history
   serialization, and the IPC frame (`app/pipe_client.py`). Each must gain a 30m slot by iteration, not literal.
3. Confirm `target_vol` derivation reads `TF_SECONDS` (so 30m auto = 2× 15m). If it's a static table, add the 30m entry
   = 2× the 15m target.
4. Cold-archive: `ops/archive_buckets.py` must iterate `TIMEFRAMES` → 30m gets a GCS keyspace
   `gs://smc-quant-archive/30m/` on the 6h cron (memory `bucket-cold-archive`). `load_archive("30m")` then works locally.
5. **Memory headroom:** a 6th bucketer + its history buffers adds daemon RAM. VM is e2-small (2GB), MemoryCurrent
   ~726MB. Check headroom after deploy; may need a VM bump or a shorter 30m history cap (memories `smc-cloud-deployment`,
   `daemon-latency-partial-fix`).
6. Deploy: `deploy.ps1` + `systemctl restart orderflow`; terminal reaches it via the port-9999 SSH tunnel.

## Phase 2 — Terminal consumes 30m
1. Shared `app/config.py` change flows to the client. Add `"30m"` to the terminal-local TF→seconds maps that are
   duplicated as literals: `terminal.py` ~2697/2852/2997 `{"1m":60,...,"4h":14400}`, `_tfmin` ~5795, `_fk/_fx` ~13589
   — or refactor these to read `config.TF_SECONDS`.
2. **Radar Runner overlay gate `terminal.py:7249`**: `self._tf not in ("1m","5m","15m","1h")` → add `"30m"`.
3. Per-tf bucket cache (`app/bucket_cache.py`, memory `startup-delta-cache`) is tf-keyed → should just work; verify.
4. TF selector UI + panel-toggle persistence (memory `panel-toggles-persist`) — 30m shows up and persists.

## Phase 3 — Validate live before sizing real money
1. Forward-collect a few weeks of the live 30m stream (snapshot approach, memory `barrier-study-walkforward`); confirm
   the live 30m Radar Runner matches the recon backtest — especially the **both-year-robust** behavior (the sensitivity
   caveat). 
2. Method cross-check: rebuild native 30m from the daemon's own live 15m by volume-accumulation and compare to the
   daemon's real 30m stream — they should agree, validating the reconstruction against a true feed.
3. Paper-trade the exit spec on live 30m (memory `position-paper-sim`) before real risk.

## Optional — encode the tradeable bracket in the detector
`app/radar_breakout_detect.py` can emit the tradeable levels directly: **entry = breakout close; TP = fixed 0.4–0.5%;
SL = 0.3% beyond the entry candle's opposite extreme, capped at the radar extreme ("cand+0.3cap")**. Then the overlay +
paper account show/trade the exact bracket instead of the tiered targets.

## Risks / open questions
- "Exactly five TF" hardcoding is the implementation risk — audit first.
- Daemon memory on e2-small with a 6th stream — may force a VM bump.
- **The sensitivity caveat is load-bearing:** do NOT size real money until the LIVE 30m stream reproduces the recon edge.
