"""FADE test in MOROCCAN time (UTC+1, what the terminal shows). User's idea: if the Xam-Moroccan bar is bullish -> enter
bearish next bar (fade). Frozen shipped config: enter next 5m bar's open, SL 0.8%, TP 0.5x the pre-signal (00:00->signal)
range, hold EoD. Tests Moroccan 8/9/10/11am (= 07/08/09/10 UTC). The user's literal '9am Moroccan' = 08:00 UTC.
5m clock, IS(2025)/OOS(2026), prop-MC. python study/ny_9am_fade_moroccan.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_FIX, TP_MULT = 0.0004, 0.0003, 0.008, 0.5
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


def run(D, Hutc):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    tr = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        idxs = sorted(idxs)
        pre = [i for i in idxs if HR[i] < Hutc]                  # 00:00 -> signal hour = the range for the TP
        if not pre:
            continue
        tHi = max(Hi[i] for i in pre); tLo = min(Lo[i] for i in pre)
        bpos = next((p for p, i in enumerate(idxs) if HR[i] == Hutc and MN[i] == 0), None)
        if bpos is None or bpos + 1 >= len(idxs):
            continue
        b = idxs[bpos]; ent = idxs[bpos + 1]; dayend = idxs[-1]
        if C[b] == O[b]:
            continue
        side = -1 if C[b] > O[b] else 1                          # FADE the signal bar
        entry = O[ent]; sl = entry * (1 - side * SL_FIX); tp = entry + side * TP_MULT * (tHi - tLo)
        if ((sl >= entry or tp <= entry) if side > 0 else (sl <= entry or tp >= entry)):
            continue
        net = None
        for j in range(ent + 1, dayend + 1):
            if (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl):
                net = side * (sl - entry) / entry - FEE - 2 * SLIP; break
            if (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp):
                net = side * (tp - entry) / entry - FEE - SLIP; break
        if net is None:
            net = side * (C[dayend] - entry) / entry - FEE - 2 * SLIP
        tr.append((ST[ent], datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, net, SL_FIX))
    return tr


def cell(tr):
    if not tr:
        return "n=0                    "
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def main():
    D = load()
    print("FADE the Xam-MOROCCAN bar (enter next 5m bar, SL 0.8%%, TP 0.5x pre-range, hold EoD) | 5m clock | Moroccan = UTC+1\n", flush=True)
    print("  Moroccan bar        | ALL %-24s | IS %-24s | OOS %-24s | R0.4 pass" % ("", "", ""), flush=True)
    for Hutc, mor in ((7, "8am"), (8, "9am  <- YOUR idea"), (9, "10am <- the edge"), (10, "11am")):
        tr = run(D, Hutc)
        m = mc(day_blocks([(t[0], t[2] / t[3]) for t in tr])[0]) if tr else dict(p=0)
        print("  %-5s Moroccan (%02d UTC)%s | ALL %s | IS %s | OOS %s | %5.1f%%"
              % (mor.split()[0], Hutc, "" if "<-" not in mor else " "+mor.split("<-",1)[1].strip(),
                 cell(tr), cell([t for t in tr if t[1] == 2025]), cell([t for t in tr if t[1] == 2026]), m["p"]), flush=True)


if __name__ == "__main__":
    main()
