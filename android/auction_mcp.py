# -*- coding: utf-8 -*-
"""SMC AUCTION -- the Claude app's CONNECTOR to the live market (user 2026-09-24: "go ahead with the two-way connector").

A read-only MCP server (Streamable HTTP) on the daemon VM. The Claude app (any Claude surface where the user added the
custom connector) calls it whenever the user asks about the market, so a conversation can re-read the auction as often
as it likes -- no button, no screenshot, always the market of the moment.

What it reads, and nothing else:
  * the engine's AUCTION SNAPSHOT file (android/flow_engine.py -> terminal._auction_snapshot_write): rewritten every
    20 s, and at once when the user marks a cycle on the tablet. Today's HLH value + D-blocs, the multi-day value, the
    summary, every cycle of the feed's 6 h window with interest / impact / kept / push-back / labels.
  * app/auction_read_prompt.md: the user's auction doctrine and how to read every field.
It never writes, never runs anything, never touches the daemon, the database or the network.

Exposure: it binds 127.0.0.1 only. Caddy terminates HTTPS in front of it and forwards ONLY a long random secret path
(android/deploy/Caddyfile.template); every other path is a 404. The secret lives in /etc/caddy/Caddyfile on the VM,
never in this repo.

Run (the VM's systemd unit android/deploy/smcmcp.service):
    /home/yassine_mdouari/mcpvenv/bin/python android/auction_mcp.py --port 8770
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import time

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SNAPSHOT = os.environ.get("AUCTION_SNAPSHOT", os.path.join(REPO, "data", "auction_snapshot.json"))
PROMPT = os.environ.get("AUCTION_PROMPT", os.path.join(REPO, "app", "auction_read_prompt.md"))
STALE_SECS = 90.0            # the engine rewrites it every 20 s: past this, say so
MAX_CYCLES = 200             # the most one call returns (tokens: ~250 per cycle)

INSTRUCTIONS = (
    "Live SOLUSDT auction data from Yassine's SMC Flow terminal (read-only). Yassine reads the market as an AUCTION: "
    "HLH Volume Profile value (POC, VAL..VAH), RESPONSIVE vs INITIATIVE activity, each side's interest and impact per "
    "flow cycle. At the start of a conversation about the market, call get_reading_instructions ONCE and follow it. "
    "Then call get_auction_snapshot for the live market (again whenever he asks what is happening now), "
    "get_marked_cycle when he refers to the cycle he marked / selected / tapped on the tablet, and get_cycle for a "
    "cycle at a given time (UTC, as his cards show). Describe what the auction IS doing, with numbers; never predict "
    "price and never suggest trades."
)

mcp = None     # built in main() with the port from the command line


# ============================================================================ the snapshot
def _load():
    """(snapshot dict, age in seconds). Raises with a plain message when the file is missing or unreadable."""
    try:
        st = os.stat(SNAPSHOT)
        with open(SNAPSHOT, encoding="utf-8") as fh:
            d = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError("no auction snapshot yet: the SMC Flow engine has not written one (is it running?)")
    except Exception as ex:
        raise RuntimeError("the auction snapshot could not be read: %s" % ex)
    return d, max(0.0, time.time() - st.st_mtime)


def _fresh(d: dict, age: float) -> dict:
    out = {"generated_utc": d.get("generated_utc"), "age_seconds": int(round(age))}
    if age > STALE_SECS:
        out["stale"] = True
        out["stale_note"] = ("the engine has not refreshed this for %d s -- the engine or the daemon may be down or "
                             "catching up; say so before reading it" % int(age))
    return out


def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _cycles(d: dict) -> list:
    return list(d.get("cycles_oldest_first") or [])


def _epoch(s: str) -> float:
    return float(calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S")))


def _marked(d: dict):
    """(index in the cycle list or None, the marked row or None)."""
    cy = _cycles(d)
    for i, c in enumerate(cy):
        if c.get("user_selected"):
            return i, c
    return None, d.get("user_selected_cycle")


def _around(cy: list, i: int, n: int = 3) -> dict:
    return {"previous_oldest_first": cy[max(0, i - n):i], "next_oldest_first": cy[i + 1:i + 1 + n]}


# ============================================================================ tools
def get_reading_instructions() -> str:
    """Yassine's auction-market doctrine and how to read the data: what responsive / initiative, effective / absorbed /
    passive mean, what every field of the snapshot means, the rules (describe, never predict; quote numbers) and the
    format of a read. Call this ONCE at the start of a conversation about the market, before reading the snapshot.
    (This connector has no screenshot: the screen section only applies to shares from the tablet.)"""
    try:
        with open(PROMPT, encoding="utf-8") as fh:
            return fh.read()
    except Exception as ex:
        return "The reading instructions could not be read (%s). Read the data descriptively; never predict." % ex


def get_auction_snapshot(last_n_cycles: int = 30) -> str:
    """The live SOLUSDT auction NOW (the engine refreshes it every 20 s): the live price; today's HLH value (POC, VAH,
    VAL, its D-blocs, how far the POC moved in the last hour) and the multi-day value; a summary of the last cycles;
    the newest `last_n_cycles` flow cycles (1-200, oldest first) with each side's interest, the leader's impact /
    conversion / kept share, the other side's push-back, the wall, and the responsive / initiative labels; and the
    cycle Yassine marked on his tablet, if any. Call it again whenever he asks what is happening now."""
    d, age = _load()
    n = max(1, min(MAX_CYCLES, int(last_n_cycles)))
    cy = _cycles(d)
    out = _fresh(d, age)
    for k in ("symbol", "tick", "live_price", "cycle_lookback_n", "value", "summary"):
        out[k] = d.get(k)
    mi, mrow = _marked(d)
    out["marked_cycle_start_utc"] = d.get("user_selected_cycle_start_utc")
    if mrow is not None and (mi is None or mi < len(cy) - n):
        out["marked_cycle"] = mrow                 # older than the rows returned: it goes along on its own
    out["cycles_in_window"] = len(cy)
    out["window_from_utc"] = cy[0].get("start_utc") if cy else None
    out["cycles_oldest_first"] = cy[-n:]
    return _dump(out)


def get_marked_cycle() -> str:
    """The cycle Yassine marked (tapped) on his tablet -- a card in the Interpretation feed or a candle -- in full, with
    the 3 cycles before and after it for context, and today's value for location. Use it whenever he says "the cycle I
    marked / selected / tapped / this candle"."""
    d, age = _load()
    out = _fresh(d, age)
    mi, mrow = _marked(d)
    if mrow is None:
        out["marked_cycle"] = None
        out["note"] = (d.get("user_selected_note") or "nothing is marked on the tablet: ask him to tap the card or "
                       "candle he means, or to give its start time")
        return _dump(out)
    out["marked_cycle"] = mrow
    if mi is not None:
        out.update(_around(_cycles(d), mi))
    out["value"] = d.get("value")
    out["live_price"] = d.get("live_price")
    return _dump(out)


_TIME = re.compile(r"^\s*(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:UTC|Z)?\s*$", re.I)


def get_cycle(time_utc: str) -> str:
    """One flow cycle in full -- the one running at `time_utc` (or starting nearest it) -- with the 3 cycles before and
    after. `time_utc` is a UTC time as Yassine's cards show it: "11:28:56", "11:28" or "2026-09-24 11:28:56". Covers
    the engine's feed window (about the last 6 hours)."""
    d, age = _load()
    cy = _cycles(d)
    out = _fresh(d, age)
    m = _TIME.match(str(time_utc or ""))
    if not m:
        out["error"] = "give a UTC time like 11:28:56, 11:28 or 2026-09-24 11:28:56"
        return _dump(out)
    if not cy:
        out["error"] = "the snapshot holds no cycles"
        return _dump(out)
    gen = _epoch(d["generated_utc"]) if d.get("generated_utc") else time.time()
    day = m.group(1) or time.strftime("%Y-%m-%d", time.gmtime(gen))
    q = _epoch("%s %02d:%s:%s" % (day, int(m.group(2)), m.group(3), m.group(4) or "00"))
    if not m.group(1) and q > gen + 60.0:
        q -= 86400.0                               # a clock time "after now" means yesterday's
    starts = [_epoch(c["start_utc"]) for c in cy]
    # a START time names its own cycle; any other time belongs to the cycle running then -- the one that started last
    # before it (boundary rule B: a cycle owns its start second up to the next cycle's start). Not dur_s: it is
    # rounded, and a 482 s cycle "ending" at 11:28:57 would swallow the one starting at 11:28:56.
    exact = [i for i, s in enumerate(starts) if abs(s - q) < 1.0]
    if exact:
        hit = exact[0]
    elif q >= starts[0]:
        hit = max(i for i, s in enumerate(starts) if s <= q)
    else:
        hit = 0
        out["note"] = "no cycle was running at that time; this is the one starting nearest it"
    if q < starts[0] - 60.0:
        out["note"] = ("that time is before the engine's window (it starts %s UTC); this is the oldest cycle held"
                       % cy[0]["start_utc"])
    out["cycle"] = cy[hit]
    out.update(_around(cy, hit))
    return _dump(out)


def main() -> None:
    global mcp
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    # stateless + plain JSON responses: no session to lose across restarts, nothing streamed through the proxy.
    # host 127.0.0.1 keeps the SDK's DNS-rebinding guard ON (Host must be 127.0.0.1:*): Caddy forwards with that Host.
    mcp = FastMCP("SMC Auction", instructions=INSTRUCTIONS, host="127.0.0.1", port=int(a.port),
                  streamable_http_path="/mcp", stateless_http=True, json_response=True)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    for fn in (get_reading_instructions, get_auction_snapshot, get_marked_cycle, get_cycle):
        # structured_output=False: the text only -- a str result would otherwise ALSO go out as structuredContent,
        # the same JSON twice in the model's context
        mcp.tool(annotations=ro, structured_output=False)(fn)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
