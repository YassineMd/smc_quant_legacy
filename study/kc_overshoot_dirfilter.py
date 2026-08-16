"""Can a DIRECTIONAL (trend) filter rescue the KC Overshoot 2nd entry? Only take the continuation WITH the trend,
evaluated causally at the entry bar. Exit = the scale-out (0.1% SL / TP1 0.3%->BE / TP2 0.5%). All tf except 1m, both
recon years + forward. Filters:
  none       - baseline (no filter)
  e50_price  - short only if close < EMA50 ; long only if close > EMA50
  e100_price - same vs EMA100 (slower regime)
  e50_slope  - short only if EMA50 falling over 10 bars ; long only if rising
A filter is only interesting if it turns exp-R POSITIVE in BOTH 2025 AND 2026 (regime-robust), not one lucky cell.
python study/kc_overshoot_dirfilter.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import kc_overshoot_detect as KC
from study.kc_overshoot_backtest import get_buckets, sim_scaleout, SL_BUF, TP1, FEE, SLIP, H, _f

TP2 = 0.005
TFS = ("5m", "15m", "30m", "1h", "4h")
FILTERS = ("none", "e50_price", "e100_price", "e50_slope")


def ema(vals, length):
    k = 2.0 / (length + 1); out = [0.0] * len(vals)
    if not vals:
        return out
    e = vals[0]; out[0] = e
    for i in range(1, len(vals)):
        e = vals[i] * k + e * (1.0 - k); out[i] = e
    return out


def _pass(filt, side, k, C, e50, e100):
    if filt == "none":
        return True
    if filt == "e50_price":
        return (C[k] < e50[k]) if side < 0 else (C[k] > e50[k])
    if filt == "e100_price":
        return (C[k] < e100[k]) if side < 0 else (C[k] > e100[k])
    if filt == "e50_slope":
        j = max(0, k - 10)
        return (e50[k] < e50[j]) if side < 0 else (e50[k] > e50[j])
    return True


def trades(A, kind, filt):
    n = len(A)
    if n < 120:
        return []
    C = [_f(b.get("close_price")) for b in A]
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = [_f(b.get("start_time")) for b in A]
    Ca = np.array(C); e50 = ema(C, 50); e100 = ema(C, 100)
    ev = [e for e in KC.detect(A, with_failed=(kind == "failed"), skip_last=False) if e.get("kind", "entry") == kind]
    _ib = (lambda z: int(z["i_fail"])) if kind == "failed" else (lambda z: int(z["i_e2"]))
    rows = []; last = -1
    for e in sorted(ev, key=_ib):
        k = _ib(e); side = int(e["side"])
        if k <= last or k + 1 >= n:
            continue
        if not _pass(filt, side, k, C, e50, e100):
            continue
        entry = Ca[k]
        if side < 0:
            sl0 = Hi[k] * (1.0 + SL_BUF); tp1p = entry * (1.0 - TP1); tp2p = entry * (1.0 - TP2); risk = (sl0 - entry) / entry
        else:
            sl0 = Lo[k] * (1.0 - SL_BUF); tp1p = entry * (1.0 + TP1); tp2p = entry * (1.0 + TP2); risk = (entry - sl0) / entry
        if risk <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        gross, outc, off = sim_scaleout(side, entry, sl0, tp1p, tp2p, Hi[j0:j1], Lo[j0:j1], Ca[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp2" else 0.0)
        y = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        rows.append((y, net, net / risk)); last = k + int(off)
    return rows


def er(rows):
    if len(rows) < 10:
        return "n=%-4d  --" % len(rows)
    net = np.array([r[1] for r in rows])
    return "n=%-4d win=%2.0f%% expR=%+.3f" % (len(net), 100 * (net > 0).mean(), np.mean([r[2] for r in rows]))


def main():
    kind = "entry"                                   # rescue the 2nd entry (the primary); reversal was worse
    for tf in TFS:
        print("\n################  TF = %s   (2nd ENTRY, scale-out TP2=0.5%%)  ################" % tf, flush=True)
        recA = get_buckets(tf, forward=False)
        try:
            fwdA = get_buckets(tf, forward=True)
        except Exception:
            fwdA = None
        print("    %-11s %-26s %-26s %s" % ("filter", "2025", "2026", "forward"), flush=True)
        for filt in FILTERS:
            rec = trades(recA, kind, filt)
            r25 = er([r for r in rec if r[0] == 2025]); r26 = er([r for r in rec if r[0] == 2026])
            rfw = er(trades(fwdA, kind, filt)) if fwdA is not None else "--"
            print("    %-11s %-26s %-26s %s" % (filt, r25, r26, rfw), flush=True)


if __name__ == "__main__":
    main()
