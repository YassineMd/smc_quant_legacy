"""09:00 5m FADE — STOP sweep on the DIRECT entry (immediate, preserves the 57.6% reversion edge). Wide/adaptive TP fixed;
sweep the SL from tight to the full Tokyo range to find the R:R that makes avgR (the prop-relevant metric) positive while
keeping OOS expectancy positive. Signal: fade the 09:00 5m bar; entry = 09:05 open. Stops: fixed 0.3/0.5/0.8/1.0%, 0.1%
past the 09:00 bar extreme, 0.25x/0.5x Tokyo range, full Tokyo range (baseline). TP = 0.5x / 1.0x Tokyo range. Hold EoD.
5m clock, IS(2025)/OOS(2026), prop-MC HyroTrader $200k R0.4. python study/ny_9am_fade_stop_sweep.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, BARPAD = 0.0004, 0.0003, 0.001
H = 9; ROOT, TF = "study/clock_archive", "5m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, MN, DATE, WD, n


def collect(D):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    trades = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        idxs = sorted(idxs)
        tok = [i for i in idxs if HR[i] < H]
        if not tok:
            continue
        tHi = max(Hi[i] for i in tok); tLo = min(Lo[i] for i in tok)
        bpos = next((p for p, i in enumerate(idxs) if HR[i] == H and MN[i] == 0), None)
        if bpos is None or bpos + 1 >= len(idxs):
            continue
        b = idxs[bpos]; ent = idxs[bpos + 1]
        if C[b] == O[b]:
            continue
        side = -1 if C[b] > O[b] else 1                           # FADE
        trades.append(dict(ts=ST[ent], yr=datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, side=side,
                           entry=O[ent], bHi=Hi[b], bLo=Lo[b], tHi=tHi, tLo=tLo,
                           seq=[j for j in idxs if j > ent]))
    return trades


def sl_price(t, kind):
    e = t["entry"]; s = t["side"]
    if isinstance(kind, float):
        return e * (1 - s * kind)
    if kind == "bar":
        return t["bLo"] * (1 - BARPAD) if s > 0 else t["bHi"] * (1 + BARPAD)
    if kind == "tokfull":
        return t["tLo"] * (1 - BARPAD) if s > 0 else t["tHi"] * (1 + BARPAD)
    if kind.startswith("tok"):
        f = float(kind[3:]); return e - s * f * (t["tHi"] - t["tLo"])
    return e


def tp_price(t, kind):
    return t["entry"] + t["side"] * float(kind[1:]) * (t["tHi"] - t["tLo"])   # r-mult of Tokyo range


def sim(t, stopk, tpk, C, Hi, Lo):
    e = t["entry"]; s = t["side"]; sl = sl_price(t, stopk); tp = tp_price(t, tpk)
    if ((sl >= e or tp <= e) if s > 0 else (sl <= e or tp >= e)):
        return None
    sld = abs(sl - e) / e
    for j in t["seq"]:
        if (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl):
            return s * (sl - e) / e - FEE - 2 * SLIP, sld
        if (Hi[j] >= tp) if s > 0 else (Lo[j] <= tp):
            return s * (tp - e) / e - FEE - SLIP, sld
    ex = C[t["seq"][-1]] if t["seq"] else e
    return s * (ex - e) / e - FEE - 2 * SLIP, sld


def report(name, trades, stopk, tpk, C, Hi, Lo):
    res = []; slds = []
    for t in trades:
        r = sim(t, stopk, tpk, C, Hi, Lo)
        if r is not None:
            res.append((t["ts"], t["yr"], r[0], r[1])); slds.append(r[1])
    if not res:
        print("  %-16s n=0" % name); return

    def cell(rr):
        if not rr:
            return "n=0                    "
        a = np.array([x[2] for x in rr]) * 100.0; rm = np.array([x[2] / x[3] for x in rr])
        return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())
    m = mc(day_blocks([(x[0], x[2] / x[3]) for x in res])[0])
    print("  %-16s SLd%4.2f%% | ALL %s | IS %s | OOS %s | pass%5.1f%% DD%4.1f%%"
          % (name, np.mean(slds) * 100, cell(res), cell([x for x in res if x[1] == 2025]),
             cell([x for x in res if x[1] == 2026]), m["p"], m["dd99"]), flush=True)


def main():
    D = load(); O, C, Hi, Lo = D[0], D[1], D[2], D[3]
    trades = collect(D)
    print("09:00 5m FADE — STOP sweep, DIRECT entry | n=%d | hold EoD | 5m clock\n" % len(trades), flush=True)
    STOPS = [(0.003, "fixed 0.3%"), (0.005, "fixed 0.5%"), (0.008, "fixed 0.8%"), (0.010, "fixed 1.0%"),
             ("bar", "0.1% past 9am bar"), ("tok0.25", "0.25x Tokyo"), ("tok0.5", "0.5x Tokyo"), ("tokfull", "full Tokyo (base)")]
    for tpk, tpn in (("r0.5", "TP = 0.5x Tokyo range"), ("r1.0", "TP = 1.0x Tokyo range")):
        print("== %s ==" % tpn, flush=True)
        for sk, sn in STOPS:
            report(sn, trades, sk, tpk, C, Hi, Lo)
        print("", flush=True)


if __name__ == "__main__":
    main()
