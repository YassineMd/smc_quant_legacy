"""DEEP winner/loser study on the 30m Radar Runner BREAKOUT BAR (the signal candle) -- the stats-box feature set.
Every feature is causal (known at entry), so any that separates winners from losers is a USABLE filter. W = net>0 on
the shipped spec (MINVISIT=1, candle-SL + 0.5% TP). Per feature: AUC(feat ranks winner over loser) split by YEAR --
credible only if it separates the SAME way in BOTH 2025 AND 2026 (regime-robust, guards the 12-feature data-mine).
Top features get disjoint-tercile win-rate gradients. python study/radarrun_30m_candlestats.py"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; TP_FRAC = 0.005; SLIP = 0.0003; SLBUF = 0.003


def _merge_lv(dst, b):
    for p, vv in (b.get("levels") or {}).items():
        e = dst.get(p)
        if e is None:
            dst[p] = {"b": _f(vv.get("b")), "s": _f(vv.get("s"))}
        else:
            e["b"] += _f(vv.get("b")); e["s"] += _f(vv.get("s"))


def native_30m(A15):
    tvs = [_f(b.get("target_vol")) for b in A15 if _f(b.get("target_vol")) > 0]
    T = 2.0 * (statistics.median(tvs) if tvs else statistics.median([_f(b.get("curr_vol")) for b in A15]))
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


def feats(A, i, s, rlo, rhi):
    """Causal breakout-bar features (dict). Order-flow ones via app modules; NaN if they fail."""
    b = A[i]; O = _f(b.get("open_price")); C = _f(b.get("close_price")); Hh = _f(b.get("high")); Ll = _f(b.get("low"))
    bv = _f(b.get("buy_vol")); sv = _f(b.get("sell_vol")); vol = _f(b.get("curr_vol"))
    rng = max(Hh - Ll, 1e-9); band = (rhi - rlo) / (2 * RM)
    up_wick = (Hh - max(O, C)) / rng; lo_wick = (min(O, C) - Ll) / rng
    d = {
        "body": abs(C - O) / rng,
        "rng_pct": rng / C * 100.0,
        "close_str": ((C - Ll) / rng) if s > 0 else ((Hh - C) / rng),        # closed in the breakout dir (1=strong)
        "wick_against": up_wick if s > 0 else lo_wick,                        # rejection wick against the break
        "delta_al": ((bv - sv) / vol * s) if vol > 0 else 0.0,               # net flow agrees with the break
        "vol_rel": vol / (statistics.median([_f(A[j].get("curr_vol")) for j in range(max(0, i - 20), i)]) or vol),
        "elapsed": (_f(b.get("end_time")) - _f(b.get("start_time"))) / 60.0,  # bucket duration (min); low=fast tape
        "pen": ((C - rhi) if s > 0 else (rlo - C)) / max(band, 1e-9),         # penetration beyond the radar, in bands
    }
    try:
        from app import absorption
        d["absorp_A"] = _f(absorption.absorption(A, i)[0])
    except Exception:
        d["absorp_A"] = float("nan")
    try:
        from app import reward_eff
        base = reward_eff.strength_baseline(A, i); bo = float("nan")
        if base and base.get("vol"):
            st = reward_eff.strength(A, i, i, base=base)
            if st.get("ok"):
                bo = st["buy" if s > 0 else "sell"]["effort_z"]
        d["bo_str"] = bo
        sh, ok = reward_eff.share(A, i - 49, i)
        d["rew_al"] = (sh if s > 0 else 100.0 - sh) if ok else float("nan")
    except Exception:
        d["bo_str"] = float("nan"); d["rew_al"] = float("nan")
    return d


def signals(A):
    n = len(A)
    O = np.array([_f(b.get("open_price")) for b in A]); C = np.array([_f(b.get("close_price")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
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
        if k + 1 >= n or k <= last or k < 25:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP_FRAC), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        y = datetime.fromtimestamp(ST[k], tz=timezone.utc).year
        fr = feats(A, k, s, rlo, rhi); fr["_win"] = 1 if net > 0 else 0; fr["_y"] = y; fr["_R"] = net / dist
        rows.append(fr); last = k + int(off)
    return rows


def auc(x, y):
    m = ~np.isnan(x); x = x[m]; y = y[m]
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 < 3 or n0 < 3:
        return float("nan"), n1 + n0
    order = np.argsort(x, kind="mergesort"); ranks = np.empty(len(x)); ranks[order] = np.arange(1, len(x) + 1)
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0), n1 + n0


FKEYS = ["body", "rng_pct", "close_str", "wick_against", "delta_al", "vol_rel", "elapsed", "pen", "absorp_A", "bo_str", "rew_al"]


def main():
    A15 = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    A = native_30m(A15); del A15
    rows = signals(A)
    for Y in (2025, 2026):
        yr = [r for r in rows if r["_y"] == Y]
        w = sum(r["_win"] for r in yr)
        print("\n====  30m breakout bar, %d :  n=%d  win=%d (%.0f%%)  ====" % (Y, len(yr), w, 100 * w / max(1, len(yr))), flush=True)
    print("\n  %-13s %9s %9s   %s" % ("feature", "AUC_2025", "AUC_2026", "both-year read (>.55 or <.45 same side)"), flush=True)
    keep = []
    for f in FKEYS:
        a25, n25 = auc(np.array([r[f] for r in rows if r["_y"] == 2025], float), np.array([r["_win"] for r in rows if r["_y"] == 2025]))
        a26, n26 = auc(np.array([r[f] for r in rows if r["_y"] == 2026], float), np.array([r["_win"] for r in rows if r["_y"] == 2026]))
        rd = ""
        if a25 == a25 and a26 == a26:
            if (a25 > 0.55 and a26 > 0.55) or (a25 < 0.45 and a26 < 0.45):
                rd = "<-- CONSISTENT"; keep.append(f)
        print("  %-13s %9.3f %9.3f   %s" % (f, a25, a26, rd), flush=True)
    for f in keep + ["rng_pct"] * (0 if "rng_pct" in keep else 1):
        xs = np.array([r[f] for r in rows], float); mask = ~np.isnan(xs)
        q = np.quantile(xs[mask], [1 / 3, 2 / 3])
        print("\n  %s -- tercile win%% / meanR, PER YEAR (edge is real only if LO vs HI holds BOTH years):" % f, flush=True)
        for Y in (2025, 2026):
            yr = [r for r in rows if r["_y"] == Y and not np.isnan(r[f])]
            parts = []
            for lab, sel in (("LO", lambda v: v < q[0]), ("MID", lambda v: q[0] <= v < q[1]), ("HI", lambda v: v >= q[1])):
                g = [r for r in yr if sel(r[f])]
                if g:
                    parts.append("%s %.0f%%/%+.3f(n%d)" % (lab, 100 * np.mean([r["_win"] for r in g]), np.mean([r["_R"] for r in g]), len(g)))
            print("     %d:  %s" % (Y, "   ".join(parts)), flush=True)

    # --- FORWARD (daemon) out-of-sample check on PENETRATION, using the RECON-derived tercile bands ---
    try:
        F = native_30m(sorted(load_archive("15m")[1], key=lambda b: _f(b.get("start_time", 0))))   # default root = forward
        fr = signals(F)
        pv = np.array([r["pen"] for r in rows], float); pq = np.quantile(pv[~np.isnan(pv)], [1 / 3, 2 / 3])
        print("\n  DAEMON forward pen check (recon bands q=%.2f / %.2f, OUT-OF-SAMPLE, n_fwd=%d):" % (pq[0], pq[1], len(fr)), flush=True)
        parts = []
        for lab, sel in (("LO", lambda v: v < pq[0]), ("MID", lambda v: pq[0] <= v < pq[1]), ("HI", lambda v: v >= pq[1])):
            g = [r for r in fr if not np.isnan(r["pen"]) and sel(r["pen"])]
            if g:
                parts.append("%s %.0f%%/%+.3f(n%d)" % (lab, 100 * np.mean([r["_win"] for r in g]), np.mean([r["_R"] for r in g]), len(g)))
        print("     %s" % "   ".join(parts), flush=True)
    except Exception as e:
        print("  (daemon pen check skipped: %s)" % e, flush=True)


if __name__ == "__main__":
    main()
