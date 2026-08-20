"""NY range-break RETEST entry (clock 15m). Break sets the side (brS short close<rlo / brB long close>rhi). Then WAIT
for price to come BACK to the broken range edge (short: a later candle's HIGH >= rlo; long: LOW <= rhi), and enter at
the CLOSE of the first NON-DOJI candle IN FAVOR (short: bearish real body; long: bullish real body). SL 0.1% past the
range wick, vol-adaptive TP (2x/0.5x range). TIMEOUT AT MIDNIGHT (00:00 UTC = end of the break's UTC day): no confirmed
entry by then -> no trade; still open -> flatten at the last same-day candle. Compare vs break-close baselines (same-day
timeout AND 2-day hold) to separate the retest-entry effect from the timeout. Stop-first pessimistic. clock 15m. Short
+ long, doji thresholds 0.1/0.3/0.5, IS(2025)/OOS(2026). python study/ny_rangebreak_retest_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD_2D = 48 * 3600
ROOT, TF = "study/clock_archive", "15m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, DATE, WD, n


def _net(side, entry, exitp, is_tp):
    g = side * (exitp - entry) / entry
    return g - FEE - SLIP - (0.0 if is_tp else SLIP)


def walk(side, entry, sl, tp, seq, O, C, Hi, Lo):
    """stop-first pessimistic walk over candle indices `seq`; flatten at the last one (midnight/hold cap)."""
    if not seq:
        return _net(side, entry, entry, False), "flat"
    for j in seq:
        hi = Hi[j]; lo = Lo[j]
        if (hi >= sl) if side < 0 else (lo <= sl):
            return _net(side, entry, sl, False), "sl"
        if (lo <= tp) if side < 0 else (hi >= tp):
            return _net(side, entry, tp, True), "tp"
    return _net(side, entry, C[seq[-1]], False), "flat"


def run(mode, doji=0.1, hold="day"):
    """mode: 'break2d' (enter break close, 2-day hold), 'breakday' (enter break close, midnight timeout),
    'retest' (wait return + non-doji confirm, enter). hold: 'day' (midnight timeout) or '2d' (2-day hold) for retest."""
    O, C, Hi, Lo, ST, HR, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    dates = sorted(bydate)
    out_s = []; out_l = []; nbreak = {-1: 0, 1: 0}; nfill = {-1: 0, 1: 0}
    for di, d in enumerate(dates):
        idxs = bydate[d]
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        whi = max(Hi[i] for i in ri); wlo = min(Lo[i] for i in ri); rng = whi - wlo
        if rng <= 0:
            continue
        k = None; side = 0
        for i in bi:
            if C[i] < rlo:
                k = i; side = -1; break
            if C[i] > rhi:
                k = i; side = 1; break
        if k is None:
            continue
        nbreak[side] += 1
        edge = rlo if side < 0 else rhi
        day_after = [j for j in idxs if j > k]                     # same UTC day, after the break (midnight timeout)
        if mode == "retest":
            entry_j = None; returned = False
            for j in day_after:
                if not returned:
                    if (Hi[j] >= edge) if side < 0 else (Lo[j] <= edge):
                        returned = True
                if returned:
                    body = abs(C[j] - O[j]); rj = Hi[j] - Lo[j]
                    fav = (C[j] < O[j]) if side < 0 else (C[j] > O[j])
                    if fav and rj > 0 and body / rj >= doji:
                        entry_j = j; break
            if entry_j is None:
                continue                                           # no confirmed retest by midnight -> no trade
            entry = C[entry_j]
            if hold == "2d":
                seq = [j for j in range(entry_j + 1, n) if ST[j] <= ST[entry_j] + MAXHOLD_2D]
            else:
                seq = [j for j in day_after if j > entry_j]        # midnight timeout
        else:
            entry_j = k; entry = C[k]
            if mode == "breakday":
                seq = day_after
            else:                                                  # break2d: walk until TP/SL or 2-day cap
                seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD_2D]
        nfill[side] += 1
        sl = whi * (1 + SL_PAD) if side < 0 else wlo * (1 - SL_PAD)
        lowvol = (rng / entry * 100.0) < TP_THR; mult = TP_LOW if lowvol else TP_HIGH
        tp = entry + side * mult * rng
        net, _ = walk(side, entry, sl, tp, seq, O, C, Hi, Lo)
        (out_s if side < 0 else out_l).append((ST[k], net))
    return out_s, out_l, nbreak, nfill


def cell(tr, yr=None):
    r = [t for t in tr if (yr is None or datetime.fromtimestamp(t[0], tz=timezone.utc).year == yr)]
    if not r:
        return "n=0            "
    a = np.array([t[1] for t in r]) * 100.0
    return "n=%-3d win%4.1f%% exp%+.3f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())


def line(nm, tr, nb, nf, sidekey):
    fill = 100.0 * nf[sidekey] / max(1, nb[sidekey])
    print("    %-30s fill%3.0f%% | ALL %s | IS %s | OOS %s" % (nm, fill, cell(tr), cell(tr, 2025), cell(tr, 2026)), flush=True)


def main():
    print("NY range-break RETEST entry (clock 15m) | wait return-to-range + first non-doji favorable candle | midnight timeout", flush=True)
    print("vs break-close baselines. exp = per-unit net %%. fill%% = entries / breaks (how many breaks retrace+confirm).\n", flush=True)
    b2s, b2l, nb2, nf2 = run("break2d")
    bds, bdl, nbd, nfd = run("breakday")
    print("==== SHORT (brS) ====", flush=True)
    line("BASELINE break-close, 2-day hold", b2s, nb2, nf2, -1)
    line("BASELINE break-close, midnight TO", bds, nbd, nfd, -1)
    for dj in (0.1, 0.3, 0.5):
        rs, rl, nbr, nfr = run("retest", doji=dj, hold="day")
        line("RETEST doji>=%.1f, midnight TO" % dj, rs, nbr, nfr, -1)
    for dj in (0.1, 0.5):                                          # isolate the retest-entry effect (no timeout confound)
        rs, rl, nbr, nfr = run("retest", doji=dj, hold="2d")
        line("RETEST doji>=%.1f, 2-day hold" % dj, rs, nbr, nfr, -1)
    print("==== LONG (brB) ====", flush=True)
    line("BASELINE break-close, 2-day hold", b2l, nb2, nf2, 1)
    line("BASELINE break-close, midnight TO", bdl, nbd, nfd, 1)
    for dj in (0.1, 0.3, 0.5):
        rs, rl, nbr, nfr = run("retest", doji=dj, hold="day")
        line("RETEST doji>=%.1f, midnight TO" % dj, rl, nbr, nfr, 1)


if __name__ == "__main__":
    main()
