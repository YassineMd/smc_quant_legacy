"""RADAR RUNNER 15m clock — the TRULY honest loser list, built from the terminal's OWN fired-signal record
(data/radarrun_fired.json), NOT from a batch re-detection. Batch detect() over full history repaints the radar runs
(later revisits merge the run and shift the breakout bar), so it DROPS live-fired badges — e.g. 2025-02-19 20:15 LONG,
which the user saw and which IS a loser. The persisted file is the ground truth of what actually fired (each entry already
carries the terminal's exact entry/sl/tp). Here we take every persisted 15m fire and resolve its outcome at 1m (first bar
to touch tp vs sl from the entry = the fire's end_time). Coverage = whatever the user has run/replayed (grows over time);
fires past the 1m archive end are flagged no-coverage. python study/radarrun_15m_losers_persisted.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from app import config
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

LOCAL = timezone(timedelta(hours=1))
TF = "15m"


def resolve_1m(side, entry, sl, tp, t_open, T1, H1, L1):
    """Walk 1m clock bars from the fire's entry (end_time) forward; first bar to touch tp vs sl = the true outcome."""
    i0 = int(np.searchsorted(T1, t_open - 1))
    if i0 >= len(T1):
        return "NOCOV", 0
    cap = min(len(T1), i0 + 6000)
    for j in range(i0, cap):
        sl_hit = (L1[j] <= sl) if side > 0 else (H1[j] >= sl)
        tp_hit = (H1[j] >= tp) if side > 0 else (L1[j] <= tp)
        if sl_hit and tp_hit:
            return "AMB", j - i0          # both in one 1m bar (rare; conservative = loss)
        if tp_hit:
            return "TP", j - i0
        if sl_hit:
            return "SL", j - i0
    return "NOCOV", cap - i0


def main():
    print("RADAR RUNNER 15m — HONEST losers from the TERMINAL'S OWN fired record (data/radarrun_fired.json), 1m-resolved\n", flush=True)
    fired = json.load(open(os.path.join(config.DATA_DIR, "radarrun_fired.json")))[TF]
    rows = []
    for k, v in fired.items():
        t = float(k); s = 1 if float(v.get("side", 0)) > 0 else -1
        rows.append(dict(t=t, s=s, entry=float(v["entry"]), sl=float(v["sl"]), tp=float(v["tp"]),
                         y=datetime.fromtimestamp(t, tz=timezone.utc).year))
    rows.sort(key=lambda r: r["t"])
    print("loading 1m clock archive ...", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    _1mend = datetime.fromtimestamp(T1[-1], tz=timezone.utc)
    del A1
    for r in rows:
        r["outc"], r["m"] = resolve_1m(r["s"], r["entry"], r["sl"], r["tp"], r["t"], T1, H1, L1)
    resolved = [r for r in rows if r["outc"] in ("TP", "SL", "AMB")]
    nocov = [r for r in rows if r["outc"] == "NOCOV"]
    losers = [r for r in resolved if r["outc"] in ("SL", "AMB")]
    wins = [r for r in resolved if r["outc"] == "TP"]
    span_lo = datetime.fromtimestamp(rows[0]["t"], tz=timezone.utc); span_hi = datetime.fromtimestamp(rows[-1]["t"], tz=timezone.utc)
    print("=" * 100, flush=True)
    print("terminal fired %d 15m badges  (span %s .. %s)" % (len(rows), span_lo.strftime("%Y-%m-%d"), span_hi.strftime("%Y-%m-%d")), flush=True)
    print("  1m-resolved: %d  (win %d = %.1f%%, LOSERS %d)   no-1m-coverage: %d  (fires after %s — daemon-live, 1m archive ends there)"
          % (len(resolved), len(wins), 100 * len(wins) / max(1, len(resolved)), len(losers), len(nocov), _1mend.strftime("%Y-%m-%d")), flush=True)
    print("  losers by year: " + "   ".join("%d: %d" % (Y, sum(1 for r in losers if r["y"] == Y)) for Y in (2025, 2026)), flush=True)
    print("-" * 100, flush=True)
    print("  %-3s | %-16s | %-11s | %-5s | %-9s | %-9s | %-9s | %s" % ("#", "FIRE UTC (end)", "local+1", "side", "entry", "SL", "TP", "res"), flush=True)
    for i, r in enumerate(sorted(losers, key=lambda r: r["t"]), 1):
        fu = datetime.fromtimestamp(r["t"], tz=timezone.utc); fl = datetime.fromtimestamp(r["t"], tz=LOCAL)
        tag = "1m-amb" if r["outc"] == "AMB" else "SL"
        print("  %-3d | %-16s | %-11s | %-5s | %9.3f | %9.3f | %9.3f | %s" % (i, fu.strftime("%Y-%m-%d %H:%M"),
              fl.strftime("%m-%d %H:%M"), "LONG" if r["s"] > 0 else "SHORT", r["entry"], r["sl"], r["tp"], tag), flush=True)
    if nocov:
        print("\n  (%d fires past the 1m archive — not yet classifiable here: %s .. %s)"
              % (len(nocov), datetime.fromtimestamp(nocov[0]["t"], tz=timezone.utc).strftime("%Y-%m-%d"),
                 datetime.fromtimestamp(nocov[-1]["t"], tz=timezone.utc).strftime("%Y-%m-%d")), flush=True)


if __name__ == "__main__":
    main()
