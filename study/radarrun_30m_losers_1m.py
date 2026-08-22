"""RADAR RUNNER 30m losers — RE-RESOLVE intrabar order at 1m. The 30m sim checks SL before TP within a bar (conservative),
so a candle big enough to touch BOTH the 0.25% TP and the ~1.2% candle-SL is credited to the SL even if price hit the TP
FIRST intrabar. This ONLY manufactures false losers (a win in the 30m sim had no bar touching SL before TP -> safe). Here
we take every 30m-flagged loser and walk 1m clock bars from the entry (breakout bar's close) forward, recording the FIRST
1m bar that touches TP vs SL -> the true outcome. Same terminal badge spec (entry=close, candle-SL 0.3% cap, TP=RR_TP_FRAC).
python study/radarrun_30m_losers_1m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import config, radar_breakout_detect as RB

H, SLBUF = 200, 0.003
TP_FRAC = config.RR_TP_FRAC
LOCAL = timezone(timedelta(hours=1))


def sim30(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        if (pl[off] <= sl) if s > 0 else (ph[off] >= sl):
            return "SL", off + 1
        if (ph[off] >= tp) if s > 0 else (pl[off] <= tp):
            return "TP", off + 1
    return "END", len(ph)


def flagged_losers(root, tf):
    """Every 30m-sim SL-loser (per-badge) + total badge/win counts (for the corrected win rate)."""
    A = sorted(load_archive(tf, root=root)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ET = np.array([_f(b.get("end_time")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    out = []; nbadge = 0; nwin30 = 0
    for g in RB.detect(A, skip_last=True, sl_buf=SLBUF, tp_frac=TP_FRAC):
        k = g["i"]; s = g["side"]; entry = g["entry"]; sl = g["sl_trade"]; tp = g["tp_trade"]
        if abs(entry - sl) / entry <= 0 or k + 1 >= n:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, off = sim30(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        nbadge += 1
        if outc == "SL":
            out.append(dict(s=s, entry=entry, sl=sl, tp=tp, et=ET[k], y=datetime.fromtimestamp(ST[k], tz=timezone.utc).year, off30=off))
        else:
            nwin30 += 1
    return out, nbadge, nwin30


def resolve_1m(loser, T1, H1, L1):
    """Walk 1m bars from the entry time forward; return ('TP'|'SL'|'AMB'|'NOCOV', minutes_to_hit)."""
    s = loser["s"]; sl = loser["sl"]; tp = loser["tp"]
    i0 = int(np.searchsorted(T1, loser["et"] - 1))          # first 1m bar at/after the breakout bar's close
    if i0 >= len(T1):
        return "NOCOV", 0
    cap = min(len(T1), i0 + 6000)                            # up to ~100h forward
    for j in range(i0, cap):
        sl_hit = (H1[j] >= sl) if s < 0 else (L1[j] <= sl)
        tp_hit = (L1[j] <= tp) if s < 0 else (H1[j] >= tp)
        if sl_hit and tp_hit:
            return "AMB", j - i0                            # both in ONE 1m bar (still ambiguous, keep conservative)
        if tp_hit:
            return "TP", j - i0
        if sl_hit:
            return "SL", j - i0
    return "NOCOV", cap - i0                                 # never resolved within coverage


def main():
    print("RADAR RUNNER 30m losers — 1m intrabar re-resolution (badge spec TP=%.2f%%, candle-SL 0.3%% cap)\n" % (TP_FRAC * 100), flush=True)
    print("loading 1m clock archive (large) ...", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    print("1m bars: %d  (%s .. %s)\n" % (len(A1), datetime.fromtimestamp(T1[0], tz=timezone.utc).date(),
          datetime.fromtimestamp(T1[-1], tz=timezone.utc).date()), flush=True)
    del A1

    for root, tf, label in (("study/clock_archive", "30m", "CLOCK"), ("study/recon_archive", "30m", "BUCKET")):
        L, nbadge, nwin30 = flagged_losers(root, tf)
        for r in L:
            r["true"], r["m"] = resolve_1m(r, T1, H1, L1)
        tpfirst = [r for r in L if r["true"] == "TP"]
        truelos = [r for r in L if r["true"] in ("SL", "AMB", "NOCOV")]        # keep AMB/NOCOV as losers (conservative)
        nwin_corr = nwin30 + len(tpfirst)
        print("=" * 100, flush=True)
        print("RADAR RUNNER 30m %s — 1m-VERIFIED  |  badges=%d  win(30m-sim)=%.1f%%  ->  win(1m-verified)=%.1f%%"
              % (label, nbadge, 100 * nwin30 / max(1, nbadge), 100 * nwin_corr / max(1, nbadge)), flush=True)
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
        print("", flush=True)


if __name__ == "__main__":
    main()
