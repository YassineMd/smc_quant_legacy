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
calls; it appends to `study/cycle_archive/logs/`.

## What a harvest holds

| key | what |
|---|---|
| `t, t_end, done, is_buy, strong, move, cbuy, csell, o, h, l, c` | every cycle of the store (uncapped read) |
| `iimp` | the pane's OWN numbers per rated cycle: both sides' I×I, the previous bar's, interest ratios, impact score, leader, wall, reach, move, kept, vacuum / quiet, buyer / seller scores (columns in `meta.iimp_cols`) |
| `colours` | each candle's cached state colour on the PRICE pane |
| `badges` | the Takeover marks **as the terminal drew them** (+1 buy, −1 sell) |
| `recomputed` | the same rule recomputed from the harvested numbers — `meta.takeover` compares the two |
| `walls`, `books` | both sides' resting $ at each cycle's open, and the book means over it |
| `bin_buy, bin_sell, bin_px, bin_pxh, bin_pxl` | the store's 1-second bins (bin `i` is second `meta.bin_base + i`) — the price path for first-touch TP / SL |
| `meta` | rule constants, lookback, flow window, commit, the walk's log |

## Load it

```python
from study.cycle_archive.load_cycles import load_archive
A = load_archive(verbose=True)     # one row per cycle, harvests merged, coverage + gaps + stability printed
```

`python study/cycle_archive/load_cycles.py --pull` first mirrors the bucket into `data/` (additive).

A cycle seen by several harvests is taken from the one where it sits deepest past that harvest's window start. **Gaps
are listed and must be excluded from any test** — a mark that could not be drawn is a missing signal, not a quiet tape.

## Before testing anything on it

Freeze the Takeover rule first. Every change to the rule after data has been looked at turns the archive collected so
far into in-sample data for that change. The harvest records the rule's constants and the commit, so a later test can
tell which marks were drawn under which rule.
