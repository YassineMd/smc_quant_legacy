"""DIRECTIONAL-BIAS study of the per-candle STRENGTH readout (effort=max(size,speed) x reward/eff x tempo), vs the plain
REWARD/EFF, alone and WITH the table (Last20/Last50/Today), per timeframe, both recon years. CAUSAL + base-rate-aware.

OUTCOME (per study candle A, body B = close-open, dir = sign(B)):
  bias = 1  if price reaches  TP = close + 0.5*B   (continuation, half a body beyond close)
              BEFORE           SL = open            (full reversal),  scanning the FOLLOWING candles A+1..A+H
              (first-passage, SL-first on a same-bar tie = conservative). Unresolved-at-H dropped from the rate.
  Barriers use A's OWN body; price starts at A's close. Doji (|B|~0) skipped.

PREDICTORS (all use ONLY data through A -> rolling baselines are TRAILING & EXCLUSIVE of A; the outcome is A+1 on):
  strength : s_int=max(z_vol,z_speed)  s_speed=z(vol/elapsed)  s_size=z(vol)  s_side=winner_vol/vol  effic=|B|/vol
             tempo (impulsive: speed-led & int>=.5 / grind: size-led & int>=.5)  loser_abs (losing side pushing hard)
  reward/eff (table, aligned to dir): r20/r50/rToday = (share_W-50)*dir   |  strength-table: sint20/sint50 (winner-signed z-vol)
Usage: python study/strength_bias.py [tf ...]   (default: 4h 1h 15m 5m ; add 1m explicitly)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

H = 20                 # horizon: following candles to resolve the barrier
BASE = 300             # trailing baseline window for z-scores
MINBODY = 1e-9         # skip dojis


def rollz(x, W):
    """Trailing z-score over [i-W, i-1] (EXCLUSIVE of i) — causal. NaN for i<W."""
    c1 = np.concatenate([[0.0], np.cumsum(x)]); c2 = np.concatenate([[0.0], np.cumsum(x * x)])
    n = len(x); z = np.full(n, np.nan)
    i = np.arange(W, n)
    s = c1[i] - c1[i - W]; ss = c2[i] - c2[i - W]
    mean = s / W; var = np.maximum(1e-12, ss / W - mean * mean); std = np.sqrt(var)
    z[i] = (x[i] - mean) / std
    return z


def roll_share(up, dn, bv, sv, W):
    """reward/eff BUY share over the trailing window [i-W+1, i] INCLUSIVE (the table read known at A's close)."""
    cu = np.concatenate([[0.0], np.cumsum(up)]); cd = np.concatenate([[0.0], np.cumsum(dn)])
    cb = np.concatenate([[0.0], np.cumsum(bv)]); cs = np.concatenate([[0.0], np.cumsum(sv)])
    n = len(up); sh = np.full(n, np.nan)
    i = np.arange(W - 1, n); lo = i - W + 1
    sumu = cu[i + 1] - cu[lo]; sumd = cd[i + 1] - cd[lo]; sumb = cb[i + 1] - cb[lo]; sums = cs[i + 1] - cs[lo]
    rb = np.where(sumb > 0, sumu / np.where(sumb > 0, sumb, 1), 0.0)
    rs = np.where(sums > 0, sumd / np.where(sums > 0, sums, 1), 0.0)
    t = rb + rs; sh[i] = np.where(t > 0, 100.0 * rb / np.where(t > 0, t, 1), 50.0)
    return sh


def today_share(up, dn, bv, sv, st):
    """reward/eff BUY share since the UTC-midnight day open up to i INCLUSIVE (causal). Matches the table's Today row."""
    day = np.floor(st / 86400.0).astype(np.int64)
    ds = np.zeros(len(st), dtype=np.int64)                    # first index of each candle's day
    start = 0
    for i in range(len(st)):
        if i == 0 or day[i] != day[i - 1]:
            start = i
        ds[i] = start
    cu = np.concatenate([[0.0], np.cumsum(up)]); cd = np.concatenate([[0.0], np.cumsum(dn)])
    cb = np.concatenate([[0.0], np.cumsum(bv)]); cs = np.concatenate([[0.0], np.cumsum(sv)])
    i = np.arange(len(st))
    sumu = cu[i + 1] - cu[ds]; sumd = cd[i + 1] - cd[ds]; sumb = cb[i + 1] - cb[ds]; sums = cs[i + 1] - cs[ds]
    rb = np.where(sumb > 0, sumu / np.where(sumb > 0, sumb, 1), 0.0)
    rs = np.where(sums > 0, sumd / np.where(sums > 0, sums, 1), 0.0)
    t = rb + rs
    return np.where(t > 0, 100.0 * rb / np.where(t > 0, t, 1), 50.0)


def auc(score, label):
    ok = ~np.isnan(score); s = score[ok]; y = label[ok]
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan"), npos + nneg
    order = s.argsort(kind="mergesort"); sv = s[order]; r = np.empty(len(s)); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (r[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg), npos + nneg


def study(tf):
    B = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(B)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in B])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in B])
    Hi = np.array([_f(b.get("high")) for b in B]); Lo = np.array([_f(b.get("low")) for b in B])
    bv = np.array([_f(b.get("buy_vol")) for b in B]); sv = np.array([_f(b.get("sell_vol")) for b in B])
    st = np.array([_f(b.get("start_time")) for b in B]); et = np.array([_f(b.get("end_time")) for b in B])
    yr = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in st])
    body = C - O; adir = np.sign(body); vol = bv + sv
    el = et - st
    med_el = np.median(el[el > 0]) if np.any(el > 0) else 1.0
    el = np.where(el > 0, el, med_el)
    up = np.where(body > 0, np.where(O > 0, body / O, 0.0), 0.0)
    dn = np.where(body < 0, np.where(O > 0, -body / O, 0.0), 0.0)

    # ---- OUTCOME: first-passage TP(=C+0.5B) vs SL(=O), scanning A+1..A+H (SL-first tie) ----
    TP = C + 0.5 * body; SL = O
    resolved = np.zeros(n, bool); bias = np.full(n, -1)
    valid0 = (np.abs(body) > MINBODY) & (O > 0)
    for d in range(1, H + 1):
        k = np.arange(n) + d; okk = k < n
        hi = np.where(okk, Hi[np.clip(k, 0, n - 1)], np.nan); lo = np.where(okk, Lo[np.clip(k, 0, n - 1)], np.nan)
        tp_hit = np.where(adir > 0, hi >= TP, lo <= TP)
        sl_hit = np.where(adir > 0, lo <= SL, hi >= SL)
        newly = valid0 & okk & ~resolved
        is_sl = newly & sl_hit                                # SL-first on a same-bar tie (conservative)
        is_tp = newly & tp_hit & ~sl_hit
        bias[is_sl] = 0; bias[is_tp] = 1; resolved |= (is_sl | is_tp)

    # ---- PREDICTORS (causal) ----
    z_vol = rollz(vol, BASE); z_spd = rollz(vol / el, BASE)
    s_size = z_vol; s_speed = z_spd; s_int = np.fmax(z_vol, z_spd)
    winner_vol = np.where(adir > 0, bv, sv); loser_vol = np.where(adir > 0, sv, bv)
    s_side = np.where(vol > 0, winner_vol / np.where(vol > 0, vol, 1), 0.5)
    effic = np.where(vol > 0, np.abs(body) / np.where(vol > 0, vol, 1), 0.0)
    # tempo + loser-absorbed (loser pushing hard = high loser volume-rate z)
    zlose = np.fmax(rollz(loser_vol, BASE), rollz(loser_vol / el, BASE))
    impulsive = (s_int >= 0.5) & (z_spd >= z_vol)
    grind = (s_int >= 0.5) & (z_vol > z_spd)
    loser_abs = zlose >= 0.5
    # table: reward/eff aligned to dir
    r20 = (roll_share(up, dn, bv, sv, 20) - 50.0) * adir
    r50 = (roll_share(up, dn, bv, sv, 50) - 50.0) * adir
    rtd = (today_share(up, dn, bv, sv, st) - 50.0) * adir
    # table: strength intensity signed toward the winner (winner-vol z minus loser-vol z, over the window via rolling)
    def roll_sum(x, W):
        c = np.concatenate([[0.0], np.cumsum(x)]); n2 = len(x); o = np.full(n2, np.nan); i = np.arange(W - 1, n2)
        o[i] = c[i + 1] - c[i - W + 1]; return o
    sint50 = (rollz(np.nan_to_num(roll_sum(winner_vol, 50)), BASE)
              - rollz(np.nan_to_num(roll_sum(loser_vol, 50)), BASE))    # nan_to_num: cumsum can't cross the leading NaN
    abody = np.abs(body) / np.where(O > 0, O, 1)               # BODY SIZE (the barrier scale) — the confound to control for

    feats = {"s_int": s_int, "s_speed": s_speed, "s_side": s_side, "effic": effic, "abody": abody,
             "r_align20": r20, "r_align50": r50, "r_today": rtd, "str_tab50": sint50}
    STR_F = ("s_int", "s_speed", "s_side")                     # STRENGTH (effort/speed/one-sidedness)
    REF_F = ("effic", "r_align20", "r_align50", "r_today")     # REWARD/EFF (efficiency + windowed lean)

    def auc_strat(score, label, strat, nb=10):                 # AUC computed WITHIN body-size deciles (partials out body)
        ok = ~np.isnan(score) & ~np.isnan(strat); s = score[ok]; y = label[ok]; g = strat[ok]
        if len(s) < nb * 50:
            return float("nan")
        qs = np.quantile(g, [b / nb for b in range(nb + 1)]); tot = w = 0.0
        for b in range(nb):
            mb = (g >= qs[b]) & (g <= qs[b + 1] if b == nb - 1 else g < qs[b + 1])
            if mb.sum() < 50:
                continue
            a, nn = auc(s[mb], y[mb])
            if not np.isnan(a):
                tot += a * nn; w += nn
        return tot / w if w > 0 else float("nan")

    m0 = valid0 & resolved & (np.arange(n) >= BASE)
    print("\n================  TF = %s   (bars=%d, horizon=%d)  ================" % (tf, n, H), flush=True)
    for Y in (2025, 2026):
        mm = m0 & (yr == Y); by = bias[mm]
        base = 100.0 * by.mean() if len(by) else float("nan")
        unres = 100.0 * np.mean((valid0 & (yr == Y) & (np.arange(n) >= BASE)) & ~resolved)
        print("  %d  base P(bias)=%.1f%%  n=%-6d  unresolved=%.0f%%" % (Y, base, len(by), unres), flush=True)
        # geometry check: base rate by BODY-SIZE decile (expect it to RISE if the barrier scaling drives the outcome)
        ab = abody[mm]; qs = np.quantile(ab, [0, .2, .4, .6, .8, 1.0]); gl = "      base by body-quintile:"
        for b in range(5):
            mb = (ab >= qs[b]) & (ab <= qs[b + 1] if b == 4 else ab < qs[b + 1])
            gl += " Q%d=%.0f%%" % (b + 1, 100.0 * bias[mm][mb].mean() if mb.sum() else float("nan"))
        print(gl, flush=True)
        # RAW AUC vs BODY-STRATIFIED AUC (the honest one) for every feature
        print("      %-11s %7s %7s" % ("feature", "AUCraw", "AUCbody"), flush=True)
        for k in ("abody", "effic", "s_side", "s_int", "s_speed", "r_align20", "r_align50", "r_today", "str_tab50"):
            araw, _ = auc(feats[k][mm], bias[mm]); astr = auc_strat(feats[k][mm], bias[mm], abody[mm])
            tag = "  <== STRENGTH" if k in STR_F else ("  <== reward/eff" if k in REF_F else "")
            print("      %-11s %7.3f %7.3f%s" % (k, araw, astr, tag), flush=True)

        def rate(msk):
            b2 = bias[mm][msk[mm]]
            return (100.0 * b2.mean(), len(b2)) if len(b2) else (float("nan"), 0)
        for lbl, msk in (("impulsive", impulsive), ("grind", grind), ("loser_abs", loser_abs)):
            rp, nn = rate(msk & m0); print("       P(bias|%-9s)=%.1f%% (%+.1f) n=%d" % (lbl, rp, rp - base, nn), flush=True)
        # WITH THE TABLE: strong candle AND the reward/eff table agrees with A's direction
        rp, nn = rate(((s_int >= 0.5) & (r20 > 0)) & m0)
        print("       P(bias|int>=.5 & reff20-aligned)=%.1f%% (%+.1f) n=%d" % (rp, rp - base, nn), flush=True)
        rp, nn = rate(((r20 > 0) & (r50 > 0) & (rtd > 0)) & m0)
        print("       P(bias|table all aligned 20/50/today)=%.1f%% (%+.1f) n=%d" % (rp, rp - base, nn), flush=True)


if __name__ == "__main__":
    tfs = sys.argv[1:] or ["4h", "1h", "15m", "5m"]
    for tf in tfs:
        try:
            study(tf)
        except Exception as e:
            print("TF %s FAILED: %r" % (tf, e), flush=True)
