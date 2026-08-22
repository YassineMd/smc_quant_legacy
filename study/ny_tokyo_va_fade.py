"""TOKYO VALUE-AREA FADE (1h clock). Fade the last Tokyo 1h candle (07:00-08:00 UTC): bullish -> SHORT bias / bearish ->
LONG bias. Tokyo VOLUME PROFILE (built from 5m clock, 00:00-08:00 UTC, 70% value area): SHORT -> limit sell at VAH,
SL 0.2%% above the Tokyo HIGH; LONG -> limit buy at VAL, SL 0.2%% below the Tokyo LOW. TP 0.5%%. Limit is maker (no entry
slip); trade simulated on 1h clock bars after 08:00 UTC, flatten at day end. Reports fill%%, win%%, exp net%%, avgR, RR,
IS/OOS, prop-MC, + a TP sweep (0.5%% is the tight-TP/wide-SL family that's usually null -> see if a wider TP rescues it).
5m+1h clock. python study/ny_tokyo_va_fade.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD = 0.0004, 0.0003, 0.002    # 0.2% past the Tokyo extreme
TOK_END = 8                                  # Tokyo = 00:00-08:00 UTC (terminal def; London opens 08:00)
ROOT = "study/clock_archive"


def load(tf):
    A = sorted(load_archive(tf, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n); V = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        v = b.get("curr_vol"); V[i] = _f((v if v is not None else _f(b.get("buy_vol", 0) or 0) + _f(b.get("sell_vol", 0) or 0)) or 0)
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    by = {}
    for i in range(n):
        by.setdefault(DATE[i], []).append(i)
    return dict(O=O, C=C, Hi=Hi, Lo=Lo, ST=ST, V=V, HR=HR, MN=MN, WD=WD, by=by)


def value_area(lohv, nbins=120, va=0.70):
    """lohv = list of (low, high, vol) 5m bars. Returns (VAH, VAL, POC). Volume spread uniformly across each bar's range."""
    lo = min(x[0] for x in lohv); hi = max(x[1] for x in lohv)
    if hi <= lo:
        return None
    bw = (hi - lo) / nbins; vol = np.zeros(nbins)
    for l, h, v in lohv:
        b0 = max(0, min(nbins - 1, int((l - lo) / bw))); b1 = max(0, min(nbins - 1, int((h - lo) / bw)))
        span = b1 - b0 + 1
        vol[b0:b1 + 1] += v / span
    poc = int(np.argmax(vol)); tot = vol.sum(); tgt = va * tot
    li = hi_i = poc; acc = vol[poc]
    while acc < tgt:
        up = vol[hi_i + 1] if hi_i + 1 < nbins else -1.0
        dn = vol[li - 1] if li - 1 >= 0 else -1.0
        if up < 0 and dn < 0:
            break
        if up >= dn:
            hi_i += 1; acc += vol[hi_i]
        else:
            li -= 1; acc += vol[li]
    return lo + (hi_i + 1) * bw, lo + li * bw, lo + (poc + 0.5) * bw    # VAH, VAL, POC


def build(D5, D1, tp_frac):
    trades = []
    for d, idx1 in D1["by"].items():
        if D1["WD"][idx1[0]] >= 5 or d not in D5["by"]:
            continue
        idx1 = sorted(idx1); idx5 = sorted(D5["by"][d])
        tok5 = [i for i in idx5 if D5["HR"][i] < TOK_END]
        if len(tok5) < 40:
            continue
        va = value_area([(D5["Lo"][i], D5["Hi"][i], D5["V"][i]) for i in tok5])
        if va is None:
            continue
        VAH, VAL, POC = va
        tHi = max(D5["Hi"][i] for i in tok5); tLo = min(D5["Lo"][i] for i in tok5)
        last = next((i for i in reversed(idx1) if D1["HR"][i] == TOK_END - 1), None)   # last Tokyo 1h candle (07:00 UTC)
        if last is None or D1["C"][last] == D1["O"][last]:
            continue
        bull = D1["C"][last] > D1["O"][last]
        side = -1 if bull else 1                                       # FADE
        post = [i for i in idx1 if D1["HR"][i] >= TOK_END]             # post-Tokyo 1h bars (London+NY), same day
        if not post:
            continue
        entry = VAH if side < 0 else VAL
        sl = tHi * (1 + SL_PAD) if side < 0 else tLo * (1 - SL_PAD)
        tp = entry * (1 + side * tp_frac)                             # long -> above entry / short -> below
        if (sl <= entry or tp >= entry) if side < 0 else (sl >= entry or tp <= entry):
            continue
        # limit fill: first post bar that TOUCHES the entry level (short: high>=VAH ; long: low<=VAL)
        fj = next((j for j in post if ((D1["Hi"][j] >= entry) if side < 0 else (D1["Lo"][j] <= entry))), None)
        if fj is None:
            continue                                                   # limit never filled -> no trade
        net = None
        for j in [p for p in post if p >= fj]:                         # walk from fill bar, stop-first
            adverse = (D1["Hi"][j] >= sl) if side < 0 else (D1["Lo"][j] <= sl)
            favor = (D1["Lo"][j] <= tp) if side < 0 else (D1["Hi"][j] >= tp)
            if adverse:
                net = side * (sl - entry) / entry - FEE - SLIP; break  # SL = taker (slip)
            if favor:
                net = side * (tp - entry) / entry - FEE; break         # limit entry + limit TP = maker (no slip)
        if net is None:
            net = side * (D1["C"][post[-1]] - entry) / entry - FEE - SLIP
        sld = abs(sl - entry) / entry
        trades.append((D1["ST"][fj], datetime.fromtimestamp(D1["ST"][fj], tz=timezone.utc).year, net, sld, side))
    return trades


def stat(tr):
    if not tr:
        return "n=0                    "
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def main():
    D5 = load("5m"); D1 = load("1h")
    ndays = sum(1 for d, ix in D1["by"].items() if D1["WD"][ix[0]] < 5)
    print("TOKYO VALUE-AREA FADE (1h clock, VP from 5m) | fade last Tokyo candle, limit @VAH/VAL, SL 0.2%% past extreme | ~%d weekdays\n" % ndays, flush=True)
    print("== PRIMARY: TP 0.5%% (your spec) ==", flush=True)
    tr = build(D5, D1, 0.005)
    m = mc(day_blocks([(t[0], t[2] / t[3]) for t in tr])[0]) if tr else dict(p=0, dd99=0)
    sld = np.mean([t[3] for t in tr]) * 100 if tr else 0
    print("  fill %.0f%% of weekdays  SLd %.2f%%  RR %.2f" % (100 * len(tr) / ndays, sld, 0.5 / sld if sld else 0), flush=True)
    print("  ALL %s | IS %s | OOS %s | R0.4 pass %.1f%% DDp99 %.1f%%"
          % (stat(tr), stat([t for t in tr if t[1] == 2025]), stat([t for t in tr if t[1] == 2026]), m["p"], m["dd99"]), flush=True)
    print("  short-only %s | long-only %s" % (stat([t for t in tr if t[4] < 0]), stat([t for t in tr if t[4] > 0])), flush=True)
    print("\n== TP sweep (is 0.5%% too tight?) ==", flush=True)
    for tpf in (0.005, 0.008, 0.012, 0.016, 0.020):
        t2 = build(D5, D1, tpf)
        m2 = mc(day_blocks([(t[0], t[2] / t[3]) for t in t2])[0]) if t2 else dict(p=0)
        print("  TP %.1f%%  ALL %s | IS %s | OOS %s | pass %.1f%%"
              % (tpf * 100, stat(t2), stat([t for t in t2 if t[1] == 2025]), stat([t for t in t2 if t[1] == 2026]), m2["p"]), flush=True)


if __name__ == "__main__":
    main()
