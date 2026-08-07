"""Break of the FIRST 4h candle in the NY session. Range = that 4h bucket's HIGH/LOW (or BODY = max/min open|close);
after it closes, the first 15m close beyond the range triggers (long above / short below). Wide SL past the opposite
extreme, TP = 1/2 the 4h wick range, ~2-day cap. Break allowed up to BRK_END UTC. Recon 15m+4h; DATA_ROOT=fwd.
Run: python study/ny_break_4h.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001; NY0 = 13; BRK_END = int(os.environ.get("BRK_END", "24"))
_DR = os.environ.get("DATA_ROOT", "")


def load(tf):
    if _DR:
        from study.archive_loader import load_archive
        r = [b for b in load_archive(tf, root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
        r.sort(key=lambda b: b["start_time"])
        return r
    return L.load_features(tf)["A"]


B4 = load("4h"); B15 = load("15m")


def g(b, k, alt):
    v = b.get(k)
    return float(v) if v is not None else float(b.get(alt, 0) or 0)


st15 = [float(b.get("start_time", 0) or 0) for b in B15]; n = len(B15)
H = [g(b, "high", "high") for b in B15]; Lo = [g(b, "low", "low") for b in B15]; C = [g(b, "close", "close_price") for b in B15]

# first 4h bucket per weekday with start hour in the NY session
first4 = {}
for b in B4:
    t = datetime.fromtimestamp(float(b.get("start_time", 0) or 0), tz=timezone.utc)
    if t.weekday() >= 5 or not (NY0 <= t.hour < 21):
        continue
    d = t.date()
    if d not in first4:
        first4[d] = b


def walk(bi, side, e, sl, tp):
    for j in range(bi + 1, min(n, bi + 1 + CAP)):
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl / e - 1) - FEE
        if (hi >= tp) if side > 0 else (lo <= tp):
            return side * (tp / e - 1) - FEE
    ke = min(n - 1, bi + CAP)
    return side * (float(C[ke]) / e - 1) - FEE


def backtest(mode):                                              # mode: "hl" (high/low) or "body"
    rows = []
    for d, b in first4.items():
        whi = g(b, "high", "high"); wlo = g(b, "low", "low")
        o = g(b, "open", "open_price"); c = g(b, "close", "close_price")
        if whi <= wlo or o <= 0:
            continue
        rhi, rlo = (whi, wlo) if mode == "hl" else (max(o, c), min(o, c))
        t_end = float(b.get("end_time", 0) or 0) or float(b.get("start_time", 0))
        t_cut = datetime(d.year, d.month, d.day, min(BRK_END, 23), 59, tzinfo=timezone.utc).timestamp() if BRK_END <= 24 else t_end + 24 * 3600
        side = 0; bi = None
        for j in range(n):
            if st15[j] <= t_end:
                continue
            if st15[j] >= t_cut:
                break
            cl = float(C[j])
            if cl > rhi:
                side = 1; bi = j; break
            if cl < rlo:
                side = -1; bi = j; break
        if bi is None:
            continue
        e = float(C[bi]); sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        if (side > 0 and sl >= e) or (side < 0 and sl <= e):
            continue
        rows.append((walk(bi, side, e, sl, e + side * 0.5 * (whi - wlo)),
                     datetime.fromtimestamp(st15[bi], tz=timezone.utc).year))
    return rows


def rep(label, rows):
    nt = np.array([x[0] for x in rows])
    if len(nt) == 0:
        print("  %-16s n=0" % label); return
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf")
    t25 = (np.prod([1 + x for x, y in rows if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in rows if y == 2026]) - 1) * 100
    print("  %-16s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%% | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, t25, t26))


print("=" * 96)
print("Break of the FIRST NY-session 4h candle | 15m break + walk | TP 1/2 range | 15m %s | days=%d"
      % ("DAEMON/fwd" if _DR else "recon", len(first4)))
print("  (reference 2-5pm body range, adaptive TP = +192%% recon / +3.7%% fwd)")
print("=" * 96)
rep("4h HIGH/LOW", backtest("hl"))
rep("4h BODY", backtest("body"))
print("=" * 96)
