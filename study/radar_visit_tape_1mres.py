"""5m WALLS / radars, but the absorbed-side tape fade measured on the underlying 1m CANDLES inside each visit.
NO minimum visit length (any radar visit counts). Full 18 months (memory-safe: from 1m we keep only per-candle
tape + timestamp = 3 float arrays; walls come from the full 5m load).

Per 5m radar visit (radar_run rk0..rk1, completed, resisted or broken): its time span = [start(5m rk0), end(5m rk1)];
take the 1m candles in that span; measure the ABSORBED-side tape over them (S->Tape-S sellers, R->Tape-B buyers).

[1] DESCRIPTIVE (P(fade | outcome)): resisted vs broken, absorbed vs passive, both years.
[2] FORWARD (P(outcome | fade)): among ALL visits, P(wall RESISTED | absorbed tape faded), vs the base resist rate.
    Uses the EARLY-HALF fade (slope<0 over the first half of the visit's 1m candles) = a causal read, plus the
    full-visit fade for reference. Run: python study/radar_visit_tape_1mres.py
"""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

ROOT = "study/recon_archive"

# ---- 5m walls ----
A5 = sorted(load_archive("5m", root=ROOT)[1], key=lambda b: _f(b.get("start_time", 0)))
n5 = len(A5)
st5 = np.array([_f(b.get("start_time")) for b in A5]); et5 = np.array([_f(b.get("end_time")) for b in A5])
print("5m bars=%d  detecting walls ..." % n5, flush=True)
walls = AL.detect(A5)
print("5m walls=%d  streaming 1m tape ..." % len(walls), flush=True)

# ---- 1m tape (lean: only start_time + Tape-B + Tape-S per 1m candle) ----
by_bid = {}
for fn in sorted(glob.glob(os.path.join(ROOT, "1m", "1m_*.jsonl.gz"))):
    with gzip.open(fn, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            s = _f(d.get("start_time")); dur = max(1.0, _f(d.get("end_time")) - s)
            by_bid[int(r["bid"])] = (s, sum(d.get("sz_cb") or []) / dur, sum(d.get("sz_cs") or []) / dur)
order = sorted(by_bid)
t1 = np.array([by_bid[b][0] for b in order]); TB = np.array([by_bid[b][1] for b in order]); TS = np.array([by_bid[b][2] for b in order])
print("1m candles=%d" % len(t1), flush=True)


def slope_neg(y):
    return bool(np.polyfit(np.arange(len(y)), y, 1)[0] < 0) if len(y) >= 2 else None   # PYTHON bool (np.bool_ is not `is True`)


# rows: (year, side, resisted, abs_neg, abs_ll, abs_rel, opp_neg, abs_early_neg)
rows = []
for w in walls:
    side = w["side"]; runs = w.get("radar_runs", ())
    if not runs:
        continue
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n5 - 1))
    for (rk0, rk1, _pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n5 - 1:                                  # completed visit only (NO minimum length)
            continue
        t0 = st5[rk0]; tE = et5[rk1]
        j0 = int(np.searchsorted(t1, t0, "left")); j1 = int(np.searchsorted(t1, tE, "left"))
        if j1 - j0 < 2:                                    # need >=2 underlying 1m candles to read a trend
            continue
        tgt = (TS if side == "S" else TB)[j0:j1]           # absorbed side: S sellers / R buyers
        opp = (TB if side == "S" else TS)[j0:j1]
        f2 = tgt[:2].mean(); l2 = tgt[-2:].mean()
        abs_rel = (f2 - l2) / f2 if f2 > 0 else 0.0
        half = tgt[:max(2, len(tgt) // 2)]                 # EARLY half (causal) — slope over the first part of the visit
        rows.append((datetime.fromtimestamp(t0, tz=timezone.utc).year, side, (not (broken and rk0 <= i1 <= rk1 + 2)),
                     slope_neg(tgt), (l2 < f2), abs_rel, slope_neg(opp), slope_neg(half)))
print("visits=%d" % len(rows), flush=True)


def pct(sub, key):
    v = [key(r) for r in sub if key(r) is not None]
    return (100.0 * sum(v) / len(v), len(v)) if v else (float("nan"), 0)


print("\n[1] DESCRIPTIVE  P(tape fades | outcome) — 5m walls, 1m tape, ANY visit length:", flush=True)
for lbl, side in (("SUPPORT (buy) -> Tape-S", "S"), ("RESIST (sell) -> Tape-B", "R")):
    for tag, yr in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
        sub = [r for r in rows if r[1] == side and r[2] and (yr is None or r[0] == yr)]
        ps, N = pct(sub, lambda r: r[3]); pl, _ = pct(sub, lambda r: r[4]); po, _ = pct(sub, lambda r: r[6])
        md = float(np.median([r[5] for r in sub])) * 100 if sub else float("nan")
        print("  RESISTED %-24s [%-4s] n=%-5d  ABSORBED slope<0 %4.1f%% / last<first %4.1f%% (drop %+4.1f%%)  |  PASSIVE slope<0 %4.1f%%"
              % (lbl, tag, N, ps, pl, md, po), flush=True)
    brk = [r for r in rows if r[1] == side and not r[2]]
    pb, Nb = pct(brk, lambda r: r[3])
    print("  BROKEN   %-24s [BOTH] n=%-5d  ABSORBED slope<0 %4.1f%%" % (lbl, Nb, pb), flush=True)

print("\n[2] FORWARD  P(wall RESISTED | absorbed tape faded)  vs base resist rate:", flush=True)
for lbl, side in (("SUPPORT (buy)", "S"), ("RESIST (sell)", "R")):
    allv = [r for r in rows if r[1] == side]
    base = 100.0 * sum(1 for r in allv if r[2]) / len(allv) if allv else float("nan")
    # EARLY-half fade = causal (read before the visit ends); full-visit fade for reference
    for fade_name, idx in (("early-half fade", 7), ("full-visit fade", 3)):
        faded = [r for r in allv if r[idx] is True]; nofade = [r for r in allv if r[idx] is False]
        pf = 100.0 * sum(1 for r in faded if r[2]) / len(faded) if faded else float("nan")
        pn = 100.0 * sum(1 for r in nofade if r[2]) / len(nofade) if nofade else float("nan")
        print("  %-13s base P(resist)=%4.1f%% (n=%d)  |  %-16s: P(resist|fade)=%4.1f%% (n=%d)   P(resist|NO fade)=%4.1f%% (n=%d)"
              % (lbl, base, len(allv), fade_name, pf, len(faded), pn, len(nofade)), flush=True)
