"""DRAWDOWN / prop-firm feasibility for the 1h Radar Runner (cand+0.3cap SL, TP=0.5%, slip=3bps).

FIXED-FRACTIONAL sizing: each trade sized to lose R% of the account at its stop. acct_return = R * net/dist (net = the
sim's fee+slip net price return, dist = that trade's SL distance). Equity is ADDITIVE in % of the initial balance (how a
prop challenge reads). Reports, per risk R:
  * HISTORICAL (actual 2-yr order): max trailing DD, max static loss-from-start, worst day, longest losing streak, total.
  * MONTE-CARLO by-DAY block bootstrap (preserves intraday clustering): distribution of max DD, and PASS-rate under two
    common rule templates. NOTE: bootstrap can UNDER-state clustered-regime drawdowns; the historical path is the anchor.
NOT trading advice -- backtest statistics on RECON data (not live fills). CLI: python study/radarrun_1h_drawdown.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; H = 200
TP = 0.005; SLIP = 0.0003; random.seed(12345); np.random.seed(12345)
RISKS = [0.005, 0.0075, 0.010]          # risk per trade (fraction of account)
TEMPLATES = [("2-step 10/5/10", 0.10, 0.05, 0.10, "static"),
             ("1-step 6/4/9", 0.06, 0.04, 0.09, "trail")]   # (name, totalDD, dailyDD, target, dd_mode)
NMC = 4000


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def build_trades():
    A = sorted(load_archive("1h", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])

    ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += 5000

    trades = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.003), rlo) if up else min(Hi[k] * (1 + 0.003), rhi)   # cand+0.3cap
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        day = int(ST[k] // 86400)
        yr = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        trades.append({"k": k, "day": day, "year": yr, "acct1": net / dist})   # acct return per 1.0 unit of R
        last = k + int(off)
    return trades


def max_dd(equity):                       # trailing peak-to-trough and static loss-from-start (0)
    peak = 0.0; tdd = 0.0; sdd = 0.0
    for e in equity:
        peak = max(peak, e); tdd = max(tdd, peak - e); sdd = max(sdd, -e)
    return tdd, sdd


def hist_stats(trades, R):
    acc = np.array([t["acct1"] * R for t in trades])
    eq = np.cumsum(acc)
    tdd, sdd = max_dd(eq)
    # worst day
    byday = {}
    for t in trades:
        byday.setdefault(t["day"], 0.0)
        byday[t["day"]] += t["acct1"] * R
    worst_day = min(byday.values()) if byday else 0.0
    # longest losing streak (consecutive negative trades)
    ls = mx = 0
    for a in acc:
        ls = ls + 1 if a < 0 else 0; mx = max(mx, ls)
    return eq[-1], tdd, sdd, worst_day, mx


def mc_pass(days_list, R, total_dd, daily_dd, target, mode, ntrials=NMC, max_days=300):
    npass = fdaily = ftotal = 0; ttp = []
    idx = np.arange(len(days_list))
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; done = False; nt = 0
        pick = np.random.randint(0, len(idx), size=max_days)
        for di in pick:
            dstart = eq; dmin = eq
            for a1 in days_list[di]:
                eq += a1 * R; nt += 1
                peak = max(peak, eq); dmin = min(dmin, eq)
                dd = (peak - eq) if mode == "trail" else (-eq)
                if dd >= total_dd:
                    ftotal += 1; done = True; break
                if (dstart - eq) >= daily_dd:
                    fdaily += 1; done = True; break
                if eq >= target:
                    npass += 1; ttp.append(nt); done = True; break
            if done:
                break
        if not done:                       # ran out of days without passing/failing -> treat as not passed
            pass
    return npass / ntrials, ftotal / ntrials, fdaily / ntrials, (np.median(ttp) if ttp else float("nan"))


def mc_maxdd(days_list, R, ntrials=2000, max_days=300):
    out = []
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; tdd = 0.0
        pick = np.random.randint(0, len(days_list), size=max_days)
        for di in pick:
            for a1 in days_list[di]:
                eq += a1 * R; peak = max(peak, eq); tdd = max(tdd, peak - eq)
        out.append(tdd)
    return np.percentile(out, [50, 90, 99])


def main():
    trades = build_trades()
    ny = {}
    for t in trades:
        ny[t["year"]] = ny.get(t["year"], 0) + 1
    ndays = len(set(t["day"] for t in trades))
    print("1h Radar Runner (cand+0.3cap SL, TP=0.5%%, slip=3bps)  trades=%d  [%s]  active-days=%d"
          % (len(trades), ", ".join("%d:%d" % (y, ny[y]) for y in sorted(ny)), ndays), flush=True)
    exp = np.mean([t["acct1"] for t in trades])
    winrate = 100 * np.mean([1.0 if t["acct1"] > 0 else 0.0 for t in trades])
    span_days = (max(t["day"] for t in trades) - min(t["day"] for t in trades)) + 1
    print("  per-trade expectancy = %+.3f R   win-rate = %.0f%%   trades/day = %.2f   (R = risk-per-trade, %% of account)"
          % (exp, winrate, len(trades) / span_days))
    # build day blocks
    byday = {}
    for t in trades:
        byday.setdefault(t["day"], []).append(t["acct1"])
    days_list = list(byday.values())

    for R in RISKS:
        tot, tdd, sdd, worst_day, streak = hist_stats(trades, R)
        p50, p90, p99 = mc_maxdd(days_list, R)
        print("\n  ===== RISK R = %.2f%% per trade =====" % (R * 100), flush=True)
        print("    HISTORICAL (actual 2-yr path): total=%+.1f%%  maxDD(trailing)=%.1f%%  maxLoss-from-start=%.1f%%  "
              "worst-day=%.1f%%  longest-losing-streak=%d" % (tot * 100, tdd * 100, sdd * 100, worst_day * 100, streak),
              flush=True)
        print("    MC max trailing DD:  median=%.1f%%  90th=%.1f%%  99th=%.1f%%" % (p50 * 100, p90 * 100, p99 * 100),
              flush=True)
        for name, tdl, ddl, tgt, mode in TEMPLATES:
            pp, ft, fd, med = mc_pass(days_list, R, tdl, ddl, tgt, mode)
            print("    %-16s (DD %.0f%% / daily %.0f%% / target %.0f%%): PASS=%.0f%%  fail-total=%.0f%%  "
                  "fail-daily=%.0f%%  median-trades-to-pass=%s"
                  % (name, tdl * 100, ddl * 100, tgt * 100, 100 * pp, 100 * ft, 100 * fd,
                     ("%.0f" % med) if med == med else "n/a"), flush=True)


if __name__ == "__main__":
    main()
