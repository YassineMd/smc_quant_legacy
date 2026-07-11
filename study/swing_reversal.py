"""STANDALONE research — the SWING-TOP / SWING-BOTTOM signature. NOT Pivot V3.

Goal: fade the extremes — SHORT the swing high, LONG the swing low, SL 0.1%, TP = the OTHER extreme of the swing.
Find the >=0.5% ZigZag swings, run that fade, split TP-hit (winners) vs SL-hit (losers), and profile what the panels
show AT the extreme so the reversal is identifiable:
  - candle: body direction + FLOW border (absorb-orange = buy-led closed-down / absorb-blue = sell-led closed-up / neon / std)
  - P2 eff-agg spread LOCKED and NON-LOCKED (and whether the live value is turning past the locked one)
  - P2 HMS (last-2-cycle) + current-cycle HM spread + current-cycle age (minutes)
  - P0 (sum0) level, slope, extreme (>|50|), recent cross
  - scalp ZigZag label at the extreme
Everything aligned to the FADE (short@top / long@bottom): "+" = supports the reversal. Run: python study/swing_reversal.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402
import pivot_v3_de_zone_pdf as B                          # 1m loader + zz  # noqa: E402

LW = config.LIVE_PANEL_WINDOW; LOCK = PD.LOCK; MIN_CYC = 4
SWING_PCT = 0.005      # >=0.5% swings to study
SCALP_PCT = 0.0015     # finer micro-structure ZigZag
SL_P = 0.001           # 0.1% stop
PK = 15                # window for "recent P0 cross"


def zz(H, L, thr):     # ZigZag -> [(pivot_bar, price, is_high, confirm_bar)]
    n = len(H); piv = []; d = 0; hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        h = H[i]; l = L[i]
        if d >= 0:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi * (1 - thr):
                piv.append((hi_i, hi, True, i)); d = -1; lo, lo_i = l, i; continue
        if d <= 0:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo * (1 + thr):
                piv.append((lo_i, lo, False, i)); d = 1; hi, hi_i = h, i
    return piv


def build_cycles(sh, m):
    n = len(sh); cyc = []; i0 = 0; dom = sh[0] >= 0.5
    for k in range(1, n):
        if (sh[k] >= 0.5) != dom:
            cyc.append([i0, k - 1, dom]); i0 = k; dom = sh[k] >= 0.5
    cyc.append([i0, n - 1, dom])
    while len(cyc) > 1:
        si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
        if (cyc[si][1] - cyc[si][0] + 1) >= m:
            break
        cyc[si][2] = not cyc[si][2]; mg = [cyc[0]]
        for c in cyc[1:]:
            if c[2] == mg[-1][2]:
                mg[-1][1] = c[1]
            else:
                mg.append(c)
        cyc = mg
    return cyc


def main():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    op = np.array([float(d.get("open", d.get("open_price", 0.0))) for d in raws])
    cl = np.array([float(d.get("close", d.get("close_price", 0.0))) for d in raws])
    hi = np.array([float(d.get("high", 0.0)) for d in raws]); lo = np.array([float(d.get("low", 0.0)) for d in raws])
    et = np.array([float(d.get("end_time", 0.0)) for d in raws])
    bvv = np.array([float(d.get("buy_vol", 0.0)) for d in raws]); svv = np.array([float(d.get("sell_vol", 0.0)) for d in raws])
    _a, e_sh, _r, sum0 = PD._p9_global(snaps)                                     # P2 settled share, P0 blue line
    e_shc = PD.eff_causal_share(snaps)                                            # P2 first-print (non-locked) share

    cyc = build_cycles(e_sh, MIN_CYC); cyc_ends = [c[1] for c in cyc]

    def hms2(lb, bull):                                                           # HMS over last 2 LOCKED cycles
        m = bisect.bisect_left(cyc_ends, lb)
        if m < 1:
            return 0.0
        w = cyc[max(0, m - 2):m]; s0 = w[0][0]; s1 = w[-1][1]
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1 - e_sh[k]) for k in range(s0, s1 + 1) if 0 <= k < n]
        hm = len(dom) / sum(1 / v for v in dom) if dom else 0.5
        sp = (2 * hm - 1) * 100; nb = float(np.mean(e_sh[s0:s1 + 1])) >= 0.5
        return (sp if nb else -sp) * (1 if bull else -1)

    def cur_cycle(lb, bull):                                                      # current locked cycle: HM spread + age(min)
        m = min(bisect.bisect_left(cyc_ends, lb), len(cyc) - 1)
        s0 = cyc[m][0]; s1 = min(lb, cyc[m][1])
        dom = [(e_sh[k] if e_sh[k] >= 0.5 else 1 - e_sh[k]) for k in range(s0, s1 + 1) if 0 <= k < n]
        hm = len(dom) / sum(1 / v for v in dom) if dom else 0.5
        sp = (2 * hm - 1) * 100; nb = float(np.mean(e_sh[s0:s1 + 1])) >= 0.5
        age = (et[s1] - et[s0]) / 60.0 if s1 > s0 else 0.0
        return (sp if nb else -sp) * (1 if bull else -1), age

    def candle(b, bull):
        """Flow border class + booleans, aligned to the FADE (bull=long@bottom / short@top)."""
        bv, sv = bvv[b], svv[b]; up = cl[b] >= op[b]
        absorb = (bv > sv and not up) if (not bull) else (sv > bv and up)         # top: buy-led closed-down / bottom: sell-led closed-up
        neon = (sv > 1.5 * bv and not up) if (not bull) else (bv > 1.5 * sv and up)  # top: heavy sell / bottom: heavy buy
        body_rev = (not up) if (not bull) else up                                # top: closed down / bottom: closed up
        return absorb, neon, body_rev

    def p0_cross(b, red):                                                         # recent red(down@+50/0) or green(up@-50/0) cross
        for k in range(max(1, b - PK), b + 1):
            for Lv in (50.0, 0.0, -50.0):
                a = sum0[k - 1] - Lv; c = sum0[k] - Lv
                if a > 0 and c <= 0 and red:
                    return True
                if a < 0 and c >= 0 and not red:
                    return True
        return False

    def feats(b, want_bear):
        """Reversal tells at bar b. want_bear=True -> the SHORT/top tells; False -> the LONG/bottom tells."""
        lb = max(0, b - LOCK)
        p2l = (2 * e_sh[lb] - 1) * 100.0                                          # LOCKED bull-spread
        p2n = (2 * e_shc[b] - 1) * 100.0                                          # NON-LOCKED (live) bull-spread
        bv, sv = bvv[b], svv[b]; up = cl[b] >= op[b]
        hb = hms2(lb, True); chm, age = cur_cycle(lb, True)                       # bull-signed
        if want_bear:                                                            # expecting a DOWN reversal (short a top)
            d = dict(absorb=(bv > sv and not up), neon=(sv > 1.5 * bv and not up), body=(not up),
                     p2l_conf=(p2l < 0), p2n_conf=(p2n < 0), turning=(p2n < p2l), hms_conf=(hb < 0),
                     p0_conf=(sum0[lb] > 50), p0_cross=p0_cross(b, red=True),
                     p2l=-p2l, p2n=-p2n, hms=-hb, chm=-chm, age=age, p0=sum0[lb])
        else:                                                                    # expecting an UP reversal (long a bottom)
            d = dict(absorb=(sv > bv and up), neon=(bv > 1.5 * sv and up), body=up,
                     p2l_conf=(p2l > 0), p2n_conf=(p2n > 0), turning=(p2n > p2l), hms_conf=(hb > 0),
                     p0_conf=(sum0[lb] < -50), p0_cross=p0_cross(b, red=False),
                     p2l=p2l, p2n=p2n, hms=hb, chm=chm, age=age, p0=-sum0[lb])
        return d

    piv = zz(list(hi), list(lo), SWING_PCT)
    tops = [feats(pb, True) for pb, p, ih, cb in piv if ih and pb > LOCK]
    bots = [feats(pb, False) for pb, p, ih, cb in piv if (not ih) and pb > LOCK]
    rng = np.random.default_rng(1)
    sample = rng.integers(LOCK + 2, n - 2, size=3000)
    base_bear = [feats(int(b), True) for b in sample]                            # base rate of the bear tells (any bar)
    base_bull = [feats(int(b), False) for b in sample]

    def profile(recs, base, title):
        print("%s — n=%d extremes  (vs base = random bar)" % (title, len(recs)))
        print("  tell                              at extreme   base rate   LIFT")
        for key, lab in (("absorb", "ABSORB border (buy-led closed-down / sell-led up)"),
                         ("body", "candle body reverses"), ("neon", "heavy-flow neon border"),
                         ("p2l_conf", "P2 LOCKED already reversed"), ("p2n_conf", "P2 non-locked reversed"),
                         ("turning", "P2 live turning past the locked"), ("hms_conf", "HMS (2-cycle) reversed"),
                         ("p0_conf", "P0 at the extreme (>|50|)"), ("p0_cross", "P0 recent reversal cross")):
            fe = 100 * np.mean([r[key] for r in recs]); fb = 100 * np.mean([r[key] for r in base])
            print("  %-33s %6.0f%%     %6.0f%%   %+5.0f%s" % (lab, fe, fb, fe - fb, "  <==" if (fe - fb) >= 12 else ""))
        print("  ---- magnitudes (aligned to the reversal; +=supports) ----   extreme / base")
        for key, lab in (("p2l", "P2 locked spread"), ("p2n", "P2 non-locked spread"), ("hms", "HMS 2-cycle"),
                         ("chm", "current-cycle HM"), ("age", "current-cycle age (min)"), ("p0", "P0 level")):
            print("  %-33s %+8.1f  /  %+.1f" % (lab, np.mean([r[key] for r in recs]), np.mean([r[key] for r in base])))

    print("SWING-REVERSAL signature — what shows up AT the >=%.1f%% swing extremes vs a random bar. tape n=%d.\n" % (SWING_PCT * 100, n))
    profile(tops, base_bear, "TOPS (short the swing high)")
    print("")
    profile(bots, base_bull, "BOTTOMS (long the swing low)")


if __name__ == "__main__":
    main()
