# Decoupled Order Flow Terminal

A native, multi-window order-flow trading terminal for SOLUSDT perpetuals.
Headless asyncio data core + GPU-accelerated PySide6/PyQtGraph clients, talking
over a local TCP loopback. Ported from a legacy FastAPI/web prototype; the
quantitative engine (Otsu-calculus order blocks, 4-vector flow decomposition,
VPIN toxicity) is preserved verbatim.

## Architecture

```
  OrderFlowDaemon  (headless core, 127.0.0.1:9999)
    ├─ 5 concurrent Binance kline streams (1m/5m/15m/1h/4h)
    ├─ depth, liquidations, open-interest feeds
    ├─ QuantEngine per timeframe (Otsu + calculus + VPIN)
    └─ fire-and-forget broadcast → newline-framed JSON
                │
                ▼  (per-window timeframe subscription)
  OrderFlowTerminal × N  (independent GUI windows, Ctrl+N to spawn)
    └─ 20Hz render: candles, order blocks, footprints, imbalances,
       icebergs, COB depth ladder, depth walls, 12-line stats,
       drawing tools, alerts ledger, indestructible price/countdown HUD
```

All controls live in the top-right `☰` hamburger panel; the canvas stays nude.

## Requirements

- Python 3.10+ (3.11+ recommended)
- A live internet connection (streams from `fstream.binance.com`)

```bash
pip install -r requirements.txt
```

## Run from source (development)

Two processes — start the daemon first, then one or more terminals:

```bash
python -m app.daemon       # headless core (keep this running)
python -m app.terminal     # GUI window — Ctrl+N spawns more
```

Each terminal window picks its own timeframe from the hamburger; they do not
contend.

## Data & schema migrations

The daemon keeps finalized state in `data/history.db`. On a **schema-version bump**
it prints `SCHEMA MIGRATION: db bucket schema vN != code vM; cleared … (footprints
kept)` and re-accumulates from a cold start. **This is intentional, not a crash:** a
bump means a bucket's *meaning* changed, so old finalized buckets / order-blocks /
engine-state are cleared to avoid mixing old- and new-meaning rows in one rolling
window; footprints are keyed separately and kept as the rebuild source. The current
bump is **v3 (Phase 5): the data source moved from 1-second klines to order-by-order
aggTrade**, so every bucket's price levels changed meaning (smeared-onto-close →
true per-trade price) — a deliberate fidelity cutover, not a regression. The frozen
pre-fix reference `data/history.db.before-fixes` is never touched by this.

## Build standalone executables

```bash
python build.py                 # both, one-file
python build.py --onedir        # both, one-folder (faster startup)
python build.py --daemon-only   # just the core
python build.py --terminal-only # just the GUI
```

Output in `dist/`:

| Executable | Type | Role |
|------------|------|------|
| `OrderFlowDaemon`   | console  | headless data core — shows logs |
| `OrderFlowTerminal` | windowed | GUI client |

Launch `OrderFlowDaemon` first, then `OrderFlowTerminal`. User data
(`data/server_footprints.json`, `data/drawings.json`) is written next to the
executable.

## Controls

- **Pan:** left-drag · **Zoom:** wheel (cursor-centered) · **Axis scale:** right-drag on an axis
- **Drawing:** enable the Vector Drawing Toolbar; press `V` to cancel a tool and restore panning
- **Position tools:** Long/Short brackets have independent draggable Entry/Stop/Target handles with live R:R
- **Hover** a candle for the 12-line stats overlay + structural verdict
