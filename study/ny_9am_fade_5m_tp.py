"""09:00-UTC 5m FADE — TP study. The 09:00 5m bar has a real, OOS-stable reversal lean (fade dir-hit 57.6%, win 79.5%
IS=OOS) but the fixed 0.2%% TP (RR 0.10) taxes it into a small loss. Does a WIDER / adaptive TP monetize the lean? First
the fade MFE (how far the reversion runs), then a TP sweep: fixed 0.2/0.3/0.5/0.8/1.0/1.5%% net + adaptive {Tokyo mid,
opposite Tokyo extreme, 0.5x/1.0x Tokyo range}. SL beyond the Tokyo(00-09) range. Entry = 09:05 open (next 5m). Hold EoD.
5m clock, IS(2025)/OOS(2026), prop-MC HyroTrader $200k R0.4. python study/ny_9am_fade_5m_tp.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD = 0.0004, 0.0003, 0.0005
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
        entry = O[ent]; sl = tLo * (1 - SL_PAD) if side > 0 else tHi * (1 + SL_PAD)
        if (sl >= entry) if side > 0 else (sl <= entry):
            continue
        seq = [j for j in idxs if j > ent]
        trades.append(dict(ts=ST[ent], yr=datetime.fromtimestamp(ST[ent], tz=timezone.utc).year, side=side,
                           entry=entry, sl=sl, tHi=tHi, tLo=tLo, seq=seq))
    return trades


def mfe(t, Hi, Lo):
    best = t["entry"]
    for j in t["seq"]:
        if (Lo[j] <= t["sl"]) if t["side"] > 0 else (Hi[j] >= t["sl"]):
            best = max(best, Hi[j]) if t["side"] > 0 else min(best, Lo[j]); break
        best = max(best, Hi[j]) if t["side"] > 0 else min(best, Lo[j])
    return abs(best - t["entry"]) / t["entry"] * 100.0


def tp_price(t, kind):
    e = t["entry"]; s = t["side"]
    if isinstance(kind, float):
        return e * (1 + s * (kind + FEE + SLIP))
    if kind == "mid":
        return (t["tHi"] + t["tLo"]) / 2.0
    if kind == "opp":
        return t["tHi"] if s > 0 else t["tLo"]
    if kind.startswith("r"):
        return e + s * float(kind[1:]) * (t["tHi"] - t["tLo"])
    return e


def sim(t, kind, C, Hi, Lo):
    e = t["entry"]; s = t["side"]; sl = t["sl"]; tp = tp_price(t, kind)
    if (tp <= e) if s > 0 else (tp >= e):
        return None
    for j in t["seq"]:
        adverse = (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl)
        favor = (Hi[j] >= tp) if s > 0 else (Lo[j] <= tp)
        if adverse:
            return s * (sl - e) / e - FEE - SLIP - SLIP, abs(sl - e) / e
        if favor:
            return s * (tp - e) / e - FEE - SLIP, abs(sl - e) / e
    ex = C[t["seq"][-1]] if t["seq"] else e
    return s * (ex - e) / e - FEE - SLIP - SLIP, abs(sl - e) / e


def report(name, trades, kind, C, Hi, Lo):
    res = [(t["ts"], t["yr"]) + sim(t, kind, C, Hi, Lo) for t in trades if sim(t, kind, C, Hi, Lo) is not None]
    if not res:
        print("  %-14s n=0" % name); return
    def cell(rr):
        if not rr:
            return "n=0                    "
        a = np.array([x[2] for x in rr]) * 100.0; rm = np.array([x[2] / x[3] for x in rr])
        return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())
    m = mc(day_blocks([(x[0], x[2] / x[3]) for x in res])[0])
    tpd = np.mean([abs(tp_price(t, kind) - t["entry"]) / t["entry"] for t in trades if sim(t, kind, C, Hi, Lo)]) * 100
    print("  %-14s TPd%4.2f%% | ALL %s | IS %s | OOS %s | pass%5.1f%% DD%4.1f%%"
          % (name, tpd, cell(res), cell([x for x in res if x[1] == 2025]), cell([x for x in res if x[1] == 2026]), m["p"], m["dd99"]), flush=True)


def main():
    D = load(); O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    trades = collect(D)
    mfes = np.array([mfe(t, Hi, Lo) for t in trades])
    print("09:00-UTC 5m FADE — TP study | n=%d | SL past Tokyo(00-09) range | hold EoD | 5m clock\n" % len(trades), flush=True)
    print("fade MFE (reversion depth before SL/EoD): med %.2f%%  mean %.2f%%  p75 %.2f%%  p90 %.2f%%\n"
          % (np.median(mfes), mfes.mean(), np.percentile(mfes, 75), np.percentile(mfes, 90)), flush=True)
    print("== fixed-TP sweep (net) ==", flush=True)
    for x in (0.002, 0.003, 0.005, 0.008, 0.010, 0.015):
        report("fixed %.1f%%" % (x * 100), trades, x, C, Hi, Lo)
    print("\n== adaptive / mean-reversion targets ==", flush=True)
    for k, nm in (("mid", "Tokyo mid"), ("r0.5", "0.5x Tokyo rng"), ("r1.0", "1.0x Tokyo rng"), ("opp", "opp Tokyo extreme")):
        report(nm, trades, k, C, Hi, Lo)


if __name__ == "__main__":
    main()
