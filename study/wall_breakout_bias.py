"""DIRECTIONAL BIAS of a RADAR-WALL BREAKOUT. Event: a wall's radar visit that RESISTED, then a breakout bar exits the
radar in the DEFENSE direction (open inside [radar_lo, radar_hi], close beyond the defended extreme: up for a support S,
down for a resistance R). radar = wall price +/- 3*band; L = radar_hi - radar_lo.

OUTCOME (bias): from the breakout, price travels one FULL radar-length further in the breakout direction BEFORE touching
the opposite radar extreme.  Support:  TP = radar_hi + L , SL = radar_lo.  Resistance: TP = radar_lo - L , SL = radar_hi.
First-passage over the FOLLOWING bars (k+1..k+H), SL-first on a same-bar tie; unresolved-at-H dropped. Barriers are
RADAR-scaled (not candle-body), so the body/volume confound of study/strength_bias.py does NOT apply here.

PREDICTORS (all causal, using only the visit up to the breakout bar k):
  wall     : P_resist (calibrated hold odds of the run)
  reward/eff: reff_whole = defender reward/eff over the visit; dev_reff = its 1st-half -> 2nd-half change (rising = defence ramping)
  STRENGTH : str_whole  = defender strength effort_z over the visit; dev_str = its 1st->2nd-half change  <- the "development"
  breakout : bo_eff (breakout bar effort_z), bo_effic (|body|/vol), pen = (close beyond the extreme)/band
Controls (audit lesson): base P(bias) by band quartile + by penetration; AUC reported RAW and BAND-STRATIFIED.
TFs: 5m 15m 1h 4h (NOT 1m). Both recon years. Usage: python study/wall_breakout_bias.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 50; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3


def auc(score, label):
    ok = ~np.isnan(score); s = score[ok]; y = label[ok]
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    order = s.argsort(kind="mergesort"); sv = s[order]; r = np.empty(len(s)); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (r[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def auc_strat(score, label, strat, nb=5):
    ok = ~np.isnan(score) & ~np.isnan(strat); s = score[ok]; y = label[ok]; g = strat[ok]
    if len(s) < nb * 30:
        return float("nan")
    qs = np.quantile(g, [b / nb for b in range(nb + 1)]); tot = w = 0.0
    for b in range(nb):
        mb = (g >= qs[b]) & (g <= qs[b + 1] if b == nb - 1 else g < qs[b + 1])
        if mb.sum() < 30:
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
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

    # ---- collect RESISTED-breakout events (windowed wall detection, runs-based) ----
    ev = {}                                                   # (k, side) -> dict, first-seen wins
    c0 = 0
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
                for k in range(b, min(b + 2, n - 1) + 1):        # breakout bar at/just after the visit end
                    if not (rlo <= O[k] <= rhi):                 # open INSIDE the radar
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)   # close BEYOND the defended extreme
                    if not broke:
                        continue
                    if (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = dict(k=k, side=side, a=a, rlo=rlo, rhi=rhi, band=band, pr=pr)
                    break
        if c1 >= n:
            break
        c0 += 5000

    def dshare(side, a, b):
        s, ok = reward_eff.share(A, a, b)
        return (s if side == "S" else 100.0 - s) if ok else None

    rows = []
    for (k, side), e in ev.items():
        if k + 1 >= n:
            continue
        rlo = e["rlo"]; rhi = e["rhi"]; band = e["band"]; a = e["a"]; L = rhi - rlo
        TP = (rhi + L) if side == "S" else (rlo - L); SL = rlo if side == "S" else rhi
        # outcome: first passage over k+1..k+H (SL-first tie)
        out = -1
        for j in range(k + 1, min(n, k + 1 + H)):
            sl_hit = (Lo[j] <= SL) if side == "S" else (Hi[j] >= SL)
            tp_hit = (Hi[j] >= TP) if side == "S" else (Lo[j] <= TP)
            if sl_hit:
                out = 0; break
            if tp_hit:
                out = 1; break
        if out < 0:
            continue
        # predictors (causal: visit [a, k-1], breakout bar k)
        vb = k - 1
        if vb - a < 2:
            continue
        mid = a + (vb - a) // 2
        base = reward_eff.strength_baseline(A, k)
        if not base or base.get("vol") is None:
            continue
        defk = "buy" if side == "S" else "sell"
        sw = reward_eff.strength(A, a, vb, base=base); s1 = reward_eff.strength(A, a, mid, base=base)
        s2 = reward_eff.strength(A, mid, vb, base=base); sb = reward_eff.strength(A, k, k, base=base)
        if not (sw["ok"] and s1["ok"] and s2["ok"] and sb["ok"]):
            continue
        str_whole = sw[defk]["effort_z"]; dev_str = s2[defk]["effort_z"] - s1[defk]["effort_z"]
        rw = dshare(side, a, vb); r1 = dshare(side, a, mid); r2 = dshare(side, mid, vb)
        if rw is None or r1 is None or r2 is None:
            continue
        reff_whole = rw; dev_reff = r2 - r1
        bo_eff = sb[defk]["effort_z"]
        vol_k = _f(A[k].get("buy_vol")) + _f(A[k].get("sell_vol"))
        bo_effic = (abs(C[k] - O[k]) / vol_k) if vol_k > 0 else 0.0
        pen = (C[k] - rhi) / band if side == "S" else (rlo - C[k]) / band
        rows.append((int(yr[k]), out, e["pr"], reff_whole, dev_reff, str_whole, dev_str,
                     bo_eff, bo_effic, pen, band / C[k], k - a))

    R = np.array(rows, dtype=float) if rows else np.zeros((0, 12))
    NAMES = ["pr", "reff_whole", "dev_reff", "str_whole", "dev_str", "bo_eff", "bo_effic", "pen", "bandpct", "vlen"]
    print("\n============  TF = %s   (bars=%d, events=%d, horizon=%d)  ============" % (tf, n, len(rows), H), flush=True)
    if len(rows) < 60:
        print("  too few events", flush=True); return
    for Y in (2025, 2026):
        m = R[:, 0] == Y
        if m.sum() < 30:
            print("  %d  n<30" % Y); continue
        y = R[m, 1]; base = 100.0 * y.mean(); bandq = R[m, 8]
        print("  %d  base P(bias)=%.1f%%  n=%d" % (Y, base, int(m.sum())), flush=True)
        gl = "      base by band-quintile:"; qs = np.quantile(bandq, [0, .2, .4, .6, .8, 1.0])
        for b in range(5):
            mb = (bandq >= qs[b]) & (bandq <= qs[b + 1] if b == 4 else bandq < qs[b + 1])
            gl += " Q%d=%.0f%%" % (b + 1, 100.0 * y[mb].mean() if mb.sum() else float("nan"))
        print(gl, flush=True)
        pnq = np.quantile(R[m, 9], [0, .5, 1.0]); pl = "      base by penetration:"
        for b in range(2):
            mb = (R[m, 9] >= pnq[b]) & (R[m, 9] <= pnq[b + 1] if b == 1 else R[m, 9] < pnq[b + 1])
            pl += " %s=%.0f%%" % (("low", "high")[b], 100.0 * y[mb].mean() if mb.sum() else float("nan"))
        print(pl, flush=True)
        print("      %-11s %7s %7s" % ("feature", "AUCraw", "AUCband"), flush=True)
        for fi, nm in enumerate(NAMES):
            col = R[m, 2 + fi]
            araw = auc(col, y); astr = auc_strat(col, y, bandq)
            tag = "  <== DEVELOPMENT" if nm in ("dev_str", "dev_reff", "str_whole", "reff_whole", "pr") else ""
            print("      %-11s %7.3f %7.3f%s" % (nm, araw, astr, tag), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["4h", "1h", "15m", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
