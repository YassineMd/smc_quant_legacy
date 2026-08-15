"""Combined 1h + native-30m Radar Runner (MINVISIT=1): TIME to pass a 1-step 10% target. Merges both signal streams
chronologically, measures their overlap (correlated same-breakout signals), and runs a day-block-bootstrap MC to get the
PASS rate + MEDIAN days-to-target at R=0.5/0.75/1%, under a strict (6% DD/4% daily) and a lenient (10% DD/5% daily) 1-step.
Day-block bootstrap keeps same-day 1h+30m trades together so the correlation shows in the drawdown. 3bps slip, 0.04% fee."""
import os, sys, statistics, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003
np.random.seed(12345)
RISKS = [0.005, 0.0075, 0.010]
TEMPLATES = [("1-step strict 6/4/10", 0.06, 0.04, 0.10, "trail"), ("1-step lenient 10/5/10", 0.10, 0.05, 0.10, "static")]
NMC = 4000


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


def get_buckets(tf):
    if tf == "30m":
        A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
        tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
        T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
        return build_native_30m(A15, T)
    return sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def build_trades(tf):
    A = get_buckets(tf); n = len(A)
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
    trades = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        trades.append({"ts": float(ST[k]), "side": s, "acctR": net / dist}); last = k + int(off)
    span = (ST[-1] - ST[0]) / 86400.0
    return trades, span


def mc_ttt(days_list, R, total_dd, daily_dd, target, mode, ntrials=NMC, max_days=400):
    npass = ftot = fday = 0; ttp = []
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; done = False
        for di_i, di in enumerate(np.random.randint(0, len(days_list), size=max_days), start=1):
            dstart = eq
            for a1 in days_list[di]:
                eq += a1 * R; peak = max(peak, eq)
                dd = (peak - eq) if mode == "trail" else (-eq)
                if dd >= total_dd:
                    ftot += 1; done = True; break
                if (dstart - eq) >= daily_dd:
                    fday += 1; done = True; break
                if eq >= target:
                    npass += 1; ttp.append(di_i); done = True; break
            if done:
                break
    return npass / ntrials, ftot / ntrials, fday / ntrials, (np.median(ttp) if ttp else float("nan"))


def main():
    t1, s1 = build_trades("1h"); t30, s30 = build_trades("30m")
    span = max(s1, s30); mo = span / 30.437
    allt = sorted(t1 + t30, key=lambda t: t["ts"])
    exp = np.mean([t["acctR"] for t in allt])
    # overlap: 1h signals with a same-side 30m within +-1h
    by = {1: sorted(t["ts"] for t in t30 if t["side"] == 1), -1: sorted(t["ts"] for t in t30 if t["side"] == -1)}
    near = sum(1 for t in t1 if bisect.bisect_right(by[t["side"]], t["ts"] + 3600) - bisect.bisect_left(by[t["side"]], t["ts"] - 3600) > 0)
    byday = {}
    for t in allt:
        byday.setdefault(int(t["ts"] // 86400), []).append(t["acctR"])
    days_list = list(byday.values()); active = len(byday); total_days = int(span) + 1
    print("COMBINED 1h + native-30m (MINVISIT=1)  span=%.0f days" % span)
    print("  1h=%d  30m=%d  COMBINED=%d trades  (%.0f/mo, %.2f/day)  blended exp=%+.3fR  active %d/%d days (%.0f%%)"
          % (len(t1), len(t30), len(allt), len(allt) / mo, len(allt) / span, exp, active, total_days, 100 * active / total_days))
    print("  overlap: %d/%d (%.0f%%) of 1h signals have a same-side 30m within +-1h (correlated exposure)"
          % (near, len(t1), 100 * near / len(t1)))
    print("  ~1 day-block == 1 active day (combined is active ~%.0f%% of days -> days-to-pass ~= calendar days):" % (100 * active / total_days))
    for name, tdl, ddl, tgt, mode in TEMPLATES:
        print("  --- %s ---" % name)
        for R in RISKS:
            pp, ft, fd, med = mc_ttt(days_list, R, tdl, ddl, tgt, mode)
            cal = med * total_days / active if med == med else float("nan")   # active days -> calendar days
            print("    R=%.2f%%: PASS=%.0f%%  fail-total=%.0f%%  fail-daily=%.0f%%  median-days-to-10%%=%s (~%.1f weeks)"
                  % (R * 100, 100 * pp, 100 * ft, 100 * fd, ("%.0f" % med) if med == med else "n/a", (cal / 7) if cal == cal else float("nan")))


if __name__ == "__main__":
    main()
