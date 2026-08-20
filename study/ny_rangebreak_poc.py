"""NY Range-break SHORT + POC RE-ENTRY (user recall: re-enter on the retrace at the range POC to boost win rate). Break
short (close<rlo) at break close = entry1; POC = range-window VWAP (volume-weighted center; fallback range-body midpoint)
-- a level ABOVE entry1, inside the range. On the retrace UP, ADD a 2nd short at the POC; blend the avg entry and RECOMPUTE
the vol-adaptive TP from the average (easier to hit). Shared SL 0.1%% beyond the range wick. Compare BASELINE (single
entry) vs POC-REENTRY: win%%, avg per-unit net, add-fill%%, OOS. Stop-first (pessimistic: adverse/up move first). SHORT
side only (the edge). clock 5m/15m/30m/1h + bucket 15m/30m/1h. IN-SAMPLE. python study/ny_rangebreak_poc.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR = 0.0004, 0.0003, 0.001, 2.85
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600
CELLS = [("clock", "study/clock_archive", "5m"), ("clock", "study/clock_archive", "15m"),
         ("clock", "study/clock_archive", "30m"), ("clock", "study/clock_archive", "1h"),
         ("bucket", "study/recon_archive", "15m"), ("bucket", "study/recon_archive", "30m"),
         ("bucket", "study/recon_archive", "1h")]


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n); V = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        V[i] = _f(b.get("volume", b.get("vol", b.get("v", 0))) or 0)
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, V, HR, DATE, WD, n


def sim(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry):
    """SHORT (s=-1). returns (net_per_unit, outcome, add_filled). Stop-first; add fills on retrace UP to poc."""
    tpmult = 2.0 if rngpct < TP_THR else 0.5
    filled = False; et = ST[k]
    def avg():
        return (entry1 + poc) / 2.0 if filled else entry1
    def tp():
        return avg() - tpmult * rng                                  # short target below the (blended) avg
    def out(exitp):
        # per-unit net = mean of the units' returns, one round-trip fee per unit averaged (~ FEE+SLIP), + exit slip if not TP
        g1 = (entry1 - exitp) / entry1
        if filled:
            g2 = (poc - exitp) / poc; g = 0.5 * (g1 + g2)
        else:
            g = g1
        return g
    for j in range(k + 1, n):
        if ST[j] > et + MAXHOLD:
            return out(C[j - 1]) - FEE - SLIP - SLIP, "end", filled
        hi = Hi[j]; lo = Lo[j]
        if reentry and not filled and hi >= poc:                     # retrace UP to POC -> add the 2nd short
            filled = True
        if hi >= sl:                                                 # stopped (all units), above the range wick
            return out(sl) - FEE - SLIP - SLIP, "sl", filled
        if lo <= tp():                                               # TP (all units), below the blended avg
            return out(tp()) - FEE - SLIP, "tp", filled
    return out(C[-1]) - FEE - SLIP - SLIP, "end", filled


def backtest(root, tf, reentry):
    O, C, Hi, Lo, ST, V, HR, DATE, WD, n = load_arrays(root, tf)
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    tr = []; nfill = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        rng_idx = [i for i in idxs if HR[i] in R_HRS]; brk_idx = sorted([i for i in idxs if HR[i] in B_HRS])
        if not rng_idx or not brk_idx:
            continue
        rlo = min(min(O[i], C[i]) for i in rng_idx); rhi = max(max(O[i], C[i]) for i in rng_idx)
        whi = max(Hi[i] for i in rng_idx); wlo = min(Lo[i] for i in rng_idx); rng = whi - wlo
        if rng <= 0:
            continue
        vsum = sum(V[i] for i in rng_idx)
        poc = (sum(((Hi[i] + Lo[i] + C[i]) / 3.0) * V[i] for i in rng_idx) / vsum) if vsum > 0 else (rhi + rlo) / 2.0
        k = None
        for i in brk_idx:                                            # SHORT break only (the edge)
            if C[i] < rlo:
                k = i; break
        if k is None:
            continue
        entry1 = C[k]; rngpct = 100.0 * rng / entry1; sl = whi * (1 + SL_PAD)
        if poc <= entry1:                                            # POC must be a retrace-UP level for a short
            poc = (rhi + rlo) / 2.0
        net, outc, filled = sim(entry1, poc, sl, rng, rngpct, ST, Hi, Lo, C, k, n, reentry)
        if filled:
            nfill += 1
        tr.append((net, datetime.fromtimestamp(ST[k], tz=timezone.utc).year))
    return tr, nfill


def st(tr, yr=None):
    r = [t for t in tr if (yr is None or t[1] == yr)]
    if not r:
        return "n=0"
    a = np.array([t[0] for t in r]) * 100.0
    return "n=%-4d win%4.1f%% exp%+.3f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())


def main():
    print("NY RANGE-BREAK SHORT + POC RE-ENTRY vs single-entry baseline | POC=range VWAP | OOS | IN-SAMPLE\n", flush=True)
    for dsname, root, tf in CELLS:
        base, _ = backtest(root, tf, reentry=False)
        poc, nf = backtest(root, tf, reentry=True)
        if not base:
            print("  %-6s %-4s no trades" % (dsname, tf)); continue
        print("  %-6s %-4s  BASELINE  ALL %s | IS %s | OOS %s" % (dsname, tf, st(base), st(base, 2025), st(base, 2026)), flush=True)
        print("           +POC-RE  (add-fill %.0f%%)  ALL %s | IS %s | OOS %s"
              % (100.0 * nf / max(1, len(poc)), st(poc), st(poc, 2025), st(poc, 2026)), flush=True)
    print("", flush=True)


if __name__ == "__main__":
    main()
