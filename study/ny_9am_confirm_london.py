"""9am-fade with CONFIRMATION ENTRY + London SL. Bias = FADE the last Tokyo candle (the candle ending at 09:00 UTC):
bearish Tokyo -> LONG bias / bullish -> SHORT bias. Then wait in London (09:00-12:00) for the FIRST candle printing in
the bias direction; enter at its close. SL 0.1%% beyond the London range (09:00->entry) low(long)/high(short) -- much
TIGHTER than the Tokyo range, so the R:R should lift avgR (the prop-viability lever). TP swept (fixed + adaptive, since
unspecified). Reports SL distance, directional hit, P&L, prop-MC, IS/OOS. 5m clock primary. python study/ny_9am_confirm_london.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD = 0.0004, 0.0003, 0.001
TOK_END, LON_END = 9, 12                                          # Tokyo ends 09:00; confirm-entry window 09:00-12:00
ROOT = "study/clock_archive"


def load(tf):
    A = sorted(load_archive(tf, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, DATE, WD, n


def collect(D):
    O, C, Hi, Lo, ST, HR, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    trades = []; dhit = 0; dn = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        idxs = sorted(idxs)
        tok = [i for i in idxs if HR[i] < TOK_END]
        if not tok:
            continue
        tHi = max(Hi[i] for i in tok); tLo = min(Lo[i] for i in tok)
        last = tok[-1]                                            # last Tokyo candle (ends 09:00)
        if C[last] == O[last]:
            continue
        bias = 1 if C[last] < O[last] else -1                     # FADE the last Tokyo candle
        lon = [i for i in idxs if TOK_END <= HR[i] < LON_END]
        ent = None; lows = []; highs = []
        for i in lon:
            lows.append(Lo[i]); highs.append(Hi[i])
            conf = (C[i] > O[i]) if bias > 0 else (C[i] < O[i])   # first London candle in the bias direction
            if conf:
                ent = i; break
        if ent is None:
            continue
        lonLo = min(lows); lonHi = max(highs)
        entry = C[ent]; side = bias
        sl = lonLo * (1 - SL_PAD) if side > 0 else lonHi * (1 + SL_PAD)
        if (sl >= entry) if side > 0 else (sl <= entry):
            continue
        seq = [j for j in idxs if j > ent]
        endp = C[seq[-1]] if seq else entry
        dn += 1; dhit += 1 if (endp > entry) == (side > 0) else 0
        trades.append(dict(ts=ST[ent], yr=datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, side=side,
                           entry=entry, sl=sl, tHi=tHi, tLo=tLo, seq=seq))
    return trades, (dhit / dn if dn else 0), dn


def tp_price(t, kind):
    e = t["entry"]; s = t["side"]
    if isinstance(kind, float):
        return e * (1 + s * (kind + FEE + SLIP))
    if kind == "opp":
        return t["tHi"] if s > 0 else t["tLo"]
    if kind.startswith("r"):
        return e + s * float(kind[1:]) * (t["tHi"] - t["tLo"])
    return e


def sim(t, kind, C, Hi, Lo):
    e = t["entry"]; s = t["side"]; sl = t["sl"]; tp = tp_price(t, kind)
    if (tp <= e) if s > 0 else (tp >= e):
        return None
    sld = abs(sl - e) / e
    for j in t["seq"]:
        if (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl):
            return s * (sl - e) / e - FEE - 2 * SLIP, sld
        if (Hi[j] >= tp) if s > 0 else (Lo[j] <= tp):
            return s * (tp - e) / e - FEE - SLIP, sld
    ex = C[t["seq"][-1]] if t["seq"] else e
    return s * (ex - e) / e - FEE - 2 * SLIP, sld


def report(name, trades, kind, C, Hi, Lo):
    res = []
    for t in trades:
        r = sim(t, kind, C, Hi, Lo)
        if r is not None:
            res.append((t["ts"], t["yr"], r[0], r[1]))
    if not res:
        print("  %-16s n=0" % name); return

    def cell(rr):
        if not rr:
            return "n=0                    "
        a = np.array([x[2] for x in rr]) * 100.0; rm = np.array([x[2] / x[3] for x in rr])
        return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())
    m = mc(day_blocks([(x[0], x[2] / x[3]) for x in res])[0])
    print("  %-16s | ALL %s | IS %s | OOS %s | pass%5.1f%% DD%4.1f%%"
          % (name, cell(res), cell([x for x in res if x[1] == 2025]), cell([x for x in res if x[1] == 2026]), m["p"], m["dd99"]), flush=True)


def main():
    for tf in ("5m", "15m"):
        D = load(tf); O, C, Hi, Lo, ST, HR, DATE, WD, n = D
        trades, dh, dn = collect(D)
        slds = np.array([abs(t["sl"] - t["entry"]) / t["entry"] * 100 for t in trades])
        print("==== %s clock ====  n=%d  entry-dir hit %.1f%%  |  London SLd med %.2f%% mean %.2f%% (vs Tokyo-SL ~1.9%%)"
              % (tf, len(trades), 100 * dh, np.median(slds), slds.mean()), flush=True)
        for x in (0.003, 0.005, 0.008):
            report("fixed %.1f%%" % (x * 100), trades, x, C, Hi, Lo)
        for k, nm in (("r0.5", "0.5x Tokyo rng"), ("r1.0", "1.0x Tokyo rng"), ("opp", "opp Tokyo extr")):
            report(nm, trades, k, C, Hi, Lo)
        print("", flush=True)


if __name__ == "__main__":
    main()
