"""IN-DEPTH ORDER-FLOW study of the RESISTED-WALL RADAR BREAKOUT directional bias. Same event + outcome as
study/wall_breakout_bias.py (open inside [radar_lo,radar_hi], close beyond the defended extreme; bias = reach one full
radar-length past the break BEFORE touching the opposite extreme). This time the question is the one you asked:

  does the ORDER FLOW predict which breakouts follow through? -- strength, reward/eff, their DEVELOPMENT over the visit,
  and the TABLE reads (Last 20 / Last 50 / Today), all ALIGNED to the breakout direction.

PREDICTORS (all causal, aligned so >0 / >50 favours the breakout direction):
  breakout bar : bo_reff (reward/eff share), bo_str (strength effort z), bo_speed (speed z)
  DEVELOPMENT  : vis_reff (defender reward/eff over the visit) + vis_reff_slope (1st->2nd half change)
                 vis_str  (defender strength effort z over the visit) + vis_str_slope
  TABLE        : t_reff20/50/today (reward/eff over those windows) ; t_str20/50 (strength effort z)
  wall/context : pr (P_resist) ; pen (penetration) ; band (control)
Reports AUC RAW and BAND-STRATIFIED (the honest one) per TF (5m/15m/1h/4h, NOT 1m), both recon years, + a both-year flag
and the conditional P(bias) for anything that clears it. Usage: python study/wall_breakout_orderflow.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bisect
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 50; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3


def auc(score, label):
    ok = ~np.isnan(score); s = score[ok]; y = label[ok]
    p = int((y == 1).sum()); q = int((y == 0).sum())
    if p == 0 or q == 0:
        return float("nan")
    o = s.argsort(kind="mergesort"); sv = s[o]; r = np.empty(len(s)); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (r[y == 1].sum() - p * (p + 1) / 2.0) / (p * q)


def auc_strat(score, label, strat, nb=5):
    ok = ~np.isnan(score) & ~np.isnan(strat); s = score[ok]; y = label[ok]; g = strat[ok]
    if len(s) < nb * 30:
        return float("nan")
    qs = np.quantile(g, [b / nb for b in range(nb + 1)]); tot = w = 0.0
    for b in range(nb):
        mb = (g >= qs[b]) & (g <= qs[b + 1] if b == nb - 1 else g < qs[b + 1])
        if mb.sum() < 30 or (y[mb] == 1).sum() < 8 or (y[mb] == 0).sum() < 8:
            continue
        a = auc(s[mb], y[mb])
        if not np.isnan(a):
            tot += a * mb.sum(); w += mb.sum()
    return tot / w if w > 0 else float("nan")


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = [_f(b.get("start_time")) for b in A]
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

    ev = {}; c0 = 0                                            # collect resisted-breakout events (runs-based, windowed)
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0; pr = _f(r[2]) if len(r) > 2 else float("nan")
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (a, rlo, rhi, band, pr); break
        if c1 >= n:
            break
        c0 += 5000

    def sh(i0, i1, up):                                        # reward/eff aligned to the breakout direction
        s, ok = reward_eff.share(A, i0, i1)
        return (s if up else 100.0 - s) if ok else np.nan

    def steff(i0, i1, up, base):                               # strength effort_z of the breakout-direction side
        st = reward_eff.strength(A, i0, i1, base=base)
        if not st["ok"]:
            return np.nan
        return st["buy" if up else "sell"]["effort_z"]

    def stspd(i0, i1, up, base):
        st = reward_eff.strength(A, i0, i1, base=base)
        if not st["ok"]:
            return np.nan
        return st["buy" if up else "sell"]["speed_z"]

    NAMES = ["bo_reff", "bo_str", "bo_speed", "vis_reff", "vis_reff_slope", "vis_str", "vis_str_slope",
             "t_reff20", "t_reff50", "t_reff_today", "t_str20", "t_str50", "pr", "pen"]
    rows = []
    for (k, side), (a, rlo, rhi, band, pr) in ev.items():
        if k + 1 >= n:
            continue
        up = side == "S"; L = rhi - rlo; s = 1 if up else -1
        TP = (rhi + L) if up else (rlo - L); SL = rlo if up else rhi
        out = -1
        for j in range(k + 1, min(n, k + 1 + H)):
            slh = (Lo[j] <= SL) if up else (Hi[j] >= SL); tph = (Hi[j] >= TP) if up else (Lo[j] <= TP)
            if slh:
                out = 0; break
            if tph:
                out = 1; break
        if out < 0:
            continue
        vb = k - 1
        if vb - a < 2:
            continue
        base = reward_eff.strength_baseline(A, k)
        if not base or base.get("vol") is None:
            continue
        mid = a + (vb - a) // 2
        d0 = datetime.fromtimestamp(ST[k], tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        it = bisect.bisect_left(ST, float(d0))
        f = [sh(k, k, up), steff(k, k, up, base), stspd(k, k, up, base),
             sh(a, vb, up), sh(mid, vb, up) - sh(a, mid, up),
             steff(a, vb, up, base), steff(mid, vb, up, base) - steff(a, mid, up, base),
             sh(k - 19, k, up), sh(k - 49, k, up), sh(it, k, up),
             steff(k - 19, k, up, base), steff(k - 49, k, up, base), pr, s * (C[k] - (rhi if up else rlo)) / band]
        rows.append((int(yr[k]), out, band / C[k]) + tuple(f))

    R = np.array(rows) if rows else np.zeros((0, 3 + len(NAMES)))
    print("\n========  TF = %s   (bars=%d, events=%d)  ========" % (tf, n, len(rows)), flush=True)
    if len(rows) < 80:
        print("  too few events"); return
    for Y in (2025, 2026):
        m = R[:, 0] == Y
        if m.sum() < 50:
            print("  %d n<50" % Y); continue
        y = R[m, 1]; bandq = R[m, 2]
        print("  %d  base P(bias)=%.1f%%  n=%d" % (Y, 100.0 * y.mean(), int(m.sum())), flush=True)
        print("      %-14s %7s %7s" % ("order-flow feat", "AUCraw", "AUCband"), flush=True)
        for fi, nm in enumerate(NAMES):
            col = R[m, 3 + fi]; araw = auc(col, y); astr = auc_strat(col, y, bandq)
            flag = "  <==" if (not np.isnan(astr) and abs(astr - 0.5) >= 0.04) else ""
            print("      %-14s %7.3f %7.3f%s" % (nm, araw, astr, flag), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["4h", "1h", "15m", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
