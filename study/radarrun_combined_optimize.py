"""Optimize the COMBINED portfolio: give each source its OWN best TP (highest return-per-drawdown = fast+safe), then
pool them onto one account and compare vs the uniform-TP combined.

Per source x TP{0.2..0.5%}: win% / maxDD(@R0.5) / total-gain%(@R0.5) / Calmar(gain/DD) / solo-median-days(@R0.5). Pick
each source's max-Calmar TP (best risk-adjusted = fastest per unit drawdown). Build the MIXED-TP combined + report
pass%/median-days/maxDD @R0.5/0.75/1.0, alongside uniform @0.2% and @0.3%. (Per-source pick is a 4-option choice, not
a joint grid -> mild overfit only.) python study/radarrun_combined_optimize.py"""
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
NPATH = 6000
SOURCES = [("5m clock", "study/clock_archive", "5m", True), ("15m clock", "study/clock_archive", "15m", False),
           ("30m clock", "study/clock_archive", "30m", False), ("30m bucket", "study/recon_archive", "30m", False)]


def detect_source(root, tf, filt):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    sigs, Hi, Lo, C, n = detect(A, SLBUF.get(tf, 0.003))
    if filt:
        sigs = [sg for sg in sigs if (lambda a: a is None or a >= config.RR_ABSORPR_MIN)(_safe(A, sg[0]))]
    return sigs, Hi, Lo, C, n


def _safe(A, k):
    try:
        return ABS.absorption(A, k)[0]
    except Exception:
        return None


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


def summarize(name, tr, rs_list=(0.5, 0.75, 1.0)):
    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
    days = day_blocks(tr); spd = sum(len(d) for d in days) / max(1, len(days))
    cells = "  ".join("%3.0f%%/%sd" % (p, md if md else "--") for p, md in (mc(days, Rp) for Rp in rs_list))
    print("  %-22s | n=%-5d win %4.1f%% | DD %5.2f%% | %5.2f/day | %s"
          % (name, len(tr), 100 * (net > 0).mean(), maxdd_pct(rs), spd, cells), flush=True)


def main():
    det = {name: detect_source(root, tf, filt) for name, root, tf, filt in SOURCES}
    best_tp = {}
    print("PER-SOURCE TP grid  (win / DD@R0.5 / gain%@R0.5 / Calmar=gain/DD / solo-days@R0.5)  * = max-Calmar pick\n", flush=True)
    for name, *_ in SOURCES:
        print("  %s:" % name, flush=True)
        best = None
        for tp in TPS:
            tr = eval_tp(*det[name], tp)
            rs = [t[2] for t in tr]; dd = maxdd_pct(rs)
            gain = 0.5 * float(np.sum(rs))                    # total account gain % at R0.5 over the whole history
            calmar = gain / dd if dd > 0 else 0.0
            _p, md = mc(day_blocks(tr), 0.5)
            if best is None or calmar > best[1]:
                best = (tp, calmar)
            print("     TP %.1f%%  win %4.1f%%  DD %5.2f%%  gain %+6.0f%%  Calmar %5.1f  solo-days %d"
                  % (tp * 100, 100 * (np.array([t[1] for t in tr]) > 0).mean(), dd, gain, calmar, md), flush=True)
        best_tp[name] = best[0]
        print("     -> best (max-Calmar) TP = %.1f%%\n" % (best[0] * 100), flush=True)

    print("=" * 70, flush=True)
    print("COMBINED, each source at its BEST-Calmar TP: %s" % ", ".join(
        "%s@%.1f%%" % (nm, best_tp[nm] * 100) for nm, *_ in SOURCES), flush=True)
    pooled = []
    for name, *_ in SOURCES:
        pooled.extend(eval_tp(*det[name], best_tp[name]))
    pooled.sort(key=lambda t: t[0])
    summarize("MIXED-TP combined", pooled)
    for utp in (0.002, 0.003):
        pu = []
        for name, *_ in SOURCES:
            pu.extend(eval_tp(*det[name], utp))
        pu.sort(key=lambda t: t[0])
        summarize("uniform @%.1f%%" % (utp * 100), pu)
    # SAFE-FAST mix: 5m at its LOW-DD 0.2% (it's the DD liability), 30m-bucket+15m at their efficient 0.3%, 30m-clock ballast 0.2%
    sf = {"5m clock": 0.002, "15m clock": 0.003, "30m clock": 0.002, "30m bucket": 0.003}
    ps = []
    for name, *_ in SOURCES:
        ps.extend(eval_tp(*det[name], sf[name]))
    ps.sort(key=lambda t: t[0])
    summarize("SAFE-FAST (5m.2/15m.3/30mc.2/30mb.3)", ps)


if __name__ == "__main__":
    main()
