"""Shared HONEST evaluation harness for the pre-daemon reconstruction signal search (1h scale).

Every search agent uses THIS to score a rule, so stats are consistent + disciplined:
  - non-overlap taken() (canonical basis)
  - fixed bracket, ADVERSE-first fill on 1h bars
  - FEE-ADJUSTED break-even win rate w* = (fee + sl)/(sl*(1+rr))
  - exact one-sided binomial p vs w*  (split-half is near-vacuous; we use the exact test + the 2025/2026 split)
  - 2025 vs 2026 sub-splits (both must hold the same direction, or it's a regime artifact)
  - INVERSE-check helper: if flipping the rule is ~equally good, the "edge" is noise.
Features are the FAITHFUL (trade-derived) reconstruction stats only (delta/skew/eff-agg/absorption/mov_mag/VA-bias/
climax/run) — NOT OI-force or liquidations. 1h footprint fidelity ~7% independent, gates ~97%.

API:
  F = load_features()            # dict of np arrays + F['A'] (raw 1h dicts) + F['first'] (maturity index)
  r = evaluate(side, sl, rr)     # side: int array in {-1,0,1} per bar. -> dict of honest stats
  print(fmt(r))
"""
from __future__ import annotations
import os, sys, math, pickle, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from app import pivot_detect as PD
from app.footprint_panel import profile_skewness
from study.mm_skew_feature_matrix import va_poc
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "signal_feat_1h.pkl")
FEE = 0.0008
MATURE = 100000.0
# In-sample / holdout boundary: hunters search t < SPLIT_TS; the last ~4 months are the untouched verify tail.
SPLIT_TS = int(dt.datetime(2026, 2, 19, tzinfo=dt.timezone.utc).timestamp())   # 2026-02-19 00:00 UTC


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def _daily_va(A):
    days = {}; first = {}
    for i, b in enumerate(A):
        d = _dtu(b["start_time"]).date(); days.setdefault(d, []).append(b)
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
    return out, first, days


def _partial_va(A, i, i0):
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
        return 0
    if b["vah"] > a["vah"] and b["val"] > a["val"]:
        return 1
    if b["vah"] < a["vah"] and b["val"] < a["val"]:
        return -1
    return 0


def build_features(tf="1h"):
    _, raws, _ = load_archive(tf, root=RECON)
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r); d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l; d["open"] = o; d["close"] = c
        A.append(d)
    n = len(A)
    dayva, dfirst, _ = _daily_va(A)
    c = np.array([b["c"] for b in A]); o = np.array([b["o"] for b in A])
    h = np.array([b["h"] for b in A]); l = np.array([b["l"] for b in A])
    bv = np.array([float(b.get("buy_vol", 0)) for b in A]); sv = np.array([float(b.get("sell_vol", 0)) for b in A])
    cv = np.array([float(b.get("curr_vol", 0)) or 1.0 for b in A])
    delta = (bv - sv) / cv * 100.0
    sk = np.array([profile_skewness(b.get("levels")) or 0.0 for b in A])
    spread = (2.0 * np.asarray(PD.eff_causal_share(A), float) - 1.0) * 100.0
    absA = np.array([(lambda x: x if x is not None else 0.0)(_safe_abs(A, i)) for i in range(n)])
    ref = np.where(c > o, l, h)
    movmag = np.where(ref > 0, (((c * 100.0 / ref) - 100.0) ** 2) * 100.0, 0.0)
    dirn = np.sign(c - o).astype(int)
    prevdir = np.concatenate([[0], dirn[:-1]])
    runpos = np.zeros(n, int)
    for i in range(1, n):
        runpos[i] = runpos[i - 1] + 1 if dirn[i] == dirn[i - 1] and dirn[i] != 0 else (1 if dirn[i] != 0 else 0)
    rng = np.where(h > l, h - l, 1e-9)
    climax = (c - l) / rng                     # 1 = closed at the top, 0 = bottom
    yr = np.array([_dtu(b["start_time"]).year for b in A])
    st = np.array([float(b["start_time"]) for b in A])
    hour = np.array([_dtu(b["start_time"]).hour for b in A])
    dow = np.array([_dtu(b["start_time"]).weekday() for b in A])   # 0=Mon .. 6=Sun
    # two-phase VA-trend bias per bar: +1 long-only day, -1 short-only, 0 both
    vabias = np.zeros(n, int)
    for i in range(n):
        t = _dtu(st[i]); d3 = t.date()
        if t.hour < 15:
            vabias[i] = _trend(dayva.get(d3 - dt.timedelta(days=2)), dayva.get(d3 - dt.timedelta(days=1)))
        else:
            vabias[i] = _trend(dayva.get(d3 - dt.timedelta(days=1)), _partial_va(A, i, dfirst.get(d3, i)))
    first = 0                       # recon emits ONLY complete (target-volume) buckets -> all mature by construction
    F = dict(A=A, n=n, first=first, tf=tf, c=c, o=o, h=h, l=l, buy=bv, sell=sv, vol=cv, delta=delta, sk=sk,
             spread=spread, absA=absA, movmag=movmag, dir=dirn, prevdir=prevdir, runpos=runpos,
             climax=climax, vabias=vabias, year=yr, start=st, hour=hour, dow=dow)
    return F


def _safe_abs(A, i):
    try:
        return ABS.absorption(A, i)[0]
    except Exception:
        return None


def _cache_path(tf):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "signal_feat_%s.pkl" % tf)


def load_features(tf="1h"):
    cp = _cache_path(tf)
    if os.path.exists(cp):
        with open(cp, "rb") as f:
            return pickle.load(f)
    F = build_features(tf)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    with open(cp, "wb") as f:
        pickle.dump(F, f)
    return F


def _binom_ge(k, n, p):
    if n <= 0:
        return float("nan")
    if n <= 300:
        return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    mu = n * p; sd = math.sqrt(n * p * (1 - p))
    if sd <= 0:
        return 1.0 if k <= mu else 0.0
    return 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))


def _sim(A, i, side, sl, rr):
    e = A[i]["c"]; slp = e * (1 - sl) if side > 0 else e * (1 + sl)
    tpp = e + rr * (e * sl) * side
    for j in range(i + 1, len(A)):
        hi = A[j]["h"]; lo = A[j]["l"]
        if (lo <= slp) if side > 0 else (hi >= slp):
            return 0, j                        # SL
        if (hi >= tpp) if side > 0 else (lo <= tpp):
            return 1, j                        # TP
    return 0, len(A) - 1                        # OPEN -> non-win


def evaluate(F, side, sl=0.005, rr=1.0, mature=True, t0=None, t1=None):
    """side: int array {-1,0,1}. Non-overlap taken(); fixed bracket; honest stats + 2025/26 split + binomial p.
    t0/t1 (unix): restrict ENTRIES to t0 <= start_time < t1 (in-sample vs holdout). None = unrestricted."""
    A = F["A"]; yr = F["year"]; st = F["start"]
    lo_i = F["first"] if mature else 0
    last = -1; wins = 0; nt = []; yrs = []
    for i in range(lo_i, len(A)):
        s = int(side[i])
        if s == 0 or i <= last:
            continue
        if t0 is not None and st[i] < t0:
            continue
        if t1 is not None and st[i] >= t1:
            continue
        w, ej = _sim(A, i, s, sl, rr); last = ej
        g = (rr * sl) if w else -sl
        nt.append(g - FEE); wins += 1 if (g - FEE) > 0 else 0; yrs.append(int(yr[i]))
    n = len(nt)
    if n == 0:
        return dict(n=0)
    nt = np.array(nt); yrs = np.array(yrs)
    be = (sl + FEE) / (sl * (1 + rr))                 # fee-adjusted break-even win rate
    gross_be = 1.0 / (1 + rr)
    winr = wins / n
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    p = _binom_ge(wins, n, be)                        # H0: win rate == fee-adjusted BE
    def half(mask):
        m = mask.sum()
        return (int(m), (nt[mask] > 0).mean() * 100 if m else 0.0, ((np.prod(1 + nt[mask]) - 1) * 100) if m else 0.0)
    return dict(n=n, win=winr * 100, be=be * 100, gross_be=gross_be * 100, edge=(winr - be) * 100,
                net=tot, mean=nt.mean() * 100, pf=pf, binom_p=p,
                y25=half(yrs == 2025), y26=half(yrs == 2026), sl=sl, rr=rr)


def fmt(r):
    if r.get("n", 0) == 0:
        return "n=0 (no signals)"
    return ("n=%4d win %5.1f%% (BE %.1f%%, edge %+.1f) net %+7.1f%% mean %+.4f%% PF %.2f binom_p=%.3f | "
            "2025 n=%d %.0f%%/%+.0f%%  2026 n=%d %.0f%%/%+.0f%%  [SL %.2f%% RR %.1f]"
            % (r["n"], r["win"], r["be"], r["edge"], r["net"], r["mean"], r["pf"], r["binom_p"],
               r["y25"][0], r["y25"][1], r["y25"][2], r["y26"][0], r["y26"][1], r["y26"][2], r["sl"] * 100, r["rr"]))


if __name__ == "__main__":
    F = load_features()
    print("features: %d 1h buckets, mature-from %d, %s -> %s" % (F["n"], F["first"],
          _dtu(F["start"][0]).strftime("%Y-%m-%d"), _dtu(F["start"][-1]).strftime("%Y-%m-%d")))
    # sanity: a known-ish rule (bullish + skew>0 + spread>=35 long ; mirror short) at SL0.5% RR1.5
    s = F["sk"]; sp = F["spread"]; d = F["dir"]
    side = np.where((d > 0) & (s > 0) & (sp >= 35), 1, np.where((d < 0) & (s < 0) & (sp <= -35), -1, 0))
    print("sanity MMXSKEW-ish:", fmt(evaluate(F, side, sl=0.005, rr=1.5)))
    print("inverse (should NOT also be good):", fmt(evaluate(F, -side, sl=0.005, rr=1.5)))
