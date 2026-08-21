"""NY OPENING-range breakout + STOP-AND-REVERSE (clock 15m). Base = the opening breakout (range = first 2 NY candles
13:00+13:15 UTC, first close beyond, SL 0.1% past wick, adaptive TP, 2-day hold). If the breakout's SL is TOUCHED, the
break failed -> REVERSE: enter the COUNTER position at the SL price with the MIRRORED breakout structure (counter SL =
0.1% past the OPPOSITE wick, adaptive TP the other way). One reversal per day. Report the BREAKOUT leg alone, the COUNTER
leg alone (does fading a failed opening break have an edge?), and the SAR COMBINED per-day P&L (orig + counter, each
risking 1R). exp = per-unit net %%; avgR = net/stop; prop-MC HyroTrader $200k R0.4. IS/OOS. python study/ny_opening_sar_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.ny_opening_breakout_15m import load, _net, SL_PAD, TP_THR, TP_LOW, TP_HIGH, NY_OPEN, MAXHOLD
from study.ny_rangebreak_poc_prop import mc, day_blocks
BRK_END = 21


def _walk(side, entry, sl, tp, seq, O, C, Hi, Lo):
    """stop-first pessimistic; returns (R, exit_kind, stop_idx_or_None). R normalised by the stop distance."""
    sld = abs(sl - entry) / entry
    for idx, j in enumerate(seq):
        adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
        favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
        if adverse:
            return _net(side, entry, sl, False) / sld, "sl", idx
        if favor:
            return _net(side, entry, tp, True) / sld, "tp", None
    ex = C[seq[-1]] if seq else entry
    return _net(side, entry, ex, False) / sld, "flat", None


def run():
    O, C, Hi, Lo, ST, HR, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    orig = []; counter = []; combined = []; nbrk = 0; nstop = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        sess = [i for i in idxs if HR[i] >= NY_OPEN]
        if len(sess) < 3:
            continue
        r2 = sess[:2]
        rhi = max(max(O[i], C[i]) for i in r2); rlo = min(min(O[i], C[i]) for i in r2)
        whi = max(Hi[i] for i in r2); wlo = min(Lo[i] for i in r2); rng = whi - wlo
        if rng <= 0:
            continue
        k = None; s0 = 0
        for j in sess[2:]:
            if HR[j] >= BRK_END:
                break
            if C[j] > rhi:
                k = j; s0 = 1; break
            if C[j] < rlo:
                k = j; s0 = -1; break
        if k is None:
            continue
        nbrk += 1
        e0 = C[k]; sl0 = wlo * (1 - SL_PAD) if s0 > 0 else whi * (1 + SL_PAD)
        m0 = TP_LOW if (rng / e0 * 100.0) < TP_THR else TP_HIGH; tp0 = e0 + s0 * m0 * rng
        seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD]
        r0, kind0, sidx = _walk(s0, e0, sl0, tp0, seq, O, C, Hi, Lo)
        orig.append((ST[k], r0)); day_R = r0
        if kind0 == "sl" and sidx is not None:                    # breakout failed -> stop-and-reverse
            nstop += 1
            s1 = -s0; e1 = sl0
            sl1 = wlo * (1 - SL_PAD) if s1 > 0 else whi * (1 + SL_PAD)
            m1 = TP_LOW if (rng / e1 * 100.0) < TP_THR else TP_HIGH; tp1 = e1 + s1 * m1 * rng
            ok = (tp1 > e1 and sl1 < e1) if s1 > 0 else (tp1 < e1 and sl1 > e1)
            if ok:
                cseq = seq[sidx:]                                 # from the stop bar onward (reverse fills at sl0)
                r1, _, _ = _walk(s1, e1, sl1, tp1, cseq, O, C, Hi, Lo)
                counter.append((ST[k], r1)); day_R = r0 + r1
        combined.append((ST[k], day_R))
    return orig, counter, combined, nbrk, nstop


def cell(tr, yr=None):
    r = [t for t in tr if (yr is None or datetime.fromtimestamp(t[0], tz=timezone.utc).year == yr)]
    if not r:
        return "n=0                "
    a = np.array([t[1] for t in r])
    return "n=%-3d win%4.1f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean())


def line(nm, tr, prop=False):
    extra = ""
    if prop and tr:
        m = mc(day_blocks(tr)[0]); extra = " | R0.4 pass%5.1f%% med%4.0fd DDp99%4.1f%% worst%4.1f%%" % (m["p"], m["med"], m["dd99"], m["worst"])
    print("  %-24s ALL %s | IS %s | OOS %s%s" % (nm, cell(tr), cell(tr, 2025), cell(tr, 2026), extra), flush=True)


def main():
    print("NY OPENING breakout + STOP-AND-REVERSE (clock 15m) | reverse at the SL when the break fails | avgR = net/stop-dist", flush=True)
    orig, counter, combined, nbrk, nstop = run()
    print("breaks %d | stopped (reversed) %d = %.0f%% of breaks\n" % (nbrk, nstop, 100.0 * nstop / max(1, nbrk)), flush=True)
    line("BREAKOUT leg (orig)", orig, prop=True)
    line("COUNTER leg (reversal)", counter, prop=True)
    line("SAR COMBINED (orig+ctr)", combined, prop=True)


if __name__ == "__main__":
    main()
