"""FORWARD VALIDATION tracker for the Radar Runner. The recon backtest ends 2026-06-19; the LIVE cold-archive
(study/archive_data, pulled from GCS via study/pull_archive.ps1) holds everything AFTER that = genuine out-of-sample.
This detects the SHIPPED signal (MINVISIT=1, per-tf candle-SL + fixed 0.5% TP) on the live window for 1h + native-30m,
evaluates each RESOLVED trade, prints forward win/avg/exp-R vs the recon baseline, and writes a ledger to
study/out/radarrun_forward_ledger.csv. Re-run after `study/pull_archive.ps1` to accumulate as the daemon runs on.

RECON BASELINE (the number to reproduce): 1h ~84-88% win / +0.22R ; native-30m ~82-86% / +0.185R.
Usage: python study/radarrun_forward.py"""
import os, sys, csv, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003
BASELINE = {"1h": "recon 84-88% win / +0.22R", "30m": "recon 82-86% win / +0.185R"}


def _merge_lv(dst, b):
    for p, vv in (b.get("levels") or {}).items():
        e = dst.get(p)
        if e is None:
            dst[p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
        else:
            e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))


def build_native_30m(A15, T):
    out = []; acc = None
    for b in A15:
        if acc is None:
            acc = {"open_price": _f(b.get("open_price", b.get("open"))), "close_price": _f(b.get("close_price", b.get("close"))),
                   "high": _f(b.get("high")), "low": _f(b.get("low")), "buy_vol": _f(b.get("buy_vol")),
                   "sell_vol": _f(b.get("sell_vol")), "curr_vol": _f(b.get("curr_vol")), "start_time": b.get("start_time"),
                   "end_time": b.get("end_time"), "levels": {}}
            _merge_lv(acc["levels"], b)
        else:
            acc["close_price"] = _f(b.get("close_price", b.get("close")))
            acc["high"] = max(acc["high"], _f(b.get("high"))); acc["low"] = min(acc["low"], _f(b.get("low")))
            acc["buy_vol"] += _f(b.get("buy_vol")); acc["sell_vol"] += _f(b.get("sell_vol")); acc["curr_vol"] += _f(b.get("curr_vol"))
            acc["end_time"] = b.get("end_time"); _merge_lv(acc["levels"], b)
        if acc["curr_vol"] >= T:
            out.append(acc); acc = None
    if acc is not None:
        out.append(acc)
    return out


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def forward(tf):
    if tf == "30m":
        A15 = sorted(load_archive("15m")[1], key=lambda b: _f(b.get("start_time", 0)))    # default root = LIVE cold-archive
        tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
        T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
        A = build_native_30m(A15, T)
    else:
        A = sorted(load_archive(tf)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    slbuf = 0.002 if tf == "1h" else 0.003
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000
    rows = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        tp = entry * (1 + s * TP_FRAC)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        rows.append({"tf": tf, "ts": float(ST[k]), "side": s, "entry": entry, "sl": sl, "tp": tp,
                     "outcome": outc, "net": net, "R": net / dist}); last = k + int(off)
    span = (ST[-1] - ST[0]) / 86400.0 if n else 0
    return rows, (ST[0] if n else 0), (ST[-1] if n else 0), span


def main():
    print("==== RADAR RUNNER — FORWARD VALIDATION (live cold-archive, out-of-sample vs the recon backtest) ====")
    all_rows = []
    for tf in ("1h", "30m"):
        rows, t0, t1, span = forward(tf)
        res = [r for r in rows if r["outcome"] in ("tp", "sl")]         # resolved trades only
        d0 = datetime.fromtimestamp(t0, tz=timezone.utc).date(); d1 = datetime.fromtimestamp(t1, tz=timezone.utc).date()
        a = np.array([r["net"] for r in res]); rr = np.array([r["R"] for r in res])
        print("\n  %-4s  window %s -> %s (%.0f days)   signals=%d  resolved=%d  unresolved=%d"
              % (tf, d0, d1, span, len(rows), len(res), len(rows) - len(res)))
        if len(res):
            print("        FORWARD:  win=%.0f%%   avg=%+.3f%%/trade   exp=%+.3fR   total=%+.1fR"
                  % (100 * (a > 0).mean(), a.mean() * 100, rr.mean(), rr.sum()))
        print("        BASELINE: %s" % BASELINE[tf])
        all_rows += rows
    os.makedirs("study/out", exist_ok=True)
    with open("study/out/radarrun_forward_ledger.csv", "w", newline="") as f:
        wtr = csv.writer(f); wtr.writerow(["tf", "utc", "side", "entry", "sl", "tp", "outcome", "net_pct", "R"])
        for r in sorted(all_rows, key=lambda x: x["ts"]):
            wtr.writerow([r["tf"], datetime.fromtimestamp(r["ts"], tz=timezone.utc).isoformat(), r["side"],
                          "%.4f" % r["entry"], "%.4f" % r["sl"], "%.4f" % r["tp"], r["outcome"],
                          "%.4f" % (r["net"] * 100), "%.4f" % r["R"]])
    print("\n  ledger -> study/out/radarrun_forward_ledger.csv (%d signals). Re-run after study/pull_archive.ps1 to update."
          % len(all_rows))


if __name__ == "__main__":
    main()
