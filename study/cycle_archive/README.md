# Cycle archive

The cycle chart (PRICE pane candles, INTEREST × IMPACT, Takeover) is rebuilt from the daemon's **rolling 72 hours** of
tape and order book. Nothing older holds either, and the impact half of interest × impact needs the recorded book, which
no exchange publishes as history. So the numbers the terminal shows can only be kept by **saving them as they go by**.

72 hours hold about 75 Takeover signals. A strategy test needs hundreds, across more than one market regime.

## Run a harvest

```bash
python study/cycle_archive/collect_cycles.py
```

Takes 5–8 minutes. It boots an offscreen terminal on a **temporary copy** of your UI state, opens the SSH tunnel only if
it is not already up (and closes it again), waits for the full 72 h backfill, walks the tape in 3-hour views, and writes
one immutable file:

- `study/cycle_archive/data/cycles_<UTC stamp>.npz` + `.json`
- a copy to `gs://smc-quant-archive/solusdt/cycles/` (skip with `--no-upload`)

**Run it at least every 2 days.** The pane rows start one lookback (4 h at N = 20) into the window, so one harvest
covers about 68 h; harvests more than ~2.8 days apart leave a gap. `run_collect.cmd` is the wrapper a scheduled task
calls; it appends to `study/cycle_archive/logs/collect_YYYYMM.log`.

**The daily task** (user 2026-09-24): `register_daily_task.ps1` registers **"SMC Cycle Archive Harvest"** for the current
user -- daily at 18:00 PC time, and at the next chance if the PC was off then (StartWhenAvailable), minimized, only
while logged on (no stored password), one at a time, stopped after 45 min. `-Remove` deletes it; `-At HH:MM` moves it.

```powershell
powershell -ExecutionPolicy Bypass -File study\cycle_archiveegister_daily_task.ps1
Get-ScheduledTaskInfo -TaskName "SMC Cycle Archive Harvest"      # LastRunTime / LastTaskResult / NextRunTime
```

The PC has to be on and logged on at least once every ~2 days, or the archive gets a gap (the loader reports it).

## What a harvest holds

| key | what |
|---|---|
| `t, t_end, done, is_buy, strong, move, cbuy, csell, o, h, l, c` | every cycle of the store (uncapped read) |
| `iimp` | the pane's OWN numbers per rated cycle: both sides' I×I, the previous bar's, interest ratios, impact score, leader, wall, reach, move, kept, vacuum / quiet, buyer / seller scores (columns in `meta.iimp_cols`) |
| `colours` | each candle's cached state colour on the PRICE pane |
| `badges` | the Takeover marks **as the terminal drew them** (+1 buy, −1 sell) |
| `recomputed` | the same rule recomputed from the harvested numbers — `meta.takeover` compares the two |
| `wall_grid`, `wall_prov` | **the canonical 15 s wall grid** the terminal's I×I reads: every column of the window, `(col, ask $, bid $, mid)` at `IIMP_WALL_RADIUS` (column `k` covers `[k·15 s, (k+1)·15 s)`), and the columns still provisional. Recompute any cycle's wall under any cycle rule: column `floor(t/15) − 1`, asks for a buy cycle, bids for a sell one (`load_cycles.wall_at`) |
| `walls` | both sides' resting $ at each cycle's open, read from that grid by the terminal's own `_iimp_wall` |
| `books` | always empty since 2026-09-23 (the per-cycle book cache was retired) |
| `bin_buy, bin_sell, bin_px, bin_pxh, bin_pxl` | the store's 1-second bins (bin `i` is second `meta.bin_base + i`) — the price path for first-touch TP / SL |
| `meta` | rule constants, lookback, flow window, commit, the walk's log |

⚠ **Harvests before 2026-09-24** read a per-cycle wall cache the terminal retired on 2026-09-23: from then on they saved
no walls, and they read their oldest views before the wall grid had reached back there. From 2026-09-24 the collector
waits for the whole grid first and saves it. ⚠ **`cycles_20260921_203803`** was taken before the flow-bins double-count
fix (2026-09-22): checked against clean data, its 09-21 19:00 UTC hour holds exactly 2× the $ (prices unaffected);
its first ~45 h (09-18 20:38 → 09-20 17:46) cannot be checked against anything.

## Load it

```python
from study.cycle_archive.load_cycles import load_archive
A = load_archive(verbose=True)     # one row per cycle, harvests merged, coverage + gaps + stability printed
```

`python study/cycle_archive/load_cycles.py --pull` first mirrors the bucket into `data/` (additive).
`load_wall_grid()` merges the harvests' wall grids (a final reading beats a provisional one) and lists the gaps.

A cycle seen by several harvests is taken from the one where it sits deepest past that harvest's window start. **Gaps
are listed and must be excluded from any test** — a mark that could not be drawn is a missing signal, not a quiet tape.

## Before testing anything on it

Freeze the Takeover rule first. Every change to the rule after data has been looked at turns the archive collected so
far into in-sample data for that change. The harvest records the rule's constants and the commit, so a later test can
tell which marks were drawn under which rule.
