"""NY short break — LIMIT entry at the range LOW edge (rlo) instead of a market fill at the breakout candle close, with
a FIXED 0.4%-from-CLOSE TP (the gold 'TP 0.4%' overlay line). Because rlo sits ABOVE the break close, the limit gives a
better (higher) short entry -> a BIGGER effective target (entry->TP > 0.4%) AND a SMALLER stop (better R:R) -- but only
fills if price RETRACES up to rlo (misses runners; retrace fills may be adversely selected). SL = 0.1% past the range
wick (unchanged). 2-day hold. Limit = maker (no entry slip); TP = maker (no slip); SL/flat = taker (exit slip). SHORT
only. clock 15m. Decompose vs the validated break-close/adaptive baseline. IS(2025)/OOS(2026).
python study/ny_rangebreak_limit_rlo_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH, TPFIX = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5, 0.004
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600
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


def _net(entry, exitp, is_tp, market_entry):
    g = -1.0 * (exitp - entry) / entry                            # SHORT
    return g - FEE - (SLIP if market_entry else 0.0) - (0.0 if is_tp else SLIP)


def run(mode):
    """mode: 'A' break-close+adaptive (validated ref), 'B' break-close+fix0.4, 'C' LIMIT@rlo+fix0.4 (USER),
    'D' LIMIT@rlo+adaptive."""
    O, C, Hi, Lo, ST, HR, DATE, WD, n = load()
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    tr = []; nbreak = 0; nfill = 0; tpd = []; sld = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        ri = [i for i in idxs if HR[i] in R_HRS]; bi = sorted([i for i in idxs if HR[i] in B_HRS])
        if not ri or not bi:
            continue
        rlo = min(min(O[i], C[i]) for i in ri); rhi = max(max(O[i], C[i]) for i in ri)
        whi = max(Hi[i] for i in ri); wlo = min(Lo[i] for i in ri); rng = whi - wlo
        if rng <= 0:
            continue
        k = None
        for i in bi:
            if C[i] < rlo:                                         # SHORT break only
                k = i; break
        if k is None:
            continue
        nbreak += 1
        brkclose = C[k]; sl = whi * (1 + SL_PAD)
        seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD]
        limit = mode in ("C", "D")
        if limit:                                                 # LIMIT@rlo -> fill when a later candle retraces up to rlo
            fi = None
            for idx, j in enumerate(seq):
                if Hi[j] >= rlo:
                    fi = idx; break
            if fi is None:
                continue                                          # never retraced -> no fill (miss)
            entry = rlo; walkseq = seq[fi:]; market_entry = False
        else:
            entry = brkclose; walkseq = seq; market_entry = True
        if mode in ("A", "D"):                                    # adaptive TP from entry
            lowvol = (rng / entry * 100.0) < TP_THR; mult = TP_LOW if lowvol else TP_HIGH
            tp = entry - mult * rng
        else:                                                     # fixed 0.4% below the BREAK CLOSE (a level)
            tp = brkclose * (1 - TPFIX)
        if tp >= entry or sl <= entry:                            # geometry guard
            continue
        nfill += 1; tpd.append(100.0 * (entry - tp) / entry); sld.append(100.0 * (sl - entry) / entry)
        net = None
        for j in walkseq:
            if Hi[j] >= sl:
                net = _net(entry, sl, False, market_entry); break
            if Lo[j] <= tp:
                net = _net(entry, tp, True, market_entry); break
        if net is None:
            net = _net(entry, C[walkseq[-1]], False, market_entry) if walkseq else _net(entry, entry, False, market_entry)
        rmult = net / (sld[-1] / 100.0) if sld[-1] > 0 else 0.0
        tr.append((ST[k], net, rmult))
    return tr, nbreak, nfill, (np.mean(tpd) if tpd else 0), (np.mean(sld) if sld else 0)


def cell(tr, yr=None):
    r = [t for t in tr if (yr is None or datetime.fromtimestamp(t[0], tz=timezone.utc).year == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t[1] for t in r]) * 100.0; rm = np.array([t[2] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, tr, nb, nf, tpd, sld):
    print("  %-34s fill%3.0f%% TPd%.2f%% SLd%.2f%% RR%.2f | ALL %s | IS %s | OOS %s"
          % (nm, 100.0 * nf / max(1, nb), tpd, sld, (tpd / sld if sld else 0), cell(tr), cell(tr, 2025), cell(tr, 2026)), flush=True)


def main():
    print("NY SHORT break — LIMIT@rlo vs break-close, fixed-0.4%%-from-close TP vs adaptive TP | clock 15m | 2-day hold | IN-SAMPLE", flush=True)
    print("TPd/SLd = avg TP/SL distance from ENTRY %%; RR = TPd/SLd; exp = per-unit net %%; avgR = net / stop-distance.\n", flush=True)
    for mode, nm in (("A", "break-close + ADAPTIVE TP (validated)"), ("B", "break-close + fixed 0.4% TP"),
                     ("C", "LIMIT@rlo + fixed 0.4% TP  (USER)"), ("D", "LIMIT@rlo + ADAPTIVE TP")):
        tr, nb, nf, tpd, sld = run(mode)
        line(nm, tr, nb, nf, tpd, sld)


if __name__ == "__main__":
    main()
