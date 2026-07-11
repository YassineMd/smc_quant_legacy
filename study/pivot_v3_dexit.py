"""PIVOT V3 — ALTERNATE EXIT experiment ("D-EXIT"). STUDY ONLY (the frozen default stays ZZTRAIL). CAUSAL.

Exit under test:
  - Stop loss is FIXED (NO MFE/swing auto-trail): initial = 0.1% below the last CONFIRMED swing low (long) /
    above the last confirmed swing high (short).  [swing = ZigZag(0.20%) pivot, confirmed by the entry bar]
  - TAKE PROFIT only when an OPPOSITE-side D prints -> close at that bar's close.
  - When a SAME-side D prints -> TRAIL the stop to 0.1% below the last confirmed swing low (long) / above the
    last confirmed swing high (short). Ratchet only (never loosens). Between D-prints the stop stays put.
  - No fixed TP, no +0.4%/+0.1% lock.  Fee 0.10 taker/taker.  Three-outcome NET.

The "D-print" timeline = every scan-gated D (both sides), i.e. the D badges the terminal shows (one per side,
sequential). Same entries as the frozen V3 book (Path A direct-D + Path B New-E combos); only the EXIT changes.
Run: python study/pivot_v3_dexit.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                      # noqa: E402
import pivot_v3_de_zone_pdf as B                          # reuse loaders/helpers/constants  # noqa: E402

FEE = B.FEE; SL = B.SL; SL_PAD = B.SL_PAD; BE = B.BE; LW = B.LW; LOCK_LAG = B.LOCK_LAG
P2D_HI = B.P2D_HI; P2D_VHI = B.P2D_VHI; WBACK = B.WBACK; MIN_CYC = B.MIN_CYC; E_WIN = B.E_WIN
TRAIL = B.TRAIL; ARM = B.ARM; LOCK = B.LOCK
ZMAP = B.ZMAP; ZONES = B.ZONES; TIERS = B.TIERS; V3OK = B.V3OK

# Frozen Step-4 E TAKE set (side . D-zone -> E-zone): 4 any-tier + 2 cyan/orange-only.
E_OK = {("Buy D", "buy area", "body"), ("Sell D", "sell area", "body"),
        ("Buy D", "below buy area", "buy area"), ("Sell D", "above sell area", "sell area")}
E_CYAN = {("Buy D", "body", "sell area"), ("Sell D", "body", "buy area")}


def ematch(side, tier, dz, ez):
    k = (side, dz, ez)
    return (k in E_OK) or (tier == "cyan/orange" and k in E_CYAN)


# Path-B PURE fixed-bracket sweep: (SL_frac, TP_frac). net = gross - 0.10 fee (so SL.1=-0.2 net, TP.4=+0.3 net).
BRACKETS = [(0.001, 0.004), (0.002, 0.004), (0.002, 0.0045), (0.002, 0.005),
            (0.001, 0.005), (0.001, 0.0045)]
# Wide-TP progression at the SL0.2 stop -> "can we target more than 0.5%?"
WIDE_TP = [(0.002, 0.005), (0.002, 0.006), (0.002, 0.007), (0.002, 0.008),
           (0.002, 0.010), (0.002, 0.0125), (0.002, 0.015)]
ALLBR = BRACKETS + [b for b in WIDE_TP if b not in BRACKETS]


def blabel(sl, tp):
    return "SL%g/TP%g" % (sl * 100, tp * 100)


# Breakeven-lock sweep for the D-exit: (label, lock_on, arm_%, lock_%). "nolock" = ride to opposite-D / SL only.
LOCK_CFG = [("nolock", False, 0.0, 0.0),
            ("arm.4/lk.1", True, 0.004, 0.001),   # the tight lock the user asked for (clips the runners)
            ("arm.8/lk.2", True, 0.008, 0.002),
            ("arm1.0/lk.3", True, 0.010, 0.003),
            ("arm1.2/lk.4", True, 0.012, 0.004),
            ("arm1.5/lk.5", True, 0.015, 0.005)]


def outcome_t(nets):
    a = np.asarray(nets, float); n = len(a)
    if n == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(n)) if (n > 1 and a.std(ddof=1) > 1e-9) else 0.0
    return dict(n=n, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()) * 10.0, t=float(t))


def build():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    ebu, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(ebu, er_, LW), float)
    e_sh_c = B.causal_share(ebu, er_, LW)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    z_et, z_lo, z_hi, z_low, z_high = B.load_4h()

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        return None if i4 < 0 else ZMAP[B.zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])]

    def cycles_from(w0, edge):
        cyc = []; i0 = w0; dom = e_sh[w0] >= 0.5
        for k in range(w0 + 1, edge + 1):
            dk = e_sh[k] >= 0.5
            if dk != dom:
                cyc.append([i0, k - 1, dom]); i0 = k; dom = dk
        cyc.append([i0, edge, dom])
        while len(cyc) > 1:
            si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
            if (cyc[si][1] - cyc[si][0] + 1) >= MIN_CYC:
                break
            cyc[si][2] = not cyc[si][2]
            merged = [cyc[0]]
            for c in cyc[1:]:
                if c[2] == merged[-1][2]:
                    merged[-1][1] = c[1]
                else:
                    merged.append(c)
            cyc = merged
        return np.asarray([c[1] for c in cyc]), cyc

    def find_E(det, start, buy):
        ends, cyc = cycles_from(max(0, det - WBACK), n - 1)
        t0 = et[det]; sg = 1.0 if buy else -1.0
        for j in range(start, n):
            if et[j] > t0 + E_WIN:
                break
            jl = j - LOCK_LAG
            if jl < 0 or sg * (2.0 * float(e_sh[jl]) - 1.0) * 100.0 < 15.0:
                continue
            m = int(np.searchsorted(ends, j - LOCK_LAG, side="left"))
            if m == 0:
                continue
            l3 = cyc[max(0, m - 2):m]; s0 = l3[0][0]; s1 = l3[-1][1]
            if (float(np.mean(e_sh[s0:s1 + 1])) >= 0.5) != buy:
                continue
            ci = int(np.searchsorted(ends, j, side="left"))
            cs = cyc[min(ci, len(cyc) - 1)][0]
            if (float(np.mean(e_sh_c[cs:j + 1])) >= 0.5) == buy:
                return j
        return None

    def tier_of(det, buy):
        p2d = (1.0 if buy else -1.0) * (2.0 * float(e_sh_c[det]) - 1.0) * 100.0
        return "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")

    # ZigZag swings -> low/high pivots with (confirm_bar, pivot_bar, price, label)
    sw = B.zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    lows = []; highs = []; ph = pl = None
    for pb, p, ih, cb in sw:
        if ih:
            lab = None if ph is None else ("HH" if p > ph else "LH"); ph = p
            if lab:
                highs.append((cb, pb, p, lab))
        else:
            lab = None if pl is None else ("HL" if p > pl else "LL"); pl = p
            if lab:
                lows.append((cb, pb, p, lab))
    lows.sort(); highs.sort()
    allsw = sorted([(cb, lab) for cb, pb, p, lab in lows] + [(cb, lab) for cb, pb, p, lab in highs])

    def swings_before(eb, k=3):   # last k confirmed ZigZag swing labels (HH/HL/LH/LL) before the entry bar
        return [lab for cb, lab in allsw if cb <= eb][-k:]

    def last_low_conf(j):        # price of the last low pivot (HL or LL) CONFIRMED by bar j
        r = None
        for cb, pb, p, lab in lows:
            if cb > j:
                break
            r = p
        return r

    def last_high_conf(j):
        r = None
        for cb, pb, p, lab in highs:
            if cb > j:
                break
            r = p
        return r

    def last_lbl(arr, eb, label):
        r = None
        for cb, pb, p, lab in arr:
            if pb >= eb:
                break
            if lab == label:
                r = p
        return r

    # ---- ZZTRAIL (frozen default), reproduced verbatim for the head-to-head baseline ----
    def walk_zz(eb, buy):
        entry = float(cl[eb])
        if buy:
            s0 = last_lbl(lows, eb, "LL"); s0 = s0 * (1 - SL_PAD) if s0 else entry * (1 - SL)
            trail = sorted((cb, p * (1 - TRAIL)) for cb, pb, p, lab in lows if lab == "HL" and pb > eb)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            s0 = last_lbl(highs, eb, "HH"); s0 = s0 * (1 + SL_PAD) if s0 else entry * (1 + SL)
            trail = sorted((cb, p * (1 + TRAIL)) for cb, pb, p, lab in highs if lab == "LH" and pb > eb)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        exitlvl = s0; tp = 0; armed = False
        for j in range(eb + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                l2 = trail[tp][1]; exitlvl = max(exitlvl, l2) if buy else min(exitlvl, l2); tp += 1
            e = exitlvl
            if armed:
                e = max(e, lock_lvl) if buy else min(e, lock_lvl)
            if (lo[j] <= e) if buy else (hi[j] >= e):
                return ((e - entry) if buy else (entry - e)) / entry * 100.0
            if (hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl):
                armed = True
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0

    # ---- collect the scan-gated D timeline (both sides) BEFORE walking ----
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; gated = []; d_bars = {"long": set(), "short": set()}
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        gated.append((det, ent, s)); d_bars[s].add(det)

    # ---- D-EXIT: fixed SL + TP on opposite-D + trail-on-same-D. lock=True adds an arm_p->lock_p breakeven lock.
    #      tp_p (e.g. 0.004) adds a HARD fixed take-profit at +tp_p (whichever triggers first) ----
    def walk_dx(eb, buy, lock=False, arm_p=ARM, lock_p=LOCK, tp_p=None):
        entry = float(cl[eb])
        if buy:
            lv = last_low_conf(eb); sl = lv * (1 - SL_PAD) if lv else entry * (1 - SL)
            arm_lvl = entry * (1 + arm_p); lock_lvl = entry * (1 + lock_p)
        else:
            hv = last_high_conf(eb); sl = hv * (1 + SL_PAD) if hv else entry * (1 + SL)
            arm_lvl = entry * (1 - arm_p); lock_lvl = entry * (1 - lock_p)
        tplvl = (entry * (1 + tp_p) if buy else entry * (1 - tp_p)) if tp_p else None
        opp = d_bars["short" if buy else "long"]; same = d_bars["long" if buy else "short"]
        armed = False
        for j in range(eb + 1, n):
            use_lock = lock and armed and ((lock_lvl > sl) if buy else (lock_lvl < sl))   # lock beats a looser struct stop
            e = lock_lvl if use_lock else sl
            if (lo[j] <= e) if buy else (hi[j] >= e):                      # effective stop hit intrabar (checked first)
                return ((e - entry) if buy else (entry - e)) / entry * 100.0, ("lock" if use_lock else "SL"), j
            if tplvl is not None and ((hi[j] >= tplvl) if buy else (lo[j] <= tplvl)):   # HARD fixed take-profit
                return ((tplvl - entry) if buy else (entry - tplvl)) / entry * 100.0, "TP", j
            if j in opp:                                                    # opposite D -> take profit at close
                return ((cl[j] - entry) if buy else (entry - cl[j])) / entry * 100.0, "oppD", j
            if lock and ((hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl)):   # +0.4% MFE -> arm the +0.1% lock
                armed = True
            if j in same:                                                  # same D -> ratchet the structural stop
                if buy:
                    lv = last_low_conf(j)
                    if lv is not None:
                        sl = max(sl, lv * (1 - SL_PAD))
                else:
                    hv = last_high_conf(j)
                    if hv is not None:
                        sl = min(sl, hv * (1 + SL_PAD))
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0, "edge", n - 1   # data edge = m2m

    # ---- PURE FIXED BRACKET: flat SL sl_p below/above entry + flat TP tp_p (Path B experiment). SL checked first. ----
    def walk_bracket(eb, buy, sl_p=0.001, tp_p=0.004):
        entry = float(cl[eb])
        sl = entry * (1 - sl_p) if buy else entry * (1 + sl_p)
        tp = entry * (1 + tp_p) if buy else entry * (1 - tp_p)
        for j in range(eb + 1, n):
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                return ((sl - entry) if buy else (entry - sl)) / entry * 100.0, "SL", j
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return ((tp - entry) if buy else (entry - tp)) / entry * 100.0, "TP", j
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0, "edge", n - 1

    def mfe_stop(eb, buy, sl_p=0.002):
        """Max FAVOURABLE excursion (%) the trade reaches before a flat sl_p stop would kill it (or the data edge).
        The headroom available to a take-profit: how far these entries actually run in favour under the 0.2% stop."""
        entry = float(cl[eb]); sl = entry * (1 - sl_p) if buy else entry * (1 + sl_p); m = 0.0
        for j in range(eb + 1, n):
            fav = ((hi[j] - entry) if buy else (entry - lo[j])) / entry * 100.0
            if fav > m:
                m = fav
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                break
        return m

    def hms_signal(b, buy, ncyc=2, include_cur=False, tw=False):
        """Aligned HMS spread at bar b: harmonic mean of the DOMINANT share over a cycle window, VOLUME-weighted
        (tw=False -> equal per-bucket, since buckets are equal-volume) or TIME-weighted (tw=True -> per-bucket
        duration). Window = last `ncyc` LOCKED cycles, optionally extended through the CURRENT forming cycle up to
        b. Settled buckets use e_sh, the forming tail uses causal e_sh_c. Returns the spread (0-100) signed +ve when
        the window's (weighted) net side AGREES with `buy` (favour), -ve when against."""
        _ends, cyc = cycles_from(max(0, b - WBACK), b)
        lockidx = b - LOCK_LAG
        locked = [c for c in cyc if c[1] < lockidx]
        if not locked:
            return 0.0
        win = locked[-ncyc:]; seg0 = win[0][0]; seg1 = b if include_cur else win[-1][1]
        idxs = [k for k in range(seg0, min(seg1, len(e_sh) - 1) + 1)]
        if not idxs:
            return 0.0
        sh = [float(e_sh[k]) if k < lockidx else float(e_sh_c[k]) for k in idxs]   # settled / forming share
        dom = [s if s >= 0.5 else 1.0 - s for s in sh]                             # dominant share in [0.5,1]
        wts = [max(1e-9, float(et[k]) - float(et[k - 1])) if k > 0 else 1.0 for k in idxs] if tw else [1.0] * len(idxs)
        denom = sum(wi / vi for wi, vi in zip(wts, dom))
        whm = (sum(wts) / denom) if denom > 0 else 0.5                             # weighted harmonic mean
        spread = (2.0 * whm - 1.0) * 100.0
        net_bull = (sum(wi * si for wi, si in zip(wts, sh)) / sum(wts)) >= 0.5     # weighted net side
        return spread if (net_bull == buy) else -spread                           # +favour / -against

    # ---- select the SAME taken set as the frozen book, compute both exits ----
    usedE = {"long": set(), "short": set()}; trades = []; all_d = []
    for det, ent, s in gated:
        buy = s == "long"; dz = zone_at(det)
        tier = tier_of(det, buy); side = "Buy D" if buy else "Sell D"
        gdd, _wd, _xd = walk_dx(det, buy)                  # DIRECT-D entry under D-EXIT for EVERY fired D (tier focus)
        all_d.append(dict(tier=tier, side=side, net=gdd - FEE,                    # + HMS signals for the weighting test
                          vw2=hms_signal(det, buy, 2, False, False),   # CURRENT: volume-wt, last 2 locked
                          vw3=hms_signal(det, buy, 2, True, False),    # control: volume-wt, 2 locked + current
                          tw3=hms_signal(det, buy, 2, True, True)))    # TIME-wt, 2 locked + current
        if dz is None:
            continue
        step3 = (tier == "cyan/orange") and ((side, dz) in V3OK)
        if step3:
            eb = det; path = "A"; ez = None
        else:
            e0 = find_E(det, det, buy)
            if not (e0 is not None and e0 > det and e0 not in usedE[s]):
                continue
            usedE[s].add(e0); ez = zone_at(e0)
            if not ematch(side, tier, dz, ez):
                continue
            eb = e0; path = "B"
        gz = walk_zz(eb, buy)
        rec = dict(path=path, tier=tier, side=side, dz=dz, ez=ez, net_zz=gz - FEE, nets={}, whys={})
        for lbl, lk, ap, lp in LOCK_CFG:
            g, w, _x = walk_dx(eb, buy, lock=lk, arm_p=ap, lock_p=lp)
            rec["nets"][lbl] = g - FEE; rec["whys"][lbl] = w
        gtp, wtp, _xtp = walk_dx(eb, buy, tp_p=0.004)          # D-EXIT + HARD +0.4% take-profit (Path B experiment)
        rec["net_tp"] = gtp - FEE; rec["why_tp"] = wtp
        rec["brk"] = {}; rec["brk_why"] = {}                   # PURE fixed-bracket sweep (Path B experiment)
        for _sl, _tp in ALLBR:
            gbr, wbr, _xbr = walk_bracket(eb, buy, sl_p=_sl, tp_p=_tp)
            rec["brk"][blabel(_sl, _tp)] = gbr - FEE; rec["brk_why"][blabel(_sl, _tp)] = wbr
        rec["mfe"] = mfe_stop(eb, buy, sl_p=0.002)             # headroom under the 0.2% stop
        rec["sw"] = swings_before(eb)                          # last 3 confirmed ZigZag swing labels before entry
        # RECORDED per-path exit + its entry/exit TIMES (for the account/earnings sim): Path A = D-EXIT (nolock),
        # Path B = fixed bracket SL0.2/TP0.6. net_final = the live-strategy net%.
        if path == "A":
            gf, _wf, xbf = walk_dx(eb, buy)
        else:
            gf, _wf, xbf = walk_bracket(eb, buy, sl_p=0.002, tp_p=0.006)
        rec["net_final"] = gf - FEE; rec["buy"] = buy
        rec["t_in"] = float(et[eb]); rec["t_out"] = float(et[int(xbf)])
        trades.append(rec)
    return trades, all_d


def line(tag, o):
    return "  %-16s n=%-3d | W %-2d BE %-2d L %-2d | net %+.3f%% | TOT $%+.0f | t=%+.2f" % (
        tag, o["n"], o["w"], o["b"], o["l"], o["mean"], o["tot"], o["t"])


def account_sim(trades, bal0=200000.0, lev=10.0, pos_frac=0.10, tz_off_h=1, h0=8, h1=22):
    """Chronological earnings sim under the LIVE per-path exits (Path A D-EXIT, Path B SL0.2/TP0.6).
    Position notional = pos_frac x lev x balance (10% margin x 10x = 1.0x balance) -> the ACCOUNT return per trade =
    strategy net% (COMPOUNDED on the running balance). Rules: only ONE position open at a time (any entry that fires
    while a position is live is skipped); only ENTER inside the [h0,h1) local-hour window (tz_off_h ahead of UTC)."""
    import datetime
    nm = pos_frac * lev                                     # notional multiple of balance (0.10 x 10 = 1.0)
    ts = sorted((t for t in trades if "net_final" in t), key=lambda t: t["t_in"])
    bal = bal0; last_out = -1.0; taken = []; n_hours_cut = 0
    for t in ts:
        lt = datetime.datetime.utcfromtimestamp(t["t_in"] + tz_off_h * 3600)
        if not (h0 <= lt.hour < h1):                       # 8am-10pm local ENTRY window
            n_hours_cut += 1; continue
        if t["t_in"] < last_out:                           # a position is still open -> skip (1 side & once)
            continue
        r = nm * t["net_final"] / 100.0                    # account return this trade (compounded)
        pnl = bal * r; bal += pnl; last_out = t["t_out"]
        taken.append((t, pnl, bal))
    return dict(bal0=bal0, bal=bal, taken=taken, nm=nm, n_all=len(ts), n_hours_cut=n_hours_cut)


def main():
    tr, all_d = build()
    A = [t for t in tr if t["path"] == "A"]; Bp = [t for t in tr if t["path"] == "B"]
    print("PIVOT V3 exit — same taken entries. Exit = fixed structural SL + TAKE-PROFIT-on-opposite-D + trail-on-same-D,")
    print("with an optional +arm%->+lock% breakeven lock. Sweeping the lock width vs ZZTRAIL (frozen) baseline.\n")
    for tag, grp in (("COMBINED", tr), ("Path A (D)", A), ("Path B (E)", Bp)):
        print(tag)
        print(line("  ZZTRAIL", outcome_t([t["net_zz"] for t in grp])))
        for lbl, *_ in LOCK_CFG:
            print(line("  DX " + lbl, outcome_t([t["nets"][lbl] for t in grp])))
        print("")
    print("sanity: ZZTRAIL combined must reproduce the freeze baseline (+$75, t+2.06).")

    print("\nExit reasons per lock config (combined):")
    for lbl, *_ in LOCK_CFG:
        parts = []
        for why in ("oppD", "lock", "SL", "edge"):
            c = sum(1 for t in tr if t["whys"][lbl] == why)
            if c:
                parts.append("%s=%d" % (why, c))
        tot = sum(t["nets"][lbl] for t in tr) * 10.0
        print("  %-12s | %-28s | TOT $%+.0f" % (lbl, "  ".join(parts), tot))

    # ---- TIER FOCUS: DIRECT-D entry under the D-EXIT for EVERY fired D, by tier (raw per-tier edge) ----
    print("\nDIRECT-D by tier — every fired D entered at D under the D-EXIT (nolock), regardless of zone:")
    for t_ in TIERS:
        grp = [d for d in all_d if d["tier"] == t_]
        o = outcome_t([d["net"] for d in grp])
        print(line("  " + t_, o))
        for sd in ("Buy D", "Sell D"):
            os_ = outcome_t([d["net"] for d in grp if d["side"] == sd])
            if os_["n"]:
                print(line("     " + sd, os_))
    print(line("  ALL D", outcome_t([d["net"] for d in all_d])))

    # ---- LIVE BOOK per setup (the ACTUAL V3 taken trades), ranked by total $, under D-EXIT nolock ----
    NL = "nolock"

    def agg(recs):
        return outcome_t([r["nets"][NL] for r in recs])
    print("\n=== LIVE BOOK per setup (D-EXIT nolock), ranked by total $ ===")
    print(" PATH A — direct-D (cyan/orange + directional 4H zone):")
    kA = {}
    for r in A:
        kA.setdefault((r["side"], r["dz"]), []).append(r)
    for (sd, dz), recs in sorted(kA.items(), key=lambda kv: -agg(kv[1])["tot"]):
        print(line("  %s @ %s" % (sd[:-2], dz), agg(recs)))
    print(line("  ALL Path A", agg(A)))
    print(" PATH B — New-E (side · D-zone -> E-zone [tier]):")
    kB = {}
    for r in Bp:
        kB.setdefault((r["side"], r["dz"], r["ez"], r["tier"]), []).append(r)
    for (sd, dz, ez, tr_), recs in sorted(kB.items(), key=lambda kv: -agg(kv[1])["tot"]):
        print(line("  %s %s->%s [%s]" % (sd[:-2], dz, ez, tr_.split("/")[0]), agg(recs)))
    print(line("  ALL Path B", agg(Bp)))

    # ---- PATH B EXPERIMENT: hard +0.4% take-profit instead of ride-to-opposite-D ----
    print("\n=== PATH B — hard +0.4%% TAKE-PROFIT vs the ride-to-opposite-D D-EXIT ===")
    print(line("  Path B  D-EXIT (ride)", outcome_t([r["nets"][NL] for r in Bp])))
    print(line("  Path B  +0.4% TP", outcome_t([r["net_tp"] for r in Bp])))
    print("  Path B +0.4%% TP exit reasons: " + "  ".join(
        "%s=%d" % (w, sum(1 for r in Bp if r["why_tp"] == w)) for w in ("TP", "oppD", "SL", "edge")
        if sum(1 for r in Bp if r["why_tp"] == w)))
    print("  Path B  +0.4% TP per combo (side · D-zone -> E-zone):")
    kB2 = {}
    for r in Bp:
        kB2.setdefault((r["side"], r["dz"], r["ez"]), []).append(r)
    for (sd, dz, ez), recs in sorted(kB2.items(), key=lambda kv: -outcome_t([r["net_tp"] for r in kv[1]])["tot"]):
        print(line("    %s %s->%s" % (sd[:-2], dz, ez), outcome_t([r["net_tp"] for r in recs])))
    print("\n  COMBINED book — Path A on D-EXIT + Path B on +0.4%% TP:")
    mixed = [r["nets"][NL] for r in A] + [r["net_tp"] for r in Bp]
    print(line("  Combined (mix)", outcome_t(mixed)))
    print(line("  Combined (all D-EXIT)", outcome_t([r["nets"][NL] for r in tr])))

    # ---- PATH B EXPERIMENT 2: PURE fixed-bracket SWEEP (SL x TP) ----
    print("\n=== PATH B — PURE fixed-bracket sweep (flat SL / TP, net = gross - 0.10 fee), ranked by total $ ===")
    for _sl, _tp in sorted(BRACKETS, key=lambda st: -outcome_t([r["brk"][blabel(*st)] for r in Bp])["tot"]):
        lbl = blabel(_sl, _tp)
        o = outcome_t([r["brk"][lbl] for r in Bp])
        rr = (_tp * 100 - FEE) / (_sl * 100 + FEE)              # net reward:risk (win = TP-fee, loss = SL+fee)
        reasons = "  ".join("%s=%d" % (w, sum(1 for r in Bp if r["brk_why"][lbl] == w))
                            for w in ("TP", "SL", "edge") if sum(1 for r in Bp if r["brk_why"][lbl] == w))
        cmb = outcome_t([r["nets"][NL] for r in A] + [r["brk"][lbl] for r in Bp])
        print("  %-13s (net R:R %.2f) | Path B: %s  [%s]" % (lbl, rr, line("", o).strip(), reasons))
        print("                    -> Combined w/ Path A: %s" % line("", cmb).strip())
    # ---- MFE headroom + wide-TP progression: "can we target more than 0.5%?" ----
    mfes = sorted(r["mfe"] for r in Bp)
    runners = [m for m in mfes if m >= 0.4]                     # trades that get going (not an instant stop)
    print("\n=== PATH B — MFE headroom under the 0.2%% stop (how far the entries run in favour) ===")
    print("  avg MAX favourable = %.2f%%   median = %.2f%%   avg of 'runners' (MFE>=0.4%%) = %.2f%%  (n_run=%d/%d)"
          % (sum(mfes) / len(mfes), mfes[len(mfes) // 2],
             (sum(runners) / len(runners) if runners else 0.0), len(runners), len(mfes)))
    print("  reach rate: " + "  ".join("%.2f%%=%d%%" % (thr, round(100 * sum(m >= thr for m in mfes) / len(mfes)))
                                        for thr in (0.4, 0.5, 0.6, 0.75, 1.0, 1.25, 1.5)))
    print("  per-trade MFE (sorted): " + " ".join("%.2f" % m for m in mfes))
    print("\n  WIDE-TP progression at SL0.2% (Path B $ / TP-hit% / Combined $), targeting past 0.5%:")
    for _sl, _tp in WIDE_TP:
        lbl = blabel(_sl, _tp); o = outcome_t([r["brk"][lbl] for r in Bp])
        ntp = sum(1 for r in Bp if r["brk_why"][lbl] == "TP")
        cmb = outcome_t([r["nets"][NL] for r in A] + [r["brk"][lbl] for r in Bp])
        print("    %-12s | Path B $%+3.0f (W%d/L%d, t%+.2f)  TP-hit %d/%d  | Combined $%+.0f t%+.2f"
              % (lbl, o["tot"], o["w"], o["l"], o["t"], ntp, len(Bp), cmb["tot"], cmb["t"]))

    # ---- STRUCTURE CHECK: were the losing Sell body->buy trades entered into an UP-structure (HL/HH before)? ----
    print("\n=== STRUCTURE CHECK — Path B trades, last 3 ZigZag swings before entry (HL/HH = up-structure) ===")
    for (sd, dz, ez) in (("Sell D", "body", "buy area"), ("Buy D", "body", "sell area")):
        grp = [r for r in Bp if (r["side"], r["dz"], r["ez"]) == (sd, dz, ez)]
        if not grp:
            continue
        print("  %s %s->%s (n=%d):" % (sd[:-2], dz, ez, len(grp)))
        for r in grp:
            up = r["sw"][-1] in ("HL", "HH") if r["sw"] else None
            print("    net(SL.2/TP.6)=%+.2f%%  MFE=%.2f%%  swings=%s  -> %s"
                  % (r["brk"].get("SL0.2/TP0.6", 0.0), r["mfe"], "/".join(r["sw"]) or "none",
                     "UP-structure (fought trend)" if up else ("down/neutral" if up is not None else "no swings")))

    print("\n  per-combo net $ by bracket (Path B):")
    kB3 = {}
    for r in Bp:
        kB3.setdefault((r["side"], r["dz"], r["ez"]), []).append(r)
    hdr = "    %-30s | " % "combo (side · Dz->Ez)" + " ".join("%-11s" % blabel(*st) for st in BRACKETS)
    print(hdr)
    for (sd, dz, ez), recs in kB3.items():
        cells = " ".join("%+11.0f" % outcome_t([r["brk"][blabel(*st)] for r in recs])["tot"] for st in BRACKETS)
        print("    %-30s | %s" % ("%s %s->%s" % (sd[:-2], dz, ez), cells))

    # ---- EARNINGS SIM: $200k, 10% position x10 leverage, 1 position at a time, 8am-10pm (UTC+1), compounded ----
    import datetime
    R = account_sim(tr, bal0=200000.0, lev=10.0, pos_frac=0.10, tz_off_h=1, h0=8, h1=22)
    tk = R["taken"]
    print("\n=== EARNINGS SIM — $200k · 10%% position x10 lev (notional %.1fx balance) · ONE position at a time · "
          "enter 8am-10pm UTC+1 · compounded ===" % R["nm"])
    if tk:
        t0 = datetime.datetime.utcfromtimestamp(min(t["t_in"] for t, _, _ in tk) + 3600)
        t1 = datetime.datetime.utcfromtimestamp(max(t["t_out"] for t, _, _ in tk) + 3600)
        days = max(1e-9, (t1 - t0).total_seconds() / 86400.0)
        nA = sum(1 for t, _, _ in tk if t["path"] == "A"); nB = len(tk) - nA
        w = sum(1 for t, _, _ in tk if t["net_final"] > 0.05)
        l = sum(1 for t, _, _ in tk if t["net_final"] < -0.05); be = len(tk) - w - l
        prof = R["bal"] - R["bal0"]; ret = (R["bal"] / R["bal0"] - 1) * 100.0
        print(f"  tape span (taken): {t0:%b %d %H:%M} -> {t1:%b %d %H:%M} UTC+1  (~{days:.1f} days)")
        print(f"  V3 entries in tape: {R['n_all']}  ->  cut by 8am-10pm: {R['n_hours_cut']}  ->  "
              f"cut by 1-at-a-time overlap: {R['n_all'] - R['n_hours_cut'] - len(tk)}  ->  TAKEN: {len(tk)}")
        print(f"  taken breakdown: Path A {nA} · Path B {nB}  |  W {w} / BE {be} / L {l}  ({100*w/len(tk):.0f}% win)")
        print(f"  START ${R['bal0']:,.0f}  ->  END ${R['bal']:,.0f}   |   PROFIT ${prof:,.0f}  ({ret:+.1f}%)")
        print(f"  avg ${prof/len(tk):,.0f}/trade · ~{len(tk)/days:.1f} trades/day · ~{ret/days:+.2f}%/day compounded")
        print("  NOTE notional=1.0x balance (10% margin x10). If you meant 10% NOTIONAL (not margin), scale profit x0.1.")

    # ---- SIGNAL RATE: how many D-entries (Path A) and E-entries (Path B) fire per day ----
    allt = [t for t in tr if "net_final" in t]
    if allt:
        span = max(1e-9, (max(t["t_in"] for t in allt) - min(t["t_in"] for t in allt)) / 86400.0)
        nA = sum(1 for t in allt if t["path"] == "A"); nB = len(allt) - nA

        def _inwin(t, off=1, h0=8, h1=22):
            return h0 <= datetime.datetime.utcfromtimestamp(t["t_in"] + off * 3600).hour < h1
        nAw = sum(1 for t in allt if t["path"] == "A" and _inwin(t))
        nBw = sum(1 for t in allt if t["path"] == "B" and _inwin(t))
        print("\n=== SIGNAL RATE — V3 entries per day (tape ~%.1f days) ===" % span)
        print("  ALL 24h       : D-entries (Path A) %2d = %.2f/day | E-entries (Path B) %2d = %.2f/day | total %.2f/day"
              % (nA, nA / span, nB, nB / span, (nA + nB) / span))
        print("  8am-10pm UTC+1: D-entries         %2d = %.2f/day | E-entries         %2d = %.2f/day | total %.2f/day"
              % (nAw, nAw / span, nBw, nBw / span, (nAw + nBw) / span))

    # ---- HMS WEIGHTING TEST: is a TIME-weighted HMS a better directional filter than the current volume-weighted?
    #      Split ALL fired D's (direct-D under D-EXIT) by HMS favour(+)/against(-); a good filter -> favour >> against.
    print("\n=== HMS WEIGHTING TEST — direct-D (n=%d) outcomes split by HMS favour(+)/against(-) ===" % len(all_d))
    print("  (favour = the window's dominant side AGREES with the D's direction; a useful filter separates them)")

    def _corr(key):
        xs = np.array([d[key] for d in all_d], float); ys = np.array([d["net"] for d in all_d], float)
        return float(np.corrcoef(xs, ys)[0, 1]) if (len(xs) > 2 and xs.std() > 0 and ys.std() > 0) else 0.0
    for key, desc in (("vw2", "VW-2L  volume-wt · last 2 locked      (CURRENT)"),
                      ("vw3", "VW-3   volume-wt · 2 locked + current  (window control)"),
                      ("tw3", "TW-3   TIME-wt  · 2 locked + current  (the candidate)")):
        fav = outcome_t([d["net"] for d in all_d if d[key] > 0])
        agn = outcome_t([d["net"] for d in all_d if d[key] < 0])
        strong = outcome_t([d["net"] for d in all_d if d[key] > 25])   # strong favour (aligned spread > 25)
        print("  " + desc + "   corr(spread,net)=%+.3f" % _corr(key))
        print(line("    favour(+)", fav)); print(line("    against(-)", agn)); print(line("    STRONG(+>25)", strong))
        print("    -> separation: favour-net %+.3f%% vs against-net %+.3f%%  (gap %+.3f%%/tr, $%+.0f)"
              % (fav["mean"], agn["mean"], fav["mean"] - agn["mean"], fav["tot"] - agn["tot"]))


if __name__ == "__main__":
    main()
