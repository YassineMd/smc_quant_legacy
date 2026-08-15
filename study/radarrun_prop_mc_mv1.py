"""Prop-firm drawdown Monte-Carlo on the SHIPPED spec (MINVISIT=1, per-tf candle-SL + fixed 0.5% TP), 1h + native 30m.
Fixed-fractional sizing (risk R% at the stop -> acct_return = R*net/dist). Day-block bootstrap (resample whole days so
same-day clusters stay together). Reports hist + MC max drawdown and PASS-rate under two templates. 3bps slip, 0.04% fee."""
import os, sys, statistics, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003
random.seed(12345); np.random.seed(12345)
RISKS = [0.005, 0.0075, 0.010]
TEMPLATES = [("2-step 10/5/10", 0.10, 0.05, 0.10, "static"), ("1-step 6/4/9", 0.06, 0.04, 0.09, "trail")]
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
        trades.append({"day": int(ST[k] // 86400), "acctR": net / dist}); last = k + int(off)
    span = (ST[-1] - ST[0]) / 86400.0
    return trades, span


def mc_maxdd(days_list, R, ntrials=2000, max_days=400):
    out = []
    for _ in range(ntrials):
        eq = 0.0; peak = 0.0; tdd = 0.0
        for di in np.random.randint(0, len(days_list), size=max_days):
            for a1 in days_list[di]:
                eq += a1 * R; peak = max(peak, eq); tdd = max(tdd, peak - eq)
        out.append(tdd)
    return np.percentile(out, [50, 90, 99])


def mc_pass(days_list, R, total_dd, daily_dd, target, mode, ntrials=NMC, max_days=400):
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


def study(tf):
    trades, span = build_trades(tf)
    exp = np.mean([t["acctR"] for t in trades]); mo = span / 30.437
    print("\n########  %s  (MINVISIT=1, %d trades, %.0f days, %.0f/mo)  exp=%+.3fR ########"
          % (tf, len(trades), span, len(trades) / mo, exp), flush=True)
    byday = {}
    for t in trades:
        byday.setdefault(t["day"], []).append(t["acctR"])
    days_list = list(byday.values())
    for R in RISKS:
        acc = np.cumsum([a1 * R for t in trades for a1 in [t["acctR"]]]); peak = 0.0; tdd = 0.0
        for e in acc:
            peak = max(peak, e); tdd = max(tdd, peak - e)
        p50, p90, p99 = mc_maxdd(days_list, R)
        line = "  R=%.2f%%: total=%+.0f%%  histMaxDD=%.1f%%  MC-DD 50/90/99=%.1f/%.1f/%.1f%%" % (
            R * 100, acc[-1] * 100, tdd * 100, p50 * 100, p90 * 100, p99 * 100)
        print(line, flush=True)
        for name, tdl, ddl, tgt, mode in TEMPLATES:
            pp, ft, fd = mc_pass(days_list, R, tdl, ddl, tgt, mode)
            print("       %-15s PASS=%.0f%%  fail-total=%.0f%%  fail-daily=%.0f%%" % (name, 100 * pp, 100 * ft, 100 * fd), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "30m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
