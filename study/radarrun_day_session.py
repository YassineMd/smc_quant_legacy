"""Radar Runner (shipped spec: MINVISIT=1, per-tf candle-SL + 0.5% TP) stratified by DAY-OF-WEEK (Mon-Sun UTC) and
SESSION (terminal convention: Tokyo 00-08 / London 08-13 / New York 13-21 / Off 21-24 UTC). Recon, BOTH years shown
separately so 'best/worst' must be CONSISTENT across years (day-of-week mining is a false-pattern trap). 1h + native-30m.
Reports n / win% / avg%net per stratum + the all-trades baseline. 3bps slip, 0.04% fee. CLI: [tf ...]"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _sess(h):
    return "Tokyo" if h < 8 else ("London" if h < 13 else ("NewYork" if h < 21 else "Off"))


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


def study(tf):
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
    T = []; last = -1
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
        dt = datetime.fromtimestamp(ST[k], tz=timezone.utc)
        T.append((dt.year, dt.weekday(), _sess(dt.hour), net)); last = k + int(off)

    def cell(sub):
        a = np.array([x[3] for x in sub])
        return (len(a), 100 * (a > 0).mean() if len(a) else 0, a.mean() * 100 if len(a) else 0)

    print("\n########  TF = %s  (n=%d)  ########" % (tf, len(T)), flush=True)
    for Y in (2025, 2026):
        bn, bw, ba = cell([x for x in T if x[0] == Y])
        print("  %d baseline: n=%-4d win=%.0f%% avg=%+.3f%%" % (Y, bn, bw, ba), flush=True)
    print("  --- DAY OF WEEK ---", flush=True)
    for d in range(7):
        row = "    %s" % DOW[d]
        for Y in (2025, 2026):
            nn, ww, aa = cell([x for x in T if x[0] == Y and x[1] == d])
            row += "  | %d n=%-4d win=%2.0f%% avg=%+.3f%%" % (Y, nn, ww, aa)
        print(row, flush=True)
    print("  --- SESSION (UTC) ---", flush=True)
    for sname in ("Tokyo", "London", "NewYork", "Off"):
        row = "    %-8s" % sname
        for Y in (2025, 2026):
            nn, ww, aa = cell([x for x in T if x[0] == Y and x[2] == sname])
            row += "  | %d n=%-4d win=%2.0f%% avg=%+.3f%%" % (Y, nn, ww, aa)
        print(row, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "30m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
