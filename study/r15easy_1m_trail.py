"""15mReasy retest on the PRE-DAEMON reconstruction (study/recon_archive) with a 1m-followed TRAILING-STOP exit.

Signals (15m, frozen base + two-phase VA bias):
  1) R-easy  A <= -0.75 ;  2) |skew| >= 0.4 (side) ;  3) prev 15m same direction ;
  4) VA-trend bias D1-vs-D2 (before 3PM UTC) ;  5) switch to D2-vs-D3-so-far (from 3PM). Both: up->long / down->short / else both.

Exit (followed on the 1m volume buckets):
  entry = 15m signal close. Initial SL = 0.1% beyond the 15m bar's extreme (frozen). Then a ratcheting 1m trail on the
  peak favourable excursion E: E>=0.1% -> SL=entry-0.1% ; E>=0.2% -> SL=entry+0.1% (breakeven+comm) ; E>=n*0.1% (n>=2)
  -> SL=entry+(n-1)*0.1%. SL only ratchets toward profit. No fixed TP; ride until the 1m hits the SL. Fee 0.08%.
  Intrabar convention: adverse-first (check current SL vs the 1m low/high, THEN ratchet from that bar's extreme).

Run: python study/r15easy_1m_trail.py
"""
from __future__ import annotations
import os, sys, gzip, json, glob, math, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from app.footprint_panel import profile_skewness
from study.mm_skew_feature_matrix import va_poc
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
R_EASY = -0.75
SKEW_MIN = 0.4
SL_PAD = 0.001           # stop sits 0.1% BEYOND the entry candle's extreme (structural)
SL_MAX = 0.009           # ... but cap the stop distance from entry at 0.9%
TP_FIXED = 0.003         # fixed 0.3% target from entry
CUTOFF_HOUR = 15         # 3PM UTC


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def load_15m():
    _, raws, _ = load_archive("15m", root=RECON)
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r)
        d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["open"] = o; d["close"] = c
        d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o
        A.append(d)
    return A


def load_1m_ohlc():
    """Compact (t_start, high, low) arrays for the 1m follow — streamed, OHLC only (no levels) to stay light."""
    # sort chunk files by NUMERIC first-bid (glob's lexical order puts 1m_10001_ before 1m_1_10000 -> non-monotonic t)
    files = sorted(glob.glob(os.path.join(RECON, "1m", "1m_*.jsonl.gz")),
                   key=lambda p: int(os.path.basename(p).split("_")[1]))
    ts = []; hi = []; lo = []
    for fn in files:
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)["data"]
                ts.append(float(d["start_time"])); hi.append(float(d["high"])); lo.append(float(d["low"]))
    ts = np.array(ts); hi = np.array(hi); lo = np.array(lo)
    order = np.argsort(ts, kind="stable")                 # guarantee monotonic time for searchsorted
    return ts[order], hi[order], lo[order]


# ---- VA bias (two-phase) ----
def daily_va(A):
    days = {}; first = {}
    for i, b in enumerate(A):
        d = _dtu(b["start_time"]).date()
        days.setdefault(d, []).append(b)
        if d not in first:
            first[d] = i
    out = {}
    for d, bs in days.items():
        prof = {}
        for b in bs:
            for pr, v in (b.get("levels") or {}).items():
                try:
                    p = float(pr)
                except (TypeError, ValueError):
                    continue
                prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
        va = va_poc(prof)
        if va:
            out[d] = va
    return out, first


def partial_va(A, i, i0):
    prof = {}
    for k in range(i0, i + 1):
        for pr, v in (A[k].get("levels") or {}).items():
            try:
                p = float(pr)
            except (TypeError, ValueError):
                continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return va_poc(prof)


def _trend(a, b):
    if not a or not b:
        return "both"
    if b["vah"] > a["vah"] and b["val"] > a["val"]:
        return "long"
    if b["vah"] < a["vah"] and b["val"] < a["val"]:
        return "short"
    return "both"


def bias(A, i, dayva, first):
    t = _dtu(A[i]["start_time"]); d3 = t.date()
    if t.hour < CUTOFF_HOUR:
        return _trend(dayva.get(d3 - dt.timedelta(days=2)), dayva.get(d3 - dt.timedelta(days=1)))
    return _trend(dayva.get(d3 - dt.timedelta(days=1)), partial_va(A, i, first.get(d3, i)))


def gen_signals(A):
    dayva, first = daily_va(A)
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)
    sigs = []
    for i in range(1, len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or dirn[i - 1] != s:
            continue
        sk = A[i].get("sk")
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):
            continue
        b = bias(A, i, dayva, first)
        if not ((b == "both") or (b == "long" and s > 0) or (b == "short" and s < 0)):
            continue
        sigs.append(dict(i=i, side=s, entry=float(A[i]["c"]),
                         ext=float(A[i]["l"]) if s > 0 else float(A[i]["h"]),
                         t=float(A[i]["end_time"]), yr=_dtu(A[i]["start_time"]).year))
    return sigs


def _bracket(entry, side, ext):
    """SL = 0.1% BEYOND the entry candle's extreme (ext = candle low for long / high for short), capped so the
    stop distance from entry never exceeds 0.9%. TP = fixed 0.3% from entry. Returns (sl_price, tp_price)."""
    if side > 0:
        dist = min((entry - ext) / entry + SL_PAD, SL_MAX)
        return entry * (1 - dist), entry * (1 + TP_FIXED)
    dist = min((ext - entry) / entry + SL_PAD, SL_MAX)
    return entry * (1 + dist), entry * (1 - TP_FIXED)


def sl_dist(sg):
    """Realised stop distance from entry (fraction), for reporting the risk profile."""
    e = sg["entry"]; x = sg["ext"]
    return min((e - x) / e + SL_PAD, SL_MAX) if sg["side"] > 0 else min((x - e) / e + SL_PAD, SL_MAX)


def follow_1m(t1, h1, l1, sg):
    """Structural stop (0.1% beyond extreme, cap 0.9%) / fixed 0.3% TP, resolved on the 1m buckets, adverse(SL)-first."""
    entry = sg["entry"]; side = sg["side"]; sl, tp = _bracket(entry, side, sg["ext"])
    j = int(np.searchsorted(t1, sg["t"])); n = len(t1)
    while j < n:
        hi = float(h1[j]); lo = float(l1[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (sl - entry) / entry * side, "SL"
        if (hi >= tp) if side > 0 else (lo <= tp):
            return (tp - entry) / entry * side, "TP"
        j += 1
    mark = float(l1[-1]) if side > 0 else float(h1[-1])
    return (mark - entry) / entry * side, "OPEN"


def follow_15m(A, sg):
    """Same bracket resolved on the 15m bars (the canonical frozen-15mReasy fill), adverse-first."""
    entry = sg["entry"]; side = sg["side"]; sl, tp = _bracket(entry, side, sg["ext"])
    for j in range(sg["i"] + 1, len(A)):
        hi = float(A[j]["h"]); lo = float(A[j]["l"])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (sl - entry) / entry * side, "SL"
        if (hi >= tp) if side > 0 else (lo <= tp):
            return (tp - entry) / entry * side, "TP"
    return 0.0, "OPEN"


def stat(rows, label):
    n = len(rows)
    if n == 0:
        print("  %-16s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    tp = sum(1 for r in rows if r["out"] == "TP"); op = sum(1 for r in rows if r["out"] == "OPEN")
    sl = sum(1 for r in rows if r["out"] == "SL")
    nt = np.array([r["net"] for r in rows]); tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    print("  %-16s n=%4d  win %5.1f%% (tp %d / sl %d / open %d)  net %+7.1f%%  mean %+.4f%%  PF %.2f"
          % (label, n, 100.0 * w / n, tp, sl, op, tot, nt.mean() * 100, pf))


def main():
    print("loading recon 15m + 1m ...", flush=True)
    A = load_15m()
    t1, h1, l1 = load_1m_ohlc()
    sigs = gen_signals(A)
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    nl = sum(1 for s in sigs if s["side"] > 0)
    dists = [sl_dist(s) for s in sigs]
    capped = sum(1 for d in dists if d >= SL_MAX - 1e-9)
    print("=" * 92)
    print("15mReasy structural stop (0.1%% beyond extreme, cap 0.9%%) / TP fixed 0.3%%  PRE-DAEMON recon  |  %d 15m + %dk 1m, %.0f days"
          % (len(A), len(t1) // 1000, span))
    print("  %s -> %s  |  signals %d (%dL/%dS)  |  SL dist mean %.3f%% median %.3f%%  (%d/%d hit the 0.9%% cap)"
          % (_dtu(A[0]['start_time']).strftime('%Y-%m-%d'), _dtu(A[-1]['start_time']).strftime('%Y-%m-%d'),
             len(sigs), nl, len(sigs) - nl,
             (sum(dists) / len(dists) * 100 if dists else 0), (sorted(dists)[len(dists)//2] * 100 if dists else 0),
             capped, len(dists)))
    print("=" * 92)
    for fill, fn in (("15m-bar fill (canonical)", lambda sg: follow_15m(A, sg)),
                     ("1m fill (finer)", lambda sg: follow_1m(t1, h1, l1, sg))):
        rows = [dict(side=sg["side"], net=fn(sg)[0] - FEE, out=fn(sg)[1], yr=sg["yr"]) for sg in sigs]
        print("--- %s ---" % fill)
        stat(rows, "ALL")
        stat([r for r in rows if r["side"] > 0], "LONG")
        stat([r for r in rows if r["side"] < 0], "SHORT")
        stat([r for r in rows if r["yr"] == 2025], "2025")
        stat([r for r in rows if r["yr"] == 2026], "2026")
        print()
    print("CAVEAT: reconstructed 15m footprint fidelity (skew/absorption gates ~97%). Structural SL (0.1%% beyond extreme,")
    print("        cap 0.9%%), TP +0.3%%, entry 15m close, 1m follow. Fee 0.08%% RT.")


if __name__ == "__main__":
    main()
