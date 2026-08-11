"""1m version of study/radar_visit_tape.py. The full 18-month 1m archive (~1.25M fat dicts) doesn't fit in RAM, so we
stream the chunks and keep only two CONTIGUOUS ~3-month windows (one 2025, one 2026) as LEAN buckets (per-print size
lists dropped after summing tape). Same logic + controls as the 5m study; both-year robustness via the two windows."""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

MINBARS = 5
ROOT = "study/recon_archive"; TF = "1m"
WINDOWS = [("2025", datetime(2025, 3, 1, tzinfo=timezone.utc).timestamp(), datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp()),
           ("2026", datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp(), datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())]
LO = min(w[1] for w in WINDOWS); HI = max(w[2] for w in WINDOWS)


def _tape_sums(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


win_buckets = {w[0]: {} for w in WINDOWS}                       # label -> {bid: lean bucket} (dedup by bid)
for fn in sorted(glob.glob(os.path.join(ROOT, TF, "%s_*.jsonl.gz" % TF))):
    with gzip.open(fn, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            st = _f(d.get("start_time"))
            if st < LO or st >= HI:
                continue
            for wl, a, b2 in WINDOWS:
                if a <= st < b2:
                    tb, ts = _tape_sums(d)
                    d["_tb"] = tb; d["_ts"] = ts
                    for k in ("sz_cb", "sz_cs", "sz_vb", "sz_vs"):   # drop the heavy per-print lists
                        d.pop(k, None)
                    win_buckets[wl][int(r["bid"])] = d
                    break
print("loaded lean:", {k: len(v) for k, v in win_buckets.items()}, flush=True)


def decline(series):
    y = np.asarray(series, float); m = len(y)
    slope = np.polyfit(np.arange(m), y, 1)[0]
    f2 = y[:2].mean(); l2 = y[-2:].mean()
    return slope < 0, l2 < f2, ((f2 - l2) / f2 if f2 > 0 else 0.0)


rows = []
for wl, _a, _b in WINDOWS:
    A = [win_buckets[wl][k] for k in sorted(win_buckets[wl])]
    A.sort(key=lambda b: _f(b.get("start_time")))
    n = len(A)
    TB = np.array([b["_tb"] for b in A]); TS = np.array([b["_ts"] for b in A])
    walls = AL.detect(A)
    print("[%s] bars=%d walls=%d" % (wl, n, len(walls)), flush=True)
    for w in walls:
        side = w["side"]; runs = w.get("radar_runs", ())
        if not runs:
            continue
        broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
        for (rk0, rk1, _pr) in runs:
            rk0 = int(rk0); rk1 = int(rk1)
            if (rk1 - rk0 + 1) < MINBARS or rk1 >= n - 1:
                continue
            is_break = broken and (rk0 <= i1 <= rk1 + 2)
            tgt = TS[rk0:rk1 + 1] if side == "S" else TB[rk0:rk1 + 1]
            opp = TB[rk0:rk1 + 1] if side == "S" else TS[rk0:rk1 + 1]
            ts_neg, ts_ll, ts_rel = decline(tgt); op_neg, op_ll, _ = decline(opp)
            rows.append((wl, side, (not is_break), ts_neg, ts_ll, ts_rel, op_neg, op_ll))


def pct(sub, idx):
    return (100.0 * sum(1 for r in sub if r[idx]) / len(sub), len(sub)) if sub else (float("nan"), 0)


def med_rel(sub):
    return float(np.median([r[5] for r in sub])) * 100 if sub else float("nan")


def report(tag, sub):
    p_s, N = pct(sub, 3); p_l, _ = pct(sub, 4); o_s, _ = pct(sub, 6); o_l, _ = pct(sub, 7)
    print("  %-26s n=%-4d  ABSORBED: slope<0 %4.1f%% / last<first %4.1f%% (median drop %+4.1f%%)   |   OPP: slope<0 %4.1f%% / last<first %4.1f%%"
          % (tag, N, p_s, p_l, med_rel(sub), o_s, o_l), flush=True)


print("\n=== 1m RESISTED visits (wall held & ejected), >=5 candles ===", flush=True)
for lbl, side in (("SUPPORT (buy) -> Tape-S", "S"), ("RESIST (sell) -> Tape-B", "R")):
    res = [r for r in rows if r[2] and r[1] == side]
    report(lbl + " [BOTH]", res)
    for y in ("2025", "2026"):
        report(lbl + " [%s]" % y, [r for r in res if r[0] == y])
print("\n=== 1m CONTROL: BROKEN visits ===", flush=True)
for lbl, side in (("SUPPORT broke -> Tape-S", "S"), ("RESIST broke -> Tape-B", "R")):
    report(lbl + " [BOTH]", [r for r in rows if (not r[2]) and r[1] == side])
print("\n(base rate: pure noise ~50%% slope<0 / last<first)", flush=True)
