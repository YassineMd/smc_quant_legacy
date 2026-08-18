"""Prop-firm eval of taking Radar Runner from MULTIPLE sources -- separately AND combined (one account, concurrent
positions allowed). Sources: 5m/15m/30m CLOCK candles + 30m BUCKET (volume) candles. 0.2% TP + candle-SL, fee+slip,
taken()-nonoverlap PER SOURCE, day-block MC (target10/maxDD10/daily5) at R=0.5/0.75/1%. 5m clock uses the shipped
absorpR>=-0.25 gate (the only version that passes). COMBINED = pool every source's trades onto one equity path (each
trade risks R; sources can overlap in time). Reports win% / maxDD / trades-day / pass% / MEDIAN days-to-pass.
python study/radarrun_multi_prop.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, TP, SLBUF, TARGET, MAXDD, DAILY, NPATH, MAXD
from study.radarrun_winrate_dd import maxdd_pct
from app import absorption as ABS, config

SOURCES = [
    ("5m clock (absorpR>=-.25)", "study/clock_archive", "5m", True),
    ("15m clock",                "study/clock_archive", "15m", False),
    ("30m clock",                "study/clock_archive", "30m", False),
    ("30m bucket (volume)",      "study/recon_archive", "30m", False),
]


def source_trades(root, tf, absorpR_filter):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    sigs, Hi, Lo, C, n = detect(A, SLBUF.get(tf, 0.003))
    if absorpR_filter:
        keep = []
        for sg in sigs:
            try:
                a = ABS.absorption(A, sg[0])[0]
            except Exception:
                a = None
            if a is None or a >= config.RR_ABSORPR_MIN:
                keep.append(sg)
        sigs = keep
    return eval_tp(sigs, Hi, Lo, C, n, TP)          # [(ts, net, R)]


def day_blocks(tr):
    by = {}
    for ts, _net, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append((ts, r))
    if not by:
        return []
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        day = sorted(by.get(d, []))                  # intra-day time order (for the daily-loss check)
        days.append([r for _t, r in day]); d += timedelta(days=1)
    return days


def mc(days):
    """(pass% at R0.5/0.75/1.0, median days-to-pass at each R)."""
    out = []
    for Rp in (0.5, 0.75, 1.0):
        random.seed(7); passes = 0; dtp = []
        for _ in range(NPATH):
            eq = peak = 0.0; passed = failed = False
            for dd in range(1, MAXD + 1):
                day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
                for r in day:
                    eq += Rp * r; dlow = min(dlow, eq); peak = max(peak, eq)
                    if peak - eq >= MAXDD:
                        failed = True; break
                    if eq >= TARGET:
                        passed = True; break
                if failed or (dstart - dlow) >= DAILY:
                    failed = True
                if passed or failed:
                    if passed:
                        passes += 1; dtp.append(dd)
                    break
        out.append((100.0 * passes / NPATH, int(np.median(dtp)) if dtp else 0))
    return out


def report(name, tr):
    if not tr:
        print("  %-27s no trades" % name); return
    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
    days = day_blocks(tr); spd = sum(len(d) for d in days) / max(1, len(days))
    m = mc(days)
    v = "PASS" if m[0][0] >= 80 else ("marginal" if m[0][0] >= 40 else "FAIL")
    print("  %-27s | n=%-5d win %4.1f%% | maxDD %5.2f%% | %5.2f/day | pass %3.0f/%3.0f/%3.0f%% | med-days %d/%d/%d [%s]"
          % (name, len(tr), 100 * (net > 0).mean(), maxdd_pct(rs), spd,
             m[0][0], m[1][0], m[2][0], m[0][1], m[1][1], m[2][1], v), flush=True)


def main():
    print("Radar Runner multi-source prop eval @ %.1f%% TP (2025 + 2026-H1). pass%% + median-days @ R0.5/0.75/1.0\n"
          % (TP * 100), flush=True)
    pooled = []
    for name, root, tf, filt in SOURCES:
        tr = source_trades(root, tf, filt)
        report(name, tr)
        pooled.extend(tr)
    pooled.sort(key=lambda t: t[0])
    print("  " + "-" * 108, flush=True)
    report("COMBINED (one account)", pooled)


if __name__ == "__main__":
    main()
