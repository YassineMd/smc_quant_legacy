"""TIME-CANDLE SPIKE — honesty parity check (deliverable #1 of the clock-candle feature).

Builds gap-filled CLOCK candles (1m/5m) with per-price-level footprints from a raw aggTrade tape, using the daemon's
OWN primitives (app.aggtrade.trade_to_tick + candle_open) — so this proves the exact server-side binning is honest,
not a parallel reimplementation. Then a PARITY check (an exact accounting identity, sample-size-independent):

  (A) tape total volume  ==  sum of candle buy_vol/sell_vol         -> no trade lost or double-counted
  (B) per candle: sum(footprint levels)  ==  that candle's buy/sell -> the footprint is complete (nothing off-level)

Honesty% = 100*(1 - |candle_total - tape_total| / tape_total). Gap-fill: every clock interval gets a candle (empty =
flat, zero-vol) so the index axis is a perfect linear proxy for time. python study/timecandle_spike_parity.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from app.aggtrade import trade_to_tick, candle_open
from app import config

TAPE = os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_20260615T174547Z.jsonl")


def load_aggtrades(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "aggTrade":
                out.append(r["data"])                       # {p, q, m, T, ...}
    return out


def build_clock_candles(trades, tf_secs):
    """Bin trades into clock candles by candle_open(); OHLC + per-level footprint + buy/sell totals."""
    cand = {}
    for d in trades:
        t_ms = int(d["T"]); k = candle_open(t_ms, tf_secs)
        ta = trade_to_tick(d); price = ta.price; vol = ta.vol; is_buy = ta.taker_buy > 0.0
        c = cand.get(k)
        if c is None:
            c = cand[k] = {"t": k, "o": price, "h": price, "l": price, "c": price,
                           "buy_vol": 0.0, "sell_vol": 0.0, "levels": {}, "n": 0}
        c["h"] = max(c["h"], price); c["l"] = min(c["l"], price); c["c"] = price; c["n"] += 1
        c["buy_vol" if is_buy else "sell_vol"] += vol
        lv = c["levels"].setdefault("%.2f" % price, {"b": 0.0, "s": 0.0})
        lv["b" if is_buy else "s"] += vol
    return cand


def gap_fill(cand, tf_secs):
    """One candle per interval over [first, last]; missing intervals -> flat empty candles (honest time axis)."""
    if not cand:
        return []
    ks = sorted(cand); out = []; prev_c = cand[ks[0]]["o"]; k = ks[0]
    while k <= ks[-1]:
        if k in cand:
            out.append(cand[k]); prev_c = cand[k]["c"]
        else:
            out.append({"t": k, "o": prev_c, "h": prev_c, "l": prev_c, "c": prev_c,
                        "buy_vol": 0.0, "sell_vol": 0.0, "levels": {}, "n": 0, "empty": True})
        k += tf_secs
    return out


def main():
    trades = load_aggtrades(TAPE)
    t0 = int(trades[0]["T"]); t1 = int(trades[-1]["T"])
    dt = lambda ms: datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%H:%M:%S")
    tape_buy = sum(float(d["q"]) for d in trades if not d["m"])
    tape_sell = sum(float(d["q"]) for d in trades if d["m"])
    print("TAPE: %d aggTrades  %s..%s UTC  (%.1f min)   tape buy=%.2f sell=%.2f\n" % (
        len(trades), dt(t0), dt(t1), (t1 - t0) / 60000.0, tape_buy, tape_sell), flush=True)

    for tf in ("1m", "5m"):
        secs = config.TF_SECONDS[tf]
        candles = gap_fill(build_clock_candles(trades, secs), secs)
        real = [c for c in candles if not c.get("empty")]; empt = len(candles) - len(real)
        cb = sum(c["buy_vol"] for c in candles); cs = sum(c["sell_vol"] for c in candles)   # (A) OHLC totals
        lb = sum(lv["b"] for c in candles for lv in c["levels"].values())                   # (B) footprint totals
        ls = sum(lv["s"] for c in candles for lv in c["levels"].values())
        # per-candle worst footprint-vs-total discrepancy (should be ~0)
        pcmax = max((abs(sum(lv["b"] for lv in c["levels"].values()) - c["buy_vol"]) +
                     abs(sum(lv["s"] for lv in c["levels"].values()) - c["sell_vol"])) for c in real) if real else 0.0
        tot = tape_buy + tape_sell
        honA = 100.0 * (1 - (abs(cb - tape_buy) + abs(cs - tape_sell)) / tot) if tot else 100.0
        honB = 100.0 * (1 - (abs(lb - cb) + abs(ls - cs)) / tot) if tot else 100.0
        print("==== %s clock candles: %d total (%d real + %d gap-filled) ====" % (tf, len(candles), len(real), empt), flush=True)
        print("  (A) tape vol vs candle buy/sell:  tape=%.4f/%.4f  candle=%.4f/%.4f  -> honesty %.4f%%" % (
            tape_buy, tape_sell, cb, cs, honA), flush=True)
        print("  (B) candle buy/sell vs footprint levels: %.4f/%.4f vs %.4f/%.4f  -> honesty %.4f%%  (worst per-candle d=%.2e)" % (
            cb, cs, lb, ls, honB, pcmax), flush=True)
        # show a sample real candle so we SEE it produces genuine footprint candles
        s = real[len(real) // 2]
        top = sorted(s["levels"].items(), key=lambda kv: -(kv[1]["b"] + kv[1]["s"]))[:3]
        print("  sample candle @%s UTC:  O%.2f H%.2f L%.2f C%.2f  buy=%.1f sell=%.1f  %d trades" % (
            dt(s["t"] * 1000), s["o"], s["h"], s["l"], s["c"], s["buy_vol"], s["sell_vol"], s["n"]), flush=True)
        print("     heaviest levels: " + "  ".join("%s(b%.0f/s%.0f)" % (p, v["b"], v["s"]) for p, v in top), flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()
