"""Scale-in with a DYNAMIC TP (the user's correction): when you add, the TP is recomputed at 0.25% from the NEW
volume-weighted AVERAGE entry -> the target moves toward price and gets easier to hit (vs my earlier test that wrongly
pinned TP at the original entry +0.25%). SL stays the candle-capped fixed price. Shows FIXED-TP vs DYN-TP side by side.

Sizing: base notional N0 = $800/SL-dist (base risk $800). Adds = extra notional (fraction of base) at adverse triggers,
all sharing the SL, exiting together at the current TP. At a DYN-TP exit, gross = 0.25% * total_notional (0.25% on the
whole blended position). r = total_net_usd / $800. HyroTrader day-block MC (target10/max6%/daily3% trailing), full
capture. python study/radarrun_scalein_dyntp.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, SRCS, TARGET, MAXDD, MAXD

FEE, SLIP, TP_FRAC, RISK_USD = 0.0004, 0.0003, 0.0025, 800.0
H = 200; NPATH = 12000


def walk(sigs, Hi, Lo, C, n, adds, dyn):
    tr = []; last = -1; wins = 0; tot = 0
    for (k, s, entry, sl, dist, ts) in sigs:
        if k <= last:
            continue
        N0 = RISK_USD / dist; total_qty = N0 / entry; total_cost = N0     # cost = sum of tranche notionals
        pend = []
        for (tf, sf) in adds:
            apx = entry * (1.0 - s * tf)
            beyond = (apx <= sl) if s > 0 else (apx >= sl)
            if not beyond:
                pend.append([apx, sf * N0, False])
        fixed_tp = entry * (1.0 + s * TP_FRAC)

        def cur_tp():
            if dyn:
                return (total_cost / total_qty) * (1.0 + s * TP_FRAC)      # 0.25% off the blended avg entry
            return fixed_tp
        j0 = k + 1; j1 = min(n, k + 1 + H); exit_px = None; kind = "to"; endj = j1 - 1
        for j in range(j0, j1):
            hh = Hi[j]; ll = Lo[j]
            if hh <= 0 or ll <= 0:
                continue
            adverse = ll if s > 0 else hh
            fav = hh if s > 0 else ll
            for L in pend:                                                 # adverse-first: fill adds on the dip
                if not L[2] and ((adverse <= L[0]) if s > 0 else (adverse >= L[0])):
                    L[2] = True; total_qty += L[1] / L[0]; total_cost += L[1]
            if (ll <= sl) if s > 0 else (hh >= sl):                        # SL (fixed candle-capped)
                exit_px = sl; kind = "sl"; endj = j; break
            tpn = cur_tp()                                                 # TP checked AFTER adds -> uses new blend
            if (hh >= tpn) if s > 0 else (ll <= tpn):
                exit_px = tpn; kind = "tp"; endj = j; break
        if exit_px is None:
            exit_px = C[j1 - 1]; endj = j1 - 1
        last = endj
        feef = FEE + SLIP + (SLIP if kind != "tp" else 0.0)
        fee_notional = N0 + sum(L[1] for L in pend if L[2])
        net = s * (exit_px * total_qty - total_cost) - fee_notional * feef
        tr.append((ts, net / RISK_USD)); tot += 1; wins += 1 if net > 0 else 0
    return tr, (100.0 * wins / max(1, tot))


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


def mc(days, R=0.4, daily=3.0):
    random.seed(7); p = 0; f = 0; dtp = []; ddr = []
    for _ in range(NPATH):
        eq = pk = 0.0; md = 0.0; passed = failed = False
        for dn in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ip = eq
            for r in day:
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
    med = int(np.median(dtp)) if dtp else 0
    return dict(p=100.0 * p / NPATH, fails=f, med=med, dd99=(np.percentile(ddr, 99) if ddr else 0))


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    CONFIGS = [
        ("BASELINE (no adds)", []),
        ("+0.5 @0.1 (single)", [(0.001, 0.5)]),
        ("+0.5 @0.2 (single)", [(0.002, 0.5)]),
        ("USER +0.5@0.1 +0.5@0.2", [(0.001, 0.5), (0.002, 0.5)]),
        ("AGGR +1@0.1 +1@0.2", [(0.001, 1.0), (0.002, 1.0)]),
    ]
    print("Scale-in with DYNAMIC TP (0.25%% off blended avg entry) vs FIXED TP (old, wrong) | full capture\n", flush=True)
    print("  %-24s | TP mode | win%% | mean r  | worst r | ret/DD | pass%% (fails) | DD p99 | med days"
          % "config", flush=True)
    print("  " + "-" * 116, flush=True)
    for name, adds in CONFIGS:
        for dyn in (False, True):
            pooled = []; wr = 0.0
            for nm, *_ in SRCS:
                t, w = walk(*det[nm], adds, dyn); pooled.extend(t); wr = w if nm == SRCS[-1][0] else wr
            pooled.sort(key=lambda z: z[0])
            rs = np.array([z[1] for z in pooled])
            wr = 100.0 * (rs > 0).mean()
            days = day_blocks(pooled); m = mc(days)
            eff = 100.0 * rs.mean() / m["dd99"] if m["dd99"] > 0 else 0.0
            tag = "DYN " if dyn else "fixed"
            star = "  <--" if (dyn and eff > 2.87) else ""
            print("  %-24s | %s   | %4.1f | %+.3f  | %+.2f   | %5.2f  | %5.1f%% (%4d) | %4.1f%%  | %3d%s"
                  % (name, tag, wr, rs.mean(), rs.min(), eff, m["p"], m["fails"], m["dd99"], m["med"], star), flush=True)
            if name == "BASELINE (no adds)":
                break                                                       # baseline identical both modes
        print("", flush=True)


if __name__ == "__main__":
    main()
