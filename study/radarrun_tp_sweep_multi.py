"""TP sweep across each source AND the combined -> find the lowest-DD / shortest-time-to-pass prop config.

Sources: 5m-clock(absorpR>=-0.25) + 15m-clock + 30m-clock + 30m-BUCKET. Detection is TP-independent, so detect each
source ONCE then re-price exits at TP in {0.2,0.3,0.4,0.5}% (candle-SL, fee+slip, taken()-nonoverlap). day-block MC
(target10/maxDD10/daily5). COMBINED = pool all 4 at the same TP on one account. Reports win%/maxDD/trades-day and,
per risk size R0.5/0.75/1.0, pass% + median days-to-pass. python study/radarrun_tp_sweep_multi.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, SLBUF, TARGET, MAXDD, DAILY, MAXD
from study.radarrun_winrate_dd import maxdd_pct
from app import absorption as ABS, config

TPS = [0.002, 0.003, 0.004, 0.005]
RS = [0.5, 0.75, 1.0]
NPATH = 6000
SOURCES = [("5m clock", "study/clock_archive", "5m", True), ("15m clock", "study/clock_archive", "15m", False),
           ("30m clock", "study/clock_archive", "30m", False), ("30m bucket", "study/recon_archive", "30m", False)]


def detect_source(root, tf, filt):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    sigs, Hi, Lo, C, n = detect(A, SLBUF.get(tf, 0.003))
    if filt:
        out = []
        for sg in sigs:
            try:
                a = ABS.absorption(A, sg[0])[0]
            except Exception:
                a = None
            if a is None or a >= config.RR_ABSORPR_MIN:
                out.append(sg)
        sigs = out
    return sigs, Hi, Lo, C, n


def day_blocks(tr):
    by = {}
    for ts, _net, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append((ts, r))
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append([r for _t, r in sorted(by.get(d, []))]); d += timedelta(days=1)
    return out


def mc(days, Rp):
    if not days:
        return 0.0, 0
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
    return 100.0 * passes / NPATH, (int(np.median(dtp)) if dtp else 0)


def line(name, tr):
    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
    days = day_blocks(tr); spd = sum(len(d) for d in days) / max(1, len(days))
    cells = []
    for Rp in RS:
        p, md = mc(days, Rp); cells.append("%3.0f%%/%sd" % (p, md if md else "--"))
    print("  %-13s | n=%-5d win %4.1f%% | DD %5.2f%% | %5.2f/day | %s"
          % (name, len(tr), 100 * (net > 0).mean(), maxdd_pct(rs), spd, "  ".join(cells)), flush=True)


def main():
    det = {name: detect_source(root, tf, filt) for name, root, tf, filt in SOURCES}
    print("TP sweep -- pass%%/median-days at R0.5 / R0.75 / R1.0 (target10/maxDD10/daily5, 2025+2026-H1)\n", flush=True)
    for tp in TPS:
        print("================  TP = %.1f%%  ================" % (tp * 100), flush=True)
        pooled = []
        for name, *_ in SOURCES:
            tr = eval_tp(*det[name], tp)
            line(name, tr); pooled.extend(tr)
        pooled.sort(key=lambda t: t[0])
        line("COMBINED", pooled)
        print("", flush=True)


if __name__ == "__main__":
    main()
