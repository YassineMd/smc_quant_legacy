"""RESISTED-wall Radar Runner stratified by VISIT LENGTH. The shipped detector gates MINVISIT=3 (the wall must be tested
>=3 bars before the breakout). Does a SHORTER visit (0/1/2 bars) still produce the run, or does the edge need the test?

Drop the gate, record vlen = breakout_k - visit_start, stratify {0,1,2,>=3}. Per stratum/TF(1h, native-30m)/year:
base P(reach >=1x radar-length before the opposite extreme), P(>=2x), and the tradeable quick-TP spec (candle-capped SL
per-tf 0.2%1h/0.3%30m + fixed 0.5% TP) win/avg%net at 3bps slip. >=3 is the shipped reference. 0.04% RT fee.
CLI: python study/wall_radarrun_visitlen.py [tf ...]"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003; MAXT = 3


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
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
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
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)   # RESISTED -> breaks the DEFENDED extreme
                    if not broke or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi, k - a); break                # record visit length k-a (no MINVISIT gate)
        if c1 >= n:
            break
        c0 += step - 1000

    rows = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi, vlen = ev[(k, side)]; s = 1 if side == "S" else -1; L = rhi - rlo; entry = C[k]
        brk = rhi if side == "S" else rlo; opp = rlo if side == "S" else rhi
        tiers = [brk + s * (N * L) for N in range(1, MAXT + 1)]
        bias = 0
        for j in range(k + 1, min(n, k + 1 + H)):
            if (Lo[j] <= opp) if s > 0 else (Hi[j] >= opp):
                break
            for N in range(MAXT, bias, -1):
                if (Hi[j] >= tiers[N - 1]) if s > 0 else (Lo[j] <= tiers[N - 1]):
                    bias = N; break
            if bias >= MAXT:
                break
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        vb = 0 if vlen <= 0 else (1 if vlen == 1 else (2 if vlen == 2 else 3))   # 3 == ">=3"
        rows.append((k, int(yr[k]), vb, bias, outc, gross, off))
    print("\n########  TF = %s   RESISTED breakout by VISIT LENGTH   (events=%d)  ########" % (tf, len(rows)), flush=True)
    LBL = {0: "visit=0 ", 1: "visit=1 ", 2: "visit=2 ", 3: "visit>=3"}
    for vb in (0, 1, 2, 3):
        row = "  %s" % LBL[vb]
        for Y in (2025, 2026):
            sub = [r for r in rows if r[2] == vb and r[1] == Y]
            if len(sub) < 15:
                row += "  |%d n=%-4d (<15)" % (Y, len(sub)); continue
            b = [r[3] for r in sub]; p1 = 100 * np.mean([x >= 1 for x in b]); p2 = 100 * np.mean([x >= 2 for x in b])
            acc = []; last = -1
            for r in sorted(sub):
                if r[0] <= last:
                    continue
                net = r[5] - FEE - SLIP - (SLIP if r[4] != "tp" else 0.0)
                acc.append(net); last = r[0] + int(r[6])
            a = np.array(acc)
            row += "  |%d n=%-4d P1=%.0f%% P2=%.0f%%  qTP win=%2.0f%% avg=%+.3f%%" % (
                Y, len(sub), p1, p2, 100 * (a > 0).mean() if len(a) else 0, a.mean() * 100 if len(a) else 0)
        print(row, flush=True)
    print("  (P1=reach>=1x, P2=>=2x; qTP=candle-SL+0.5%TP at 3bps; visit>=3 = the shipped spec)", flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "30m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
