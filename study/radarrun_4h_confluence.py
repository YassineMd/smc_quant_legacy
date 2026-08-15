"""Do 30m/1h Radar Runner signals that fire INSIDE a 4h wall's radar area have better RR? A lower-TF breakout inside a
higher-TF (4h) radar might ride the 4h structure and run further (room for a bigger TP). Controlled with a PLACEBO (4h
radars shifted to random levels) -- 'inside a WIDE zone' isn't special unless it beats a random wide zone.

Per TF (30m native, 1h), both recon years, split OUTSIDE / INSIDE-4h / INSIDE-placebo: tradeable win/avg%net/expR
(shipped spec MINVISIT=1, candle-SL+0.5%TP) AND the TIER LADDER P(reach >=1x/2x/3x radar-length) -- the RR signal (if
inside reaches higher tiers, a bigger TP pays). 4h wall = AL radar [P +/- 3*band] active over its [form,break] window.
3bps slip, 0.04% fee. CLI: python study/radarrun_4h_confluence.py"""
import os, sys, statistics, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003; MAXT = 3; NPLAC = 3
random.seed(12345)


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


def build_4h_walls():
    A = sorted(load_archive("4h", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    ST = [_f(b.get("start_time")) for b in A]; n = len(A); walls = []; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 4000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            P = _f(w.get("price")); band = _f(w.get("band"))
            if P <= 0 or band <= 0:
                continue
            i0 = int(w.get("i0", 0)) + c0; i1 = (int(w.get("i1")) + c0) if (w.get("broken") and w.get("i1") is not None) else (n - 1)
            i0 = max(0, min(i0, n - 1)); i1 = max(i0, min(i1, n - 1))
            walls.append((ST[i0], ST[i1], P, RM * band))         # (t_form, t_end, price, radar_half)
        if c1 >= n:
            break
        c0 += 3999
    return walls


def inside(walls, ts, price, shift=0.0):
    for (t0, t1, P, rad) in walls:
        if t0 <= ts <= t1 and abs(price - P * (1.0 + shift)) <= rad:
            return True
    return False


def study(tf, walls, shifts):
    A = get_buckets(tf); n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    slbuf = 0.002 if tf == "1h" else 0.003
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
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
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]; L = rhi - rlo
        brk = rhi if s > 0 else rlo; opp = rlo if s > 0 else rhi
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
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        y = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        ins = inside(walls, ST[k], entry)
        insp = any(inside(walls, ST[k], entry, shift=sh) for sh in shifts)
        rows.append((y, net, net / dist, bias, ins, insp)); last = k + int(off)

    def stat(sub):
        a = np.array([x[1] for x in sub]); rr = np.array([x[2] for x in sub]); bb = [x[3] for x in sub]
        return (len(a), 100 * (a > 0).mean() if len(a) else 0, a.mean() * 100 if len(a) else 0, rr.mean() if len(rr) else 0,
                100 * np.mean([b >= 1 for b in bb]) if bb else 0, 100 * np.mean([b >= 2 for b in bb]) if bb else 0,
                100 * np.mean([b >= 3 for b in bb]) if bb else 0)

    print("\n########  TF = %s  (n=%d)  ########" % (tf, len(rows)), flush=True)
    for Y in (2025, 2026):
        yr = [x for x in rows if x[0] == Y]
        for gname, sel in (("OUTSIDE   ", lambda x: not x[4]), ("INSIDE-4h ", lambda x: x[4]), ("INSIDE-plac", lambda x: x[5])):
            g = [x for x in yr if sel(x)]
            if len(g) < 15:
                print("  %d %s n=%d (<15)" % (Y, gname, len(g))); continue
            nn, ww, aa, ee, p1, p2, p3 = stat(g)
            print("  %d %s n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f  tiers>=1/2/3=%2.0f/%2.0f/%2.0f%%" % (
                Y, gname, nn, ww, aa, ee, p1, p2, p3), flush=True)


def main():
    walls = build_4h_walls()
    print("4h walls (radar_mult=%.1f): %d" % (RM, len(walls)), flush=True)
    shifts = [random.uniform(0.02, 0.04) * random.choice((-1, 1)) for _ in range(NPLAC)]
    for tf in ("30m", "1h"):
        study(tf, walls, shifts)


if __name__ == "__main__":
    main()
