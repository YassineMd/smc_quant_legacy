"""HyroTrader 1-Step $200k eval of the recommended combined config (15m clock + 30m clock + 30m bucket).

REAL Hyro rules: target 10%, MAX total 6% TRAILING (from equity high-water mark), DAILY 3%/4% TRAILING (from the day's
intraday equity PEAK, not the day's open). Two sizing modes:
  NOTIONAL (user's actual): enter 10% margin x10 lev = $200k notional = 100% of account -> per-trade account move =
      f * net_price_return (f=1.0 is the user's stated size; f<1 = smaller margin). Wins ~flat +0.23%, losses float with
      the candle stop -> tail risk.
  FIXED-R (risk-capped alt): size so every stop-out loses exactly Rp% of account (eq += Rp*r). Loss capped regardless
      of candle width -> tames the tail.
Day-block MC (resample whole UTC days). python study/radarrun_hyro_prop.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, SLBUF

TARGET, MAXDD = 10.0, 6.0
NPATH, MAXD = 20000, 400
SRCS = [("15m clock", "study/clock_archive", "15m"), ("30m clock", "study/clock_archive", "30m"),
        ("30m bucket", "study/recon_archive", "30m")]


def detect_src(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    return detect(A, SLBUF.get(tf, 0.003))


def day_blocks(tr):
    by = {}
    for ts, net, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append((net, r))
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc(days, mult, daily_lim, mode):
    """mode 'N' -> per-trade acct move = mult*net*100 ; mode 'R' -> mult*r (mult = Rp%)."""
    random.seed(7); passes = 0; dtp = []; dd_reached = []; f_daily = 0; f_max = 0
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False; fd = fm = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for net, r in day:
                eq += (mult * net * 100.0) if mode == "N" else (mult * r)
                peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = fm = True; break
                if ipeak - eq >= daily_lim:
                    failed = fd = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        dd_reached.append(mdd)
        if passed:
            passes += 1; dtp.append(dnum)
        elif fd:
            f_daily += 1
        elif fm:
            f_max += 1
    q = np.percentile(dtp, [10, 50, 90]) if dtp else [0, 0, 0]
    ddq = np.percentile(dd_reached, [50, 90, 99])
    return dict(p=100.0 * passes / NPATH, d10=q[0], d50=q[1], d90=q[2],
                dd50=ddq[0], dd90=ddq[1], dd99=ddq[2], fdaily=100.0 * f_daily / NPATH, fmax=100.0 * f_max / NPATH)


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    print("HyroTrader 1-Step $200k  |  target 10%%  max 6%% (trailing)  daily 3%%/4%% (trailing intraday)\n", flush=True)

    for tp in (0.002, 0.003):
        pooled = []
        for n, *_ in SRCS:
            pooled.extend(eval_tp(*det[n], tp))
        pooled.sort(key=lambda t: t[0])
        days = day_blocks(pooled)
        nets = np.array([t[1] for t in pooled]); rs = np.array([t[2] for t in pooled])
        win = 100.0 * (nets > 0).mean()
        # descriptive per-trade account move at the user's f=1.0 notional
        acct = nets * 100.0
        aw = acct[acct > 0].mean(); al = acct[acct < 0].mean(); worst = acct.min()
        spd = sum(len(d) for d in days) / max(1, len(days))
        print("=" * 96, flush=True)
        print("TP %.1f%%  | n=%d  win %.1f%%  | per-trade acct move @f=1.0: avg win +%.2f%%  avg loss %.2f%%  WORST %.2f%%  | %.1f trd/day"
              % (tp * 100, len(pooled), win, aw, al, worst, spd), flush=True)
        print("-" * 96, flush=True)
        print("  NOTIONAL sizing (your 10%%x10lev = f1.0; f0.75=7.5%% margin; f0.5=5%% margin)", flush=True)
        print("    size | dailycap | pass%% | days p10/med/p90 | trailing-DD med/p90/p99 | fail daily/max", flush=True)
        for f in (1.0, 0.75, 0.5):
            for dl in (3.0, 4.0):
                m = mc(days, f, dl, "N")
                print("    f%.2f |   %.0f%%    | %4.1f%% |  %3.0f / %3.0f / %3.0f  |  %4.1f%% / %4.1f%% / %4.1f%% |  %.1f%% / %.1f%%"
                      % (f, dl, m["p"], m["d10"], m["d50"], m["d90"], m["dd50"], m["dd90"], m["dd99"],
                         m["fdaily"], m["fmax"]), flush=True)
        print("  FIXED-R sizing (risk-capped: every loss = exactly Rp%% of account)", flush=True)
        for Rp in (0.3, 0.4, 0.5):
            for dl in (3.0, 4.0):
                m = mc(days, Rp, dl, "R")
                print("    R%.1f%%|   %.0f%%    | %4.1f%% |  %3.0f / %3.0f / %3.0f  |  %4.1f%% / %4.1f%% / %4.1f%% |  %.1f%% / %.1f%%"
                      % (Rp, dl, m["p"], m["d10"], m["d50"], m["d90"], m["dd50"], m["dd90"], m["dd99"],
                         m["fdaily"], m["fmax"]), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
