"""ROBUSTNESS battery for the 09:00 5m fade candidate. FROZEN geometry (hour-independent): SL fixed 0.8%, TP fixed 1.0%
net, direct entry (H:05), hold EoD, 5m clock. Tests: (1) HOURS null -- apply the SAME config to fading the H:00 bar for
H=6..14; is 09:00 special or does any morning bar work (=> geometry, not a 09:00 edge)? (2) WITH-check at 09:00 (same
geometry, continuation not fade) -- must be NEGATIVE if the edge is directional, not geometric. (3) placebo (always-long
+ always-short, same geometry) -- the pure geometric baseline. IS(2025)/OOS(2026), prop-MC. python study/ny_9am_fade_robustness.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_FIX, TP_FIX = 0.0004, 0.0003, 0.008, 0.010
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


def trades(D, Hh, mode):
    """mode: 'fade' / 'with' / 'long' / 'short'. Signal = the Hh:00 5m bar; entry = next 5m bar. Frozen fixed SL/TP."""
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    out = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        idxs = sorted(idxs)
        bpos = next((p for p, i in enumerate(idxs) if HR[i] == Hh and MN[i] == 0), None)
        if bpos is None or bpos + 1 >= len(idxs):
            continue
        b = idxs[bpos]; ent = idxs[bpos + 1]
        if C[b] == O[b]:
            continue
        bull = C[b] > O[b]
        if mode == "fade":
            side = -1 if bull else 1
        elif mode == "with":
            side = 1 if bull else -1
        elif mode == "long":
            side = 1
        else:
            side = -1
        e = O[ent]; sl = e * (1 - side * SL_FIX); tp = e * (1 + side * (TP_FIX + FEE + SLIP))
        seq = [j for j in idxs if j > ent]; sld = SL_FIX
        net = None
        for j in seq:
            if (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl):
                net = side * (sl - e) / e - FEE - 2 * SLIP; break
            if (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp):
                net = side * (tp - e) / e - FEE - SLIP; break
        if net is None:
            net = (side * (C[seq[-1]] - e) / e - FEE - 2 * SLIP) if seq else 0.0
        out.append((ST[ent], datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, net, sld))
    return out


def cell(res):
    if not res:
        return "n=0                    "
    a = np.array([x[2] for x in res]) * 100.0; rm = np.array([x[2] / x[3] for x in res])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, res):
    m = mc(day_blocks([(x[0], x[2] / x[3]) for x in res])[0]) if res else dict(p=0, dd99=0)
    print("  %-20s | ALL %s | IS %s | OOS %s | pass%5.1f%%"
          % (nm, cell(res), cell([x for x in res if x[1] == 2025]), cell([x for x in res if x[1] == 2026]), m["p"]), flush=True)


def main():
    D = load()
    print("ROBUSTNESS — 09:00 5m fade | FROZEN geometry SL 0.8%% / TP 1.0%% net / direct entry / EoD | 5m clock\n", flush=True)
    print("== (1) HOURS null: fade the H:00 bar, same frozen config -- is 09:00 special? ==", flush=True)
    for Hh in range(6, 15):
        line("fade %02d:00" % Hh, trades(D, Hh, "fade"))
    print("\n== (2) 09:00 WITH-check (continuation) + (3) placebo long/short, same geometry ==", flush=True)
    line("WITH 09:00", trades(D, 9, "with"))
    line("always-LONG 09:00", trades(D, 9, "long"))
    line("always-SHORT 09:00", trades(D, 9, "short"))


if __name__ == "__main__":
    main()
