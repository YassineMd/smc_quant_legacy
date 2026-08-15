"""MIRROR of the Radar Runner: a wall that FAILED to resist. Same visit structure (radar visit >= MINVISIT bars, breakout
bar opens INSIDE the radar) but the close breaks BEYOND THE ATTACKED extreme (the wall is run over):
  support wall S broken DOWN  (close < radar_lo) -> SHORT (cascade down)
  resistance  R broken UP     (close > radar_hi) -> LONG  (breakout up)
Does price then RUN in the attack direction (momentum cascade), or is it a false break that RECLAIMS (mean-revert)?

Same study as the resisted wall: (1) directional base rate P(reach 1x radar-length in the attack dir BEFORE the opposite
extreme), (2) tier ladder P(>=1x/2x/3x), (3) the shipped tradeable spec = candle-capped SL (per-tf 0.2% 1h / 0.3% 30m+)
+ fixed 0.5% TP, win/avg%net/net/avg-loser at slippage 0/3/6bps. Both recon years, 1h / native-30m / 15m. Sequential,
one load per TF. CLI: python study/wall_failed_breakout.py [tf ...]"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; H = 200; FEE = 0.0004
TP_FRAC = 0.005; SLIPS = [0.0, 0.0003, 0.0006]; MAXT = 3


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


def sim_scale(side, entry, sl0, targets, weights, ph, pl, pc, be):
    sl = sl0; pos = 1.0; realized = 0.0; ti = 0; nt = len(targets)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return realized + pos * side * (sl - entry) / entry, off + 1
        while ti < nt and ((hi >= targets[ti]) if side > 0 else (lo <= targets[ti])):
            realized += weights[ti] * side * (targets[ti] - entry) / entry; pos -= weights[ti]; ti += 1
            if be and ti == 1:
                sl = entry
            if pos <= 1e-9:
                return realized, off + 1
    return realized + (pos * side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def sim_trail(side, entry, sl0, tiers, ph, pl, pc):
    sl = sl0; reached = 0; nt = len(tiers)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl - entry) / entry, off + 1
        while reached < nt and ((hi >= tiers[reached]) if side > 0 else (lo <= tiers[reached])):
            reached += 1; sl = entry if reached == 1 else tiers[reached - 2]
            if reached >= nt:
                return side * (tiers[-1] - entry) / entry, off + 1
    return (side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


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
                    failed = (C[k] < rlo) if side == "S" else (C[k] > rhi)   # broke THROUGH the wall (attacked extreme)
                    if not failed or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000

    rows = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; s = -1 if side == "S" else 1            # failed S -> short / failed R -> long
        L = rhi - rlo; entry = C[k]
        brk = rlo if side == "S" else rhi                                # the extreme it broke through
        opp = rhi if side == "S" else rlo                                # opposite extreme = bias SL
        tiers = [brk + s * (N * L) for N in range(1, MAXT + 1)]
        # bias: MAX tier reached in the attack dir BEFORE the opposite extreme (SL-first tie); don't stop at 1x
        bias = 0
        for j in range(k + 1, min(n, k + 1 + H)):
            if (Hi[j] >= opp) if s < 0 else (Lo[j] <= opp):              # opposite extreme touched -> stop
                break
            for N in range(MAXT, bias, -1):                             # extend the highest tier reached so far
                if (Lo[j] <= tiers[N - 1]) if s < 0 else (Hi[j] >= tiers[N - 1]):
                    bias = N; break
            if bias >= MAXT:
                break
        # tradeable: candle-capped SL + fixed TP
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, ph, pl, pc)
        rBE, oBE = sim_scale(s, entry, opp, tiers, [1 / 3.0, 1 / 3.0, 1 / 3.0], ph, pl, pc, True)
        rTR, oTR = sim_trail(s, entry, opp, tiers, ph, pl, pc)
        rows.append((k, int(yr[k]), bias, outc, gross, off, rBE, oBE, rTR, oTR))
    R = rows
    print("\n########  TF = %s   FAILED-WALL breakout   (events=%d)  ########" % (tf, len(R)), flush=True)
    if len(R) < 40:
        print("  too few events"); return
    for Y in (2025, 2026):
        b = [r[2] for r in R if r[1] == Y]
        if not b:
            continue
        nb = len(b); p1 = 100 * np.mean([x >= 1 for x in b]); p2 = 100 * np.mean([x >= 2 for x in b]); p3 = 100 * np.mean([x >= 3 for x in b])
        print("  %d  n=%-4d  base P(reach>=1x)=%.1f%%  (>=2x %.1f%%  >=3x %.1f%%)   [resisted ~75/51/40]" % (Y, nb, p1, p2, p3), flush=True)
    print("  -- CONTINUATION quick-TP (candle-SL %s + fixed TP0.5%%) --" % ("0.2%" if tf == "1h" else "0.3%"), flush=True)
    for slip in SLIPS:
        line = "     slip%.0fbps" % (slip * 1e4)
        for Y in (2025, 2026):
            acc = []; last = -1
            for r in R:
                if r[1] != Y or r[0] <= last:
                    continue
                net = r[4] - FEE - slip - (slip if r[3] != "tp" else 0.0)
                acc.append(net); last = r[0] + int(r[5])
            a = np.array(acc); los = a[a < 0]
            line += "  |%d n=%-4d win=%2.0f%% avg=%+.3f%% net=%+.0f%% L=%.2f%%" % (
                Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100, los.mean() * 100 if len(los) else 0)
        print(line, flush=True)
    print("  -- CONTINUATION tiered (structural SL = opp extreme, tiers 1x/2x/3x, 0.04%% fee) --", flush=True)
    for schname, ri, oi in (("scale+BE", 6, 7), ("trail", 8, 9)):
        line = "     %-9s" % schname
        for Y in (2025, 2026):
            acc = []; last = -1
            for r in R:
                if r[1] != Y or r[0] <= last:
                    continue
                acc.append(r[ri] - FEE); last = r[0] + int(r[oi])
            a = np.array(acc)
            line += "  |%d n=%-4d win=%2.0f%% avg=%+.3f%% net=%+.0f%%" % (
                Y, len(a), 100 * (a > 0).mean() if len(a) else 0, a.mean() * 100 if len(a) else 0, a.sum() * 100 if len(a) else 0)
        print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "30m", "15m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
