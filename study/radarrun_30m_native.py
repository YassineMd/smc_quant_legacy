"""NATIVE 30m reconstruction + drawdown MC. Builds 30m by TRUE cumulative-volume cutting from the 5m recon (cut a bucket
each time cumulative curr_vol >= 30m target = 6x the 5m target) -> volume-paced boundaries at 5m precision, uniform
volume/bar (fixes the 15m-pair proxy's variable-volume flaw). Merges OHLC/volumes/`levels` footprint faithfully. Then:
  (1) census + TP x slippage robustness sweep (cand+0.3cap SL) -> compare to the proxy,
  (2) fixed-fractional drawdown Monte-Carlo + prop pass-rate under two templates.
Loads ONLY 5m (never 1m). CLI: python study/radarrun_30m_native.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; H = 200; FEE = 0.0004
TPS = [0.003, 0.004, 0.005]; SLIPS = [0.0, 0.0003, 0.0006]
random.seed(12345); np.random.seed(12345)
RISKS = [0.005, 0.0075, 0.010]
TEMPLATES = [("2-step 10/5/10", 0.10, 0.05, 0.10, "static"), ("1-step 6/4/9", 0.06, 0.04, 0.09, "trail")]
NMC = 4000


def finalize(a):
    poc = float(max(a["levels"].items(), key=lambda kv: kv[1]["b"] + kv[1]["s"])[0]) if a["levels"] else None
    return {"open_price": a["op"], "close_price": a["cl"], "high": a["hi"], "low": a["lo"], "buy_vol": a["bv"],
            "sell_vol": a["sv"], "curr_vol": a["cv"], "start_time": a["st"], "end_time": a["et"],
            "levels": a["levels"], "poc_price": poc}


def build_native_30m(A5, T):
    out = []; acc = None
    for b in A5:
        o = _f(b.get("open_price", b.get("open"))); c = _f(b.get("close_price", b.get("close")))
        hi = _f(b.get("high")); lo = _f(b.get("low")); cv = _f(b.get("curr_vol"))
        if acc is None:
            acc = {"op": o, "cl": c, "hi": hi, "lo": lo, "bv": _f(b.get("buy_vol")), "sv": _f(b.get("sell_vol")),
                   "cv": cv, "st": b.get("start_time"), "et": b.get("end_time"), "levels": {}}
        else:
            acc["cl"] = c; acc["hi"] = max(acc["hi"], hi); acc["lo"] = min(acc["lo"], lo)
            acc["bv"] += _f(b.get("buy_vol")); acc["sv"] += _f(b.get("sell_vol")); acc["cv"] += cv
            acc["et"] = b.get("end_time")
        for p, vv in (b.get("levels") or {}).items():
            e = acc["levels"].get(p)
            if e is None:
                acc["levels"][p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
            else:
                e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))
        if acc["cv"] >= T:
            out.append(finalize(acc)); acc = None
    if acc is not None:
        out.append(finalize(acc))
    return out


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def mc_maxdd(days_list, R, ntrials=2000, max_days=300):
    out = []
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; tdd = 0.0
        pick = np.random.randint(0, len(days_list), size=max_days)
        for di in pick:
            for a1 in days_list[di]:
                eq += a1 * R; peak = max(peak, eq); tdd = max(tdd, peak - eq)
        out.append(tdd)
    return np.percentile(out, [50, 90, 99])


def mc_pass(days_list, R, total_dd, daily_dd, target, mode, ntrials=NMC, max_days=300):
    npass = ftot = fday = 0
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; done = False
        for di in np.random.randint(0, len(days_list), size=max_days):
            dstart = eq
            for a1 in days_list[di]:
                eq += a1 * R; peak = max(peak, eq)
                dd = (peak - eq) if mode == "trail" else (-eq)
                if dd >= total_dd:
                    ftot += 1; done = True; break
                if (dstart - eq) >= daily_dd:
                    fday += 1; done = True; break
                if eq >= target:
                    npass += 1; done = True; break
            if done:
                break
    return npass / ntrials, ftot / ntrials, fday / ntrials


def main():
    _, A15, _ = load_archive("15m", root="study/recon_archive")
    A15.sort(key=lambda b: _f(b.get("start_time", 0)))
    tv = [_f(b.get("target_vol")) for b in A15 if b.get("target_vol")]
    T = 2.0 * (np.median(tv) if tv else np.median([_f(b.get("curr_vol")) for b in A15]))
    A = build_native_30m(A15, T)
    print("NATIVE 30m (volume-accumulation): built %d buckets from %d 15m buckets (target vol/bar T=%.0f = 2x 15m)"
          % (len(A), len(A15), T), flush=True)
    del A15
    n = len(A)
    O = np.array([_f(b.get("open_price")) for b in A]); C = np.array([_f(b.get("close_price")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

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

    rows = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.003), rlo) if up else min(Hi[k] * (1 + 0.003), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        d = {tp: sim(s, entry, entry * (1 + s * tp), sl, ph, pl, pc) for tp in TPS}
        rows.append((k, int(yr[k]), int(ST[k] // 86400), dist, d))
    span = (ST[-1] - ST[0]) / 86400.0; mo = span / 30.437

    # census (non-overlap at TP0.5, 3bps)
    taken = []; last = -1
    for r in rows:
        if r[0] <= last:
            continue
        taken.append(r); last = r[0] + int(r[4][0.005][2])
    nl = sum(1 for (k, side) in ev if side == "S")
    accts = []
    for (k, y, day, dist, d) in taken:
        outc, gross, off = d[0.005]; net = gross - FEE - 0.0003 - (0.0003 if outc != "tp" else 0.0)
        accts.append({"day": day, "year": y, "acctR": net / dist})
    exp = np.mean([a["acctR"] for a in accts]); win = 100 * np.mean([1.0 if a["acctR"] > 0 else 0.0 for a in accts])
    print("  census: raw=%d (L%d/S%d)  tradeable=%d  %.1f/mo  win=%.0f%%  exp(TP0.5,3bps)=%+.3fR"
          % (len(ev), len(ev) - nl, nl, len(taken), len(taken) / mo, win, exp), flush=True)
    print("  robustness sweep (avg %%/trade net):", flush=True)
    for tp in TPS:
        line = "    TP=%.1f%%" % (tp * 100)
        for slip in SLIPS:
            r25 = []; r26 = []; last = -1
            for r in rows:
                if r[0] <= last:
                    continue
                outc, gross, off = r[4][tp]; net = gross - FEE - slip - (slip if outc != "tp" else 0.0)
                (r25 if r[1] == 2025 else r26).append(net); last = r[0] + int(off)
            line += "  |%.0fbps 25:%+.3f 26:%+.3f" % (slip * 1e4, np.mean(r25) * 100, np.mean(r26) * 100)
        print(line, flush=True)

    # drawdown MC
    byday = {}
    for a in accts:
        byday.setdefault(a["day"], []).append(a["acctR"])
    days_list = list(byday.values())
    print("\n  DRAWDOWN (fixed-fractional, TP0.5/cand+0.3cap, 3bps):", flush=True)
    for R in RISKS:
        eq = np.cumsum([a["acctR"] * R for a in accts]); peak = 0.0; tdd = 0.0
        for e in eq:
            peak = max(peak, e); tdd = max(tdd, peak - e)
        p50, p90, p99 = mc_maxdd(days_list, R)
        print("    R=%.2f%%: total=%+.1f%%  hist maxDD=%.1f%%  MC maxDD 50/90/99= %.1f/%.1f/%.1f%%"
              % (R * 100, eq[-1] * 100, tdd * 100, p50 * 100, p90 * 100, p99 * 100), flush=True)
        for name, tdl, ddl, tgt, mode in TEMPLATES:
            pp, ft, fd = mc_pass(days_list, R, tdl, ddl, tgt, mode)
            print("       %-15s PASS=%.0f%%  fail-total=%.0f%%  fail-daily=%.0f%%" %
                  (name, 100 * pp, 100 * ft, 100 * fd), flush=True)


if __name__ == "__main__":
    main()
