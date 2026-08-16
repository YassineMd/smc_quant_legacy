"""Backtest the KC Overshoot 2nd-Entry strategy (app/kc_overshoot_detect). All tf EXCEPT 1m (5m/15m/30m/1h/4h).
  ENTRY = the 2nd-entry bar's close (side per detector).
  SL    = 0.3% BEYOND the entry candle's extreme (short: high*1.003 ; long: low*0.997).
  TP    = FIXED 0.5% from entry (was the overshoot close -- swapped per user, that TP was RR-upside-down).
Both recon years (2025 / 2026) + the live forward cold-archive. Canonical NON-OVERLAP. 3bps slip, 0.04% fee.
Memory-safe: recon buckets are STREAMED OHLC-ONLY off the gz (no footprints) so 5m won't OOM. python study/kc_overshoot_backtest.py"""
import os, sys, glob, gzip, json, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from app import kc_overshoot_detect as KC

FEE = 0.0004; SLIP = 0.0003; SL_BUF = 0.001; TP1 = 0.003; TP2S = (0.005, 0.006); SPLIT = 0.5; H = 200
#  SL 0.1% beyond the entry candle | SCALE-OUT: 50% at TP1=0.3% then stop->breakeven | remaining 50% -> TP2 (0.5% / 0.6%)
TFS = ("5m", "15m", "30m", "1h", "4h")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _lite(d):
    return {"start_time": d.get("start_time"), "open_price": _f(d.get("open_price", d.get("open"))),
            "close_price": _f(d.get("close_price", d.get("close"))), "high": _f(d.get("high")),
            "low": _f(d.get("low")), "curr_vol": _f(d.get("curr_vol"))}


def stream_recon(tf):
    """OHLC-only recon buckets off the gz (no footprints) -> memory-safe even for 5m."""
    out = []
    for fn in sorted(glob.glob("study/recon_archive/%s/%s_*.jsonl.gz" % (tf, tf))):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line); d = r.get("data", r)
                out.append(_lite(json.loads(d) if isinstance(d, str) else d))
    out.sort(key=lambda b: _f(b.get("start_time", 0)))
    return out


def native_30m(A15):
    tvs = [_f(b.get("curr_vol")) for b in A15 if _f(b.get("curr_vol")) > 0]
    T = 2.0 * (statistics.median(tvs) if tvs else 0.0)
    out = []; acc = None
    for b in A15:
        o = _f(b.get("open_price")); c = _f(b.get("close_price")); h = _f(b.get("high")); l = _f(b.get("low")); v = _f(b.get("curr_vol"))
        if acc is None:
            acc = {"open_price": o, "close_price": c, "high": h, "low": l, "curr_vol": v, "start_time": b.get("start_time")}
        else:
            acc["close_price"] = c; acc["high"] = max(acc["high"], h); acc["low"] = min(acc["low"], l); acc["curr_vol"] += v
        if T > 0 and acc["curr_vol"] >= T:
            out.append(acc); acc = None
    if acc is not None:
        out.append(acc)
    return out


def get_buckets(tf, forward):
    if forward:
        if tf == "30m":
            return native_30m(sorted([_lite(b) for b in load_archive("15m")[1]], key=lambda b: _f(b.get("start_time", 0))))
        return sorted([_lite(b) for b in load_archive(tf)[1]], key=lambda b: _f(b.get("start_time", 0)))
    if tf == "30m":
        return native_30m(stream_recon("15m"))
    return stream_recon(tf)


def sim_scaleout(side, entry, sl0, tp1, tp2, PH, PL, PC):
    """SCALE-OUT: SPLIT at tp1, then stop->breakeven(entry) on the runner -> tp2. Returns (gross, outcome, off).
    gross is the blended return; outcomes: sl0 (full stop pre-TP1) / be (TP1 locked, runner stopped at BE) /
    tp2 (both) / end (TP1 locked, runner ran to the horizon)."""
    g1 = SPLIT * side * (tp1 - entry) / entry                          # the locked-in TP1 half (+~0.3% * SPLIT)
    tp1_done = False
    for off in range(len(PH)):
        hi = PH[off]; lo = PL[off]
        if not tp1_done:
            if (lo <= sl0) if side > 0 else (hi >= sl0):               # full position stopped before any scale-out
                return side * (sl0 - entry) / entry, "sl0", off + 1
            if (hi >= tp1) if side > 0 else (lo <= tp1):
                tp1_done = True
                if (hi >= tp2) if side > 0 else (lo <= tp2):           # same bar also cleared TP2
                    return g1 + (1 - SPLIT) * side * (tp2 - entry) / entry, "tp2", off + 1
        else:
            if (lo <= entry) if side > 0 else (hi >= entry):           # runner stopped at breakeven
                return g1, "be", off + 1
            if (hi >= tp2) if side > 0 else (lo <= tp2):
                return g1 + (1 - SPLIT) * side * (tp2 - entry) / entry, "tp2", off + 1
    lastret = (side * (PC[-1] - entry) / entry) if len(PC) else 0.0
    return (g1 + (1 - SPLIT) * lastret, "end", len(PH)) if tp1_done else (lastret, "end0", len(PH))


def trades(A, kind, tp2):
    n = len(A)
    if n < 60:
        return []
    C = np.array([_f(b.get("close_price")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = [e for e in KC.detect(A, with_failed=(kind == "failed"), skip_last=False) if e.get("kind", "entry") == kind]
    _ib = (lambda z: int(z["i_fail"])) if kind == "failed" else (lambda z: int(z["i_e2"]))   # entry bar per kind
    rows = []; last = -1
    for e in sorted(ev, key=_ib):
        k = _ib(e); side = int(e["side"])
        if k <= last or k + 1 >= n:
            continue
        entry = C[k]
        if side < 0:
            sl0 = Hi[k] * (1.0 + SL_BUF); tp1p = entry * (1.0 - TP1); tp2p = entry * (1.0 - tp2); risk = (sl0 - entry) / entry
        else:
            sl0 = Lo[k] * (1.0 - SL_BUF); tp1p = entry * (1.0 + TP1); tp2p = entry * (1.0 + tp2); risk = (entry - sl0) / entry
        if risk <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        gross, outc, off = sim_scaleout(side, entry, sl0, tp1p, tp2p, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp2" else 0.0)   # TP2 = both legs limit; else 1 market exit
        y = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        rows.append((y, net, net / risk, outc)); last = k + int(off)
    return rows


def rep(rows, label):
    if len(rows) < 10:
        print("    %-24s n=%d (<10)" % (label, len(rows))); return
    net = np.array([r[1] for r in rows])
    tp1 = 100.0 * np.mean([r[3] in ("be", "tp2", "end") for r in rows])   # reached TP1 (locked the half + BE)
    tp2 = 100.0 * np.mean([r[3] == "tp2" for r in rows]); sl0 = 100.0 * np.mean([r[3] == "sl0" for r in rows])
    print("    %-24s n=%-4d TP1=%2.0f%% TP2=%2.0f%% SL0=%2.0f%% win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (
        label, len(net), tp1, tp2, sl0, 100 * (net > 0).mean(), net.mean() * 100, np.mean([r[2] for r in rows])), flush=True)


def main():
    for tf in TFS:
        print("\n################  TF = %s  ################" % tf, flush=True)
        recA = get_buckets(tf, forward=False)
        try:
            fwdA = get_buckets(tf, forward=True)
        except Exception:
            fwdA = None
        for kind in ("entry", "failed"):                    # entry = the 2nd entry ; failed = the reversal
            print("  === %s ===" % ("2nd ENTRY" if kind == "entry" else "FAILED-2nd -> REVERSAL"), flush=True)
            for tp2 in TP2S:
                print("   TP2=%.1f%%:" % (tp2 * 100), flush=True)
                rec = trades(recA, kind, tp2)
                rep([r for r in rec if r[0] == 2025], "2025")
                rep([r for r in rec if r[0] == 2026], "2026")
                if fwdA is not None:
                    rep(trades(fwdA, kind, tp2), "forward")


if __name__ == "__main__":
    main()
