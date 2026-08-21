"""09:00 5m FADE — ENTRY sweep (frozen config: fade the 09:00 bar, SL 0.8%, TP 0.5x Tokyo range, hold EoD). Sweep only
the ENTRY: (A) immediate market @09:05 (baseline); (B) delayed market @09:10/09:15/09:30 (does waiting decay it?);
(C) LIMIT at a better price -- sell higher (short) / buy lower (long) by X%, fill window 09:05-10:00, miss if unfilled
(the untested lever: better R:R vs missed runners, like limit@rlo on the NY break). SL/TP recompute from the actual
fill price. 5m clock, IS(2025)/OOS(2026), prop-MC. python study/ny_9am_fade_entry_sweep.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_FIX, TP_MULT, FILL_END_H = 0.0004, 0.0003, 0.008, 0.5, 10
ROOT, TF = "study/clock_archive", "5m"


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
    T = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        idxs = sorted(idxs)
        tok = [i for i in idxs if HR[i] < 9]
        if not tok:
            continue
        tHi = max(Hi[i] for i in tok); tLo = min(Lo[i] for i in tok)
        bpos = next((p for p, i in enumerate(idxs) if HR[i] == 9 and MN[i] == 0), None)
        if bpos is None or bpos + 1 >= len(idxs):
            continue
        b = idxs[bpos]; ent = idxs[bpos + 1]
        if C[b] == O[b]:
            continue
        side = -1 if C[b] > O[b] else 1                          # FADE
        T.append(dict(ts=ST[ent], yr=datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, side=side,
                      ent=ent, dayend=idxs[-1], tHi=tHi, tLo=tLo))
    return T


def walk(side, entry, sl, tp, start, dayend, C, Hi, Lo):
    for j in range(start, dayend + 1):
        if (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl):
            return side * (sl - entry) / entry - FEE - 2 * SLIP
        if (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp):
            return side * (tp - entry) / entry - FEE - SLIP
    return side * (C[dayend] - entry) / entry - FEE - 2 * SLIP


def sim(t, variant, D):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    s = t["side"]; ent = t["ent"]; de = t["dayend"]
    kind, param = variant
    if kind == "mkt":
        E = ent + param
        if E > de:
            return None
        entry = O[E]; wstart = E + 1
    else:                                                        # limit at a better price (sell higher / buy lower)
        level = O[ent] * (1 - s * param)                         # short: open*(1+X) ; long: open*(1-X)
        E = None
        for j in range(ent, de + 1):
            if HR[j] >= FILL_END_H:
                break
            if (Hi[j] >= level) if s < 0 else (Lo[j] <= level):
                E = j; break
        if E is None:
            return None                                          # limit never filled -> no trade
        entry = level; wstart = E + 1
    sl = entry * (1 - s * SL_FIX); tp = entry + s * TP_MULT * (t["tHi"] - t["tLo"])
    if ((sl >= entry or tp <= entry) if s > 0 else (sl <= entry or tp >= entry)):
        return None
    net = walk(s, entry, sl, tp, wstart, de, C, Hi, Lo)
    return (t["ts"], t["yr"], net, SL_FIX)


def report(name, T, variant, D):
    res = [r for t in T for r in [sim(t, variant, D)] if r is not None]
    if not res:
        print("  %-22s n=0" % name); return

    def cell(rr):
        if not rr:
            return "n=0                    "
        a = np.array([x[2] for x in rr]) * 100.0; rm = np.array([x[2] / x[3] for x in rr])
        return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())
    m = mc(day_blocks([(x[0], x[2] / x[3]) for x in res])[0])
    fill = 100.0 * len(res) / len(T)
    print("  %-22s fill%3.0f%% | ALL %s | IS %s | OOS %s | pass%5.1f%% DD%4.1f%%"
          % (name, fill, cell(res), cell([x for x in res if x[1] == 2025]),
             cell([x for x in res if x[1] == 2026]), m["p"], m["dd99"]), flush=True)


def main():
    D = load(); T = collect(D)
    print("09:00 5m FADE — ENTRY sweep | frozen SL 0.8%% / TP 0.5x Tokyo / hold EoD | n=%d | 5m clock\n" % len(T), flush=True)
    print("== immediate + delayed market ==", flush=True)
    report("market @09:05 (base)", T, ("mkt", 0), D)
    report("market @09:10 (+1)", T, ("mkt", 1), D)
    report("market @09:15 (+2)", T, ("mkt", 2), D)
    report("market @09:30 (+5)", T, ("mkt", 5), D)
    print("\n== LIMIT at a better price (sell higher / buy lower), fill by 10:00 ==", flush=True)
    for x in (0.001, 0.002, 0.003, 0.005):
        report("limit better %.1f%%" % (x * 100), T, ("lim", x), D)


if __name__ == "__main__":
    main()
