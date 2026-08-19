"""Scale-in / average-down test on the OPTIMAL Radar Runner (15m+30m clock + 30m bucket, TP 0.25%, candle-capped SL).

USER IDEA: when price goes against entry, ADD to the position (e.g. +half margin at 0.1% against, +half at 0.2%),
banking on the ~90% win rate. Two parts:

  PART 1 - ADVERSE SELECTION: for every trade, the max adverse excursion (MAE) it reached before resolving, vs whether
    it won (hit TP). If trades that dip 0.1%/0.2% win LESS often than the base rate, averaging down loads size into the
    losers -> a NEGATIVE edge. This is the make-or-break diagnostic.

  PART 2 - P&L: honest sim of the add ladder. Base position sized so the SL loss = $800 (the OPTIMAL base). Adds are
    extra notional (fraction of base) at adverse triggers; ALL tranches share the candle SL (fixed price) and exit
    together at the 0.25% TP or the SL. r = total_net_usd / $800 (so base-only reproduces the OPTIMAL trade). Then the
    same HyroTrader day-block MC (target10/max6%/daily3% trailing) at full + 70% manual capture.
Within-bar convention: adverse-first (adds fill + SL before TP on a same-bar touch) = conservative on losers.
python study/radarrun_scalein_test.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, SRCS, TARGET, MAXDD, MAXD
from study.radarrun_proptp_alltf_clock import SLBUF

FEE, SLIP, TP_FRAC, RISK_USD = 0.0004, 0.0003, 0.0025, 800.0
H = 200; NPATH = 12000
PROBES = [0.001, 0.002, 0.003]                 # adverse depths to profile in Part 1


def walk(sigs, Hi, Lo, C, n, adds):
    """Return (trades, diag). trades = [(ts, r_total)]. diag rows = (mae_frac, won) per trade (add-independent)."""
    tr = []; diag = []; last = -1
    for (k, s, entry, sl, dist, ts) in sigs:
        if k <= last:
            continue
        tp = entry * (1.0 + s * TP_FRAC)
        N0 = RISK_USD / dist                                    # base notional s.t. N0*dist = $800 risk
        lv = []
        for (tf, sf) in adds:
            apx = entry * (1.0 - s * tf)
            beyond = (apx <= sl) if s > 0 else (apx >= sl)      # add level past the stop never fills
            if not beyond:
                lv.append([apx, sf * N0, False])
        j0 = k + 1; j1 = min(n, k + 1 + H); exit_px = None; kind = "to"; endj = j1 - 1; mae = 0.0
        for j in range(j0, j1):
            hh = Hi[j]; ll = Lo[j]
            if hh <= 0 or ll <= 0:
                continue
            adverse = ll if s > 0 else hh
            mae = max(mae, s * (entry - adverse) / entry)        # how far against (fraction, >=0)
            for L in lv:
                if not L[2] and ((adverse <= L[0]) if s > 0 else (adverse >= L[0])):
                    L[2] = True
            if (ll <= sl) if s > 0 else (hh >= sl):
                exit_px = sl; kind = "sl"; endj = j; break
            if (hh >= tp) if s > 0 else (ll <= tp):
                exit_px = tp; kind = "tp"; endj = j; break
        if exit_px is None:
            exit_px = C[j1 - 1]; endj = j1 - 1
        last = endj
        feef = FEE + SLIP + (SLIP if kind != "tp" else 0.0)

        def tn(Ei, Ni):
            return Ni * (s * (exit_px - Ei) / Ei) - Ni * feef
        net = tn(entry, N0)
        for L in lv:
            if L[2]:
                net += tn(L[0], L[1])
        tr.append((ts, net / RISK_USD))
        diag.append((mae, 1 if kind == "tp" else 0))
    return tr, diag


def day_blocks(tr):
    from datetime import datetime, timezone, timedelta
    by = {}
    for ts, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc(days, cap, daily=3.0, R=0.4):
    random.seed(7); p = 0; f = 0; dtp = []; ddr = []
    for _ in range(NPATH):
        eq = pk = 0.0; md = 0.0; passed = failed = False
        for dn in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ip = eq
            for r in day:
                if cap < 1.0 and random.random() > cap:
                    continue
                eq += R * r; pk = max(pk, eq); ip = max(ip, eq); md = max(md, pk - eq)
                if pk - eq >= MAXDD or ip - eq >= daily:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        if passed:
            p += 1; dtp.append(dn); ddr.append(md)
        elif failed:
            f += 1
    dq = np.percentile(dtp, [50, 90]) if dtp else [0, 0]
    return dict(p=100.0 * p / NPATH, fails=f, med=dq[0], p90=dq[1], dd99=(np.percentile(ddr, 99) if ddr else 0))


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}

    # ---- PART 1: adverse selection ----
    alld = []
    for n, *_ in SRCS:
        _tr, d = walk(*det[n], [])
        alld.extend(d)
    mae = np.array([x[0] for x in alld]); won = np.array([x[1] for x in alld])
    base = 100.0 * won.mean()
    print("PART 1 - ADVERSE SELECTION  (n=%d, base win rate %.1f%%)\n" % (len(alld), base), flush=True)
    print("  If price goes X against you first, does it still win as often? (win = hit the 0.25%% TP)", flush=True)
    print("  adverse dip | %% of trades | win rate | vs base", flush=True)
    for pr in PROBES:
        m = mae >= pr
        if m.sum() == 0:
            continue
        wr = 100.0 * won[m].mean()
        print("   >= %.2f%%    |   %5.1f%%    |  %5.1f%%  | %+.1f pp" % (pr * 100, 100.0 * m.mean(), wr, wr - base), flush=True)
    nd = mae < PROBES[0]
    print("   (no dip)    |   %5.1f%%    |  %5.1f%%  | %+.1f pp" % (100.0 * nd.mean(), 100.0 * won[nd].mean(),
                                                                   100.0 * won[nd].mean() - base), flush=True)

    # ---- PART 2: scale-in P&L ----
    CONFIGS = [
        ("BASELINE (no adds)", []),
        ("+0.5 @0.1 (single)", [(0.001, 0.5)]),
        ("+0.5 @0.2 (single)", [(0.002, 0.5)]),
        ("+0.25 @0.1 (single)", [(0.001, 0.25)]),
        ("+0.25 @0.2 (single)", [(0.002, 0.25)]),
        ("USER +0.5@0.1 +0.5@0.2", [(0.001, 0.5), (0.002, 0.5)]),
    ]
    print("\nPART 2 - SCALE-IN P&L  (base sized so SL = $800; adds are extra notional; r = net/$800)\n", flush=True)
    print("  %-24s | mean r  | worst r | ret/DD | pass%%@100 (fails) med/p90 DDp99 | pass%%@70cap med/p90 DDp99"
          % "config", flush=True)
    print("  " + "-" * 126, flush=True)
    for name, adds in CONFIGS:
        pooled = []
        for n, *_ in SRCS:
            t, _ = walk(*det[n], adds); pooled.extend(t)
        pooled.sort(key=lambda z: z[0])
        rs = np.array([z[1] for z in pooled])
        days = day_blocks(pooled)
        m100 = mc(days, 1.0); m70 = mc(days, 0.7)
        eff = 100.0 * rs.mean() / m100["dd99"] if m100["dd99"] > 0 else 0.0   # return per unit drawdown (edge, not leverage)
        print("  %-24s | %+.3f  | %+.2f   | %5.2f  | %5.1f%% (%4d) %3.0f/%3.0f %.1f%%   | %5.1f%% %3.0f/%3.0f %.1f%%"
              % (name, rs.mean(), rs.min(), eff, m100["p"], m100["fails"], m100["med"], m100["p90"], m100["dd99"],
                 m70["p"], m70["med"], m70["p90"], m70["dd99"]), flush=True)


if __name__ == "__main__":
    main()
