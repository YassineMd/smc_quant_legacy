"""Are 5m Radar Runner signals that fire inside a 15m / 30m / 1h wall's radar winners / better? Placebo-controlled.
MEMORY-SAFE: the 5m recon is too big to hold in RAM (and the terminal is running), so 5m is STREAMED from the gz chunks
in a sliding 6000-bucket window (chunk 6000 / stride 5000 = the same windowing the in-memory studies use, just off disk);
only signals (compact) + the small HTF wall lists are kept. Shipped spec: MINVISIT=1, candle-SL(0.3%) + 0.5% TP. Both
recon years. Per HTF: OUTSIDE / INSIDE / INSIDE-placebo -> n / win% / avg%net / exp-R. 3bps, 0.04% fee."""
import os, sys, glob, gzip, json, random, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003; MAXT = 3; NPLAC = 3
CHUNK = 6000; STRIDE = 5000
random.seed(12345)


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


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


def build_walls_from(A):
    ST = [_f(b.get("start_time")) for b in A]; n = len(A); walls = []; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + CHUNK); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            P = _f(w.get("price")); band = _f(w.get("band"))
            if P <= 0 or band <= 0:
                continue
            i0 = int(w.get("i0", 0)) + c0
            i1 = (int(w.get("i1")) + c0) if (w.get("broken") and w.get("i1") is not None) else (n - 1)
            i0 = max(0, min(i0, n - 1)); i1 = max(i0, min(i1, n - 1))
            walls.append((ST[i0], ST[i1], P, RM * band))
        if c1 >= n:
            break
        c0 += STRIDE
    return walls


def get_htf_walls():
    A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    w15 = build_walls_from(A15)
    tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
    T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
    w30 = build_walls_from(build_native_30m(A15, T)); del A15
    A1h = sorted(load_archive("1h", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    w1h = build_walls_from(A1h); del A1h
    return {"15m": w15, "30m": w30, "1h": w1h}


def inside(walls, ts, price, shift=0.0):
    for (t0, t1, P, rad) in walls:
        if t0 <= ts <= t1 and abs(price - P * (1.0 + shift)) <= rad:
            return True
    return False


def stream_5m():
    for fn in sorted(glob.glob("study/recon_archive/5m/5m_*.jsonl.gz")):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line); d = r["data"]
                yield json.loads(d) if isinstance(d, str) else d


def extract_5m_signals(htf, shifts):
    buf = []; base = 0; ev = {}; done = set(); sigs = []; slbuf = 0.003; gen = stream_5m(); exhausted = False
    while True:
        while len(buf) < CHUNK and not exhausted:
            try:
                buf.append(next(gen))
            except StopIteration:
                exhausted = True
        n = len(buf)
        if n < 4:
            break
        O = np.array([_f(b.get("open", b.get("open_price"))) for b in buf]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in buf])
        Hi = np.array([_f(b.get("high")) for b in buf]); Lo = np.array([_f(b.get("low")) for b in buf]); STa = np.array([_f(b.get("start_time")) for b in buf])
        for w in AL.detect(buf, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]); b = int(r[1])
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT:
                        continue
                    gk = base + k
                    if (gk, side) not in ev:
                        ev[(gk, side)] = (rlo, rhi, 1 if side == "S" else -1)
                    break
        eval_end = base + (n if exhausted else n - H)
        for (gk, side) in sorted(ev):
            if (gk, side) in done or gk >= eval_end:
                continue
            lk = gk - base
            if lk < 0 or lk + 1 >= n:
                done.add((gk, side)); continue
            rlo, rhi, s = ev[(gk, side)]; entry = C[lk]; L = rhi - rlo
            brk = rhi if s > 0 else rlo; opp = rlo if s > 0 else rhi
            tiers = [brk + s * (N * L) for N in range(1, MAXT + 1)]; bias = 0
            for j in range(lk + 1, min(n, lk + 1 + H)):
                if (Lo[j] <= opp) if s > 0 else (Hi[j] >= opp):
                    break
                for N in range(MAXT, bias, -1):
                    if (Hi[j] >= tiers[N - 1]) if s > 0 else (Lo[j] <= tiers[N - 1]):
                        bias = N; break
                if bias >= MAXT:
                    break
            sl = max(Lo[lk] * (1 - slbuf), rlo) if s > 0 else min(Hi[lk] * (1 + slbuf), rhi)
            dist = abs(entry - sl) / entry
            if dist <= 0:
                done.add((gk, side)); continue
            j0 = lk + 1; j1 = min(n, lk + 1 + H)
            outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
            ts = float(STa[lk]); y = datetime.fromtimestamp(ts, tz=timezone.utc).year
            rec = {"gk": gk, "y": y, "net": net, "R": net / dist, "off": int(off)}
            for htfn, walls in htf.items():
                rec["in_" + htfn] = inside(walls, ts, entry); rec["pl_" + htfn] = any(inside(walls, ts, entry, shift=sh) for sh in shifts)
            sigs.append(rec); done.add((gk, side))
        if exhausted:
            break
        del buf[:STRIDE]; base += STRIDE
    return sigs


def main():
    htf = get_htf_walls()
    print("HTF walls: 15m=%d 30m=%d 1h=%d" % (len(htf["15m"]), len(htf["30m"]), len(htf["1h"])), flush=True)
    shifts = [random.uniform(0.01, 0.03) * random.choice((-1, 1)) for _ in range(NPLAC)]
    sigs = extract_5m_signals(htf, shifts)
    taken = []; last = -1
    for r in sorted(sigs, key=lambda x: x["gk"]):
        if r["gk"] <= last:
            continue
        taken.append(r); last = r["gk"] + r["off"]
    print("5m signals: %d raw -> %d taken (non-overlap)" % (len(sigs), len(taken)), flush=True)

    def stat(sub):
        a = np.array([x["net"] for x in sub]); rr = np.array([x["R"] for x in sub])
        return (len(a), 100 * (a > 0).mean() if len(a) else 0, a.mean() * 100 if len(a) else 0, rr.mean() if len(rr) else 0)

    for Y in (2025, 2026):
        yr = [x for x in taken if x["y"] == Y]
        bn, bw, ba, be = stat(yr)
        print("\n  %d  ALL 5m: n=%-4d win=%.0f%% avg=%+.3f%% expR=%+.3f" % (Y, bn, bw, ba, be), flush=True)
        for htfn in ("15m", "30m", "1h"):
            for gtag, sel in (("outside", lambda x, h=htfn: not x["in_" + h]), ("INSIDE ", lambda x, h=htfn: x["in_" + h]),
                              ("placebo", lambda x, h=htfn: x["pl_" + htfn])):
                g = [x for x in yr if sel(x)]
                if len(g) < 15:
                    print("      %s-wall %s n=%d (<15)" % (htfn, gtag, len(g))); continue
                nn, ww, aa, ee = stat(g)
                print("      %s-wall %s n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (htfn, gtag, nn, ww, aa, ee), flush=True)


if __name__ == "__main__":
    main()
