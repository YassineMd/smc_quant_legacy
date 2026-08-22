"""RADAR RUNNER 15m CLOCK — the HONEST (1m-verified) loser list. Reuses the 30m verification machinery: detect with the
terminal badge spec (entry=breakout close, candle-SL 0.3% cap, TP=RR_TP_FRAC=0.25%, MINVISIT=1), 30m-style sim to flag
SL-losers, then RE-RESOLVE each at 1m (walk clock_archive/1m from the breakout bar's close forward; first 1m bar to touch
TP vs SL is the true outcome). This strips the FALSE losers the bar-level SL-first sim manufactures (a candle spanning both
levels gets credited to the SL even when price hit the TP first intrabar). python study/radarrun_15m_losers.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_30m_losers_1m import flagged_losers, resolve_1m, LOCAL

ROOT, TF, LABEL = "study/clock_archive", "15m", "CLOCK"


def main():
    print("RADAR RUNNER 15m %s — HONEST (1m-verified) losers | badge spec TP=0.25%%, candle-SL 0.3%% cap, MINVISIT=1\n" % LABEL, flush=True)
    print("loading 1m clock archive (large) ...", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    print("1m bars: %d  (%s .. %s)\n" % (len(A1), datetime.fromtimestamp(T1[0], tz=timezone.utc).date(),
          datetime.fromtimestamp(T1[-1], tz=timezone.utc).date()), flush=True)
    del A1

    L, nbadge, nwin30 = flagged_losers(ROOT, TF)
    for r in L:
        r["true"], r["m"] = resolve_1m(r, T1, H1, L1)
    tpfirst = [r for r in L if r["true"] == "TP"]
    truelos = [r for r in L if r["true"] in ("SL", "AMB", "NOCOV")]
    nwin_corr = nwin30 + len(tpfirst)
    print("=" * 100, flush=True)
    print("RADAR RUNNER 15m %s — 1m-VERIFIED  |  badges=%d  win(30m-sim)=%.1f%%  ->  win(1m-verified)=%.1f%%"
          % (LABEL, nbadge, 100 * nwin30 / max(1, nbadge), 100 * nwin_corr / max(1, nbadge)), flush=True)
    print("  flagged losers=%d  ->  FALSE (TP-first at 1m)=%d   TRUE losers=%d" % (len(L), len(tpfirst), len(truelos)), flush=True)
    print("  by year (true losers):  " + "   ".join("%d: %d" % (Y, sum(1 for r in truelos if r["y"] == Y)) for Y in (2025, 2026)), flush=True)
    if tpfirst:
        print("  --- REMOVED false losers (were actually TP-first WINS): " +
              ", ".join("%s %s (TP@%dm)" % (datetime.fromtimestamp(r["et"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "L" if r["s"] > 0 else "S", r["m"]) for r in sorted(tpfirst, key=lambda r: r["et"])), flush=True)
    print("-" * 100, flush=True)
    print("  %-3s | %-16s | %-11s | %-5s | %-9s | %-9s | %-9s" % ("#", "FIRE UTC (end)", "local+1", "side", "entry", "SL", "TP"), flush=True)
    for i, r in enumerate(sorted(truelos, key=lambda r: r["et"]), 1):
        fu = datetime.fromtimestamp(r["et"], tz=timezone.utc); fl = datetime.fromtimestamp(r["et"], tz=LOCAL)
        tag = "  <-- no 1m cov" if r["true"] == "NOCOV" else ("  <-- 1m-ambiguous" if r["true"] == "AMB" else "")
        print("  %-3d | %-16s | %-11s | %-5s | %9.3f | %9.3f | %9.3f%s" % (i, fu.strftime("%Y-%m-%d %H:%M"),
              fl.strftime("%m-%d %H:%M"), "LONG" if r["s"] > 0 else "SHORT", r["entry"], r["sl"], r["tp"], tag), flush=True)


if __name__ == "__main__":
    main()
