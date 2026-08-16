"""Should the Radar Runner TP adapt to VELOCITY? User's observation: in a slow tape (Jul-Aug 2026) the fixed 0.5% TP is
'nearly impossible' to reach. Test, DESCRIPTIVELY, on recon (2025-01..2026-06) + forward cold-archive (2026-06-20..now),
1h + native-30m, same entry + candle-capped SL. Compare TP schemes:
  fix0.3/0.4/0.5  - fixed % (0.5 = the shipped baseline)
  vel{m}          - velocity-scaled: TP = m * recent_velocity (mean |ret|/bar over the last 14 bars, CAUSAL), clamp [0.2%,1.0%]
  RR{r}           - risk-multiple: TP = r * SL-distance (adapts to the candle-capped stop, not the clock)
Per scheme: TP-reach% (of exits), win% (net>0), avg%net, exp-R, and a LOW vs HIGH recent-velocity split (does the slow
regime specifically want a smaller TP?). 3bps slip, 0.04% fee. python study/radarrun_tp_velocity.py"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; VK = 14


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
                   "high": _f(b.get("high")), "low": _f(b.get("low")), "curr_vol": _f(b.get("curr_vol")),
                   "buy_vol": _f(b.get("buy_vol")), "sell_vol": _f(b.get("sell_vol")),
                   "start_time": b.get("start_time"), "end_time": b.get("end_time"), "levels": {}}
            _merge_lv(acc["levels"], b)
        else:
            acc["close_price"] = _f(b.get("close_price", b.get("close")))
            acc["high"] = max(acc["high"], _f(b.get("high"))); acc["low"] = min(acc["low"], _f(b.get("low")))
            acc["curr_vol"] += _f(b.get("curr_vol")); acc["end_time"] = b.get("end_time")
            acc["buy_vol"] += _f(b.get("buy_vol")); acc["sell_vol"] += _f(b.get("sell_vol")); _merge_lv(acc["levels"], b)
        if acc["curr_vol"] >= T:
            out.append(acc); acc = None
    if acc is not None:
        out.append(acc)
    return out


def get_buckets(tf, root):
    if tf == "30m":
        A15 = sorted(load_archive("15m", **root)[1], key=lambda b: _f(b.get("start_time", 0)))
        tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
        T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
        return build_native_30m(A15, T)
    return sorted(load_archive(tf, **root)[1], key=lambda b: _f(b.get("start_time", 0)))


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        if (pl[off] <= sl) if s > 0 else (ph[off] >= sl):
            return "sl", s * (sl - entry) / entry
        if (ph[off] >= tp) if s > 0 else (pl[off] <= tp):
            return "tp", s * (tp - entry) / entry
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0)


def signals(A, slbuf):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    aret = np.abs(C - O) / np.where(O > 0, O, 1.0)                    # per-bar |return|
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
    sigs = []; last = -1
    for (k, side) in sorted(ev):
        if k + 1 >= n or k <= last or k < VK:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - slbuf), rlo) if s > 0 else min(Hi[k] * (1 + slbuf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        vel = float(aret[k - VK:k].mean())                           # CAUSAL recent velocity (excludes the breakout bar)
        dt = datetime.fromtimestamp(ST[k], tz=timezone.utc)
        sigs.append({"y": dt.year, "k": k, "s": s, "entry": entry, "sl": sl, "dist": dist, "vel": vel})
        last = k + 1                                                  # provisional; refined per-scheme below is overkill -> keep non-overlap simple
    return sigs, Hi, Lo, C


SCHEMES = [
    ("fix0.3", lambda g: 0.003), ("fix0.4", lambda g: 0.004), ("fix0.5*", lambda g: 0.005),
    ("vel1.0", lambda g: min(0.010, max(0.002, 1.0 * g["vel"]))),
    ("vel1.5", lambda g: min(0.010, max(0.002, 1.5 * g["vel"]))),
    ("vel2.0", lambda g: min(0.010, max(0.002, 2.0 * g["vel"]))),
    ("RR1.5",  lambda g: 1.5 * g["dist"]), ("RR2.0", lambda g: 2.0 * g["dist"]),
]


def evaluate(sigs, Hi, Lo, C):
    n = len(C); med_vel = statistics.median([g["vel"] for g in sigs]) if sigs else 0.0
    out = {}
    for name, fn in SCHEMES:
        rows = []
        for g in sigs:
            k = g["k"]; s = g["s"]; entry = g["entry"]; sl = g["sl"]; dist = g["dist"]
            tp_frac = fn(g); tp = entry * (1 + s * tp_frac)
            j0 = k + 1; j1 = min(n, k + 1 + H)
            outc, gross = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
            rows.append((net, net / dist, outc, g["vel"], tp_frac))
        out[name] = rows
    return out, med_vel


def report(label, ev_out, med_vel):
    print("\n====  %s   (median recent-vel = %.3f %%/bar)  ====" % (label, med_vel * 100), flush=True)
    print("  %-8s %6s %7s %8s %8s %9s   |  LOW-vel expR   HIGH-vel expR" % ("scheme", "n", "TPhit%", "win%", "avg%", "expR"), flush=True)
    for name, _ in SCHEMES:
        rows = ev_out[name]
        if len(rows) < 10:
            continue
        net = np.array([r[0] for r in rows]); rr = np.array([r[1] for r in rows])
        tph = 100.0 * np.mean([r[2] == "tp" for r in rows])
        lo = [r[1] for r in rows if r[3] < med_vel]; hi = [r[1] for r in rows if r[3] >= med_vel]
        print("  %-8s %6d %7.0f %8.0f %+8.3f %+9.3f   |  %+8.3f     %+8.3f" % (
            name, len(rows), tph, 100 * (net > 0).mean(), net.mean() * 100, rr.mean(),
            (np.mean(lo) if lo else 0.0), (np.mean(hi) if hi else 0.0)), flush=True)


def main():
    for tf in ("1h", "30m"):
        slbuf = 0.002 if tf == "1h" else 0.003
        rec_sigs, rH, rL, rC = signals(get_buckets(tf, {"root": "study/recon_archive"}), slbuf)
        rev, rmed = evaluate(rec_sigs, rH, rL, rC)
        report("TF=%s  RECON 2025-01..2026-06" % tf, rev, rmed)
        try:
            fwd = get_buckets(tf, {})                             # default root = forward cold-archive (already post-recon)
            f_sigs, fH, fL, fC = signals(fwd, slbuf)
            if len(f_sigs) >= 10:
                fev, fmed = evaluate(f_sigs, fH, fL, fC)
                report("TF=%s  FORWARD 2026-06-20..now (the SLOW tape)" % tf, fev, fmed)
            else:
                print("  (forward %s: only %d signals)" % (tf, len(f_sigs)), flush=True)
        except Exception as e:
            print("  (forward %s skipped: %s)" % (tf, e), flush=True)


if __name__ == "__main__":
    main()
